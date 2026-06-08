import time
import datetime
import random
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice

from core.bot import bot
from config import STAFF_GROUP_ID
from database.mongo import paid_collection, archive_collection, db
from utils.logger import logger
from utils.templates import NETWORK_LINKS

# ================= ЧЕК-АУТ И ГЕНЕРАЦИЯ СЧЕТОВ =================
@bot.callback_query_handler(func=lambda call: call.data.startswith('checkout_'))
def handle_checkout(call):
    try: bot.answer_callback_query(call.id)
    except Exception as e: logger.debug(f"Игнор ошибки (payments): {e}")
        
    parts = call.data.split('_')
    action = parts[1] # "promo", "pay", "partial", "balance"
    target_type = parts[2] # "fine", "ads", "vip"
    original_amount = int(parts[3])
    
    # 1. Если юзер просто хочет оплатить
    if action == "pay":
        try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception as e: logger.debug(f"Игнор ошибки (payments): {e}")
        try:
            bot.send_invoice(
                call.message.chat.id, 
                title=f"Оплата ({original_amount}⭐️)", 
                description="Оплата услуг бота. После оплаты ограничения будут сняты.", 
                invoice_payload=f"{target_type}_payment_{original_amount}", 
                provider_token="", currency="XTR", 
                prices=[LabeledPrice(label="К оплате", amount=original_amount)]
            )
        except Exception as e: logger.error(f"Ошибка инвойса (pay): {e}")
        
    # 2. Если юзер нажал "Ввести промокод"
    elif action == "promo":
        try:
            bot.send_message(call.message.chat.id, "👇 **Введите ваш промокод ответом на это сообщение:**", parse_mode="Markdown")
            # 🚀 FSM: Записываем состояние в базу (без next_step_handler)
            db['user_states'].update_one(
                {"uid": call.from_user.id},
                {"$set": {
                    "state": "waiting_promo",
                    "target_type": target_type,
                    "original_amount": original_amount,
                    "call_msg_chat_id": call.message.chat.id,
                    "call_msg_id": call.message.message_id
                }},
                upsert=True
            )
        except Exception as e: logger.warning(f"Ошибка запроса промокода: {e}")

    # 3. Смешанная оплата (Часть рублями, остаток Звездами)
    elif action == "partial":
        used_rubles = int(parts[4])
        user_data = paid_collection.find_one({"uid": call.from_user.id}) or {}
        
        if user_data.get("cashback_balance", 0) < used_rubles:
            try: bot.answer_callback_query(call.id, "❌ Ошибка: ваш рублевый баланс изменился!", show_alert=True)
            except Exception as e: logger.debug(f"Игнор ошибки (payments): {e}")
            return
            
        remaining_stars = original_amount - (used_rubles // 2)
        if remaining_stars < 1: remaining_stars = 1
        
        try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception as e: logger.debug(f"Игнор ошибки (payments): {e}")
        try:
            bot.send_invoice(
                call.message.chat.id, title="Оплата штрафа (Смешанная)", 
                description=f"Штраф: {original_amount}⭐️\nСписано: {used_rubles}₽\nК доплате: {remaining_stars}⭐️", 
                invoice_payload=f"finepartial_{original_amount}_{used_rubles}", 
                provider_token="", currency="XTR", 
                prices=[LabeledPrice(label="К оплате", amount=remaining_stars)]
            )
        except Exception as e: logger.error(f"Ошибка инвойса (partial): {e}")

    # 4. Оплата полностью с внутреннего баланса
    elif action == "balance":
        cost_rub = original_amount * 2
        user_data = paid_collection.find_one({"uid": call.from_user.id}) or {}
        current_balance = user_data.get("cashback_balance", 0)
        
        if current_balance < cost_rub:
            try: bot.answer_callback_query(call.id, f"❌ Недостаточно средств! Нужно {cost_rub}₽, а у вас {current_balance}₽.", show_alert=True)
            except Exception as e: logger.debug(f"Игнор ошибки (payments): {e}")
            return
            
        paid_collection.update_one({"uid": call.from_user.id}, {"$inc": {"cashback_balance": -cost_rub}})
        try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception as e: logger.debug(f"Игнор ошибки (payments): {e}")
        
        now = datetime.datetime.now()
        ticket_num = now.strftime("%d%m%Y%H%M%S") + f"-{random.randint(100, 999)}"
        db['skynet_tasks'].insert_one({"uid": call.from_user.id, "action": "fine_unban", "amount": original_amount, "timestamp": now})
        archive_collection.update_one({"target": str(call.from_user.id)}, {"$push": {"history": {"date": now.strftime("%d.%m.%Y %H:%M"), "action": "Разблокировка (Внутренний баланс)", "reason": "Оплата кэшбеком"}}}, upsert=True)
        
        user_data_full = paid_collection.find_one({"uid": call.from_user.id})
        if user_data_full and "thread_id" in user_data_full:
            try: bot.send_message(STAFF_GROUP_ID, f"🟢 **ЮЗЕР ОПЛАТИЛ ШТРАФ С БАЛАНСА ({cost_rub}₽)!**\nСкайнет получил приказ на разбан. Тикет закрыт: `{ticket_num}`", message_thread_id=user_data_full["thread_id"], parse_mode="Markdown")
            except Exception as e: logger.debug(f"Игнор ошибки (payments): {e}")
            try: bot.close_forum_topic(STAFF_GROUP_ID, user_data_full["thread_id"])
            except Exception as e: logger.debug(f"Игнор ошибки (payments): {e}")
            
        paid_collection.update_one({"uid": call.from_user.id}, {"$set": {"status": 0}, "$unset": {"topic_type": ""}})
        try: bot.send_message(call.message.chat.id, f"✅ **Оплата с баланса прошла успешно!**\n\nВаши ограничения сняты. Уникальный номер: `{ticket_num}`\n\n{NETWORK_LINKS}", parse_mode="Markdown", disable_web_page_preview=True)
        except Exception as e: logger.error(f"Не удалось отправить юзеру сообщение о разбане за баланс: {e}")

# 🔥 ИСПРАВЛЕНО: Функция теперь принимает ID чата и ID сообщения
def process_promo_code(message, target_type, original_amount, call_msg_chat_id, call_msg_id):
    if message.text == '/start':
        from handlers.start_menu import send_welcome
        send_welcome(message)
        return

    # Удаляем часики со старого сообщения, используя сохраненные ID
    try: bot.edit_message_reply_markup(call_msg_chat_id, call_msg_id, reply_markup=None)
    except Exception as e: logger.debug(f"Игнор ошибки (payments): {e}")
    
    promo_text = message.text.strip().upper()
    promo_data = db['promocodes'].find_one({"_id": promo_text})
    
    def send_full_invoice(error_msg):
        try:
            bot.send_message(message.chat.id, error_msg)
            bot.send_invoice(message.chat.id, title=f"Оплата ({original_amount}⭐️)", description="Оплата услуг.", invoice_payload=f"{target_type}_payment_{original_amount}", provider_token="", currency="XTR", prices=[LabeledPrice(label="К оплате", amount=original_amount)])
        except Exception as e: logger.warning(f"Ошибка выставления инвойса: {e}")

    if not promo_data or not promo_data.get("is_active"):
        send_full_invoice("❌ Промокод не найден или уже недействителен. Выставляем полный счет.")
        return
        
    if promo_data["used_count"] >= promo_data["usage_limit"]:
        send_full_invoice("❌ Лимит активаций этого промокода исчерпан. Выставляем полный счет.")
        return
        
    if promo_data["target"] != "all" and promo_data["target"] != target_type:
        send_full_invoice("❌ Этот промокод нельзя применить к данной услуге. Выставляем полный счет.")
        return

    discount = promo_data["value"]
    new_amount = original_amount
    
    if promo_data["type"] == "percent": new_amount = int(original_amount * (1 - discount / 100))
    elif promo_data["type"] == "fixed": new_amount = original_amount - discount
        
    if new_amount < 1: new_amount = 1 
        
    db['promocodes'].update_one({"_id": promo_text}, {"$inc": {"used_count": 1}})
    
    try:
        bot.send_message(message.chat.id, f"✅ **Промокод успешно применен!**\nСкидка составила {original_amount - new_amount}⭐️. Счет пересчитан.", parse_mode="Markdown")
        bot.send_invoice(
            message.chat.id, title=f"Оплата со скидкой ({new_amount}⭐️)", 
            description="Оплата услуг бота с учетом промокода.", 
            invoice_payload=f"{target_type}_payment_{new_amount}", 
            provider_token="", currency="XTR", 
            prices=[LabeledPrice(label="К оплате", amount=new_amount)]
        )
    except Exception as e: logger.error(f"Ошибка выставления инвойса СО СКИДКОЙ: {e}")

# ================= ПРИЕМ ПЛАТЕЖЕЙ TELEGRAM STARS =================
@bot.pre_checkout_query_handler(func=lambda query: True)
def checkout_process(pre_checkout_query):
    try:
        bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)
    except Exception as e:
        logger.warning(f"Ошибка answer_pre_checkout_query: {e}")

@bot.message_handler(content_types=['successful_payment'])
def successful_payment(message):
    payload = message.successful_payment.invoice_payload
    amount = message.successful_payment.total_amount
    uid = message.from_user.id

    # ПОПОЛНЕНИЕ КАССЫ ПРЕМИУМА (Отчисляем 20% от любого платежа в Фонд)
    db['casino_bank'].update_one({"_id": "premium_fund"}, {"$inc": {"balance": amount * 0.20}}, upsert=True)

    # 1. ДОНАТ
    if payload.startswith("donation_"):
        db['daily_revenue'].insert_one({"type": "donation", "amount": amount, "timestamp": time.time(), "date": datetime.datetime.now().strftime("%d.%m.%Y")})
        try:
            bot.send_message(uid, f"💖 **Огромное спасибо за ваш донат ({amount}⭐️)!**\nЭти средства очень помогут нашему проекту развиваться.", parse_mode="Markdown")
            bot.send_message(STAFF_GROUP_ID, f"💸 **ДОНАТ!** Пользователь `{uid}` только что отправил чаевые: **{amount}⭐️**! 🎉", parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Ошибка отправки благодарности за донат: {e}")

    # 2. ШТРАФ (Авторазбан)
    elif payload.startswith("fine_payment_"):
        db['daily_revenue'].insert_one({"type": "fine", "amount": amount, "timestamp": time.time(), "date": datetime.datetime.now().strftime("%d.%m.%Y")})
        
        now = datetime.datetime.now()
        ticket_num = now.strftime("%d%m%Y%H%M%S") + f"-{random.randint(100, 999)}"

        db['fine_payments'].insert_one({"uid": uid, "amount": amount, "timestamp": time.time(), "date": now.strftime("%d.%m.%Y")})

        # Приказ Скайнету
        db['skynet_tasks'].insert_one({
            "uid": uid, 
            "action": "fine_unban", 
            "amount": amount, 
            "timestamp": now
        })

        archive_collection.update_one(
            {"target": str(uid)},
            {"$push": {"history": {"date": now.strftime("%d.%m.%Y %H:%M"), "action": "Разблокировка (Штраф оплачен)", "reason": "Автоматическое снятие"}}},
            upsert=True
        )

        user_data = paid_collection.find_one({"uid": uid})
        if user_data and "thread_id" in user_data:
            thread_id = user_data["thread_id"]
            try: bot.send_message(STAFF_GROUP_ID, f"🤑 **ЮЗЕР ОПЛАТИЛ ШТРАФ ({amount}⭐️)!**\nСкайнет получил приказ на разбан. Тикет закрыт: `{ticket_num}`", message_thread_id=thread_id, parse_mode="Markdown")
            except Exception as e: logger.debug(f"Игнор ошибки (payments): {e}")
            try: bot.close_forum_topic(STAFF_GROUP_ID, thread_id)
            except Exception as e: logger.debug(f"Игнор ошибки (payments): {e}")

        paid_collection.update_one({"uid": uid}, {"$set": {"status": 0}, "$unset": {"topic_type": ""}})

        try:
            bot.send_message(uid, f"✅ **Оплата штрафа успешно получена!**\n\nВаши ограничения сняты автоматически. Уникальный номер: `{ticket_num}`\n\n{NETWORK_LINKS}", parse_mode="Markdown", disable_web_page_preview=True)
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление о разбане за штраф: {e}")

    # 2.5 СМЕШАННАЯ ОПЛАТА ШТРАФА
    elif payload.startswith("finepartial_"):
        parts = payload.split('_')
        original_amount = int(parts[1])
        used_rubles = int(parts[2])
        
        paid_collection.update_one({"uid": uid}, {"$inc": {"cashback_balance": -used_rubles}})
        
        db['daily_revenue'].insert_one({"type": "fine_partial", "amount": amount, "timestamp": time.time(), "date": datetime.datetime.now().strftime("%d.%m.%Y")})
        db['fine_payments'].insert_one({"uid": uid, "amount": amount, "timestamp": time.time(), "date": datetime.datetime.now().strftime("%d.%m.%Y")})
        
        now = datetime.datetime.now()
        ticket_num = now.strftime("%d%m%Y%H%M%S") + f"-{random.randint(100, 999)}"
        
        db['skynet_tasks'].insert_one({"uid": uid, "action": "fine_unban", "amount": original_amount, "timestamp": now})
        archive_collection.update_one({"target": str(uid)}, {"$push": {"history": {"date": now.strftime("%d.%m.%Y %H:%M"), "action": "Разблокировка (Смешанная оплата)", "reason": "Звезды + Кэшбек"}}}, upsert=True)
        
        user_data = paid_collection.find_one({"uid": uid})
        if user_data and "thread_id" in user_data:
            try: bot.send_message(STAFF_GROUP_ID, f"🤑 **СМЕШАННАЯ ОПЛАТА!**\nЮзер доплатил {amount}⭐️ и списал {used_rubles}₽. Тикет закрыт: `{ticket_num}`", message_thread_id=user_data["thread_id"], parse_mode="Markdown")
            except Exception as e: logger.debug(f"Игнор ошибки (payments): {e}")
            try: bot.close_forum_topic(STAFF_GROUP_ID, user_data["thread_id"])
            except Exception as e: logger.debug(f"Игнор ошибки (payments): {e}")
            
        paid_collection.update_one({"uid": uid}, {"$set": {"status": 0}, "$unset": {"topic_type": ""}})
        try:
            bot.send_message(uid, f"✅ **Оплата успешно получена!**\n\nСписано: {used_rubles}₽ + {amount}⭐️\nВаши ограничения сняты. Номер: `{ticket_num}`\n\n{NETWORK_LINKS}", parse_mode="Markdown", disable_web_page_preview=True)
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление (Смешанная оплата): {e}")

    # 3. ПОКУПКА ОЧКОВ (МАГАЗИН)
    elif payload.startswith("buy_points_"):
        points_to_add = int(payload.split('_')[2])
        
        db['daily_revenue'].insert_one({"type": "points_shop", "amount": amount, "timestamp": time.time(), "date": datetime.datetime.now().strftime("%d.%m.%Y")})
        
        update_data = {"$inc": {"bounty_points": points_to_add}}
        if points_to_add == 1000:
            update_data["$inc"]["immunity"] = 1 # Бонусный щит
            
        paid_collection.update_one({"uid": uid}, update_data, upsert=True)
        
        success_msg = f"🎰 **Оплата успешно получена!**\nВам начислено: **+{points_to_add} очков**."
        if points_to_add == 1000:
            success_msg += "\n🛡 **Бонус:** +1 Щит Иммунитета!"
        success_msg += "\n\n_Крутите рулетку прямо сейчас командой /spin!_"
        
        try:
            bot.send_message(uid, success_msg, parse_mode="Markdown")
            bot.send_message(STAFF_GROUP_ID, f"🤑 **МАГАЗИН:** Пользователь `{uid}` купил {points_to_add} очков за {amount}⭐️!", parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Ошибка уведомления о покупке очков: {e}")

# ================= АУДИТ КАЗИНО =================
@bot.message_handler(commands=['bank'])
def handle_bank_check(message):
    if str(message.chat.id) != str(STAFF_GROUP_ID):
        return
        
    bank_data = db['casino_bank'].find_one({"_id": "premium_fund"}) or {"balance": 0}
    current_fund = int(bank_data.get("balance", 0))
    
    users_with_cb = list(paid_collection.find({"cashback_balance": {"$gt": 0}}))
    total_cb_held = sum(u.get("cashback_balance", 0) for u in users_with_cb)
    
    text = (
        f"🏦 **СВОДКА КАССЫ КАЗИНО**\n\n"
        f"💎 Фонд Premium: **{current_fund} / 1500 ⭐️**\n"
        f"💸 На руках у юзеров (Кэшбек): **{total_cb_held} ₽**\n\n"
        f"_Фонд пополняется на 20% от всех покупок в боте._"
    )
    try:
        bot.reply_to(message, text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Не удалось отправить аудит банка: {e}")

# ================= МАГАЗИН ОЧКОВ (НОВЫЕ КНОПКИ) =================
@bot.callback_query_handler(func=lambda call: call.data.startswith('shop_points_buy_'))
def handle_shop_buy(call):
    try: bot.answer_callback_query(call.id)
    except Exception as e: logger.debug(f"Игнор ошибки (payments): {e}")
    parts = call.data.split('_')
    points = int(parts[3])
    price = int(parts[4])
    try:
        bot.send_invoice(
            call.message.chat.id,
            title=f"Покупка {points} очков",
            description=f"Пакет на {points} очков бдительности.",
            invoice_payload=f"buy_points_{points}",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label="К оплате", amount=price)]
        )
    except Exception as e:
        logger.error(f"Ошибка магазина: {e}")

@bot.callback_query_handler(func=lambda call: call.data == 'shop_points_menu')
def handle_shop_menu(call):
    try: bot.answer_callback_query(call.id)
    except Exception as e: logger.debug(f"Игнор ошибки (payments): {e}")
    
    # Имитируем команду /start shop, чтобы открыть витрину
    call.message.from_user = call.from_user
    call.message.text = "/start shop"
    
    from handlers.start_menu import send_welcome
    send_welcome(call.message)

# ================= ДОНАТЫ (ЧАЕВЫЕ) =================
@bot.callback_query_handler(func=lambda call: call.data == 'start_donate')
def handle_start_donate(call):
    # Снимаем залипание главной кнопки
    try: bot.answer_callback_query(call.id)
    except Exception as e: logger.debug(f"Игнор ошибки (payments): {e}")
    
    markup = InlineKeyboardMarkup(row_width=3)
    markup.add(
        InlineKeyboardButton("50 ⭐️", callback_data="send_don_50"),
        InlineKeyboardButton("150 ⭐️", callback_data="send_don_150"),
        InlineKeyboardButton("500 ⭐️", callback_data="send_don_500")
    )
    markup.add(InlineKeyboardButton("✍️ Указать свою сумму", callback_data="send_don_custom"))
    
    try: 
        bot.send_message(
            call.message.chat.id, 
            "💖 **Поддержка команды**\n\nСпасибо, что высоко оценили нашу работу! Выберите готовую сумму чаевых или укажите свою личную:", 
            reply_markup=markup
        )
    except Exception as e: 
        logger.warning(f"Ошибка отправки меню доната: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('send_don_'))
def handle_send_donation_invoice(call):
    # Снимаем залипание кнопок выбора
    try: bot.answer_callback_query(call.id)
    except Exception as e: logger.debug(f"Игнор ошибки (payments): {e}")
    
    action = call.data.split('_')[2]
    
    # Если пользователь хочет ввести свою сумму
    if action == "custom":
        msg = bot.send_message(
            call.message.chat.id, 
            "✍️ **Введите количество звёзд (цифрами), которое вы хотите отправить в качестве чаевых:**\n_Например: 100_"
        )
        bot.register_next_step_handler(msg, process_custom_donation)
        return

    # Если выбрана готовая кнопка
    amount = int(action)
    send_donation_invoice_helper(call.message.chat.id, amount)

def process_custom_donation(message):
    if message.text == '/start':
        from handlers.start_menu import send_welcome
        send_welcome(message)
        return
        
    if not message.text or not message.text.isdigit():
        bot.send_message(message.chat.id, "❌ **Ошибка:** Нужно ввести только число цифрами (например: 200). Попробуйте снова, нажав кнопку доната.")
        return
        
    amount = int(message.text)
    if amount < 1 or amount > 50000:
        bot.send_message(message.chat.id, "❌ **Ошибка:** Сумма чаевых должна быть от 1 до 50 000 звёзд.")
        return
        
    send_donation_invoice_helper(message.chat.id, amount)

def send_donation_invoice_helper(chat_id, amount):
    """Вспомогательная функция для генерации инвойса доната"""
    try:
        bot.send_invoice(
            chat_id,
            title=f"Чаевые ({amount}⭐️)",
            description="Поддержка команды модераторов Скайнета. Спасибо за вашу бдительность!",
            invoice_payload=f"donation_{amount}", # Payload динамически подставит любую сумму
            provider_token="", 
            currency="XTR",
            prices=[LabeledPrice(label="Донат", amount=amount)]
        )
    except Exception as e:
        logger.error(f"Ошибка выставления инвойса на донат: {e}")