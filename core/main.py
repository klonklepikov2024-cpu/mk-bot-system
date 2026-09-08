import os
import time
import telebot
import threading
import requests
from flask import Flask, request
import hashlib
import hmac
import json
import html # <--- ДОБАВЬ ВОТ ЭТУ СТРОЧКУ
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
import handlers.polls       # <--- ДОБАВИТЬ ЭТУ СТРОКУ СЮДА
import handlers.market  # <--- ДОБАВЬТЕ ЭТУ СТРОКУ
import handlers.start_menu # <--- ГЛАВНОЕ МЕНЮ ВСЕГДА В САМОМ НИЗУ!

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
        
        # Упрощённый вариант
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
    # 🔥 Бронебойный прием без проверки заголовков
    update = telebot.types.Update.de_json(request.stream.read().decode('utf-8'))
    bot.process_new_updates([update])
    return 'ok', 200

@app.route('/ping')
def ping():
    return "I am alive!", 200

# ================= WEB APP API =================

def validate_webapp_data(init_data, token):
    """Секретная функция проверки подписи от Telegram (Защита от хакеров)"""
    try:
        parsed_data = dict(qc.split("=") for qc in unquote(init_data).split("&"))
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
    # Flask будет искать файл webapp.html в папке templates/
    return render_template('webapp.html')

@app.route('/api/profile', methods=['POST'])
def get_profile():
    data = request.json
    init_data = data.get('initData')
    
    if not validate_webapp_data(init_data, BOT_TOKEN):
        return jsonify({"error": "Взлом жопы отклонен"}), 403

    # Вытаскиваем ID юзера
    parsed_data = dict(qc.split("=") for qc in unquote(init_data).split("&"))
    user_info = json.loads(parsed_data['user'])
    uid = user_info['id']
    
    # Берем баланс Скайнета
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

    parsed_data = dict(qc.split("=") for qc in unquote(data.get('initData')).split("&"))
    user_info = json.loads(parsed_data['user'])
    uid = user_info['id']
    username = user_info.get('first_name', 'Аноним')
    
    amount = int(data.get('amount', 1))
    giveaway_id = data.get('giveaway_id')
    
    # Ищем розыгрыш (он должен быть active)
    gw = db['giveaways'].find_one({"_id": giveaway_id, "status": "active"})
    if not gw: return jsonify({"error": "Розыгрыш окончен или не найден"}), 400
    
    total_cost = amount * gw['ticket_price']
    
    # 1. Списываем очки (АТОМАРНО)
    updated_user = paid_collection.find_one_and_update(
        {"uid": uid, "bounty_points": {"$gte": total_cost}},
        {"$inc": {"bounty_points": -total_cost}}
    )
    if not updated_user:
        return jsonify({"error": "Недостаточно очков!"}), 400
        
    # 2. Атомарно бронируем номера билетов в розыгрыше!
    updated_gw = db['giveaways'].find_one_and_update(
        {"_id": giveaway_id},
        {"$inc": {"last_ticket_num": amount, "total_tickets": amount}}
    )
    
    start_num = updated_gw.get('last_ticket_num', 0) + 1
    end_num = start_num + amount - 1
    
    # 3. Сохраняем выданные билеты в прозрачную таблицу
    import time
    db['tickets_history'].insert_one({
        "giveaway_id": giveaway_id,
        "uid": uid,
        "name": username,
        "amount": amount,
        "range": f"{start_num}-{end_num}", # Диапазон номеров!
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
    
    # Берем активные (сортировка по дате окончания, старые сверху)
    active_gws = list(db['giveaways'].find({"status": "active"}).sort("end_date", 1))
    # Берем завершенные (новые сверху, лимит 5 штук, чтобы не засорять экран)
    completed_gws = list(db['giveaways'].find({"status": "completed"}).sort("end_date", -1).limit(5))
    
    gws = active_gws + completed_gws
    
    result = []
    for gw in gws:
        time_left_str = ""
        if gw['status'] == 'active':
            time_left = gw['end_date'] - now
            days = time_left.days
            hours = time_left.seconds // 3600
            if days > 0: time_left_str = f"Осталось {days} д. {hours} ч."
            elif hours > 0: time_left_str = f"Осталось {hours} ч."
            else: time_left_str = "Скоро итоги!"
        else:
            time_left_str = "Завершен"
            
        # Ищем имя победителя, если он есть
        winner_name = "Нет участников"
        if gw.get('winner_uid'):
            win_tx = db['tickets_history'].find_one({"giveaway_id": gw["_id"], "uid": gw["winner_uid"]})
            if win_tx:
                winner_name = win_tx.get('name', f"ID {gw['winner_uid']}")
            else:
                winner_name = f"ID {gw['winner_uid']}"
        elif gw.get('winner') == "Нет участников":
            winner_name = "Никто не участвовал"

        result.append({
            "id": str(gw["_id"]),
            "title": gw["title"],
            "price": gw["ticket_price"],
            "total": gw.get("total_tickets", 0),
            "time_left": time_left_str,
            "status": gw["status"],
            "winner_name": winner_name,
            "winning_number": gw.get("winning_number")
        })
        
    return jsonify(result)

@app.route('/api/get_market', methods=['POST'])
def api_get_market():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN):
        return jsonify({"error": "Auth failed"}), 403

    # Достаем активные лоты (от новых к старым)
    lots = list(db['market_orders'].find({"status": "active"}).sort("created_at", -1))
    
    result = []
    for lot in lots:
        # Определяем красивое название типа скидки
        t_name = "Штраф" if lot.get('target') == 'fine' else "Рекламу" if lot.get('target') == 'ads' else "VIP" if lot.get('target') == 'vip' else "Любую услугу"
        val = f"{lot.get('value')}%" if lot.get('type') == 'percent' else f"{lot.get('value')}₽"
        
        result.append({
            "id": str(lot["_id"]),
            "seller": lot.get('seller_name', 'Аноним'),
            "seller_uid": lot.get('seller_uid'),
            "title": f"Скидка {val} на {t_name}",
            "price_rub": lot['price_rub'],
            "price_pts": int(lot['price_rub'] * 2.5) # Авто-конвертация рублей в очки
        })
        
    return jsonify(result)

@app.route('/api/buy_market', methods=['POST'])
def api_buy_market():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN):
        return jsonify({"error": "Auth failed"}), 403

    parsed_data = dict(qc.split("=") for qc in unquote(data.get('initData')).split("&"))
    user_info = json.loads(parsed_data['user'])
    uid = user_info['id']
    
    lot_id = data.get('lot_id')
    currency = data.get('currency') # "rub" или "pts"
    
    from bson.objectid import ObjectId
    
    user_db = paid_collection.find_one({"uid": uid}) or {}
    
    # Ищем лот (АТОМАРНАЯ блокировка от двойной покупки)
    lot = db['market_orders'].find_one({"_id": ObjectId(lot_id), "status": "active"})
    if not lot:
        return jsonify({"error": "Упс! Лот уже продан или снят с продажи!"}), 400
        
    if lot['seller_uid'] == uid:
        return jsonify({"error": "Вы не можете купить свой собственный лот!"}), 400
        
    price_rub = lot['price_rub']
    price_pts = int(price_rub * 2.5)
    
    # Списываем средства
    if currency == "rub":
        if user_db.get("cashback_balance", 0) < price_rub:
            return jsonify({"error": "Недостаточно рублей (кэшбэка)!"}), 400
        paid_collection.update_one({"uid": uid}, {"$inc": {"cashback_balance": -price_rub}})
    elif currency == "pts":
        if user_db.get("bounty_points", 0) < price_pts:
            return jsonify({"error": "Недостаточно очков!"}), 400
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": -price_pts}})
    else:
        return jsonify({"error": "Ошибка валюты!"}), 400
        
    # Бронируем лот
    db['market_orders'].update_one({"_id": ObjectId(lot_id)}, {"$set": {"status": "sold", "buyer_uid": uid}})
    
    # Начисляем продавцу рубли за вычетом 10% комиссии
    seller_profit = int(price_rub * 0.9)
    paid_collection.update_one({"uid": lot['seller_uid']}, {"$inc": {"cashback_balance": seller_profit}})
    
    # Выдаем промокод покупателю
    promo_id = lot['promo_id']
    db['promocodes'].update_one({"_id": promo_id}, {"$set": {"owner_uid": uid}})
    
    # Уведомляем продавца в ЛС телеграма (через бота)
    try:
        from core.bot import bot
        bot.send_message(lot['seller_uid'], f"💸 **НОВОСТИ С РЫНКА!**\n\nВаш лот `{promo_id}` был успешно продан!\nНа ваш счет зачислено: **{seller_profit}₽** (с учетом 10% комиссии).", parse_mode="Markdown")
    except: pass
    
    return jsonify({"success": True, "promo_id": promo_id})

@app.route('/api/spin_roulette', methods=['POST'])
def api_spin_roulette():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN):
        return jsonify({"error": "Auth failed"}), 403

    parsed_data = dict(qc.split("=") for qc in unquote(data.get('initData')).split("&"))
    user_info = json.loads(parsed_data['user'])
    uid = user_info['id']

    # Безопасное получение юзернейма
    username = user_info.get('username')
    username_str = f"@{username}" if username else f"ID {uid}"
    first_name = user_info.get('first_name', 'Аноним')

    SPIN_PRICE = 50
    updated_user = paid_collection.find_one_and_update(
        {"uid": uid, "bounty_points": {"$gte": SPIN_PRICE}},
        {"$inc": {"bounty_points": -SPIN_PRICE}}
    )

    if not updated_user:
        return jsonify({"error": "Недостаточно очков! Нужно 50 💎."}), 400

    import random
    val = random.randint(1, 64)
    prize_msg = ""
    
    bank_data = db['casino_bank'].find_one({"_id": "premium_fund"}) or {"balance": 0}
    premium_cost_stars = 1500

    if val == 63 and bank_data.get("balance", 0) >= premium_cost_stars:
        db['casino_bank'].update_one({"_id": "premium_fund"}, {"$inc": {"balance": -premium_cost_stars}})
        prize_msg = "🏆 ГЛАВНЫЙ СУПЕР-ПРИЗ!!!\nВы выиграли Telegram Premium (3 мес.)!\nЗаявка отправлена админам."
        
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
            # 🔥 ЖЕСТКО ВШИВАЕМ ПРАВИЛЬНЫЙ АДРЕС ЦУПА 🔥
            markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ Обработать в ЦУП", url="https://elite-poster-bot.onrender.com/glaz"))
            bot.send_message(
                STAFF_GROUP_ID, 
                f"🏆 <b>СОРВАН ДЖЕКПОТ (TELEGRAM PREMIUM) ИЗ WEB APP!</b> 🏆\n\n"
                f"👤 Победитель: {first_name} ({username_str})\n\n"
                f"❗️ <i>Заявка добавлена в Веб-панель.</i>", 
                parse_mode="HTML", reply_markup=markup, message_thread_id=PRIZES_THREAD_ID
            )
        except: pass

    elif val == 63:
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": 1000, "jackpot_shards": 5}})
        prize_msg = "🎰 МИНИ-ДЖЕКПОТ!\nФонд Premium пуст, поэтому вы получаете +1000 Очков и 5 Осколков!"

    elif val == 64:
        code = f"JACKPOT-{random.randint(1000, 9999)}"
        db['promocodes'].insert_one({"_id": code, "type": "percent", "value": 100, "target": "vip", "usage_limit": 1, "used_count": 0, "is_active": True, "owner_uid": uid})
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": 300}})
        prize_msg = f"🚨 ДЖЕКПОТ 7️⃣7️⃣7️⃣!\nЗолотой Билет (VIP) и 300 очков!\nКод: {code}"

    elif val in [7, 21, 35]:
        prize_msg = "🌟 СУПЕР-РЕДКИЙ ДРОП!\nВы выиграли право установить Личный Тег!\nНажмите кнопку 'Рюкзак -> Ваши промокоды' или проверьте ЛС бота."
        try:
            from core.bot import bot
            from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
            markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✍️ Заказать свой тег", callback_data="claim_custom_tag"))
            bot.send_message(uid, "👑 Вы выиграли купон на создание Личного Статуса!", reply_markup=markup)
        except: pass

    elif val in [1, 22, 43]:
        paid_collection.update_one({"uid": uid}, {"$inc": {"immunity": 1, "bounty_points": 50}})
        prize_msg = "🔥 ЭПИЧЕСКИЙ ВЫИГРЫШ!\nВы получили 🛡 Щит Иммунитета и 50 очков!"

    elif val in [10, 20, 40, 50]:
        lost_points = int(updated_user.get("bounty_points", 0) * 0.3)
        if lost_points < 10: lost_points = 10
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": -lost_points}})
        prize_msg = f"💀 НАЛОГОВАЯ ПРОВЕРКА!\nСписано 30% баланса (-{lost_points} очков)."

    elif val in [5, 17, 29]:
        code = f"ARREST-{random.randint(100, 999)}"
        db['promocodes'].insert_one({"_id": code, "type": "artifact", "value": 0, "target": "mute", "usage_limit": 1, "used_count": 0, "is_active": True, "owner_uid": uid})
        prize_msg = f"🚓 СОЦИАЛЬНЫЙ АРТЕФАКТ!\nВы нашли Ордер на Арест!\nКод: {code}"

    elif val in [13, 26, 39, 52]:
        strikes = updated_user.get("strikes", 0)
        if strikes > 0:
            paid_collection.update_one({"uid": uid}, {"$inc": {"strikes": -1}})
            prize_msg = f"🕊 АМНИСТИЯ!\nСписан 1 штрафной страйк!"
        else:
            paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": 100}})
            prize_msg = "🕊 БЕЛЫЙ БИЛЕТ!\nУ вас нет страйков. Получите +100 очков!"

    elif val in [15, 30, 45, 60]:
        win_points = random.choice([100, 150, 250])
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": win_points}})
        prize_msg = f"💸 КРУПНЫЙ КУШ!\nВы выиграли {win_points} 💎!"

    elif val % 7 == 0:
        promos = [
            {"target": "fine", "value": 50, "prefix": "FINE50", "name": "50% на оплату Штрафа"},
            {"target": "ads", "value": 30, "prefix": "ADS30", "name": "30% на покупку Рекламы"},
            {"target": "vip", "value": 40, "prefix": "VIP40", "name": "40% на покупку VIP"},
            {"target": "all", "value": 15, "prefix": "ALL15", "name": "15% на Любую услугу"}
        ]
        drop = random.choice(promos)
        code = f"{drop['prefix']}-{random.randint(1000, 9999)}"
        db['promocodes'].insert_one({"_id": code, "type": "percent", "value": drop["value"], "target": drop["target"], "usage_limit": 1, "used_count": 0, "is_active": True, "owner_uid": uid})
        prize_msg = f"✨ РЕДКИЙ ДРОП!\nВыиграна скидка {drop['name']}!\nКод: {code}"

    elif val in [11, 33]:
        win_rub = random.choices([100, 250, 500], weights=[75, 20, 5], k=1)[0]
        cost_in_stars = win_rub // 2 
        if bank_data.get("balance", 0) >= cost_in_stars:
            db['casino_bank'].update_one({"_id": "premium_fund"}, {"$inc": {"balance": -cost_in_stars}})
            paid_collection.update_one({"uid": uid}, {"$inc": {"cashback_balance": win_rub}})
            prize_msg = f"✨ ДЕНЕЖНЫЙ КУПОН! ✨\nВы выиграли {win_rub} руб. на счет!"
        else:
            fallback_points = win_rub * 2
            paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": fallback_points}})
            prize_msg = f"💸 КРУПНЫЙ КУШ!\nВы выиграли {fallback_points} очков!"

    else:
        shards_won = random.choice([1, 1, 1, 2])
        paid_collection.update_one({"uid": uid}, {"$inc": {"jackpot_shards": shards_won}})
        prize_msg = f"🧩 Барабан остановился...\nВы получили: +{shards_won} Осколок(ка) джекпота!"

    return jsonify({"success": True, "message": prize_msg})

@app.route('/api/get_inventory', methods=['POST'])
def api_get_inventory():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN):
        return jsonify({"error": "Auth failed"}), 403

    parsed_data = dict(qc.split("=") for qc in unquote(data.get('initData')).split("&"))
    user_info = json.loads(parsed_data['user'])
    uid = user_info['id']
    
    user_data = paid_collection.find_one({"uid": uid}) or {}
    shields = user_data.get("immunity", 0)
    shards = user_data.get("jackpot_shards", 0)
    
    # Ищем артефакты и промокоды
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
    
    parsed_data = dict(qc.split("=") for qc in unquote(data.get('initData')).split("&"))
    user_info = json.loads(parsed_data['user'])
    uid = user_info['id']
    
    # Безопасное получение юзернейма
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
                # 🔥 ЖЕСТКО ВШИВАЕМ ПРАВИЛЬНЫЙ АДРЕС ЦУПА 🔥
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
    
    parsed_data = dict(qc.split("=") for qc in unquote(data.get('initData')).split("&"))
    uid = json.loads(parsed_data['user'])['id']
    
    PRICE = 1000
    user_data = paid_collection.find_one({"uid": uid}) or {}
    points = user_data.get("bounty_points", 0)
    
    if points < PRICE: return jsonify({"error": "Нужно 1000 очков!"}), 400
    
    paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": -PRICE}})
    remaining = points - PRICE
    
    import random
    chance = random.randint(1, 100)
    if chance <= 45:
        stolen = int(remaining * random.uniform(0.10, 0.25))
        if stolen > 0: paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": -stolen}})
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
    uid = json.loads(dict(qc.split("=") for qc in unquote(data.get('initData')).split("&"))['user'])['id']
    
    hold = db['cpa_traffic'].count_documents({"agent_id": uid, "status": "hold"})
    approved = db['cpa_traffic'].count_documents({"agent_id": uid, "status": "approved"})
    fraud = db['cpa_traffic'].count_documents({"agent_id": uid, "status": "fraud"})
    user_data = paid_collection.find_one({"uid": uid}) or {}
    dupes = user_data.get("cpa_duplicates", 0)
    
    return jsonify({"hold": hold, "approved": approved, "fraud": fraud, "duplicates": dupes})

@app.route('/api/exchange', methods=['POST'])
def api_exchange():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN): return jsonify({"error": "Auth failed"}), 403
    uid = json.loads(dict(qc.split("=") for qc in unquote(data.get('initData')).split("&"))['user'])['id']
    
    cost = data.get('cost')
    reward = data.get('reward')
    
    user_db = paid_collection.find_one_and_update(
        {"uid": uid, "cashback_balance": {"$gte": cost}},
        {"$inc": {"cashback_balance": -cost, "bounty_points": reward}}
    )
    if not user_db: return jsonify({"error": "Недостаточно рублей!"}), 400
    return jsonify({"success": True, "msg": f"✅ Успешно обменяли {cost}₽ на {reward}💎!"})

@app.route('/api/payout', methods=['POST'])
def api_payout():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN): return jsonify({"error": "Auth failed"}), 403
    user_info = json.loads(dict(qc.split("=") for qc in unquote(data.get('initData')).split("&"))['user'])
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
    
    # Списываем баланс
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
        
    parsed_data = dict(qc.split("=") for qc in unquote(data.get('initData')).split("&"))
    uid = json.loads(parsed_data['user'])['id']
    
    stars_amount = int(data.get('stars_amount', 50))
    points_reward = int(data.get('points_reward', 100))
    
    try:
        # Генерируем ссылку на оплату звездами (provider_token пустой для Stars)
        from core.bot import bot
        from telebot.types import LabeledPrice
        
        invoice_link = bot.create_invoice_link(
            title="Покупка Очков Бдительности",
            description=f"Пакет: {points_reward} Очков",
            payload=f"webapp_points_{uid}_{points_reward}", # Уникальный пейлоад для обработчика
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
    
    # Берем транзакции порциями по 15 штук
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
            
    # Если мы достали 15 транзакций, значит, скорее всего, есть еще
    next_offset = offset + 15 if len(tickets_history) == 15 else None
    
    return jsonify({"participants": result, "next_offset": next_offset})

@app.route('/api/get_my_promos', methods=['POST'])
def api_get_my_promos():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN): 
        return jsonify({"error": "Auth failed"}), 403
        
    uid = json.loads(dict(qc.split("=") for qc in unquote(data.get('initData')).split("&"))['user'])['id']
    
    # Ищем только неиспользованные купоны, которые не являются аирдропами
    promos = list(db['promocodes'].find({"owner_uid": uid, "is_active": True, "used_count": 0, "type": {"$ne": "airdrop"}}))
    
    result = []
    for p in promos:
        t_name = "Штраф" if p.get('target') == 'fine' else "Рекламу" if p.get('target') == 'ads' else "VIP" if p.get('target') == 'vip' else "Любую услугу"
        val = f"{p.get('value')}%" if p.get('type') == 'percent' else f"{p.get('value')}₽"
        result.append({
            "id": p["_id"], 
            "name": f"{p['_id']} (Скидка {val} на {t_name})", 
            "target": p.get("target", "all")
        })
        
    return jsonify(result)

@app.route('/api/add_market_lot', methods=['POST'])
def api_add_market_lot():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN): 
        return jsonify({"error": "Auth failed"}), 403
        
    user_info = json.loads(dict(qc.split("=") for qc in unquote(data.get('initData')).split("&"))['user'])
    uid = user_info['id']
    first_name = user_info.get('first_name', 'Аноним')
    
    promo_id = data.get('promo_id')
    price = int(data.get('price', 0))
    
    # Защита от подмены: проверяем, что промокод реально принадлежит юзеру
    promo = db['promocodes'].find_one({"_id": promo_id, "owner_uid": uid, "is_active": True, "used_count": 0})
    if not promo: 
        return jsonify({"error": "Артефакт не найден или уже продан!"}), 400
    
    # Вычисляем максимальную цену (120% от базы)
    prices_db = db['settings'].find_one({"_id": "prices"}) or {}
    target_type = promo.get("target", "all")
    base_price = 500
    if target_type == "vip": base_price = prices_db.get("vip_price_stars", 250) * 2
    elif target_type == "ads": base_price = prices_db.get("ads_price_stars", 150) * 2
    elif target_type == "fine": base_price = prices_db.get("fine_price_stars", 650) * 2
    
    max_price = int(base_price * 1.2)
    
    if price < 10 or price > max_price:
        return jsonify({"error": f"Цена должна быть от 10₽ до {max_price}₽!"}), 400
        
    # Атомарно забираем промокод и создаем лот на рынке
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
        
    user_info = json.loads(dict(qc.split("=") for qc in unquote(data.get('initData')).split("&"))['user'])
    uid = user_info['id']
    first_name = user_info.get('first_name', 'Аноним')
    
    action = data.get('action')
    
    # 1. СНЯТЬ С ПРОДАЖИ (Рынок)
    if action == 'cancel_lot':
        lot_id = data.get('lot_id')
        from bson.objectid import ObjectId
        lot = db['market_orders'].find_one_and_update(
            {"_id": ObjectId(lot_id), "seller_uid": uid, "status": "active"},
            {"$set": {"status": "cancelled"}}
        )
        if not lot: return jsonify({"error": "Лот не найден или уже продан!"}), 400
        # Возвращаем купон владельцу
        db['promocodes'].update_one({"_id": lot['promo_id']}, {"$set": {"owner_uid": uid}})
        return jsonify({"success": True, "msg": "✅ Лот снят с продажи. Артефакт возвращен в рюкзак."})
        
    # 2. ПЕРЕПЛАВКА ПРОМОКОДА (Ломбард)
    elif action == 'pawn_promo':
        promo_id = data.get('promo_id')
        promo = db['promocodes'].find_one_and_delete({"_id": promo_id, "owner_uid": uid, "is_active": True})
        if not promo: return jsonify({"error": "Промокод не найден!"}), 400
        
        shards_reward = 10 if promo.get("target") == "vip" else 5
        paid_collection.update_one({"uid": uid}, {"$inc": {"jackpot_shards": shards_reward}})
        return jsonify({"success": True, "msg": f"♻️ Артефакт уничтожен!\nВы получили: +{shards_reward} Осколков рулетки 🧩."})
        
    # 3. АНГЕЛ ХРАНИТЕЛЬ (Использование Щита)
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
        
    # 4. ОРДЕР НА АРЕСТ
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

# === ДАТЧИК ПУЛЬСА СЕКРЕТАРЯ ===
def heartbeat_sec():
    from database.mongo import db
    while True:
        try:
            db['settings'].update_one({"_id": "bot_status"}, {"$set": {"sec_last_seen": time.time()}}, upsert=True)
        except: pass
        time.sleep(60)

# Правильный запуск потока ПОСЛЕ функции
threading.Thread(target=heartbeat_sec, daemon=True).start()

# 🔥 ИСПРАВЛЕНИЕ: Запускаем setup в фоне ДО старта сервера, 
# чтобы она сработала на любом хостинге (Gunicorn/WSGI)
if not is_setup_done:
    threading.Thread(target=setup, daemon=True).start()
    is_setup_done = True
# ===============================

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=PORT)