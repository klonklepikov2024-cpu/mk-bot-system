import math
import datetime
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice

from core.bot import bot
from utils.validators import is_user_locked
from config import STAFF_GROUP_ID
from database.mongo import paid_collection, db
from utils.logger import logger
from utils.cryptobot import get_crypto_pay_url

# ================= СИСТЕМНЫЕ ФУНКЦИИ РЫНКА =================

def get_base_price_rub(target_type):
    """Умное получение базовой цены из настроек Веб-панели"""
    prices_db = db['settings'].find_one({"_id": "prices"}) or {}
    if target_type == "vip": return prices_db.get("vip_price_stars", 250) * 2
    if target_type == "ads": return prices_db.get("ads_price_stars", 150) * 2
    if target_type == "fine": return prices_db.get("fine_price_stars", 650) * 2
    return 500 # Дефолт для неизвестных артефактов

# ================= ГЛАВНОЕ МЕНЮ РЫНКА =================

@bot.callback_query_handler(func=lambda call: call.data == 'market_main')
def handle_market_main(call):
    uid = call.from_user.id
    
    if is_user_locked(uid):
        try: bot.answer_callback_query(call.id, "❌ Доступ на Рынок закрыт! У вас активные ограничения или штраф.", show_alert=True)
        except: pass
        return

    # Фейсконтроль: Пускаем только тех, кто заработал 100+ очков за всё время (или VIP/Квир)
    u_info = db['users'].find_one({"_id": uid}) or {}
    user_data = paid_collection.find_one({"uid": uid}) or {}
    if user_data.get("bounty_points", 0) < 50 and not (u_info.get("is_vip") or u_info.get("is_queer")):
        try: bot.answer_callback_query(call.id, "🛑 Фейсконтроль: Рынок доступен только опытным пользователям (Накопите 50 очков или получите VIP).", show_alert=True)
        except: pass
        return

    try: bot.answer_callback_query(call.id)
    except: pass

    active_lots_count = db['market_orders'].count_documents({"status": "active"})

    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton(f"🛒 Смотреть витрину ({active_lots_count} лотов)", callback_data="market_show_0"),
        InlineKeyboardButton("📦 Мои выставленные лоты", callback_data="market_my_lots"),
        InlineKeyboardButton("➕ Продать артефакт/промокод", callback_data="market_sell_list"),
        InlineKeyboardButton("🔙 В игровой кабинет", callback_data="btn_game_club")
    )

    text = (
        "⚖️ **ЧЕРНЫЙ РЫНОК СКАЙНЕТА**\n\n"
        "Добро пожаловать на теневую биржу! Здесь пользователи торгуют артефактами, скидками и Золотыми Билетами.\n\n"
        "💡 _Выставляйте свои ненужные призы на продажу и зарабатывайте реальный кэшбек!\n"
        "Комиссия рынка: 10% с каждой успешной сделки._"
    )
    try: bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
    except: pass

# ================= ВЫСТАВЛЕНИЕ ЛОТА =================

@bot.callback_query_handler(func=lambda call: call.data == 'market_sell_list')
def handle_market_sell_list(call):
    uid = call.from_user.id
    
    # Ищем все активные промокоды юзера, КРОМЕ аирдропов
    user_promos = list(db['promocodes'].find({"owner_uid": uid, "is_active": True, "used_count": 0, "type": {"$ne": "airdrop"}}))
    
    if not user_promos:
        try: bot.answer_callback_query(call.id, "🪹 У вас нет артефактов или купонов для продажи!", show_alert=True)
        except: pass
        return
        
    try: bot.answer_callback_query(call.id)
    except: pass

    markup = InlineKeyboardMarkup(row_width=1)
    for p in user_promos:
        t_name = "Штраф" if p.get('target') == 'fine' else "Рекламу" if p.get('target') == 'ads' else "VIP" if p.get('target') == 'vip' else "Услугу"
        val = f"{p.get('value')}%" if p.get('type') == 'percent' else f"{p.get('value')}₽"
        btn_text = f"{p['_id']} (Скидка {val} на {t_name})"
        markup.add(InlineKeyboardButton(btn_text, callback_data=f"market_price_{p['_id']}"))
        
    markup.add(InlineKeyboardButton("🔙 Назад", callback_data="market_main"))
    
    try: bot.edit_message_text("➕ **ВЫБОР ЛОТА**\n\nВыберите артефакт из вашего инвентаря, который хотите продать:", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
    except: pass

@bot.callback_query_handler(func=lambda call: call.data.startswith('market_price_'))
def handle_market_set_price(call):
    promo_id = call.data.replace("market_price_", "")
    uid = call.from_user.id
    
    promo = db['promocodes'].find_one({"_id": promo_id, "owner_uid": uid, "is_active": True, "used_count": 0})
    if not promo:
        try: bot.answer_callback_query(call.id, "❌ Этот промокод уже недоступен!", show_alert=True)
        except: pass
        return

    try: bot.answer_callback_query(call.id)
    except: pass

    base_price = get_base_price_rub(promo.get("target", "all"))
    max_price = int(base_price * 1.2) # Максимум 120%
    
    try: bot.delete_message(call.message.chat.id, call.message.message_id)
    except: pass
    
    msg = bot.send_message(
        call.message.chat.id, 
        f"🏷 **Установка цены для {promo_id}**\n\n"
        f"Рекомендуемая (базовая) стоимость: **{base_price}₽**\n"
        f"Максимально разрешенная цена: **{max_price}₽**\n\n"
        f"👇 Введите вашу цену в рублях (цифрами, без пробелов):", 
        parse_mode="Markdown"
    )
    bot.register_next_step_handler(msg, process_market_price, promo_id=promo_id, max_price=max_price)

def process_market_price(message, promo_id, max_price):
    if message.text == '/start':
        from handlers.start_menu import send_welcome
        send_welcome(message)
        return

    uid = message.from_user.id
    
    if not message.text.isdigit():
        bot.send_message(message.chat.id, "❌ Цена должна состоять только из цифр. Начните заново.")
        return
        
    price = int(message.text)
    if price < 10 or price > max_price:
        bot.send_message(message.chat.id, f"❌ Недопустимая цена! Сумма должна быть от 10₽ до {max_price}₽. Начните заново.")
        return

    # Проверяем, существует ли еще промокод
    promo = db['promocodes'].find_one({"_id": promo_id, "owner_uid": uid, "is_active": True, "used_count": 0})
    if not promo:
        bot.send_message(message.chat.id, "❌ Артефакт больше недоступен.")
        return

    # 1. Забираем промокод (отвязываем от юзера, чтобы не смог использовать)
    db['promocodes'].update_one({"_id": promo_id}, {"$set": {"owner_uid": "MARKET"}})

    # 2. Создаем лот на рынке
    import time
    db['market_orders'].insert_one({
        "promo_id": promo_id,
        "seller_uid": uid,
        "seller_name": message.from_user.first_name,
        "price_rub": price,
        "target": promo.get("target"),
        "value": promo.get("value"),
        "type": promo.get("type"),
        "status": "active",
        "created_at": time.time()
    })

    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("⚖️ На Черный Рынок", callback_data="market_main"))
    bot.send_message(message.chat.id, f"✅ **Лот успешно выставлен!**\n\nАртефакт `{promo_id}` размещен на витрине за **{price}₽**.\nЕсли его купят, вы получите **{int(price * 0.9)}₽** на ваш рублевый баланс (с учетом 10% комиссии рынка).", parse_mode="Markdown", reply_markup=markup)

# ================= ВИТРИНА (ПАГИНАЦИЯ И ПОКУПКА) =================

@bot.callback_query_handler(func=lambda call: call.data.startswith('market_show_'))
def handle_market_showcase(call):
    uid = call.from_user.id
    page = int(call.data.split('_')[2])
    
    active_lots = list(db['market_orders'].find({"status": "active"}).sort("created_at", -1))
    total_lots = len(active_lots)
    
    if total_lots == 0:
        try: bot.answer_callback_query(call.id, "🪹 Витрина пуста. Никто ничего не продает.", show_alert=True)
        except: pass
        return

    try: bot.answer_callback_query(call.id)
    except: pass

    # Зацикливаем страницы (чтобы с последней кидало на первую и наоборот)
    if page < 0: page = total_lots - 1
    if page >= total_lots: page = 0
    
    lot = active_lots[page]
    price_rub = lot['price_rub']
    price_stars = math.ceil(price_rub / 2)
    price_pts = int(price_rub * 2.5) # Конвертация рублей в очки
    
    t_name = "Штраф" if lot.get('target') == 'fine' else "Рекламу" if lot.get('target') == 'ads' else "VIP" if lot.get('target') == 'vip' else "Любую услугу"
    val = f"{lot.get('value')}%" if lot.get('type') == 'percent' else f"{lot.get('value')}₽"
    
    seller = lot.get('seller_name', 'Аноним')
    
    text = (
        f"🛒 **ВИТРИНА (Лот {page + 1} из {total_lots})**\n\n"
        f"🏷 **Товар:** Скидка {val} на {t_name}\n"
        f"👤 **Продавец:** {seller}\n\n"
        f"💰 **Стоимость лота:**\n"
        f"• {price_rub}₽ (Кэшбек)\n"
        f"• {price_stars}⭐️ (Telegram Stars)\n"
        f"• {price_pts} 🎰 (Очки Бдительности)"
    )

    markup = InlineKeyboardMarkup(row_width=2)
    
    # Кнопки навигации
    markup.add(
        InlineKeyboardButton("◀️ Пред.", callback_data=f"market_show_{page - 1}"),
        InlineKeyboardButton("След. ▶️", callback_data=f"market_show_{page + 1}")
    )
    
    # Кнопки покупки (если лот не свой)
    if lot['seller_uid'] != uid:
        markup.add(InlineKeyboardButton(f"💸 Купить за {price_rub}₽", callback_data=f"market_buy_cb_{lot['_id']}_{price_rub}"))
        markup.add(InlineKeyboardButton(f"🎰 Купить за {price_pts} очк.", callback_data=f"market_buy_pts_{lot['_id']}_{price_pts}"))
        markup.add(InlineKeyboardButton(f"⭐️ Купить за {price_stars} Звезд", callback_data=f"market_buy_stars_{lot['_id']}_{price_stars}"))
    else:
        markup.add(InlineKeyboardButton("❌ Это ваш лот", callback_data="dummy_btn"))
        
    markup.add(InlineKeyboardButton("🔙 Назад", callback_data="market_main"))

    try: bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
    except: pass

@bot.callback_query_handler(func=lambda call: call.data.startswith('market_buy_'))
def handle_market_buy(call):
    parts = call.data.split('_')
    currency_type = parts[2] # "cb", "pts", "stars"
    lot_id_str = parts[3]
    price = int(parts[4])
    uid = call.from_user.id
    
    from bson.objectid import ObjectId
    
    # 1. Если покупка ЗВЕЗДАМИ — кидаем инвойс (транзакция пройдет в payments.py)
    if currency_type == "stars":
        try: bot.answer_callback_query(call.id)
        except: pass
        try: bot.delete_message(call.message.chat.id, call.message.message_id)
        except: pass
        
        try:
            bot.send_invoice(
                call.message.chat.id, 
                title=f"Покупка лота на Рынке", 
                description="Приобретение артефакта через Черный Рынок.", 
                invoice_payload=f"marketpay_{lot_id_str}_{price}", 
                provider_token="", currency="XTR", 
                prices=[LabeledPrice(label="К оплате", amount=price)]
            )
        except Exception as e: logger.error(f"Ошибка инвойса рынка: {e}")
        return

    # 2. Если покупка ВНУТРЕННИМИ валютами (cb или pts)
    user_data = paid_collection.find_one({"uid": uid}) or {}
    
    if currency_type == "cb":
        if user_data.get("cashback_balance", 0) < price:
            try: bot.answer_callback_query(call.id, "❌ Недостаточно рублей на балансе!", show_alert=True)
            except: pass
            return
    elif currency_type == "pts":
        if user_data.get("bounty_points", 0) < price:
            try: bot.answer_callback_query(call.id, "❌ Недостаточно Очков Бдительности!", show_alert=True)
            except: pass
            return

    # АТОМАРНАЯ ТРАНЗАКЦИЯ (Защита от двойной покупки)
    lot = db['market_orders'].find_one_and_update(
        {"_id": ObjectId(lot_id_str), "status": "active"},
        {"$set": {"status": "sold", "buyer_uid": uid}}
    )
    
    if not lot:
        try: bot.answer_callback_query(call.id, "❌ Упс! Лот уже куплен кем-то другим или снят с продажи.", show_alert=True)
        except: pass
        return
        
    try: bot.answer_callback_query(call.id, "✅ Покупка оформлена!")
    except: pass

    # Списываем средства
    if currency_type == "cb":
        paid_collection.update_one({"uid": uid}, {"$inc": {"cashback_balance": -price}})
    else:
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": -price}})
        
    # Выдаем промокод покупателю
    promo_id = lot['promo_id']
    db['promocodes'].update_one({"_id": promo_id}, {"$set": {"owner_uid": uid}})
    
    # Начисляем продавцу рубли за вычетом 10% комиссии
    seller_uid = lot['seller_uid']
    seller_profit = int(lot['price_rub'] * 0.9)
    paid_collection.update_one({"uid": seller_uid}, {"$inc": {"cashback_balance": seller_profit}})
    
    # Уведомляем покупателя
    try:
        bot.edit_message_text(
            f"🎉 **СДЕЛКА УСПЕШНА!**\n\nВы купили артефакт на Черном Рынке.\nВаш промокод: `{promo_id}`\n_Он уже добавлен в ваш Инвентарь._",
            call.message.chat.id, call.message.message_id, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🔙 В инвентарь", callback_data="forge_main"))
        )
    except: pass

    # Уведомляем продавца
    try:
        bot.send_message(seller_uid, f"💸 **НОВОСТИ С РЫНКА!**\n\nВаш лот `{promo_id}` был успешно продан!\nНа ваш счет зачислено: **{seller_profit}₽** (с учетом 10% комиссии).", parse_mode="Markdown")
    except: pass

# ================= УПРАВЛЕНИЕ СВОИМИ ЛОТАМИ =================

@bot.callback_query_handler(func=lambda call: call.data == 'market_my_lots')
def handle_market_my_lots(call):
    uid = call.from_user.id
    my_lots = list(db['market_orders'].find({"seller_uid": uid, "status": "active"}))
    
    try: bot.answer_callback_query(call.id)
    except: pass

    if not my_lots:
        markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🔙 Назад", callback_data="market_main"))
        try: bot.edit_message_text("📦 Вы ничего не продаете в данный момент.", call.message.chat.id, call.message.message_id, reply_markup=markup)
        except: pass
        return

    markup = InlineKeyboardMarkup(row_width=1)
    for lot in my_lots:
        btn_text = f"❌ Снять: {lot['promo_id']} ({lot['price_rub']}₽)"
        markup.add(InlineKeyboardButton(btn_text, callback_data=f"market_cancel_{lot['_id']}"))
        
    markup.add(InlineKeyboardButton("🔙 Назад", callback_data="market_main"))
    try: bot.edit_message_text("📦 **ВАШИ АКТИВНЫЕ ЛОТЫ**\n\nНажмите на лот, чтобы снять его с продажи и вернуть артефакт в инвентарь:", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
    except: pass

@bot.callback_query_handler(func=lambda call: call.data.startswith('market_cancel_'))
def handle_market_cancel_lot(call):
    lot_id_str = call.data.replace("market_cancel_", "")
    uid = call.from_user.id
    
    try:
        from bson.objectid import ObjectId
        
        # 1. Атомарно находим ваш лот и меняем статус на "отменен"
        lot = db['market_orders'].find_one_and_update(
            {"_id": ObjectId(lot_id_str), "seller_uid": uid, "status": "active"},
            {"$set": {"status": "cancelled"}}
        )
        
        if not lot:
            try: bot.answer_callback_query(call.id, "❌ Лот уже продан или снят!", show_alert=True)
            except: pass
            return
            
        # 2. Возвращаем промокод законному владельцу
        db['promocodes'].update_one({"_id": lot['promo_id']}, {"$set": {"owner_uid": uid}})
        
        try: bot.answer_callback_query(call.id, "✅ Лот снят с продажи! Артефакт возвращен в инвентарь.", show_alert=True)
        except: pass
        
        # 3. Перерисовываем список ваших лотов
        handle_market_my_lots(call)
        
    except Exception as e:
        logger.error(f"Ошибка отмены лота на рынке: {e}")
        try: bot.answer_callback_query(call.id, "❌ Системная ошибка при снятии лота.", show_alert=True)
        except: pass