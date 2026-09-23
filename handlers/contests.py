import time
import html
import requests
import json
import random
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from core.bot import bot
from database.mongo import db, paid_collection
from config import STAFF_GROUP_ID, chat_ids_mk, CONTESTS_THREAD_ID

# --- 1. ПРИЕМ РАБОТ ---
@bot.message_handler(commands=['contest', 'конкурс'])
def start_contest(message):
    uid = message.from_user.id
    active_contest = "halloween_2026"
    
    works_count = db['contests'].count_documents({"uid": uid, "contest_id": active_contest, "status": {"$ne": "rejected"}})
    if works_count >= 3:
        bot.send_message(uid, "❌ Вы уже отправили максимальное количество работ (3) на этот конкурс!")
        return
        
    msg = bot.send_message(
        uid,
        "🎃 <b>КОНКУРС: ХЭЛЛОУИН-2026</b>\n\nОтправьте <b>ОДНО ФОТО</b> вашего образа.\n<i>Убедитесь, что фото загружено как картинка, а не файлом.</i>",
        parse_mode="HTML"
    )
    bot.register_next_step_handler(msg, process_contest_photo, active_contest)

def process_contest_photo(message, contest_id):
    uid = message.from_user.id
    if not message.photo:
        msg = bot.send_message(uid, "❌ Это не фото! Пожалуйста, отправьте фотографию:")
        bot.register_next_step_handler(msg, process_contest_photo, contest_id)
        return
        
    file_id = message.photo[-1].file_id
    
    msg = bot.send_message(uid, "📸 Отлично! Теперь придумайте <b>Название</b> для вашей работы (до 50 символов):", parse_mode="HTML")
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
    
    db['contests'].insert_one({
        "_id": work_id,
        "contest_id": contest_id,
        "uid": uid,
        "username": username,
        "title": title,
        "photo_id": photo_id,
        "status": "pending",
        "votes": [],
        "timestamp": time.time()
    })
    
    bot.send_message(uid, "⏳ Ваша работа отправлена на проверку Темному Жюри!\nЕсли она пройдет модерацию, она будет опубликована анонимно.")
    
    # Экранируем спецсимволы для безопасности HTML
    safe_username = html.escape(username)
    safe_title = html.escape(title)
    
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("✅ В Галерею (SFW)", callback_data=f"cmod_gal_{work_id}"),
        InlineKeyboardButton("🔥 В «Без предрассудков» (18+)", callback_data=f"cmod_nsfw_{work_id}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"cmod_rej_{work_id}")
    )
    
    bot.send_photo(
        STAFF_GROUP_ID,
        photo_id,
        caption=f"🎃 <b>НОВАЯ РАБОТА НА КОНКУРС</b>\n\n👤 От: {safe_username} (<code>{uid}</code>)\n🏷 Название: «{safe_title}»",
        reply_markup=markup,
        parse_mode="HTML",
        message_thread_id=CONTESTS_THREAD_ID # <--- ВОТ ЭТА СТРОЧКА ОТПРАВИТ В НУЖНУЮ ТЕМУ
    )

# --- 2. МОДЕРАЦИЯ И ПУБЛИКАЦИЯ ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('cmod_'))
def handle_contest_moderation(call):
    if str(call.message.chat.id) != str(STAFF_GROUP_ID): return
    
    parts = call.data.split('_')
    action = parts[1]
    work_id = "_".join(parts[2:]) 
    
    work = db['contests'].find_one({"_id": work_id})
    if not work or work['status'] != 'pending':
        try: bot.answer_callback_query(call.id, "❌ Работа уже обработана!", show_alert=True)
        except: pass
        return
        
    uid = work['uid']
    safe_title = html.escape(work['title'])
    
    if action == "rej":
        db['contests'].update_one({"_id": work_id}, {"$set": {"status": "rejected"}})
        bot.edit_message_caption(f"{call.message.caption}\n\n❌ <b>ОТКЛОНЕНО</b>", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None, parse_mode="HTML")
        try: bot.send_message(uid, f"❌ Ваша конкурсная работа «{safe_title}» отклонена модератором.", parse_mode="HTML")
        except: pass
        return
        
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
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("❤️ Отдать голос (0)", callback_data=f"cvote_{work_id}"))
    
    post_caption = f"🎃 <b>Конкурс: ХЭЛЛОУИН-2026</b>\n\n🏷 Название: «{safe_title}»\n\n👇 <i>Нажми на кнопку, чтобы отдать голос за этот образ!</i>"
    
    try:
        pub_msg = bot.send_photo(target_chat, work['photo_id'], caption=post_caption, reply_markup=markup, parse_mode="HTML")
        
        db['contests'].update_one({"_id": work_id}, {
            "$set": {
                "status": "published",
                "target_chat": target_chat,
                "message_id": pub_msg.message_id
            }
        })
        
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": 200, "immunity": 1, "jackpot_shards": 1}}, upsert=True)
        
        bot.edit_message_caption(f"{call.message.caption}\n\n✅ <b>ОПУБЛИКОВАНО в {chat_name}</b>", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None, parse_mode="HTML")
        try: bot.send_message(uid, f"🎉 <b>Ваша работа «{safe_title}» одобрена и опубликована в {chat_name}!</b>\n\nСкайнет начислил вам бонус за смелость: <b>200 💎, 1 🛡 Щит и 1 🧩 Осколок!</b>", parse_mode="HTML")
        except: pass
        
    except Exception as e:
        bot.send_message(STAFF_GROUP_ID, f"❌ Ошибка публикации: {e}. Бот точно админ в этом чате?")

# --- 3. АНОНИМНОЕ ГОЛОСОВАНИЕ ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('cvote_'))
def handle_contest_vote(call):
    work_id = call.data[6:] 
    uid = call.from_user.id
    
    work = db['contests'].find_one({"_id": work_id})
    if not work:
        try: bot.answer_callback_query(call.id, "❌ Работа не найдена!", show_alert=True)
        except: pass
        return
        
    if uid in work.get('votes', []):
        try: bot.answer_callback_query(call.id, "Вы уже отдали свой голос за эту работу! ❤️", show_alert=True)
        except: pass
        return
        
    db['contests'].update_one({"_id": work_id}, {"$push": {"votes": uid}})
    new_count = len(work.get('votes', [])) + 1
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton(f"❤️ Отдать голос ({new_count})", callback_data=f"cvote_{work_id}"))
    
    try:
        bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
        bot.answer_callback_query(call.id, "Ваш голос учтен! ✨")
    except:
        try: bot.answer_callback_query(call.id, "Голос учтен, обновляю счетчик...")
        except: pass

# --- 4. ПОДВЕДЕНИЕ ИТОГОВ И РАЗДАЧА ПРИЗОВ ---
@bot.message_handler(commands=['end_contest'])
def end_contest_cmd(message):
    # Команду может писать только админ в служебной группе
    if str(message.chat.id) != str(STAFF_GROUP_ID): return
    
    parts = message.text.split()
    if len(parts) < 2:
        bot.send_message(message.chat.id, "❌ Укажите ID конкурса.\nПример: `/end_contest halloween_2026`", parse_mode="Markdown", message_thread_id=message.message_thread_id)
        return
        
    contest_id = parts[1]
    
    # Достаем все работы, которые были опубликованы
    works = list(db['contests'].find({"contest_id": contest_id, "status": "published"}))
    if not works:
        bot.send_message(message.chat.id, f"❌ Нет активных работ для конкурса `{contest_id}`.", parse_mode="Markdown", message_thread_id=message.message_thread_id)
        return
        
    # Считаем длину массива голосов для каждой работы
    for w in works:
        w['vote_count'] = len(w.get('votes', []))
        
    # Сортируем от большего к меньшему
    works.sort(key=lambda x: x['vote_count'], reverse=True)
    
    # Берем ТОП-3 (если участников меньше 3, Питон просто возьмет сколько есть)
    top_works = works[:3] 
    
    report = f"🏆 <b>ИТОГИ КОНКУРСА: {contest_id}</b> 🏆\n\n"
    
    for i, work in enumerate(top_works):
        place = i + 1
        uid = work['uid']
        safe_username = html.escape(work['username'])
        safe_title = html.escape(work['title'])
        votes = work['vote_count']
        
        # --- ВЫДАЧА ПРИЗОВ ПО МЕСТАМ ---
        if place == 1:
            u_info = db['users'].find_one({"_id": uid}) or {}
            has_vip = u_info.get("is_vip", False)
            has_beyond = u_info.get("is_queer", False) # is_queer — это флаг BEYOND в базе
            
            if has_beyond:
                # Максимальный статус уже есть -> Компенсируем деньгами (+1000₽ сверху)
                paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": 5000, "cashback_balance": 2500}}, upsert=True)
                prize_text = "5000 💎 + 2500 ₽ (Компенсация за макс. статус)"
            elif has_vip:
                # Есть VIP -> Апаем до BEYOND
                db['users'].update_one({"_id": uid}, {"$set": {"is_queer": True}}, upsert=True)
                paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": 5000, "cashback_balance": 1500}}, upsert=True)
                prize_text = "5000 💎 + Апгрейд до BEYOND + 1500 ₽"
            else:
                # Нет ничего -> Даем базовый VIP
                db['users'].update_one({"_id": uid}, {"$set": {"is_vip": True}}, upsert=True)
                paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": 5000, "cashback_balance": 1500}}, upsert=True)
                prize_text = "5000 💎 + Пожизненный VIP + 1500 ₽"
                
            medal = "🥇"
            
        elif place == 2:
            import random
            # Генерируем 3 Ордера на арест с ЗАЩИТОЙ от дубликатов
            for _ in range(3):
                while True:
                    code = f"ARREST-{random.randint(10000, 999999)}" # Увеличили диапазон
                    # Проверяем, есть ли уже такой код в базе
                    if not db['promocodes'].find_one({"_id": code}):
                        db['promocodes'].insert_one({"_id": code, "type": "artifact", "value": 0, "target": "mute", "usage_limit": 1, "used_count": 0, "is_active": True, "owner_uid": uid})
                        break # Выходим из цикла while, код уникален
            
            paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": 3000}}, upsert=True)
            prize_text = "3000 💎 + 3 Ордера на Арест"
            medal = "🥈"
            
        elif place == 3:
            paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": 1000, "immunity": 2}}, upsert=True)
            prize_text = "1000 💎 + 2 Щита Иммунитета"
            medal = "🥉"
            
        report += f"<b>{place} МЕСТО {medal}</b>\n👤 От: {safe_username} (<code>{uid}</code>)\n🏷 «{safe_title}»\n❤️ Голосов: <b>{votes}</b>\n🎁 Приз: <i>{prize_text}</i>\n\n"
        
        # Радуем победителя в ЛС
        try: bot.send_message(uid, f"🏆 <b>ПОЗДРАВЛЯЕМ!</b> 🏆\n\nВаш образ «{safe_title}» занял <b>{place} место</b> в конкурсе!\n\nВаша награда: <b>{prize_text}</b> успешно зачислена на ваш баланс Скайнета. Можете проверить в Кабинете!", parse_mode="HTML")
        except: pass
        
    # Меняем статус всем участникам, чтобы заморозить конкурс
    db['contests'].update_many({"contest_id": contest_id, "status": "published"}, {"$set": {"status": "completed"}})
    
    # Отправляем отчет тебе в админскую тему
    bot.send_message(STAFF_GROUP_ID, report, parse_mode="HTML", message_thread_id=message.message_thread_id)

# --- 5. ГЕНЕРАЦИЯ КОНКУРСА ЧЕРЕЗ ИИ (GROQ) ---
@bot.message_handler(commands=['ai_contest'])
def generate_ai_contest(message):
    if str(message.chat.id) != str(STAFF_GROUP_ID): return
    
    theme = message.text.replace('/ai_contest', '').strip()
    if not theme:
        bot.send_message(message.chat.id, "❌ Укажите праздник или тему.\nПример: `/ai_contest День Холостяка (18+)`", parse_mode="Markdown", message_thread_id=message.message_thread_id)
        return

    msg = bot.send_message(message.chat.id, f"🧠 <i>Скайнет анализирует нейронные связи и придумывает концепт для «{theme}»...</i>", parse_mode="HTML", message_thread_id=message.message_thread_id)

    # Жесткий системный промпт для LLM, чтобы она отдавала только JSON
    system_prompt = """Ты креативный директор мужского Telegram-сообщества. 
    Твоя задача — придумать тематический фотоконкурс. 
    Верни СТРОГО валидный JSON без маркдауна и лишнего текста.
    Формат ответа:
    {
      "contest_id": "уникальный_id_на_английском_например_single_day_2026",
      "title": "Яркое название с эмодзи",
      "description": "Завлекающий текст анонса. Опиши, какие фото нужно присылать.",
      "prizes": {
         "1": {"text": "5000 💎 + VIP + 1500 ₽"},
         "2": {"text": "3000 💎 + 3 Ордера на Арест"},
         "3": {"text": "1000 💎 + 2 Щита Иммунитета"}
      }
    }"""
    
    try:
        api_key = random.choice(GROQ_API_KEYS)
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": "llama3-70b-8192", 
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Сгенерируй конкурс на тему: {theme}"}
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.7
        }
        
        response = requests.post("https://api.groq.com/openai/v1/chat/completions", json=payload, headers=headers).json()
        result_json = json.loads(response['choices'][0]['message']['content'])
        
        # Сохраняем черновик в базу
        result_json['status'] = 'draft' 
        db['active_contest'].update_one({"_id": "current_event"}, {"$set": result_json}, upsert=True)
        
        # Выводим админу на проверку
        text = f"💡 <b>ИДЕЯ ОТ СКАЙНЕТА</b>\n\n"
        text += f"🏷 <b>Название:</b> {result_json['title']}\n"
        text += f"🆔 <b>ID:</b> <code>{result_json['contest_id']}</code>\n\n"
        text += f"📝 <b>Описание:</b>\n{result_json['description']}\n\n"
        text += f"🎁 <b>Призы:</b>\n1. {result_json['prizes']['1']['text']}\n2. {result_json['prizes']['2']['text']}\n3. {result_json['prizes']['3']['text']}"
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("✅ Запустить этот конкурс", callback_data="ai_start_contest"))
        
        bot.edit_message_text(text, message.chat.id, msg.message_id, reply_markup=markup, parse_mode="HTML")
        
    except Exception as e:
        bot.edit_message_text(f"❌ Ошибка связи с нейросетью: {e}", message.chat.id, msg.message_id)

# --- Активация черновика ---
@bot.callback_query_handler(func=lambda call: call.data == 'ai_start_contest')
def handle_ai_start(call):
    if str(call.message.chat.id) != str(STAFF_GROUP_ID): return
    
    db['active_contest'].update_one({"_id": "current_event"}, {"$set": {"status": "running"}})
    bot.edit_message_text(f"{call.message.text}\n\n🚀 <b>СТАТУС: ЗАПУЩЕН! Участники могут отправлять фото.</b>", call.message.chat.id, call.message.message_id, parse_mode="HTML")