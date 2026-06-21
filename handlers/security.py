import random
import string
import datetime
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove

from core.bot import bot
from config import STAFF_GROUP_ID, chat_ids_mk, chat_ids_parni, chat_ids_ns, chat_ids_gayznak
from database.mongo import paid_collection, db
from utils.logger import logger

# ================= МЕНЮ СЛУЖБЫ БЕЗОПАСНОСТИ =================
@bot.callback_query_handler(func=lambda call: call.data.startswith('sec_'))
def handle_security_menu(call):
    try: bot.answer_callback_query(call.id)
    except Exception as e: logger.warning(f"Не удалось ответить на колбэк sec_: {e}")
    
    uid = call.from_user.id
    
    if call.data == "sec_back_main":
        try: bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception as e: logger.warning(f"Не удалось удалить сообщение sec_back_main: {e}")
        
        # 🔥 ИСПРАВЛЕНИЕ: Подменяем автора сообщения на реального юзера
        call.message.from_user = call.from_user
        call.message.text = "/start" # Чтобы не сломались проверки внутри send_welcome
        
        from handlers.start_menu import send_welcome
        send_welcome(call.message)
        
    elif call.data == "sec_submit_report":
        msg = bot.send_message(
            call.message.chat.id, 
            "🕵️‍♂️ **Подача жалобы**\n\nПожалуйста, отправьте **@username** (или ID) нарушителя, а также прикрепите **скриншоты** и опишите ситуацию.\n\n_Один репорт — один нарушитель._",
            parse_mode="Markdown"
        )
        # 🚀 FSM: Записываем ожидание жалобы (без next_step_handler)
        db['user_states'].update_one(
            {"uid": uid},
            {"$set": {"state": "waiting_report"}},
            upsert=True
        )
        
    # ================= 🎰 ГЛАВНЫЙ ИГРОВОЙ ХАБ =================
@bot.callback_query_handler(func=lambda call: call.data == 'btn_game_club')
def handle_game_club(call):
    uid = call.from_user.id
    user_data = paid_collection.find_one({"uid": uid}) or {}
    points = user_data.get("bounty_points", 0)
    shards = user_data.get("jackpot_shards", 0)
    cb_balance = user_data.get("cashback_balance", 0)

    markup = InlineKeyboardMarkup(row_width=2)
    # 1 ряд: Рулетка и Призы
    markup.add(
        InlineKeyboardButton("🎰 Рулетка (-50)", callback_data="play_roulette_btn"),
        InlineKeyboardButton("🏆 Призы", callback_data="show_prizes_btn")
    )
    # 2 ряд: Бонусы и Крафт
    markup.add(
        InlineKeyboardButton("📅 Бонус", callback_data="btn_daily_bonus"),
        InlineKeyboardButton("🎒 Инвентарь и Ломбард", callback_data="forge_main")
    )
    # 3 ряд: Покупки и Заработок
    markup.add(
        InlineKeyboardButton("🛒 Магазин скидок", callback_data="shop_rewards_menu"),
        InlineKeyboardButton("🔗 Заработать (CPA)", callback_data="cpa_menu")
    )
    # Вывод денег, если есть
    if cb_balance > 0:
        markup.add(InlineKeyboardButton(f"💸 Вывести средства ({cb_balance}₽)", callback_data="request_cashback_payout"))
        
    markup.add(InlineKeyboardButton("🔙 В главное меню", callback_data="sec_back_main"))

    text = (
        f"🎰 **ИГРОВОЙ КАБИНЕТ СКАЙНЕТА**\n\n"
        f"💰 Очки Бдительности: **{points}**\n"
        f"🧩 Осколки рулетки: **{shards} шт.**\n"
        f"💵 Рублевый счет: **{cb_balance} руб.**\n\n"
        f"Выберите раздел:"
    )
    try: bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
    except: pass

# ================= 🛒 ПАПКА: МАГАЗИН СКИДОК =================
@bot.callback_query_handler(func=lambda call: call.data == 'shop_rewards_menu')
def handle_shop_rewards_menu(call):
    markup = InlineKeyboardMarkup(row_width=2)
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
    markup.add(InlineKeyboardButton("💳 Купить очки за ⭐️", callback_data="shop_points_menu"))
    markup.add(InlineKeyboardButton("🎁 Ввести промокод", callback_data="enter_gift_code"))
    markup.add(InlineKeyboardButton("🔙 Назад", callback_data="btn_game_club"))
    
    try: bot.edit_message_text("🛒 **Магазин Скидок и Услуг**\nОбменивайте заработанные очки на полезные купоны:", call.message.chat.id, call.message.message_id, reply_markup=markup)
    except: pass

# ================= ⚒ ПАПКА: ИНВЕНТАРЬ И ЛОМБАРД (КРАФТ) =================
@bot.callback_query_handler(func=lambda call: call.data == 'forge_main')
def handle_forge_main(call):
    uid = call.from_user.id
    user_data = paid_collection.find_one({"uid": uid}) or {}
    shields = user_data.get("immunity", 0)
    shards = user_data.get("jackpot_shards", 0)
    orders = db['promocodes'].count_documents({"owner_uid": uid, "type": "artifact", "target": "mute", "is_active": True, "used_count": 0})
    
    # 🔥 1. ДОСТАЕМ ЛИЧНЫЕ ПРОМОКОДЫ ЮЗЕРА 🔥
    user_promos = list(db['promocodes'].find({"owner_uid": uid, "is_active": True, "used_count": 0}))
    promo_text = ""
    if user_promos:
        promo_text = "\n\n🎟 **Ваши личные промокоды:**\n"
        for p in user_promos:
            t_name = "Штраф" if p.get('target') == 'fine' else "Рекламу" if p.get('target') == 'ads' else "VIP" if p.get('target') == 'vip' else "Любую услугу"
            val = f"{p.get('value')}%" if p.get('type') == 'percent' else f"{p.get('value')}₽"
            promo_text += f"• `{p['_id']}` — Скидка {val} на {t_name}\n"
    else:
        promo_text = "\n\n🎟 **Ваши личные промокоды:** _У вас нет активных купонов._"

    markup = InlineKeyboardMarkup(row_width=1)
    
    if shards >= 50:
        markup.add(InlineKeyboardButton("🧩 СОБРАТЬ ДЖЕКПОТ (50 осколков)", callback_data="exchange_shards"))
    else:
        markup.add(InlineKeyboardButton(f"🧩 Копите осколки ({shards}/50)", callback_data="dummy_shards"))
        
    # 🔥 2. УМНАЯ КНОПКА КРАФТА 🔥
    u_info = db['users'].find_one({"_id": uid}) or {}
    if not u_info.get("is_queer"):
        markup.add(InlineKeyboardButton("✨ Скрафтить BEYOND-статус", callback_data="forge_craft_beyond"))
    elif not u_info.get("is_vip"):
        markup.add(InlineKeyboardButton("👑 Скрафтить VIP-статус", callback_data="forge_craft_beyond"))
    else:
        markup.add(InlineKeyboardButton("💸 Скрафтить 1000₽ (Кэшбек)", callback_data="forge_craft_beyond"))
        
    markup.add(InlineKeyboardButton("♻️ Сдать промокод (Ломбард)", callback_data="forge_pawn_promo"))
    
    if shields > 0:
        markup.add(InlineKeyboardButton(f"👼 Призвать Ангела (Сжечь 1 щит)", callback_data="inv_use_angel"))
    if orders > 0:
        markup.add(InlineKeyboardButton(f"🚓 Использовать Ордер", callback_data="inv_use_arrest"))
        
    markup.add(InlineKeyboardButton("🔙 Назад", callback_data="btn_game_club"))
    
    text = (
        "⚒ **ИНВЕНТАРЬ И ТЕНЕВОЙ ЛОМБАРД**\n\n"
        f"🛡 Щиты Иммунитета: **{shields} шт.**\n"
        f"🚓 Ордера на арест: **{orders} шт.**"
        f"{promo_text}\n\n"
        "Здесь вы можете использовать свои артефакты, переплавить ненужные промокоды обратно в ресурсы или скрафтить элитный статус."
    )
    try: bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
    except: pass

@bot.callback_query_handler(func=lambda call: call.data == 'forge_craft_beyond')
def handle_craft_beyond(call):
    uid = call.from_user.id
    user_data = paid_collection.find_one({"uid": uid}) or {}
    points = user_data.get("bounty_points", 0)
    shields = user_data.get("immunity", 0)
    
    u_info = db['users'].find_one({"_id": uid}) or {}
    has_beyond = u_info.get("is_queer", False)
    has_vip = u_info.get("is_vip", False)
    
    # ЦЕНА КРАФТА: 3000 очков и 2 щита
    CRAFT_POINTS = 3000
    CRAFT_SHIELDS = 2
    
    if points < CRAFT_POINTS or shields < CRAFT_SHIELDS:
        try: bot.answer_callback_query(call.id, f"❌ Не хватает ресурсов!\nНужно: {CRAFT_POINTS} очков и {CRAFT_SHIELDS} щита.", show_alert=True)
        except: pass
        return
        
    # Списываем ресы в любом случае
    paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": -CRAFT_POINTS, "immunity": -CRAFT_SHIELDS}})
    
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🔙 В инвентарь", callback_data="forge_main"))
    
    # 🔥 3. ЛОГИКА ЭВОЛЮЦИИ КРАФТА 🔥
    if not has_beyond:
        db['users'].update_one({"_id": uid}, {"$set": {"is_queer": True}}, upsert=True)
        msg = "🔮 **КРАФТ УСПЕШЕН!**\n\nЭнергия щитов и очков слилась воедино...\nВы получили элитный статус **BEYOND (QUEER)**! 🏳️‍🌈\nТеперь вам доступны все закрытые функции сети."
    elif not has_vip:
        db['users'].update_one({"_id": uid}, {"$set": {"is_vip": True}}, upsert=True)
        msg = "👑 **КРАФТ УСПЕШЕН!**\n\nСтатус BEYOND у вас уже есть, поэтому кузница выковала вам **ПОЖИЗНЕННЫЙ VIP-СТАТУС**! 💎"
    else:
        paid_collection.update_one({"uid": uid}, {"$inc": {"cashback_balance": 1000}})
        msg = "💸 **КРАФТ УСПЕШЕН!**\n\nТак как у вас уже есть статусы BEYOND и VIP, кузница переплавила ресурсы в чистые деньги!\nВы получили **1000 рублей кэшбэка** на баланс! 💰\n\n_Их можно вывести на карту или потратить на оплату._"
    
    try: bot.edit_message_text(msg, call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=markup)
    except: pass

@bot.callback_query_handler(func=lambda call: call.data == 'forge_pawn_promo')
def handle_pawn_promo(call):
    try: bot.answer_callback_query(call.id)
    except: pass
    msg = bot.send_message(call.message.chat.id, "♻️ **Ломбард Промокодов**\n\nОтправьте мне любой рабочий промокод (например, на VIP или Рекламу), и я переплавлю его в **Осколки Джекпота**:")
    bot.register_next_step_handler(msg, process_pawn_promo)

def process_pawn_promo(message):
    if message.text == '/start':
        from handlers.start_menu import send_welcome
        send_welcome(message)
        return
        
    code = message.text.strip().upper()
    uid = message.from_user.id
    
    promo = db['promocodes'].find_one({"_id": code, "is_active": True})
    if not promo or promo.get("used_count", 0) >= promo.get("usage_limit", 1):
        bot.send_message(message.chat.id, "❌ Промокод не найден, уже использован или сгорел.")
        return
        
    # VIP-код дает 10 осколков, обычный - 5
    shards_reward = 10 if promo.get("target") == "vip" else 5
    
    db['promocodes'].delete_one({"_id": code})
    paid_collection.update_one({"uid": uid}, {"$inc": {"jackpot_shards": shards_reward}}, upsert=True)
    
    bot.send_message(message.chat.id, f"♻️ **Успешная переплавка!**\n\nПромокод `{code}` уничтожен.\nВы получили: **+{shards_reward} Осколков рулетки** 🧩!", parse_mode="Markdown")

# ================= НАГРАДЫ И ПРОМОКОДЫ =================
@bot.callback_query_handler(func=lambda call: call.data.startswith('buy_reward_'))
def handle_reward_purchase(call):
    parts = call.data.split('_')
    reward_type = parts[2]
    price = int(parts[3])
    uid = call.from_user.id
    
    user_data = paid_collection.find_one({"uid": uid}) or {}
    points = user_data.get("bounty_points", 0)
    
    if points < price:
        try: bot.answer_callback_query(call.id, f"❌ Недостаточно очков! Нужно {price}, а у вас {points}.", show_alert=True)
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        return
        
    try: bot.answer_callback_query(call.id, "Покупка...") 
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")
    
    paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": -price}})
    
    code_suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
    promo_code = f"AGENT-{code_suffix}"
    
    target, discount, item_name, instruction = "all", 50, "", ""
    
    if reward_type == "fine25": 
        target, discount = "fine", 25
        item_name = "Скидку 25% на Штраф"
        instruction = "📖 **Как применить:** При обращении в Поддержку за разбаном, нажмите **«🎫 У меня есть промокод»**."
    elif reward_type == "fine50": 
        target, discount = "fine", 50
        item_name = "Скидку 50% на Штраф"
        instruction = "📖 **Как применить:** При обращении в Поддержку за разбаном, нажмите **«🎫 У меня есть промокод»**."
    elif reward_type == "vip50": 
        target, discount = "vip", 50
        item_name = "Скидку 50% на VIP"
        instruction = "📖 **Как применить:** В @Elitepost_bot при выставлении счета нажмите **«🎫 У меня есть промокод»**."
    elif reward_type == "ads50": 
        target, discount = "ads", 50
        item_name = "Скидку 50% на Рекламу"
        instruction = "📖 **Как применить:** В @PostGoldBot_bot в меню выбора тарифов нажмите **«🎫 У меня есть промокод»**."
    elif reward_type == "vip100": 
        target, discount = "vip", 100
        item_name = "Бесплатный VIP-доступ (100%)"
        instruction = "📖 **Как применить:** В @Elitepost_bot при выставлении счета нажмите **«🎫 У меня есть промокод»**."
    
    db['promocodes'].insert_one({
        "_id": promo_code, "type": "percent", "value": discount, "target": target,
        "usage_limit": 1, "used_count": 0, "owner_uid": uid, "is_active": True
    })
    
    try:
        bot.edit_message_text(
            f"🎉 **Покупка успешна!**\n\nВы обменяли {price} очков на **{item_name}**.\n\n"
            f"Ваш промокод:\n`{promo_code}`\n\n{instruction}",
            chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Не удалось отправить промокод юзеру {uid}: {e}")

# ================= ВЫВОД КЭШБЭКА =================
@bot.callback_query_handler(func=lambda call: call.data.startswith('request_cashback_payout'))
def handle_cashback_request(call):
    uid = call.from_user.id
    user_data = paid_collection.find_one({"uid": uid}) or {}
    cb_balance = user_data.get("cashback_balance", 0)
    
    if cb_balance < 500:
        try: bot.answer_callback_query(call.id, f"❌ Минимальная сумма для вывода — 500 рублей! У вас: {cb_balance}₽.", show_alert=True)
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        return
        
    try: bot.answer_callback_query(call.id)
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")
    
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("💳 На банковскую карту (от 3500₽)", callback_data=f"paymeth_card_{cb_balance}"),
        InlineKeyboardButton("📱 На баланс телефона (от 500₽)", callback_data=f"paymeth_phone_{cb_balance}"),
        InlineKeyboardButton("💎 В крипте USDT (от 500₽)", callback_data=f"paymeth_crypto_{cb_balance}"),
        InlineKeyboardButton("🔙 Отмена", callback_data="btn_game_club")
    )
    
    try:
        bot.edit_message_text(
            f"💸 **Оформление выплаты ({cb_balance} руб.)**\n\nВыберите удобный способ получения средств:\n⚠️ _Обратите внимание на минимальные лимиты!_",
            chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup, parse_mode="Markdown"
        )
    except Exception as e: logger.warning(f"Ошибка меню кэшбэка: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('paymeth_'))
def handle_payout_method(call):
    parts = call.data.split('_')
    method = parts[1]
    cb_balance = int(parts[2])
    
    if method == "card" and cb_balance < 3500:
        try: bot.answer_callback_query(call.id, f"❌ Для вывода на карту нужно минимум 3500₽!\nУ вас: {cb_balance}₽.", show_alert=True)
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        return

    try: bot.answer_callback_query(call.id)
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")

    prompts = {
        "card": "💳 **Вывод на карту**\n\nНапишите номер карты и название банка (например: `4276123456789012 Сбербанк`):",
        "phone": "📱 **Вывод на телефон**\n\nНапишите номер телефона и оператора (например: `+79001234567 МТС`):",
        "crypto": "💎 **Вывод в крипте**\n\nНапишите ваш адрес USDT (сеть TRC20):"
    }

    try: bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")
    
    msg = bot.send_message(call.message.chat.id, prompts[method], parse_mode="Markdown")
    bot.register_next_step_handler(msg, process_payout_details, cb_balance=cb_balance, method=method)

def process_payout_details(message, cb_balance, method):
    if message.text == '/start':
        from handlers.start_menu import send_welcome
        send_welcome(message)
        return
        
    uid = message.from_user.id
    details = message.text
    
    user_data = paid_collection.find_one({"uid": uid}) or {}
    current_balance = user_data.get("cashback_balance", 0)
    
    if current_balance < cb_balance:
        bot.send_message(message.chat.id, "❌ Ошибка: ваш баланс изменился. Попробуйте снова.")
        return

    paid_collection.update_one({"uid": uid}, {"$set": {"cashback_balance": current_balance - cb_balance}})
    
    # Защита от подчеркиваний в Markdown
    username = f"@{message.from_user.username}" if message.from_user.username else f"ID {uid}"
    safe_username = username.replace('_', '\\_') 
    
    method_names = {"card": "💳 На карту", "phone": "📱 На телефон", "crypto": "💎 USDT (TRC20)"}
    
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("✅ Выплачено", callback_data=f"payout_done_{uid}_{cb_balance}"),
        InlineKeyboardButton("❌ Отклонить (Вернуть баланс)", callback_data=f"payout_cancel_{uid}_{cb_balance}")
    )
    
    bot.send_message(
        STAFF_GROUP_ID,
        f"💰 **ЗАЯВКА НА ВЫПЛАТУ КЭШБЕКА**\n\n👤 От: {safe_username} (`{uid}`)\n💵 Сумма к выдаче: **{cb_balance} руб.**\n🏦 Способ: **{method_names[method]}**\n📝 Реквизиты юзера:\n`{details}`\n\nСделайте перевод и нажмите кнопку подтверждения:",
        reply_markup=markup, parse_mode="Markdown"
    )
    bot.send_message(message.chat.id, "✅ **Заявка на выплату успешно создана!**\nСумма списана с баланса. Ожидайте поступления средств на указанные реквизиты.")

@bot.callback_query_handler(func=lambda call: call.data.startswith('payout_'))
def handle_payout_decision(call):
    if str(call.message.chat.id) != str(STAFF_GROUP_ID): return
    try: bot.answer_callback_query(call.id)
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")
    
    parts = call.data.split('_')
    action = parts[1]
    target_uid = int(parts[2])
    amount = int(parts[3])
    
    if action == "done":
        try: bot.edit_message_text(f"{call.message.text}\n\n✅ **ВЫПЛАЧЕНО УСПЕШНО**", chat_id=call.message.chat.id, message_id=call.message.message_id)
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        try: bot.send_message(target_uid, f"💸 **Ваша заявка на выплату ({amount} руб.) успешно обработана!** Деньги отправлены.")
        except Exception as e: logger.warning(f"Не удалось уведомить {target_uid} о выплате: {e}")
            
    elif action == "cancel":
        paid_collection.update_one({"uid": target_uid}, {"$inc": {"cashback_balance": amount}})
        try: bot.edit_message_text(f"{call.message.text}\n\n❌ **ОТКЛОНЕНО (Деньги возвращены)**", chat_id=call.message.chat.id, message_id=call.message.message_id)
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        try: bot.send_message(target_uid, f"❌ **Заявка на выплату отклонена.**\nСредства ({amount} руб.) возвращены на ваш внутренний баланс.")
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")

# ================= CPA-СЕТЬ =================
@bot.callback_query_handler(func=lambda call: call.data.startswith('cpa_'))
def handle_cpa_network(call):
    try: bot.answer_callback_query(call.id)
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")
    uid = call.from_user.id
    
    if call.data == "cpa_menu":
        hold_count = db['cpa_traffic'].count_documents({"agent_id": uid, "status": "hold"})
        approved_count = db['cpa_traffic'].count_documents({"agent_id": uid, "status": "approved"})
        fraud_count = db['cpa_traffic'].count_documents({"agent_id": uid, "status": "fraud"})
        
        user_data = paid_collection.find_one({"uid": uid}) or {}
        duplicates = user_data.get("cpa_duplicates", 0)
        
        total_clicks = hold_count + approved_count + fraud_count + duplicates
        
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(
            InlineKeyboardButton("🔗 Сгенерировать ссылку", callback_data="cpa_generate"),
            InlineKeyboardButton("🔙 В кабинет", callback_data="btn_game_club")
        )
        text = (
            f"💼 **Партнерская CPA-Сеть**\n\n"
            f"Приглашайте людей в наши чаты и зарабатывайте Очки Бдительности абсолютно бесплатно!\n\n"
            f"📊 **ВАША ВОРОНКА ТРАФИКА:**\n"
            f"👁 Всего заявок по вашим ссылкам: **{total_clicks}**\n"
            f"🔄 Уже были в сети (не засчитаны): **{duplicates}**\n"
            f"⏳ На проверке Скайнета (48ч): **{hold_count}**\n"
            f"🚫 Забраковано (боты/спамеры): **{fraud_count}**\n"
            f"✅ **Одобрено (оплачено):** **{approved_count}**"
        )
        try: bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")

    elif call.data == "cpa_generate":
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("МК (Мужской Клуб)", callback_data="cpa_net_mk"),
            InlineKeyboardButton("ПАРНИ 18+", callback_data="cpa_net_parni"),
            InlineKeyboardButton("НС (Exotics)", callback_data="cpa_net_ns"),
            InlineKeyboardButton("ГЕЙ ЧАТЫ", callback_data="cpa_net_gayznak")
        )
        markup.add(InlineKeyboardButton("🔙 Назад", callback_data="cpa_menu"))
        try: bot.edit_message_text("📍 Выберите сеть, которую хотите рекламировать:", call.message.chat.id, call.message.message_id, reply_markup=markup)
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")

    elif call.data.startswith("cpa_net_"):
        network = call.data.split("_")[2]
        net_dicts = {"mk": chat_ids_mk, "parni": chat_ids_parni, "ns": chat_ids_ns, "gayznak": chat_ids_gayznak}
        target_dict = net_dicts.get(network, {})
        
        markup = InlineKeyboardMarkup(row_width=2)
        for city, chat_id in list(target_dict.items())[:20]:
            markup.add(InlineKeyboardButton(city, callback_data=f"cpa_getlink_{chat_id}"))
        markup.add(InlineKeyboardButton("🔙 Назад", callback_data="cpa_generate"))
        try: bot.edit_message_text("🏙 Выберите город для создания ссылки:", call.message.chat.id, call.message.message_id, reply_markup=markup)
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")

    elif call.data.startswith("cpa_getlink_"):
        chat_id = int(call.data.split("_")[2])
        try: bot.edit_message_text("⏳ Генерирую персональную ссылку...", call.message.chat.id, call.message.message_id)
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        
        try:
            invite = bot.create_chat_invite_link(chat_id, creates_join_request=True, name=f"cpa_{uid}")
            text = f"✅ **Ваша персональная ссылка готова!**\n\n`{invite.invite_link}`\n\nКопируйте её и размещайте в ВК, комментариях или других чатах. Все пользователи, перешедшие по ней, будут автоматически закреплены за вами!"
            bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Ошибка CPA ссылки для чата {chat_id}: {e}")
            try: bot.edit_message_text(f"❌ Ошибка генерации ссылки. Возможно, бот не является админом в этом чате.\n`{e}`", call.message.chat.id, call.message.message_id)
            except Exception as e: logger.debug(f"Игнор ошибки: {e}")

# ================= ОБМЕННИК И АИРДРОПЫ =================
@bot.callback_query_handler(func=lambda call: call.data == 'exchange_shards')
def handle_shards_exchange(call):
    uid = call.from_user.id
    user_data = paid_collection.find_one({"uid": uid}) or {}
    
    if user_data.get("jackpot_shards", 0) < 50:
        try: bot.answer_callback_query(call.id, "❌ Недостаточно осколков! Нужно 50 шт.", show_alert=True)
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        return
        
    try: bot.answer_callback_query(call.id, "Сборка джекпота...")
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")
    
    # Списываем 50 осколков
    paid_collection.update_one({"uid": uid}, {"$inc": {"jackpot_shards": -50}})
    
    # 🔥 НОВАЯ МЕХАНИКА ШАНСОВ (60% VIP, 30% Щит, 10% TG Premium) 🔥
    chance = random.randint(1, 100)
    
    if chance <= 60:
        # 60% ШАНС: VIP-БИЛЕТ СО СГОРАНИЕМ (72 часа)
        code = f"JACKPOT-{random.randint(1000, 9999)}"
        import datetime
        db['promocodes'].insert_one({
            "_id": code, "type": "percent", "value": 100, "target": "vip",
            "usage_limit": 1, "used_count": 0, "is_active": True,
            "expires_at": datetime.datetime.now() + datetime.timedelta(hours=72) # ⏳ ТАЙМЕР СМЕРТИ
        })
        msg = f"🎉 **ПОЗДРАВЛЯЕМ!** 🎉\n\nВы собрали из осколков **Золотой Билет (VIP-доступ)**!\nВаш промокод: `{code}`\n\n⏳ _Внимание! Код сгорит ровно через 72 часа, успейте использовать или подарить его!_"
        
        try:
            bot.edit_message_text(msg, chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🔙 В кабинет", callback_data="btn_game_club")))
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")

    elif chance <= 90:
        # 30% ШАНС: ЩИТ ИММУНИТЕТА
        paid_collection.update_one({"uid": uid}, {"$inc": {"immunity": 1}})
        msg = f"🛡 **ПОЗДРАВЛЯЕМ!** 🛡\n\nВы собрали из осколков **Щит Иммунитета**!\nВаш аккаунт теперь защищен от одного случайного нарушения или страйка."
        
        try:
            bot.edit_message_text(msg, chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🔙 В кабинет", callback_data="btn_game_club")))
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")

    else:
        # 10% ШАНС: TELEGRAM PREMIUM (СУПЕР-ПРИЗ)
        msg = "💎 **О БОЖЕ, ЭТО ДЖЕКПОТ!!!** 💎\n\nВы выиграли **100% Супер-Приз (Telegram Premium)**! 🎉\n\nНажмите кнопку ниже, чтобы оформить заявку на получение подписки:"
        markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🎁 Забрать Premium", callback_data="claim_premium"))
        
        try:
            bot.edit_message_text(msg, chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=markup)
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('trade_'))
def handle_trade_in(call):
    uid = call.from_user.id
    parts = call.data.split('_')
    
    if parts[1] == 'promo':
        code, reward_type, amount = parts[2], parts[3], int(parts[4])
        
        promo = db['promocodes'].find_one({"_id": code, "is_active": True, "used_count": 0})
        if not promo:
            try: bot.answer_callback_query(call.id, "❌ Этот промокод уже использован или был обменян ранее!", show_alert=True)
            except Exception as e: logger.debug(f"Игнор ошибки: {e}")
            return
            
        db['promocodes'].delete_one({"_id": code})
        
        reward_text = f"{amount} очков бдительности" if reward_type == 'points' else f"{amount} осколков"
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points" if reward_type == 'points' else "jackpot_shards": amount}})
            
        try: bot.edit_message_text(f"♻️ **Приз успешно переработан!**\n\nВы уничтожили промокод `{code}`.\nПолучено: **+{reward_text}**.", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown")
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")

    elif parts[1] == 'shield':
        reward_type, amount = parts[2], int(parts[3])
        user_data = paid_collection.find_one({"uid": uid}) or {}
        
        if user_data.get("immunity", 0) < 1:
            try: bot.answer_callback_query(call.id, "❌ У вас нет активных Щитов для обмена!", show_alert=True)
            except Exception as e: logger.debug(f"Игнор ошибки: {e}")
            return
            
        paid_collection.update_one({"uid": uid}, {"$inc": {"immunity": -1}})
        reward_text = f"{amount} очков бдительности" if reward_type == 'points' else f"{amount} осколков"
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points" if reward_type == 'points' else "jackpot_shards": amount}})
            
        try: bot.edit_message_text(f"♻️ **Щит сдан в утиль!**\n\nВы разобрали 1 Щит Иммунитета.\nПолучено: **+{reward_text}**.", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown")
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")

@bot.callback_query_handler(func=lambda call: call.data == 'enter_gift_code')
def handle_enter_gift_code(call):
    try: bot.answer_callback_query(call.id)
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")
    msg = bot.send_message(call.message.chat.id, "🎁 **Активация подарочного кода**\n\nПришлите промокод ответным сообщением:")
    bot.register_next_step_handler(msg, process_gift_code)

def process_gift_code(message):
    if message.text == '/start':
        from handlers.start_menu import send_welcome
        send_welcome(message)
        return
        
    code_text = message.text.strip().upper()
    uid = message.from_user.id
    
    promo = db['promocodes'].find_one({"_id": code_text, "is_active": True})
    
    if not promo:
        bot.send_message(message.chat.id, "❌ Промокод не найден или уже недействителен.")
        return
        
    if promo.get("used_count", 0) >= promo.get("usage_limit", 1):
        bot.send_message(message.chat.id, "❌ Лимит активаций исчерпан. Вы не успели!")
        return
        
    if promo.get("type") == "airdrop":
        if uid in promo.get("activated_by", []):
            bot.send_message(message.chat.id, "❌ Вы уже активировали этот промокод!")
            return
            
        points = promo.get("value", 0)
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": points}}, upsert=True)
        db['promocodes'].update_one({"_id": code_text}, {"$inc": {"used_count": 1}, "$push": {"activated_by": uid}})
        
        bot.send_message(message.chat.id, f"✅ **Промокод успешно активирован!**\nВам начислено: **+{points} очков** 💰\n_Можете использовать их для игры в рулетку!_")
    else:
        bot.send_message(message.chat.id, "❌ Этот промокод дает скидку, а не бесплатные очки. Введите его при оплате услуг (штраф, реклама).")

# ================= ВОРОНКА ЖАЛОБ (РАБОТАЕТ ЧЕРЕЗ FSM) =================
def process_report_target(message):
    if message.text and message.text == "/start":
        db['user_states'].delete_one({"uid": message.from_user.id})
        from handlers.start_menu import send_welcome
        send_welcome(message)
        return
        
    target_info = f"ID: {message.forward_from.id}" if message.forward_from else (message.text if message.text else "Нет текста")
    db['temp_reports'].update_one({"uid": message.from_user.id}, {"$set": {"target": target_info, "media": []}}, upsert=True)

    # 🚀 FSM: Переводим юзера на 2-й шаг (Описание)
    db['user_states'].update_one({"uid": message.from_user.id}, {"$set": {"state": "waiting_report_desc"}}, upsert=True)
    bot.send_message(message.chat.id, f"✅ Цель зафиксирована: {target_info}\n\n✍️ **Теперь подробно опишите ситуацию.** Что именно произошло?")

def process_report_description(message):
    if message.text == '/start':
        db['user_states'].delete_one({"uid": message.from_user.id})
        from handlers.start_menu import send_welcome
        send_welcome(message)
        return

    description = message.text if message.text else "Описание отсутствует"
    db['temp_reports'].update_one({"uid": message.from_user.id}, {"$set": {"description": description}})

    # 🚀 FSM: Переводим юзера на 3-й шаг (Медиа)
    db['user_states'].update_one({"uid": message.from_user.id}, {"$set": {"state": "waiting_report_media"}}, upsert=True)

    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add("✅ Все доказательства отправлены")

    bot.send_message(
        message.chat.id,
        "📸 **Теперь отправляйте доказательства** (скриншоты, видео и т.д.).\n\n"
        "Вы можете отправить сразу **несколько файлов**. Как только загрузите всё необходимое — нажмите кнопку внизу 👇",
        reply_markup=markup
    )

def process_evidence_loop(message):
    uid = message.from_user.id

    if message.text == '/start':
        db['user_states'].delete_one({"uid": uid})
        bot.send_message(message.chat.id, "Отмена действия...", reply_markup=ReplyKeyboardRemove())
        from handlers.start_menu import send_welcome
        send_welcome(message)
        return

    if message.text == "✅ Все доказательства отправлены":
        # 🚀 УРА! Цикл завершен. Удаляем состояние юзера из FSM.
        db['user_states'].delete_one({"uid": uid})
        bot.send_message(message.chat.id, "⏳ Формируем дело...", reply_markup=ReplyKeyboardRemove())

        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("💊 Наркотики", callback_data=f"rep_drugs_{uid}"),
            InlineKeyboardButton("👶 Несовершеннолетний", callback_data=f"rep_minor_{uid}"),
            InlineKeyboardButton("💰 Мошенник / Спам", callback_data=f"rep_scam_{uid}"),
            InlineKeyboardButton("🤬 Неадекват", callback_data=f"rep_toxic_{uid}")
        )
        bot.send_message(message.chat.id, "❗️ **Финальный шаг: Выберите причину жалобы из списка:**\n_За ложный донос выдается страйк._", reply_markup=markup)
        return

    # Если юзер кидает фото/видео — сохраняем их (Состояние при этом НЕ удаляется!)
    if message.content_type in ['photo', 'video', 'document', 'audio', 'voice', 'video_note']:
        ev_id = ""
        if message.content_type == 'photo': ev_id = message.photo[-1].file_id
        elif message.content_type == 'video': ev_id = message.video.file_id
        elif message.content_type == 'document': ev_id = message.document.file_id
        elif message.content_type == 'voice': ev_id = message.voice.file_id
        elif message.content_type == 'audio': ev_id = message.audio.file_id
        elif message.content_type == 'video_note': ev_id = message.video_note.file_id

        if ev_id:
            db['temp_reports'].update_one({"uid": uid}, {"$push": {"media": {"type": message.content_type, "id": ev_id}}})

@bot.callback_query_handler(func=lambda call: call.data.startswith('rep_'))
def handle_report_submission(call):
    try: bot.answer_callback_query(call.id)
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")
    
    data_parts = call.data.split('_')
    reason_code = data_parts[1]
    reporter_uid = int(data_parts[2])
    
    if call.from_user.id != reporter_uid: return
        
    reasons_dict = {"drugs": "💊 Наркотики", "minor": "👶 Несовершеннолетний", "scam": "💰 Мошенник / Спам", "toxic": "🤬 Неадекват / Оскорбления"}
    reason_text = reasons_dict.get(reason_code, "Другое")
    
    report_data = db['temp_reports'].find_one({"uid": reporter_uid})
    if not report_data:
        try: bot.answer_callback_query(call.id, "❌ Ошибка: данные устарели. Начните заново через /start", show_alert=True)
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        return
        
    target_info = report_data.get("target", "Неизвестно")
    description = report_data.get("description", "Нет описания")
    media_list = report_data.get("media", [])
    
    try: bot.edit_message_text("✅ **Ваша жалоба отправлена в Службу Безопасности.**\nОжидайте проверки!", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown")
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")
    
    reporter_name = call.from_user.first_name or f"ID {reporter_uid}"
    topic = bot.create_forum_topic(chat_id=STAFF_GROUP_ID, name=f"🚨 ЖАЛОБА | {reporter_name}")
    thread_id = topic.message_thread_id
    
    if not media_list:
        bot.send_message(STAFF_GROUP_ID, "⚠️ *Пользователь не прикрепил медиафайлы.*", message_thread_id=thread_id, parse_mode="Markdown")
    else:
        for item in media_list:
            ev_type, ev_id = item["type"], item["id"]
            try:
                if ev_type == 'photo': bot.send_photo(STAFF_GROUP_ID, ev_id, message_thread_id=thread_id)
                elif ev_type == 'video': bot.send_video(STAFF_GROUP_ID, ev_id, message_thread_id=thread_id)
                elif ev_type == 'document': bot.send_document(STAFF_GROUP_ID, ev_id, message_thread_id=thread_id)
                elif ev_type == 'voice': bot.send_voice(STAFF_GROUP_ID, ev_id, message_thread_id=thread_id)
                elif ev_type == 'audio': bot.send_audio(STAFF_GROUP_ID, ev_id, message_thread_id=thread_id)
                elif ev_type == 'video_note': bot.send_video_note(STAFF_GROUP_ID, ev_id, message_thread_id=thread_id)
            except Exception as e: logger.warning(f"Не удалось переслать улику {ev_id}: {e}")
    
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("✅ Подтвердить нарушение (+Награда)", callback_data=f"adm_rep_reward_{reporter_uid}"),
        InlineKeyboardButton("❌ Отклонить (Мало улик)", callback_data=f"adm_rep_reject_{reporter_uid}"),
        InlineKeyboardButton("🚨 Ложный донос (Страйк)", callback_data=f"adm_rep_strike_{reporter_uid}")
    )
    
    bot.send_message(
        STAFF_GROUP_ID,
        f"🚨 **НОВАЯ ЖАЛОБА**\n\n👤 **Заявитель:** {reporter_name} (`{reporter_uid}`)\n🎯 **Обвиняемый:** {target_info}\n⚠️ **Причина:** {reason_text}\n💬 **Описание:** {description}\n\nПроверьте доказательства выше и вынесите вердикт:",
        message_thread_id=thread_id, parse_mode="Markdown", reply_markup=markup
    )
    db['temp_reports'].delete_one({"uid": reporter_uid})

@bot.callback_query_handler(func=lambda call: call.data.startswith('adm_rep_'))
def handle_admin_report_decision(call):
    try: bot.answer_callback_query(call.id)
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")
    if str(call.message.chat.id) != str(STAFF_GROUP_ID): return
    
    action = call.data.split('_')[2]
    reporter_uid = int(call.data.split('_')[3])
    thread_id = call.message.message_thread_id
    
    if action == "reward":
        paid_collection.update_one({"uid": reporter_uid}, {"$inc": {"bounty_points": 10, "successful_reports": 1}}, upsert=True)
        try: bot.send_message(reporter_uid, "🎉 **Ваша жалоба подтвердилась!**\nНарушитель наказан. Вам начислено **+10 очков бдительности**! 💰", parse_mode="Markdown")
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        bot.send_message(STAFF_GROUP_ID, "✅ **Вердикт:** Нарушение подтверждено. Заявитель получил +10 очков.\n\n🔨 **Админы, забаньте нарушителя вручную через Скайнет!**", message_thread_id=thread_id, parse_mode="Markdown")
        try: bot.edit_message_text(f"{call.message.text}\n\n✅ *ЗАКРЫТО: Нарушитель признан виновным.*", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=None)
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        
    elif action == "reject":
        try: bot.send_message(reporter_uid, "❌ Ваша жалоба отклонена. Предоставленных доказательств недостаточно.")
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        try: bot.edit_message_text(f"{call.message.text}\n\n❌ *ЗАКРЫТО: Отклонено (Мало улик).*", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=None)
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        try: bot.close_forum_topic(STAFF_GROUP_ID, thread_id)
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        
    elif action == "strike":
        user_data = paid_collection.find_one({"uid": reporter_uid}) or {"uid": reporter_uid, "strikes": 0, "immunity": 0}
        
        if user_data.get("immunity", 0) > 0:
            paid_collection.update_one({"uid": reporter_uid}, {"$inc": {"immunity": -1, "bounty_points": -10}, "$unset": {"topic_type": ""}})
            try: bot.send_message(reporter_uid, "⛔️ **Ложный донос!**\nВы использовали систему не по назначению. Списано **-10 очков**.\n\nБот попытался выдать вам Штрафной Страйк, но ваш **🛡 Щит Иммунитета поглотил удар!**\n_Щит разрушен._", parse_mode="Markdown")
            except Exception as e: logger.debug(f"Игнор ошибки: {e}")
            try: bot.edit_message_text(f"{call.message.text}\n\n🛡 *ЗАКРЫТО: Юзер спасен Иммунитетом! Страйк поглощен щитом.* ", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=None)
            except Exception as e: logger.debug(f"Игнор ошибки: {e}")
            try: bot.close_forum_topic(STAFF_GROUP_ID, thread_id)
            except Exception as e: logger.debug(f"Игнор ошибки: {e}")
            return

        new_strikes = user_data.get("strikes", 0) + 1
        paid_collection.update_one({"uid": reporter_uid}, {"$set": {"strikes": new_strikes}, "$inc": {"bounty_points": -10}, "$unset": {"topic_type": ""}}, upsert=True)
        try: bot.send_message(reporter_uid, f"🚨 **Внимание! Ложный донос.**\nВы использовали систему не по назначению. Списано **-10 очков**. Выдан страйк ({new_strikes}/3).", parse_mode="Markdown")
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        try: bot.edit_message_text(f"{call.message.text}\n\n🚨 *ЗАКРЫТО: Выдан страйк за ложный донос.*", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=None)
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        try: bot.close_forum_topic(STAFF_GROUP_ID, thread_id)
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")

# ================= ГЛОБАЛЬНЫЙ РОУТЕР СОСТОЯНИЙ (FSM) =================

def check_fsm_state(message):
    """Проверяет, ждет ли бот от юзера какого-то ввода"""
    state = db['user_states'].find_one({"uid": message.from_user.id})
    return bool(state) # Вернет True, если юзер есть в базе состояний

@bot.message_handler(func=check_fsm_state, content_types=['text', 'photo', 'document', 'video', 'voice', 'audio'])
def handle_fsm_states(message):
    uid = message.from_user.id
    user_state = db['user_states'].find_one({"uid": uid})
    state = user_state.get("state")
    
    # ⚠️ УМНОЕ УДАЛЕНИЕ СОСТОЯНИЯ:
    # Мы НЕ удаляем состояние, если юзер в цикле загрузки фото/видео!
    # Он сам выйдет из него, когда нажмет "✅ Все доказательства отправлены"
    if state != "waiting_report_media":
        db['user_states'].delete_one({"uid": uid})
    
    # --- РОУТИНГ СООБЩЕНИЙ ПО ШАГАМ ---
    
    if state == "waiting_report_target":
        process_report_target(message)
        
    elif state == "waiting_report_desc":
        process_report_description(message)
        
    elif state == "waiting_report_media":
        process_evidence_loop(message)
        
    elif state == "waiting_promo":
        from handlers.payments import process_promo_code
        process_promo_code(
            message, 
            user_state.get("target_type"), 
            user_state.get("original_amount"), 
            user_state.get("call_msg_chat_id"), 
            user_state.get("call_msg_id")
        )

# ================= КНОПКИ КАБИНЕТА: БОНУС И ИНВЕНТАРЬ =================
@bot.callback_query_handler(func=lambda call: call.data == 'btn_daily_bonus')
def handle_btn_daily_bonus(call):
    uid = call.from_user.id
    user_data = paid_collection.find_one({"uid": uid}) or {}
    last_bonus = user_data.get("last_bonus_date")
    now = datetime.datetime.now()

    if last_bonus:
        time_diff = (now - last_bonus).total_seconds()
        if time_diff < 86400: 
            hours_left = int((86400 - time_diff) // 3600)
            mins_left = int(((86400 - time_diff) % 3600) // 60)
            try: bot.answer_callback_query(call.id, f"⏳ Рано! Возвращайтесь через {hours_left} ч. {mins_left} мин.", show_alert=True)
            except: pass
            return

    bonus_points = random.randint(15, 50)
    bonus_shards = 1 if random.randint(1, 100) <= 5 else 0 
    
    paid_collection.update_one(
        {"uid": uid}, 
        {"$inc": {"bounty_points": bonus_points, "jackpot_shards": bonus_shards}, "$set": {"last_bonus_date": now}}, 
        upsert=True
    )
    
    msg = f"🎁 **Получен ежедневный бонус!**\nВы нашли **+{bonus_points} очков**!"
    if bonus_shards > 0: msg += "\nИ редкий дроп: **+1 Осколок рулетки!** 🧩"
    
    try: bot.answer_callback_query(call.id, msg, show_alert=True)
    except: pass
    # Перезагружаем кабинет, чтобы обновились цифры
    handle_security_menu(call) 

@bot.callback_query_handler(func=lambda call: call.data == 'btn_my_inventory')
def handle_btn_inventory(call):
    uid = call.from_user.id
    user_data = paid_collection.find_one({"uid": uid}) or {}
    shields = user_data.get("immunity", 0)
    
    # Ищем ордера юзера в базе промокодов
    orders = db['promocodes'].count_documents({"owner_uid": uid, "type": "artifact", "target": "mute", "is_active": True, "used_count": 0})
    
    markup = InlineKeyboardMarkup(row_width=1)
    if shields > 0:
        markup.add(InlineKeyboardButton(f"👼 Использовать Ангела-Хранителя (Сжечь 1 щит)", callback_data="inv_use_angel"))
    if orders > 0:
        markup.add(InlineKeyboardButton(f"🚓 Использовать Ордер на арест", callback_data="inv_use_arrest"))
        
    markup.add(InlineKeyboardButton("🔙 Назад", callback_data="btn_game_club"))
    
    text = f"🎒 **Ваш Инвентарь Артефактов**\n\n🛡 Щиты Иммунитета: **{shields} шт.**\n🚓 Ордера на арест: **{orders} шт.**\n\n_Щиты срабатывают автоматически при нарушениях, но вы также можете потратить их, чтобы разбанить друга!_"
    try: bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
    except: pass

@bot.callback_query_handler(func=lambda call: call.data == 'inv_use_angel')
def handle_inv_angel(call):
    try: bot.answer_callback_query(call.id)
    except: pass
    msg = bot.send_message(call.message.chat.id, "👼 **Ангел-Хранитель**\n\nНапишите ID пользователя (друга), с которого нужно снять все блокировки и страйки за счет вашего Щита:")
    bot.register_next_step_handler(msg, process_angel_id)

def process_angel_id(message):
    uid = message.from_user.id
    if not message.text.isdigit():
        bot.send_message(message.chat.id, "❌ Ошибка: ID должен состоять только из цифр.")
        return
        
    target_uid = int(message.text)
    user_data = paid_collection.find_one({"uid": uid}) or {}
    
    if user_data.get("immunity", 0) < 1:
        bot.send_message(message.chat.id, "❌ У вас нет активных Щитов Иммунитета!")
        return
        
    paid_collection.update_one({"uid": uid}, {"$inc": {"immunity": -1}})
    paid_collection.update_one({"uid": target_uid}, {"$set": {"strikes": 0, "status": 0}, "$unset": {"topic_type": ""}})
    db['skynet_tasks'].insert_one({"uid": target_uid, "action": "full_unban", "timestamp": datetime.datetime.now()})
    
    bot.send_message(message.chat.id, f"👼 **Магия сработала!** Вы спасли пользователя `{target_uid}`. Приказ передан Скайнету!", parse_mode="Markdown")
    try: bot.send_message(target_uid, "👼 **ЧУДО!** Кто-то из друзей пожертвовал своим Щитом, чтобы спасти вас! Все блокировки сняты.")
    except: pass

# ================= ⚒ ТЕНЕВОЙ ЛОМБАРД И КУЗНИЦА =================
@bot.callback_query_handler(func=lambda call: call.data == 'forge_main')
def handle_forge_main(call):
    uid = call.from_user.id
    user_data = paid_collection.find_one({"uid": uid}) or {}
    points = user_data.get("bounty_points", 0)
    shields = user_data.get("immunity", 0)
    
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("✨ Скрафтить BEYOND-статус", callback_data="forge_craft_beyond"),
        InlineKeyboardButton("♻️ Сдать промокод (Ломбард)", callback_data="forge_pawn_promo"),
        InlineKeyboardButton("🔙 В кабинет", callback_data="btn_game_club")
    )
    
    text = (
        "⚒ **ТЕНЕВОЙ ЛОМБАРД СКАЙНЕТА**\n\n"
        "Здесь вы можете переплавить ненужные вещи в полезные ресурсы или создать элитный статус.\n\n"
        f"📦 **Ваши ресурсы:**\n"
        f"💰 Очки: **{points}**\n"
        f"🛡 Щиты: **{shields}**\n\n"
        "Выберите действие:"
    )
    try: bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
    except: pass
    
@bot.callback_query_handler(func=lambda call: call.data == 'forge_craft_beyond')
def handle_craft_beyond(call):
    uid = call.from_user.id
    user_data = paid_collection.find_one({"uid": uid}) or {}
    points = user_data.get("bounty_points", 0)
    shields = user_data.get("immunity", 0)
    
    # Проверяем, есть ли уже статус
    u_info = db['users'].find_one({"_id": uid}) or {}
    if u_info.get("is_queer"):
        try: bot.answer_callback_query(call.id, "❌ У вас уже есть максимальный статус BEYOND!", show_alert=True)
        except: pass
        return
        
    # ЦЕНА КРАФТА (Можно поменять под твою экономику)
    CRAFT_POINTS = 3000
    CRAFT_SHIELDS = 2
    
    if points < CRAFT_POINTS or shields < CRAFT_SHIELDS:
        try: bot.answer_callback_query(call.id, f"❌ Не хватает ресурсов!\nНужно: {CRAFT_POINTS} очков и {CRAFT_SHIELDS} щита.", show_alert=True)
        except: pass
        return
        
    # Списываем ресы
    paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": -CRAFT_POINTS, "immunity": -CRAFT_SHIELDS}})
    # Выдаем статус
    db['users'].update_one({"_id": uid}, {"$set": {"is_queer": True}}, upsert=True)
    
    try: bot.edit_message_text("🔮 **КРАФТ УСПЕШЕН!**\n\nЭнергия щитов и очков слилась воедино...\nВы получили элитный статус **BEYOND (QUEER)**! 🏳️‍🌈\nТеперь вам доступны все закрытые функции сети.", call.message.chat.id, call.message.message_id, parse_mode="Markdown")
    except: pass

@bot.callback_query_handler(func=lambda call: call.data == 'forge_pawn_promo')
def handle_pawn_promo(call):
    try: bot.answer_callback_query(call.id)
    except: pass
    msg = bot.send_message(call.message.chat.id, "♻️ **Ломбард Промокодов**\n\nОтправьте мне любой рабочий промокод (например, на VIP или Рекламу), и я переплавлю его в **Осколки Джекпота**:")
    bot.register_next_step_handler(msg, process_pawn_promo)

def process_pawn_promo(message):
    if message.text == '/start':
        from handlers.start_menu import send_welcome
        send_welcome(message)
        return
        
    code = message.text.strip().upper()
    uid = message.from_user.id
    
    promo = db['promocodes'].find_one({"_id": code, "is_active": True})
    if not promo or promo.get("used_count", 0) >= promo.get("usage_limit", 1):
        bot.send_message(message.chat.id, "❌ Промокод не найден, уже использован или сгорел.")
        return
        
    # Уничтожаем код и даем осколки (VIP-код дает 10 осколков, обычный - 5)
    shards_reward = 10 if promo.get("target") == "vip" else 5
    
    db['promocodes'].delete_one({"_id": code})
    paid_collection.update_one({"uid": uid}, {"$inc": {"jackpot_shards": shards_reward}}, upsert=True)
    
    bot.send_message(message.chat.id, f"♻️ **Успешная переплавка!**\n\nПромокод `{code}` уничтожен.\nВы получили: **+{shards_reward} Осколков рулетки** 🧩!", parse_mode="Markdown")