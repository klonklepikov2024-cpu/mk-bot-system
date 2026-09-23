import time
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from core.bot import bot
from database.mongo import db, paid_collection
from config import STAFF_GROUP_ID, chat_ids_mk # <--- Берем матрицу чатов из ЦУПа

# --- 1. ПРИЕМ РАБОТ ---
@bot.message_handler(commands=['contest', 'конкурс'])
def start_contest(message):
    uid = message.from_user.id
    active_contest = "halloween_2026" # Уникальный ID текущего конкурса
    
    # Проверка лимита (максимум 3 работы от юзера)
    works_count = db['contests'].count_documents({"uid": uid, "contest_id": active_contest, "status": {"$ne": "rejected"}})
    if works_count >= 3:
        bot.send_message(uid, "❌ Вы уже отправили максимальное количество работ (3) на этот конкурс!")
        return
        
    msg = bot.send_message(
        uid,
        "🎃 **КОНКУРС: ХЭЛЛОУИН-2026**\n\nОтправьте **ОДНО ФОТО** вашего образа.\n_Убедитесь, что фото загружено как картинка, а не файлом._",
        parse_mode="Markdown"
    )
    bot.register_next_step_handler(msg, process_contest_photo, active_contest)

def process_contest_photo(message, contest_id):
    uid = message.from_user.id
    if not message.photo:
        msg = bot.send_message(uid, "❌ Это не фото! Пожалуйста, отправьте фотографию:")
        bot.register_next_step_handler(msg, process_contest_photo, contest_id)
        return
        
    file_id = message.photo[-1].file_id
    
    msg = bot.send_message(uid, "📸 Отлично! Теперь придумайте **Название** для вашей работы (до 50 символов):")
    bot.register_next_step_handler(msg, process_contest_title, contest_id, file_id)

def process_contest_title(message, contest_id, photo_id):
    uid = message.from_user.id
    title = message.text.strip()
    
    if len(title) > 50:
        msg = bot.send_message(uid, "❌ Название слишком длинное! Напишите короче (до 50 символов):")
        bot.register_next_step_handler(msg, process_contest_title, contest_id, photo_id)
        return
        
    username = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name
    work_id = f"cw_{int(time.time())}_{uid}"
    
    # Сохраняем в базу как ожидающее проверку
    db['contests'].insert_one({
        "_id": work_id,
        "contest_id": contest_id,
        "uid": uid,
        "username": username,
        "title": title,
        "photo_id": photo_id,
        "status": "pending",
        "votes": [], # Массив ID проголосовавших
        "timestamp": time.time()
    })
    
    bot.send_message(uid, "⏳ Ваша работа отправлена на проверку Темному Жюри!\nЕсли она пройдет модерацию, она будет опубликована анонимно.")
    
    # Отправляем в ЦУП на выбор категории
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("✅ В Галерею (SFW)", callback_data=f"cmod_gal_{work_id}"),
        InlineKeyboardButton("🔥 В «Без предрассудков» (18+)", callback_data=f"cmod_nsfw_{work_id}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"cmod_rej_{work_id}")
    )
    
    bot.send_photo(
        STAFF_GROUP_ID,
        photo_id,
        caption=f"🎃 **НОВАЯ РАБОТА НА КОНКУРС**\n\n👤 От: {username} (`{uid}`)\n🏷 Название: «{title}»",
        reply_markup=markup,
        parse_mode="Markdown"
    )

# --- 2. МОДЕРАЦИЯ И ПУБЛИКАЦИЯ ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('cmod_'))
def handle_contest_moderation(call):
    if str(call.message.chat.id) != str(STAFF_GROUP_ID): return
    
    parts = call.data.split('_')
    action = parts[1]
    work_id = "_".join(parts[2:]) # Собираем обратно cw_timestamp_uid
    
    work = db['contests'].find_one({"_id": work_id})
    if not work or work['status'] != 'pending':
        try: bot.answer_callback_query(call.id, "❌ Работа уже обработана!", show_alert=True)
        except: pass
        return
        
    uid = work['uid']
    
    if action == "rej":
        db['contests'].update_one({"_id": work_id}, {"$set": {"status": "rejected"}})
        bot.edit_message_caption(f"{call.message.caption}\n\n❌ **ОТКЛОНЕНО**", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
        try: bot.send_message(uid, f"❌ Ваша конкурсная работа «{work['title']}» отклонена модератором.")
        except: pass
        return
        
    # 🔥 УМНАЯ МАРШРУТИЗАЦИЯ ИЗ ЦУПа 🔥
    target_chat = None
    if action == "gal":
        target_chat = chat_ids_mk.get("Галерея")
        chat_name = "Галерею МК"
    elif action == "nsfw":
        target_chat = chat_ids_mk.get("БЕЗ ПРЕДРАССУДКОВ")
        chat_name = "чат Без предрассудков"
        
    if not target_chat:
        try: bot.answer_callback_query(call.id, f"❌ Ошибка! Чат '{chat_name}' не найден в базе ЦУПа!", show_alert=True)
        except: pass
        return
    
    # Кнопка голосования
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("❤️ Отдать голос (0)", callback_data=f"cvote_{work_id}"))
    
    # АНОНИМНЫЙ ПОСТ (Имя не выводится)
    post_caption = f"🎃 **Конкурс: ХЭЛЛОУИН-2026**\n\n🏷 Название: «{work['title']}»\n\n👇 _Нажми на кнопку, чтобы отдать голос за этот образ!_"
    
    try:
        pub_msg = bot.send_photo(target_chat, work['photo_id'], caption=post_caption, reply_markup=markup, parse_mode="Markdown")
        
        db['contests'].update_one({"_id": work_id}, {
            "$set": {
                "status": "published",
                "target_chat": target_chat,
                "message_id": pub_msg.message_id
            }
        })
        
        # Гарантированный дроп за смелость
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": 200, "immunity": 1, "jackpot_shards": 1}}, upsert=True)
        
        bot.edit_message_caption(f"{call.message.caption}\n\n✅ **ОПУБЛИКОВАНО в {chat_name}**", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
        try: bot.send_message(uid, f"🎉 **Ваша работа «{work['title']}» одобрена и опубликована в {chat_name}!**\n\nСкайнет начислил вам бонус за смелость: **200 💎, 1 🛡 Щит и 1 🧩 Осколок!**")
        except: pass
        
    except Exception as e:
        bot.send_message(STAFF_GROUP_ID, f"❌ Ошибка публикации: {e}. Бот точно админ в этом чате?")

# --- 3. АНОНИМНОЕ ГОЛОСОВАНИЕ (БЕЗ НАКРУТОК) ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('cvote_'))
def handle_contest_vote(call):
    work_id = call.data[6:] # Отрезаем 'cvote_'
    uid = call.from_user.id
    
    work = db['contests'].find_one({"_id": work_id})
    if not work:
        try: bot.answer_callback_query(call.id, "❌ Работа не найдена!", show_alert=True)
        except: pass
        return
        
    # Защита от двойного голосования
    if uid in work.get('votes', []):
        try: bot.answer_callback_query(call.id, "Вы уже отдали свой голос за эту работу! ❤️", show_alert=True)
        except: pass
        return
        
    # Фиксация голоса
    db['contests'].update_one({"_id": work_id}, {"$push": {"votes": uid}})
    new_count = len(work.get('votes', [])) + 1
    
    # Обновление счетчика на кнопке в реальном времени
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton(f"❤️ Отдать голос ({new_count})", callback_data=f"cvote_{work_id}"))
    
    try:
        bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
        bot.answer_callback_query(call.id, "Ваш голос учтен! ✨")
    except:
        try: bot.answer_callback_query(call.id, "Голос учтен, обновляю счетчик...")
        except: pass