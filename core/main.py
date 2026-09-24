import os
import time
import telebot
import threading
import requests
from flask import Flask, request
import hashlib
import hmac
import json
import random
import html
from urllib.parse import unquote
from flask import render_template, jsonify
from database.mongo import paid_collection, db
from config import BOT_TOKEN

from config import APP_URL, PORT
from core.bot import bot
from core.scheduler import start_scheduler
from utils.logger import logger

# Импорт хэндлеров (ПОРЯДОК КРИТИЧЕСКИ ВАЖЕН)
import handlers.security
import handlers.admin
import handlers.casino
import handlers.payments
import handlers.polls
import handlers.contests
import handlers.market
import handlers.start_menu

app = Flask(__name__, template_folder='templates')

is_setup_done = False

def setup():
    """Настройка бота"""
    global is_setup_done
    start_scheduler() 
    time.sleep(2)
    
    try:
        if not APP_URL:
            logger.error("❌ APP_URL не задан!")
            return

        target_url = f"{APP_URL.rstrip('/')}/webhook"
        bot_token = os.getenv('BOT_TOKEN')
        
        logger.info(f"🔄 Устанавливаем вебхук: {target_url}")
        
        requests.get(
            f"https://api.telegram.org/bot{bot_token}/deleteWebhook?drop_pending_updates=True",
            timeout=8
        )
        time.sleep(1)
        
        res = requests.get(
            f"https://api.telegram.org/bot{bot_token}/setWebhook?url={target_url}&drop_pending_updates=True",
            timeout=15
        )
        
        if res.status_code == 200:
            logger.info("✅ Вебхук успешно установлен!")
        else:
            logger.error(f"❌ Telegram ответил: {res.text}")
            
    except Exception as e:
        logger.error(f"❌ Сбой вебхука: {e}")

@app.route('/')
def index():
    return "Secretary Bot is Online and Healthy!", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    update = telebot.types.Update.de_json(request.stream.read().decode('utf-8'))
    bot.process_new_updates([update])
    return 'ok', 200

@app.route('/ping')
def ping():
    return "I am alive!", 200

# ================= WEB APP API =================

def validate_webapp_data(init_data, token):
    """Секретная функция проверки подписи от Telegram"""
    try:
        parsed_data = dict(qc.split("=", 1) for qc in unquote(init_data).split("&"))
        if "hash" not in parsed_data: return False
        hash_val = parsed_data.pop("hash")
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))
        secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        calc_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        return calc_hash == hash_val
    except Exception:
        return False

@app.route('/webapp')
def webapp_page():
    return render_template('webapp.html')

@app.route('/api/profile', methods=['POST'])
def get_profile():
    data = request.json
    init_data = data.get('initData')
    
    if not validate_webapp_data(init_data, BOT_TOKEN):
        return jsonify({"error": "Взлом жопы отклонен"}), 403

    parsed_data = dict(qc.split("=", 1) for qc in unquote(init_data).split("&"))
    user_info = json.loads(parsed_data['user'])
    uid = user_info['id']
    
    import time
    # 🔥 Радар Скайнета: Запоминаем, что юзер зашел в Web App именно сейчас
    db['users'].update_one({"_id": uid}, {"$set": {"last_webapp_visit": time.time()}}, upsert=True)
    
    user_db = paid_collection.find_one({"uid": uid}) or {}
    return jsonify({
        "points": user_db.get("bounty_points", 0),
        "rubles": user_db.get("cashback_balance", 0)
    })

@app.route('/api/buy_ticket', methods=['POST'])
def buy_ticket():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN):
        return jsonify({"error": "Auth failed"}), 403

    parsed_data = dict(qc.split("=", 1) for qc in unquote(data.get('initData')).split("&"))
    user_info = json.loads(parsed_data['user'])
    uid = user_info['id']
    username = user_info.get('first_name', 'Аноним')
    
    amount = int(data.get('amount', 1))
    giveaway_id = data.get('giveaway_id')
    
    gw = db['giveaways'].find_one({"_id": giveaway_id, "status": "active"})
    if not gw: return jsonify({"error": "Розыгрыш окончен или не найден"}), 400
    
    total_cost = amount * gw['ticket_price']
    
    updated_user = paid_collection.find_one_and_update(
        {"uid": uid, "bounty_points": {"$gte": total_cost}},
        {"$inc": {"bounty_points": -total_cost}}
    )
    if not updated_user:
        return jsonify({"error": "Недостаточно очков!"}), 400
        
    updated_gw = db['giveaways'].find_one_and_update(
        {"_id": giveaway_id},
        {"$inc": {"last_ticket_num": amount, "total_tickets": amount}}
    )
    
    start_num = updated_gw.get('last_ticket_num', 0) + 1
    end_num = start_num + amount - 1
    
    import time
    db['tickets_history'].insert_one({
        "giveaway_id": giveaway_id,
        "uid": uid,
        "name": username,
        "amount": amount,
        "range": f"{start_num}-{end_num}",
        "timestamp": time.time()
    })
    
    return jsonify({"success": True, "start": start_num, "end": end_num})

@app.route('/api/get_giveaways', methods=['POST'])
def api_get_giveaways():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN):
        return jsonify({"error": "Auth failed"}), 403
        
    import datetime
    now = datetime.datetime.now()
    
    active_gws = list(db['giveaways'].find({"status": "active"}).sort("end_date", 1))
    completed_gws = list(db['giveaways'].find({"status": "completed"}).sort("end_date", -1).limit(5))
    
    gws = active_gws + completed_gws
    
    result = []
    for gw in gws:
        time_left_str = ""
        if gw['status'] == 'active':
            # Умный расчет времени без багов
            time_left = gw['end_date'] - now
            total_seconds = int(time_left.total_seconds())
            
            if total_seconds > 0:
                days = total_seconds // 86400
                hours = (total_seconds % 86400) // 3600
                mins = (total_seconds % 3600) // 60
                
                if days > 0: time_left_str = f"Осталось {days} д. {hours} ч."
                elif hours > 0: time_left_str = f"Осталось {hours} ч. {mins} мин."
                else: time_left_str = f"Осталось {mins} мин."
            else:
                time_left_str = "Подводим итоги..."
        else:
            time_left_str = "Завершен"
            
        winner_name = "Нет участников"
        if gw.get('winner_name'):
            winner_name = gw.get('winner_name')
        elif gw.get('winner_uid'):
            win_tx = db['tickets_history'].find_one({"giveaway_id": gw["_id"], "uid": gw["winner_uid"]})
            if win_tx:
                winner_name = win_tx.get('name', f"ID {gw['winner_uid']}")
            else:
                winner_name = f"ID {gw['winner_uid']}"

        result.append({
            "id": str(gw["_id"]),
            "title": gw["title"],
            "price": gw["ticket_price"],
            "total": gw.get("total_tickets", 0),
            "time_left": time_left_str,
            "status": gw["status"],
            "winner_name": winner_name,
            "winning_number": gw.get("winning_number"),
            "winners_count": gw.get("winners_count", 1)
        })
        
    return jsonify(result)

@app.route('/api/get_market', methods=['POST'])
def api_get_market():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN):
        return jsonify({"error": "Auth failed"}), 403

    lots = list(db['market_orders'].find({"status": "active"}).sort("created_at", -1))
    prices_db = db['settings'].find_one({"_id": "prices"}) or {}
    
    import time
    from datetime import datetime
    now = time.time()
    
    result = []
    for lot in lots:
        # 1. Правильные названия
        if lot.get('type') == 'artifact' and lot.get('target') == 'mute':
            title_str = "🚓 Ордер на Арест (1 час)"
        else:
            t_name = "Штраф" if lot.get('target') == 'fine' else "Рекламу" if lot.get('target') == 'ads' else "VIP" if lot.get('target') == 'vip' else "Любую услугу"
            val = f"{lot.get('value')}%" if lot.get('type') == 'percent' else f"{lot.get('value')}₽"
            title_str = f"Скидка {val} на {t_name}"
            
        # 2. Вычисляем, продают ли НИЖЕ РЫНКА
        target_type = lot.get("target", "all")
        base_price = 500
        if target_type == "vip": base_price = prices_db.get("vip_price_stars", 250) * 2
        elif target_type == "ads": base_price = prices_db.get("ads_price_stars", 150) * 2
        elif target_type == "fine": base_price = prices_db.get("fine_price_stars", 650) * 2
        
        real_value = 500 if lot.get("type") == "artifact" else int(base_price * (lot.get("value", 0) / 100))
        rec_price = int(real_value * 0.6)
        if rec_price < 10: rec_price = 10
        
        original_rub = lot['price_rub']
        is_below_market = original_rub < rec_price # True, если цена продавца меньше рекомендованной
            
        # 3. Дата и время + Уценка
        created_at = lot.get('created_at', now)
        dt_str = datetime.fromtimestamp(created_at).strftime('%d.%m %H:%M')
        age_seconds = now - created_at
        
        discount_pct = 0
        if age_seconds > 172800: # Больше 48 часов
            discount_pct = 50
        elif age_seconds > 86400: # Больше 24 часов
            discount_pct = 20
            
        current_rub = original_rub
        if discount_pct > 0:
            current_rub = int(original_rub * (1 - discount_pct / 100))
            if current_rub < 5: current_rub = 5 
            
        current_pts = int(current_rub * 2.5)
        original_pts = int(original_rub * 2.5)
        
        result.append({
            "id": str(lot["_id"]),
            "seller": lot.get('seller_name', 'Аноним'),
            "seller_uid": lot.get('seller_uid'),
            "title": title_str,
            "date_str": dt_str,
            "price_rub": current_rub,
            "price_pts": current_pts,
            "old_price_rub": original_rub if discount_pct > 0 else None,
            "old_price_pts": original_pts if discount_pct > 0 else None,
            "discount": discount_pct,
            "is_below_market": is_below_market # <--- Передаем флаг на фронтенд
        })
        
    return jsonify(result)

@app.route('/api/buy_market', methods=['POST'])
def api_buy_market():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN):
        return jsonify({"error": "Auth failed"}), 403

    parsed_data = dict(qc.split("=", 1) for qc in unquote(data.get('initData')).split("&"))
    user_info = json.loads(parsed_data['user'])
    uid = user_info['id']
    
    lot_id = data.get('lot_id')
    currency = data.get('currency')
    from bson.objectid import ObjectId
    
    user_db = paid_collection.find_one({"uid": uid}) or {}
    lot = db['market_orders'].find_one({"_id": ObjectId(lot_id), "status": "active"})
    
    if not lot: return jsonify({"error": "Упс! Лот уже продан или снят с продажи!"}), 400
    if lot['seller_uid'] == uid: return jsonify({"error": "Вы не можете купить свой собственный лот!"}), 400
        
    # 🔥 ЛОГИКА УЦЕНКИ И УМНЫХ СУБСИДИЙ 🔥
    import time
    age_seconds = time.time() - lot.get('created_at', time.time())
    
    discount_pct = 0
    if age_seconds > 172800: discount_pct = 50
    elif age_seconds > 86400: discount_pct = 20
    
    original_rub = lot['price_rub']
    buyer_price_rub = original_rub
    
    if discount_pct > 0:
        buyer_price_rub = int(original_rub * (1 - discount_pct / 100))
        if buyer_price_rub < 5: buyer_price_rub = 5
        
    buyer_price_pts = int(buyer_price_rub * 2.5)
    
    # === 1. СПИСАНИЕ С ПОКУПАТЕЛЯ ===
    if currency == "rub":
        if user_db.get("cashback_balance", 0) < buyer_price_rub:
            return jsonify({"error": "Недостаточно рублей (кэшбэка)!"}), 400
        paid_collection.update_one({"uid": uid}, {"$inc": {"cashback_balance": -buyer_price_rub}})
    elif currency == "pts":
        if user_db.get("bounty_points", 0) < buyer_price_pts:
            return jsonify({"error": "Недостаточно очков!"}), 400
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": -buyer_price_pts}})
        pts_commission = buyer_price_pts - int(buyer_price_pts * 0.9)
        db['safes_state'].update_one({"_id": "safe_blue"}, {"$inc": {"balance": pts_commission}})
    else: return jsonify({"error": "Ошибка валюты!"}), 400
        
    db['market_orders'].update_one({"_id": ObjectId(lot_id)}, {"$set": {"status": "sold", "buyer_uid": uid}})
    
    # === 2. РАСЧЕТ ВЫПЛАТЫ ПРОДАВЦУ И СУБСИДИИ ===
    prices_db = db['settings'].find_one({"_id": "prices"}) or {}
    target_type = lot.get("target", "all")
    base_price = 500
    if target_type == "vip": base_price = prices_db.get("vip_price_stars", 250) * 2
    elif target_type == "ads": base_price = prices_db.get("ads_price_stars", 150) * 2
    elif target_type == "fine": base_price = prices_db.get("fine_price_stars", 650) * 2
    
    real_value = 500 if lot.get("type") == "artifact" else int(base_price * (lot.get("value", 0) / 100))
    rec_price = int(real_value * 0.6)
    if rec_price < 10: rec_price = 10
    
    # Адекватная ли цена? (Даем люфт +10 руб)
    is_adequate = original_rub <= (rec_price + 10)
    
    gross_payout = original_rub
    subsidy_msg = ""
    
    if discount_pct > 0:
        if is_adequate:
            if discount_pct == 20:
                gross_payout = int(original_rub * 0.90) # Теряет 10%, Скайнет платит 10%
                subsidy_msg = "🔥 _Лот ушел со скидкой -20%. Так как ваша цена была честной, Скайнет компенсировал половину уценки из своих фондов!_"
            elif discount_pct == 50:
                gross_payout = int(original_rub * 0.85) # Теряет 15%, Скайнет платит 35%
                subsidy_msg = "🔥 _Лот ушел со скидкой -50%. Так как ваша цена была честной, Скайнет компенсировал 35% от уценки из своих фондов!_"
        else:
            gross_payout = buyer_price_rub # Жадный продавец теряет всё
            subsidy_msg = f"📉 _Лот продан со скидкой -{discount_pct}%. Цена была выше рекомендованной, поэтому субсидия от Скайнета не начислена._"
            
    # Комиссия рынка
    has_rhodo = db['farm_plots'].find_one({"uid": lot['seller_uid'], "seed_type": "rhododendron", "status": "ready"})
    multiplier = 0.95 if has_rhodo else 0.90
    seller_profit = int(gross_payout * multiplier)
    
    paid_collection.update_one({"uid": lot['seller_uid']}, {"$inc": {"cashback_balance": seller_profit}})
    
    if currency == "rub":
        safe_commission = buyer_price_rub - seller_profit
        if safe_commission > 0: db['safes_state'].update_one({"_id": "safe_red"}, {"$inc": {"balance": safe_commission}}) 
    
    promo_id = lot['promo_id']
    db['promocodes'].update_one({"_id": promo_id}, {"$set": {"owner_uid": uid}})
    
    # === 3. УВЕДОМЛЕНИЕ ПРОДАВЦУ ===
    try:
        from core.bot import bot
        msg_text = f"💸 **НОВОСТИ С РЫНКА!**\n\nВаш лот `{promo_id}` был успешно продан!\nНа ваш счет зачислено: **{seller_profit}₽** (комиссия рынка учтена)."
        if subsidy_msg: msg_text += f"\n\n{subsidy_msg}"
        bot.send_message(lot['seller_uid'], msg_text, parse_mode="Markdown")
    except: pass
    
    return jsonify({"success": True, "promo_id": promo_id})

@app.route('/api/spin_roulette', methods=['POST'])
def api_spin_roulette():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN):
        return jsonify({"error": "Auth failed"}), 403

    parsed_data = dict(qc.split("=", 1) for qc in unquote(data.get('initData')).split("&"))
    user_info = json.loads(parsed_data['user'])
    uid = user_info['id']

    username = user_info.get('username')
    username_str = f"@{username}" if username else f"ID {uid}"
    first_name = user_info.get('first_name', 'Аноним')

    SPIN_PRICE = 50
    updated_user = paid_collection.find_one_and_update(
        {"uid": uid, "bounty_points": {"$gte": SPIN_PRICE}},
        {"$inc": {"bounty_points": -SPIN_PRICE}}
    )

    # 🔥 ТРЕКЕР ДЛЯ ЗОЛОТОГО КЕЙСА 🔥
    import datetime
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    db['tasks_progress'].update_one({"uid": uid, "date": today_str}, {"$inc": {"roulette_spins": 1}}, upsert=True)

    if not updated_user:
        return jsonify({"error": "Недостаточно очков! Нужно 50 💎."}), 400

    import random
    val = random.randint(1, 64)
    prize_msg = ""
    prize_id = ""
    prize_name = ""
    
    bank_data = db['casino_bank'].find_one({"_id": "premium_fund"}) or {"balance": 0}
    premium_cost_stars = 1500

    if val == 63 and bank_data.get("balance", 0) >= premium_cost_stars:
        db['casino_bank'].update_one({"_id": "premium_fund"}, {"$inc": {"balance": -premium_cost_stars}})
        prize_msg = "🏆 ГЛАВНЫЙ СУПЕР-ПРИЗ!!!\nВы выиграли Telegram Premium (3 мес.)!\nЗаявка отправлена админам."
        prize_id, prize_name = "premium", "TG Premium"
        
        import time
        db['premium_claims'].insert_one({"uid": uid, "username": username_str, "timestamp": time.time(), "status": "pending"})
        try:
            from core.bot import bot
            from config import STAFF_GROUP_ID, PRIZES_THREAD_ID
            from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
            markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ Обработать в ЦУП", url="https://elite-poster-bot.onrender.com/glaz"))
            bot.send_message(STAFF_GROUP_ID, f"🏆 <b>СОРВАН ДЖЕКПОТ (TELEGRAM PREMIUM) ИЗ WEB APP!</b> 🏆\n\n👤 Победитель: {first_name} ({username_str})\n\n❗️ <i>Заявка добавлена в Веб-панель.</i>", parse_mode="HTML", reply_markup=markup, message_thread_id=PRIZES_THREAD_ID)
        except: pass

    elif val == 63:
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": 1000, "jackpot_shards": 5}})
        prize_msg = "🎰 МИНИ-ДЖЕКПОТ!\nФонд Premium пуст, поэтому вы получаете +1000 Очков и 5 Осколков!"
        prize_id, prize_name = "mini_jackpot", "Мини-Джекпот"

    elif val == 64:
        code = f"JACKPOT-{random.randint(1000, 9999)}"
        db['promocodes'].insert_one({"_id": code, "type": "percent", "value": 100, "target": "vip", "usage_limit": 1, "used_count": 0, "is_active": True, "owner_uid": uid})
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": 300}})
        prize_msg = f"🚨 ДЖЕКПОТ 7️⃣7️⃣7️⃣!\nЗолотой Билет (VIP) и 300 очков!\nКод: {code}"
        prize_id, prize_name = "jackpot", "ДЖЕКПОТ VIP"

    elif val in [7, 21, 35]:
        prize_msg = "🌟 СУПЕР-РЕДКИЙ ДРОП!\nВы выиграли право установить Личный Тег!\nНажмите кнопку 'Рюкзак -> Ваши промокоды' или проверьте ЛС бота."
        prize_id, prize_name = "custom_tag", "Личный Тег"
        try:
            from core.bot import bot
            from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
            markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✍️ Заказать свой тег", callback_data="claim_custom_tag"))
            bot.send_message(uid, "👑 Вы выиграли купон на создание Личного Статуса!", reply_markup=markup)
        except: pass

    elif val in [1, 22, 43]:
        paid_collection.update_one({"uid": uid}, {"$inc": {"immunity": 1, "bounty_points": 50}})
        prize_msg = "🔥 ЭПИЧЕСКИЙ ВЫИГРЫШ!\nВы получили 🛡 Щит Иммунитета и 50 очков!"
        prize_id, prize_name = "shield", "Щит Иммунитета"

    elif val in [10, 20, 40, 50]:
        lost_points = int(updated_user.get("bounty_points", 0) * 0.3)
        has_cactus = db['farm_plots'].find_one({"uid": uid, "seed_type": "cactus", "status": "ready"})
        if has_cactus:
            lost_points = int(updated_user.get("bounty_points", 0) * 0.1) 
            
        if lost_points < 10: lost_points = 10
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": -lost_points}})
        db['safes_state'].update_one({"_id": "safe_blue"}, {"$inc": {"balance": lost_points}})
        
        if has_cactus:
            prize_msg = f"🌵 НАЛОГОВАЯ ПРОВЕРКА!\nКактус отпугнул инспектора! Списано лишь 10% (-{lost_points} очков)."
            prize_id, prize_name = "tax_cactus", "Спас Кактус"
        else:
            prize_msg = f"💀 НАЛОГОВАЯ ПРОВЕРКА!\nСписано 30% баланса (-{lost_points} очков)."
            prize_id, prize_name = "tax", "Налоговая (-30%)"

    elif val in [5, 17, 29]:
        code = f"ARREST-{random.randint(100, 999)}"
        db['promocodes'].insert_one({"_id": code, "type": "artifact", "value": 0, "target": "mute", "usage_limit": 1, "used_count": 0, "is_active": True, "owner_uid": uid})
        prize_msg = f"🚓 СОЦИАЛЬНЫЙ АРТЕФАКТ!\nВы нашли Ордер на Арест!\nКод: {code}"
        prize_id, prize_name = "arrest", "Ордер на Арест"

    elif val in [13, 26, 39, 52]:
        strikes = updated_user.get("strikes", 0)
        if strikes > 0:
            paid_collection.update_one({"uid": uid}, {"$inc": {"strikes": -1}})
            prize_msg = f"🕊 АМНИСТИЯ!\nСписан 1 штрафной страйк!"
        else:
            paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": 100}})
            prize_msg = "🕊 БЕЛЫЙ БИЛЕТ!\nУ вас нет страйков. Получите +100 очков!"
        prize_id, prize_name = "amnesty", "Амнистия"

    elif val in [15, 30, 45, 60]:
        win_points = random.choice([100, 150, 250])
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": win_points}})
        prize_msg = f"💸 КРУПНЫЙ КУШ!\nВы выиграли {win_points} 💎!"
        prize_id, prize_name = "big_points", f"{win_points} 💎"

    elif val % 7 == 0:
        promos = [
            {"target": "fine", "value": 50, "prefix": "FINE50", "name": "50% на Штраф"},
            {"target": "ads", "value": 30, "prefix": "ADS30", "name": "30% на Рекламу"},
            {"target": "vip", "value": 40, "prefix": "VIP40", "name": "40% на VIP"},
            {"target": "all", "value": 15, "prefix": "ALL15", "name": "15% на Любое"}
        ]
        drop = random.choice(promos)
        code = f"{drop['prefix']}-{random.randint(1000, 9999)}"
        db['promocodes'].insert_one({"_id": code, "type": "percent", "value": drop["value"], "target": drop["target"], "usage_limit": 1, "used_count": 0, "is_active": True, "owner_uid": uid})
        prize_msg = f"✨ РЕДКИЙ ДРОП!\nВыиграна скидка {drop['name']}!\nКод: {code}"
        prize_id, prize_name = "discount", "Скидка"

    elif val in [11, 33]:
        win_rub = random.choices([100, 250, 500], weights=[75, 20, 5], k=1)[0]
        cost_in_stars = win_rub // 2 
        if bank_data.get("balance", 0) >= cost_in_stars:
            db['casino_bank'].update_one({"_id": "premium_fund"}, {"$inc": {"balance": -cost_in_stars}})
            paid_collection.update_one({"uid": uid}, {"$inc": {"cashback_balance": win_rub}})
            prize_msg = f"✨ ДЕНЕЖНЫЙ КУПОН! ✨\nВы выиграли {win_rub} руб. на счет!"
            prize_id, prize_name = "rubles", f"{win_rub} ₽"
        else:
            fallback_points = win_rub * 2
            paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": fallback_points}})
            prize_msg = f"💸 КРУПНЫЙ КУШ!\nВы выиграли {fallback_points} очков!"
            prize_id, prize_name = "big_points", f"{fallback_points} 💎"

    else:
        shards_won = random.choice([1, 1, 1, 2])
        paid_collection.update_one({"uid": uid}, {"$inc": {"jackpot_shards": shards_won}})
        prize_msg = f"🧩 Барабан остановился...\nВы получили: +{shards_won} Осколок(ка) джекпота!"
        prize_id, prize_name = "shards", f"{shards_won} Осколка"

    return jsonify({"success": True, "message": prize_msg, "won_id": prize_id, "won_name": prize_name})

@app.route('/api/claim_bonus', methods=['POST'])
def api_claim_bonus():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN):
        return jsonify({"error": "Auth failed"}), 403

    parsed_data = dict(qc.split("=", 1) for qc in unquote(data.get('initData')).split("&"))
    uid = json.loads(parsed_data['user'])['id']
    
    import datetime
    now = datetime.datetime.now()
    
    user_data = paid_collection.find_one({"uid": uid}) or {}
    last_bonus = user_data.get("last_bonus_date")
    current_streak = user_data.get("bonus_streak", 0)
    
    # Проверяем таймер
    if last_bonus:
        time_diff = (now - last_bonus).total_seconds()
        if time_diff < 86400: # Прошло меньше 24 часов
            hours_left = int((86400 - time_diff) // 3600)
            mins_left = int(((86400 - time_diff) % 3600) // 60)
            return jsonify({"error": f"Рано! Приходите через {hours_left}ч {mins_left}м."}), 400
        elif time_diff > 172800: # Прошло БОЛЬШЕ 48 часов - стрик сгорел
            current_streak = 0
            
    # Увеличиваем стрик
    current_streak += 1

    # 🔥 ИСПРАВЛЕНИЕ: Генерируем today_str 🔥
    today_str = now.strftime("%Y-%m-%d")
    
    # 🔥 ТРЕКЕР ДЛЯ СЕРЕБРЯНОГО КЕЙСА (БОНУС) 🔥
    db['tasks_progress'].update_one({"uid": uid, "date": today_str}, {"$set": {"bonus_claimed": True}}, upsert=True)
    
    # Награды в зависимости от стрика
    shards_reward = 0
    if current_streak == 1: points_reward = 15
    elif current_streak == 2: points_reward = 25
    elif current_streak == 3: points_reward = 40
    elif current_streak >= 7: 
        points_reward = 100
        shards_reward = 1
        current_streak = 0 # Сброс после мега-приза (или можно оставить, чтоб фармил каждый день по 100)
    else: points_reward = 50

    update_data = {
        "$inc": {"bounty_points": points_reward, "jackpot_shards": shards_reward},
        "$set": {"last_bonus_date": now, "bonus_streak": current_streak}
    }
    
    paid_collection.update_one({"uid": uid}, update_data, upsert=True)
    
    msg = f"🎁 День {current_streak if current_streak > 0 else 7}! Вы получили {points_reward} 💎."
    if shards_reward > 0: msg += "\n🧩 +1 Осколок за 7 дней подряд!"
    
    return jsonify({"success": True, "msg": msg, "streak": current_streak})

@app.route('/api/get_inventory', methods=['POST'])
def api_get_inventory():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN):
        return jsonify({"error": "Auth failed"}), 403

    parsed_data = dict(qc.split("=", 1) for qc in unquote(data.get('initData')).split("&"))
    user_info = json.loads(parsed_data['user'])
    uid = user_info['id']
    
    user_data = paid_collection.find_one({"uid": uid}) or {}
    shields = user_data.get("immunity", 0)
    shards = user_data.get("jackpot_shards", 0)
    
    promos = list(db['promocodes'].find({"owner_uid": uid, "is_active": True, "used_count": 0}))
    orders_count = sum(1 for p in promos if p.get("type") == "artifact" and p.get("target") == "mute")
    
    regular_promos = []
    for p in promos:
        if p.get("type") != "artifact":
            t_name = "Штраф" if p.get('target') == 'fine' else "Рекламу" if p.get('target') == 'ads' else "VIP" if p.get('target') == 'vip' else "Услугу"
            val = f"{p.get('value')}%" if p.get('type') == 'percent' else f"{p.get('value')}₽"
            regular_promos.append({"id": p["_id"], "desc": f"Скидка {val} на {t_name}"})
            
    return jsonify({
        "shields": shields,
        "shards": shards,
        "orders": orders_count,
        "promos": regular_promos
    })

@app.route('/api/craft', methods=['POST'])
def api_craft():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN): return jsonify({"error": "Auth failed"}), 403
    
    parsed_data = dict(qc.split("=", 1) for qc in unquote(data.get('initData')).split("&"))
    user_info = json.loads(parsed_data['user'])
    uid = user_info['id']
    
    username = user_info.get('username')
    username_str = f"@{username}" if username else f"ID {uid}"
    first_name = user_info.get('first_name', 'Аноним')
    
    action = data.get('action')
    user_data = paid_collection.find_one({"uid": uid}) or {}
    
    if action == 'shards':
        if user_data.get("jackpot_shards", 0) < 50: return jsonify({"error": "Нужно 50 осколков!"}), 400
        paid_collection.update_one({"uid": uid}, {"$inc": {"jackpot_shards": -50}})
        
        import random
        chance = random.randint(1, 100)
        if chance <= 60:
            code = f"JACKPOT-{random.randint(1000, 9999)}"
            import datetime
            db['promocodes'].insert_one({"_id": code, "type": "percent", "value": 100, "target": "vip", "usage_limit": 1, "used_count": 0, "is_active": True, "owner_uid": uid})
            return jsonify({"success": True, "msg": f"🎉 Собран Золотой Билет VIP!\nКод: {code}"})
        elif chance <= 90:
            paid_collection.update_one({"uid": uid}, {"$inc": {"immunity": 1}})
            return jsonify({"success": True, "msg": "🛡 Вы сковали Щит Иммунитета!"})
        else:
            import time
            db['premium_claims'].insert_one({
                "uid": uid,
                "username": username_str,
                "timestamp": time.time(),
                "status": "pending"
            })
            
            try:
                from core.bot import bot
                from config import STAFF_GROUP_ID, PRIZES_THREAD_ID
                from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
                markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ Обработать в ЦУП", url="https://elite-poster-bot.onrender.com/glaz"))
                bot.send_message(
                    STAFF_GROUP_ID, 
                    f"🏆 <b>СОРВАН ДЖЕКПОТ (TELEGRAM PREMIUM) ИЗ WEB APP!</b> 🏆\n\n"
                    f"👤 Победитель: {first_name} ({username_str})\n\n"
                    f"❗️ <i>Заявка добавлена в Веб-панель.</i>", 
                    parse_mode="HTML", reply_markup=markup, message_thread_id=PRIZES_THREAD_ID
                )
            except Exception as e:
                logger.error(f"Ошибка уведомления ТГ: {e}")
                
            return jsonify({"success": True, "msg": "💎 ДЖЕКПОТ! Вы выиграли Telegram Premium! Заявка отправлена администрации."})

    elif action == 'beyond':
        if user_data.get("bounty_points", 0) < 3000 or user_data.get("immunity", 0) < 2:
            return jsonify({"error": "Нужно 3000 очков и 2 щита!"}), 400
            
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": -3000, "immunity": -2}})
        u_info = db['users'].find_one({"_id": uid}) or {}
        
        if not u_info.get("is_queer"):
            db['users'].update_one({"_id": uid}, {"$set": {"is_queer": True}}, upsert=True)
            return jsonify({"success": True, "msg": "🏳️‍🌈 Выковано: Статус BEYOND!"})
        elif not u_info.get("is_vip"):
            db['users'].update_one({"_id": uid}, {"$set": {"is_vip": True}}, upsert=True)
            return jsonify({"success": True, "msg": "👑 Выковано: Пожизненный VIP!"})
        else:
            paid_collection.update_one({"uid": uid}, {"$inc": {"cashback_balance": 1000}})
            return jsonify({"success": True, "msg": "💰 Макс. уровень! Ресурсы переплавлены в 1000₽ кэшбэка!"})

@app.route('/api/open_chest', methods=['POST'])
def api_open_chest():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN): return jsonify({"error": "Auth failed"}), 403
    
    parsed_data = dict(qc.split("=", 1) for qc in unquote(data.get('initData')).split("&"))
    uid = json.loads(parsed_data['user'])['id']
    
    PRICE = 1000
    user_data = paid_collection.find_one({"uid": uid}) or {}
    points = user_data.get("bounty_points", 0)
    
    if points < PRICE: return jsonify({"error": "Нужно 1000 очков!"}), 400

    import datetime
    today_str = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5))).strftime("%Y-%m-%d")
    db['tasks_progress'].update_one({"uid": uid, "date": today_str}, {"$inc": {"chest_opened": 1}}, upsert=True)
    
    paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": -PRICE}})
    remaining = points - PRICE
    
    import random
    chance = random.randint(1, 100)
    if chance <= 45:
        stolen = int(remaining * random.uniform(0.10, 0.25))
        if stolen > 0: 
            paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": -stolen}})
            # 🔥 ДОБАВЬ ВОТ ЭТУ СТРОЧКУ 🔥
            db['safes_state'].update_one({"_id": "safe_blue"}, {"$inc": {"balance": stolen}})
            
        return jsonify({"success": True, "msg": f"🐈‍⬛ КОТ В МЕШКЕ!\nКот выскочил из сундука и украл {stolen} очков, пока убегал!"})
    elif chance <= 80:
        shards = random.randint(1, 4)
        paid_collection.update_one({"uid": uid}, {"$inc": {"jackpot_shards": shards}})
        return jsonify({"success": True, "msg": f"📦 Сундук открыт!\nНайдено пыль и +{shards} Осколок(ка)."})
    elif chance <= 95:
        paid_collection.update_one({"uid": uid}, {"$inc": {"immunity": 1}})
        return jsonify({"success": True, "msg": "🛡 ОТЛИЧНЫЙ ДРОП!\nВы нашли Щит Иммунитета!"})
    else:
        paid_collection.update_one({"uid": uid}, {"$inc": {"cashback_balance": 500}})
        return jsonify({"success": True, "msg": "💎 ДЖЕКПОТ!!!\nСундук набит деньгами! +500 рублей кэшбэка!"})

@app.route('/api/get_cpa', methods=['POST'])
def api_get_cpa():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN): return jsonify({"error": "Auth failed"}), 403
    uid = json.loads(dict(qc.split("=", 1) for qc in unquote(data.get('initData')).split("&"))['user'])['id']
    
    hold = db['cpa_traffic'].count_documents({"agent_id": uid, "status": "hold"})
    approved = db['cpa_traffic'].count_documents({"agent_id": uid, "status": "approved"})
    fraud = db['cpa_traffic'].count_documents({"agent_id": uid, "status": "fraud"})
    user_data = paid_collection.find_one({"uid": uid}) or {}
    dupes = user_data.get("cpa_duplicates", 0)
    cases = user_data.get("agent_cases", 0) 
    
    return jsonify({"hold": hold, "approved": approved, "fraud": fraud, "duplicates": dupes, "cases": cases})

@app.route('/api/get_cpa_networks', methods=['POST'])
def api_get_cpa_networks():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN): 
        return jsonify({"error": "Auth failed"}), 403
    
    # 🔥 ИМПОРТИРУЕМ ВНУТРИ ФУНКЦИИ, ЧТОБЫ БРАТЬ СВЕЖИЕ ДАННЫЕ ИЗ БАЗЫ 🔥
    from config import chat_ids_mk, chat_ids_parni, chat_ids_ns, chat_ids_gayznak, chat_ids_rainbow
    
    networks = {
        "mk": {"name": "МК (Мужской Клуб)", "cities": chat_ids_mk},
        "parni": {"name": "ПАРНИ 18+", "cities": chat_ids_parni},
        "ns": {"name": "НС (Exotics)", "cities": chat_ids_ns},
        "gayznak": {"name": "ГЕЙ ЧАТЫ", "cities": chat_ids_gayznak},
        "rainbow": {"name": "РАДУГА", "cities": chat_ids_rainbow}
    }
    return jsonify({"networks": networks})

@app.route('/api/generate_cpa_link', methods=['POST'])
def api_generate_cpa_link():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN): 
        return jsonify({"error": "Auth failed"}), 403
        
    parsed_data = dict(qc.split("=", 1) for qc in unquote(data.get('initData')).split("&"))
    uid = json.loads(parsed_data['user'])['id']
    chat_id = data.get('chat_id')
    
    if not chat_id:
        return jsonify({"error": "Город не выбран!"}), 400
        
    try:
        # Дергаем Telegram API для создания заявки с маркером cpa_ID
        invite = bot.create_chat_invite_link(chat_id, creates_join_request=True, name=f"cpa_{uid}")
        return jsonify({"success": True, "link": invite.invite_link})
    except Exception as e:
        logger.error(f"Ошибка CPA ссылки для чата {chat_id}: {e}")
        return jsonify({"error": "Бот не является админом в этом чате или сбой API."}), 400

@app.route('/api/exchange', methods=['POST'])
def api_exchange():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN): return jsonify({"error": "Auth failed"}), 403
    uid = json.loads(dict(qc.split("=", 1) for qc in unquote(data.get('initData')).split("&"))['user'])['id']
    
    cost = data.get('cost')
    reward = data.get('reward')
    
    user_db = paid_collection.find_one_and_update(
        {"uid": uid, "cashback_balance": {"$gte": cost}},
        {"$inc": {"cashback_balance": -cost, "bounty_points": reward}}
    )
    if not user_db: return jsonify({"error": "Недостаточно рублей!"}), 400
    return jsonify({"success": True, "msg": f"✅ Успешно обменяли {cost}₽ на {reward}💎!"})

def get_daily_tasks_matrix(uid, today_str):
    """Секретная Матрица Заданий Скайнета"""
    task_db = db['tasks_progress'].find_one({"uid": uid, "date": today_str}) or {}
    
    import datetime
    weekday = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5))).weekday()
    
    msgs = task_db.get("messages", 0)
    spins = task_db.get("roulette_spins", 0)
    watered = task_db.get("watered", False)
    bonus = task_db.get("bonus_claimed", False)
    chests = task_db.get("chest_opened", 0)
    
    # 0=ПН, 1=ВТ, 2=СР, 3=ЧТ, 4=ПТ, 5=СБ, 6=ВС
    matrix = {
        0: {"name": "День Фермера", "w": {"t": "msgs", "g": 15, "d": "💬 Напишите 15 сообщений в чатах"}, "s": {"t": "water_bonus", "g": 2, "d": "💧 Полить грядку и 🎁 забрать бонус"}, "g": {"t": "spins", "g": 3, "d": "🎰 Сыграйте в Гача-Рулетку 3 раза"}},
        3: {"name": "День Фермера", "w": {"t": "msgs", "g": 15, "d": "💬 Напишите 15 сообщений в чатах"}, "s": {"t": "water_bonus", "g": 2, "d": "💧 Полить грядку и 🎁 забрать бонус"}, "g": {"t": "spins", "g": 3, "d": "🎰 Сыграйте в Гача-Рулетку 3 раза"}},
        
        1: {"name": "День Общения", "w": {"t": "msgs", "g": 25, "d": "💬 Напишите 25 сообщений в чатах"}, "s": {"t": "spins", "g": 2, "d": "🎰 Сделайте 2 прокрута рулетки"}, "g": {"t": "water", "g": 1, "d": "💧 Полейте любую грядку на ферме"}},
        4: {"name": "День Общения", "w": {"t": "msgs", "g": 25, "d": "💬 Напишите 25 сообщений в чатах"}, "s": {"t": "spins", "g": 2, "d": "🎰 Сделайте 2 прокрута рулетки"}, "g": {"t": "water", "g": 1, "d": "💧 Полейте любую грядку на ферме"}},
        
        2: {"name": "День Лудомана", "w": {"t": "msgs", "g": 10, "d": "💬 Напишите 10 сообщений в чатах"}, "s": {"t": "spins", "g": 3, "d": "🎰 Сделайте 3 прокрута рулетки"}, "g": {"t": "chest", "g": 1, "d": "📦 Взломайте Секретный Сундук 1 раз"}},
        5: {"name": "День Лудомана", "w": {"t": "msgs", "g": 10, "d": "💬 Напишите 10 сообщений в чатах"}, "s": {"t": "spins", "g": 3, "d": "🎰 Сделайте 3 прокрута рулетки"}, "g": {"t": "chest", "g": 1, "d": "📦 Взломайте Секретный Сундук 1 раз"}},
        
        6: {"name": "День Отдыха", "w": {"t": "msgs", "g": 5, "d": "💬 Напишите всего 5 сообщений"}, "s": {"t": "bonus", "g": 1, "d": "🎁 Заберите ежедневный бонус в Кабинете"}, "g": {"t": "spins", "g": 1, "d": "🎰 Испытайте удачу: 1 прокрут рулетки"}}
    }
    
    today_m = matrix.get(weekday)
    
    def calc(cfg):
        t = cfg["t"]
        if t == "msgs": return {"val": msgs, "ready": msgs >= cfg["g"]}
        if t == "spins": return {"val": spins, "ready": spins >= cfg["g"]}
        if t == "chest": return {"val": chests, "ready": chests >= cfg["g"]}
        if t == "water": return {"val": 1 if watered else 0, "ready": watered}
        if t == "bonus": return {"val": 1 if bonus else 0, "ready": bonus}
        if t == "water_bonus": return {"val": (1 if watered else 0) + (1 if bonus else 0), "ready": watered and bonus}
        return {"val": 0, "ready": False}

    return {
        "day_name": today_m["name"],
        "opened": task_db.get("opened", []),
        "watered": watered, "bonus": bonus,
        "wooden": {"desc": today_m["w"]["d"], "val": calc(today_m["w"])["val"], "goal": today_m["w"]["g"], "ready": calc(today_m["w"])["ready"], "type": today_m["w"]["t"]},
        "silver": {"desc": today_m["s"]["d"], "val": calc(today_m["s"])["val"], "goal": today_m["s"]["g"], "ready": calc(today_m["s"])["ready"], "type": today_m["s"]["t"]},
        "gold":   {"desc": today_m["g"]["d"], "val": calc(today_m["g"])["val"], "goal": today_m["g"]["g"], "ready": calc(today_m["g"])["ready"], "type": today_m["g"]["t"]}
    }

@app.route('/api/get_tasks', methods=['POST'])
def api_get_tasks():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN): return jsonify({"error": "Auth failed"}), 403
    uid = json.loads(dict(qc.split("=", 1) for qc in unquote(data.get('initData')).split("&"))['user'])['id']
    
    import datetime
    today_str = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5))).strftime("%Y-%m-%d")
    return jsonify(get_daily_tasks_matrix(uid, today_str))

@app.route('/api/open_task_case', methods=['POST'])
def api_open_task_case():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN): return jsonify({"error": "Auth failed"}), 403
    uid = json.loads(dict(qc.split("=", 1) for qc in unquote(data.get('initData')).split("&"))['user'])['id']
    case_type = data.get('case_type')
    
    import datetime
    today_str = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5))).strftime("%Y-%m-%d")
    
    # Загружаем свежую матрицу для проверки условий!
    matrix = get_daily_tasks_matrix(uid, today_str)
    
    if case_type in matrix["opened"]: return jsonify({"error": "Вы уже открывали этот кейс сегодня!"}), 400
    if not matrix[case_type]["ready"]: return jsonify({"error": "Условия задания еще не выполнены!"}), 400
        
    update_query = {"$inc": {}}
    if case_type == 'wooden':
        update_query["$inc"]["bounty_points"] = 50
        update_query["$inc"]["jackpot_shards"] = 1
        msg = "🪵 **Деревянный кейс открыт!**\nВы получили 50 💎 и 1 Осколок рулетки!"
    elif case_type == 'silver':
        update_query["$inc"]["bounty_points"] = 100
        update_query["$inc"]["immunity"] = 1
        msg = "🥈 **Серебряный кейс открыт!**\nВы получили 100 💎 и 1 Щит Иммунитета!"
    elif case_type == 'gold':
        update_query["$inc"]["bounty_points"] = 300
        update_query["$inc"]["key_red"] = 1
        msg = "🥇 **Золотой кейс открыт!**\nВы сорвали куш: 300 💎 и 🔑 Ключ от Финансового Сейфа!"
        
    paid_collection.update_one({"uid": uid}, update_query)
    db['tasks_progress'].update_one({"uid": uid, "date": today_str}, {"$push": {"opened": case_type}}, upsert=True)
    
    return jsonify({"success": True, "msg": msg})

@app.route('/api/payout', methods=['POST'])
def api_payout():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN): return jsonify({"error": "Auth failed"}), 403
    user_info = json.loads(dict(qc.split("=", 1) for qc in unquote(data.get('initData')).split("&"))['user'])
    uid = user_info['id']
    username = user_info.get('username', f"ID {uid}")
    
    amount = int(data.get('amount', 0))
    method = data.get('method')
    details = data.get('details')
    
    if amount < 500: return jsonify({"error": "Минимум 500₽ для вывода!"}), 400
    if method == "На карту" and amount < 3500: return jsonify({"error": "На карту минимум 3500₽!"}), 400
    if not details or len(details) < 5: return jsonify({"error": "Укажите корректные реквизиты!"}), 400
    
    user_db = paid_collection.find_one({"uid": uid}) or {}
    if user_db.get("cashback_balance", 0) < amount: return jsonify({"error": "Недостаточно средств!"}), 400
    
    paid_collection.update_one({"uid": uid}, {"$inc": {"cashback_balance": -amount}})
    
    import time
    db['withdrawals'].insert_one({
        "user_id": uid, "amount": amount, "method": method, "details": details, "status": "pending", "timestamp": time.time()
    })
    
    from core.bot import bot
    from config import STAFF_GROUP_ID, FINANCE_THREAD_ID
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("✅ Выплачено", callback_data=f"payout_done_{uid}_{amount}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"payout_cancel_{uid}_{amount}")
    )
    safe_username = username.replace('_', '\\_')
    try:
        bot.send_message(
            STAFF_GROUP_ID,
            f"💰 **ЗАЯВКА НА ВЫПЛАТУ (WEB APP)**\n\n👤 От: @{safe_username} (`{uid}`)\n💵 Сумма: **{amount} руб.**\n🏦 Способ: **{method}**\n📝 Реквизиты:\n`{details}`",
            reply_markup=markup, parse_mode="Markdown", message_thread_id=FINANCE_THREAD_ID
        )
    except Exception as e:
        logger.error(f"Ошибка уведомления о выплате: {e}")
        
    return jsonify({"success": True, "msg": "Заявка отправлена в финотдел!"})

@app.route('/api/get_stars_invoice', methods=['POST'])
def api_get_stars_invoice():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN): 
        return jsonify({"error": "Auth failed"}), 403
        
    parsed_data = dict(qc.split("=", 1) for qc in unquote(data.get('initData')).split("&"))
    uid = json.loads(parsed_data['user'])['id']
    
    stars_amount = int(data.get('stars_amount', 50))
    points_reward = int(data.get('points_reward', 100))
    
    try:
        from core.bot import bot
        from telebot.types import LabeledPrice
        
        invoice_link = bot.create_invoice_link(
            title="Покупка Очков Бдительности",
            description=f"Пакет: {points_reward} Очков",
            payload=f"webapp_points_{uid}_{points_reward}",
            provider_token="", 
            currency="XTR",
            prices=[LabeledPrice(label="Очки", amount=stars_amount)]
        )
        return jsonify({"success": True, "url": invoice_link})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/get_giveaway_participants', methods=['POST'])
def api_get_giveaway_participants():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN):
        return jsonify({"error": "Auth failed"}), 403
        
    gw_id = data.get('giveaway_id')
    offset = int(data.get('offset', 0))
    
    tickets_history = list(db['tickets_history'].find({"giveaway_id": gw_id}).sort("timestamp", -1).skip(offset).limit(15))
    
    import datetime
    result = []
    for t in tickets_history:
        r_start, r_end = map(int, t['range'].split('-'))
        dt = datetime.datetime.fromtimestamp(t['timestamp']).strftime('%d.%m %H:%M')
        
        for i in range(r_end, r_start - 1, -1):
            result.append({
                "num": i,
                "name": t['name'],
                "date": dt
            })
            
    next_offset = offset + 15 if len(tickets_history) == 15 else None
    
    return jsonify({"participants": result, "next_offset": next_offset})

@app.route('/api/get_my_promos', methods=['POST'])
def api_get_my_promos():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN): 
        return jsonify({"error": "Auth failed"}), 403
        
    uid = json.loads(dict(qc.split("=", 1) for qc in unquote(data.get('initData')).split("&"))['user'])['id']
    
    promos = list(db['promocodes'].find({"owner_uid": uid, "is_active": True, "used_count": 0, "type": {"$ne": "airdrop"}}))
    prices_db = db['settings'].find_one({"_id": "prices"}) or {}
    
    result = []
    for p in promos:
        target_type = p.get("target", "all")
        # Вычисляем базовую стоимость в рублях
        base_price = 500
        if target_type == "vip": base_price = prices_db.get("vip_price_stars", 250) * 2
        elif target_type == "ads": base_price = prices_db.get("ads_price_stars", 150) * 2
        elif target_type == "fine": base_price = prices_db.get("fine_price_stars", 650) * 2
        
        # Считаем Рекомендованную цену (60% от номинала)
        if p.get('type') == 'artifact' and target_type == 'mute':
            real_value = 500
            name_str = "🚓 Ордер на Арест"
        else:
            val = p.get('value', 0)
            real_value = int(base_price * (val / 100))
            t_name = "Штраф" if target_type == 'fine' else "Рекламу" if target_type == 'ads' else "VIP" if target_type == 'vip' else "Любую услугу"
            name_str = f"Скидка {val}% на {t_name}"

        rec_price = int(real_value * 0.6)
        if rec_price < 10: rec_price = 10
            
        full_name_str = f"{p['_id']} ({name_str} | Рек: ~{rec_price}₽)"
        
        result.append({
            "id": p["_id"], 
            "name": full_name_str, 
            "target": target_type
        })
        
    return jsonify(result)

@app.route('/api/add_market_lot', methods=['POST'])
def api_add_market_lot():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN): 
        return jsonify({"error": "Auth failed"}), 403
        
    user_info = json.loads(dict(qc.split("=", 1) for qc in unquote(data.get('initData')).split("&"))['user'])
    uid = user_info['id']
    first_name = user_info.get('first_name', 'Аноним')
    
    promo_id = data.get('promo_id')
    price = int(data.get('price', 0))
    
    promo = db['promocodes'].find_one({"_id": promo_id, "owner_uid": uid, "is_active": True, "used_count": 0})
    if not promo: 
        return jsonify({"error": "Артефакт не найден или уже продан!"}), 400
    
    prices_db = db['settings'].find_one({"_id": "prices"}) or {}
    target_type = promo.get("target", "all")
    base_price = 500
    if target_type == "vip": base_price = prices_db.get("vip_price_stars", 250) * 2
    elif target_type == "ads": base_price = prices_db.get("ads_price_stars", 150) * 2
    elif target_type == "fine": base_price = prices_db.get("fine_price_stars", 650) * 2
    
    # 🔥 НОВАЯ АДЕКВАТНАЯ ОЦЕНКА 🔥
    if promo.get("type") == "artifact":
        max_price = 500 # Ордера можно продавать до 500 рублей
    elif promo.get("type") == "percent":
        # Реальная ценность скидки в рублях
        real_value = int(base_price * (promo.get("value", 0) / 100))
        # Продавать можно максимум за 90% от реальной ценности, чтобы покупателю БЫЛО ВЫГОДНО!
        max_price = int(real_value * 0.9)
        if max_price < 10: max_price = 10
    else:
        max_price = int(base_price * 1.2)
    
    if price < 10 or price > max_price:
        return jsonify({"error": f"Слишком дорого! Никто не купит без выгоды. Максимальная цена: {max_price}₽!"}), 400
        
    import time
    db['promocodes'].update_one({"_id": promo_id}, {"$set": {"owner_uid": "MARKET"}})
    db['market_orders'].insert_one({
        "promo_id": promo_id, 
        "seller_uid": uid, 
        "seller_name": first_name,
        "price_rub": price, 
        "target": promo.get("target"), 
        "value": promo.get("value"),
        "type": promo.get("type"), 
        "status": "active", 
        "created_at": time.time()
    })
    
    return jsonify({"success": True, "msg": f"Лот успешно выставлен за {price}₽!"})

@app.route('/api/inventory_action', methods=['POST'])
def api_inventory_action():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN): 
        return jsonify({"error": "Auth failed"}), 403
        
    user_info = json.loads(dict(qc.split("=", 1) for qc in unquote(data.get('initData')).split("&"))['user'])
    uid = user_info['id']
    first_name = user_info.get('first_name', 'Аноним')
    
    action = data.get('action')
    
    if action == 'cancel_lot':
        lot_id = data.get('lot_id')
        from bson.objectid import ObjectId
        lot = db['market_orders'].find_one_and_update(
            {"_id": ObjectId(lot_id), "seller_uid": uid, "status": "active"},
            {"$set": {"status": "cancelled"}}
        )
        if not lot: return jsonify({"error": "Лот не найден или уже продан!"}), 400
        db['promocodes'].update_one({"_id": lot['promo_id']}, {"$set": {"owner_uid": uid}})
        return jsonify({"success": True, "msg": "✅ Лот снят с продажи. Артефакт возвращен в рюкзак."})
        
    elif action == 'pawn_promo':
        promo_id = data.get('promo_id')
        promo = db['promocodes'].find_one_and_delete({"_id": promo_id, "owner_uid": uid, "is_active": True})
        if not promo: return jsonify({"error": "Промокод не найден!"}), 400
        
        shards_reward = 10 if promo.get("target") == "vip" else 5
        paid_collection.update_one({"uid": uid}, {"$inc": {"jackpot_shards": shards_reward}})
        return jsonify({"success": True, "msg": f"♻️ Артефакт уничтожен!\nВы получили: +{shards_reward} Осколков рулетки 🧩."})
        
    elif action == 'angel':
        target_uid = data.get('target_id')
        if not target_uid.isdigit(): return jsonify({"error": "ID должен быть числом!"}), 400
        target_uid = int(target_uid)
        
        user_data = paid_collection.find_one({"uid": uid}) or {}
        if user_data.get("immunity", 0) < 1: return jsonify({"error": "Нет активных Щитов!"}), 400
        
        paid_collection.update_one({"uid": uid}, {"$inc": {"immunity": -1}})
        paid_collection.update_one({"uid": target_uid}, {"$set": {"strikes": 0, "status": 0}, "$unset": {"topic_type": ""}})
        
        import time
        db['skynet_tasks'].insert_one({"uid": target_uid, "action": "full_unban", "timestamp": time.time()})
        return jsonify({"success": True, "msg": f"👼 Чудо свершилось!\nВы пожертвовали щит. Юзер {target_uid} спасен!"})
        
    elif action == 'arrest':
        target_info = data.get('target_info')
        if not target_info or len(target_info) < 3: return jsonify({"error": "Укажите цель и причину!"}), 400
        
        promo = db['promocodes'].find_one({"owner_uid": uid, "type": "artifact", "target": "mute", "is_active": True, "used_count": 0})
        if not promo: return jsonify({"error": "У вас нет Ордеров!"}), 400
        
        code = promo["_id"]
        db['promocodes'].update_one({"_id": code}, {"$inc": {"used_count": 1}})
        
        from core.bot import bot
        from config import STAFF_GROUP_ID, PRIZES_THREAD_ID
        from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
        import html
        
        markup = InlineKeyboardMarkup().add(
            InlineKeyboardButton("✅ Замутить", callback_data=f"arrest_done_{uid}"),
            InlineKeyboardButton("❌ Отклонить (Вернуть ордер)", callback_data=f"arrest_rej_{code}_{uid}")
        )
        try:
            bot.send_message(
                STAFF_GROUP_ID, 
                f"🚓 <b>ПРИМЕНЕНИЕ ОРДЕРА (WEB APP)</b>\n\n👤 От: {first_name} (<code>{uid}</code>)\n🔑 Код: <code>{code}</code>\n🎯 Цель и причина:\n<code>{html.escape(target_info)}</code>", 
                parse_mode="HTML", reply_markup=markup, message_thread_id=PRIZES_THREAD_ID
            )
        except Exception as e: logger.error(f"Ошибка ордера: {e}")
        return jsonify({"success": True, "msg": "🚓 Заявка на арест передана Спецназу Скайнета!"})

# ================= 🚜 КИБЕР-ФЕРМА: БЭКЕНД =================

CROPS = {
    "radish": {"name": "🧅 Редис", "cost_pts": 15, "grow_time": 4*3600, "water_req": False, "reward_pts": [20, 30], "shards_chance": 0},
    "mizuna": {"name": "🥬 Мизуна", "cost_pts": 35, "grow_time": 6*3600, "water_req": False, "reward_pts": [45, 60], "shards_chance": 10},
    "tomato": {"name": "🍅 Помидоры", "cost_pts": 80, "grow_time": 12*3600, "water_req": False, "reward_pts": [100, 150], "key": None},
    "sunflower": {"name": "🌻 Подсолнух", "cost_pts": 150, "grow_time": 24*3600, "water_req": True, "reward_pts": [180, 220], "key": "blue", "key_chance": 25},
    "watermelon": {"name": "🍉 Арбуз", "cost_pts": 300, "grow_time": 48*3600, "water_req": True, "reward_pts": [400, 500], "key": "red", "key_chance": 20},
    "chestnut": {"name": "🌳 К. Каштан", "cost_pts": 1000, "grow_time": 7*24*3600, "water_req": True, "reward_pts": [0, 0], "is_decor": True},
    "rhododendron": {"name": "🌸 Рододендрон", "cost_pts": 1500, "grow_time": 3*24*3600, "water_req": True, "reward_pts": [0, 0], "is_decor": True},
    
    # 🔥 НОВЫЕ СЕМЕНА 🔥
    "parsley": {"name": "🌿 Петрушка", "cost_pts": 100, "grow_time": 8*3600, "water_req": False, "reward_pts": [50, 80]},
    "cactus": {"name": "🌵 Кактус", "cost_pts": 800, "grow_time": 5*24*3600, "water_req": False, "reward_pts": [0, 0], "is_decor": True},
    "amanita": {"name": "🍄 К-Мухомор", "cost_pts": 300, "grow_time": 2*3600, "water_req": True, "reward_pts": [0, 0]},
    "money_tree": {"name": "🌳 Ден. Дерево", "cost_pts": 5000, "grow_time": 5*24*3600, "water_req": True, "reward_rub": [10, 30]}
}

@bot.message_handler(commands=['check_gw'])
def force_check_gw(message):
    from config import STAFF_GROUP_ID, OWNER_ID
    if str(message.chat.id) != str(STAFF_GROUP_ID) and message.from_user.id != OWNER_ID:
        return
        
    from core.scheduler import check_giveaways_task
    bot.reply_to(message, "⏳ Принудительно сканирую базу розыгрышей...")
    try:
        check_giveaways_task()
        bot.reply_to(message, "✅ Сканирование завершено! Если чье-то время вышло, победитель уже определен и опубликован в чате!")
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка при сканировании: {e}")

@app.route('/api/get_farm', methods=['POST'])
def api_get_farm():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN): 
        return jsonify({"error": "Auth failed"}), 403
        
    uid = json.loads(dict(qc.split("=", 1) for qc in unquote(data.get('initData')).split("&"))['user'])['id']
    
    plots = list(db['farm_plots'].find({"uid": uid}).sort("slot_id", 1))
    
    if len(plots) == 0:
        for i in range(1, 5):
            db['farm_plots'].insert_one({"uid": uid, "slot_id": i, "status": "empty"})
        plots = list(db['farm_plots'].find({"uid": uid}).sort("slot_id", 1))
        
    import time
    now = int(time.time())
    result = []
    
    for plot in plots:
        status = plot.get("status")
        seed_type = plot.get("seed_type")
        
        if status == "growing" and seed_type in CROPS:
            crop = CROPS[seed_type]
            planted_at = plot.get("planted_at", now)
            last_watered = plot.get("last_watered", now)
            
            if crop["water_req"] and (now - last_watered > 86400):
                db['farm_plots'].update_one({"_id": plot["_id"]}, {"$set": {"status": "withered"}})
                status = "withered"
            elif now >= planted_at + crop["grow_time"]:
                db['farm_plots'].update_one({"_id": plot["_id"]}, {"$set": {"status": "ready"}})
                status = "ready"
                
        result.append({
            "slot_id": plot["slot_id"],
            "status": status,
            "seed_type": seed_type,
            "name": CROPS[seed_type]["name"] if seed_type in CROPS else "",
            "planted_at": plot.get("planted_at"),
            "grow_time": CROPS[seed_type]["grow_time"] if seed_type in CROPS else 0,
            "water_req": CROPS[seed_type]["water_req"] if seed_type in CROPS else False,
            "last_watered": plot.get("last_watered"),
            "fertilized": plot.get("fertilized", False),
            "pest": plot.get("pest")
        })
        
    user_db = paid_collection.find_one({"uid": uid}) or {}
    keys = {
        "blue": user_db.get("key_blue", 0),
        "red": user_db.get("key_red", 0)
    }
        
    return jsonify({"plots": result, "keys": keys})

@app.route('/api/farm_action', methods=['POST'])
def api_farm_action():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN): 
        return jsonify({"error": "Auth failed"}), 403
        
    uid = json.loads(dict(qc.split("=", 1) for qc in unquote(data.get('initData')).split("&"))['user'])['id']
    action = data.get('action') 
    slot_id = int(data.get('slot_id', 0))
    
    import time
    now = int(time.time())
    
    # === ПОКУПКА СЛОТА (ТРАКТОР) ===
    if action == 'buy_slot':
        cost = 1000 
        current_slots = db['farm_plots'].count_documents({"uid": uid})
        if current_slots >= 8: 
            return jsonify({"error": "У вас уже максимальное число грядок (8)!"}), 400
        
        user_db = paid_collection.find_one_and_update(
            {"uid": uid, "bounty_points": {"$gte": cost}},
            {"$inc": {"bounty_points": -cost}}
        )
        if not user_db: 
            return jsonify({"error": "Недостаточно очков для аренды трактора (нужно 1000 💎)!"}), 400
        
        db['farm_plots'].insert_one({"uid": uid, "slot_id": current_slots + 1, "status": "empty"})
        return jsonify({"success": True, "msg": f"🚜 Трактор расчистил слот #{current_slots + 1}!"})

    # Для остальных действий нам нужна конкретная грядка
    plot = db['farm_plots'].find_one({"uid": uid, "slot_id": slot_id})
    # Защита от действий во время заражения
    if action in ['water', 'fertilize', 'harvest']:
        if plot.get('pest'):
            return jsonify({"error": f"Сначала прогоните вредителя ({plot['pest']['emoji']})!"}), 400
    if not plot: return jsonify({"error": "Грядка не найдена!"}), 400
    
    # === ПОСАДКА ===
    if action == 'plant':
        seed_type = data.get('seed_type')
        if seed_type not in CROPS: return jsonify({"error": "Таких семян нет!"}), 400
        if plot['status'] != 'empty': return jsonify({"error": "Этот слот уже занят!"}), 400
        
        cost = CROPS[seed_type]['cost_pts']
        user_db = paid_collection.find_one_and_update(
            {"uid": uid, "bounty_points": {"$gte": cost}},
            {"$inc": {"bounty_points": -cost}}
        )
        if not user_db: return jsonify({"error": "Недостаточно очков!"}), 400
        
        db['farm_plots'].update_one({"_id": plot["_id"]}, {
            "$set": {"status": "growing", "seed_type": seed_type, "planted_at": now, "last_watered": now, "fertilized": False}
        })
        return jsonify({"success": True, "msg": f"🌱 Вы посадили {CROPS[seed_type]['name']}!"})
        

    # === ПРОГНАТЬ ВРЕДИТЕЛЯ ===
    elif action == 'chase_pest':
        if not plot.get('pest'): return jsonify({"error": "На грядке никого нет!"}), 400
        pest_emoji = plot['pest']['emoji']
        # Прогоняем гада и откатываем время посадки вперед (компенсируем время простоя)
        db['farm_plots'].update_one({"_id": plot["_id"]}, {"$unset": {"pest": ""}})
        return jsonify({"success": True, "msg": f"👞 Вы успешно прогнали гада ({pest_emoji})! Растение снова в безопасности."})

    # === ПОЛИВ ===
    elif action == 'water':
        if plot['status'] != 'growing': return jsonify({"error": "Нечего поливать!"}), 400

        # 🔥 ИСПРАВЛЕНИЕ: Добавили импорт datetime 🔥
        import datetime
        today_str = datetime.datetime.now().strftime("%Y-%m-%d")
        
        # 🔥 ТРЕКЕР ДЛЯ СЕРЕБРЯНОГО КЕЙСА (ПОЛИВ) 🔥
        db['tasks_progress'].update_one({"uid": uid, "date": today_str}, {"$set": {"watered": True}}, upsert=True)
        
        # 🔥 Защита от бесконечного полива (Кулдаун 4 часа)
        last_watered = plot.get('last_watered', 0)
        time_passed = now - last_watered
        cooldown = 4 * 3600 # 4 часа в секундах
        
        if time_passed < cooldown:
            left_mins = int((cooldown - time_passed) / 60)
            return jsonify({"error": f"Грядка еще влажная! Возвращайтесь через {left_mins} мин."}), 400
            
        db['farm_plots'].update_one({"_id": plot["_id"]}, {"$set": {"last_watered": now}})
        return jsonify({"success": True, "msg": "💧 Растение успешно полито. Таймер засухи сброшен!"})
        
    # === ОЧИСТКА ЗАСОХШЕГО ===
    elif action == 'clear':
        if plot['status'] != 'withered': return jsonify({"error": "Грядка еще жива!"}), 400
        db['farm_plots'].update_one({"_id": plot["_id"]}, {"$set": {"status": "empty", "seed_type": None, "fertilized": False}})
        return jsonify({"success": True, "msg": "🥀 Засохший куст убран. Слот свободен."})
        
    # === ВЫКОПКА ЖИВОГО РАСТЕНИЯ ===
    elif action == 'dig_up':
        if plot['status'] == 'empty': return jsonify({"error": "Грядка и так пуста!"}), 400
        db['farm_plots'].update_one({"_id": plot["_id"]}, {"$set": {"status": "empty", "seed_type": None, "planted_at": None, "last_watered": None, "fertilized": False}})
        return jsonify({"success": True, "msg": "⛏ Вы вырвали растение с корнем. Грядка очищена!"})
        
    # === СБОР УРОЖАЯ ===
    elif action == 'harvest':
        if plot['status'] != 'ready': return jsonify({"error": "Урожай еще не созрел!"}), 400
        crop = CROPS.get(plot['seed_type'])
        
        if crop.get('is_decor'): return jsonify({"error": "Декор нельзя собрать, он дает пассивный бонус!"}), 400
        
        import random
        update_query = {"$inc": {}}
        msg = "🚜 Урожай собран!"
        
        # 🔥 СПЕЦ-ЛОГИКА: КИБЕР-МУХОМОР (Казино)
        if plot['seed_type'] == 'amanita':
            if random.randint(1, 100) <= 50:
                update_query["$inc"]["bounty_points"] = 1000
                msg = "🎰 ДЖЕКПОТ!\nКибер-Мухомор выдал 1000 💎!"
            else:
                update_query["$inc"]["bounty_points"] = 0 # Пустышка
                msg = "🍄 Отравленная земля...\nМухомор сгнил, вы потеряли вложения."
                
        # 🔥 СПЕЦ-ЛОГИКА: ДЕНЕЖНОЕ ДЕРЕВО (Многоразовое) 🔥
        elif plot['seed_type'] == 'money_tree':
            reward_rub = random.randint(crop['reward_rub'][0], crop['reward_rub'][1])
            paid_collection.update_one({"uid": uid}, {"$inc": {"cashback_balance": reward_rub}})
            
            # Магия цикличного созревания (Откатываем таймер, чтобы осталось 24 часа)
            new_planted_at = now - (crop['grow_time'] - 86400) 
            db['farm_plots'].update_one({"_id": plot["_id"]}, {
                "$set": {
                    "status": "growing", 
                    "planted_at": new_planted_at,
                    "last_watered": now,
                    "fertilized": False # <--- Сброс удобрения для следующего урожая
                }
            })
            return jsonify({"success": True, "msg": f"🌳 Вы стрясли с дерева {reward_rub} ₽!\nДерево сбросило плоды. Следующий урожай будет готов через 24 часа. Не забывайте поливать!"})
                
        # СТАНДАРТНЫЙ УРОЖАЙ (ВКЛЮЧАЯ ПЕТРУШКУ)
        else:
            reward_pts = random.randint(crop['reward_pts'][0], crop['reward_pts'][1])
            update_query["$inc"]["bounty_points"] = reward_pts
            msg += f"\nВы получили {reward_pts} 💎."
            
            # 🔥 СПЕЦ-ЛОГИКА: ПЕТРУШКА (Инвентарь)
            if plot['seed_type'] == 'parsley':
                chance = random.randint(1, 100)
                if chance <= 10:
                    update_query["$inc"]["immunity"] = 1
                    msg += "\n🛡 В кустах найден Щит Иммунитета!"
                elif chance <= 20:
                    code = f"ARREST-{random.randint(100, 999)}"
                    db['promocodes'].insert_one({"_id": code, "type": "artifact", "value": 0, "target": "mute", "usage_limit": 1, "used_count": 0, "is_active": True, "owner_uid": uid})
                    msg += f"\n🚓 В кустах найден Ордер на Арест!\nКод: {code}"

        # Шансы на Осколки и Ключи
        if crop.get('shards_chance') and random.randint(1, 100) <= crop['shards_chance']:
            update_query["$inc"]["jackpot_shards"] = 1
            msg += "\n🧩 Найден Осколок рулетки!"
            
        key_type = crop.get('key')
        if key_type and random.randint(1, 100) <= crop['key_chance']:
            update_query["$inc"][f"key_{key_type}"] = 1
            key_name = "Синий 🗄" if key_type == "blue" else "Красный 🏦"
            msg += f"\n\n🔑 УРА! ВЫ НАШЛИ {key_name} КЛЮЧ ОТ СЕЙФА!"
            
        paid_collection.update_one({"uid": uid}, update_query)
        db['farm_plots'].update_one({"_id": plot["_id"]}, {"$set": {"status": "empty", "seed_type": None, "fertilized": False}})
        
        return jsonify({"success": True, "msg": msg})

    # === УДОБРЕНИЕ ===
    elif action == 'fertilize':
        cost = 50 
        if plot['status'] != 'growing': 
            return jsonify({"error": "Удобрение работает только на растущие культуры!"}), 400
            
        if plot.get('fertilized'):
            return jsonify({"error": "Это растение уже удобрено! Максимум 1 раз."}), 400
        
        user_db = paid_collection.find_one_and_update(
            {"uid": uid, "bounty_points": {"$gte": cost}},
            {"$inc": {"bounty_points": -cost}}
        )
        if not user_db: 
            return jsonify({"error": "Недостаточно очков для покупки нано-удобрения (50 💎)!"}), 400
        
        crop = CROPS.get(plot['seed_type'])
        time_boost = crop['grow_time'] // 2
        db['farm_plots'].update_one({"_id": plot["_id"]}, {
            "$inc": {"planted_at": -time_boost},
            "$set": {"fertilized": True}
        })
        
        return jsonify({"success": True, "msg": "🧪 Удобрение применено!\nВремя созревания сокращено в 2 раза (использован лимит)."})

# ================= 🗄 КИБЕР-СЕЙФЫ: БЭКЕНД =================

@app.route('/api/get_safes', methods=['POST'])
def api_get_safes():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN): return jsonify({"error": "Auth failed"}), 403
    uid = json.loads(dict(qc.split("=", 1) for qc in unquote(data.get('initData')).split("&"))['user'])['id']
    
    user_db = paid_collection.find_one({"uid": uid}) or {}
    keys = {"blue": user_db.get("key_blue", 0), "red": user_db.get("key_red", 0)}
    
    import random
    blue_safe = db['safes_state'].find_one({"_id": "safe_blue"})
    if not blue_safe:
        new_pin = "".join([str(random.randint(0, 9)) for _ in range(3)])
        blue_safe = {"_id": "safe_blue", "pin_code": new_pin, "balance": 15000, "logs": []}
        db['safes_state'].insert_one(blue_safe)
        
    red_safe = db['safes_state'].find_one({"_id": "safe_red"})
    if not red_safe:
        new_pin = "".join([str(random.randint(0, 9)) for _ in range(4)])
        red_safe = {"_id": "safe_red", "pin_code": new_pin, "balance": 500, "logs": []}
        db['safes_state'].insert_one(red_safe)

    return jsonify({
        "keys": keys,
        "blue_bal": blue_safe.get("balance", 0),
        "blue_logs": blue_safe.get("logs", []),
        "red_bal": red_safe.get("balance", 0),
        "red_logs": red_safe.get("logs", [])
    })

@app.route('/api/crack_safe', methods=['POST'])
def api_crack_safe():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN): 
        return jsonify({"error": "Auth failed"}), 403
    
    user_info = json.loads(dict(qc.split("=", 1) for qc in unquote(data.get('initData')).split("&"))['user'])
    uid = user_info['id']
    username = user_info.get('username')
    user_name_str = f"@{username}" if username else user_info.get('first_name', 'Аноним')
    
    safe_color = data.get('color') 
    guess_pin = data.get('pin')
    
    user_db = paid_collection.find_one({"uid": uid}) or {}
    key_field = f"key_{safe_color}"
    
    if user_db.get(key_field, 0) < 1:
        key_name = "Синий" if safe_color == 'blue' else "Красный"
        return jsonify({"error": f"Вам нужен {key_name} ключ! Вырастите его на грядке."}), 400

    # 🔥 ПАССИВКА: КОНСКИЙ КАШТАН И ИДЕАЛЬНОЕ ВРЕМЯ 🔥
    import datetime
    # Сдвиг +5 часов для Екатеринбурга (работает всегда и без сторонних библиотек!)
    tz_ekb = datetime.timezone(datetime.timedelta(hours=5))
    today_str = datetime.datetime.now(tz_ekb).strftime("%Y-%m-%d")
    
    current_cracks = user_db.get("daily_cracks", 0)
    is_new_day = (user_db.get("last_crack_date") != today_str)
    
    # Сброс лимитов на новый день в памяти
    if is_new_day:
        current_cracks = 0
        
    chestnuts_count = db['farm_plots'].count_documents({"uid": uid, "seed_type": "chestnut", "status": "ready"})
    max_cracks = 3 + chestnuts_count
    
    if current_cracks >= max_cracks:
        return jsonify({"error": f"Лимит взломов на сегодня исчерпан ({current_cracks}/{max_cracks})!\nПриходите завтра или посадите больше Каштанов."}), 400
        
    # 🔥 ЖЕСТКИЙ СБРОС ЛИМИТОВ В БАЗЕ (Больше не зациклится!) 🔥
    if is_new_day:
        paid_collection.update_one(
            {"uid": uid}, 
            {"$inc": {key_field: -1}, "$set": {"daily_cracks": 1, "last_crack_date": today_str}}
        )
    else:
        paid_collection.update_one(
            {"uid": uid}, 
            {"$inc": {key_field: -1, "daily_cracks": 1}, "$set": {"last_crack_date": today_str}}
        )
    
    safe_id = f"safe_{safe_color}"
    safe = db['safes_state'].find_one({"_id": safe_id})
    real_pin = safe['pin_code']
    prize = safe['balance']
    
    if guess_pin == real_pin:
        if safe_color == 'blue':
            paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": prize}})
            currency = "💎"
        else:
            paid_collection.update_one({"uid": uid}, {"$inc": {"cashback_balance": prize}})
            currency = "₽"
            
        import random
        pin_len = 3 if safe_color == 'blue' else 4
        new_pin = "".join([str(random.randint(0, 9)) for _ in range(pin_len)])
        start_balance = 10000 if safe_color == 'blue' else 500
        
        db['safes_state'].update_one({"_id": safe_id}, {
            "$set": {"pin_code": new_pin, "balance": start_balance, "logs": []}
        })
        
        try:
            from core.bot import bot
            from config import STAFF_GROUP_ID, PRIZES_THREAD_ID
            safe_name = "СЕЙФА ДАННЫХ (Очки)" if safe_color == 'blue' else "ФИНАНСОВОГО СЕЙФА (Рубли)"
            bot.send_message(STAFF_GROUP_ID, f"🚨 <b>СИСТЕМА ВЗЛОМАНА!</b>\n\nХакер {user_name_str} подобрал пароль от {safe_name} и унес куш в размере <b>{prize} {currency}</b>!", parse_mode="HTML", message_thread_id=PRIZES_THREAD_ID)
        except: pass
        
        return jsonify({"success": True, "msg": f"ПОЛНЫЙ ДОСТУП!\nВы сорвали куш: {prize} {currency}!", "cracked": True})
        
    else:
        exact_matches = sum(1 for a, b in zip(guess_pin, real_pin) if a == b)
        result_text = f"Точных совпадений: {exact_matches}"
        
        new_log = {"name": user_name_str, "guess": guess_pin, "result": result_text}
        db['safes_state'].update_one(
            {"_id": safe_id}, 
            {"$push": {"logs": {"$each": [new_log], "$slice": -6}}} 
        )
        
        return jsonify({"success": True, "msg": f"Доступ запрещен!\n{result_text}\nКлюч сожжен. Попыток сегодня: {current_cracks + 1}/{max_cracks}", "cracked": False})

@app.route('/api/open_agent_case', methods=['POST'])
def api_open_agent_case():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN): 
        return jsonify({"error": "Auth failed"}), 403
    
    parsed_data = dict(qc.split("=", 1) for qc in unquote(data.get('initData')).split("&"))
    user_info = json.loads(parsed_data['user'])
    uid = user_info['id']
    
    user_db = paid_collection.find_one({"uid": uid}) or {}
    cases_count = user_db.get("agent_cases", 0)
    
    if cases_count < 1:
        return jsonify({"error": "У вас нет Кейсов Агента! Приглашайте друзей по ссылке."}), 400
        
    # Списываем 1 кейс сразу, чтобы не накрутили
    paid_collection.update_one({"uid": uid}, {"$inc": {"agent_cases": -1}})
    
    # 🎲 ЛУТ-ТАБЛИЦА (Наши утвержденные шансы)
    # Формат: (ID_приза, Название, Вес/Шанс)
    loot_table = [
        ("points_25", "25 💎 Очков", 40.0),
        ("points_50", "50 💎 Очков", 30.0),
        ("points_100", "100 💎 Очков", 15.0),
        ("shield_1", "🛡 Щит Иммунитета", 7.0),
        ("shards_5", "🧩 5 Осколков", 3.0),
        ("points_500", "500 💎 Очков", 4.0),
        ("vip_status", "👑 VIP-Статус", 0.5),
        ("cash_500", "💸 500 Рублей", 0.5)
    ]
    
    # Разделяем таблицу для random.choices
    prizes = [item for item in loot_table]
    weights = [item[2] for item in loot_table]
    
    # Бросаем кубик!
    won_prize = random.choices(prizes, weights=weights, k=1)[0]
    prize_id = won_prize[0]
    prize_name = won_prize[1]
    
    # 🎁 ВЫДАЧА ПРИЗА В БАЗУ
    if prize_id.startswith("points_"):
        amount = int(prize_id.split("_")[1])
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": amount}})
    elif prize_id == "shield_1":
        paid_collection.update_one({"uid": uid}, {"$inc": {"shields": 1}})
    elif prize_id == "shards_5":
        paid_collection.update_one({"uid": uid}, {"$inc": {"shards": 5}})
    elif prize_id == "cash_500":
        paid_collection.update_one({"uid": uid}, {"$inc": {"cashback_balance": 500}})
    elif prize_id == "vip_status":
        paid_collection.update_one({"uid": uid}, {"$set": {"vip_status": True}})
        
    # Если выпал джекпот — трубим админам
    if prize_id in ["vip_status", "cash_500", "points_500"]:
        try:
            from core.bot import bot
            from config import STAFF_GROUP_ID, PRIZES_THREAD_ID
            bot.send_message(STAFF_GROUP_ID, f"🎰 <b>ДЖЕКПОТ В КЕЙСАХ АГЕНТА!</b>\nПользователь `{uid}` выбил: <b>{prize_name}</b>!", parse_mode="HTML", message_thread_id=PRIZES_THREAD_ID)
        except: pass

    # Отправляем на фронтенд ТОЛЬКО ID выигранного приза.
    # Остальную магию (прокрутку, генерацию ленты и т.д.) будет делать JavaScript.
    return jsonify({
        "success": True, 
        "won_id": prize_id, 
        "won_name": prize_name,
        "cases_left": cases_count - 1
    })

# ================= АДМИН-ПАНЕЛЬ (ЦУП В WEB APP) =================

@app.route('/api/admin/generate_contest', methods=['POST'])
def api_admin_generate_contest():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN): 
        return jsonify({"error": "Auth failed"}), 403

    # Проверка на права админа
    parsed_data = dict(qc.split("=", 1) for qc in unquote(data.get('initData')).split("&"))
    uid = json.loads(parsed_data['user'])['id']
    from config import ADMIN_CHAT_IDS
    if uid not in ADMIN_CHAT_IDS:
        return jsonify({"error": "Доступ запрещен. Вы не администратор."}), 403

    theme = data.get('theme')
    if not theme: return jsonify({"error": "Укажите тему конкурса!"}), 400

    gemini_key = os.getenv("GEMINI_API_KEY")
    if not gemini_key: return jsonify({"error": "Ключ Gemini не найден!"}), 500

    system_prompt = """Ты креативный директор мужского Telegram-сообщества. 
    Твоя задача — придумать тематический фотоконкурс. 
    Верни СТРОГО валидный JSON без маркдауна и лишнего текста (без ```json).
    Формат ответа:
    {
      "contest_id": "уникальный_id_на_английском",
      "title": "Яркое название с эмодзи",
      "description": "Короткое описание",
      "announcement_text": "ПОЛНЫЙ текст поста-анонса для рассылки по чатам (в HTML тегах <b> и <i>). Обязательно пропиши тут правила: 1. Что нужно сфоткать. 2. Никаких чужих фото. 3. Для участия перейдите в личку бота и отправьте команду /contest.",
      "prizes": {
         "1": {"text": "5000 💎 + VIP + 1500 ₽"},
         "2": {"text": "3000 💎 + 3 Ордера на Арест"},
         "3": {"text": "1000 💎 + 2 Щита Иммунитета"}
      }
    }"""
    
    models_queue = ["gemini-3.7-flash", "gemini-3.6-flash"]
    ai_data = None
    
    import requests, time
    for model_name in models_queue:
        url = f"[https://generativelanguage.googleapis.com/v1beta/models/](https://generativelanguage.googleapis.com/v1beta/models/){model_name}:generateContent?key={gemini_key}"
        for attempt in range(2):
            try:
                payload = {
                    "systemInstruction": {"parts": [{"text": system_prompt}]},
                    "contents": [{"parts": [{"text": f"Сгенерируй конкурс на тему: {theme}"}]}],
                    "generationConfig": {"temperature": 0.8, "responseMimeType": "application/json"}
                }
                res = requests.post(url, headers={"Content-Type": "application/json"}, json=payload, timeout=20)
                if res.status_code == 200:
                    try:
                        ai_data = json.loads(res.json()["candidates"][0]["content"]["parts"][0]["text"])
                        break
                    except: pass
            except: time.sleep(2)
        if ai_data: break

    if not ai_data:
        return jsonify({"error": "Нейросеть не смогла сгенерировать ответ. Попробуйте еще раз."}), 500

    # Сохраняем черновик
    ai_data['status'] = 'draft'
    db['active_contest'].update_one({"_id": "current_event"}, {"$set": ai_data}, upsert=True)

    return jsonify(ai_data)


@app.route('/api/admin/deploy_contest', methods=['POST'])
def api_admin_deploy_contest():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN): 
        return jsonify({"error": "Auth failed"}), 403

    parsed_data = dict(qc.split("=", 1) for qc in unquote(data.get('initData')).split("&"))
    uid = json.loads(parsed_data['user'])['id']
    from config import ADMIN_CHAT_IDS
    if uid not in ADMIN_CHAT_IDS: return jsonify({"error": "Доступ запрещен"}), 403

    # Обновляем черновик теми данными, которые ты отредактировал ручками в Web App
    db['active_contest'].update_one({"_id": "current_event"}, {
        "$set": {
            "title": data.get("title"),
            "announcement_text": data.get("desc"),
            "prizes.1.text": data.get("prize1"),
            "prizes.2.text": data.get("prize2"),
            "prizes.3.text": data.get("prize3"),
            "status": "running"
        }
    })

    # Запускаем фоновую рассылку
    def broadcast():
        from config import chat_ids_mk, chat_ids_parni, chat_ids_ns, chat_ids_gayznak, chat_ids_rainbow, STAFF_GROUP_ID
        from core.bot import bot
        import time
        
        all_chats = list(chat_ids_mk.values()) + list(chat_ids_parni.values()) + list(chat_ids_ns.values()) + list(chat_ids_gayznak.values()) + list(chat_ids_rainbow.values())
        unique_chats = set(all_chats)
        
        prize_block = f"\n\n🎁 <b>ПРИЗОВОЙ ФОНД:</b>\n🥇 1 место: {data.get('prize1')}\n🥈 2 место: {data.get('prize2')}\n🥉 3 место: {data.get('prize3')}"
        announcement = data.get("desc") + prize_block
        
        success = 0
        for chat_id in unique_chats:
            try:
                bot.send_message(chat_id, announcement, parse_mode="HTML")
                success += 1
                time.sleep(0.3)
            except: pass
            
        try: bot.send_message(STAFF_GROUP_ID, f"📢 <b>Анонс конкурса успешно разослан в {success} чатов! (Запущено из ЦУП)</b>", parse_mode="HTML")
        except: pass

    import threading
    threading.Thread(target=broadcast, daemon=True).start()

    return jsonify({"success": True, "msg": "Конкурс запущен! Идет рассылка по чатам."})

# ================= АДМИН-ПАНЕЛЬ (ЦУП В WEB APP) =================

@app.route('/api/admin/user_action', methods=['POST'])
def api_admin_user_action():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN): return jsonify({"error": "Auth failed"}), 403

    parsed_data = dict(qc.split("=", 1) for qc in unquote(data.get('initData')).split("&"))
    admin_uid = json.loads(parsed_data['user'])['id']
    from config import ADMIN_CHAT_IDS
    if admin_uid not in ADMIN_CHAT_IDS: return jsonify({"error": "Доступ запрещен."}), 403

    target_uid = data.get('target_uid')
    action = data.get('action')
    value = data.get('value', '')
    
    if not target_uid or not target_uid.isdigit(): return jsonify({"error": "Некорректный ID."}), 400
    target_uid = int(target_uid)

    from core.bot import bot
    from utils.cryptobot import get_crypto_pay_url
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
    import time, datetime

    try:
        if action == "invoice":
            amount = int(value)
            # Тут старый код генерации меню (как в предыдущем сообщении)
            url_usdt = get_crypto_pay_url(f"fine_{target_uid}", amount, f"Оплата штрафа ({amount}⭐️)", asset="USDT")
            markup = InlineKeyboardMarkup(row_width=1).add(InlineKeyboardButton(f"💳 Оплатить {amount}⭐️", callback_data=f"checkout_pay_fine_{amount}"))
            bot.send_message(target_uid, f"🧾 **Вам выставлен счет на: {amount}⭐️**", reply_markup=markup, parse_mode="Markdown")
            return jsonify({"success": True, "msg": f"Счет на {amount}⭐️ отправлен!"})

        elif action == "give_points":
            paid_collection.update_one({"uid": target_uid}, {"$inc": {"bounty_points": int(value)}}, upsert=True)
            bot.send_message(target_uid, f"🎁 **Бонус!**\nНачислено: **{value} Очков**.", parse_mode="Markdown")
            return jsonify({"success": True, "msg": f"Выдано {value} очков."})

        elif action == "give_shards":
            paid_collection.update_one({"uid": target_uid}, {"$inc": {"jackpot_shards": int(value)}}, upsert=True)
            bot.send_message(target_uid, f"🧩 **Бонус!**\nНачислено: **{value} Осколков**.", parse_mode="Markdown")
            return jsonify({"success": True, "msg": f"Выдано {value} осколков."})

        elif action == "send_msg":
            bot.send_message(target_uid, f"🎁 **Сообщение от Администрации:**\n\n{value}", parse_mode="Markdown")
            return jsonify({"success": True, "msg": "Сообщение доставлено!"})
            
        elif action == "ban":
            db['banned'].insert_one({"_id": target_uid, "reason": "Бан из ЦУПа"})
            return jsonify({"success": True, "msg": "Пользователь забанен."})
            
        elif action == "unban":
            db['banned'].delete_one({"_id": target_uid})
            db['skynet_tasks'].insert_one({"uid": target_uid, "action": "full_unban", "timestamp": time.time()})
            return jsonify({"success": True, "msg": "Приказ на разбан передан."})
            
        elif action == "mute":
            hours = int(value)
            db['skynet_tasks'].insert_one({"uid": target_uid, "action": "global_mute", "duration": hours * 3600, "timestamp": time.time()})
            return jsonify({"success": True, "msg": f"Мут на {hours}ч. выдан."})
            
        elif action == "tag":
            tag = value[:15]
            db['users'].update_one({"_id": target_uid}, {"$set": {"custom_tag": tag}}, upsert=True)
            return jsonify({"success": True, "msg": f"Тег {tag} установлен."})
            
        # 🔥 ВОТ ЭТОТ БЛОК НОВЫЙ 🔥
        elif action == "city":
            city = value.strip()
            db['users'].update_one({"_id": target_uid}, {"$set": {"main_city": city}}, upsert=True)
            return jsonify({"success": True, "msg": f"Город {city} успешно установлен."})

    except Exception as e:
        return jsonify({"error": f"Ошибка API: {str(e)}"}), 500

@app.route('/api/admin/giveaway', methods=['POST'])
def api_admin_giveaway():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN): return jsonify({"error": "Auth failed"}), 403
    
    import datetime
    title = data.get('title')
    price = int(data.get('price'))
    hours = int(data.get('hours'))
    winners = int(data.get('winners'))
    
    end_date = datetime.datetime.now() + datetime.timedelta(hours=hours)
    gw_id = f"gw_{int(datetime.datetime.now().timestamp())}" 
    
    db['giveaways'].insert_one({
        "_id": gw_id, "title": title, "ticket_price": price, 
        "last_ticket_num": 0, "total_tickets": 0, 
        "winners_count": winners, "status": "active", "end_date": end_date
    })
    return jsonify({"success": True, "msg": f"Розыгрыш '{title}' успешно запущен!"})

@app.route('/api/admin/emission', methods=['POST'])
def api_admin_emission():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN): return jsonify({"error": "Auth failed"}), 403
    action = data.get('action')
    
    import random
    if action == "promo_vip":
        code = f"VIP-{random.randint(1000, 9999)}"
        db['promocodes'].insert_one({"_id": code, "type": "percent", "value": 100, "target": "vip", "usage_limit": 1, "used_count": 0, "is_active": True})
        return jsonify({"success": True, "msg": f"Код VIP: {code}"})
        
    elif action == "promo_ads":
        code = f"ADS-{random.randint(1000, 9999)}"
        db['promocodes'].insert_one({"_id": code, "type": "percent", "value": 100, "target": "ads", "usage_limit": 1, "used_count": 0, "is_active": True})
        return jsonify({"success": True, "msg": f"Код Реклама: {code}"})
        
    elif action == "airdrop":
        try:
            from handlers.casino import trigger_random_airdrop 
            trigger_random_airdrop(is_manual=True)
            return jsonify({"success": True, "msg": "Аирдроп успешно сброшен в чат!"})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

@app.route('/api/admin/stats', methods=['POST'])
def api_admin_stats():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN): return jsonify({"error": "Auth failed"}), 403
    stat_type = data.get('type')
    
    import datetime
    import time
    
    # === ГЛОБАЛЬНАЯ СВОДКА ===
    if stat_type == "global":
        total_users = db['users'].count_documents({})
        total_banned = db['banned'].count_documents({})
        
        now_time = time.time()
        webapp_total = db['users'].count_documents({"last_webapp_visit": {"$exists": True}})
        webapp_dau = db['users'].count_documents({"last_webapp_visit": {"$gt": now_time - 86400}})
        active_plots = db['farm_plots'].count_documents({"status": "growing"})
        
        pipeline = [{"$group": {"_id": None, "total_points": {"$sum": "$bounty_points"}, "total_cb": {"$sum": "$cashback_balance"}}}]
        wealth = list(paid_collection.aggregate(pipeline))
        total_points = wealth[0]["total_points"] if wealth else 0
        total_cb = wealth[0]["total_cb"] if wealth else 0
        
        active_promos = db['promocodes'].count_documents({"is_active": True, "used_count": 0})
        active_airdrops = db['active_airdrops'].count_documents({"claimed_count": {"$lt": 5}})
        
        text = (
            f"📊 ГЛОБАЛЬНАЯ СВОДКА\n\n"
            f"👥 Всего бот-юзеров: {total_users}\n"
            f"🚷 В глобальном бане: {total_banned}\n\n"
            f"📱 ИГРОВАЯ СТАТИСТИКА (WEB APP):\n"
            f"🎮 Всего игроков: {webapp_total}\n"
            f"🔥 Онлайн за 24 часа: {webapp_dau} чел.\n"
            f"🌱 Растущих грядок: {active_plots} шт.\n\n"
            f"💰 Очков на руках: {total_points} 💎\n"
            f"💸 Кэшбека на руках: {total_cb} ₽\n\n"
            f"🎟 Активных артефактов: {active_promos}\n"
            f"📦 Аирдропов в чатах: {active_airdrops}"
        )
        return jsonify({"text": text})

    # === БАНК КАЗИНО ===
    elif stat_type == "bank":
        bank_data = db['casino_bank'].find_one({"_id": "premium_fund"}) or {"balance": 0}
        users_with_cb = list(paid_collection.find({"cashback_balance": {"$gt": 0}}))
        total_cb = sum(u.get("cashback_balance", 0) for u in users_with_cb)
        text = (
            f"🏦 БАНК КАЗИНО\n\n"
            f"💎 Фонд Premium: {int(bank_data.get('balance', 0))} ⭐️\n"
            f"💸 На руках у юзеров (Кэшбэк): {total_cb} ₽\n\n"
            f"Фонд пополняется на 20% от всех покупок в боте."
        )
        return jsonify({"text": text})
        
    # === CPA СТАТИСТИКА ===
    elif stat_type == "cpa":
        total = db['cpa_traffic'].count_documents({})
        approved = db['cpa_traffic'].count_documents({"status": "approved"})
        hold = db['cpa_traffic'].count_documents({"status": "hold"})
        fraud = db['cpa_traffic'].count_documents({"status": "fraud"})
        
        pipeline = [
            {"$match": {"status": "approved"}},
            {"$group": {"_id": "$agent_id", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 3}
        ]
        top_agents = list(db['cpa_traffic'].aggregate(pipeline))
        
        text = (
            f"🔗 CPA СТАТИСТИКА (ПАРТНЕРКА)\n\n"
            f"👁 Всего заявок (кликов): {total}\n"
            f"⏳ На проверке (Холд): {hold}\n"
            f"🚫 Забраковано (Боты): {fraud}\n"
            f"✅ ОДОБРЕНО (Лиды): {approved}\n\n"
            f"🏆 ТОП-3 АГЕНТА:\n"
        )
        if top_agents:
            medals = ["🥇", "🥈", "🥉"]
            for i, agent in enumerate(top_agents):
                agent_id = agent["_id"]
                count = agent["count"]
                text += f"{medals[i]} ID {agent_id} — {count} лидов\n"
        else:
            text += "Пока нет одобренных лидов."
            
        return jsonify({"text": text})
        
    # === Z-ОТЧЕТ ===
    elif stat_type == "zreport":
        today_str = datetime.datetime.now().strftime("%d.%m.%Y")
        
        all_time_raw = list(db['daily_revenue'].aggregate([{"$group": {"_id": "$type", "total": {"$sum": "$amount"}}}]))
        today_raw = list(db['daily_revenue'].aggregate([{"$match": {"date": today_str}}, {"$group": {"_id": "$type", "total": {"$sum": "$amount"}}}]))
        
        all_dict = {str(item["_id"]).lower() if item["_id"] else "unknown": item["total"] for item in all_time_raw}
        today_dict = {str(item["_id"]).lower() if item["_id"] else "unknown": item["total"] for item in today_raw}
        
        def format_money(d, key): return d.get(key, 0)
        
        known_keys = ['fine', 'fine_partial', 'ads', 'vip', 'beyond', 'indulgence', 'support', 'points_shop', 'donation', 'refund', 'city_access', 'ads_rub_balance', 'ads_points', 'payout', 'market_fee']
        
        today_other = sum(today_dict.values()) - sum(today_dict.get(k, 0) for k in known_keys)
        all_other = sum(all_dict.values()) - sum(all_dict.get(k, 0) for k in known_keys)

        today_ads = format_money(today_dict, 'ads') + format_money(today_dict, 'ads_rub_balance') + format_money(today_dict, 'ads_points')
        all_ads = format_money(all_dict, 'ads') + format_money(all_dict, 'ads_rub_balance') + format_money(all_dict, 'ads_points')

        today_fine = format_money(today_dict, 'fine') + format_money(today_dict, 'fine_partial')
        all_fine = format_money(all_dict, 'fine') + format_money(all_dict, 'fine_partial')
        
        text = (
            f"🧾 Z-ОТЧЕТ ({today_str})\n\n"
            f"📅 СЕГОДНЯ:\n"
            f"💰 Штрафы: {today_fine} ⭐️\n"
            f"📢 Реклама: {today_ads} ⭐️\n"
            f"👑 VIP-доступ: {format_money(today_dict, 'vip')} ⭐️\n"
            f"🏳️‍🌈 BEYOND-чат: {format_money(today_dict, 'beyond')} ⭐️\n"
            f"📜 Индульгенции: {format_money(today_dict, 'indulgence')} ⭐️\n"
            f"🛒 Магазин очков: {format_money(today_dict, 'points_shop')} ⭐️\n"
            f"⚖️ Ком-я рынка: {format_money(today_dict, 'market_fee')} ⭐️\n"
            f"💸 Возвраты/Выплаты: {format_money(today_dict, 'refund') + format_money(today_dict, 'payout')} ⭐️\n"
        )
        if today_other != 0: text += f"📦 Прочее: {today_other} ⭐️\n"
        text += f"🟢 ИТОГО ЗА ДЕНЬ: {sum(today_dict.values())} ⭐️\n\n"
        
        text += (
            f"🌍 ЗА ВСЁ ВРЕМЯ:\n"
            f"💰 Штрафы: {all_fine} ⭐️\n"
            f"📢 Реклама: {all_ads} ⭐️\n"
            f"👑 VIP-доступ: {format_money(all_dict, 'vip')} ⭐️\n"
            f"🏳️‍🌈 BEYOND-чат: {format_money(all_dict, 'beyond')} ⭐️\n"
            f"📜 Индульгенции: {format_money(all_dict, 'indulgence')} ⭐️\n"
            f"🛒 Магазин очков: {format_money(all_dict, 'points_shop')} ⭐️\n"
            f"⚖️ Ком-я рынка: {format_money(all_dict, 'market_fee')} ⭐️\n"
            f"💸 Возвраты/Выплаты: {format_money(all_dict, 'refund') + format_money(all_dict, 'payout')} ⭐️\n"
        )
        if all_other != 0: text += f"📦 Прочее: {all_other} ⭐️\n"
        text += f"🏆 ОБЩАЯ КАССА: {sum(all_dict.values())} ⭐️"
        
        return jsonify({"text": text})

# === ДАТЧИК ПУЛЬСА СЕКРЕТАРЯ ===
def heartbeat_sec():
    from database.mongo import db
    while True:
        try:
            db['settings'].update_one({"_id": "bot_status"}, {"$set": {"sec_last_seen": time.time()}}, upsert=True)
        except: pass
        time.sleep(60)

threading.Thread(target=heartbeat_sec, daemon=True).start()

if not is_setup_done:
    threading.Thread(target=setup, daemon=True).start()
    is_setup_done = True
# ===============================

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=PORT)