import telebot
import pymongo
import datetime
import random
import os
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask, request

# ================= НАСТРОЙКИ (БЕЗОПАСНЫЕ) =================
# Теперь мы не светим пароли! Сервер сам подставит их из настроек.
TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
APP_URL = os.getenv("APP_URL") # Ссылка на твой апп на Render (например, https://my-bot.onrender.com)

STAFF_GROUP_ID = "-1002196190507" # ID группы можно оставить в коде, это не страшно

client = pymongo.MongoClient(MONGO_URI)
db = client['elite_bot_db']
archive_collection = db['grouphelp_archive']

bot = telebot.TeleBot(TOKEN)

topic_to_user = {} 
user_to_topic = {}
paid_collection = db['paid_users'] 
user_verif_timer = {}

# ================= ШАБЛОНЫ ОТВЕТОВ =================
TEMPLATES = {
    "tpl_18": "🛑 **Внимание: Проверка возраста**\n\nУ администрации сети возникли подозрения относительно вашего совершеннолетия.\n\nℹ️ **Правило:** Находиться в сети чатов МК, ПАРНИ 18+, ГЕЙ чаты, НС, Радуга разрешено *исключительно* лицам, достигшим 18 лет.\n\n🛡 **Как снять ограничения:**\nВам необходимо предоставить фото одного из официальных документов, подтверждающих возраст:\n• Паспорт (РФ или заграничный)\n• Водительское удостоверение\n• Военный билет\n• Паспорт иностранного гражданина или ВНЖ\n*(Студенческие билеты, банковские карты и пропуски не принимаются!)*\n\nВ целях вашей безопасности мы просим **закрасить или скрыть** все персональные данные, оставив видимыми только **фотографию лица и дату рождения**.\n\n*После отправки фото ожидайте, администратор укажет дальнейший порядок действий.*",

    "tpl_nark_react": "⛔️ **БЛОКИРОВКА: Реакция на запрещенные вещества**\n\nВы были заблокированы за положительную реакцию (смайлик) на сообщение, связанное с наркотическими веществами.\n\nℹ️ В нашей сети действует нулевая терпимость к любым формам поддержки запрещенных веществ.\n\n🔓 Разблокировка возможна только на платной основе (штраф).",
    
    "tpl_verif": "⚠️ **Сработала система защиты**\n\nМы временно ограничили ваш доступ к сети МК из-за подозрительной активности аккаунта.\n\nℹ️ **Как снять ограничения:**\nДля подтверждения необходимо пройти видео-верификацию (записать видео-кружок). На видео должно быть четко видно ваше лицо, и вам нужно будет произнести специальную фразу.\n\n👉 Если вы готовы пройти проверку, напишите сюда: **«Пройти верификацию»**.",
    
    "tpl_mp": "💰 **Ограничение: Коммерческая деятельность**\n\nВаши ограничения связаны с публикацией объявлений об оказании услуг за материальную помощь (МП).\n\nℹ️ Согласно правилам сети: любая коммерческая деятельность допускается *только после оплаты рекламного взноса*.\n\n🔓 **Для снятия ограничений** необходимо оплатить штраф за нарушение правил + оплатить рекламный пакет. Напишите, если готовы узнать условия.",
    
    "tpl_nark": "⛔️ **БЛОКИРОВКА: Наркотические вещества**\n\nПричина вашей блокировки — упоминание наркотиков. Любые вещества и их эвфемизмы (смайлики, сленг, положительные реакции, комментарии) строго запрещены.\n\n⚖️ **Условия разблокировки:**\nРазбан возможен только после предоставления справки от врача-нарколога либо справки от МВД.\n\n*В исключительных случаях возможен разбан после оплаты штрафа (сумма определяется старшим администратором).*.",
    
    "tpl_flood": "🔇 **Ограничение: Флуд в чатах**\n\nВы получили временный мут за флуд (однотипные сообщения более 3-х раз подряд).\n\n⏳ **Ограничение снимется автоматически** (точное время указано в системном сообщении внутри чата).\n\n⚡️ Если вы не хотите ждать, возможно досрочное снятие мута на платной основе (от 100₽).",
    
    "tpl_vip": "⚠️ **Служебное уведомление системы**\n\nВы были заблокированы по внутренней сети партнерских проектов.\n\nℹ️ **Причина:** Вы заблокировали VIP-бота в момент проведения диалога и не отправили ключевую фразу.\n\n🔓 Разблокировка возможна только на платной основе.",
    
    "tpl_bio": "🛑 **Ограничение: Ссылка в профиле**\n\nАвтомодератор обнаружил в вашем профиле (BIO) стороннюю ссылку или тег канала.\n\nℹ️ **Порядок действий:**\n1. Полностью уберите ссылку/канал из профиля Telegram.\n2. Не возвращайте ее на всё время пребывания в сетях МК, ПАРНИ 18+, ГЕЙ чаты, НС, Радуга \n\n\n*После проверки профиля администратором ограничения будут сняты.*",
    
    "tpl_minor": "⛔️ **БЛОКИРОВКА: Несовершеннолетние**\n\nВы заблокированы за то, что оставили реакцию на объявление несовершеннолетнего пользователя.\n\nℹ️ Мы строго следим за возрастным цензом. Это грубое нарушение правил безопасности.\n\n🔓 Разблокировка возможна только на платной основе (штраф)."
}

# ================= МЕНЮ ПОЛЬЗОВАТЕЛЯ =================
@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("💰 Купить рекламу / Сотрудничество", callback_data="btn_ads"),
        InlineKeyboardButton("🆘 Разблокировка / Верификация", callback_data="btn_unban"),
    )
    bot.send_message(message.chat.id, f"Привет, {message.from_user.first_name}! 👋\nВыберите нужный раздел:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('btn_'))
def handle_user_query(call):
    bot.answer_callback_query(call.id)
    uid = call.from_user.id
    name = call.from_user.first_name or "Скрыто"
    username = f"@{call.from_user.username}" if call.from_user.username else f"ID {uid}"

    user_data = paid_collection.find_one({"uid": uid}) or {"uid": uid, "status": 0, "strikes": 0, "thread_id": None}
    thread_id = user_data.get("thread_id")

    # ================= ЛОГИКА КНОПКИ РАЗБАНА =================
    if call.data == "btn_unban":
        if user_data.get("strikes", 0) >= 3 and user_data.get("status") != 1:
            bot.send_message(call.message.chat.id, "⛔️ Вы заблокированы за спам. Лимит обращений исчерпан.")
            return 

        if user_data.get("status") == 1:
            paid_collection.update_one({"uid": uid}, {"$set": {"strikes": 0}}) 
            
            user_record = archive_collection.find_one({"target": username}) or archive_collection.find_one({"target": str(uid)})
            history_text = "🟢 История чиста."
            if user_record and "history" in user_record:
                history_text = "⚠️ **Досье из GroupHelp:**\n"
                for entry in user_record["history"][-15:]: 
                    history_text += f"• {entry['date']} — {entry['action']}\nПричина: {entry.get('reason', 'Не указана')}\n"
            
            bot.send_message(call.message.chat.id, "✅ Ваша оплата подтверждена. Напишите вашу проблему ниже, и мы начнем процесс верификации.")

            markup_ban = InlineKeyboardMarkup()
            markup_ban.add(InlineKeyboardButton("🚷 Заблокировать (Спам)", callback_data=f"ban_{uid}"))
            
            if thread_id:
                caption = f"🔄 **Повторное обращение (ОПЛАЧЕНО ⭐️):**\n• ID: `{uid}`\n• Юзер: {username}\n\n{history_text}"
                bot.send_message(STAFF_GROUP_ID, caption, message_thread_id=thread_id, parse_mode="Markdown", reply_markup=markup_ban)
                # Обновляем тип топика на случай, если он пришел из рекламы
                paid_collection.update_one({"uid": uid}, {"$set": {"topic_type": "unban"}})
            else:
                topic = bot.create_forum_topic(chat_id=STAFF_GROUP_ID, name=f"🆘 | {name}")
                thread_id = topic.message_thread_id
                # Ставим штамп "unban"
                paid_collection.update_one({"uid": uid}, {"$set": {"thread_id": thread_id, "topic_type": "unban"}}, upsert=True)
                caption = f"🆕 **Новое обращение (ОПЛАЧЕНО ⭐️):**\n• ID: `{uid}`\n• Юзер: {username}\n\n{history_text}"
                bot.send_message(STAFF_GROUP_ID, caption, message_thread_id=thread_id, parse_mode="Markdown", reply_markup=markup_ban)
            
            topic_to_user[thread_id] = uid
            user_to_topic[uid] = thread_id
            
        else:
            new_strikes = user_data.get("strikes", 0) + 1
            paid_collection.update_one({"uid": uid}, {"$set": {"strikes": new_strikes}}, upsert=True)
            
            if new_strikes >= 3:
                bot.send_message(call.message.chat.id, "⛔️ Вы заблокированы за спам. Обращения без оплаты игнорируются.")
            else:
                bot.send_message(call.message.chat.id, f"⚠️ **Внимание!** Сначала необходимо задать вопрос в платной группе SUPPORT и оплатить 50 звезд.\nПопытка {new_strikes} из 3. После 3-й попытки бот вас заблокирует.")
        return

    # ================= ЛОГИКА КНОПКИ РЕКЛАМЫ =================
    elif call.data == "btn_ads":
        bot.send_message(call.message.chat.id, "Вы выбрали раздел 'Купить рекламу'. Пожалуйста, напишите ваше предложение ниже:")
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🚨 Это хитрец (Впаять страйк)", callback_data=f"trap_{uid}"))
        
        if thread_id:
            bot.send_message(STAFF_GROUP_ID, f"🔄 **Повторный запрос на РЕКЛАМУ:**\n• ID: `{uid}`\n• Юзер: {username}\n\nЕсли он просит разбан, жмите кнопку ниже 👇", message_thread_id=thread_id, parse_mode="Markdown", reply_markup=markup)
            paid_collection.update_one({"uid": uid}, {"$set": {"topic_type": "ads"}})
        else:
            topic = bot.create_forum_topic(chat_id=STAFF_GROUP_ID, name=f"💰 РЕКЛАМА | {name}")
            thread_id = topic.message_thread_id
            # Ставим штамп "ads"
            paid_collection.update_one({"uid": uid}, {"$set": {"thread_id": thread_id, "topic_type": "ads"}}, upsert=True)
            bot.send_message(STAFF_GROUP_ID, f"🆕 **Новый запрос на РЕКЛАМУ:**\n• ID: `{uid}`\n• Юзер: {username}\n\nЕсли он просит разбан, жмите кнопку ниже 👇", message_thread_id=thread_id, parse_mode="Markdown", reply_markup=markup)
        
        topic_to_user[thread_id] = uid
        user_to_topic[uid] = thread_id
        return

# ================= СООБЩЕНИЯ ОТ ЮЗЕРА -> АДМИНАМ =================
@bot.message_handler(func=lambda message: message.chat.type == 'private', content_types=['text', 'photo', 'document', 'video_note', 'voice', 'video', 'sticker', 'audio'])
def handle_user_messages(message):
    uid = message.from_user.id
    
    # Загружаем профиль юзера
    user_data = paid_collection.find_one({"uid": uid}) or {}
    
    # 🛑 ВЫШИБАЛА: Если у юзера 3 страйка и нет оплаты — сообщения летят в пустоту
    if user_data.get("strikes", 0) >= 3 and user_data.get("status") != 1:
        return # Бот просто игнорирует всё
        
    # 🩹 ЛЕКАРСТВО ОТ АМНЕЗИИ
    if uid not in user_to_topic and "thread_id" in user_data:
        user_to_topic[uid] = user_data["thread_id"]
        topic_to_user[user_data["thread_id"]] = uid

    if uid in user_to_topic:
        thread_id = user_to_topic[uid]
        topic_type = user_data.get("topic_type", "unban") # Читаем штамп
        
        # 💬 Если юзер прислал ТЕКСТ
        if message.content_type == 'text':
            
            # --- АВТОМАТИЧЕСКАЯ ВЫДАЧА ФРАЗЫ И ЗАПУСК ТАЙМЕРА ---
            if message.text.lower() in ["готов", "готова", "готовы", "готов(а)"]:
                user_verif_timer[uid] = datetime.datetime.now() # Засекаем 10 минут
                text_phrase = "⏳ **Таймер запущен! У вас ровно 10 минут.**\n\nЗапишите **видео-кружок**, на котором четко видно лицо, и произнесите:\n\n💬 *«Привет команде МК из [ВАШ ГОРОД], сейчас на часах [СКОЛЬКО ВРЕМЕНИ]»*."
                bot.send_message(uid, text_phrase, parse_mode="Markdown")
                bot.send_message(STAFF_GROUP_ID, "⏳ *Пользователь написал «Готов». Бот выдал фразу и запустил таймер 10 минут! Ждем кружок.*", message_thread_id=thread_id, parse_mode="Markdown")
                return # Прерываем, чтобы не кидать вам меню кнопок на одно слово
            # ----------------------------------------------------

            if topic_type == "unban":
                # Показывать 9 кнопок только для нарушителей
                markup = InlineKeyboardMarkup(row_width=2)
                markup.add(
                    InlineKeyboardButton("🔞 Запрос /18", callback_data="tpl_18"),
                    InlineKeyboardButton("🎥 Верификация", callback_data="tpl_verif"),
                    InlineKeyboardButton("💰 /мп", callback_data="tpl_mp"),
                    InlineKeyboardButton("💊 /нарк", callback_data="tpl_nark"),
                    InlineKeyboardButton("💉 Реакция нарк", callback_data="tpl_nark_react"),
                    InlineKeyboardButton("🔇 /флуд", callback_data="tpl_flood"),
                    InlineKeyboardButton("🤖 Блок VIP", callback_data="tpl_vip"),
                    InlineKeyboardButton("🔗 Ссылка БИО", callback_data="tpl_bio"),
                    InlineKeyboardButton("👶 Реакция 18-", callback_data="tpl_minor")
                )
                # Две главные кнопки управления диалогом
                markup.add(
                    InlineKeyboardButton("🔓 РАЗБАН (Снять ограничения)", callback_data="force_unban"),
                    InlineKeyboardButton("🏁 Закрыть (Без разбана)", callback_data="close_ticket")
                )
                bot.send_message(STAFF_GROUP_ID, f"📩 {message.text}", message_thread_id=thread_id, reply_markup=markup)
            else:
                # Для рекламщиков только кнопка закрытия
                markup = InlineKeyboardMarkup()
                markup.add(InlineKeyboardButton("🏁 Закрыть диалог", callback_data="close_ticket"))
                bot.send_message(STAFF_GROUP_ID, f"📩 {message.text}", message_thread_id=thread_id, reply_markup=markup)
        
        # 🎙 Если юзер прислал ГОЛОСОВОЕ, ВИДЕО или СТИКЕР (Фикс краша)
        elif message.content_type in ['voice', 'video', 'sticker', 'audio']:
            bot.copy_message(STAFF_GROUP_ID, message.chat.id, message.message_id, message_thread_id=thread_id)
            # Текста здесь нет, поэтому просто дублируем медиа в админку без лишних кнопок
        
        # 📸 Если юзер прислал ФОТО или ДОКУМЕНТ (паспорт)
        elif message.content_type in ['photo', 'document']:
            markup = InlineKeyboardMarkup(row_width=1)
            markup.add(
                InlineKeyboardButton("✅ Документ принят (Запросить видео)", callback_data="doc_ok"),
                InlineKeyboardButton("❌ Плохое фото (Перезапросить)", callback_data="doc_bad")
            )
            if message.photo:
                bot.send_photo(STAFF_GROUP_ID, message.photo[-1].file_id, caption="📸 **Пользователь прислал фото!**\nПроверьте документ:", message_thread_id=thread_id, parse_mode="Markdown", reply_markup=markup)
            elif message.document:
                bot.send_document(STAFF_GROUP_ID, message.document.file_id, caption="📄 **Пользователь прислал документ!**\nПроверьте:", message_thread_id=thread_id, parse_mode="Markdown", reply_markup=markup)
        
        # 🎥 Если юзер прислал КРУЖОК
        elif message.content_type == 'video_note':
            if uid in user_verif_timer:
                time_diff = (datetime.datetime.now() - user_verif_timer[uid]).total_seconds()
                if time_diff > 600:
                    bot.send_message(uid, "❌ **Время вышло!** Вы не уложились в 10 минут. Ожидайте решения администратора.")
                    bot.send_message(STAFF_GROUP_ID, "⚠️ **ВНИМАНИЕ! Юзер просрочил таймер.** Прошло больше 10 минут. Тщательно проверьте на фейк!", message_thread_id=thread_id)
                del user_verif_timer[uid]
            
            markup = InlineKeyboardMarkup(row_width=1)
            markup.add(
                InlineKeyboardButton("✅ Кружок принят (РАЗБАН)", callback_data="vid_ok"),
                InlineKeyboardButton("❌ Плохое видео (Перезапросить)", callback_data="vid_bad")
            )
            bot.send_video_note(STAFF_GROUP_ID, message.video_note.file_id, message_thread_id=thread_id, reply_markup=markup)
            bot.send_message(STAFF_GROUP_ID, "🎥 **Пользователь прислал кружок!**\nПроверьте лицо и фразу:", message_thread_id=thread_id, parse_mode="Markdown")

    else:
        bot.send_message(message.chat.id, "Сначала выберите раздел в меню /start!")

# ================= ПРОВЕРКА ДОКУМЕНТОВ (КНОПКИ АДМИНА) =================
@bot.callback_query_handler(func=lambda call: call.data in ['doc_ok', 'doc_bad'])
def handle_doc_check(call):
    if str(call.message.chat.id) != STAFF_GROUP_ID:
        return
        
    thread_id = call.message.message_thread_id
    if thread_id in topic_to_user:
        target_uid = topic_to_user[thread_id]
        
        if call.data == 'doc_ok':
            # ЗАПУСКАЕМ ТАЙМЕР!
            user_verif_timer[target_uid] = datetime.datetime.now()
            text_to_user = "✅ **Документ принят! Отлично.**\n\nВторой этап верификации:\nЗапишите **видео-кружок**, на котором будет четко видно ваше лицо, и произнесите фразу:\n\n💬 *«Привет команде МК из [ВАШ ГОРОД], сейчас на часах [СКОЛЬКО ВРЕМЕНИ]»*.\n\nУ вас есть 10 минут на отправку видео."
            bot.send_message(target_uid, text_to_user, parse_mode="Markdown")
            bot.edit_message_caption("✅ *Документ одобрен. Запрошен видео-кружок.*", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=None)
            
        elif call.data == 'doc_bad':
            # Выдаем админу подменю с причинами
            markup = InlineKeyboardMarkup(row_width=1)
            markup.add(
                InlineKeyboardButton("🔎 Размыто / Засветы", callback_data="rej_doc_blur"),
                InlineKeyboardButton("🙈 Скрыты нужные данные", callback_data="rej_doc_hidden"),
                InlineKeyboardButton("📄 Не тот документ", callback_data="rej_doc_wrong")
            )
            bot.edit_message_caption("❓ **Укажите причину отказа:**", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=markup)

# ================= ОБРАБОТКА КНОПОК АДМИНА (ШАБЛОНЫ) =================
@bot.callback_query_handler(func=lambda call: call.data.startswith('tpl_'))
def handle_admin_templates(call):
    # Проверка, что кнопку жмут в админском чате
    if str(call.message.chat.id) != STAFF_GROUP_ID:
        return
        
    thread_id = call.message.message_thread_id
    if thread_id in topic_to_user:
        target_uid = topic_to_user[thread_id]
        template_text = TEMPLATES.get(call.data)
        
        if template_text:
            # 1. Отправляем юзеру
            bot.send_message(target_uid, template_text, parse_mode="Markdown")
            # 2. Пишем всплывашку админу "Успешно"
            bot.answer_callback_query(call.id, "✅ Шаблон успешно отправлен пользователю!")
            # 3. Отчитываемся в теме, чтобы другие админы видели
            bot.send_message(STAFF_GROUP_ID, f"🟢 *Скайнет отправил шаблон:*\n_{template_text.splitlines()[0]}_", message_thread_id=thread_id, parse_mode="Markdown")
            
            # Убираем кнопки из исходного сообщения, чтобы не нажать дважды
            bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
    else:
        bot.answer_callback_query(call.id, "❌ Ошибка: Не удалось найти пользователя.")

# ================= ПРОВЕРКА КРУЖКА И ФИНАЛ (КНОПКИ АДМИНА) =================
@bot.callback_query_handler(func=lambda call: call.data in ['vid_ok', 'vid_bad'])
def handle_vid_check(call):
    if str(call.message.chat.id) != STAFF_GROUP_ID:
        return
        
    thread_id = call.message.message_thread_id
    if thread_id in topic_to_user:
        target_uid = topic_to_user[thread_id]
        
        if call.data == 'vid_ok':
            now = datetime.datetime.now()
            ticket_num = now.strftime("%d%m%Y%H%M%S") + f"-{random.randint(100, 999)}"
            
            # 1. ПЕРЕДАЕМ ПРИКАЗ СКАЙНЕТУ ЧЕРЕЗ БАЗУ
            # (Скайнет потом прочитает это и снимет муты во всех чатах)
            db['skynet_tasks'].insert_one({
                "uid": target_uid,
                "action": "full_unban",
                "timestamp": now
            })

            # 1.5. АВТОМАТИЧЕСКАЯ ВЫДАЧА ТЕГА СКАЙНЕТА!
            # Секретарь лезет в мозги Скайнета и ставит тег
            db['users'].update_one(
                {"_id": target_uid}, 
                {"$set": {"custom_tag": "Верифицирован МК"}}, 
                upsert=True
            )
            
            # КТО НАЖАЛ КНОПКУ? Запоминаем админа
            admin_username = call.from_user.username or call.from_user.first_name
            db['ticket_ratings'].update_one(
                {"thread_id": thread_id},
                {"$set": {"admin": admin_username, "uid": target_uid}},
                upsert=True
            )
            
            # 2. ОТПРАВЛЯЕМ РАДОСТНУЮ ВЕСТЬ И ССЫЛКУ НА ДОНАТ
            text_success = f"🎉 **Ограничения удалены, выдан тег верифицированного участника!** ❤️\n\nЕсли Вам все понравилось, вы можете задонатить нам пару звездочек на канале: [Единый Платежный Центр](https://t.me/+9qx9PAJQjcdmY2Yy) абсолютно на любой пост, нам будет приятно😍, а также вы поддержите работу качественного сервиса💸💸\n\n🔒 **Обращение закрыто. Уникальный номер:** `{ticket_num}`"
            bot.send_message(target_uid, text_success, parse_mode="Markdown", disable_web_page_preview=True)
            
            # 3. ОТПРАВЛЯЕМ МЕНЮ ОЦЕНКИ
            markup = InlineKeyboardMarkup(row_width=5)
            markup.add(
                InlineKeyboardButton("1⭐", callback_data=f"rate_1_{thread_id}"),
                InlineKeyboardButton("2⭐", callback_data=f"rate_2_{thread_id}"),
                InlineKeyboardButton("3⭐", callback_data=f"rate_3_{thread_id}"),
                InlineKeyboardButton("4⭐", callback_data=f"rate_4_{thread_id}"),
                InlineKeyboardButton("5⭐", callback_data=f"rate_5_{thread_id}")
            )
            bot.send_message(target_uid, "🏁 Пожалуйста, оцените работу службы поддержки. Нам важно ваше мнение! 👇", reply_markup=markup)
            
            # 4. ЗАПИСЫВАЕМ УСПЕХ В АРХИВ (ДОСЬЕ)
            archive_collection.update_one(
                {"target": str(target_uid)},
                {"$push": {"history": {
                    "date": now.strftime("%d.%m.%Y %H:%M"),
                    "action": "Успешная верификация",
                    "reason": "Кружок принят админом"
                }}},
                upsert=True
            )
            
            # 5. ЗАКРЫВАЕМ ТЕМУ У АДМИНОВ И АННУЛИРУЕМ БИЛЕТ
            bot.edit_message_caption(f"✅ *Видео-кружок одобрен!*\nЮзер верифицирован. Приказ на размут передан Скайнету. Тикет закрыт: `{ticket_num}`", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=None)
            
            try:
                bot.close_forum_topic(STAFF_GROUP_ID, thread_id)
            except Exception as e:
                pass 
            
            # Сбрасываем статус оплаты (юзер снова становится "С улицы")
            paid_collection.update_one({"uid": target_uid}, {"$set": {"status": 0}})
            
        elif call.data == 'vid_bad':
            # Выдаем админу подменю с причинами
            markup = InlineKeyboardMarkup(row_width=1)
            markup.add(
                InlineKeyboardButton("👤 Не видно лицо", callback_data="rej_vid_face"),
                InlineKeyboardButton("🤐 Не та фраза / Нет времени", callback_data="rej_vid_phrase"),
                InlineKeyboardButton("🔇 Нет звука / Тишина", callback_data="rej_vid_sound")
            )
            bot.edit_message_caption("❓ **Укажите причину отказа:**", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=markup)

# ================= ОБРАБОТКА ПРИЧИН ОТКАЗА =================
@bot.callback_query_handler(func=lambda call: call.data.startswith('rej_'))
def handle_rejections(call):
    if str(call.message.chat.id) != STAFF_GROUP_ID:
        return
        
    thread_id = call.message.message_thread_id
    if thread_id in topic_to_user:
        target_uid = topic_to_user[thread_id]
        
        # Словари с текстами для юзера и отчетами для админа
        reasons_user = {
            "rej_doc_blur": "❌ **Документ не принят.**\nФотография размыта или имеет сильные засветы. Пожалуйста, сделайте более четкое фото и отправьте снова.",
            "rej_doc_hidden": "❌ **Документ не принят.**\nСкрыты необходимые данные. Повторите отправку, оставив открытыми **дату рождения и лицо**.",
            "rej_doc_wrong": "❌ **Документ не принят.**\nПредоставленный документ не входит в официальный перечень. Пришлите паспорт, ВУ, ВНЖ или военный билет.",
            "rej_vid_face": "❌ **Видео-кружок не принят.**\nНа видео плохо видно ваше лицо (темно или обрезано). Запишите кружок при хорошем освещении.",
            "rej_vid_phrase": "❌ **Видео-кружок не принят.**\nВы произнесли не ту фразу или забыли назвать точное время. Перечитайте инструкцию и запишите снова.",
            "rej_vid_sound": "❌ **Видео-кружок не принят.**\nНа видео отсутствует звук. Проверьте микрофон устройства и отправьте кружок повторно."
        }
        
        reasons_admin = {
            "rej_doc_blur": "Размыто", "rej_doc_hidden": "Скрыты данные", "rej_doc_wrong": "Не тот документ",
            "rej_vid_face": "Не видно лицо", "rej_vid_phrase": "Неверная фраза", "rej_vid_sound": "Нет звука"
        }
        
        text_to_user = reasons_user.get(call.data)
        admin_report = reasons_admin.get(call.data)
        
        if text_to_user:
            # Отправляем юзеру точную причину
            bot.send_message(target_uid, text_to_user, parse_mode="Markdown")
            # Отчитываемся в теме, убираем кнопки
            bot.edit_message_caption(f"❌ *Отклонено (Причина: {admin_report}). Запрошено повторно.*", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=None)

# ================= КАПКАН ДЛЯ ХИТРЕЦОВ =================
@bot.callback_query_handler(func=lambda call: call.data.startswith('trap_'))
def handle_trap(call):
    if str(call.message.chat.id) != STAFF_GROUP_ID:
        return
        
    target_uid = int(call.data.split('_')[1])
    thread_id = call.message.message_thread_id
    
    # Начисляем страйк в базу
    user_data = paid_collection.find_one({"uid": target_uid}) or {"uid": target_uid, "strikes": 0}
    new_strikes = user_data.get("strikes", 0) + 1
    paid_collection.update_one({"uid": target_uid}, {"$set": {"strikes": new_strikes}}, upsert=True)
    
    # Бьем юзера током
    bot.send_message(target_uid, f"⛔️ **Вы выбрали раздел 'Реклама' для обхода системы.**\nВам начислен штрафной страйк за спам ({new_strikes}/3)! Для разбана используйте платную поддержку.")
    
    # Отчитываемся в админке и закрываем топик
    bot.edit_message_text(f"🚨 **Хитрец пойман!** Ему начислен страйк ({new_strikes}/3). Топик закрыт.", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown")
    
    try:
        bot.close_forum_topic(STAFF_GROUP_ID, thread_id)
    except Exception:
        pass

# ================= БЫСТРАЯ БЛОКИРОВКА АДМИНОМ =================
@bot.callback_query_handler(func=lambda call: call.data.startswith('ban_'))
def handle_fast_ban(call):
    if str(call.message.chat.id) != STAFF_GROUP_ID: return
    target_uid = int(call.data.split('_')[1])
    thread_id = call.message.message_thread_id
    
    # Выдаем 3 страйка и аннулируем билет
    paid_collection.update_one({"uid": target_uid}, {"$set": {"strikes": 3, "status": 0}}, upsert=True)
    bot.send_message(target_uid, "⛔️ **Вы были заблокированы администратором за нарушение правил общения.**")
    bot.edit_message_text(f"🚷 **Юзер заблокирован администратором!** Топик закрыт.", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown")
    try: bot.close_forum_topic(STAFF_GROUP_ID, thread_id)
    except: pass
        
# ================= ФИНАЛЬНОЕ ЗАКРЫТИЕ ТИКЕТА С ОЦЕНКОЙ =================
@bot.callback_query_handler(func=lambda call: call.data == "close_ticket")
def handle_close_ticket(call):
    if str(call.message.chat.id) != STAFF_GROUP_ID:
        return
        
    thread_id = call.message.message_thread_id
    if thread_id in topic_to_user:
        target_uid = topic_to_user[thread_id]
        
        # 1. Отправляем пользователю меню оценки
        markup = InlineKeyboardMarkup(row_width=5)
        markup.add(
            InlineKeyboardButton("1⭐", callback_data=f"rate_1_{thread_id}"),
            InlineKeyboardButton("2⭐", callback_data=f"rate_2_{thread_id}"),
            InlineKeyboardButton("3⭐", callback_data=f"rate_3_{thread_id}"),
            InlineKeyboardButton("4⭐", callback_data=f"rate_4_{thread_id}"),
            InlineKeyboardButton("5⭐", callback_data=f"rate_5_{thread_id}")
        )
        # КТО НАЖАЛ КНОПКУ? Запоминаем админа
        admin_username = call.from_user.username or call.from_user.first_name
        db['ticket_ratings'].update_one({"thread_id": thread_id}, {"$set": {"admin": admin_username, "uid": target_uid}}, upsert=True)
        bot.send_message(
            target_uid, 
            "🏁 **Ваше обращение закрыто.**\n\nПожалуйста, оцените работу службы поддержки. Нам важно ваше мнение! 👇", 
            reply_markup=markup
        )
        
        # 2. Обновляем MongoDB (базовое закрытие)
        now_str = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
        archive_collection.update_one(
            {"target": str(target_uid)},
            {"$push": {"history": {
                "date": now_str,
                "action": "Обращение закрыто",
                "reason": "Вопрос решен админом"
            }}},
            upsert=True
        )
        
        # 3. Информируем админов
        bot.edit_message_text(f"🏁 **Тикет закрыт.** Пользователю отправлен запрос оценки.", chat_id=call.message.chat.id, message_id=call.message.message_id)
        try:
            bot.close_forum_topic(STAFF_GROUP_ID, thread_id)
        except Exception:
            pass
        
# ================= ОБРАБОТКА ОЦЕНКИ И ПРЕМИЙ =================
@bot.callback_query_handler(func=lambda call: call.data.startswith('rate_'))
def handle_rating(call):
    # Разбираем данные: rate_ОЦЕНКА_THREADID
    _, rating, t_id = call.data.split('_')
    t_id = int(t_id)
    
    # Благодарим юзера и убираем кнопки
    bot.answer_callback_query(call.id, f"Спасибо за вашу оценку {rating}⭐!")
    bot.edit_message_text(f"🙏 Спасибо за оценку {rating}⭐! Мы работаем для вас.", chat_id=call.message.chat.id, message_id=call.message.message_id)
    
    # Идем в базу и ищем, кто был тем самым админом
    rating_data = db['ticket_ratings'].find_one({"thread_id": t_id})
    admin_name = rating_data["admin"] if rating_data else "Неизвестный герой"
    
    # Красивый отчет в админку прямо в топик юзера
    if rating in ['4', '5']:
        mood = "🎉 Отличная работа!"
    else:
        mood = "⚠️ Нужно обратить внимание."
        
    report = f"🌟 **Получена новая оценка!**\n\n👨‍💻 Админ: @{admin_name}\n⭐️ Оценка: **{rating} из 5**\n{mood}"
    bot.send_message(STAFF_GROUP_ID, report, message_thread_id=t_id)

# ================= УНИВЕРСАЛЬНЫЙ РАЗБАН (ДЛЯ ЛЮБЫХ НАРУШЕНИЙ) =================
@bot.callback_query_handler(func=lambda call: call.data == "force_unban")
def handle_force_unban(call):
    if str(call.message.chat.id) != STAFF_GROUP_ID:
        return
        
    thread_id = call.message.message_thread_id
    if thread_id in topic_to_user:
        target_uid = topic_to_user[thread_id]
        now = datetime.datetime.now()
        ticket_num = now.strftime("%d%m%Y%H%M%S") + f"-{random.randint(100, 999)}"
        
        # 1. ПЕРЕДАЕМ ПРИКАЗ СКАЙНЕТУ ЧЕРЕЗ БАЗУ
        db['skynet_tasks'].insert_one({
            "uid": target_uid,
            "action": "full_unban",
            "timestamp": now
        })
        
        # КТО НАЖАЛ КНОПКУ? Запоминаем админа
        admin_username = call.from_user.username or call.from_user.first_name
        db['ticket_ratings'].update_one({"thread_id": thread_id}, {"$set": {"admin": admin_username, "uid": target_uid}}, upsert=True)
        
        # 2. ОТПРАВЛЯЕМ ТЕКСТ ЮЗЕРУ
        text_success = f"🎉 **Ваши ограничения успешно сняты!** ❤️\n\nЕсли Вам все понравилось, вы можете задонатить нам пару звездочек на канале: [Единый Платежный Центр](https://t.me/+9qx9PAJQjcdmY2Yy) абсолютно на любой пост, нам будет приятно😍, а также вы поддержите работу качественного сервиса💸💸\n\n🔒 **Обращение закрыто. Уникальный номер:** `{ticket_num}`"
        bot.send_message(target_uid, text_success, parse_mode="Markdown", disable_web_page_preview=True)
        
        # 3. ОТПРАВЛЯЕМ МЕНЮ ОЦЕНКИ
        markup = InlineKeyboardMarkup(row_width=5)
        markup.add(
            InlineKeyboardButton("1⭐", callback_data=f"rate_1_{thread_id}"),
            InlineKeyboardButton("2⭐", callback_data=f"rate_2_{thread_id}"),
            InlineKeyboardButton("3⭐", callback_data=f"rate_3_{thread_id}"),
            InlineKeyboardButton("4⭐", callback_data=f"rate_4_{thread_id}"),
            InlineKeyboardButton("5⭐", callback_data=f"rate_5_{thread_id}")
        )
        bot.send_message(target_uid, "🏁 Пожалуйста, оцените работу службы поддержки. Нам важно ваше мнение! 👇", reply_markup=markup)
        
        # 4. ЗАПИСЫВАЕМ УСПЕХ В АРХИВ
        archive_collection.update_one(
            {"target": str(target_uid)},
            {"$push": {"history": {
                "date": now.strftime("%d.%m.%Y %H:%M"),
                "action": "Разблокировка (Ручная)",
                "reason": "Вопрос решен админом"
            }}},
            upsert=True
        )
        
        # 5. ЗАКРЫВАЕМ ТЕМУ В АДМИНКЕ
        bot.edit_message_text(f"🔓 **Пользователь разбанен!** Приказ передан Скайнету. Тикет закрыт: `{ticket_num}`", chat_id=call.message.chat.id, message_id=call.message.message_id)
        
        try: bot.close_forum_topic(STAFF_GROUP_ID, thread_id)
        except Exception: pass
        
        # Аннулируем билет
        paid_collection.update_one({"uid": target_uid}, {"$set": {"status": 0}})

# ================= РУЧНЫЕ ОТВЕТЫ ОТ АДМИНА -> ЮЗЕРУ =================
@bot.message_handler(func=lambda message: str(message.chat.id) == STAFF_GROUP_ID and message.is_topic_message)
def handle_admin_replies(message):
    thread_id = message.message_thread_id
    
    # 🩹 ЛЕКАРСТВО ОТ АМНЕЗИИ ДЛЯ АДМИНОВ
    if thread_id not in topic_to_user:
        user_data = paid_collection.find_one({"thread_id": thread_id})
        if user_data:
            topic_to_user[thread_id] = user_data["uid"]
            user_to_topic[user_data["uid"]] = thread_id

    if thread_id in topic_to_user:
        target_uid = topic_to_user[thread_id]
        bot.send_message(target_uid, message.text)

# ==================== WEBHOOK ====================
@app.route('/webhook', methods=['POST'])
def webhook():
    update = telebot.types.Update.de_json(request.stream.read().decode('utf-8'))
    bot.process_new_updates([update])
    return 'ok', 200

if __name__ == '__main__':
    print("Бот запущен — мягкая версия с приветствием и удалением сообщений (кроме сети ПАРНИ)")
    app.run(host='0.0.0.0', port=5000)