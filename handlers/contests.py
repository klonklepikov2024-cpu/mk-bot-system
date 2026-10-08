import os  # <--- ВОТ ЭТА СТРОЧКА РЕШИТ ПРОБЛЕМУ
import time
import html
import requests
import threading
import json
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from core.bot import bot
from database.mongo import db, paid_collection
from utils.logger import logger
from config import STAFF_GROUP_ID, chat_ids_mk, chat_ids_parni, chat_ids_ns, chat_ids_rainbow, chat_ids_gayznak, CONTESTS_THREAD_ID

# --- 1. ПРИЕМ РАБОТ ---
@bot.message_handler(commands=['contest', 'конкурс'])
def start_contest(message):
    uid = message.from_user.id
    active = db['active_contest'].find_one({"_id": "current_event", "status": "running"})
    
    if not active:
        bot.send_message(uid, "❌ Сейчас нет активных конкурсов.")
        return
        
    from datetime import datetime
    from zoneinfo import ZoneInfo
    today_str = datetime.now(ZoneInfo("Europe/Moscow")).strftime("%Y-%m-%d")
    
    if today_str < active.get('sub_start', '') or today_str > active.get('sub_end', ''):
        bot.send_message(uid, "❌ Сейчас не время для приема работ! Проверьте даты в анонсе.")
        return
        
    active_contest = active['contest_id']
    
    works_count = db['contests'].count_documents({"uid": uid, "contest_id": active_contest, "status": {"$ne": "rejected"}})
    if works_count >= 3:
        bot.send_message(uid, "❌ Вы уже отправили максимальное количество работ (3) на этот конкурс!")
        return
        
    title = active.get('title', 'Конкурс')
    desc = active.get('description', 'Пришлите ваше фото.')
        
    msg = bot.send_message(
        uid,
        f"🎉 <b>{title}</b>\n\n{desc}\n\n👇 <b>Отправьте ОДНО ФОТО вашей работы.</b>\n<i>Вы можете сразу написать выбранную номинацию или название образа в подписи к фото!</i>",
        parse_mode="HTML"
    )
    bot.register_next_step_handler(msg, process_contest_photo, active) # Передаем словарь

def process_contest_photo(message, active_contest):
    from core.bot import bot
    from database.mongo import db

    if message.text and message.text.lower() in ['/cancel', 'отмена']:
        bot.send_message(message.chat.id, "🛑 Отправка работы отменена.")
        return

    if not message.photo:
        msg = bot.send_message(message.chat.id, "❌ Это не фотография! Пожалуйста, отправьте именно фото (сжатое, не файлом/документом).\n\n<i>Для отмены напишите /cancel</i>", parse_mode="HTML")
        bot.register_next_step_handler(msg, process_contest_photo, active_contest)
        return

    uid = message.from_user.id
    contest_id = active_contest.get("contest_id", "current_event")

    user_submissions = db['contests'].count_documents({"uid": uid, "contest_id": contest_id, "status": {"$ne": "rejected"}})
    if user_submissions >= 3:
        bot.send_message(message.chat.id, "🚫 <b>Лимит исчерпан!</b>\nВы уже отправили максимальное количество работ (3 шт.) на этот конкурс.", parse_mode="HTML")
        return

    file_id = message.photo[-1].file_id

    # Если юзер написал номинацию сразу в подписи к фото
    if message.caption:
        finalize_contest_submission(message, active_contest, file_id, message.caption, user_submissions)
    else:
        # Если прислал просто фото, запрашиваем текст
        msg = bot.send_message(
            message.chat.id, 
            "📸 Фото получено!\n\nТеперь <b>напишите название вашей работы и/или выбранную номинацию</b> (одним сообщением).\n\n<i>Например: «Номинация: Самый жуткий грим» или просто креативное название вашего образа.</i>", 
            parse_mode="HTML"
        )
        bot.register_next_step_handler(msg, process_contest_nomination, active_contest, file_id, user_submissions)

def process_contest_nomination(message, active_contest, file_id, user_submissions):
    from core.bot import bot
    
    if message.text and message.text.lower() in ['/cancel', 'отмена']:
        bot.send_message(message.chat.id, "🛑 Отправка работы отменена.")
        return
        
    if not message.text:
        msg = bot.send_message(message.chat.id, "❌ Отправьте название или номинацию ТЕКСТОМ.\n\n<i>Для отмены напишите /cancel</i>", parse_mode="HTML")
        bot.register_next_step_handler(msg, process_contest_nomination, active_contest, file_id, user_submissions)
        return
        
    finalize_contest_submission(message, active_contest, file_id, message.text, user_submissions)

def finalize_contest_submission(message, active_contest, file_id, nomination_text, user_submissions):
    from core.bot import bot
    from database.mongo import db
    import time
    import html
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
    from config import STAFF_GROUP_ID, CONTESTS_THREAD_ID

    uid = message.from_user.id
    contest_id = active_contest.get("contest_id", "current_event")

    # Ограничиваем длину названия
    safe_nomination = html.escape(nomination_text[:150])

    inserted = db['contests'].insert_one({
        "uid": uid,
        "name": message.from_user.first_name,
        "username": message.from_user.username,
        "contest_id": contest_id,
        "title": safe_nomination, # <--- ПИШЕМ НОМИНАЦИЮ / НАЗВАНИЕ СЮДА
        "photo_id": file_id,
        "timestamp": time.time(),
        "status": "pending",
        "votes": [] 
    })
    
    work_id = str(inserted.inserted_id)
    
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("✅ В Галерею МК", callback_data=f"cmod_gal_{work_id}"),
        InlineKeyboardButton("🔞 В Без предрассудков", callback_data=f"cmod_nsfw_{work_id}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"cmod_rej_{work_id}")
    )
    
    safe_name = html.escape(message.from_user.first_name)
    try:
        bot.send_photo(
            chat_id=STAFF_GROUP_ID,
            photo=file_id,
            caption=f"📸 <b>НОВАЯ РАБОТА НА КОНКУРС</b>\n\n👤 От: {safe_name} (<code>{uid}</code>)\n🏆 Конкурс: {html.escape(active_contest.get('title', 'Конкурс'))}\n🏷 Номинация/Название:\n<i>{safe_nomination}</i>",
            parse_mode="HTML",
            message_thread_id=CONTESTS_THREAD_ID,
            reply_markup=markup
        )
    except Exception as e:
        print(f"Ошибка отправки работы админам: {e}")

    bot.send_message(
        message.chat.id, 
        f"✅ <b>РАБОТА ПРИНЯТА!</b> ({user_submissions + 1}/3)\n\nВаш шедевр успешно зарегистрирован на конкурс <b>«{active_contest.get('title', 'Конкурс')}»</b>!\nЖдите результатов модерации.", 
        parse_mode="HTML"
    )

# --- 2. МОДЕРАЦИЯ И ПУБЛИКАЦИЯ ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('cmod_'))
def handle_contest_moderation(call):
    if str(call.message.chat.id) != str(STAFF_GROUP_ID): return
    
    from bson.objectid import ObjectId # <--- ИМПОРТИРУЕМ OBJECTID
    
    parts = call.data.split('_')
    action = parts[1]
    work_id = "_".join(parts[2:]) 
    
    # 🔥 ОБОРАЧИВАЕМ work_id В ObjectId ПРИ ПОИСКЕ 🔥
    work = db['contests'].find_one({"_id": ObjectId(work_id)})
    if not work or work['status'] != 'pending':
        try: bot.answer_callback_query(call.id, "❌ Работа уже обработана!", show_alert=True)
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        return
        
    uid = work['uid']
    safe_title = html.escape(work.get('title', 'Без названия'))
    
    if action == "rej":
        # 🔥 И ЗДЕСЬ ТОЖЕ ОБОРАЧИВАЕМ В ObjectId 🔥
        db['contests'].update_one({"_id": ObjectId(work_id)}, {"$set": {"status": "rejected"}})
        bot.edit_message_caption(f"{call.message.caption}\n\n❌ <b>ОТКЛОНЕНО</b>", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None, parse_mode="HTML")
        try: bot.send_message(uid, f"❌ Ваша конкурсная работа «{safe_title}» отклонена модератором.", parse_mode="HTML")
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
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
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        return
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("❤️ Отдать голос (0)", callback_data=f"cvote_{work_id}"))
    
    active_contest = db['active_contest'].find_one({"contest_id": work['contest_id']})
    contest_name = active_contest.get("title", "Конкурс") if active_contest else "Конкурс"
    
    post_caption = f"🏆 <b>{html.escape(contest_name)}</b>\n\n🏷 <i>{safe_title}</i>\n\n👇 Нажми на кнопку, чтобы отдать голос за этот образ!"
    
    try:
        pub_msg = bot.send_photo(chat_id=target_chat, photo=work['photo_id'], caption=post_caption, reply_markup=markup, parse_mode="HTML")
        
        # Обновляем статус текущей работы
        db['contests'].update_one({"_id": ObjectId(work_id)}, {
            "$set": {
                "status": "published",
                "target_chat": target_chat,
                "message_id": pub_msg.message_id
            }
        })
        
        # 🔥 ПРОВЕРЯЕМ, ВЫДАВАЛИ ЛИ МЫ УЖЕ БОНУС В ЭТОМ КОНКУРСЕ 🔥
        # Ищем, есть ли у юзера ДРУГИЕ опубликованные работы в этом же конкурсе
        already_published = db['contests'].count_documents({
            "uid": uid, 
            "contest_id": work['contest_id'], 
            "status": "published",
            "_id": {"$ne": ObjectId(work_id)} # Не считаем ту работу, которую только что одобрили
        })
        
        bot.edit_message_caption(f"{call.message.caption}\n\n✅ <b>ОПУБЛИКОВАНО в {chat_name}</b>", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None, parse_mode="HTML")
        
        if already_published == 0:
            # Выдаем бонус (только за первое фото!)
            paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": 200, "immunity": 1, "jackpot_shards": 1}}, upsert=True)
            try: bot.send_message(uid, f"🎉 <b>Ваша работа одобрена и опубликована в {chat_name}!</b>\n\nСкайнет начислил вам бонус за смелость: <b>200 💎, 1 🛡 Щит и 1 🧩 Осколок!</b>", parse_mode="HTML")
            except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        else:
            # Бонус уже был, просто уведомляем о публикации еще одного фото
            try: bot.send_message(uid, f"🎉 <b>Ваша дополнительная работа одобрена и опубликована в {chat_name}!</b>\n\n<i>Желаем удачи в голосовании!</i>", parse_mode="HTML")
            except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        
    except Exception as e:
        bot.send_message(STAFF_GROUP_ID, f"❌ Ошибка публикации: {e}. Бот точно админ в этом чате?")

# --- 3. АНОНИМНОЕ ГОЛОСОВАНИЕ ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('cvote_'))
def handle_contest_vote(call):
    work_id = call.data[6:] 
    uid = call.from_user.id
    
    from bson.objectid import ObjectId # <--- ИМПОРТИРУЕМ СЮДА
    
    active = db['active_contest'].find_one({"_id": "current_event", "status": "running"})
    if active:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        today_str = datetime.now(ZoneInfo("Europe/Moscow")).strftime("%Y-%m-%d")
        
        if today_str < active.get('vote_start', '') or today_str > active.get('vote_end', ''):
            try: bot.answer_callback_query(call.id, "❌ Голосование сейчас закрыто! Сверьтесь с датами.", show_alert=True)
            except Exception as e: logger.debug(f"Игнор ошибки: {e}")
            return

    # 🔥 ОБОРАЧИВАЕМ work_id В ObjectId 🔥
    work = db['contests'].find_one({"_id": ObjectId(work_id)})
    if not work:
        try: bot.answer_callback_query(call.id, "❌ Работа не найдена!", show_alert=True)
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        return
        
    if uid in work.get('votes', []):
        try: bot.answer_callback_query(call.id, "Вы уже отдали свой голос за эту работу! ❤️", show_alert=True)
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        return
        
    # 🔥 И ЗДЕСЬ ОБОРАЧИВАЕМ В ObjectId 🔥
    db['contests'].update_one({"_id": ObjectId(work_id)}, {"$push": {"votes": uid}})
    new_count = len(work.get('votes', [])) + 1
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton(f"❤️ Отдать голос ({new_count})", callback_data=f"cvote_{work_id}"))
    
    try:
        bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
        bot.answer_callback_query(call.id, "Ваш голос учтен! ✨")
    except Exception:
        try: bot.answer_callback_query(call.id, "Голос учтен, обновляю счетчик...")
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")

# --- 4. ПОДВЕДЕНИЕ ИТОГОВ И РАЗДАЧА ПРИЗОВ ---
@bot.message_handler(commands=['end_contest'])
def end_contest_cmd(message):
    if str(message.chat.id) != str(STAFF_GROUP_ID): return
    
    parts = message.text.split()
    if len(parts) < 2:
        bot.send_message(message.chat.id, "❌ Укажите ID конкурса.\nПример: `/end_contest halloween_2026`", parse_mode="Markdown", message_thread_id=message.message_thread_id)
        return
        
    contest_id = parts[1]
    
    contest_data = db['active_contest'].find_one({"contest_id": contest_id})
    prizes_dict = contest_data.get("prizes", {}) if contest_data else {}
    
    works = list(db['contests'].find({"contest_id": contest_id, "status": "published"}))
    if not works:
        bot.send_message(message.chat.id, f"❌ Нет активных работ для конкурса `{contest_id}`.", parse_mode="Markdown", message_thread_id=message.message_thread_id)
        return
        
    # Считаем лайки (Зрительские симпатии)
    for w in works:
        w['vote_count'] = len(w.get('votes', []))
        
    works.sort(key=lambda x: x['vote_count'], reverse=True)
    top_works = works[:3] 
    
    report = f"🏆 <b>ИТОГИ ЗРИТЕЛЬСКОГО ГОЛОСОВАНИЯ: {contest_id}</b> 🏆\n\n"
    
    import time
    for i, work in enumerate(top_works):
        place = str(i + 1)
        uid = work['uid']
        safe_username = html.escape(work.get('username', f"ID {uid}"))
        safe_title = html.escape(work['title'])
        votes = work['vote_count']
        
        # Тянем актуальный текст приза
        prize_text = prizes_dict.get(place, {}).get("text", "Ценный приз (выбор админа)")
        medal = ["🥇", "🥈", "🥉"][i]
        
        report += f"<b>{place} МЕСТО {medal}</b>\n👤 От: {safe_username} (<code>{uid}</code>)\n🏷 «{safe_title}»\n❤️ Голосов: <b>{votes}</b>\n🎁 Приз: <i>{prize_text}</i>\n\n"
        
        # Создаем заявку в ЦУП на ручную выдачу
        db['premium_claims'].insert_one({
            "uid": uid, 
            "username": safe_username, 
            "timestamp": time.time(), 
            "status": "pending",
            "prize_desc": f"Приз за {place} место в конкурсе: {prize_text}" 
        })
        
        try: bot.send_message(uid, f"🏆 <b>ПОЗДРАВЛЯЕМ!</b> 🏆\n\nПо итогам зрительского голосования ваш образ «{safe_title}» занял <b>{place} место</b>!\n\nВаша награда: <b>{prize_text}</b>.\n<i>Заявка на выдачу приза передана администрации. С вами скоро свяжутся в ЛС, либо приз будет начислен вам на баланс!</i>", parse_mode="HTML")
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        
    db['contests'].update_many({"contest_id": contest_id, "status": "published"}, {"$set": {"status": "completed"}})
    db['active_contest'].update_one({"_id": "current_event"}, {"$set": {"status": "completed"}})
    
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ Обработать призы в ЦУП", url="https://elite-poster-bot.onrender.com/glaz"))
    report += "❗️ <i>Заявки на выдачу призов отправлены в панель управления. Вы можете начислить очки/сертификаты вручную. Победителей в тематических номинациях можно наградить отдельно!</i>"
    
    bot.send_message(STAFF_GROUP_ID, report, parse_mode="HTML", reply_markup=markup, message_thread_id=message.message_thread_id)