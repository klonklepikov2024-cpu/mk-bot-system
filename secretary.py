import telebot
import pymongo
import datetime
import random
import os
import time
import string
import threading # 👇 ДОБАВИТЬ ЭТУ СТРОКУ 👇
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask, request
import requests

# 👇 УНИВЕРСАЛЬНЫЙ КАССИР CRYPTOBOT (ДЛЯ РЕКЛАМЫ И ШТРАФОВ) 👇
def get_crypto_pay_url(custom_payload, amount_stars, description, asset=None):
    import os
    import requests
    
    amount_rub = int(amount_stars * 1.8)
    API_TOKEN = os.getenv("CRYPTO_TOKEN")
    
    if not API_TOKEN:
        print("❌ ОШИБКА: Токен CRYPTO_TOKEN не найден!", flush=True)
        return None

    url = "https://pay.crypt.bot/api/createInvoice"
    
    headers = {
        "Crypto-Pay-API-Token": API_TOKEN,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    payload = {
        "currency_type": "fiat",
        "fiat": "RUB",
        "amount": str(amount_rub), 
        "payload": custom_payload,
        "description": description
    }
    
    if asset:
        payload["asset"] = asset
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        res = response.json()
        
        if res.get("ok"): 
            return res["result"]["mini_app_invoice_url"] # 💥 Запускаем красивое Mini-App окно!
    except Exception as e: 
        print(f"❌ Ошибка связи с CryptoBot: {e}", flush=True)
        
    return None

NETWORK_LINKS = (
    "📍 **Ссылки для возврата в чаты:**\n"
    "• [МК (Мужской Клуб)](https://t.me/clubofrm/44)\n"
    "• [ПАРНИ 18+](https://t.me/znakparni/116)\n"
    "• [ГЕЙ чаты (Инфо)](https://t.me/gaychatcities_info/4)\n"
    "• [НС (Урал)](https://t.me/uralns/118)"
)

# ================= НАСТРОЙКИ (БЕЗОПАСНЫЕ) =================
# Теперь мы не светим пароли! Сервер сам подставит их из настроек.
TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
APP_URL = os.getenv("APP_URL") # Ссылка на твой апп на Render (например, https://my-bot.onrender.com)

STAFF_GROUP_ID = "-1002196190507" # ID группы можно оставить в коде, это не страшно

client = pymongo.MongoClient(MONGO_URI)
db = client['elite_bot_db']
archive_collection = db['grouphelp_archive']

bot = telebot.TeleBot(TOKEN, threaded=False)

paid_collection = db['paid_users'] 

# ================= ШАБЛОНЫ ОТВЕТОВ =================
TEMPLATES = {
    "tpl_18": "🛑 **Внимание: Проверка возраста**\n\nУ администрации сети возникли подозрения относительно вашего совершеннолетия.\n\nℹ️ **Правило:** Находиться в сети чатов МК, ПАРНИ 18+, ГЕЙ чаты, НС, Радуга разрешено *исключительно* лицам, достигшим 18 лет.\n\n🛡 **Как снять ограничения:**\nВам необходимо предоставить фото одного из официальных документов, подтверждающих возраст:\n• Паспорт (РФ или заграничный)\n• Водительское удостоверение\n• Военный билет\n• Паспорт иностранного гражданина или ВНЖ\n*(Студенческие билеты, банковские карты и пропуски не принимаются!)*\n\nВ целях вашей безопасности мы просим **закрасить или скрыть** все персональные данные, оставив видимыми только **фотографию лица и дату рождения**.\n\n*После отправки фото ожидайте, администратор укажет дальнейший порядок действий.*",

    "tpl_nark_react": "⛔️ **БЛОКИРОВКА: Реакция на запрещенные вещества**\n\nВы были заблокированы за положительную реакцию (смайлик) на сообщение, связанное с наркотическими веществами.\n\nℹ️ В нашей сети действует нулевая терпимость к любым формам поддержки запрещенных веществ.\n\n🔓 Разблокировка возможна только на платной основе (штраф).",
    
"tpl_verif": "⚠️ **Сработала система защиты**\n\nМы временно ограничили ваш доступ к сети МК из-за подозрительной активности аккаунта.\n\nℹ️ **Как снять ограничения:**\nДля подтверждения необходимо пройти видео-верификацию (записать видео-кружок). На видео должно быть четко видно ваше лицо, и вам нужно будет произнести специальную фразу.\n\n👉 Если вы готовы пройти проверку, напишите сюда: **«Готов»**.",
    
    "tpl_mp": "💰 **Ограничение: Коммерческая деятельность**\n\nВаши ограничения связаны с публикацией объявлений об оказании услуг за материальную помощь (МП).\n\nℹ️ Согласно правилам сети: любая коммерческая деятельность допускается *только после оплаты рекламного взноса*.\n\n🔓 **Для снятия ограничений** необходимо оплатить штраф за нарушение правил + оплатить рекламный пакет. Напишите «+» или «ДА», если хотите узнать условия.",
    
    "tpl_nark": "⛔️ **БЛОКИРОВКА: Наркотические вещества**\n\nПричина вашей блокировки — упоминание наркотиков. Любые вещества и их эвфемизмы (смайлики, сленг, положительные реакции, комментарии) строго запрещены.\n\n⚖️ **Условия разблокировки:**\nРазбан возможен только после предоставления справки от врача-нарколога либо справки от МВД.\n\n*В исключительных случаях возможен разбан после оплаты штрафа (сумма определяется старшим администратором).*.",
    
    "tpl_flood": "🔇 **Ограничение: Флуд в чатах**\n\nВы получили временный мут за флуд (однотипные сообщения более 3-х раз подряд).\n\n⏳ **Ограничение снимется автоматически** (точное время указано в системном сообщении внутри чата).\n\n⚡️ Если вы не хотите ждать, возможно досрочное снятие мута на платной основе (от 100₽).",
    
    "tpl_vip": "⚠️ **Служебное уведомление системы**\n\nВы были заблокированы по внутренней сети партнерских проектов.\n\nℹ️ **Причина:** Вы заблокировали VIP-бота в момент проведения диалога и не отправили ключевую фразу.\n\n🔓 Разблокировка возможна только на платной основе.",
    
    "tpl_bio": "🛑 **Ограничение: Ссылка в профиле**\n\nАвтомодератор обнаружил в вашем профиле (BIO) стороннюю ссылку или тег канала.\n\nℹ️ **Порядок действий:**\n1. Полностью уберите ссылку/канал из профиля Telegram.\n2. Не возвращайте ее на всё время пребывания в сетях МК, ПАРНИ 18+, ГЕЙ чаты, НС, Радуга \n\n\n*После проверки профиля администратором ограничения будут сняты.*",
    
    "tpl_minor": "⛔️ **БЛОКИРОВКА: Несовершеннолетние**\n\nВы заблокированы за то, что оставили реакцию на объявление несовершеннолетнего пользователя.\n\nℹ️ Мы строго следим за возрастным цензом. Это грубое нарушение правил безопасности.\n\n🔓 Разблокировка возможна только на платной основе (штраф)."
}

@bot.message_handler(commands=['start'])
def send_welcome(message):
    try:
        # 👇 НОВАЯ МАГИЯ: Обработка диплинк-ссылки на магазин 👇
        if len(message.text.split()) > 1 and message.text.split()[1] == "shop":
            markup = InlineKeyboardMarkup(row_width=1)
            markup.add(
                InlineKeyboardButton("📦 50 очков (1 прокрут) — 50⭐️", callback_data="shop_points_buy_50_50"),
                InlineKeyboardButton("🔥 300 очков (6 прокрутов) — 200⭐️", callback_data="shop_points_buy_300_200"),
                InlineKeyboardButton("💎 1000 очк. + 🛡 Щит — 500⭐️", callback_data="shop_points_buy_1000_500"),
                InlineKeyboardButton("🔙 В главное меню", callback_data="sec_back_main")
            )
            shop_text = "🎰 **Магазин Очков Бдительности**\n\nОчки можно тратить на скидки в кабинете или использовать для игры в Гача-Рулетку (`/spin`).\n\n🏆 _Посмотреть список призов: /prizes_\n\nВыберите нужный пакет:"
            bot.send_message(message.chat.id, shop_text, reply_markup=markup, parse_mode="Markdown")
            return
        # 👆 ============================================== 👆

        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(
            InlineKeyboardButton("💰 Купить рекламу / Сотрудничество", callback_data="btn_ads"),
            InlineKeyboardButton("🆘 Разблокировка / Верификация", callback_data="btn_unban"),
            InlineKeyboardButton("🚨 Пожаловаться на нарушителя", callback_data="btn_report")
        )
        bot.send_message(message.chat.id, f"Привет, {message.from_user.first_name}! 👋\nВыберите нужный раздел:", reply_markup=markup)
    except Exception as e:
        print(f"❌ КРИТИЧЕСКАЯ ОШИБКА ОТПРАВКИ: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('btn_'))
def handle_user_query(call):
    bot.answer_callback_query(call.id)
    
    # --- УБИРАЕМ КНОПКИ У ЮЗЕРА, ЧТОБЫ НЕ СПАМИЛ ---
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except:
        pass
    
    uid = call.from_user.id
    
    # ЗАЩИТА ОТ "НЕВИДИМОК"
    name = call.from_user.first_name
    if not name or name == '\u3164' or name == 'ㅤ':
        name = f"Без Имени (ID {uid})"

    username = f"@{call.from_user.username}" if call.from_user.username else f"ID {uid}"
    safe_username = username.replace('_', '\\_')

    user_data = paid_collection.find_one({"uid": uid}) or {"uid": uid, "status": 0, "strikes": 0, "thread_id": None}
    thread_id = user_data.get("thread_id")

    # ================= ЛОГИКА КНОПКИ РАЗБАНА =================
    if call.data == "btn_unban":
        if user_data.get("strikes", 0) >= 3 and user_data.get("status") != 1:
            bot.send_message(call.message.chat.id, "⛔️ Вы заблокированы за спам. Лимит обращений исчерпан.")
            return 

        if user_data.get("status") == 1:
            paid_collection.update_one({"uid": uid}, {"$set": {"strikes": 0}}) 
            
            # --- УЛУЧШЕННЫЙ ПОИСК ИСТОРИИ ---
            user_record = archive_collection.find_one({"target": username}) or \
                          archive_collection.find_one({"target": str(uid)}) or \
                          archive_collection.find_one({"target": uid})
            
            skynet_ban = db['banned'].find_one({"_id": uid})
            
            history_text = "🟢 История чиста."
            if user_record and "history" in user_record:
                history_text = "⚠️ **Досье пользователя:**\n"
                for entry in user_record["history"][-10:]:
                    history_text += f"• {entry['date']} — {entry['action']}\nПричина: {entry.get('reason', 'Не указана')}\n"
            
            if skynet_ban:
                history_text += f"\n🚨 **АКТИВНЫЙ БАН СКАЙНЕТА:**\nПричина: {skynet_ban.get('reason', 'Не указана')}"
            
            bot.send_message(call.message.chat.id, "✅ Ваша оплата подтверждена. Напишите вашу проблему ниже, и мы начнем процесс верификации.")

            markup_ban = InlineKeyboardMarkup()
            markup_ban.add(InlineKeyboardButton("🚷 Заблокировать (Спам)", callback_data=f"ban_{uid}"))
            
            if thread_id:
                try:
                    bot.reopen_forum_topic(STAFF_GROUP_ID, thread_id)
                except:
                    pass
                caption = f"🔄 **Повторное обращение (ОПЛАЧЕНО ⭐️):**\n• ID: `{uid}`\n• Юзер: {safe_username}\n\n{history_text}"
                bot.send_message(STAFF_GROUP_ID, caption, message_thread_id=thread_id, parse_mode="Markdown", reply_markup=markup_ban)
                paid_collection.update_one({"uid": uid}, {"$set": {"topic_type": "unban"}})
            else:
                topic = bot.create_forum_topic(chat_id=STAFF_GROUP_ID, name=f"🆘 | {name}")
                thread_id = topic.message_thread_id
                paid_collection.update_one({"uid": uid}, {"$set": {"thread_id": thread_id, "topic_type": "unban"}}, upsert=True)
                caption = f"🆕 **Новое обращение (ОПЛАЧЕНО ⭐️):**\n• ID: `{uid}`\n• Юзер: {safe_username}\n\n{history_text}"
                bot.send_message(STAFF_GROUP_ID, caption, message_thread_id=thread_id, parse_mode="Markdown", reply_markup=markup_ban)
                 
        else:
            new_strikes = user_data.get("strikes", 0) + 1
            paid_collection.update_one({"uid": uid}, {"$set": {"strikes": new_strikes}}, upsert=True)
            
            if new_strikes >= 3:
                bot.send_message(call.message.chat.id, "⛔️ Вы заблокированы за спам. Обращения без оплаты игнорируются.")
            else:
                warning_text = f"⚠️ **Внимание!** Сначала необходимо задать вопрос в платной группе [СЛУЖБЫ ПОДДЕРЖКИ](https://t.me/MK_MensClubSUPPORT) и оплатить 50 звезд.\n\nПопытка {new_strikes} из 3. После 3-й попытки бот вас заблокирует."
                bot.send_message(call.message.chat.id, warning_text, parse_mode="Markdown", disable_web_page_preview=True)
        return

    # ================= ЛОГИКА КНОПКИ РЕКЛАМЫ =================
    elif call.data == "btn_ads":
        bot.send_message(call.message.chat.id, "Вы выбрали раздел 'Купить рекламу'. Пожалуйста, напишите ваше предложение ниже:")
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🚨 Это хитрец (Впаять страйк)", callback_data=f"trap_{uid}"))
        
        if thread_id:
            # ВОСКРЕШАЕМ ТОПИК, ЕСЛИ ОН БЫЛ ЗАКРЫТ
            try: bot.reopen_forum_topic(STAFF_GROUP_ID, thread_id)
            except: pass
            
            bot.send_message(STAFF_GROUP_ID, f"🔄 **Повторный запрос на РЕКЛАМУ:**\n• ID: `{uid}`\n• Юзер: {safe_username}\n\nЕсли он просит разбан, жмите кнопку ниже 👇", message_thread_id=thread_id, parse_mode="Markdown", reply_markup=markup)
            paid_collection.update_one({"uid": uid}, {"$set": {"topic_type": "ads"}})
        else:
            topic = bot.create_forum_topic(chat_id=STAFF_GROUP_ID, name=f"💰 РЕКЛАМА | {name}")
            thread_id = topic.message_thread_id
            paid_collection.update_one({"uid": uid}, {"$set": {"thread_id": thread_id, "topic_type": "ads"}}, upsert=True)
            bot.send_message(STAFF_GROUP_ID, f"🆕 **Новый запрос на РЕКЛАМУ:**\n• ID: `{uid}`\n• Юзер: {safe_username}\n\nЕсли он просит разбан, жмите кнопку ниже 👇", message_thread_id=thread_id, parse_mode="Markdown", reply_markup=markup)
        
        return

# ================= ЛОГИКА КНОПКИ СЛУЖБЫ БЕЗОПАСНОСТИ =================
    elif call.data == "btn_report":
        # Проверяем, не улетел ли юзер в жесткий минус
        points = user_data.get("bounty_points", 0)
        if points <= -50:
            bot.send_message(call.message.chat.id, "⛔️ **Доступ закрыт.**\nСлужба безопасности отключила вам доступ к системе жалоб из-за большого количества ложных доносов.")
            return

        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(
            InlineKeyboardButton("🚨 Подать жалобу", callback_data="sec_submit_report"),
            InlineKeyboardButton(f"🏆 Кабинет Агента ({points} очков)", callback_data="sec_agent_cabinet"),
            InlineKeyboardButton("🔙 В главное меню", callback_data="sec_back_main")
        )
        
        text = (
            "🛡 **Служба Безопасности МК**\n\n"
            "Помогайте нам очищать чаты от спамеров, мошенников и нарушителей, и получайте за это баллы!\n\n"
            "Накопленные баллы можно обменять на ценные скидки или VIP-статус."
        )
        # Отправляем новое сообщение, чтобы не ломать старую разметку
        bot.send_message(call.message.chat.id, text, reply_markup=markup)
        return

# ================= МЕНЮ СЛУЖБЫ БЕЗОПАСНОСТИ =================
@bot.callback_query_handler(func=lambda call: call.data.startswith('sec_'))
def handle_security_menu(call):
    bot.answer_callback_query(call.id)
    uid = call.from_user.id
    
    if call.data == "sec_back_main":
        bot.delete_message(call.message.chat.id, call.message.message_id)
        send_welcome(call.message)
        
    elif call.data == "sec_submit_report":
        bot.send_message(
            call.message.chat.id, 
            "🕵️‍♂️ **Подача жалобы**\n\nПожалуйста, отправьте **@username** нарушителя, его **ID**, либо просто **перешлите его сообщение** сюда:"
        )
        bot.register_next_step_handler(call.message, process_report_target)
        
    elif call.data == "sec_agent_cabinet":
        user_data = paid_collection.find_one({"uid": uid}) or {}
        points = user_data.get("bounty_points", 0)
        reports_count = user_data.get("successful_reports", 0)
        shards = user_data.get("jackpot_shards", 0)
        cb_balance = user_data.get("cashback_balance", 0) # 👈 ДОСТАЕМ РУБЛИ

        markup = InlineKeyboardMarkup(row_width=2) 
        
        # 👈 НОВАЯ КНОПКА ВЫВОДА (показываем, если есть баланс)
        if cb_balance > 0:
            markup.add(InlineKeyboardButton(f"💸 Вывести / Потратить ({cb_balance}₽)", callback_data="request_cashback_payout"))

        markup.add(
            InlineKeyboardButton("🎫 -25% Штраф (30)", callback_data="buy_reward_fine25_30"),
            InlineKeyboardButton("🎫 -50% Штраф (60)", callback_data="buy_reward_fine50_60")
        )
        markup.add(
            InlineKeyboardButton("💎 -50% VIP (100)", callback_data="buy_reward_vip50_100"),
            InlineKeyboardButton("📢 -50% Реклама (150)", callback_data="buy_reward_ads50_150")
        )
        markup.add(
            InlineKeyboardButton("👑 VIP-билет БЕСПЛАТНО (300)", callback_data="buy_reward_vip100_300")
        )
        markup.add(InlineKeyboardButton("💳 ПОПОЛНИТЬ БАЛАНС (Купить очки)", callback_data="shop_points_menu"))
        
        # 👇 УМНАЯ КНОПКА ДЛЯ ОСКОЛКОВ 👇
        if shards >= 50:
            markup.add(InlineKeyboardButton("🧩 СОБРАТЬ ДЖЕКПОТ (50 осколков)", callback_data="exchange_shards"))
        else:
            markup.add(InlineKeyboardButton(f"🧩 Копите осколки ({shards}/50)", callback_data="dummy_shards"))
            
        markup.add(InlineKeyboardButton("🔙 Назад", callback_data="btn_report"))

        bot.edit_message_text(
            f"🕵️‍♂️ **Ваш профиль Агента**\n\n"
            f"💰 Баланс: **{points} очков**\n"
            f"💵 Рублевый счет: **{cb_balance} руб.**\n"
            f"📊 Успешных жалоб: **{reports_count}**\n"
            f"🧩 Осколки рулетки: **{shards} шт.**\n\n"
            f"💡 _Очки можно зарабатывать бесплатными жалобами на спамеров, либо просто купить, нажав кнопку **«ПОПОЛНИТЬ БАЛАНС»**._\n\n"
            f"*Выберите действие:*",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=markup,
            parse_mode="Markdown"
        )
        
# Обработка клика по наградам (ПОКУПКА)
@bot.callback_query_handler(func=lambda call: call.data.startswith('buy_reward_'))
def handle_reward_purchase(call):
    # ❌ УДАЛИЛИ bot.answer_callback_query(call.id) отсюда
    parts = call.data.split('_')
    reward_type = parts[2]
    price = int(parts[3])
    uid = call.from_user.id # 👈 Чуть поправил для надежности
    
    user_data = paid_collection.find_one({"uid": uid}) or {}
    points = user_data.get("bounty_points", 0)
    
    # Проверка баланса
    if points < price:
        bot.answer_callback_query(call.id, f"❌ Недостаточно очков! Нужно {price}, а у вас {points}.", show_alert=True)
        return
        
    bot.answer_callback_query(call.id, "Покупка...") # 👈 ДОБАВИЛИ СЮДА
    
    # 1. Списываем очки
    paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": -price}})
    
    # 2. Генерируем уникальный код
    code_suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
    promo_code = f"AGENT-{code_suffix}"
    
    # 3. Настраиваем логику скидки в зависимости от того, что купили
    target = "all"
    discount = 50
    
    if reward_type == "fine25": target = "fine"; discount = 25
    elif reward_type == "fine50": target = "fine"; discount = 50
    elif reward_type == "vip50": target = "vip"; discount = 50
    elif reward_type == "ads50": target = "ads"; discount = 50
    elif reward_type == "vip100": target = "vip"; discount = 100
    
    # 4. Записываем свежий промокод в базу
    db['promocodes'].insert_one({
        "_id": promo_code,
        "type": "percent",
        "value": discount,
        "target": target,      # На что действует скидка (штраф, реклама, вип)
        "usage_limit": 1,      # Купленный агентом код всегда одноразовый!
        "used_count": 0,
        "owner_uid": uid,
        "is_active": True
    })
    
    # 5. Выдаем код счастливчику
    bot.edit_message_text(
        f"🎉 **Покупка успешна!**\n\n"
        f"Вы обменяли {price} очков.\n"
        f"Ваш личный уникальный промокод:\n\n"
        f"`{promo_code}`\n\n"
        f"*(Нажмите на код, чтобы скопировать)*\n\n"
        f"ℹ️ Сохраните его! Бот спросит его у вас при следующей оплате услуг.",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode="Markdown"
    )

# ================= ВЫВОД КЭШБЭКА =================
# ================= ВЫВОД КЭШБЭКА =================
@bot.callback_query_handler(func=lambda call: call.data.startswith('request_cashback_payout'))
def handle_cashback_request(call):
    uid = call.from_user.id
    user_data = paid_collection.find_one({"uid": uid}) or {}
    cb_balance = user_data.get("cashback_balance", 0)
    
    if cb_balance < 500: # 👈 Лимит на вывод
        bot.answer_callback_query(call.id, f"❌ Минимальная сумма для вывода — 500 рублей! У вас: {cb_balance}₽.", show_alert=True)
        return
        
    bot.answer_callback_query(call.id)
    
    msg = bot.send_message(
        call.message.chat.id, 
        f"💸 **Оформление выплаты ({cb_balance} руб.)**\n\n"
        f"Пожалуйста, напишите прямо сюда ваши реквизиты для перевода:\n\n"
        f"💳 **На карту:** Номер карты и название банка (например: `4276123456789012 Сбербанк`)\n"
        f"📱 **На баланс:** Номер телефона и оператор (например: `+79001234567 МТС`)\n"
        f"💎 **В крипте:** Ваш USDT (TRC20) адрес\n\n"
        f"_Отправьте реквизиты одним сообщением:_"
    )
    bot.register_next_step_handler(msg, process_payout_details, cb_balance=cb_balance)

def process_payout_details(message, cb_balance):
    # АНТИ-КАПКАН
    if message.text == '/start':
        send_welcome(message)
        return
        
    uid = message.from_user.id
    details = message.text
    
    # Еще раз проверяем баланс для защиты от хитрецов
    user_data = paid_collection.find_one({"uid": uid}) or {}
    current_balance = user_data.get("cashback_balance", 0)
    
    if current_balance < cb_balance:
        bot.send_message(message.chat.id, "❌ Ошибка: ваш баланс изменился. Попробуйте снова.")
        return

    # Списываем баланс!
    paid_collection.update_one({"uid": uid}, {"$set": {"cashback_balance": current_balance - cb_balance}})
    
    username = f"@{message.from_user.username}" if message.from_user.username else f"ID {uid}"
    
    # Кнопки для админа
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("✅ Выплачено", callback_data=f"payout_done_{uid}_{cb_balance}"),
        InlineKeyboardButton("❌ Отклонить (Вернуть баланс)", callback_data=f"payout_cancel_{uid}_{cb_balance}")
    )
    
    bot.send_message(
        STAFF_GROUP_ID,
        f"💰 **ЗАЯВКА НА ВЫПЛАТУ КЭШБЕКА**\n\n"
        f"👤 От: {username} (`{uid}`)\n"
        f"💵 Сумма к выдаче: **{cb_balance} руб.**\n"
        f"📝 Реквизиты юзера:\n`{details}`\n\n"
        f"Сделайте перевод и нажмите кнопку подтверждения:",
        reply_markup=markup,
        parse_mode="Markdown"
    )
    
    bot.send_message(message.chat.id, "✅ **Заявка на выплату успешно создана!**\nСумма списана с баланса. Ожидайте поступления средств на указанные реквизиты.")

@bot.callback_query_handler(func=lambda call: call.data.startswith('payout_'))
def handle_payout_decision(call):
    if str(call.message.chat.id) != STAFF_GROUP_ID: return
    bot.answer_callback_query(call.id)
    
    parts = call.data.split('_')
    action = parts[1] # done или cancel
    target_uid = int(parts[2])
    amount = int(parts[3])
    
    if action == "done":
        bot.edit_message_text(f"{call.message.text}\n\n✅ **ВЫПЛАЧЕНО УСПЕШНО**", chat_id=call.message.chat.id, message_id=call.message.message_id)
        try: bot.send_message(target_uid, f"💸 **Ваша заявка на выплату ({amount} руб.) успешно обработана!** Деньги отправлены.")
        except: pass
    elif action == "cancel":
        # Возвращаем деньги на баланс
        paid_collection.update_one({"uid": target_uid}, {"$inc": {"cashback_balance": amount}})
        bot.edit_message_text(f"{call.message.text}\n\n❌ **ОТКЛОНЕНО (Деньги возвращены)**", chat_id=call.message.chat.id, message_id=call.message.message_id)
        try: bot.send_message(target_uid, f"❌ **Заявка на выплату отклонена.**\nСредства ({amount} руб.) возвращены на ваш внутренний баланс.")
        except: pass

# ================= ОБМЕННИК ОСКОЛКОВ =================
@bot.callback_query_handler(func=lambda call: call.data == 'exchange_shards')
def handle_shards_exchange(call):
    # ❌ УДАЛИЛИ bot.answer_callback_query(call.id) отсюда
    uid = call.from_user.id
    
    user_data = paid_collection.find_one({"uid": uid}) or {}
    shards = user_data.get("jackpot_shards", 0)
    
    if shards < 50:
        bot.answer_callback_query(call.id, "❌ Недостаточно осколков! Нужно 50 шт.", show_alert=True)
        return
        
    bot.answer_callback_query(call.id, "Сборка джекпота...") # 👈 ДОБАВИЛИ СЮДА
    
    # 1. Списываем 50 осколков
    paid_collection.update_one({"uid": uid}, {"$inc": {"jackpot_shards": -50}})
    
    # 2. Определяем супер-приз (50% шанс на Щит, 50% на VIP Билет)
    import random
    is_vip = random.choice([True, False])
    
    if is_vip:
        code = f"JACKPOT-{random.randint(1000, 9999)}"
        db['promocodes'].insert_one({
            "_id": code, "type": "percent", "value": 100, "target": "vip",
            "usage_limit": 1, "used_count": 0, "is_active": True
        })
        msg = f"🎉 **ПОЗДРАВЛЯЕМ!** 🎉\n\nВы собрали из осколков **Золотой Билет (VIP-доступ)**!\nВаш промокод: `{code}`\n\n_Сохраните его и введите при оплате._"
    else:
        paid_collection.update_one({"uid": uid}, {"$inc": {"immunity": 1}})
        msg = f"🛡 **ПОЗДРАВЛЯЕМ!** 🛡\n\nВы собрали из осколков **Щит Иммунитета**!\nВаш аккаунт теперь защищен от одного случайного нарушения или страйка."
        
    bot.edit_message_text(
        msg, 
        chat_id=call.message.chat.id, 
        message_id=call.message.message_id, 
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🔙 В кабинет", callback_data="sec_agent_cabinet"))
    )
    
@bot.callback_query_handler(func=lambda call: call.data == 'dummy_shards')
def handle_dummy_shards(call):
    # Кнопка-пустышка, которая просто показывает подсказку
    bot.answer_callback_query(call.id, "🧩 Соберите 50 осколков из неудачных прокруток рулетки, чтобы гарантированно получить Супер-Приз!", show_alert=True)

# ================= СИСТЕМА ОБМЕНА ПРИЗОВ (ТРЕЙД-ИН) =================
@bot.callback_query_handler(func=lambda call: call.data.startswith('trade_'))
def handle_trade_in(call):
    uid = call.from_user.id
    parts = call.data.split('_')
    
    # Формат колбэка для промокодов: trade_promo_CODE_rewardType_amount
    if parts[1] == 'promo':
        code = parts[2]
        reward_type = parts[3] # 'points' или 'shards'
        amount = int(parts[4])
        
        # 1. Проверяем, существует ли промокод и не использован ли он
        promo = db['promocodes'].find_one({"_id": code, "is_active": True, "used_count": 0})
        if not promo:
            bot.answer_callback_query(call.id, "❌ Этот промокод уже использован или был обменян ранее!", show_alert=True)
            return
            
        # 2. Удаляем промокод из базы (сжигаем его)
        db['promocodes'].delete_one({"_id": code})
        
        # 3. Начисляем награду
        reward_text = f"{amount} очков бдительности" if reward_type == 'points' else f"{amount} осколков"
        
        if reward_type == 'points':
            paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": amount}})
        else:
            paid_collection.update_one({"uid": uid}, {"$inc": {"jackpot_shards": amount}})
            
        # 4. Меняем сообщение в ЛС, чтобы кнопку больше нельзя было нажать
        bot.edit_message_text(
            f"♻️ **Приз успешно переработан!**\n\nВы уничтожили промокод `{code}`.\nПолучено: **+{reward_text}**.", 
            chat_id=call.message.chat.id, 
            message_id=call.message.message_id,
            parse_mode="Markdown"
        )

    # Формат колбэка для щитов: trade_shield_rewardType_amount
    elif parts[1] == 'shield':
        reward_type = parts[2]
        amount = int(parts[3])
        
        user_data = paid_collection.find_one({"uid": uid}) or {}
        
        # 1. Проверяем, есть ли у юзера щиты
        if user_data.get("immunity", 0) < 1:
            bot.answer_callback_query(call.id, "❌ У вас нет активных Щитов для обмена!", show_alert=True)
            return
            
        # 2. Списываем один щит
        paid_collection.update_one({"uid": uid}, {"$inc": {"immunity": -1}})
        
        # 3. Начисляем награду
        reward_text = f"{amount} очков бдительности" if reward_type == 'points' else f"{amount} осколков"
        
        if reward_type == 'points':
            paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": amount}})
        else:
            paid_collection.update_one({"uid": uid}, {"$inc": {"jackpot_shards": amount}})
            
        bot.edit_message_text(
            f"♻️ **Щит сдан в утиль!**\n\nВы разобрали 1 Щит Иммунитета.\nПолучено: **+{reward_text}**.", 
            chat_id=call.message.chat.id, 
            message_id=call.message.message_id,
            parse_mode="Markdown"
        )

# ================= МАГАЗИН ОЧКОВ =================
@bot.callback_query_handler(func=lambda call: call.data.startswith('shop_points_'))
def handle_points_shop(call):
    bot.answer_callback_query(call.id)
    uid = call.from_user.id
    
    # 1. ОТКРЫВАЕМ ВИТРИНУ
    if call.data == "shop_points_menu":
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(
            InlineKeyboardButton("📦 50 очков (1 прокрут) — 50⭐️", callback_data="shop_points_buy_50_50"),
            InlineKeyboardButton("🔥 300 очков (6 прокрутов) — 200⭐️", callback_data="shop_points_buy_300_200"),
            InlineKeyboardButton("💎 1000 очк. + 🛡 Щит — 500⭐️", callback_data="shop_points_buy_1000_500"),
            InlineKeyboardButton("🔙 В кабинет", callback_data="sec_agent_cabinet")
        )
        
        shop_text = "🎰 **Магазин Очков Бдительности**\n\nОчки можно тратить на скидки в кабинете или использовать для игры в Гача-Рулетку (`/spin`).\n\n🏆 _Посмотреть список призов: /prizes_\n\nВыберите нужный пакет:"
        
        try:
            # Пытаемся отредактировать сообщение (сработает, если перешли из обычного меню)
            bot.edit_message_text(
                shop_text,
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=markup,
                parse_mode="Markdown"
            )
        except Exception as e:
            # Если Телеграм ругается (значит мы пытаемся отредактировать инвойс)
            if "can't be edited" in str(e).lower() or "not modified" in str(e).lower():
                # Просто удаляем инвойс и шлем меню новым сообщением
                try: bot.delete_message(call.message.chat.id, call.message.message_id)
                except: pass
                bot.send_message(call.message.chat.id, shop_text, reply_markup=markup, parse_mode="Markdown")
        return

    # 2. ЕСЛИ НАЖАЛИ КУПИТЬ ПАКЕТ
    parts = call.data.split('_')
    if len(parts) == 5 and parts[2] == "buy":
        points_amount = int(parts[3])
        price_stars = int(parts[4])
        
        # 👇 Генерируем раздельные крипто-ссылки 👇
        url_usdt = get_crypto_pay_url(f"buy_points_{points_amount}_{uid}", price_stars, f"Покупка {points_amount} очков", asset="USDT")
        url_ton = get_crypto_pay_url(f"buy_points_{points_amount}_{uid}", price_stars, f"Покупка {points_amount} очков", asset="TON")
        
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(InlineKeyboardButton(text=f"⭐️ Оплатить {price_stars} Звезд", pay=True))
        if url_usdt: markup.add(InlineKeyboardButton("🟢 Оплатить USDT (CryptoBot)", url=url_usdt))
        if url_ton: markup.add(InlineKeyboardButton("💎 Оплатить TON (CryptoBot)", url=url_ton))
        markup.add(InlineKeyboardButton("🔙 Назад", callback_data="shop_points_menu"))

        description = f"Пакет: {points_amount} Очков Бдительности."
        if points_amount == 1000:
            description += "\n🎁 Бонус: 1 Щит Иммунитета!"

        bot.send_invoice(
            call.message.chat.id,
            title=f"Покупка Очков ({points_amount})",
            description=description,
            invoice_payload=f"buy_points_{points_amount}",
            provider_token="",
            currency="XTR",
            prices=[telebot.types.LabeledPrice(label="Пакет Очков", amount=price_stars)],
            reply_markup=markup
        )
        # Убираем старое сообщение с меню, чтобы не засорять чат
        try: bot.delete_message(call.message.chat.id, call.message.message_id)
        except: pass

# ================= ВОРОНКА ЖАЛОБ =================
def process_report_target(message):
    if message.text and message.text == "/start":
        send_welcome(message)
        return
        
    target_info = f"ID: {message.forward_from.id}" if message.forward_from else (message.text if message.text else "Нет текста")

    # Создаем базу с пустым массивом для улик
    db['temp_reports'].update_one(
        {"uid": message.from_user.id},
        {"$set": {"target": target_info, "media": []}},
        upsert=True
    )

    msg = bot.send_message(
        message.chat.id,
        f"✅ Цель зафиксирована: {target_info}\n\n"
        "✍️ **Теперь подробно опишите ситуацию.** Что именно произошло? (Это поможет админам быстрее разобраться)"
    )
    bot.register_next_step_handler(msg, process_report_description)

def process_report_description(message):
    # 👇 АНТИ-КАПКАН 👇
    if message.text == '/start':
        send_welcome(message)
        return
    # 👆 ============ 👆

    description = message.text if message.text else "Описание отсутствует"
    
    # Сохраняем текстовое описание
    db['temp_reports'].update_one({"uid": message.from_user.id}, {"$set": {"description": description}})

    # Включаем обычную (нижнюю) клавиатуру с кнопкой "Готово"
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add("✅ Все доказательства отправлены")

    msg = bot.send_message(
        message.chat.id,
        "📸 **Теперь отправляйте доказательства** (скриншоты, видео, аудио или пересланные сообщения).\n\n"
        "Вы можете отправить сразу **несколько файлов** подряд. Как только загрузите всё необходимое — нажмите кнопку **«✅ Все доказательства отправлены»** внизу экрана 👇",
        reply_markup=markup
    )
    bot.register_next_step_handler(msg, process_evidence_loop)

def process_evidence_loop(message):
    uid = message.from_user.id

    # 👇 АНТИ-КАПКАН 👇
    if message.text == '/start':
        bot.send_message(message.chat.id, "Отмена действия...", reply_markup=telebot.types.ReplyKeyboardRemove())
        send_welcome(message)
        return
    # 👆 ============ 👆

    # Если юзер нажал кнопку "Готово"
    if message.text == "✅ Все доказательства отправлены":
        # Убираем огромную нижнюю кнопку
        remove_markup = telebot.types.ReplyKeyboardRemove()
        bot.send_message(message.chat.id, "⏳ Формируем дело...", reply_markup=remove_markup)

        # Выводим инлайн-кнопки с финальными причинами
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("💊 Наркотики", callback_data=f"rep_drugs_{uid}"),
            InlineKeyboardButton("👶 Несовершеннолетний", callback_data=f"rep_minor_{uid}")
        )
        markup.add(
            InlineKeyboardButton("💰 Мошенник / Спам", callback_data=f"rep_scam_{uid}"),
            InlineKeyboardButton("🤬 Неадекват / Оскорбления", callback_data=f"rep_toxic_{uid}")
        )
        
        bot.send_message(
            message.chat.id,
            "❗️ **Финальный шаг: Выберите причину жалобы из списка:**\n_За ложный донос выдается страйк._",
            reply_markup=markup
        )
        return

    # Если юзер прислал медиафайл — сохраняем его в массив
    if message.content_type in ['photo', 'video', 'document', 'audio', 'voice', 'video_note']:
        ev_id = ""
        if message.content_type == 'photo': ev_id = message.photo[-1].file_id
        elif message.content_type == 'video': ev_id = message.video.file_id
        elif message.content_type == 'document': ev_id = message.document.file_id
        elif message.content_type == 'voice': ev_id = message.voice.file_id
        elif message.content_type == 'audio': ev_id = message.audio.file_id
        elif message.content_type == 'video_note': ev_id = message.video_note.file_id

        if ev_id:
            db['temp_reports'].update_one(
                {"uid": uid},
                {"$push": {"media": {"type": message.content_type, "id": ev_id}}}
            )

    # Снова зацикливаем, ждем следующий файл или нажатие "Готово"
    bot.register_next_step_handler(message, process_evidence_loop)

# ================= ФИНАЛ ЖАЛОБЫ: ОТПРАВКА АДМИНАМ =================
@bot.callback_query_handler(func=lambda call: call.data.startswith('rep_'))
def handle_report_submission(call):
    bot.answer_callback_query(call.id)
    data_parts = call.data.split('_')
    reason_code = data_parts[1]
    reporter_uid = int(data_parts[2])
    
    if call.from_user.id != reporter_uid: return
        
    reasons_dict = {"drugs": "💊 Наркотики", "minor": "👶 Несовершеннолетний", "scam": "💰 Мошенник / Спам", "toxic": "🤬 Неадекват / Оскорбления"}
    reason_text = reasons_dict.get(reason_code, "Другое")
    
    report_data = db['temp_reports'].find_one({"uid": reporter_uid})
    if not report_data:
        bot.answer_callback_query(call.id, "❌ Ошибка: данные устарели. Начните заново через /start", show_alert=True)
        return
        
    target_info = report_data.get("target", "Неизвестно")
    description = report_data.get("description", "Нет описания")
    media_list = report_data.get("media", [])
    
    bot.edit_message_text("✅ **Ваша жалоба отправлена в Службу Безопасности.**\nОжидайте проверки!", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown")
    
    reporter_name = call.from_user.first_name or f"ID {reporter_uid}"
    topic = bot.create_forum_topic(chat_id=STAFF_GROUP_ID, name=f"🚨 ЖАЛОБА | {reporter_name}")
    thread_id = topic.message_thread_id
    
    # Отправляем ВСЕ сохраненные улики по очереди
    if not media_list:
        bot.send_message(STAFF_GROUP_ID, "⚠️ *Пользователь не прикрепил медиафайлы.*", message_thread_id=thread_id, parse_mode="Markdown")
    else:
        for item in media_list:
            ev_type = item["type"]
            ev_id = item["id"]
            try:
                if ev_type == 'photo': bot.send_photo(STAFF_GROUP_ID, ev_id, message_thread_id=thread_id)
                elif ev_type == 'video': bot.send_video(STAFF_GROUP_ID, ev_id, message_thread_id=thread_id)
                elif ev_type == 'document': bot.send_document(STAFF_GROUP_ID, ev_id, message_thread_id=thread_id)
                elif ev_type == 'voice': bot.send_voice(STAFF_GROUP_ID, ev_id, message_thread_id=thread_id)
                elif ev_type == 'audio': bot.send_audio(STAFF_GROUP_ID, ev_id, message_thread_id=thread_id)
                elif ev_type == 'video_note': bot.send_video_note(STAFF_GROUP_ID, ev_id, message_thread_id=thread_id)
            except: pass
    
    # ОБНОВЛЕННЫЙ УМНЫЙ ПУЛЬТ АДМИНА
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("✅ Подтвердить нарушение (+Награда заявителю)", callback_data=f"adm_rep_reward_{reporter_uid}"),
        InlineKeyboardButton("❌ Отклонить (Мало улик)", callback_data=f"adm_rep_reject_{reporter_uid}"),
        InlineKeyboardButton("🚨 Ложный донос (Страйк)", callback_data=f"adm_rep_strike_{reporter_uid}")
    )
    
    bot.send_message(
        STAFF_GROUP_ID,
        f"🚨 **НОВАЯ ЖАЛОБА**\n\n"
        f"👤 **Заявитель:** {reporter_name} (`{reporter_uid}`)\n"
        f"🎯 **Обвиняемый:** {target_info}\n"
        f"⚠️ **Причина:** {reason_text}\n"
        f"💬 **Описание:** {description}\n\n"
        f"Проверьте доказательства выше и вынесите вердикт:",
        message_thread_id=thread_id,
        parse_mode="Markdown",
        reply_markup=markup
    )
    db['temp_reports'].delete_one({"uid": reporter_uid})

# ================= РЕАКЦИЯ АДМИНА НА ЖАЛОБУ =================
@bot.callback_query_handler(func=lambda call: call.data.startswith('adm_rep_'))
def handle_admin_report_decision(call):
    bot.answer_callback_query(call.id)
    if str(call.message.chat.id) != STAFF_GROUP_ID: return
    
    action = call.data.split('_')[2]
    reporter_uid = int(call.data.split('_')[3])
    thread_id = call.message.message_thread_id
    
    # 1. ПОДТВЕРДИТЬ И НАГРАДИТЬ (ОБЪЕДИНЕННАЯ КНОПКА)
    if action == "reward":
        paid_collection.update_one({"uid": reporter_uid}, {"$inc": {"bounty_points": 10, "successful_reports": 1}}, upsert=True)
        try: bot.send_message(reporter_uid, "🎉 **Ваша жалоба подтвердилась!**\nНарушитель наказан. Вам начислено **+10 очков бдительности**! 💰", parse_mode="Markdown")
        except: pass
        
        bot.send_message(STAFF_GROUP_ID, "✅ **Вердикт:** Нарушение подтверждено. Заявитель получил +10 очков.\n\n🔨 **Админы, забаньте нарушителя вручную через Скайнет!**", message_thread_id=thread_id, parse_mode="Markdown")
        try: bot.edit_message_text(f"{call.message.text}\n\n✅ *ЗАКРЫТО: Нарушитель признан виновным.*", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=None)
        except: pass
        
    # 2. ОТКЛОНИТЬ
    elif action == "reject":
        try: bot.send_message(reporter_uid, "❌ Ваша жалоба отклонена. Предоставленных доказательств недостаточно.")
        except: pass
        try: bot.edit_message_text(f"{call.message.text}\n\n❌ *ЗАКРЫТО: Отклонено (Мало улик).*", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=None)
        except: pass
        try: bot.close_forum_topic(STAFF_GROUP_ID, thread_id)
        except: pass
        
    # 3. ЛОЖНЫЙ ДОНОС
    elif action == "strike":
        user_data = paid_collection.find_one({"uid": reporter_uid}) or {"uid": reporter_uid, "strikes": 0, "immunity": 0}
        
        # 👇 ПРОВЕРКА НА ЩИТ ИММУНИТЕТА 👇
        if user_data.get("immunity", 0) > 0:
            # Очки за ложный донос все равно списываем, а вот страйк поглощается Щитом!
            paid_collection.update_one({"uid": reporter_uid}, {"$inc": {"immunity": -1, "bounty_points": -10}, "$unset": {"topic_type": ""}})
            
            try: bot.send_message(reporter_uid, "⛔️ **Ложный донос!**\nВы использовали систему не по назначению. Списано **-10 очков**.\n\nБот попытался выдать вам Штрафной Страйк, но ваш **🛡 Щит Иммунитета поглотил удар!**\n_Щит разрушен._", parse_mode="Markdown")
            except: pass
            
            try: bot.edit_message_text(f"{call.message.text}\n\n🛡 *ЗАКРЫТО: Юзер спасен Иммунитетом! Страйк поглощен щитом (очки списаны).* ", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=None)
            except: pass
            
            try: bot.close_forum_topic(STAFF_GROUP_ID, thread_id)
            except: pass
            return
        # 👆 ============================ 👆

        # Если щита нет - выдаем классический страйк
        new_strikes = user_data.get("strikes", 0) + 1
        paid_collection.update_one({"uid": reporter_uid}, {"$set": {"strikes": new_strikes}, "$inc": {"bounty_points": -10}, "$unset": {"topic_type": ""}}, upsert=True)
        
        try: bot.send_message(reporter_uid, f"🚨 **Внимание! Ложный донос.**\nВы использовали систему не по назначению. Списано **-10 очков**. Выдан страйк ({new_strikes}/3).", parse_mode="Markdown")
        except: pass
        
        try: bot.edit_message_text(f"{call.message.text}\n\n🚨 *ЗАКРЫТО: Выдан страйк за ложный донос.*", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=None)
        except: pass
        try: bot.close_forum_topic(STAFF_GROUP_ID, thread_id)
        except: pass

# ================= СООБЩЕНИЯ ОТ ЮЗЕРА -> АДМИНАМ =================
@bot.message_handler(func=lambda message: message.chat.type == 'private', content_types=['text', 'photo', 'document', 'video_note', 'voice', 'video', 'sticker', 'audio'])
def handle_user_messages(message):
    uid = message.from_user.id
    user_data = paid_collection.find_one({"uid": uid}) or {}
    
    # 🛑 ВЫШИБАЛА: Если у юзера 3 страйка и нет оплаты — игнор
    if user_data.get("strikes", 0) >= 3 and user_data.get("status") != 1:
        return 
        
    # 🛑 ЗАЩИТА: ЕСЛИ ТИКЕТ ЗАКРЫТ — НЕ ДАЕМ ПИСАТЬ
    topic_type = user_data.get("topic_type")
    if not topic_type:
        if user_data.get("status") == 1:
            bot.send_message(message.chat.id, "✅ Вижу вашу оплату! Пожалуйста, нажмите /start и выберите нужный раздел, чтобы мы начали.")
        else:
            bot.send_message(message.chat.id, "🏁 Ваше обращение закрыто.\nДля создания нового выберите нужный раздел в меню /start.")
        return

    # 🔗 ДОСТАЕМ THREAD_ID НАПРЯМУЮ ИЗ БАЗЫ (Без амнезии!)
    thread_id = user_data.get("thread_id")
    if not thread_id:
        bot.send_message(message.chat.id, "⚠️ Ошибка связи: Топик не найден. Пожалуйста, нажмите /start и выберите раздел заново.")
        return

    # 🧹 ФУНКЦИЯ ОЧИСТКИ ПРЕДЫДУЩИХ КНОПОК
    def cleanup_old_buttons():
        last_msg_id = user_data.get("last_admin_msg_id")
        if last_msg_id:
            try: bot.edit_message_reply_markup(STAFF_GROUP_ID, last_msg_id, reply_markup=None)
            except: pass

    # ==================== ОТПРАВКА АДМИНАМ С ЗАЩИТОЙ ====================
    try:
        # 💬 ТЕКСТ
        if message.content_type == 'text':
            if message.text.lower() in ["готов", "готова", "готовы", "готов(а)"]:
                
                # Генерируем уникальный код
                code_words = ["ЯБЛОКО", "ТИГР", "СОЛНЦЕ", "МОРЕ", "СОКОЛ", "РАКЕТА", "ВЕТЕР", "МАЯК"]
                secret_code = f"{random.choice(code_words)}-{random.randint(10, 99)}"
                
                # ЗАПИСЫВАЕМ ТАЙМЕР И КОД В БАЗУ
                paid_collection.update_one({"uid": uid}, {"$set": {"verif_timer": datetime.datetime.now(), "secret_code": secret_code}})
                
                text_phrase = f"⏳ **Таймер запущен! У вас ровно 10 минут.**\n\nЗапишите **видео-кружок**, на котором четко видно лицо, и произнесите:\n\n💬 *«Привет команде МК, я из *города* на часах: *хх:хх* часов. Мой код: {secret_code}»*."
                bot.send_message(uid, text_phrase, parse_mode="Markdown")
                bot.send_message(STAFF_GROUP_ID, f"⏳ *Пользователь написал «Готов». Бот выдал код: {secret_code} и запустил таймер 10 минут! Ждем кружок.*", message_thread_id=thread_id, parse_mode="Markdown")
                return 

            cleanup_old_buttons()
            
            if topic_type in ["unban", "ads"]: # <-- Добавил ads, чтобы кнопки были и в рекламе!
                markup = InlineKeyboardMarkup(row_width=2)
                markup.add(
                    InlineKeyboardButton("💳 250⭐️", callback_data="fine_250"),
                    InlineKeyboardButton("💳 650⭐️", callback_data="fine_650"),
                    InlineKeyboardButton("💳 1563⭐️", callback_data="fine_1563")
                )
                markup.add(InlineKeyboardButton("✍️ Указать свою сумму", callback_data="fine_custom"))
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
                markup.add(InlineKeyboardButton("🔓 РАЗБАН (Снять ограничения)", callback_data="force_unban"), InlineKeyboardButton("🏁 Закрыть (Без разбана)", callback_data="close_ticket"))
            else:
                markup = InlineKeyboardMarkup()
                markup.add(InlineKeyboardButton("🏁 Закрыть диалог", callback_data="close_ticket"))
                
            sent_msg = bot.send_message(STAFF_GROUP_ID, f"📩 {message.text}", message_thread_id=thread_id, reply_markup=markup)
            paid_collection.update_one({"uid": uid}, {"$set": {"last_admin_msg_id": sent_msg.message_id}})
        
        # 📸 ФОТО И ДОКУМЕНТЫ
        elif message.content_type in ['photo', 'document']:
            markup = InlineKeyboardMarkup(row_width=1)
            markup.add(InlineKeyboardButton("✅ Документ принят (Запросить видео)", callback_data="doc_ok"), InlineKeyboardButton("❌ Плохое фото (Перезапросить)", callback_data="doc_bad"))
            if message.photo:
                sent_msg = bot.send_photo(STAFF_GROUP_ID, message.photo[-1].file_id, caption="📸 **Пользователь прислал фото!**\nПроверьте документ:", message_thread_id=thread_id, parse_mode="Markdown", reply_markup=markup)
            else:
                sent_msg = bot.send_document(STAFF_GROUP_ID, message.document.file_id, caption="📄 **Пользователь прислал документ!**\nПроверьте:", message_thread_id=thread_id, parse_mode="Markdown", reply_markup=markup)
        
        # 🎥 КРУЖКИ
        elif message.content_type == 'video_note':
            verif_timer = user_data.get("verif_timer")
            expected_code = user_data.get("secret_code", "Неизвестно (Старая заявка)") # 👈 Достаем код
            
            if verif_timer:
                time_diff = (datetime.datetime.now() - verif_timer).total_seconds()
                if time_diff > 600:
                    bot.send_message(uid, "❌ **Время вышло!** Вы не уложились в 10 минут. Ожидайте решения администратора.")
                    bot.send_message(STAFF_GROUP_ID, "⚠️ **ВНИМАНИЕ! Юзер просрочил таймер.**", message_thread_id=thread_id)
                paid_collection.update_one({"uid": uid}, {"$unset": {"verif_timer": ""}}) # Удаляем таймер
            
            markup = InlineKeyboardMarkup(row_width=1)
            markup.add(InlineKeyboardButton("✅ Кружок принят (РАЗБАН)", callback_data="vid_ok"), InlineKeyboardButton("❌ Плохое видео (Перезапросить)", callback_data="vid_bad"))
            sent_msg = bot.send_video_note(STAFF_GROUP_ID, message.video_note.file_id, message_thread_id=thread_id, reply_markup=markup)
            bot.send_message(STAFF_GROUP_ID, f"🎥 **Пользователь прислал кружок!**\n\n🗣 **ОН ДОЛЖЕН СКАЗАТЬ:**\n_{expected_code}_", message_thread_id=thread_id, parse_mode="Markdown")

        # 🎙 ПРОЧЕЕ (Голосовые, стикеры, видео)
        elif message.content_type in ['voice', 'video', 'sticker', 'audio']:
            bot.copy_message(STAFF_GROUP_ID, message.chat.id, message.message_id, message_thread_id=thread_id)

    except Exception as e:
        # 🚨 ЛОВЕЦ ОШИБОК: Если Телеграм ругнется, мы об этом узнаем!
        bot.send_message(message.chat.id, "⚠️ Произошла ошибка при отправке сообщения. Пожалуйста, отправьте его еще раз.")
        bot.send_message(STAFF_GROUP_ID, f"❌ **СИСТЕМНАЯ ОШИБКА ДОСТАВКИ:**\nБот не смог переслать сообщение от юзера `{uid}`.\nПричина: `{e}`", message_thread_id=thread_id, parse_mode="Markdown")

# ================= ПРОВЕРКА ДОКУМЕНТОВ (КНОПКИ АДМИНА) =================
@bot.callback_query_handler(func=lambda call: call.data in ['doc_ok', 'doc_bad'])
def handle_doc_check(call):
    bot.answer_callback_query(call.id)
    if str(call.message.chat.id) != STAFF_GROUP_ID:
        return
        
    thread_id = call.message.message_thread_id
    user_data = paid_collection.find_one({"thread_id": thread_id})
    if not user_data:
        return 
    target_uid = user_data["uid"]
        
    if call.data == 'doc_ok':
        # Генерируем уникальный код
        code_words = ["ЯБЛОКО", "ТИГР", "СОЛНЦЕ", "МОРЕ", "СОКОЛ", "РАКЕТА", "ВЕТЕР", "МАЯК"]
        secret_code = f"{random.choice(code_words)}-{random.randint(10, 99)}"
        
        # ЗАПУСКАЕМ ТАЙМЕР ЧЕРЕЗ БАЗУ И ПИШЕМ КОД!
        paid_collection.update_one({"uid": target_uid}, {"$set": {"verif_timer": datetime.datetime.now(), "secret_code": secret_code}})
        
        text_to_user = f"✅ **Документ принят! Отлично.**\n\nВторой этап верификации:\nЗапишите **видео-кружок**, на котором будет четко видно ваше лицо, и произнесите фразу:\n\n💬 *«Привет команде МК, я из *города* на часах: *хх:хх* часов. Мой код: {secret_code}»*.\n\nУ вас есть 10 минут на отправку видео."
        bot.send_message(target_uid, text_to_user, parse_mode="Markdown")
        bot.edit_message_caption(f"✅ *Документ одобрен. Запрошен видео-кружок с кодом: {secret_code}.*", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=None)
        
    elif call.data == 'doc_bad':
        # Выдаем админу подменю с причинами
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(
            InlineKeyboardButton("🔎 Размыто / Засветы", callback_data="rej_doc_blur"),
            InlineKeyboardButton("🙈 Скрыты нужные данные", callback_data="rej_doc_hidden"),
            InlineKeyboardButton("📄 Не тот документ", callback_data="rej_doc_wrong")
        )
        bot.edit_message_caption("❓ **Укажите причину отказа:**", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=markup)

# ================= ОБРАБОТКА КНОПОК АДМИНА И ШТРАФОВ =================
@bot.callback_query_handler(func=lambda call: call.data.startswith('tpl_') or call.data.startswith('fine_'))
def handle_admin_templates(call):
    bot.answer_callback_query(call.id)
    if str(call.message.chat.id) != STAFF_GROUP_ID:
        return
        
    thread_id = call.message.message_thread_id
    
    user_data = paid_collection.find_one({"thread_id": thread_id})
    if not user_data: return
    target_uid = user_data["uid"]

    # --- РУЧНОЙ ВВОД СУММЫ ---
    if call.data == 'fine_custom':
        msg = bot.send_message(STAFF_GROUP_ID, "✍️ **Введите сумму штрафа (от 1 до 10000):**\n_Просто отправьте число сообщением сюда._", message_thread_id=thread_id, parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_custom_fine, target_uid=target_uid, thread_id=thread_id, call_msg=call.message)
        return

    # --- ФИКСИРОВАННЫЙ ШТРАФ (250, 650, 1563) ---
    if call.data.startswith('fine_'):
        amount = int(call.data.split('_')[1])
        try:
            # 👇 Достаем рублевый баланс юзера
            user_data_pay = paid_collection.find_one({"uid": target_uid}) or {}
            cb_balance = user_data_pay.get("cashback_balance", 0)
            cost_in_rub = amount * 2 # Курс 1 звезда = 2 рубля
            
            # Генерируем раздельные крипто-ссылки
            url_usdt = get_crypto_pay_url(f"fine_{target_uid}", amount, f"Оплата штрафа ({amount}⭐️)", asset="USDT")
            url_ton = get_crypto_pay_url(f"fine_{target_uid}", amount, f"Оплата штрафа ({amount}⭐️)", asset="TON")
            
            markup = InlineKeyboardMarkup(row_width=1)
            markup.add(InlineKeyboardButton("🎫 У меня есть промокод", callback_data=f"checkout_promo_fine_{amount}"))
            
            # 👇 ЛОГИКА СМЕШАННОЙ ОПЛАТЫ 👇
            if cb_balance >= cost_in_rub:
                markup.add(InlineKeyboardButton(f"💰 Оплатить полностью с баланса ({cost_in_rub}₽)", callback_data=f"checkout_balance_fine_{amount}"))
            elif cb_balance > 0:
                remaining_stars = amount - (cb_balance // 2)
                markup.add(InlineKeyboardButton(f"💳 Списать {cb_balance}₽ и доплатить {remaining_stars}⭐️", callback_data=f"checkout_partial_fine_{amount}_{cb_balance}"))
            else:
                markup.add(InlineKeyboardButton(f"💳 Оплатить {amount}⭐️", callback_data=f"checkout_pay_fine_{amount}"))
            
            # Раздельные красивые кнопки
            if url_usdt: markup.add(InlineKeyboardButton("🟢 Оплатить через USDT (CryptoBot)", url=url_usdt))
            if url_ton: markup.add(InlineKeyboardButton("💎 Оплатить через TON (CryptoBot)", url=url_ton))
                
            bot.send_message(
                target_uid, 
                f"🧾 **Вам выставлен счет на оплату штрафа.**\n\nСумма к оплате: **{amount}⭐️**\nПосле оплаты ограничения будут сняты автоматически.",
                reply_markup=markup,
                parse_mode="Markdown"
            )
            bot.answer_callback_query(call.id, f"✅ Чек-аут на {amount}⭐️ отправлен!")
            bot.send_message(STAFF_GROUP_ID, f"🟢 *Скайнет отправил кассу на штраф ({amount}⭐️)*", message_thread_id=thread_id, parse_mode="Markdown")
            bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
        except Exception as e:
            bot.answer_callback_query(call.id, "❌ Ошибка! Возможно бот заблокирован.", show_alert=True)
        return

    # --- СТАНДАРТНЫЕ ТЕКСТОВЫЕ ШАБЛОНЫ ---
    template_text = TEMPLATES.get(call.data)
    if template_text:
        try:
            bot.send_message(target_uid, template_text, parse_mode="Markdown")
            bot.answer_callback_query(call.id, "✅ Шаблон успешно отправлен!")
            bot.send_message(STAFF_GROUP_ID, f"🟢 *Скайнет отправил шаблон:*\n_{template_text.splitlines()[0]}_", message_thread_id=thread_id, parse_mode="Markdown")
            bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
        except Exception as e:
            if "blocked" in str(e).lower():
                bot.answer_callback_query(call.id, "❌ Ошибка: Пользователь заблокировал бота!", show_alert=True)
                bot.send_message(STAFF_GROUP_ID, "⚠️ **ОШИБКА:** Невозможно отправить шаблон. Пользователь заблокировал бота!", message_thread_id=thread_id, parse_mode="Markdown")

# ================= ОБРАБОТКА РУЧНОГО ВВОДА ШТРАФА =================
def process_custom_fine(message, target_uid, thread_id, call_msg):
    if not message.text or not message.text.isdigit():
        bot.send_message(STAFF_GROUP_ID, "❌ **Ошибка:** Нужно было отправить только число (например: 350). Попробуйте нажать кнопку заново.", message_thread_id=thread_id, parse_mode="Markdown")
        return
        
    amount = int(message.text)
    if amount < 1 or amount > 10000:
        bot.send_message(STAFF_GROUP_ID, "❌ **Ошибка:** По правилам Telegram сумма должна быть от 1 до 10000 звезд.", message_thread_id=thread_id, parse_mode="Markdown")
        return
        
    try:
        # 👇 Достаем баланс юзера
        user_data_pay = paid_collection.find_one({"uid": target_uid}) or {}
        cb_balance = user_data_pay.get("cashback_balance", 0)
        cost_in_rub = amount * 2
        
        # Генерируем раздельные крипто-ссылки
        url_usdt = get_crypto_pay_url(f"fine_{target_uid}", amount, f"Оплата штрафа ({amount}⭐️)", asset="USDT")
        url_ton = get_crypto_pay_url(f"fine_{target_uid}", amount, f"Оплата штрафа ({amount}⭐️)", asset="TON")
        
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(InlineKeyboardButton("🎫 У меня есть промокод", callback_data=f"checkout_promo_fine_{amount}"))
        
        # 👇 ЛОГИКА СМЕШАННОЙ ОПЛАТЫ 👇
        if cb_balance >= cost_in_rub:
            markup.add(InlineKeyboardButton(f"💰 Оплатить полностью с баланса ({cost_in_rub}₽)", callback_data=f"checkout_balance_fine_{amount}"))
        elif cb_balance > 0:
            remaining_stars = amount - (cb_balance // 2)
            markup.add(InlineKeyboardButton(f"💳 Списать {cb_balance}₽ и доплатить {remaining_stars}⭐️", callback_data=f"checkout_partial_fine_{amount}_{cb_balance}"))
        else:
            markup.add(InlineKeyboardButton(f"💳 Оплатить {amount}⭐️", callback_data=f"checkout_pay_fine_{amount}"))
        
        # Раздельные красивые кнопки
        if url_usdt: markup.add(InlineKeyboardButton("🟢 Оплатить через USDT (CryptoBot)", url=url_usdt))
        if url_ton: markup.add(InlineKeyboardButton("💎 Оплатить через TON (CryptoBot)", url=url_ton))
            
        bot.send_message(
            target_uid, 
            f"🧾 **Вам выставлен счет на оплату штрафа.**\n\nСумма к оплате: **{amount}⭐️**\nПосле оплаты ограничения будут сняты автоматически.",
            reply_markup=markup,
            parse_mode="Markdown"
        )
        bot.send_message(STAFF_GROUP_ID, f"🟢 *Скайнет отправил кассу на штраф ({amount}⭐️) по вашему поручению.*", message_thread_id=thread_id, parse_mode="Markdown")
        try: bot.edit_message_reply_markup(chat_id=call_msg.chat.id, message_id=call_msg.message_id, reply_markup=None)
        except: pass
    except Exception:
        bot.send_message(STAFF_GROUP_ID, f"⚠️ **ОШИБКА:** Не удалось выставить счет.", message_thread_id=thread_id, parse_mode="Markdown")

# ================= ЧЕК-АУТ И ОПЛАТА =================
@bot.callback_query_handler(func=lambda call: call.data.startswith('checkout_'))
def handle_checkout(call):
    bot.answer_callback_query(call.id)
    parts = call.data.split('_')
    action = parts[1] # "promo", "pay", "partial", "balance"
    target_type = parts[2] # "fine", "ads", "vip"
    original_amount = int(parts[3])
    
    # 1. Если юзер просто хочет оплатить
    if action == "pay":
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        bot.send_invoice(
            call.message.chat.id, 
            title=f"Оплата ({original_amount}⭐️)", 
            description="Оплата услуг бота. После оплаты ограничения будут сняты.", 
            invoice_payload=f"{target_type}_payment_{original_amount}", 
            provider_token="", 
            currency="XTR", 
            prices=[telebot.types.LabeledPrice(label="К оплате", amount=original_amount)]
        )
        
    # 2. Если юзер нажал "Ввести промокод"
    elif action == "promo":
        msg = bot.send_message(call.message.chat.id, "👇 **Введите ваш промокод ответом на это сообщение:**", parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_promo_code, target_type=target_type, original_amount=original_amount, call_msg=call.message)

    # 3. Смешанная оплата (Часть рублями, остаток Звездами)
    elif action == "partial":
        used_rubles = int(parts[4])
        
        user_data = paid_collection.find_one({"uid": call.from_user.id}) or {}
        if user_data.get("cashback_balance", 0) < used_rubles:
            bot.answer_callback_query(call.id, "❌ Ошибка: ваш рублевый баланс изменился!", show_alert=True)
            return
            
        remaining_stars = original_amount - (used_rubles // 2)
        if remaining_stars < 1: remaining_stars = 1
        
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        
        bot.send_invoice(
            call.message.chat.id, 
            title="Оплата штрафа (Смешанная)", 
            description=f"Штраф: {original_amount}⭐️\nСписано: {used_rubles}₽\nК доплате: {remaining_stars}⭐️", 
            invoice_payload=f"finepartial_{original_amount}_{used_rubles}", 
            provider_token="", 
            currency="XTR", 
            prices=[telebot.types.LabeledPrice(label="К оплате", amount=remaining_stars)]
        )

def process_promo_code(message, target_type, original_amount, call_msg):
    if message.text == '/start':
        send_welcome(message)
        return

    try: bot.edit_message_reply_markup(call_msg.chat.id, call_msg.message_id, reply_markup=None)
    except: pass
    
    promo_text = message.text.strip().upper()
    promo_data = db['promocodes'].find_one({"_id": promo_text})
    
    if not promo_data or not promo_data.get("is_active"):
        bot.send_message(message.chat.id, "❌ Промокод не найден или уже недействителен. Выставляем полный счет.")
        bot.send_invoice(message.chat.id, title=f"Оплата ({original_amount}⭐️)", description="Оплата услуг.", invoice_payload=f"{target_type}_payment_{original_amount}", provider_token="", currency="XTR", prices=[telebot.types.LabeledPrice(label="К оплате", amount=original_amount)])
        return
        
    if promo_data["used_count"] >= promo_data["usage_limit"]:
        bot.send_message(message.chat.id, "❌ Лимит активаций этого промокода исчерпан.")
        bot.send_invoice(message.chat.id, title=f"Оплата ({original_amount}⭐️)", description="Оплата услуг.", invoice_payload=f"{target_type}_payment_{original_amount}", provider_token="", currency="XTR", prices=[telebot.types.LabeledPrice(label="К оплате", amount=original_amount)])
        return
        
    if promo_data["target"] != "all" and promo_data["target"] != target_type:
        bot.send_message(message.chat.id, "❌ Этот промокод нельзя применить к данной услуге.")
        bot.send_invoice(message.chat.id, title=f"Оплата ({original_amount}⭐️)", description="Оплата услуг.", invoice_payload=f"{target_type}_payment_{original_amount}", provider_token="", currency="XTR", prices=[telebot.types.LabeledPrice(label="К оплате", amount=original_amount)])
        return

    discount = promo_data["value"]
    new_amount = original_amount
    
    if promo_data["type"] == "percent":
        new_amount = int(original_amount * (1 - discount / 100))
    elif promo_data["type"] == "fixed":
        new_amount = original_amount - discount
        
    if new_amount < 1:
        new_amount = 1 
        
    db['promocodes'].update_one({"_id": promo_text}, {"$inc": {"used_count": 1}})
    
    bot.send_message(message.chat.id, f"✅ **Промокод успешно применен!**\nСкидка составила {original_amount - new_amount}⭐️. Счет пересчитан.", parse_mode="Markdown")
    
    bot.send_invoice(
        message.chat.id, 
        title=f"Оплата со скидкой ({new_amount}⭐️)", 
        description="Оплата услуг бота с учетом промокода.", 
        invoice_payload=f"{target_type}_payment_{new_amount}", 
        provider_token="", 
        currency="XTR", 
        prices=[telebot.types.LabeledPrice(label="К оплате", amount=new_amount)]
    )

# ================= ПРОВЕРКА КРУЖКА И ФИНАЛ (КНОПКИ АДМИНА) =================
@bot.callback_query_handler(func=lambda call: call.data in ['vid_ok', 'vid_bad'])
def handle_vid_check(call):
    bot.answer_callback_query(call.id)
    if str(call.message.chat.id) != STAFF_GROUP_ID:
        return
        
    thread_id = call.message.message_thread_id
    user_data = paid_collection.find_one({"thread_id": thread_id})
    if not user_data:
        return 
    target_uid = user_data["uid"]
        
    if call.data == 'vid_ok':
        now = datetime.datetime.now()
        ticket_num = now.strftime("%d%m%Y%H%M%S") + f"-{random.randint(100, 999)}"
        
        # 1. ПЕРЕДАЕМ ПРИКАЗ СКАЙНЕТУ ЧЕРЕЗ БАЗУ
        db['skynet_tasks'].insert_one({
            "uid": target_uid,
            "action": "full_unban",
            "timestamp": now
        })

        # 1.5. АВТОМАТИЧЕСКАЯ ВЫДАЧА ТЕГА СКАЙНЕТА!
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
        
        # 2. ОТПРАВЛЯЕМ РАДОСТНУЮ ВЕСТЬ
        try:
            text_success = f"🎉 **Ограничения удалены, выдан тег верифицированного участника!** ❤️\n\n🔒 **Обращение закрыто. Уникальный номер:** `{ticket_num}`\n\n{NETWORK_LINKS}"
            bot.send_message(target_uid, text_success, parse_mode="Markdown", disable_web_page_preview=True)
            
            # 3. ОТПРАВЛЯЕМ МЕНЮ ОЦЕНКИ + КНОПКУ ДОНАТА
            markup = InlineKeyboardMarkup(row_width=5)
            markup.add(
                InlineKeyboardButton("1⭐", callback_data=f"rate_1_{thread_id}"),
                InlineKeyboardButton("2⭐", callback_data=f"rate_2_{thread_id}"),
                InlineKeyboardButton("3⭐", callback_data=f"rate_3_{thread_id}"),
                InlineKeyboardButton("4⭐", callback_data=f"rate_4_{thread_id}"),
                InlineKeyboardButton("5⭐", callback_data=f"rate_5_{thread_id}")
            )
            markup.add(InlineKeyboardButton("💸 Отправить чаевые админам (Донат) ⭐️", callback_data="start_donate"))
            
            bot.send_message(target_uid, "🏁 Пожалуйста, оцените работу службы поддержки. Нам важно ваше мнение! 👇", reply_markup=markup)
        except Exception as e:
            bot.send_message(STAFF_GROUP_ID, "⚠️ **Внимание:** Пользователь заблокировал бота, поэтому не получил сообщение о разбане.", message_thread_id=thread_id)
        
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
        bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
        bot.send_message(call.message.chat.id, f"✅ *Видео-кружок одобрен!*\nЮзер верифицирован. Приказ на размут передан Скайнету. Тикет закрыт: `{ticket_num}`", message_thread_id=thread_id, parse_mode="Markdown")
        
        try:
            bot.close_forum_topic(STAFF_GROUP_ID, thread_id)
        except Exception as e:
            pass 
        
        paid_collection.update_one({"uid": target_uid}, {"$set": {"status": 0}, "$unset": {"topic_type": ""}})
        
    elif call.data == 'vid_bad':
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(
            InlineKeyboardButton("👤 Не видно лицо", callback_data="rej_vid_face"),
            InlineKeyboardButton("🤐 Не та фраза / Нет времени", callback_data="rej_vid_phrase"),
            InlineKeyboardButton("🔇 Нет звука / Тишина", callback_data="rej_vid_sound")
        )
        bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

# ================= ОБРАБОТКА ПРИЧИН ОТКАЗА =================
@bot.callback_query_handler(func=lambda call: call.data.startswith('rej_'))
def handle_rejections(call):
    bot.answer_callback_query(call.id)
    if str(call.message.chat.id) != STAFF_GROUP_ID:
        return
        
    thread_id = call.message.message_thread_id
    user_data = paid_collection.find_one({"thread_id": thread_id})
    if not user_data:
        return 
    target_uid = user_data["uid"]
        
    # Словари с текстами для юзера и отчетами для админа
    reasons_user = {
        "rej_doc_blur": "❌ **Документ не принят.**\nФотография размыта или имеет сильные засветы. Пожалуйста, сделайте более четкое фото и отправьте снова.",
        "rej_doc_hidden": "❌ **Документ не принят.**\nСкрыты необходимые данные. Повторите отправку, оставив открытыми **дату рождения и лицо**.",
        "rej_doc_wrong": "❌ **Документ не принят.**\nПредоставленный документ не входит в официальный перечень. Пришлите паспорт, ВУ, ВНЖ или военный билет.",
        "rej_vid_face": "❌ **Видео-кружок не принят.**\nНа видео плохо видно ваше лицо (темно или обрезано). Запишите кружок при хорошем освещении.",
        "rej_vid_phrase": "❌ **Видео-кружок не принят.**\nВы произнесли не ту фразу. Пожалуйста, посмотрите вашу секретную фразу выше и запишите кружок снова.",
        "rej_vid_sound": "❌ **Видео-кружок не принят.**\nНа видео отсутствует звук. Проверьте микрофон устройства и отправьте кружок повторно."
    }
    
    reasons_admin = {
        "rej_doc_blur": "Размыто", "rej_doc_hidden": "Скрыты данные", "rej_doc_wrong": "Не тот документ",
        "rej_vid_face": "Не видно лицо", "rej_vid_phrase": "Неверная фраза", "rej_vid_sound": "Нет звука"
    }
    
    text_to_user = reasons_user.get(call.data)
    admin_report = reasons_admin.get(call.data)
    
    if text_to_user:
        bot.send_message(target_uid, text_to_user, parse_mode="Markdown")
        try:
            bot.edit_message_caption(f"❌ *Отклонено (Причина: {admin_report}). Запрошено повторно.*", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=None)
        except Exception:
            bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
            bot.send_message(call.message.chat.id, f"❌ *Отклонено (Причина: {admin_report}). Запрошено повторно.*", message_thread_id=thread_id, parse_mode="Markdown")

# ================= КАПКАН ДЛЯ ХИТРЕЦОВ =================
@bot.callback_query_handler(func=lambda call: call.data.startswith('trap_'))
def handle_trap(call):
    if str(call.message.chat.id) != STAFF_GROUP_ID:
        return
        
    target_uid = int(call.data.split('_')[1])
    thread_id = call.message.message_thread_id
    
    # 1. Снимаем "залипание" кнопки мгновенно!
    bot.answer_callback_query(call.id, "Обработка...")
    
    # Достаем юзера и проверяем его защиту
    user_data = paid_collection.find_one({"uid": target_uid}) or {"uid": target_uid, "strikes": 0, "immunity": 0}
    
    # 👇 МАГИЯ ЩИТА ИММУНИТЕТА 👇
    if user_data.get("immunity", 0) > 0:
        # Списываем один щит, страйк НЕ выдаем!
        paid_collection.update_one({"uid": target_uid}, {"$inc": {"immunity": -1}, "$unset": {"topic_type": ""}})
        
        try:
            bot.send_message(target_uid, "⛔️ **Вы нарушили правила!**\n\nБот попытался выдать вам Штрафной Страйк, но ваш **🛡 Щит Иммунитета поглотил удар!**\n_Щит разрушен. Будьте осторожны в следующий раз._", parse_mode="Markdown")
        except: pass
        
        try:
            new_text = f"{call.message.html}\n\n🛡 <b>Юзер спасен Иммунитетом!</b> Страйк поглощен щитом. Топик закрыт."
            bot.edit_message_text(new_text, chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML", reply_markup=None)
        except: pass
        
        try: bot.close_forum_topic(STAFF_GROUP_ID, thread_id)
        except: pass
        return
    # 👆 ======================= 👆

    # Обычная логика, если щита нет:
    new_strikes = user_data.get("strikes", 0) + 1
    paid_collection.update_one({"uid": target_uid}, {"$set": {"strikes": new_strikes}, "$unset": {"topic_type": ""}}, upsert=True)
    
    # 2. Бьем юзера током (С ЗАЩИТОЙ ОТ БЛОКИРОВКИ)
    try:
        bot.send_message(target_uid, f"⛔️ **Вы выбрали раздел 'Реклама' для обхода системы.**\nВам начислен штрафной страйк за спам ({new_strikes}/3)! Для разбана используйте платную поддержку.")
    except Exception as e:
        bot.send_message(STAFF_GROUP_ID, "⚠️ **Внимание:** Хитрец уже заблокировал бота, поэтому сообщение о страйке ему не доставлено.", message_thread_id=thread_id)
    
    # 3. Сохраняем старый текст, дописываем статус и УБИРАЕМ КНОПКУ
    try:
        new_text = f"{call.message.html}\n\n🚨 <b>Хитрец пойман!</b> Ему начислен страйк ({new_strikes}/3). Топик закрыт."
        bot.edit_message_text(new_text, chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML", reply_markup=None)
    except: pass
    
    try: bot.close_forum_topic(STAFF_GROUP_ID, thread_id)
    except: pass
    
    # 4. Закрываем топик
    try:
        bot.close_forum_topic(STAFF_GROUP_ID, thread_id)
    except Exception:
        pass

# ================= БЫСТРАЯ БЛОКИРОВКА АДМИНОМ =================
@bot.callback_query_handler(func=lambda call: call.data.startswith('ban_'))
def handle_fast_ban(call):
    bot.answer_callback_query(call.id)
    if str(call.message.chat.id) != STAFF_GROUP_ID: return
    target_uid = int(call.data.split('_')[1])
    thread_id = call.message.message_thread_id
    
    # Выдаем 3 страйка и аннулируем билет
    paid_collection.update_one({"uid": target_uid}, {"$set": {"strikes": 3, "status": 0}, "$unset": {"topic_type": ""}}, upsert=True)
    
    # ЗАЩИТА: Пытаемся отправить сообщение о бане, но не падаем, если юзер мертв
    try:
        bot.send_message(target_uid, "⛔️ **Вы были заблокированы администратором за нарушение правил общения.**")
    except Exception as e:
        # Если юзер удален, просто пропускаем ошибку
        pass
    
    # Сохраняем старый текст и дописываем статус
    try:
        new_text = f"{call.message.html}\n\n🚷 <b>Юзер заблокирован администратором! Топик закрыт.</b>"
        bot.edit_message_text(new_text, chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML")
    except: pass
    
    try: bot.close_forum_topic(STAFF_GROUP_ID, thread_id)
    except: pass
        
# ================= ФИНАЛЬНОЕ ЗАКРЫТИЕ ТИКЕТА С ОЦЕНКОЙ =================
@bot.callback_query_handler(func=lambda call: call.data == "close_ticket")
def handle_close_ticket(call):
    bot.answer_callback_query(call.id)
    if str(call.message.chat.id) != STAFF_GROUP_ID:
        return
        
    thread_id = call.message.message_thread_id
    user_data = paid_collection.find_one({"thread_id": thread_id})
    if not user_data:
        return 
    target_uid = user_data["uid"]
        
    # 1. Отправляем пользователю меню оценки (с защитой от вылета)
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
    
    try:
        bot.send_message(
            target_uid, 
            "🏁 **Ваше обращение закрыто.**\n\nПожалуйста, оцените работу службы поддержки. Нам важно ваше мнение! 👇", 
            reply_markup=markup
        )
    except Exception as e:
        bot.send_message(STAFF_GROUP_ID, f"ℹ️ Не удалось отправить запрос оценки юзеру `{target_uid}` (удален или заблокировал бота).", message_thread_id=thread_id)

    
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
    
    # 3. Информируем админов (СОХРАНЯЯ ТЕКСТ)
    try:
        new_text = f"{call.message.html}\n\n🏁 <b>Тикет закрыт.</b> Пользователю отправлен запрос оценки."
        bot.edit_message_text(new_text, chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML")
    except: 
        # РЕЗЕРВНЫЙ ПЛАН: Если админ нажал закрыть на ФОТОГРАФИИ, текст поменять нельзя. 
        # Мы просто УДАЛЯЕМ саму кнопку, чтобы она не висела!
        try: bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
        except: pass
    
    try:
        bot.close_forum_topic(STAFF_GROUP_ID, thread_id)
    except Exception:
        pass
    paid_collection.update_one({"uid": target_uid}, {"$set": {"status": 0}, "$unset": {"topic_type": ""}})

# ================= ДОНАТЫ (ЧАЕВЫЕ) =================
@bot.callback_query_handler(func=lambda call: call.data == "start_donate")
def handle_start_donate(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "💖 **Спасибо за желание поддержать нас!**\n\nПожалуйста, отправьте сообщением сумму, которую хотите задонатить (от 1 до 10000 ⭐️):")
    bot.register_next_step_handler(msg, process_donate_amount)

def process_donate_amount(message):
    # 👇 АНТИ-КАПКАН 👇
    if message.text == '/start':
        send_welcome(message)
        return
    # 👆 ============ 👆

    if not message.text or not message.text.isdigit():
        bot.send_message(message.chat.id, "❌ Ошибка: нужно ввести только число (например: 100). Попробуйте нажать кнопку доната еще раз.")
        return

    amount = int(message.text)
    if amount < 1 or amount > 10000:
        bot.send_message(message.chat.id, "❌ Сумма должна быть от 1 до 10000.")
        return

    # 👇 Генерируем раздельные крипто-ссылки для доната 👇
    url_usdt = get_crypto_pay_url(f"donation_{message.from_user.id}", amount, f"Чаевые проекту ({amount}⭐️)", asset="USDT")
    url_ton = get_crypto_pay_url(f"donation_{message.from_user.id}", amount, f"Чаевые проекту ({amount}⭐️)", asset="TON")
    
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton(text=f"⭐️ Отправить {amount} Звезд", pay=True)) # <- Кнопка Телеграма
    
    # Раздельные красивые кнопки
    if url_usdt:
        markup.add(InlineKeyboardButton("🟢 Донат через USDT (CryptoBot)", url=url_usdt))
    if url_ton:
        markup.add(InlineKeyboardButton("💎 Донат через TON (CryptoBot)", url=url_ton))

    # Выставляем счет ровно на ту сумму, которую захотел юзер
    bot.send_invoice(
        message.chat.id,
        title="Чаевые проекту 💖",
        description="Добровольное пожертвование на развитие сервиса.",
        invoice_payload=f"donation_{amount}",
        provider_token="",
        currency="XTR",
        prices=[telebot.types.LabeledPrice(label="Донат", amount=amount)],
        reply_markup=markup # <--- ДОБАВИЛИ НАШИ КНОПКИ В СЧЕТ
    )
        
# ================= ОБРАБОТКА ОЦЕНКИ И ПРЕМИЙ =================
@bot.callback_query_handler(func=lambda call: call.data.startswith('rate_'))
def handle_rating(call):
    # Разбираем данные: rate_ОЦЕНКА_THREADID
    _, rating, t_id = call.data.split('_')
    t_id = int(t_id)
    
    # Благодарим юзера всплывающим окном
    bot.answer_callback_query(call.id, f"Спасибо за вашу оценку {rating}⭐!")
    
    # === ИСПРАВЛЕНИЕ: ОСТАВЛЯЕМ КНОПКУ ЧАЕВЫХ ===
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("💸 Отправить чаевые админам (Донат) ⭐️", callback_data="start_donate"))
    
    # Меняем текст на "Спасибо", убираем звезды, но возвращаем кнопку доната!
    bot.edit_message_text(
        f"🙏 Спасибо за оценку {rating}⭐! Мы работаем для вас.", 
        chat_id=call.message.chat.id, 
        message_id=call.message.message_id,
        reply_markup=markup
    )
    
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
    bot.answer_callback_query(call.id)
    if str(call.message.chat.id) != STAFF_GROUP_ID:
        return
        
    thread_id = call.message.message_thread_id
    
    user_data = paid_collection.find_one({"thread_id": thread_id})
    if not user_data: return
    target_uid = user_data["uid"]
    
    now = datetime.datetime.now()
    ticket_num = now.strftime("%d%m%Y%H%M%S") + f"-{random.randint(100, 999)}"
    
    # Передаем приказ Скайнету
    db['skynet_tasks'].insert_one({
        "uid": target_uid,
        "action": "full_unban",
        "timestamp": now
    })
    
    # КТО НАЖАЛ КНОПКУ? Запоминаем админа
    admin_username = call.from_user.username or call.from_user.first_name
    db['ticket_ratings'].update_one({"thread_id": thread_id}, {"$set": {"admin": admin_username, "uid": target_uid}}, upsert=True)
    
    # 2. ОТПРАВЛЯЕМ РАДОСТНУЮ ВЕСТЬ
    try:
        text_success = f"🎉 **Ваши ограничения успешно сняты!** ❤️\n\n🔒 **Обращение закрыто. Уникальный номер:** `{ticket_num}`\n\n{NETWORK_LINKS}"
        bot.send_message(target_uid, text_success, parse_mode="Markdown", disable_web_page_preview=True)
        
        # 3. ОТПРАВЛЯЕМ МЕНЮ ОЦЕНКИ + КНОПКУ ДОНАТА
        markup = InlineKeyboardMarkup(row_width=5)
        markup.add(
            InlineKeyboardButton("1⭐", callback_data=f"rate_1_{thread_id}"),
            InlineKeyboardButton("2⭐", callback_data=f"rate_2_{thread_id}"),
            InlineKeyboardButton("3⭐", callback_data=f"rate_3_{thread_id}"),
            InlineKeyboardButton("4⭐", callback_data=f"rate_4_{thread_id}"),
            InlineKeyboardButton("5⭐", callback_data=f"rate_5_{thread_id}")
        )
        markup.add(InlineKeyboardButton("💸 Отправить чаевые админам (Донат) ⭐️", callback_data="start_donate"))
        
        bot.send_message(target_uid, "🏁 Пожалуйста, оцените работу службы поддержки. Нам важно ваше мнение! 👇", reply_markup=markup)
    except Exception as e:
        bot.send_message(STAFF_GROUP_ID, "⚠️ **Внимание:** Пользователь заблокировал бота, поэтому не получил сообщение о разбане.", message_thread_id=thread_id)
    
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
    
    # 5. ЗАКРЫВАЕМ ТЕМУ В АДМИНКЕ (СОХРАНЯЯ ТЕКСТ)
    try:
        new_text = f"{call.message.html}\n\n🔓 <b>Пользователь разбанен!</b> Приказ передан Скайнету. Тикет закрыт: {ticket_num}"
        bot.edit_message_text(new_text, chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML")
    except: 
        # РЕЗЕРВНЫЙ ПЛАН: просто убираем кнопку!
        try: bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
        except: pass
    
    try: bot.close_forum_topic(STAFF_GROUP_ID, thread_id)
    except Exception: pass
    
    # Аннулируем билет
    paid_collection.update_one({"uid": target_uid}, {"$set": {"status": 0}, "$unset": {"topic_type": ""}})

# ================= РУЧНЫЕ ОТВЕТЫ ОТ АДМИНА -> ЮЗЕРУ =================

@bot.message_handler(
    func=lambda message:
        str(message.chat.id) == STAFF_GROUP_ID
        and message.is_topic_message
        and not message.from_user.is_bot,
    content_types=[
        'text', 'photo', 'video', 'document', 'voice', 
        'audio', 'sticker', 'video_note', 'animation'
    ]
)
def handle_admin_replies(message):
    thread_id = message.message_thread_id

    # НОВЫЙ БЛОК:
    user_data = paid_collection.find_one({"thread_id": thread_id})
    if not user_data: return
    target_uid = user_data["uid"]

    # 🔓 Разрешаем юзеру отвечать
    paid_collection.update_one(
        {"uid": target_uid},
        {"$set": {"topic_type": "manual"}}
    )

    try:
        # пересылка сообщения админу -> юзеру
        bot.copy_message(
            target_uid,
            STAFF_GROUP_ID,
            message.message_id
        )
    except Exception as e:
        error_text = str(e).lower()
        if "blocked" in error_text:
            bot.send_message(
                STAFF_GROUP_ID,
                "⚠️ Пользователь заблокировал бота.",
                message_thread_id=thread_id
            )
        elif "not found" in error_text:
            bot.send_message(
                STAFF_GROUP_ID,
                "⚠️ Чат с пользователем не найден.",
                message_thread_id=thread_id
            )
        else:
            print(f"[ADMIN_REPLY_ERROR] {e}")

@bot.message_handler(commands=['give'])
def handle_give_cmd(message):
    # Команда работает только в админской группе
    if str(message.chat.id) != STAFF_GROUP_ID:
        return
        
    # Ожидаемый формат: /give 123456789 points 50
    # Или: /give 123456789 shards 5
    args = message.text.split()
    if len(args) != 4:
        bot.reply_to(message, "❌ **Ошибка формата!**\nИспользуйте: `/give [ID] [points/shards] [сумма]`\n\n*Пример:* `/give 123456789 points 100`", parse_mode="Markdown")
        return
        
    try:
        target_uid = int(args[1])
        currency = args[2].lower()
        amount = int(args[3])
        
        paid_collection = db['paid_users']
        
        if currency in ['points', 'очки']:
            paid_collection.update_one({"uid": target_uid}, {"$inc": {"bounty_points": amount}}, upsert=True)
            bot.reply_to(message, f"✅ Выдано **{amount} Очков Бдительности** пользователю `{target_uid}`.", parse_mode="Markdown")
            try: bot.send_message(target_uid, f"🎁 **Бонус от администрации!**\nВам начислено: **{amount} Очков Бдительности**.", parse_mode="Markdown")
            except: pass
            
        elif currency in ['shards', 'осколки']:
            paid_collection.update_one({"uid": target_uid}, {"$inc": {"jackpot_shards": amount}}, upsert=True)
            bot.reply_to(message, f"✅ Выдано **{amount} Осколков** пользователю `{target_uid}`.", parse_mode="Markdown")
            try: bot.send_message(target_uid, f"🧩 **Бонус от администрации!**\nВам начислено: **{amount} Осколков рулетки**.", parse_mode="Markdown")
            except: pass
            
        else:
            bot.reply_to(message, "❌ Неизвестная валюта. Используйте `points` (очки) или `shards` (осколки).")
            
    except ValueError:
        bot.reply_to(message, "❌ Ошибка: ID пользователя и сумма должны быть числами.")

# Функция для тихого самоуничтожения сообщений
def auto_delete_message(chat_id, message_id):
    try:
        bot.delete_message(chat_id, message_id)
    except:
        pass

# ================= АНТИ-СПАМ СМАЙЛИКАМИ В ЧАТАХ =================
@bot.message_handler(content_types=['dice'], func=lambda message: message.chat.type in ['group', 'supergroup'])
def handle_manual_dice(message):
    try:
        # 1. Молча удаляем брошенный вручную смайлик
        bot.delete_message(message.chat.id, message.message_id)
        
        # 2. Выдаем предупреждение
        warning = bot.send_message(
            message.chat.id, 
            f"⚠️ @{message.from_user.username}, рулетка с реальными призами работает **только через команду** `/казино` (или `/spin`)!\nПростые смайлики здесь бессильны 😅",
            parse_mode="Markdown"
        )
        
        # 3. Самоуничтожение предупреждения через 15 секунд, чтобы не мусорить в чате
        threading.Timer(15.0, auto_delete_message, args=(message.chat.id, warning.message_id)).start()
    except Exception:
        # Если у бота нет прав удалять сообщения, он просто промолчит
        pass

# ================= КАЗИНО (РУЛЕТКА) =================
# Функция для тихого самоуничтожения сообщений
def auto_delete_message(chat_id, message_id):
    try:
        bot.delete_message(chat_id, message_id)
    except:
        pass

@bot.message_handler(commands=['spin', 'казино', 'рулетка'])
def handle_casino_spin(message):
    uid = message.from_user.id
    user_data = paid_collection.find_one({"uid": uid}) or {}
    points = user_data.get("bounty_points", 0)

    SPIN_PRICE = 50 # Стоимость одной прокрутки

    if points < SPIN_PRICE:
        # Динамически получаем юзернейм вашего бота для формирования ссылки
        bot_info = bot.get_me()
        bot_username = bot_info.username
        
        # Кнопка, ведущая в ЛС бота сразу в магазин
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("💳 Купить очки (В ЛС бота)", url=f"https://t.me/{bot_username}?start=shop"))
        
        msg = bot.reply_to(
            message, 
            f"❌ **Недостаточно Очков Бдительности!**\n\n"
            f"Стоимость прокрутки: `{SPIN_PRICE}` очков.\n"
            f"Ваш баланс: `{points}` очков.\n\n"
            f"💡 _Нажмите кнопку ниже, чтобы мгновенно перейти в магазин._", 
            parse_mode="Markdown",
            reply_markup=markup
        )
        
        # Запускаем таймеры на удаление сообщения бота и команды юзера через 180 секунд (3 минуты)
        threading.Timer(180.0, auto_delete_message, args=(message.chat.id, msg.message_id)).start()
        threading.Timer(180.0, auto_delete_message, args=(message.chat.id, message.message_id)).start()
        return

    # 1. Списываем очки за прокрут
    paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": -SPIN_PRICE}}, upsert=True)

    # 2. Кидаем анимированные слоты в чат
    sent_dice = bot.send_dice(message.chat.id, emoji='🎰')
    val = sent_dice.dice.value # Телеграм сам генерирует честный рандом (1-64)

    # 3. Запускаем обработку в фоновом потоке, чтобы бот не завис на время анимации
    threading.Thread(target=process_spin_result, args=(message, sent_dice, val, uid), daemon=True).start()

def process_spin_result(message, sent_dice, val, uid):
    import time
    import random
    time.sleep(2.2) 

    user_data = paid_collection.find_one({"uid": uid}) or {}
    points = user_data.get("bounty_points", 0)
    pm_msg = None
    pm_markup = None

    # 👇 ДОСТАЕМ КАССУ КАЗИНО 👇
    bank_data = db['casino_bank'].find_one({"_id": "premium_fund"}) or {"balance": 0}
    premium_cost_stars = 1500 # Цена 3-х месяцев премиума в звездах (укажите вашу)

    # 1. 🏆 СУПЕР-ПРИЗ: TELEGRAM PREMIUM (Шанс 1 из 64 — val: 63)
    if val == 63:
        # Проверяем, накопило ли казино на Премиум?
        if bank_data.get("balance", 0) >= premium_cost_stars:
            # Касса полная! Списываем стоимость и выдаем приз
            db['casino_bank'].update_one({"_id": "premium_fund"}, {"$inc": {"balance": -premium_cost_stars}})
            
            msg = f"🏆 **ГЛАВНЫЙ СУПЕР-ПРИЗ!!!** 🏆\n\nНевероятно! Барабан остановился на счастливой звезде!\n🎁 **Приз:** Telegram Premium на 3 месяца!\n\n_🎁 Инструкция отправлена в ЛС!_"
            pm_msg = f"💎 **ВЫ ВЫИГРАЛИ TELEGRAM PREMIUM (3 мес.)!** 💎\n\nПерешлите это сообщение в нашу Службу Поддержки (/start -> Разблокировка), чтобы администратор вручную подарил вам подписку!"
            bot.send_message(STAFF_GROUP_ID, f"🚨 **ВНИМАНИЕ! СОРВАН СУПЕР-ПРИЗ!** 🚨\n\nПользователь `{uid}` (@{message.from_user.username}) выбил **TELEGRAM PREMIUM**! Фонд казино списан.")
        else:
            # Касса пуста! Заменяем на резервный КУШ
            val = 999 

    # 1.5 РЕЗЕРВНЫЙ СУПЕР-ПРИЗ (Если выпал Премиум, но в кассе пусто)
    if val == 999:
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": 500, "cashback_balance": 500}})
        msg = f"🎰 **МИНИ-ДЖЕКПОТ!** 🎰\n\nВы были в миллиметре от Премиума, но срываете отличный куш!\n🎁 **Приз:** +500 Очков Бдительности и 500 Рублей кэшбэка!"
        pm_msg = f"💎 **Ваш выигрыш:** +500 Очков и 500 Руб. Главный приз (Premium) пока копится в фонде казино, попробуйте позже!"

    # 2. 🎰 ЛЕГЕНДАРНЫЙ ДЖЕКПОТ (7️⃣7️⃣7️⃣ — val: 64)
    elif val == 64:
        code = f"JACKPOT-{random.randint(1000, 9999)}"
        db['promocodes'].insert_one({"_id": code, "type": "percent", "value": 100, "target": "vip", "usage_limit": 1, "used_count": 0, "is_active": True})
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": 300}})
        msg = f"🚨 **ДЖЕКПОТ!!! 7️⃣7️⃣7️⃣** 🚨\n\n🎟 **Приз:** Золотой Билет (VIP) + 💰 300 очков!\n_🎁 Промокод отправлен в ЛС!_"
        pm_msg = f"🎉 **ВАШ ДЖЕКПОТ!**\n🎫 VIP-доступ: `{code}`\n💰 Начислено: 300 очков.\n_Если у вас уже есть VIP, вы можете подарить или продать этот код другому участнику!_"
        pm_markup = InlineKeyboardMarkup().add(InlineKeyboardButton("♻️ Сдать билет (+150 Очков)", callback_data=f"trade_promo_{code}_points_150"))

    # 3. 🌟 ЛИЧНЫЙ СТАТУС (Кастомный тег — val: 7, 21, 35)
    elif val in [7, 21, 35]:
        msg = f"🌟 **СУПЕР-РЕДКИЙ ДРОП!** 🌟\n\nВы выиграли право установить **Личный Кастомный Тег** рядом с вашим ником во всех чатах!\n\n_🎁 Заберите приз в ЛС!_"
        pm_msg = f"👑 **Ваш выигрыш из рулетки!**\n\nВы получили купон на создание личного тега. Выберите, как вас будут видеть другие участники!"
        pm_markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✍️ Заказать свой тег", callback_data="claim_custom_tag"))

    # 4. 🔥 ЭПИЧЕСКИЙ (Щит Иммунитета — val: 1, 22, 43)
    elif val in [1, 22, 43]:
        paid_collection.update_one({"uid": uid}, {"$inc": {"immunity": 1}})
        msg = f"🔥 **ЭПИЧЕСКИЙ ВЫИГРЫШ!** 🔥\n\n🛡 **Ваш приз:** Щит Иммунитета.\n_Он поглотит ваш следующий штраф или страйк!_"
        pm_msg = "🛡 **Ваш выигрыш!** Вы получили 1 Щит Иммунитета."
        pm_markup = InlineKeyboardMarkup().add(InlineKeyboardButton("♻️ Разобрать щит (+5 Осколков)", callback_data="trade_shield_shards_5"))

    # 5. 💀 НАЛОГОВАЯ / СКАМ (Сектор Риска — val: 10, 20, 40, 50)
    elif val in [10, 20, 40, 50]:
        lost_points = int(points * 0.3) # Забираем 30% баланса
        if lost_points < 10: lost_points = 10 # Минимальный штраф
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": -lost_points}})
        msg = f"💀 **НАЛОГОВАЯ ПРОВЕРКА!** 💀\n\nВы попали на сектор риска! Налоговая инспекция списывает 30% ваших сбережений.\n_Потеряно: {lost_points} очков._"
        pm_msg = None

    # 6. 🚓 ОРДЕР НА АРЕСТ (Социальный артефакт — val: 5, 17, 29)
    elif val in [5, 17, 29]:
        code = f"ARREST-{random.randint(100, 999)}"
        db['promocodes'].insert_one({"_id": code, "type": "artifact", "value": 0, "target": "mute", "usage_limit": 1, "used_count": 0, "is_active": True})
        msg = f"🚓 **СОЦИАЛЬНЫЙ АРТЕФАКТ!**\n\nВы нашли **Ордер на Арест**! Теперь у вас есть власть над другими.\n_🎁 Инструкция в ЛС!_"
        pm_msg = (
            f"🚓 **Ваш артефакт: Ордер на Арест**\n\n"
            f"Код: `{code}`\n\n"
            f"📜 **Что это дает:**\n"
            f"Вы можете легально отправить ЛЮБОГО пользователя нашей сети чатов в мут (лишить права писать) на 1 час!\n\n"
            f"⚙️ **Как использовать:**\n"
            f"1. Скопируйте ID или @username вашей жертвы в чате.\n"
            f"2. Зайдите в меню `/start` -> `🆘 Разблокировка`.\n"
            f"3. Напишите в чат с поддержкой: *«Применяю Ордер {code} на пользователя @username»*.\n\n"
            f"⚠️ *Ограничения: Запрещено применять на администраторов. Ордер сгорает после одного использования.*"
        )

    # 7. 🕊 АМНИСТИЯ ИЛИ ДОНАТ (val: 13, 26, 39, 52)
    elif val in [13, 26, 39, 52]:
        current_strikes = user_data.get("strikes", 0)
        if current_strikes > 0:
            paid_collection.update_one({"uid": uid}, {"$inc": {"strikes": -1}})
            msg = f"🕊 **АМНИСТИЯ!**\n\nСписан 1 штрафной страйк!\n_Текущие страйки: {current_strikes - 1}/3_"
        else:
            paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": 100}})
            msg = f"🕊 **БЕЛЫЙ БИЛЕТ!**\n\nУ вас нет страйков! Мы дарим вам 💰 **100 очков**!"
        pm_msg = msg

    # 8. 💸 КЭШБЭК / УМНОЖИТЕЛЬ (val: 15, 30, 45, 60)
    elif val in [15, 30, 45, 60]:
        win_points = random.choice([100, 150, 250])
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": win_points}})
        msg = f"💸 **КРУПНЫЙ КУШ!**\n\nВы выиграли **{win_points} очков**!"
        pm_msg = f"💸 Ваш выигрыш: +{win_points} очков."

    # 9. 📢 СКИДКИ (Любое кратное 7, кроме уже занятых 7, 21, 35, 63)
    elif val % 7 == 0:
        promos = [
            {"target": "fine", "value": 50, "prefix": "FINE50", "name": "50% на оплату Штрафа"},
            {"target": "ads", "value": 30, "prefix": "ADS30", "name": "30% на покупку Рекламы"},
            {"target": "vip", "value": 40, "prefix": "VIP40", "name": "40% на покупку VIP"},
            {"target": "all", "value": 15, "prefix": "ALL15", "name": "15% на Любую услугу"}
        ]
        drop = random.choice(promos)
        code = f"{drop['prefix']}-{random.randint(1000, 9999)}"
        db['promocodes'].insert_one({"_id": code, "type": "percent", "value": drop["value"], "target": drop["target"], "usage_limit": 1, "used_count": 0, "is_active": True})
        
        msg = f"✨ **РЕДКИЙ ДРОП!**\n\n🎁 **Ваш приз:** Скидка {drop['name']}!\n_🎁 Промокод отправлен вам в ЛС!_"
        pm_msg = f"✨ **Ваш выигрыш из рулетки!**\n\n🎫 {drop['name']}\nВаш код: `{code}`"
        pm_markup = InlineKeyboardMarkup().add(InlineKeyboardButton("♻️ Обменять на 2 Осколка", callback_data=f"trade_promo_{code}_shards_2"))

    # 9.5 🌟 ЗАМАСКИРОВАННЫЙ КЭШБЭК (Реальные рубли на баланс — val: 11, 33)
    elif val in [11, 33]:
        win_rub = random.choice([100, 250, 500]) # Сумма выигрыша в рублях
        paid_collection.update_one({"uid": uid}, {"$inc": {"cashback_balance": win_rub}})
        
        msg = f"✨ **РЕДКИЙ ДРОП: ДЕНЕЖНЫЙ КУПОН!** ✨\n\nВы выиграли **{win_rub} руб.** на внутренний счет!\n\n_🎁 Баланс обновлен, подробности в ЛС._"
        pm_msg = (
            f"🎁 **Вы выбили денежный купон на {win_rub} руб.!**\n\n"
            f"Средства зачислены на ваш баланс. Вы можете копить их для вывода на карту/телефон или оплачивать ими внутренние штрафы и рекламу!"
        )
        pm_markup = None

    # 10. 🥉 УТЕШИТЕЛЬНЫЙ ДРОП (Все остальные числа — Осколки)
    else:
        shards_won = random.choice([1, 1, 1, 2])
        paid_collection.update_one({"uid": uid}, {"$inc": {"jackpot_shards": shards_won}})
        msg = f"🧩 *Барабан остановился...*\n\nВы нашли: **+{shards_won} Осколок Джекпота**!\n_Соберите 50 штук в кабинете для супер-приза._"

    # Отвечаем в чате, где крутили рулетку
    bot.reply_to(sent_dice, msg, parse_mode="Markdown")
    
    # Отправляем секретные коды и кнопки в ЛС
    if pm_msg:
        try:
            if pm_markup:
                bot.send_message(uid, pm_msg, parse_mode="Markdown", reply_markup=pm_markup)
            else:
                bot.send_message(uid, pm_msg, parse_mode="Markdown")
        except:
            bot.send_message(message.chat.id, f"⚠️ @{message.from_user.username}, я не смог отправить вам приз в ЛС. Напишите мне в личные сообщения /start!", parse_mode="Markdown")

# ================= ОБНОВЛЕННАЯ ТАБЛИЦА ПРИЗОВ =================
@bot.message_handler(commands=['prizes', 'призы', 'куш'])
def show_casino_prizes(message):
    text = (
        "🎰 **ТАБЛИЦА ПРИЗОВ РУЛЕТКИ** 🎰\n\n"
        "Стоимость игры: **50 очков** (`/spin`)\n\n"
        "💎 **СУПЕР-ПРИЗ:** Telegram Premium на 1 месяц!\n"
        "🏆 **ДЖЕКПОТ (7️⃣7️⃣7️⃣):** Золотой Билет (VIP-доступ) + 💰 300 очков!\n\n"
        "🌟 **РЕДКОСТЬ:** Личный Кастомный Тег (Статус) в чатах!\n"
        "🔥 **ЭПИЧЕСКИЙ:** 🛡 Щит Иммунитета *(Авто-защита от бана)*\n"
        "🚓 **АРТЕФАКТ:** Ордер на Арест *(Отправь любого в мут!)*\n\n"
        "🕊 **АМНИСТИЯ:** Снятие 1 штрафа (или +100 очков).\n"
        "💸 **КУШ:** Кэшбэк от 100 до 250 очков!\n"
        "📢 **СКИДКИ:** Промокоды до -50% на услуги.\n\n"
        "💀 **ОПАСНОСТЬ:** Сектор «Налоговая» (Сжигает 30% ваших очков).\n\n"
        "🥉 **ОБЫЧНЫЙ (Неудача):** 🧩 **1-2 Осколка Джекпота** *(50 шт = 100% Супер-Приз)*"
    )
    bot.reply_to(message, text, parse_mode="Markdown")

# ================= ОБРАБОТКА ВСЕХ ПЛАТЕЖЕЙ В 1.py =================
@bot.pre_checkout_query_handler(func=lambda query: True)
def checkout_process(pre_checkout_query):
    bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@bot.message_handler(content_types=['successful_payment'])
def successful_payment(message):
    payload = message.successful_payment.invoice_payload
    amount = message.successful_payment.total_amount
    uid = message.from_user.id

    # 👇 ПОПОЛНЕНИЕ КАССЫ ПРЕМИУМА (Отчисляем 20% от любого платежа в Фонд) 👇
    db['casino_bank'].update_one({"_id": "premium_fund"}, {"$inc": {"balance": amount * 0.20}}, upsert=True)

    # 1. Если это ДОНАТ
    if payload.startswith("donation_"):
        # 👇 НОВАЯ СТРОЧКА 👇
        db['daily_revenue'].insert_one({"type": "donation", "amount": amount, "timestamp": time.time(), "date": datetime.datetime.now().strftime("%d.%m.%Y")})
        
        bot.send_message(uid, f"💖 **Огромное спасибо за ваш донат ({amount}⭐️)!**\nЭти средства очень помогут нашему проекту развиваться.", parse_mode="Markdown")
        bot.send_message(STAFF_GROUP_ID, f"💸 **ДОНАТ!** Пользователь `{uid}` только что отправил чаевые: **{amount}⭐️**! 🎉", parse_mode="Markdown")

    # 2. Если это ШТРАФ (Авторазбан)
    elif payload.startswith("fine_payment_"):
        # 👇 НОВАЯ СТРОЧКА 👇
        db['daily_revenue'].insert_one({"type": "fine", "amount": amount, "timestamp": time.time(), "date": datetime.datetime.now().strftime("%d.%m.%Y")})
        
        now = datetime.datetime.now()
        ticket_num = now.strftime("%d%m%Y%H%M%S") + f"-{random.randint(100, 999)}"

        # 👇 НАША НОВАЯ СТРОЧКА: Записываем деньги в кассу 👇
        db['fine_payments'].insert_one({"uid": uid, "amount": amount, "timestamp": time.time(), "date": now.strftime("%d.%m.%Y")})

        # 🔥 ИСПРАВЛЕНИЕ: Передаем приказ fine_unban и точную сумму!
        db['skynet_tasks'].insert_one({
            "uid": uid, 
            "action": "fine_unban", 
            "amount": amount, 
            "timestamp": now
        })

        # Досье
        archive_collection.update_one(
            {"target": str(uid)},
            {"$push": {"history": {"date": now.strftime("%d.%m.%Y %H:%M"), "action": "Разблокировка (Штраф оплачен)", "reason": "Автоматическое снятие"}}},
            upsert=True
        )

        # Закрываем тикет у админов
        user_data = paid_collection.find_one({"uid": uid})
        if user_data and "thread_id" in user_data:
            thread_id = user_data["thread_id"]
            try: bot.send_message(STAFF_GROUP_ID, f"🤑 **ЮЗЕР ОПЛАТИЛ ШТРАФ ({amount}⭐️)!**\nСкайнет получил приказ на разбан. Тикет закрыт: `{ticket_num}`", message_thread_id=thread_id, parse_mode="Markdown")
            except: pass
            try: bot.close_forum_topic(STAFF_GROUP_ID, thread_id)
            except: pass

        paid_collection.update_one({"uid": uid}, {"$set": {"status": 0}, "$unset": {"topic_type": ""}})

        # Пишем юзеру
        success_msg = f"✅ **Оплата штрафа успешно получена!**\n\nВаши ограничения сняты автоматически. Уникальный номер: `{ticket_num}`\n\n{NETWORK_LINKS}"
        bot.send_message(uid, success_msg, parse_mode="Markdown", disable_web_page_preview=True)

    # 2.5 СМЕШАННАЯ ОПЛАТА ШТРАФА (Звезды + Рубли)
    elif payload.startswith("finepartial_"):
        parts = payload.split('_')
        original_amount = int(parts[1])
        used_rubles = int(parts[2])
        
        # 1. Списываем рубли ТОЛЬКО СЕЙЧАС, когда звезды успешно оплачены!
        paid_collection.update_one({"uid": uid}, {"$inc": {"cashback_balance": -used_rubles}})
        
        # 2. Пополняем кассу Премиума (20% от реально оплаченных звезд)
        db['daily_revenue'].insert_one({"type": "fine_partial", "amount": amount, "timestamp": time.time(), "date": datetime.datetime.now().strftime("%d.%m.%Y")})
        db['fine_payments'].insert_one({"uid": uid, "amount": amount, "timestamp": time.time(), "date": datetime.datetime.now().strftime("%d.%m.%Y")})
        
        now = datetime.datetime.now()
        ticket_num = now.strftime("%d%m%Y%H%M%S") + f"-{random.randint(100, 999)}"
        
        # Передаем оригинальную сумму Скайнету
        db['skynet_tasks'].insert_one({"uid": uid, "action": "fine_unban", "amount": original_amount, "timestamp": now})
        
        # Обновляем досье
        archive_collection.update_one({"target": str(uid)}, {"$push": {"history": {"date": now.strftime("%d.%m.%Y %H:%M"), "action": "Разблокировка (Смешанная оплата)", "reason": "Звезды + Кэшбек"}}}, upsert=True)
        
        # Закрываем тикет админам
        user_data = paid_collection.find_one({"uid": uid})
        if user_data and "thread_id" in user_data:
            try: bot.send_message(STAFF_GROUP_ID, f"🤑 **СМЕШАННАЯ ОПЛАТА!**\nЮзер доплатил {amount}⭐️ и списал {used_rubles}₽. Тикет закрыт: `{ticket_num}`", message_thread_id=user_data["thread_id"], parse_mode="Markdown")
            except: pass
            try: bot.close_forum_topic(STAFF_GROUP_ID, user_data["thread_id"])
            except: pass
            
        paid_collection.update_one({"uid": uid}, {"$set": {"status": 0}, "$unset": {"topic_type": ""}})
        bot.send_message(uid, f"✅ **Оплата успешно получена!**\n\nСписано: {used_rubles}₽ + {amount}⭐️\nВаши ограничения сняты. Номер: `{ticket_num}`\n\n{NETWORK_LINKS}", parse_mode="Markdown", disable_web_page_preview=True)

    # 3. Если это ПОКУПКА ОЧКОВ (МАГАЗИН)
    elif payload.startswith("buy_points_"):
        points_to_add = int(payload.split('_')[2])
        
        # Пишем деньги в кассу (для админской аналитики)
        db['daily_revenue'].insert_one({"type": "points_shop", "amount": amount, "timestamp": time.time(), "date": datetime.datetime.now().strftime("%d.%m.%Y")})
        
        # Формируем пакет начисления
        update_data = {"$inc": {"bounty_points": points_to_add}}
        if points_to_add == 1000:
            update_data["$inc"]["immunity"] = 1 # Выдаем бонусный щит за покупку VIP-пакета!
            
        # Начисляем юзеру в базу
        paid_collection.update_one({"uid": uid}, update_data, upsert=True)
        
        # Радуем юзера
        success_msg = f"🎰 **Оплата успешно получена!**\nВам начислено: **+{points_to_add} очков**."
        if points_to_add == 1000:
            success_msg += "\n🛡 **Бонус:** +1 Щит Иммунитета!"
        success_msg += "\n\n_Крутите рулетку прямо сейчас командой /spin!_"
        
        bot.send_message(uid, success_msg, parse_mode="Markdown")
        bot.send_message(STAFF_GROUP_ID, f"🤑 **МАГАЗИН:** Пользователь `{uid}` купил {points_to_add} очков за {amount}⭐️!", parse_mode="Markdown")

# ================= АУДИТ КАЗИНО =================
@bot.message_handler(commands=['bank'])
def handle_bank_check(message):
    # Команда работает только в админской группе
    if str(message.chat.id) != STAFF_GROUP_ID:
        return
        
    # Считаем фонд Премиума
    bank_data = db['casino_bank'].find_one({"_id": "premium_fund"}) or {"balance": 0}
    current_fund = int(bank_data.get("balance", 0))
    
    # Считаем, сколько всего кэшбека (рублей) на руках у пользователей
    users_with_cb = list(paid_collection.find({"cashback_balance": {"$gt": 0}}))
    total_cb_held = sum(u.get("cashback_balance", 0) for u in users_with_cb)
    
    text = (
        f"🏦 **СВОДКА КАССЫ КАЗИНО**\n\n"
        f"💎 Фонд Premium: **{current_fund} / 1500 ⭐️**\n"
        f"💸 На руках у юзеров (Кэшбек): **{total_cb_held} ₽**\n\n"
        f"_Фонд пополняется на 20% от всех покупок в боте._"
    )
    bot.reply_to(message, text, parse_mode="Markdown")

# ================= СИСТЕМА КАСТОМНЫХ ТЕГОВ =================

# 1. Обработка нажатия на кнопку "Использовать купон на тег"
@bot.callback_query_handler(func=lambda call: call.data == 'claim_custom_tag')
def handle_claim_tag(call):
    bot.answer_callback_query(call.id)
    msg = bot.send_message(
        call.message.chat.id, 
        "✍️ **Создание личного тега**\n\nПридумайте и напишите ваш новый статус (максимум 15 символов).\n_Внимание: Тег будет проверен модератором! Запрещено использовать слова 'Админ', 'Модератор' и нецензурную лексику._"
    )
    bot.register_next_step_handler(msg, process_tag_input)

# 2. Пользователь вводит текст тега
def process_tag_input(message):
    if message.text == '/start':
        send_welcome(message)
        return
        
    tag_text = message.text.strip()
    
    if len(tag_text) > 15:
        bot.send_message(message.chat.id, "❌ **Слишком длинный тег!** Максимум 15 символов. Нажмите на кнопку в сообщении с выигрышем еще раз.")
        return
        
    uid = message.from_user.id
    name = message.from_user.first_name
    
    # Сохраняем тег во временную базу, чтобы не потерять
    db['temp_tags'].update_one(
        {"uid": uid},
        {"$set": {"tag": tag_text, "name": name}},
        upsert=True
    )
    
    # Сообщаем юзеру, что заявка ушла
    bot.send_message(message.chat.id, f"⏳ Ваш тег **«{tag_text}»** отправлен на проверку администраторам. Ожидайте!")
    
    # Отправляем пульт админам
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("✅ Одобрить", callback_data=f"adm_tag_ok_{uid}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"adm_tag_rej_{uid}")
    )
    
    bot.send_message(
        STAFF_GROUP_ID,
        f"👑 **ЗАПРОС НА КАСТОМНЫЙ ТЕГ**\n\n"
        f"👤 От: {name} (`{uid}`)\n"
        f"📝 Желаемый тег: **{tag_text}**\n\n"
        f"Одобрить установку?",
        parse_mode="Markdown",
        reply_markup=markup
    )

# 3. Реакция админа на тег
@bot.callback_query_handler(func=lambda call: call.data.startswith('adm_tag_'))
def handle_admin_tag_decision(call):
    bot.answer_callback_query(call.id)
    if str(call.message.chat.id) != STAFF_GROUP_ID: return
    
    action = call.data.split('_')[2] # ok или rej
    target_uid = int(call.data.split('_')[3])
    
    # Достаем тег из временной базы
    tag_data = db['temp_tags'].find_one({"uid": target_uid})
    if not tag_data:
        bot.edit_message_text("❌ Данные устарели или уже обработаны.", chat_id=call.message.chat.id, message_id=call.message.message_id)
        return
        
    tag_text = tag_data["tag"]
    
    if action == "ok":
        # Записываем тег в основную базу юзера (как при верификации)
        db['users'].update_one(
            {"_id": target_uid}, 
            {"$set": {"custom_tag": tag_text}}, 
            upsert=True
        )
        
        bot.edit_message_text(f"{call.message.text}\n\n✅ **ВЕРДИКТ: ОДОБРЕНО**", chat_id=call.message.chat.id, message_id=call.message.message_id)
        try:
            bot.send_message(target_uid, f"🎉 **Поздравляем!**\nВаш личный тег **«{tag_text}»** успешно одобрен и установлен во всех чатах сети!")
        except: pass
        
    elif action == "rej":
        bot.edit_message_text(f"{call.message.text}\n\n❌ **ВЕРДИКТ: ОТКЛОНЕНО**", chat_id=call.message.chat.id, message_id=call.message.message_id)
        try:
            # Возвращаем юзеру кнопку, чтобы он попробовал другой тег
            markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✍️ Придумать другой тег", callback_data="claim_custom_tag"))
            bot.send_message(target_uid, f"❌ **Ваш тег «{tag_text}» был отклонен модератором.**\nПожалуйста, придумайте что-то другое, не нарушающее правила.", reply_markup=markup)
        except: pass
        
    # Очищаем временную базу
    db['temp_tags'].delete_one({"uid": target_uid})

# ==================== WEBHOOK И СЕРВЕР ====================
from flask import Flask, request

# СОЗДАЕМ СЕРВЕР (Именно из-за отсутствия этой строки была ошибка 500)
app = Flask(__name__)

# Прием сообщений от Телеграма
@app.route('/webhook', methods=['POST'])
def webhook():
    update = telebot.types.Update.de_json(request.stream.read().decode('utf-8'))
    bot.process_new_updates([update])
    return 'ok', 200

# Линия жизни для UptimeRobot
@app.route('/ping')
def ping():
    try:
        db.command('ping') 
        return "I am alive!", 200
    except:
        return "Database error", 500

# === УСТАНОВКА ВЕБХУКА ПРИ ЗАПУСКЕ GUNICORN ===
try:
    bot.remove_webhook()
    bot.set_webhook(url=f"{APP_URL}/webhook")
    print(f"Вебхук успешно установлен на: {APP_URL}/webhook")
except Exception as e:
    print(f"Ошибка установки вебхука: {e}")

if __name__ == '__main__':
    print("Бот Секретарь запущен и готов к работе!")
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))