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
from telebot.types import ChatPermissions
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

def mute_user(chat_id, user_id, seconds, reason=""):
    until = int(time.time()) + seconds
    perms = ChatPermissions(
        can_send_messages=False,
        can_send_audios=False,
        can_send_documents=False,
        can_send_photos=False,
        can_send_videos=False,
        can_send_video_notes=False,
        can_send_voice_notes=False,
        can_send_polls=False,
        can_send_other_messages=False,
        can_add_web_page_previews=False,
        can_change_info=False,
        can_invite_users=False,
        can_pin_messages=False,
        can_manage_topics=False
    )
    try:
        from core.bot import bot
        bot.restrict_chat_member(
            chat_id,
            user_id,
            until_date=until,
            permissions=perms
        )
        return True
    except Exception as e:
        from config import STAFF_GROUP_ID
        from core.bot import bot
        error_text = f"🔇 <b>СБОЙ ВЫДАЧИ МУТА</b>\nЧат: <code>{chat_id}</code>\nЮзер: <code>{user_id}</code>\nВремя: {seconds} сек.\nПричина: {reason}\n\n<b>Ответ Telegram:</b> <code>{str(e)}</code>"
        print(error_text)
        try:
            bot.send_message(STAFF_GROUP_ID, error_text, parse_mode="HTML")
        except:
            pass
        return False

# ================= WEB APP API =================

def validate_webapp_data(init_data, token):
    """Секретная функция проверки подписи от Telegram"""
    if not init_data: return False # <--- ДОБАВИЛИ ЗАЩИТУ
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
    db['users'].update_one({"_id": uid}, {"$set": {"last_webapp_visit": time.time()}}, upsert=True)
    
    user_db = paid_collection.find_one({"uid": uid}) or {}
    
    # --- СОБИРАЕМ ИНФУ О СЕМЬЕ ДЛЯ WEB APP ---
    partner_id = user_db.get("partner_id")
    partner_name = None
    family_balance = 0 # По умолчанию копилка пуста
    if partner_id:
        p_stat = db['chat_stats'].find_one({"uid": partner_id}) or {}
        partner_name = p_stat.get("name", f"ID {partner_id}")
        
        # Тянем баланс семьи из базы
        fam_id = f"family_{min(uid, partner_id)}_{max(uid, partner_id)}"
        fam_db = db['family_banks'].find_one({"_id": fam_id}) or {}
        family_balance = fam_db.get("balance", 0)
        
    children_count = len(user_db.get("children", []))
    
    return jsonify({
        "points": user_db.get("bounty_points", 0),
        "rubles": user_db.get("cashback_balance", 0),
        "karma": user_db.get("social_rating", 0),
        "partner": partner_name,
        "kids": children_count,
        "family_balance": family_balance, # 🔥 ДОБАВИЛИ ЭТУ СТРОЧКУ 🔥
        "golden_frame": user_db.get("golden_frame", False),
        "achievements": user_db.get("achievements", [])
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
    
    # 🔥 ПАТЧ: ПРОВЕРКА НА ТВИНКОВ В РОЗЫГРЫШАХ 🔥
    chat_stat = db['chat_stats'].find_one({"uid": uid, "msgs": {"$gte": 50}})
    
    # Достаем статусы VIP и BEYOND из ПРАВИЛЬНОЙ коллекции
    u_info = db['users'].find_one({"_id": uid}) or {}
    is_elite = u_info.get("is_vip") or u_info.get("is_queer")
    
    if not chat_stat and not is_elite:
        return jsonify({"error": "Розыгрыши только для своих! Напишите хотя бы 50 сообщений в чате (или получите статус VIP/BEYOND), чтобы участвовать."}), 400

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
    import time
    if user_db.get("bankrupt_until", 0) > time.time():
        return jsonify({"error": "📉 Ваш статус «Банкрот» блокирует доступ к Черному Рынку! Ограничение длится 24 часа."}), 400
        
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

    if discount_pct > 0:
        buyer_price_rub = int(original_rub * (1 - discount_pct / 100))
        if buyer_price_rub < 5: buyer_price_rub = 5
        
    buyer_price_pts = int(buyer_price_rub * 2.5)

    # 🔥 ВЛИЯНИЕ КАРМЫ: СКИДКА 10% ДЛЯ ХОРОШИХ ГРАЖДАН 🔥
    is_good_citizen = user_db.get("social_rating", 0) >= 50
    if is_good_citizen:
        buyer_price_rub = int(buyer_price_rub * 0.9)
        buyer_price_pts = int(buyer_price_pts * 0.9)
        if buyer_price_rub < 1: buyer_price_rub = 1
        if buyer_price_pts < 1: buyer_price_pts = 1
    
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
    
    is_adequate = original_rub <= (rec_price + 10)
    gross_payout = original_rub
    subsidy_msg = ""
    
    if discount_pct > 0:
        if is_adequate:
            if discount_pct == 20:
                gross_payout = int(original_rub * 0.90) 
                subsidy_msg = "🔥 _Лот ушел со скидкой -20%. Скайнет компенсировал уценку из своих фондов!_"
            elif discount_pct == 50:
                gross_payout = int(original_rub * 0.85) 
                subsidy_msg = "🔥 _Лот ушел со скидкой -50%. Скайнет компенсировал уценку из своих фондов!_"
        else:
            gross_payout = buyer_price_rub 
            subsidy_msg = f"📉 _Лот продан со скидкой -{discount_pct}%. Цена была выше рекомендованной, субсидия не начислена._"
            
    has_rhodo = db['farm_plots'].find_one({"uid": lot['seller_uid'], "seed_type": "rhododendron", "status": "ready"})
    multiplier = 0.95 if has_rhodo else 0.90
    
    # ОФШОР: Проверяем, есть ли активный офшор
    seller_data = paid_collection.find_one({"uid": lot['seller_uid']}) or {}
    if seller_data.get("offshore_until", 0) > time.time(): 
        multiplier = 1.0 # 0% комиссии
    
    promo_id = lot['promo_id'] # <--- ПЕРЕНЕСЛИ СЮДА
    
    # 🔥 ПАТЧ РЫНКА: Разделение валют 🔥
    if currency == "rub":
        seller_profit = int(gross_payout * multiplier)
        paid_collection.update_one({"uid": lot['seller_uid']}, {"$inc": {"cashback_balance": seller_profit}})
        safe_commission = buyer_price_rub - seller_profit
        if safe_commission > 0: db['safes_state'].update_one({"_id": "safe_red"}, {"$inc": {"balance": safe_commission}}) 
        db['ruble_ledger'].insert_one({"uid": lot['seller_uid'], "amount": seller_profit, "reason": f"Продажа лота {promo_id} на Рынке", "timestamp": time.time()}) 
        currency_sym = "₽"
    else:
        # Покупали за очки -> Продавец получает ОЧКИ
        seller_profit = int(buyer_price_pts * multiplier)
        paid_collection.update_one({"uid": lot['seller_uid']}, {"$inc": {"bounty_points": seller_profit}})
        currency_sym = "💎"
        subsidy_msg = "" # Субсидия в рублях не платится, если сделка в очках
    
    db['promocodes'].update_one({"_id": promo_id}, {"$set": {"owner_uid": uid}})
    
    # === 3. УВЕДОМЛЕНИЕ ПРОДАВЦУ ===
    try:
        from core.bot import bot
        msg_text = f"💸 **НОВОСТИ С РЫНКА!**\n\nВаш лот `{promo_id}` был успешно продан!\nНа ваш счет зачислено: **{seller_profit}{currency_sym}** (комиссия рынка учтена)."
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
    pay_casino_owner(5)

    # 🔥 ТРЕКЕР ДЛЯ ЗОЛОТОГО КЕЙСА (СИНХРОНИЗАЦИЯ ПО ЕКБ) 🔥
    import datetime
    tz_ekb = datetime.timezone(datetime.timedelta(hours=5))
    today_str = datetime.datetime.now(tz_ekb).strftime("%Y-%m-%d")
    db['tasks_progress'].update_one({"uid": uid, "date": today_str}, {"$inc": {"roulette_spins": 1}}, upsert=True)

    if not updated_user:
        return jsonify({"error": "Недостаточно очков! Нужно 50 💎."}), 400

    import random
    val = random.randint(1, 64)
    
    # БАФФ САНТЫ (Используем updated_user вместо user_data)
    if "santa" in updated_user.get("achievements", []):
        if val != 64 and random.randint(1, 100) <= 5:
            val = 64
            
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

    # === НОВЫЙ БЛОК: УНИВЕРСАЛЬНЫЙ СЕРТИФИКАТ ===
    cert_cost_stars = 1250
    if val == 62 and bank_data.get("balance", 0) >= cert_cost_stars:
        db['casino_bank'].update_one({"_id": "premium_fund"}, {"$inc": {"balance": -cert_cost_stars}})
        prize_msg = "🛍 СУПЕР-ПРИЗ!!!\nВы выиграли Универсальный Сертификат (1500 ₽)!\nЗаявка отправлена админам."
        prize_id, prize_name = "certificate", "Сертификат 1500₽"
        
        import time
        db['premium_claims'].insert_one({"uid": uid, "username": username_str, "timestamp": time.time(), "status": "pending"})
        try:
            from core.bot import bot
            from config import STAFF_GROUP_ID, PRIZES_THREAD_ID
            from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
            markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ Обработать в ЦУП", url="https://elite-poster-bot.onrender.com/glaz"))
            bot.send_message(STAFF_GROUP_ID, f"🛍 <b>СОРВАН СУПЕР-ПРИЗ (СЕРТИФИКАТ) ИЗ WEB APP!</b> 🛍\n\n👤 Победитель: {first_name} ({username_str})\n\n❗️ <i>Заявка добавлена в Веб-панель.</i>", parse_mode="HTML", reply_markup=markup, message_thread_id=PRIZES_THREAD_ID)
        except: pass

    elif val == 62:
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": 1000, "jackpot_shards": 5}})
        prize_msg = "🎰 МИНИ-ДЖЕКПОТ!\nФонд пуст, поэтому вы получаете +1000 Очков и 5 Осколков!"
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
        # 🔥 ВЛИЯНИЕ КАРМЫ: ШТРАФ 50% ДЛЯ ИЗГОЕВ 🔥
        tax_pct = 0.5 if updated_user.get("social_rating", 0) <= -50 else 0.3
        
        lost_points = int(updated_user.get("bounty_points", 0) * tax_pct)
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
            if tax_pct == 0.5:
                prize_msg = f"💀 НАЛОГОВАЯ ПРОВЕРКА!\nУ вас КРИТИЧЕСКИ низкий социальный рейтинг! Штраф увеличен: списано 50% баланса (-{lost_points} очков)."
            else:
                prize_msg = f"💀 НАЛОГОВАЯ ПРОВЕРКА!\nСписано 30% баланса (-{lost_points} очков)."
            prize_id, prize_name = "tax", f"Налог -{int(tax_pct*100)}%"

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
            import time
            db['ruble_ledger'].insert_one({"uid": uid, "amount": win_rub, "reason": "Выигрыш в Гача-Рулетку", "timestamp": time.time()})
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

    # 🔥 ИСПРАВЛЕНИЕ: Генерируем today_str С УЧЕТОМ ЧАСОВОГО ПОЯСА 🔥
    tz_ekb = datetime.timezone(datetime.timedelta(hours=5))
    today_str = datetime.datetime.now(tz_ekb).strftime("%Y-%m-%d")
    
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
            # 🔥 БЕЗОПАСНЫЙ КРАФТ С ПРОВЕРКОЙ КАССЫ 🔥
            bank_data = db['casino_bank'].find_one({"_id": "premium_fund"}) or {"balance": 0}
            premium_cost = 1500
            
            if bank_data.get("balance", 0) >= premium_cost:
                db['casino_bank'].update_one({"_id": "premium_fund"}, {"$inc": {"balance": -premium_cost}})
                
                import time
                db['premium_claims'].insert_one({"uid": uid, "username": username_str, "timestamp": time.time(), "status": "pending"})
                
                try:
                    from core.bot import bot
                    from config import STAFF_GROUP_ID, PRIZES_THREAD_ID
                    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
                    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ Обработать в ЦУП", url="https://elite-poster-bot.onrender.com/glaz"))
                    bot.send_message(
                        STAFF_GROUP_ID, 
                        f"🏆 <b>СОРВАН ДЖЕКПОТ (TELEGRAM PREMIUM) ИЗ КРАФТА!</b> 🏆\n\n"
                        f"👤 Победитель: {first_name} ({username_str})\n"
                        f"❗️ <i>Списано {premium_cost}⭐️ из фонда. Заявка в Веб-панели.</i>", 
                        parse_mode="HTML", reply_markup=markup, message_thread_id=PRIZES_THREAD_ID
                    )
                except Exception as e:
                    pass
                return jsonify({"success": True, "msg": "💎 ДЖЕКПОТ! Вы выиграли Telegram Premium! Заявка отправлена администрации."})
            else:
                # Касса пуста! Даем жирный, но ВИРТУАЛЬНЫЙ приз
                paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": 3000, "jackpot_shards": 5}})
                return jsonify({"success": True, "msg": "🎰 СУПЕР-ПРИЗ!\nФонд Premium сейчас копится, поэтому Наковальня выдала вам 3000 💎 и 5 Осколков обратно!"})

    elif action == 'beyond':
        if user_data.get("bounty_points", 0) < 3000 or user_data.get("immunity", 0) < 2:
            return jsonify({"error": "Нужно 3000 очков и 2 щита!"}), 400
            
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": -3000, "immunity": -2}})
        u_info = db['users'].find_one({"_id": uid}) or {}
        
        import random
        # 🔥 ПАТЧ: Выдаем КУПОНЫ вместо прямой записи в базу 🔥
        if not u_info.get("is_queer"):
            code = f"BEYOND-{random.randint(1000, 9999)}"
            db['promocodes'].insert_one({"_id": code, "type": "percent", "value": 100, "target": "beyond", "usage_limit": 1, "used_count": 0, "is_active": True, "owner_uid": uid})
            return jsonify({"success": True, "msg": f"🏳️‍🌈 Выкован 100% Купон на BEYOND!\nВаш код: {code}\n(Ищите в Рюкзаке)"})
            
        elif not u_info.get("is_vip"):
            code = f"VIP-{random.randint(1000, 9999)}"
            db['promocodes'].insert_one({"_id": code, "type": "percent", "value": 100, "target": "vip", "usage_limit": 1, "used_count": 0, "is_active": True, "owner_uid": uid})
            return jsonify({"success": True, "msg": f"👑 Выкован 100% Купон на VIP!\nВаш код: {code}\n(Ищите в Рюкзаке)"})
            
        else:
            paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": 25000, "immunity": 5}})
            return jsonify({"success": True, "msg": "💰 Макс. уровень! Ресурсы переплавлены в 25 000 💎 и 5 🛡 Щитов!"})

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
    karma = user_data.get("social_rating", 0)
    
    # 🔥 ВЛИЯНИЕ КАРМЫ НА СУНДУК 🔥
    cat_threshold = 45
    if karma >= 50: cat_threshold = 5   # Святые почти не встречают кота
    elif karma <= -50: cat_threshold = 70 # Злых кот грабит по-черному
    
    if chance <= cat_threshold:
        rubles = user_data.get("cashback_balance", 0)
        target = "points"
        
        # 🔥 УМНЫЙ КОТ: Выбирает, что жрать 🔥
        if rubles >= 2000 and random.randint(1, 100) <= 50:
            target = "rubles" # Шанс 50%, если денег больше 2000
        elif rubles > 0 and random.randint(1, 100) <= 15:
            target = "rubles" # Шанс 15%, если просто есть деньги
            
        if target == "rubles":
            stolen_rub = int(rubles * random.uniform(0.10, 0.25))
            if stolen_rub > 0:
                paid_collection.update_one({"uid": uid}, {"$inc": {"cashback_balance": -stolen_rub}})
                db['safes_state'].update_one({"_id": "safe_red"}, {"$inc": {"balance": stolen_rub}})
                import time
                db['ruble_ledger'].insert_one({"uid": uid, "amount": -stolen_rub, "reason": "Кот в мешке (Сундук)", "timestamp": time.time()})
                return jsonify({"success": True, "msg": f"🐈‍⬛ КОТ-КАПИТАЛИСТ!\nКот выскочил из сундука и разорвал ваши купюры! Потеряно {stolen_rub} ₽ (переведены в Фин. Сейф)!"})

        # Если кот выбрал очки
        steal_pct = random.uniform(0.15, 0.35) if remaining >= 10000 else random.uniform(0.10, 0.25)
        stolen_pts = int(remaining * steal_pct)
        if stolen_pts > 0: 
            paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": -stolen_pts}})
            db['safes_state'].update_one({"_id": "safe_blue"}, {"$inc": {"balance": stolen_pts}})
            
        return jsonify({"success": True, "msg": f"🐈‍⬛ КОТ В МЕШКЕ!\nКот выскочил из сундука и украл {stolen_pts} очков, пока убегал!"})
    elif chance <= 80:
        shards = random.randint(1, 4)
        paid_collection.update_one({"uid": uid}, {"$inc": {"jackpot_shards": shards}})
        return jsonify({"success": True, "msg": f"📦 Сундук открыт!\nНайдено пыль и +{shards} Осколок(ка)."})
    elif chance <= 95:
        paid_collection.update_one({"uid": uid}, {"$inc": {"immunity": 1}})
        return jsonify({"success": True, "msg": "🛡 ОТЛИЧНЫЙ ДРОП!\nВы нашли Щит Иммунитета!"})
    else:
        paid_collection.update_one({"uid": uid}, {"$inc": {"cashback_balance": 500}})
        import time
        db['ruble_ledger'].insert_one({"uid": uid, "amount": 500, "reason": "Джекпот в Секретном Сундуке", "timestamp": time.time()})

@app.route('/api/get_cpa', methods=['POST'])
def api_get_cpa():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN): return jsonify({"error": "Auth failed"}), 403
    uid = json.loads(dict(qc.split("=", 1) for qc in unquote(data.get('initData')).split("&"))['user'])['id']
    
    # 1. Личная стата агента за ВСЁ ВРЕМЯ
    hold = db['cpa_traffic'].count_documents({"agent_id": uid, "status": "hold"})
    approved = db['cpa_traffic'].count_documents({"agent_id": uid, "status": "approved"})
    fraud_banned = db['cpa_traffic'].count_documents({"agent_id": uid, "status": "fraud_banned"})
    fraud_left = db['cpa_traffic'].count_documents({"agent_id": uid, "status": "fraud_left"})
    fraud_old = db['cpa_traffic'].count_documents({"agent_id": uid, "status": "fraud"})
    
    user_data = paid_collection.find_one({"uid": uid}) or {}
    dupes = user_data.get("cpa_duplicates", 0)
    cases = user_data.get("agent_cases", 0) 
    
    # 2. 🔥 ФОРМИРУЕМ ЛИДЕРБОРД ТЕКУЩЕГО МЕСЯЦА (С БРАКОМ) 🔥
    import datetime
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5)))
    current_month_str = now.strftime("%Y-%m")
    start_of_month_ts = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    
    pipeline = [
        {"$match": {
            "join_time": {"$gte": start_of_month_ts}
        }},
        {"$group": {
            "_id": "$agent_id",
            "valid_leads": {"$sum": {"$cond": [{"$in": ["$status", ["approved", "hold"]]}, 1, 0]}},
            "approved": {"$sum": {"$cond": [{"$eq": ["$status", "approved"]}, 1, 0]}},
            "hold": {"$sum": {"$cond": [{"$eq": ["$status", "hold"]}, 1, 0]}},
            "fraud": {"$sum": {"$cond": [{"$in": ["$status", ["fraud_banned", "fraud_left", "fraud"]]}, 1, 0]}}
        }},
        {"$match": {"valid_leads": {"$gt": 0}}},
        {"$sort": {"valid_leads": -1}},
        {"$limit": 5}
    ]
    top_agents_raw = list(db['cpa_traffic'].aggregate(pipeline))
    
    top_agents = []
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    for idx, agent in enumerate(top_agents_raw):
        u_info = db['users'].find_one({"_id": agent["_id"]}) or {}
        first_name = u_info.get("first_name", "Аноним")
        safe_name = f"{first_name[:6]}***" 
        
        is_me = (agent["_id"] == uid)
        display_name = "Вы (Лидер!)" if is_me else safe_name
        
        top_agents.append({
            "medal": medals[idx] if idx < 5 else f"{idx+1}️⃣",
            "name": display_name,
            "count": agent["valid_leads"],
            "approved": agent["approved"],
            "hold": agent["hold"],
            "fraud": agent["fraud"], # <--- Передаем БРАК на фронтенд
            "is_me": is_me
        })
    
    return jsonify({
        "hold": hold, 
        "approved": approved, 
        "fraud_banned": fraud_banned, 
        "fraud_left": fraud_left,
        "fraud_old": fraud_old,
        "duplicates": dupes, 
        "cases": cases,
        "leaderboard": top_agents,
        "current_month": current_month_str
    })

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
    import time
    db['ruble_ledger'].insert_one({"uid": uid, "amount": -cost, "reason": f"Обмен на {reward} очков", "timestamp": time.time()})
    return jsonify({"success": True, "msg": f"✅ Успешно обменяли {cost}₽ на {reward}💎!"})

@app.route('/api/loan', methods=['POST'])
def api_loan():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN): return jsonify({"error": "Auth failed"}), 403
    uid = json.loads(dict(qc.split("=", 1) for qc in unquote(data.get('initData')).split("&"))['user'])['id']
    
    action = data.get('action')
    user_db = paid_collection.find_one({"uid": uid}) or {}
    
    import time
    now = time.time()
    
    # === ВЗЯТЬ КРЕДИТ ===
    if action == 'take':
        if user_db.get("debt", 0) > 0:
            return jsonify({"error": "У вас уже есть непогашенный кредит! МФО отказывает в выдаче."}), 400
            
        # 🔥 ПАТЧ: ПРОВЕРКА НА ТВИНКОВ И РЕЙТИНГ 🔥
        karma = user_db.get("social_rating", 0)
        if karma <= -100:
            return jsonify({"error": "ВРАГ НАРОДА! Скайнет не спонсирует криминал. Выдача запрещена."}), 400

        chat_stat = db['chat_stats'].find_one({"uid": uid, "msgs": {"$gte": 100}})
        if not chat_stat and karma <= 0:
            return jsonify({"error": "МФО не дает деньги незнакомцам! Напишите хотя бы 100 сообщений в наших чатах."}), 400
            
        amount = int(data.get('amount', 0))
        days = int(data.get('days', 1))
        
        max_loan = 20000 if karma >= 50 else 10000
        if amount < 100 or amount > max_loan:
            return jsonify({"error": f"Сумма кредита: от 100 до {max_loan} 💎!"}), 400
            
        # Считаем проценты в зависимости от Кармы
        if days == 1: percent = 0.05 if karma >= 50 else (0.40 if karma <= -50 else 0.20)
        elif days == 3: percent = 0.15 if karma >= 50 else (0.80 if karma <= -50 else 0.40)
        elif days == 7: percent = 0.30 if karma >= 50 else (1.40 if karma <= -50 else 0.70)
        else: return jsonify({"error": "Неверный срок кредита!"}), 400
        
        debt_amount = int(amount + (amount * percent))
        deadline = now + (days * 86400)
        
        # Выдаем деньги и вешаем долг
        paid_collection.update_one({"uid": uid}, {
            "$inc": {"bounty_points": amount},
            "$set": {"debt": debt_amount, "debt_deadline": deadline, "debt_notified": False}
        })
        
        return jsonify({"success": True, "msg": f"💳 Кредит одобрен!\nПолучено: {amount} 💎\nК возврату: {debt_amount} 💎\nСрок: {days} дн."})
        
    # === ПОГАСИТЬ КРЕДИТ ===
    elif action == 'pay':
        debt = user_db.get("debt", 0)
        if debt <= 0: return jsonify({"error": "У вас нет долгов!"}), 400
        
        if user_db.get("bounty_points", 0) < debt:
            return jsonify({"error": f"Недостаточно средств! Нужно {debt} 💎 для полного погашения."}), 400
            
        # Списываем долг и разблокируем юзера (если он был в муте)
        paid_collection.update_one({"uid": uid}, {
            "$inc": {"bounty_points": -debt},
            "$unset": {"debt": "", "debt_deadline": "", "debt_notified": ""}
        })
        
        # Снимаем мут (добавляем задачу Скайнетам)
        db['skynet_tasks'].insert_one({"uid": uid, "action": "full_unban", "timestamp": now})
        
        # Проценты (чистая прибыль) уходят в Синий Сейф!
        profit = debt - int(debt / (1 + percent)) if 'percent' in locals() else int(debt * 0.2) # примерный расчет прибыли
        db['safes_state'].update_one({"_id": "safe_blue"}, {"$inc": {"balance": profit}})
        
        return jsonify({"success": True, "msg": "✅ Долг полностью погашен! Вы свободны."})

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

    # 🔥 ПРОВЕРКА VIP И BEYOND СТАТУСА (УМНОЖАЕМ НАГРАДЫ ЕЖЕДНЕВОК) 🔥
    u_info = db['users'].find_one({"_id": uid}) or {}
    is_elite = u_info.get("is_vip") or u_info.get("is_queer")
    multiplier = 2 if is_elite else 1
    
    # 👇 ИСПРАВЛЕНИЕ: Добавили BEYOND в текстовое уведомление! 👇
    vip_msg = "👑 (VIP/BEYOND x2)" if is_elite else "\n_👑 Получи VIP или BEYOND, чтобы забирать х2 лута!_"
        
    update_query = {"$inc": {}}
    if case_type == 'wooden':
        pts = 50 * multiplier
        shards = 1 * multiplier
        update_query["$inc"]["bounty_points"] = pts
        update_query["$inc"]["jackpot_shards"] = shards
        msg = f"🪵 **Деревянный кейс открыт!** {vip_msg}\nВы получили {pts} 💎 и {shards} Осколок(ка)!"
        
    elif case_type == 'silver':
        pts = 100 * multiplier
        shields = 1 * multiplier
        update_query["$inc"]["bounty_points"] = pts
        update_query["$inc"]["immunity"] = shields
        msg = f"🥈 **Серебряный кейс открыт!** {vip_msg}\nВы получили {pts} 💎 и {shields} Щит(а) Иммунитета!"
        
    elif case_type == 'gold':
        pts = 300 * multiplier
        keys = 1 * multiplier
        update_query["$inc"]["bounty_points"] = pts
        update_query["$inc"]["key_red"] = keys
        msg = f"🥇 **Золотой кейс открыт!** {vip_msg}\nВы сорвали куш: {pts} 💎 и {keys} 🔑 Ключ(а) от Сейфа!"
        
    paid_collection.update_one({"uid": uid}, update_query)
    db['tasks_progress'].update_one({"uid": uid, "date": today_str}, {"$push": {"opened": case_type}}, upsert=True)
    
    return jsonify({"success": True, "msg": msg})

@app.route('/api/payout', methods=['POST'])
def api_payout():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN): 
        return jsonify({"error": "Auth failed"}), 403

    parsed_data = dict(qc.split("=", 1) for qc in unquote(data.get('initData')).split("&"))
    user_info = json.loads(parsed_data['user'])
    uid = user_info['id']
    
    # === ПАТЧ БЕЗОПАСНОСТИ: Блокировка вывода для нарушителей ===
    from utils.validators import is_user_locked
    if is_user_locked(uid):
        return jsonify({"error": "⛔️ Вывод средств заморожен! Оплатите штраф или снимите блокировку через бота (/start)."}), 400
    # ============================================================
    
    username = user_info.get('username', f"ID {uid}")
    
    amount = int(data.get('amount', 0))
    method = data.get('method')
    details = data.get('details')
    
    if amount <= 0: return jsonify({"error": "Сумма должна быть больше нуля!"}), 400
    if amount < 500: return jsonify({"error": "Минимум 500₽ для вывода!"}), 400
    if method == "На карту" and amount < 3500: return jsonify({"error": "На карту минимум 3500₽!"}), 400
    if not details or len(details) < 5: return jsonify({"error": "Укажите корректные реквизиты!"}), 400
    
    user_db = paid_collection.find_one({"uid": uid}) or {}
    if user_db.get("cashback_balance", 0) < amount: 
        return jsonify({"error": "Недостаточно средств!"}), 400
    
    # 1. Списываем баланс
    paid_collection.update_one({"uid": uid}, {"$inc": {"cashback_balance": -amount}})
    
    # 👇 ФИКС 1: ИМПОРТ ДО ИСПОЛЬЗОВАНИЯ ВРЕМЕНИ 👇
    import time
    
    # 2. Пишем в финансовый лог
    db['ruble_ledger'].insert_one({
        "uid": uid, "amount": -amount, "reason": f"Заявка на вывод: {method}", "timestamp": time.time()
    })
    
    # 3. Пишем в базу ЦУПа
    db['withdrawals'].insert_one({
        "user_id": uid, "amount": amount, "method": method, "details": details, "status": "pending", "timestamp": time.time()
    })
    
    from core.bot import bot
    from config import STAFF_GROUP_ID, FINANCE_THREAD_ID
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
    import html # 👇 ФИКС 2: БЕЗОПАСНАЯ РАЗМЕТКА 👇
    
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("✅ Выплачено", callback_data=f"payout_done_{uid}_{amount}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"payout_cancel_{uid}_{amount}")
    )
    
    safe_username_html = html.escape(username)
    safe_details = html.escape(details)
    
    try:
        bot.send_message(
            STAFF_GROUP_ID,
            f"💰 <b>ЗАЯВКА НА ВЫПЛАТУ (WEB APP)</b>\n\n👤 От: {safe_username_html} (<code>{uid}</code>)\n💵 Сумма: <b>{amount} руб.</b>\n🏦 Способ: <b>{method}</b>\n📝 Реквизиты:\n<code>{safe_details}</code>",
            reply_markup=markup, parse_mode="HTML", message_thread_id=FINANCE_THREAD_ID
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
            t_name = "Штраф" if target_type == 'fine' else "Рекламу" if target_type == 'ads' else "VIP" if target_type == 'vip' else "BEYOND" if target_type == 'beyond' else "Любую услугу"
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
    
    user_db = paid_collection.find_one({"uid": uid}) or {}
    import time
    if user_db.get("bankrupt_until", 0) > time.time():
        return jsonify({"error": "📉 Ваш статус «Банкрот» блокирует доступ к Черному Рынку! Ограничение длится 24 часа."}), 400
        
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

@app.route('/api/get_auction', methods=['POST'])
def api_get_auction():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN): return jsonify({"error": "Auth failed"}), 403
    
    # Тянем активные лоты теневого аукциона
    lots = list(db['auction_lots'].find({"status": "active"}).sort("end_time", 1))
    
    import time
    now = int(time.time())
    result = []
    for lot in lots:
        time_left = lot['end_time'] - now
        if time_left < 0: time_left = 0
        
        hrs = time_left // 3600
        mins = (time_left % 3600) // 60
        time_str = f"{hrs}ч {mins}м" if time_left > 0 else "Завершается..."
        
        result.append({
            "id": str(lot["_id"]),
            "name": lot["name"],
            "desc": lot["desc"],
            "icon": lot["icon"],
            "current_bid": lot["current_bid"],
            "leader_name": lot.get("leader_name", "Нет ставок"),
            "time_left": time_str
        })
    return jsonify(result)

@app.route('/api/place_bid', methods=['POST'])
def api_place_bid():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN): return jsonify({"error": "Auth failed"}), 403
    
    parsed_data = dict(qc.split("=", 1) for qc in unquote(data.get('initData')).split("&"))
    user_info = json.loads(parsed_data['user'])
    uid = user_info['id']
    first_name = user_info.get('first_name', 'Аноним')
    
    lot_id = data.get('lot_id')
    bid_amount = int(data.get('bid', 0))
    
    from bson.objectid import ObjectId
    import time
    
    lot = db['auction_lots'].find_one({"_id": ObjectId(lot_id), "status": "active"})
    if not lot: return jsonify({"error": "Лот уже продан или не существует!"}), 400
    
    if lot['end_time'] <= int(time.time()):
        return jsonify({"error": "Торги по этому лоту уже завершены!"}), 400
        
    min_bid = lot['current_bid'] + 100 # Минимальный шаг 100 очков
    if bid_amount < min_bid:
        return jsonify({"error": f"Ставка перебита! Минимальная ставка сейчас: {min_bid} 💎"}), 400
        
    user_db = paid_collection.find_one({"uid": uid}) or {}
    if user_db.get("bounty_points", 0) < bid_amount:
        return jsonify({"error": "Недостаточно очков для такой ставки!"}), 400
        
    # Возвращаем очки предыдущему лидеру (если он был)
    prev_leader = lot.get("leader_uid")
    prev_bid = lot.get("current_bid", 0)
    if prev_leader and prev_bid > 0:
        paid_collection.update_one({"uid": prev_leader}, {"$inc": {"bounty_points": prev_bid}})
        try:
            from core.bot import bot
            bot.send_message(prev_leader, f"⚠️ <b>АУКЦИОН:</b> Вашу ставку на лот «{lot['name']}» перебили! Ваши {prev_bid} 💎 возвращены на баланс.", parse_mode="HTML")
        except: pass
        
    # Списываем очки у нового лидера
    paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": -bid_amount}})
    
    # Обновляем лот (Анти-снайпер: если до конца < 5 минут, продлеваем на 5 минут)
    end_time = lot['end_time']
    if end_time - int(time.time()) < 300:
        end_time += 300 
        
    db['auction_lots'].update_one({"_id": ObjectId(lot_id)}, {
        "$set": {
            "current_bid": bid_amount,
            "leader_uid": uid,
            "leader_name": first_name,
            "end_time": end_time
        }
    })
    
    return jsonify({"success": True, "msg": f"Ваша ставка {bid_amount} 💎 принята!"})

@app.route('/api/inventory_action', methods=['POST'])
def api_inventory_action():
    data = request.json
    if not validate_webapp_data(data.get('initData'), BOT_TOKEN): 
        return jsonify({"error": "Ошибка авторизации (Неверная подпись)"}), 403
        
    user_info = json.loads(dict(qc.split("=", 1) for qc in unquote(data.get('initData')).split("&"))['user'])
    uid = user_info['id']
    first_name = user_info.get('first_name', 'Аноним')
    
    action = data.get('action')
    
    # Секретный переводчик @username -> ID
    def resolve_uid(target_info):
        t_info = str(target_info).strip()
        if t_info.isdigit(): return int(t_info)
        if t_info.startswith('@'):
            uname = t_info.replace('@', '').lower()
            u = db['users'].find_one({"username": uname})
            if u: return u['_id']
            cs = db['chat_stats'].find_one({"username": uname})
            if cs: return cs['uid']
        return None

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
        target_uid = resolve_uid(data.get('target_info'))
        if not target_uid: return jsonify({"error": "Пользователь не найден в базе! Пусть напишет что-то в чат."}), 400
        
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

    elif action == 'hack':
        target_uid = resolve_target_uid(data.get('target_info'))
        if not target_uid: return jsonify({"error": "Пользователь не найден в базе! Пусть напишет что-то в чат."}), 400
        
        if target_uid == uid: return jsonify({"error": "Нельзя взломать самого себя!"}), 400
        
        user_data = paid_collection.find_one({"uid": uid}) or {}
        target_data = paid_collection.find_one({"uid": target_uid}) or {}
        target_karma = target_data.get("social_rating", 0)
        
        import time
        now = time.time()

        # 🔥 ПРОВЕРКА АНАРХИИ (СУДНАЯ НОЧЬ) ПОДНЯТА НАВЕРХ 🔥
        anarchy = db['settings'].find_one({"_id": "anarchy_mode"}) or {}
        is_anarchy = anarchy.get("active", False) and anarchy.get("end_time", 0) > time.time()
        
        # 1. ПРОВЕРКА КУЛДАУНА ХАКЕРА (Учитываем Анархию)
        last_hack = user_data.get("last_hack_time", 0)
        cooldown_time = 900 if is_anarchy else 3600 # 15 минут при Анархии
        
        if now - last_hack < cooldown_time:
            left_mins = int((cooldown_time - (now - last_hack)) / 60)
            return jsonify({"error": f"Ваш вирус еще компилируется! Ждите {left_mins} мин."}), 400
            
        if user_data.get("bounty_points", 0) < 200:
            return jsonify({"error": "У вас нет 200 💎 для запуска вируса!"}), 400
            
        if target_data.get("bounty_points", 0) < 100:
            return jsonify({"error": "Жертва слишком бедна, нечего красть!"}), 400
            
        # 🔥 2. ПРОВЕРКА ИММУНИТЕТА ЖЕРТВЫ 🔥
        # Во время Анархии защита Кармы и таймеры отключаются полностью!
        if not is_anarchy:
            if target_karma >= 50: 
                imm_time = 21600 # 6 часов для Святых
                imm_text = "6 часов"
            elif target_karma <= -50: 
                imm_time = 7200  # 2 часа для Изгоев (Кормовая база)
                imm_text = "2 часа"
            else: 
                imm_time = 14400 # 4 часа по умолчанию
                imm_text = "4 часа"

            last_hacked = target_data.get("last_hacked_time", 0)
            if now - last_hacked < imm_time:
                return jsonify({"error": f"Сервер жертвы под защитой! Из-за её рейтинга защита длится {imm_text}."}), 400
            
        # Списываем 200 очков за попытку и ставим Кулдаун хакеру
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": -200}, "$set": {"last_hack_time": now}})
        from core.bot import bot
        
        # 1. Пробиваем Щит (Щит работает даже при Анархии)
        if target_data.get("immunity", 0) > 0:
            # Списываем щит и даем жертве иммунитет на 4 часа
            paid_collection.update_one({"uid": target_uid}, {"$inc": {"immunity": -1}, "$set": {"last_hacked_time": now}})
            try: bot.send_message(target_uid, f"🛡 **ВАШ СЕРВЕР АТАКОВАЛИ!**\nХакер `ID {uid}` пытался украсть ваши Очки, но Щит Иммунитета ударил его током!\n_(Щит разрушен, система в безопасности на 4 часа)_", parse_mode="Markdown")
            except: pass
            return jsonify({"success": True, "msg": "❌ АТАКА ОТРАЖЕНА!\nУ жертвы был Щит. Вирус уничтожен, вы потеряли 200 💎."})
            
        # 2. Если щита нет - бросаем кубик
        import random
        target_achievements = target_data.get("achievements", [])
        hack_chance = 40 if "rat" in target_achievements else 30
        
        extra_msg = ""
        if is_anarchy:
            hack_chance = 80 # При Анархии шанс взлома 80% для всех!
            extra_msg = "\n🏴‍‍☠️ <b>СУДНАЯ НОЧЬ!</b> Брандмауэры отключены, грабеж прошел как по маслу!"
        
        if random.randint(1, 100) <= hack_chance:
            # 🔥 ВЛИЯНИЕ КАРМЫ: ГРАБИМ ИЗГОЕВ СИЛЬНЕЕ 🔥
            if target_karma <= -50:
                steal_pct = random.uniform(0.15, 0.30)
                if not is_anarchy:
                    extra_msg = "\n📉 *Цель неблагонадежна!* Защита сервера была ослаблена из-за плохой кармы, вы украли в 2 раза больше очков!"
            else:
                steal_pct = random.uniform(0.05, 0.15)
                
            stolen = int(target_data.get("bounty_points", 0) * steal_pct)
            if stolen < 10: stolen = 10
            
            # Крадем очки и вешаем иммунитет жертве
            paid_collection.update_one({"uid": target_uid}, {"$inc": {"bounty_points": -stolen}, "$set": {"last_hacked_time": now}})
            paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": stolen}})
            
            try: bot.send_message(target_uid, f"🚨 **СИСТЕМА ВЗЛОМАНА!**\nХакер `ID {uid}` пробил вашу защиту и украл **{stolen} 💎**!\n_Срочно покупайте Щиты на Ферме или в Рюкзаке._", parse_mode="Markdown")
            except: pass
            
            return jsonify({"success": True, "msg": f"💻 ВЗЛОМ УСПЕШЕН!\nВы обошли защиту и украли {stolen} 💎 у жертвы!{extra_msg}"})
        else:
            return jsonify({"success": True, "msg": "📉 Атака провалилась. Брандмауэр жертвы выстоял, вирус стерт. Вы потеряли 200 💎."})

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
    
    if not plot: return jsonify({"error": "Грядка не найдена!"}), 400 # <--- ПЕРЕНЕСЛИ НАВЕРХ
    
    # Защита от действий во время заражения
    if action in ['water', 'fertilize', 'harvest']:
        if plot.get('pest'):
            return jsonify({"error": f"Сначала прогоните вредителя ({plot['pest']['emoji']})!"}), 400
    
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
        
        # БАФФ ПАТРИАРХА
        grow_time = CROPS[seed_type]['grow_time']
        if "patriarch" in (paid_collection.find_one({"uid": uid}) or {}).get("achievements", []):
            now -= int(grow_time * 0.1)
            
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

        import datetime
        tz_ekb = datetime.timezone(datetime.timedelta(hours=5))
        today_str = datetime.datetime.now(tz_ekb).strftime("%Y-%m-%d")
        db['tasks_progress'].update_one({"uid": uid, "date": today_str}, {"$set": {"watered": True}}, upsert=True)
        
        last_watered = plot.get('last_watered', 0)
        time_passed = now - last_watered
        cooldown = 4 * 3600 # 4 часа в секундах
        
        if time_passed < cooldown:
            left_mins = int((cooldown - time_passed) / 60)
            return jsonify({"error": f"Грядка еще влажная! Возвращайтесь через {left_mins} мин."}), 400
            
        db['farm_plots'].update_one({"_id": plot["_id"]}, {"$set": {"last_watered": now}})
        
        # 🔥 СИНДИКАТ: КООПЕРАТИВНЫЙ ПОЛИВ 🔥
        user_db = paid_collection.find_one({"uid": uid}) or {}
        partner_id = user_db.get("partner_id")
        extra_msg = ""
        
        if partner_id:
            # Поливаем ВСЕ растущие грядки партнера одним махом!
            db['farm_plots'].update_many(
                {"uid": partner_id, "status": "growing"}, 
                {"$set": {"last_watered": now}}
            )
            extra_msg = "\n💖 Грядки вашего супруга(и) также автоматически политы!"
            
        return jsonify({"success": True, "msg": f"💧 Растение успешно полито. Таймер засухи сброшен!{extra_msg}"})
        
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
        
        # 👇 ИСПРАВЛЕНИЕ: Получаем user_db и karma В САМОМ НАЧАЛЕ сбора урожая 👇
        user_db = paid_collection.find_one({"uid": uid}) or {}
        karma = user_db.get("social_rating", 0)

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
            db['ruble_ledger'].insert_one({"uid": uid, "amount": reward_rub, "reason": "Урожай: Денежное Дерево", "timestamp": time.time()})
            
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
            
            # 🔥 ВЛИЯНИЕ КАРМЫ НА УРОЖАЙ 🔥
            if karma <= -50 and random.randint(1, 100) <= 10:
                penalty = int(reward_pts * 0.5)
                reward_pts -= penalty
                msg += f"\n📉 <b>ЭКО-ШТРАФ!</b> Участок признан свалкой из-за кармы. Удержано {penalty} 💎."
                
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
        key_chance = crop.get('key_chance', 0)
        if karma >= 50: key_chance += 5 # Бафф святых на +5% к шансу ключа
        
        if key_type and random.randint(1, 100) <= key_chance:
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
        
        # 🔥 ПРАВИЛЬНЫЙ ПАТЧ: Сокращаем ОСТАВШЕЕСЯ время в 2 раза 🔥
        elapsed = now - plot.get('planted_at', now)
        remaining_time = crop['grow_time'] - elapsed
        
        if remaining_time <= 0:
            # Возвращаем 50 очков, если растение уже созрело, пока юзер жал кнопку
            paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": cost}})
            return jsonify({"error": "Растение уже почти созрело, удобрение не требуется!"}), 400
            
        time_boost = remaining_time // 2
        
        db['farm_plots'].update_one({"_id": plot["_id"]}, {
            "$inc": {"planted_at": -time_boost},
            "$set": {"fertilized": True}
        })
        
        return jsonify({"success": True, "msg": "🧪 Удобрение применено!\nОставшееся время до созревания сокращено в 2 раза."})

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
        
    # === ИЗМЕНИТЬ ВОТ ТАК ===
    chestnuts_count = db['farm_plots'].count_documents({"uid": uid, "seed_type": "chestnut", "status": "ready"})
    max_cracks = 3 + chestnuts_count
    
    # БАФФ МЕДВЕЖАТНИКА
    if "safecracker" in user_db.get("achievements", []):
        max_cracks += 1
    
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
        grant_achievement(uid, "safecracker", "Медвежатник", "🏦", None)
        if safe_color == 'blue':
            paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": prize}})
            currency = "💎"
        else:
            paid_collection.update_one({"uid": uid}, {"$inc": {"cashback_balance": prize}})
            currency = "₽"
            # 👇 ЛОГИРУЕМ ВЗЛОМ
            db['ruble_ledger'].insert_one({"uid": uid, "amount": prize, "reason": "Взлом Финансового Сейфа", "timestamp": time.time()})
            
        import random
        pin_len = 3 if safe_color == 'blue' else 4
        new_pin = "".join([str(random.randint(0, 9)) for _ in range(pin_len)])
        start_balance = 10000 if safe_color == 'blue' else 500
        
        db['safes_state'].update_one({"_id": safe_id}, {
            "$set": {"pin_code": new_pin, "balance": start_balance, "logs": []}
        })
        
        # 1. Отчет админам (Тихо)
        try:
            from core.bot import bot
            from config import STAFF_GROUP_ID, PRIZES_THREAD_ID
            safe_name = "СЕЙФА ДАННЫХ (Очки)" if safe_color == 'blue' else "ФИНАНСОВОГО СЕЙФА (Рубли)"
            bot.send_message(STAFF_GROUP_ID, f"🚨 <b>СИСТЕМА ВЗЛОМАНА!</b>\n\nХакер {user_name_str} подобрал пароль от {safe_name} и унес куш в размере <b>{prize} {currency}</b>!", parse_mode="HTML", message_thread_id=PRIZES_THREAD_ID)
        except: pass

        # 🔥 2. ГРОМКОЕ ОПОВЕЩЕНИЕ ПО ВСЕМ ЧАТАМ 🔥
        def broadcast_safe_crack():
            from config import chat_ids_mk, chat_ids_parni, chat_ids_ns, chat_ids_gayznak, chat_ids_rainbow
            import time
            from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
            from core.scheduler import schedule_message_deletion # 👈 Импорт функции удаления
            
            all_chats = list(chat_ids_mk.values()) + list(chat_ids_parni.values()) + list(chat_ids_ns.values()) + list(chat_ids_gayznak.values()) + list(chat_ids_rainbow.values())
            unique_chats = set(all_chats)
            
            safe_title = "🔵 СЕЙФ ДАННЫХ" if safe_color == 'blue' else "🔴 ФИНАНСОВЫЙ СЕЙФ"
            
            msg_text = (
                f"🚨 <b>ВНИМАНИЕ ВСЕМ УЗЛАМ СКАЙНЕТА! СИСТЕМА ВЗЛОМАНА!</b> 🚨\n\n"
                f"Хакер {user_name_str} только что подобрал пароль и вскрыл <b>{safe_title}</b>!\n"
                f"💰 Украденный куш: <b>{prize} {currency}</b>!\n\n"
                f"<i>Сейф обнулен, сгенерирован новый пароль. Успей добыть ключи на Кибер-Участке и сорвать следующий джекпот!</i>"
            )
            
            try:
                bot_username = bot.get_me().username
                markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🔐 Пойти взламывать", url=f"https://t.me/{bot_username}?start=app_farm"))
                
                for cid in unique_chats:
                    try:
                        # 👈 1. Ловим отправленное сообщение в переменную sent_msg
                        sent_msg = bot.send_message(cid, msg_text, parse_mode="HTML", reply_markup=markup)
                        
                        # 👈 2. Отправляем его в таймер на 1 час (3600 секунд)
                        schedule_message_deletion(cid, sent_msg.message_id, 3600, bot)
                        
                        time.sleep(0.3) 
                    except: pass
            except: pass

        import threading
        threading.Thread(target=broadcast_safe_crack, daemon=True).start()

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

    parsed_data = dict(qc.split("=", 1) for qc in unquote(data.get('initData')).split("&"))
    uid = json.loads(parsed_data['user'])['id']
    from config import ADMIN_CHAT_IDS
    if str(uid) not in [str(x) for x in ADMIN_CHAT_IDS]:
        return jsonify({"error": "Доступ запрещен. Вы не администратор."}), 403

    active = db['active_contest'].find_one({"_id": "current_event", "status": "running"})
    if active:
        return jsonify({"error": f"Сейчас уже идет конкурс «{active.get('title', 'Без названия')}»! Сначала подведите итоги."}), 400

    theme = data.get('theme')
    theme_instruction = f"Сгенерируй конкурс на тему: {theme}" if theme else "Определи ближайший крупный праздник, время года или актуальный тренд (сейчас 2026 год) и придумай масштабный конкурс."

    gemini_key = os.getenv("GEMINI_API_KEY")
    if not gemini_key: return jsonify({"error": "Ключ Gemini не найден на сервере!"}), 500

    # 🔥 Промпт без дат! Нейросеть генерирует только креатив 🔥
    system_prompt = """Ты креативный директор мужского Telegram-сообщества. Твоя задача — придумать МАСШТАБНЫЙ тематический фотоконкурс.
    Верни СТРОГО валидный JSON без маркдауна (без ```json) и без комментариев.
    Формат ответа:
    {
      "contest_id": "уникальный_id_на_английском",
      "title": "Яркое название с эмодзи",
      "description": "Короткое описание",
      "announcement_text": "ОГРОМНЫЙ текст поста-анонса и правил (используй HTML теги <b> и <i>). ОБЯЗАТЕЛЬНО СКОПИРУЙ ЭТУ СТРУКТУРУ ТЕКСТА:
      
      [Завлекающее вступление и призыв к действию]
      
      <b>⚡️ Суть конкурса</b>
      [Что именно нужно сфоткать и как проявить креатив]
      
      <b>🚀 Как участвовать?</b>
      1. Сделай снимок.
      2. Перейди в личку бота и отправь команду /contest.
      3. (Добавь свои пункты по теме).
      
      <b>🎭 Номинации</b>
      [Придумай 4-5 крутых названий номинаций по теме конкурса и распиши, за что они даются]
      
      <b>💡 Советы для успеха</b>
      [Дай 3 креативных совета участникам]
      
      <b>📜 ПРАВИЛА КОНКУРСА</b>
      - Кто участвует: Мужчины 18+
      - 1 аккаунт = максимум 3 фото
      - Никаких ИИ, стоков и чужих фото из интернета
      - Разрешены работы 18+ 🔞 (откровенные материалы будут эксклюзивно размещены в группе «Без предрассудков»)
      - Пользователи не имеющие действующих блокировок
      - Запрет на реалистичную кровь (жесть) и политику
      - Накрутка = мгновенная дисквалификация",
      "prizes": {
         "1": {"text": "5000 💎 + VIP + 1500 ₽"},
         "2": {"text": "3000 💎 + 3 Ордера на Арест"},
         "3": {"text": "1000 💎 + 2 Щита Иммунитета"}
      }
    }"""
    
    models_queue = ["gemini-3.7-flash", "gemini-3.8-flash", "gemini-3.6-flash"]
    ai_data = None
    last_err_msg = "Неизвестная ошибка"
    
    import requests, time
    for model_name in models_queue:
        # Убрали скобки и дублирование ссылки
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={gemini_key}"
        try:
            payload = {
                "systemInstruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"parts": [{"text": theme_instruction}]}],
                "generationConfig": {"temperature": 0.8, "responseMimeType": "application/json"}
            }
            res = requests.post(url, headers={"Content-Type": "application/json"}, json=payload, timeout=25)
            
            if res.status_code == 200:
                try:
                    raw_text = res.json()["candidates"][0]["content"]["parts"][0]["text"]
                    clean_text = raw_text.strip()
                    if clean_text.startswith("```json"): clean_text = clean_text[7:]
                    if clean_text.startswith("```"): clean_text = clean_text[3:]
                    if clean_text.endswith("```"): clean_text = clean_text[:-3]
                    
                    ai_data = json.loads(clean_text.strip())
                    break 
                except (KeyError, IndexError):
                    last_err_msg = "Цензура Gemini заблокировала генерацию (Safety Filter)."
            else:
                last_err_msg = f"Ошибка API: HTTP {res.status_code}"
                
        except requests.exceptions.Timeout:
            last_err_msg = f"Таймаут: Скайнет думал дольше 25 секунд."
            break 
        except json.JSONDecodeError as e:
            last_err_msg = f"Кривой JSON от нейросети: {str(e)}"
        except Exception as e:
            last_err_msg = f"Системная ошибка: {str(e)}"
            
        time.sleep(1)
        
    if not ai_data:
        return jsonify({"error": f"{last_err_msg}"}), 400

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

    # Сохраняем все 4 даты в базу
    update_fields = {
        "title": data.get("title"),
        "announcement_text": data.get("desc"),
        "sub_start": data.get("sub_start"),
        "sub_end": data.get("sub_end"),
        "vote_start": data.get("vote_start"),
        "vote_end": data.get("vote_end"),
        "prizes": {
            "1": {"text": data.get("prize1")},
            "2": {"text": data.get("prize2")},
            "3": {"text": data.get("prize3")}
        },
        "status": "running"
    }
    db['active_contest'].update_one({"_id": "current_event"}, {"$set": update_fields})

    def broadcast():
        from config import chat_ids_mk, chat_ids_parni, chat_ids_ns, chat_ids_gayznak, chat_ids_rainbow, STAFF_GROUP_ID
        from core.bot import bot
        import time
        from datetime import datetime, timedelta # <--- ДОБАВИЛИ timedelta
        
        all_chats = list(chat_ids_mk.values()) + list(chat_ids_parni.values()) + list(chat_ids_ns.values()) + list(chat_ids_gayznak.values()) + list(chat_ids_rainbow.values())
        unique_chats = set(all_chats)
        
        # Переводим машинные даты в человеческие
        def format_date(d_str):
            try: return datetime.strptime(d_str, "%Y-%m-%d").strftime("%d.%m.%Y")
            except: return "??.??.????"

        # 🔥 НОВАЯ ФУНКЦИЯ: Вычисляет дату итогов (+1 день к концу голосования)
        def get_results_date(d_str):
            try: 
                return (datetime.strptime(d_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%d.%m.%Y")
            except: return "??.??.????"
            
        ss = format_date(data.get("sub_start"))
        se = format_date(data.get("sub_end"))
        vs = format_date(data.get("vote_start"))
        ve = format_date(data.get("vote_end"))
        
        # Получаем дату итогов
        res_date = get_results_date(data.get("vote_end"))
        
        dates_block = (
            f"\n\n⏰ <b>ВАЖНЫЕ ДАТЫ (МСК):</b>\n"
            f"— Прием работ: <b>с {ss} по {se}</b>\n"
            f"— Зрительское голосование: <b>с {vs} по {ve}</b>\n"
            f"— Подведение итогов: <b>{res_date} в 10:30</b>\n" # <--- ДОБАВИЛИ СТРОЧКУ
        )
        
        prize_block = f"\n\n🎁 <b>ПРИЗОВОЙ ФОНД:</b>\n🥇 1 место: {data.get('prize1')}\n🥈 2 место: {data.get('prize2')}\n🥉 3 место: {data.get('prize3')}"
        announcement = data.get("desc") + dates_block + prize_block
        
        # Склеиваем франкенштейна
        announcement = data.get("desc") + dates_block + prize_block
        
        success = 0
        for chat_id in unique_chats:
            try:
                bot.send_message(chat_id, announcement, parse_mode="HTML")
                success += 1
                time.sleep(0.3)
            except: pass
            
        try: bot.send_message(STAFF_GROUP_ID, f"📢 <b>Анонс конкурса успешно разослан в {success} чатов!</b>", parse_mode="HTML")
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

        elif action == "statement":
            logs = list(db['ruble_ledger'].find({"uid": target_uid}).sort("timestamp", -1).limit(40))
            if not logs:
                return jsonify({"success": True, "msg": f"🪹 У пользователя {target_uid} нет истории рублевых операций.", "is_statement": True})
                
            text = f"📜 ВЫПИСКА ЮЗЕРА {target_uid}\n\n"
            total_earned = 0
            for l in logs:
                import datetime
                dt = datetime.datetime.fromtimestamp(l['timestamp']).strftime('%d.%m %H:%M')
                sign = "+" if l['amount'] > 0 else ""
                if l['amount'] > 0: total_earned += l['amount']
                text += f"• {dt} | {sign}{l['amount']}₽ | {l['reason']}\n"
                
            text += f"\n📊 Всего получено (последние 40 опер.): {total_earned} ₽"
            return jsonify({"success": True, "msg": text, "is_statement": True})
            
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
    
    parsed_data = dict(qc.split("=", 1) for qc in unquote(data.get('initData')).split("&"))
    uid = json.loads(parsed_data['user'])['id']
    
    import datetime
    import time
    from config import ADMIN_CHAT_IDS, OWNER_ID
    
    if uid not in ADMIN_CHAT_IDS and uid != OWNER_ID:
        return jsonify({"error": "Доступ запрещен."}), 403

    # 🔥 ВЫНОСИМ ФУНКЦИЮ В САМОЕ НАЧАЛО, ЧТОБЫ НЕ ЛОМАТЬ ЦЕПОЧКУ IF-ELIF 🔥
    def get_real_wealth():
        pts_res = list(paid_collection.aggregate([
            {"$match": {
                "uid": {"$nin": ADMIN_CHAT_IDS}, 
                "bounty_points": {"$gt": 0}
            }},
            {"$group": {"_id": None, "total": {"$sum": "$bounty_points"}}}
        ]))
        
        cb_res = list(paid_collection.aggregate([
            {"$match": {
                "uid": {"$nin": ADMIN_CHAT_IDS}, 
                "cashback_balance": {"$gt": 0}
            }},
            {"$group": {"_id": None, "total": {"$sum": "$cashback_balance"}}}
        ]))
        
        return (
            pts_res[0]["total"] if pts_res else 0,
            cb_res[0]["total"] if cb_res else 0
        )

    # 🔥 ПРАВДОПОДОБНОЕ ОТРИЦАНИЕ: Скрываем финансы от модераторов 🔥
    if stat_type in ["zreport", "bank", "global", "cpa"] and uid != OWNER_ID:
        return jsonify({"text": "🛠 **Ошибка синхронизации кластера.**\n\nФинансовые шарды базы данных временно отключены сервером для создания бэкапа. Попробуйте запросить аналитику позже."})

    # === ТОП 30 БОГАЧЕЙ (ОЧКИ 💎) ===
    if stat_type == "top30_pts":
        exclude_ids = list(set(ADMIN_CHAT_IDS + [OWNER_ID]))
        top_pts = list(paid_collection.find({"uid": {"$nin": exclude_ids}, "bounty_points": {"$gt": 0}}).sort("bounty_points", -1).limit(30))
        
        text = "🏆 **ТОП 30 БОГАЧЕЙ (ОЧКИ 💎)**\n\n"
        for i, u in enumerate(top_pts, 1):
            uid = u['uid']
            u_info = db['users'].find_one({"_id": uid}) or {}
            c_info = db['chat_stats'].find_one({"uid": uid}) or {}
            name = u_info.get("first_name") or c_info.get("name") or "Аноним"
            
            # 🔥 ВЫВОДИМ И ИМЯ, И ID 🔥
            text += f"{i}. {name} [ID: {uid}] — {int(u.get('bounty_points', 0))} 💎\n"
        return jsonify({"text": text})

    # === ТОП 30 ОЛИГАРХОВ (РУБЛИ) ===
    elif stat_type == "top30_rub":
        exclude_ids = list(set(ADMIN_CHAT_IDS + [OWNER_ID]))
        top_rub = list(paid_collection.find({"uid": {"$nin": exclude_ids}, "cashback_balance": {"$gt": 0}}).sort("cashback_balance", -1).limit(30))
        
        text = "💸 **ТОП 30 ОЛИГАРХОВ (РУБЛИ ₽)**\n\n"
        for i, u in enumerate(top_rub, 1):
            uid = u['uid']
            u_info = db['users'].find_one({"_id": uid}) or {}
            c_info = db['chat_stats'].find_one({"uid": uid}) or {}
            name = u_info.get("first_name") or c_info.get("name") or "Аноним"
            
            # 🔥 ВЫВОДИМ И ИМЯ, И ID 🔥
            text += f"{i}. {name} [ID: {uid}] — {int(u.get('cashback_balance', 0))} ₽\n"
        return jsonify({"text": text})

    # === ГЛОБАЛЬНАЯ СВОДКА ===
    elif stat_type == "global":
        total_users = db['users'].count_documents({})
        total_banned = db['banned'].count_documents({})
        
        now_time = time.time()
        webapp_total = db['users'].count_documents({"last_webapp_visit": {"$exists": True}})
        webapp_dau = db['users'].count_documents({"last_webapp_visit": {"$gt": now_time - 86400}})
        active_plots = db['farm_plots'].count_documents({"status": "growing"})
        
        total_points, total_cb = get_real_wealth()
        
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
            f"💰 Очков на руках (у игроков): {total_points} 💎\n"
            f"💸 Кэшбека на руках (у игроков): {total_cb} ₽\n\n"
            f"🎟 Активных артефактов: {active_promos}\n"
            f"📦 Аирдропов в чатах: {active_airdrops}"
        )
        return jsonify({"text": text})

    # === БАНК КАЗИНО ===
    elif stat_type == "bank":
        bank_data = db['casino_bank'].find_one({"_id": "premium_fund"}) or {"balance": 0}
        _, total_cb = get_real_wealth()
        
        text = (
            f"🏦 БАНК КАЗИНО\n\n"
            f"💎 Фонд Premium: {int(bank_data.get('balance', 0))} ⭐️\n"
            f"💸 На руках у юзеров (Кэшбэк): {total_cb} ₽\n\n"
            f"Фонд пополняется на 10% от всех покупок в боте."
        )
        return jsonify({"text": text})
        
    # === CPA СТАТИСТИКА ===
    elif stat_type == "cpa":
        import datetime
        now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5)))
        current_month_str = now.strftime("%Y-%m")
        start_of_month_ts = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
        
        total = db['cpa_traffic'].count_documents({})
        approved = db['cpa_traffic'].count_documents({"status": "approved"})
        hold = db['cpa_traffic'].count_documents({"status": "hold"})
        
        fraud_banned = db['cpa_traffic'].count_documents({"status": "fraud_banned"})
        fraud_left = db['cpa_traffic'].count_documents({"status": "fraud_left"})
        fraud_old = db['cpa_traffic'].count_documents({"status": "fraud"})
        total_fraud = fraud_banned + fraud_left + fraud_old
        
        pipeline = [
            {"$match": {"join_time": {"$gte": start_of_month_ts}}},
            {"$group": {
                "_id": "$agent_id",
                "valid_leads": {"$sum": {"$cond": [{"$in": ["$status", ["approved", "hold"]]}, 1, 0]}},
                "approved": {"$sum": {"$cond": [{"$eq": ["$status", "approved"]}, 1, 0]}},
                "hold": {"$sum": {"$cond": [{"$eq": ["$status", "hold"]}, 1, 0]}},
                "fraud": {"$sum": {"$cond": [{"$in": ["$status", ["fraud_banned", "fraud_left", "fraud"]]}, 1, 0]}}
            }},
            {"$match": {"valid_leads": {"$gt": 0}}}, 
            {"$sort": {"valid_leads": -1}},
            {"$limit": 15}
        ]
        top_agents = list(db['cpa_traffic'].aggregate(pipeline))
        
        text = (
            f"🔗 CPA СТАТИСТИКА (ГЛОБАЛЬНАЯ)\n\n"
            f"👁 Всего заявок (кликов): {total}\n"
            f"⏳ На проверке (Холд 14 дней): {hold}\n"
            f"🚫 Забраковано всего: {total_fraud}\n"
            f"   ├ Сбежали из чата: {fraud_left}\n"
            f"   └ Забанены за спам: {fraud_banned}\n"
            f"✅ ОДОБРЕНО (Лиды за всё время): {approved}\n\n"
            f"🏆 ДЕТАЛЬНЫЙ ТОП АГЕНТОВ ЗА {current_month_str}:\n\n"
        )
        
        if top_agents:
            medals = ["🥇", "🥈", "🥉"]
            for i, agent in enumerate(top_agents):
                agent_id = agent["_id"]
                valid_leads = agent["valid_leads"]
                approved_leads = agent["approved"]
                hold_leads = agent["hold"]
                fraud_leads = agent["fraud"]
                
                u_info = db['users'].find_one({"_id": agent_id}) or {}
                username = u_info.get("username")
                first_name = u_info.get("first_name", "Аноним")
                
                agent_name = f"@{username.lstrip('@')}" if username else first_name
                
                medal = medals[i] if i < 3 else "🔹"
                text += f"{medal} {agent_name} (ID {agent_id})\n"
                text += f"   ├ В зачёт: {valid_leads} (Одобрено: {approved_leads} | Холд: {hold_leads})\n"
                text += f"   └ Брак (сбежали/бан): {fraud_leads}\n\n"
        else:
            text += "В этом месяце пока нет переходов по ссылкам."
            
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
            f"👑 VIP-доступ: {format_money(today_dict, 'vip')} ⭐\n"
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
            f"🏳‍🌈 BEYOND-чат: {format_money(all_dict, 'beyond')} ⭐️\n"
            f"📜 Индульгенции: {format_money(all_dict, 'indulgence')} ⭐️\n"
            f"🛒 Магазин очков: {format_money(all_dict, 'points_shop')} ⭐️\n"
            f"⚖️ Ком-я рынка: {format_money(all_dict, 'market_fee')} ⭐️\n"
            f"💸 Возвраты/Выплаты: {format_money(all_dict, 'refund') + format_money(all_dict, 'payout')} ⭐️\n"
        )
        if all_other != 0: text += f"📦 Прочее: {all_other} ⭐️\n"
        text += f"🏆 ОБЩАЯ КАССА: {sum(all_dict.values())} ⭐️"
        
        return jsonify({"text": text})

@app.route('/api/submit_quest', methods=['POST'])
def api_submit_quest():
    init_data = request.form.get('initData')
    quest_id = request.form.get('quest_id')
    reward_type = request.form.get('reward_type', 'points') # Получаем выбор юзера
    screenshot = request.files.get('screenshot')
    
    if not validate_webapp_data(init_data, BOT_TOKEN): 
        return jsonify({"error": "Auth failed"}), 403
        
    if not screenshot:
        return jsonify({"error": "Файл скриншота не найден!"}), 400
        
    parsed_data = dict(qc.split("=", 1) for qc in unquote(init_data).split("&"))
    user_info = json.loads(parsed_data['user'])
    uid = user_info['id']
    username = user_info.get('username')
    user_name_str = f"@{username}" if username else user_info.get('first_name', 'Аноним')
    
    # Анти-спам защита
    last_quest = db['quests_history'].find_one({"uid": uid, "quest_id": quest_id, "status": "pending"})
    if last_quest:
        return jsonify({"error": "Ваша предыдущая заявка еще на проверке у админов!"}), 400

    import time
    # Сохраняем в базу, ЧТО именно он выбрал
    db['quests_history'].insert_one({
        "uid": uid, "quest_id": quest_id, "reward_type": reward_type, "status": "pending", "timestamp": time.time()
    })
    
    # Перевод для админа (Рубли убрали)
    reward_text = "3000 💎 + 3 🛡"
    if reward_type == 'elite': reward_text = "Статус VIP / BEYOND"
    
    from core.bot import bot
    from config import STAFF_GROUP_ID, PRIZES_THREAD_ID
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("✅ Одобрить", callback_data=f"q_ok_{uid}_{quest_id}"),
        InlineKeyboardButton("❌ Отклонить (Фейк)", callback_data=f"q_no_{uid}_{quest_id}")
    )
    
    try:
        safe_username = user_name_str.replace('_', '\\_')
        bot.send_photo(
            STAFF_GROUP_ID,
            screenshot.read(),
            caption=f"📸 **НОВАЯ ЗАЯВКА НА КВЕСТ**\n\n👤 Юзер: {safe_username} (`{uid}`)\n🛍 Партнер: **ABC Poppers**\n🎁 Хочет: **{reward_text}**\n\n_Проверьте скриншот. Если всё четко, жмите кнопку!_",
            parse_mode="Markdown",
            reply_markup=markup,
            message_thread_id=PRIZES_THREAD_ID
        )
    except Exception as e:
        logger.error(f"Ошибка отправки квеста: {e}")
        db['quests_history'].delete_one({"uid": uid, "quest_id": quest_id, "status": "pending"})
        return jsonify({"error": "Не удалось загрузить фото. Попробуйте сжать скриншот."}), 500
        
    return jsonify({"success": True, "msg": "Скриншот успешно передан в Спецотдел Скайнета! Ожидайте начисления награды."})

@bot.callback_query_handler(func=lambda call: call.data.startswith('q_ok_') or call.data.startswith('q_no_'))
def handle_quest_resolution(call):
    parts = call.data.split('_')
    action = parts[1]  # 'ok' или 'no'
    uid = int(parts[2])
    quest_id = parts[3]
    
    quest = db['quests_history'].find_one_and_update(
        {"uid": uid, "quest_id": quest_id, "status": "pending"},
        {"$set": {"status": "resolved"}}
    )
    
    if not quest:
        bot.answer_callback_query(call.id, "Эта заявка уже обработана!", show_alert=True)
        return

    reward_type = quest.get('reward_type', 'points')

    try:
        # 🔥 БРОНЕБОЙНЫЙ ФИКС: Сначала принудительно стираем кнопки отдельным запросом! 🔥
        try: bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
        except: pass

        if action == 'ok':
            import time
            import random
                
            if reward_type == 'elite':
                u_info = db['users'].find_one({"_id": uid}) or {}
                
                if not u_info.get("is_queer"):
                    code = f"BEYOND-{random.randint(1000, 9999)}"
                    db['promocodes'].insert_one({"_id": code, "type": "percent", "value": 100, "target": "beyond", "usage_limit": 1, "used_count": 0, "is_active": True, "owner_uid": uid})
                    granted = f"Купон 100% на BEYOND 🏳️‍🌈 (Код: `{code}`)"
                    
                elif not u_info.get("is_vip"):
                    code = f"VIP-{random.randint(1000, 9999)}"
                    db['promocodes'].insert_one({"_id": code, "type": "percent", "value": 100, "target": "vip", "usage_limit": 1, "used_count": 0, "is_active": True, "owner_uid": uid})
                    granted = f"Купон 100% на VIP 👑 (Код: `{code}`)"
                    
                else:
                    paid_collection.update_one({"uid": uid}, {"$inc": {"cashback_balance": 500}})
                    db['ruble_ledger'].insert_one({"uid": uid, "amount": 500, "reason": "Квест (Компенсация за фулл-элиту)", "timestamp": time.time()})
                    granted = "500 ₽ (т.к. все статусы уже есть)"
                
                admin_msg = f"Награда ({granted}) выдана!"
                user_msg = f"🎉 **Партнерский Квест выполнен!**\nЧек проверен. Вы получили:\n**{granted}**!\n\n_Ищите купон в Рюкзаке или введите его при оплате в боте._"
                
            else:
                paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": 3000, "immunity": 3}})
                admin_msg = "Награда (3000💎 + 3🛡) выдана!"
                user_msg = "🎉 **Партнерский Квест выполнен!**\nЧек проверен. Награда: **3000 💎 и 3 🛡 Щита**!"
                
            bot.edit_message_caption(f"✅ **ЗАЯВКА ОДОБРЕНА**\n{admin_msg}", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown")
            bot.send_message(uid, user_msg, parse_mode="Markdown")
            
        else:
            bot.edit_message_caption("❌ **ЗАЯВКА ОТКЛОНЕНА**\nПричина: Фейк / Чек недействителен.", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown")
            bot.send_message(uid, "❌ **Заявка на квест отклонена.**\nЧек недействителен, не читается или заказ отменен.", parse_mode="Markdown")
            
    except Exception as e:
        logger.error(f"Ошибка при обработке квеста: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('defuse_'))
def handle_defuse(call):
    parts = call.data.split('_')
    bomb_id = f"{parts[1]}_{parts[2]}"
    color = parts[3]
    
    # Пытаемся захватить бомбу (чтобы 2 человека не нажали одновременно)
    bomb = db['active_bombs'].find_one_and_update(
        {"_id": bomb_id, "status": "active"},
        {"$set": {"status": "defused", "defuser_id": call.from_user.id}}
    )
    
    if not bomb:
        bot.answer_callback_query(call.id, "Слишком поздно! Бомба уже взорвалась или обезврежена!", show_alert=True)
        return
        
    uid = call.from_user.id
    user_name = call.from_user.first_name
    
    # Рандомим правильный провод на лету
    import random
    correct_wire = random.choice(['red', 'blue', 'green'])
    color_emoji = {"red": "🔴 Красный", "blue": "🔵 Синий", "green": "🟢 Зеленый"}[color]
    
    if color == correct_wire:
        # УГАДАЛ!
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": 1000}}, upsert=True)
        text = f"✅ **БОМБА ОБЕЗВРЕЖЕНА!**\n\nГерой [{user_name}](tg://user?id={uid}) перерезал {color_emoji} кабель и сорвал куш в **1000 💎**!\n\n_Чат спасен._"
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode="Markdown")
    else:
        # БАБАХ!
        text = f"💥 **БАБАХ!** 💥\n\nХакер [{user_name}](tg://user?id={uid}) перерезал {color_emoji} кабель... ОШИБКА!\n\nЕму оторвало руки, он отправляется в реанимацию (Мут на 15 минут)."
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode="Markdown")
        mute_user(call.message.chat.id, uid, 15 * 60, "Провал в разминировании бомбы")

# ================= УБИЙЦА ИРИСА (МОДУЛЬ 1: РОЛПЛЕЙ И РАЗДАЧИ) =================

# 1. Интерактивные RP-команды (РАСШИРЕННЫЙ АРСЕНАЛ)
RP_COMMANDS = {
    # Добрые / Дружеские
    "обнять": "🤗 [{name1}](tg://user?id={id1}) тепло обнял(а) [{name2}](tg://user?id={id2})",
    "поцеловать": "💋 [{name1}](tg://user?id={id1}) страстно поцеловал(а) [{name2}](tg://user?id={id2})",
    "погладить": "🐈 [{name1}](tg://user?id={id1}) ласково погладил(а) [{name2}](tg://user?id={id2})",
    "дать пять": "✋ [{name1}](tg://user?id={id1}) дал(а) пять [{name2}](tg://user?id={id2}). Красава!",
    "пожать": "🤝 [{name1}](tg://user?id={id1}) с уважением пожал(а) руку [{name2}](tg://user?id={id2})",
    "укрыть": "🛌 [{name1}](tg://user?id={id1}) заботливо укрыл(а) пледом [{name2}](tg://user?id={id2})",

    # Агрессивные / Смешные
    "укусить": "🧛‍♂️ [{name1}](tg://user?id={id1}) кусьнул(а) [{name2}](tg://user?id={id2}) за бочок",
    "ударить": "🥊 [{name1}](tg://user?id={id1}) прописал(а) мощный хук [{name2}](tg://user?id={id2})",
    "лещ": "🐟 [{name1}](tg://user?id={id1}) отвесил(а) звонкого леща [{name2}](tg://user?id={id2})",
    "пнуть": "🥾 [{name1}](tg://user?id={id1}) дал(а) смачного пинка [{name2}](tg://user?id={id2})",
    "задушить": "🤲 [{name1}](tg://user?id={id1}) начал(а) безжалостно душить [{name2}](tg://user?id={id2})",
    "расстрелять": "🔫 [{name1}](tg://user?id={id1}) выпустил(а) обойму в [{name2}](tg://user?id={id2}). F.",
    "отравить": "🧪 [{name1}](tg://user?id={id1}) подсыпал(а) яд в бокал [{name2}](tg://user?id={id2})",
    "послать": "🖕 [{name1}](tg://user?id={id1}) послал(а) [{name2}](tg://user?id={id2}) куда подальше",

    # Пошловатые / 18+
    "отшлепать": "🍑 [{name1}](tg://user?id={id1}) жестко отшлепал(а) [{name2}](tg://user?id={id2})",
    "связать": "🪢 [{name1}](tg://user?id={id1}) крепко связал(а) [{name2}](tg://user?id={id2})",
    "наказать": "😈 [{name1}](tg://user?id={id1}) жестоко наказал(а) [{name2}](tg://user?id={id2})",
    "лизнуть": "👅 [{name1}](tg://user?id={id1}) облизал(а) [{name2}](tg://user?id={id2})",
    "потрогать": "👉 [{name1}](tg://user?id={id1}) бесстыдно потрогал(а) [{name2}](tg://user?id={id2})",
    "раздеть": "👕 [{name1}](tg://user?id={id1}) стянул(а) одежду с [{name2}](tg://user?id={id2})",
    "трахнуть": "🔞 [{name1}](tg://user?id={id1}) жестко трахнул(а) [{name2}](tg://user?id={id2})",
    "отжарить": "🔥 [{name1}](tg://user?id={id1}) отжарил(а) во все щели [{name2}](tg://user?id={id2})",
    "выебать": "💦 [{name1}](tg://user?id={id1}) выебал(а) без смазки [{name2}](tg://user?id={id2})",
    "нагнуть": "😈 [{name1}](tg://user?id={id1}) нагнул(а) раком [{name2}](tg://user?id={id2})",
    "сдать": "👴 [{name1}](tg://user?id={id1}) сдал(а) в дом престарелых [{name2}](tg://user?id={id2})",
    "посадить на бутылку": "🍾 [{name1}](tg://user?id={id1}) посадил(а) на бутылку [{name2}](tg://user?id={id2})",

    # Бар / Взаимодействия
    "выпить": "🍻 [{name1}](tg://user?id={id1}) чокнулся(лась) бокалами с [{name2}](tg://user?id={id2}). За здоровье!",
    "напоить": "🥃 [{name1}](tg://user?id={id1}) угостил(а) [{name2}](tg://user?id={id2}) элитным коньяком",
    "накормить": "🍔 [{name1}](tg://user?id={id1}) накормил(а) [{name2}](tg://user?id={id2}) шаурмой",
    "украсть": "🥷 [{name1}](tg://user?id={id1}) украл(а) сердечко у [{name2}](tg://user?id={id2})",
    "понюхать": "👃 [{name1}](tg://user?id={id1}) подозрительно обнюхал(а) [{name2}](tg://user?id={id2})"
}

# Ловим команды, но СТРОГО игнорируем системные
@bot.message_handler(func=lambda m: m.reply_to_message and m.text and not m.text.strip().lower().startswith(('!дуэль', 'дуэль', '/duel', '!свадьба', '!брак', '!суд', 'суд', '!усыновить', '!удочерить', '!выгнать', '!отказаться', '!детдом', '!сбежать', '!копилка', '!погасить', 'погасить', '!развести', '!рейд', '!щелчок', '!глас', '!гуантанамо', '!вскрыть', '!создать_нпс', '!профиль', 'профиль', '/profile', '+', '-', '👍', '👎', 'лайк', 'дизлайк', '!донат', 'донат', '!чаевые', 'чаевые', '!перевести', 'перевести', '!pay', 'pay', '!взятка', 'взятка', '!ограбление', 'ограбление', '!в деле', 'в деле', '!побег', 'побег', '!кальмар', 'кальмар', '!играю', 'играю', '!должники', 'должники')))
def handle_rp_commands(message):
    # ЗАБЛОКИРОВАТЬ АНОНИМОВ СРАЗУ
    if message.sender_chat:
        # Отвечаем только если это реально похоже на команду
        if message.text.startswith('!') or any(message.text.lower().startswith(k) for k in RP_COMMANDS.keys()):
            bot.reply_to(message, "👻 Вы пишете от имени группы! Выйдите из анонимного режима, чтобы играть.")
        return

    text_lower = message.text.strip().lower()
    if text_lower.startswith('!'):
        text_lower = text_lower[1:]
        
    cmd = None
    for k in sorted(RP_COMMANDS.keys(), key=len, reverse=True):
        if text_lower.startswith(k):
            cmd = k
            break

    user_data = paid_collection.find_one({"uid": message.from_user.id}) or {}
    karma = user_data.get("social_rating", 0)

    # Блокировка добрых действий для изгоев
    if karma <= -50:
        good_cmds = ["обнять", "поцеловать", "погладить", "дать пять", "пожать", "укрыть"]
        if cmd in good_cmds:
            return bot.reply_to(message, "🚫 <b>ЦЕНЗУРА:</b> Грязным преступникам (Карма < -50) запрещено прикасаться к порядочным гражданам. Вам доступны только агрессивные действия!", parse_mode="HTML")
            
    text_template = None
            
    text_template = None
    if cmd:
        text_template = RP_COMMANDS[cmd]
    else:
        # Проверяем личных NPC
        for npc in db['custom_rp'].find({"uid": message.from_user.id}):
            if text_lower.startswith(npc['cmd']):
                text_template = npc['text']
                cmd = npc['cmd']
                break
                
    if not text_template: return
    
    name1 = message.from_user.first_name
    id1 = message.from_user.id
    name2 = message.reply_to_message.from_user.first_name
    id2 = message.reply_to_message.from_user.id
    
    if id1 == id2:
        bot.reply_to(message, "🤡 Одиночество — это когда ты пытаешься сделать это с самим собой.")
        grant_achievement(id1, "schizo", "Шизофреник", "🤡", message.chat.id)
        return
        
    # 🔥 ХВАТАЕМ ТЕКСТ ПОСЛЕ КОМАНДЫ (ХВОСТ) 🔥
    clean_text = message.text.strip()
    if clean_text.startswith('!'):
        clean_text = clean_text[1:]
        
    extra_text = clean_text[len(cmd):].strip()
    tail = f" {extra_text}" if extra_text else ""
        
    bot.send_message(message.chat.id, text_template.format(name1=name1, id1=id1, name2=name2, id2=id2) + tail, parse_mode="Markdown")

# 1.5 СПРАВОЧНИК КОМАНД ДЛЯ ИГРОКОВ
@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() in ['!рп', 'рп', '/rp'])
def show_rp_list(message):
    text = "🎭 **ДОСТУПНЫЕ RP-КОМАНДЫ СКАЙНЕТА** 🎭\n_Отправьте любое из этих слов в ответ (реплай) на сообщение другого человека:_\n\n"
    text += "🤗 **Добрые:** `обнять`, `поцеловать`, `погладить`, `дать пять`, `пожать`, `укрыть`\n"
    text += "🤬 **Агрессивные:** `ударить`, `пнуть`, `лещ`, `задушить`, `расстрелять`, `отравить`, `послать`\n"
    text += "🔞 **Горячие:** `отшлепать`, `связать`, `наказать`, `лизнуть`, `потрогать`, `раздеть`\n"
    text += "🍻 **Взаимодействие:** `выпить`, `напоить`, `накормить`, `украсть`, `понюхать`, `укусить`\n\n"
    text += "💡 _Подсказка: Вы можете добавлять текст после команды. Например: «Обнять очень крепко!»._"
    
    bot.reply_to(message, text, parse_mode="Markdown")

# ================= УБИЙЦА ИРИСА (МОДУЛЬ 2: КИБЕР-БРАКИ И СИНДИКАТЫ) =================

@bot.message_handler(func=lambda m: m.reply_to_message and m.text and m.text.lower().startswith(('!свадьба', '!брак')))
def propose_marriage(message):
    initiator_id = message.from_user.id
    initiator_name = message.from_user.first_name
    partner_id = message.reply_to_message.from_user.id
    partner_name = message.reply_to_message.from_user.first_name
    
    if initiator_id == partner_id:
        bot.reply_to(message, "🤡 Скайнет не регистрирует браки с самим собой. Найдите кого-нибудь живого!")
        return
        
    if message.reply_to_message.from_user.is_bot:
        bot.reply_to(message, "🤖 Любовь к машинам — это похвально, но незаконно.")
        return
        
    init_data = paid_collection.find_one({"uid": initiator_id}) or {}
    part_data = paid_collection.find_one({"uid": partner_id}) or {}
    
    if init_data.get("partner_id"):
        bot.reply_to(message, "💍 Вы уже состоите в Кибер-Браке! Сначала оформите `!развод`.")
        return
        
    if part_data.get("partner_id"):
        bot.reply_to(message, f"💍 [{partner_name}](tg://user?id={partner_id}) уже состоит в браке! Отбивать чужих партнеров нельзя.", parse_mode="Markdown")
        return
        
    PRICE = 500
    if init_data.get("bounty_points", 0) < PRICE:
        bot.reply_to(message, f"💸 Регистрация Синдиката стоит {PRICE} 💎! У вас недостаточно средств.")
        return
        
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("💖 Согласиться", callback_data=f"marry_yes_{initiator_id}_{partner_id}"),
        InlineKeyboardButton("💔 Отказать", callback_data=f"marry_no_{initiator_id}_{partner_id}")
    )
    
    bot.send_message(
        message.chat.id,
        f"💍 **ПРЕДЛОЖЕНИЕ О КИБЕР-БРАКЕ!**\n\n[{initiator_name}](tg://user?id={initiator_id}) предлагает [{partner_name}](tg://user?id={partner_id}) объединить капиталы и создать Синдикат.\n\n_Пошлина ({PRICE} 💎) будет списана с инициатора._\n\n[{partner_name}](tg://user?id={partner_id}), ваш ответ?",
        parse_mode="Markdown", reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith('marry_'))
def handle_marriage_response(call):
    parts = call.data.split('_')
    action = parts[1]
    initiator_id = int(parts[2])
    partner_id = int(parts[3])
    
    if call.from_user.id != partner_id:
        bot.answer_callback_query(call.id, "Эй! Предложение сделали не вам!", show_alert=True)
        return
        
    PRICE = 500
    init_data = paid_collection.find_one({"uid": initiator_id}) or {}
    
    if init_data.get("partner_id") or (paid_collection.find_one({"uid": partner_id}) or {}).get("partner_id"):
        bot.edit_message_text("❌ Предложение отменено: кто-то из вас уже успел вступить в брак!", call.message.chat.id, call.message.message_id)
        return
        
    if action == 'no':
        bot.edit_message_text(f"💔 **ОТКАЗ!**\n[{call.from_user.first_name}](tg://user?id={partner_id}) отверг(ла) предложение. Капиталы остаются раздельными.", call.message.chat.id, call.message.message_id, parse_mode="Markdown")
        return
        
    if init_data.get("bounty_points", 0) < PRICE:
        bot.edit_message_text("❌ Свадьба отменяется: у инициатора закончились деньги на оплату пошлины!", call.message.chat.id, call.message.message_id)
        return
        
    # Списываем деньги и женим!
    paid_collection.update_one({"uid": initiator_id}, {"$inc": {"bounty_points": -PRICE}, "$set": {"partner_id": partner_id}})
    paid_collection.update_one({"uid": partner_id}, {"$set": {"partner_id": initiator_id}})
    
    bot.edit_message_text(f"🎊 **НОВЫЙ СИНДИКАТ ЗАРЕГИСТРИРОВАН!** 🎊\n\nСкайнет официально объявляет вас Кибер-Партнерами!\n\n_Пошлина {PRICE} 💎 уплачена. Теперь вы одна семья!_", call.message.chat.id, call.message.message_id, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text and m.text.lower() == '!развод')
def divorce(message):
    uid = message.from_user.id
    user_data = paid_collection.find_one({"uid": uid}) or {}
    partner_id = user_data.get("partner_id")
    
    if not partner_id:
        bot.reply_to(message, "🤷‍♂️ Вы не состоите в браке. Разводиться не с кем!")
        return
        
    PENALTY = 1000
    
    # Разводим в базе
    paid_collection.update_one({"uid": uid}, {"$unset": {"partner_id": ""}, "$inc": {"bounty_points": -PENALTY}})
    paid_collection.update_one({"uid": partner_id}, {"$unset": {"partner_id": ""}})
    
    bot.reply_to(message, f"💔 **СИНДИКАТ РАСПАЛСЯ!**\n\nВы расторгли Кибер-Брак с [{partner_id}](tg://user?id={partner_id}).\n_За развод в одностороннем порядке с вас удержан штраф в размере {PENALTY} 💎._", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text and m.text.lower() in ['!брак', '!свадьба'])
def empty_marriage(message):
    if not message.reply_to_message:
        bot.reply_to(message, "💍 Чтобы сделать предложение, отправьте `!свадьба` в ответ (реплай) на сообщение вашего избранника!")

# ================= УБИЙЦА ИРИСА (МОДУЛЬ 3: ТОПЫ И АЗАРТ) =================

# 1. РУССКАЯ РУЛЕТКА (Вирусный PvP-азарт + Трекер Ачивок)
@bot.message_handler(func=lambda m: m.text and m.text.lower() in ['!рулетка', 'рулетка', '/рулетка'])
def russian_roulette(message):
    uid = message.from_user.id
    name = message.from_user.first_name
    import random, time
    
    user_data = paid_collection.find_one({"uid": uid}) or {}
    
    if random.randint(1, 6) == 1:
        # УВЕЛИЧИВАЕМ СЧЕТЧИК СМЕРТЕЙ
        paid_collection.update_one({"uid": uid}, {"$inc": {"roulette_deaths_streak": 1}}, upsert=True)
        new_data = paid_collection.find_one({"uid": uid}) or {}
        
        if user_data.get("immunity", 0) > 0:
            paid_collection.update_one({"uid": uid}, {"$inc": {"immunity": -1}})
            bot.reply_to(message, "💥 **БАБАХ!**\nПуля вылетела, но отрикошетила от **Щита Иммунитета**!\n_Вам повезло. Щит разрушен._", parse_mode="Markdown")
        else:
            bot.reply_to(message, "💥 **БАБАХ!**\nВы словили пулю. Скайнет отправляет вас в реанимацию на 1 час.\n_F._", parse_mode="Markdown")
            mute_user(message.chat.id, uid, 3600, "Смерть в русской рулетке")
            
        # ПРОВЕРЯЕМ АЧИВКУ (3 смерти подряд)
        if new_data.get("roulette_deaths_streak", 0) == 3:
            granted = grant_achievement(uid, "black_streak", "Черная полоса", "🎰", message.chat.id)
            if granted:
                paid_collection.update_one({"uid": uid}, {"$inc": {"immunity": 1}})
                bot.send_message(message.chat.id, "🎁 Скайнет сжалился над вашим невезением и выдал **1 🛡 Щит Иммунитета** в качестве утешения!", parse_mode="Markdown")

    else:
        # ВЫЖИЛ - ОБНУЛЯЕМ СЧЕТЧИК СМЕРТЕЙ
        reward = random.randint(5, 15)
        bonus_text = ""
        
        # 🔥 ВЛИЯНИЕ КАРМЫ: Х2 НАГРАДА ЗА ВЫЖИВАНИЕ 🔥
        if user_data.get("social_rating", 0) >= 50:
            reward *= 2
            bonus_text = "\n🌟 _Бонус за Отличную Карму (x2)!_"
            
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": reward}, "$set": {"roulette_deaths_streak": 0}}, upsert=True)
        bot.reply_to(message, f"😅 *Щелк...* Осечка!\n[{name}](tg://user?id={uid}) выживает и получает **+{reward} 💎**.{bonus_text}", parse_mode="Markdown")

# 2. РЕЙТИНГ АКТИВНОСТИ ЧАТА
@bot.message_handler(func=lambda m: m.text and m.text.lower() in ['!топ', 'топ чата', '/top'])
def chat_top_activity(message):
    top_users = list(db['chat_stats'].find({"chat_id": message.chat.id}).sort("msgs", -1).limit(10))
    if not top_users: return bot.reply_to(message, "🪹 В этом чате еще никто ничего не писал.")
        
    text = f"🏆 **ТОП БОЛТУНОВ ЧАТА** 🏆\n\n"
    medals = ["🥇", "🥈", "🥉"]
    for i, u in enumerate(top_users):
        medal = medals[i] if i < 3 else f"{i+1}."
        text += f"{medal} **{u.get('name', 'Аноним')}** — {u.get('msgs', 0)} сообщ.\n"
    bot.reply_to(message, text, parse_mode="Markdown")

# --- СЕКРЕТНЫЙ РАЗДАВАТЕЛЬ АЧИВОК ---
def grant_achievement(uid, ach_id, ach_name, ach_icon, chat_id):
    user_data = paid_collection.find_one({"uid": uid}) or {}
    achievements = user_data.get("achievements", [])
    
    if ach_id not in achievements:
        paid_collection.update_one({"uid": uid}, {"$push": {"achievements": ach_id}})
        try:
            from core.bot import bot
            bot.send_message(chat_id, f"🏆 **ДОСТИЖЕНИЕ РАЗБЛОКИРОВАНО!**\nВы получили значок: {ach_icon} **«{ach_name}»**!", parse_mode="Markdown")
        except: pass
        return True
    return False

# 3. ТЕКСТОВЫЙ ПРОФИЛЬ (Бронебойная версия)
@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() in ['!профиль', 'профиль', '/profile', '!profile'])
def text_profile(message):
    try:
        target_user = message.reply_to_message.from_user if message.reply_to_message else message.from_user
        uid = target_user.id
        
        user_data = paid_collection.find_one({"uid": uid}) or {}
        msgs_data = db['chat_stats'].find_one({"chat_id": message.chat.id, "uid": uid}) or {}
        msgs = msgs_data.get("msgs", 0)
        
        pts = user_data.get("bounty_points", 0)
        rub = user_data.get("cashback_balance", 0)
        karma = user_data.get("social_rating", 0)
        partner_id = user_data.get("partner_id")
        partner_text = f"В браке с ID {partner_id}" if partner_id else "Одинок(а)"
        title = "👑 VIP-Персона" if user_data.get("is_vip") else "🔴 Гражданин Империи"
        
        # 🔥 АВТО-ТИТУЛЫ ПО КАРМЕ 🔥
        if karma >= 100: title = "😇 Святой (Неприкасаемый)"
        elif karma <= -100: title = "💀 Враг Народа (Опущенный)"
        elif karma <= -50: title = "🗑 Изгой (Под надзором)"
        
        # Сборка ачивок
        ach_map = {
            "schizo": "🤡", "black_streak": "🎰", "gladiator": "⚔️", 
            "santa": "🎅", "safecracker": "🏦", "patriarch": "👨‍👩‍👧‍👦",
            "drought": "💩", "rat": "🔪", "cuckold": "🦌", "bankrupt": "📉"
        }
        achievements = user_data.get("achievements", [])
        ach_text = " ".join([ach_map.get(a, "") for a in achievements if a in ach_map])
        if not ach_text: ach_text = "Нет наград"
        
        gold_status = "⚜️ [ВЛАДЕЛЕЦ ЗОЛОТА]\n" if user_data.get("golden_frame") else ""
        
        text = (
            f"👤 **ДОСЬЕ СКАЙНЕТА: {target_user.first_name}**\n"
            f"{gold_status}🔑 **ID:** `{uid}`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🎖 **Статус:** {title}\n"
            f"💰 **Счет:** {pts} 💎 | {rub} ₽\n"
            f"🎭 **Карма:** {karma}\n"
            f"💬 **Написано тут:** {msgs} сообщений\n"
            f"💍 **Семья:** {partner_text}\n"
            f"🏆 **Зал Славы:** {ach_text}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🎮 _Полный инвентарь — в Web App_"
        )
        bot.reply_to(message, text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Ошибка в !профиль: {e}")
        bot.reply_to(message, "⚠️ Скайнет временно потерял базу данных профилей. Попробуйте еще раз.")

# 5. КИБЕР-ДУЭЛИ (PvP на ставки)
@bot.message_handler(func=lambda m: m.reply_to_message and m.text and m.text.lower().startswith(('!дуэль', 'дуэль', '/duel')))
def challenge_duel(message):
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        bot.reply_to(message, "⚠️ **Формат:** `!дуэль [ставка]` в ответ на сообщение противника.\n_Пример:_ `!дуэль 100`")
        return
        
    bet = int(parts[1])
    if bet < 10:
        bot.reply_to(message, "📉 Минимальная ставка: 10 💎")
        return
        
    challenger_id = message.from_user.id
    challenger_name = message.from_user.first_name
    target_id = message.reply_to_message.from_user.id
    target_name = message.reply_to_message.from_user.first_name
    
    if challenger_id == target_id:
        bot.reply_to(message, "🤡 Вызывать на дуэль самого себя — признак шизофрении.")
        return
        
    if message.reply_to_message.from_user.is_bot:
        bot.reply_to(message, "🤖 Терминаторы не играют в кости.")
        return
        
    # Проверяем баланс вызывающего
    ch_data = paid_collection.find_one({"uid": challenger_id}) or {}
    if ch_data.get("bounty_points", 0) < bet:
        bot.reply_to(message, f"💸 У вас нет {bet} 💎 для такой ставки!")
        return
        
    # Проверяем баланс цели (чтобы не спамили бедняков)
    tg_data = paid_collection.find_one({"uid": target_id}) or {}
    if tg_data.get("bounty_points", 0) < bet:
        bot.reply_to(message, f"📉 У противника нет {bet} 💎. Ищите кого-то побогаче!")
        return
        
    import time
    duel_id = f"duel_{int(time.time())}_{challenger_id}_{target_id}"
    
    # Записываем дуэль в базу (ожидание)
    db['active_duels'].insert_one({
        "_id": duel_id,
        "challenger_id": challenger_id,
        "challenger_name": challenger_name,
        "target_id": target_id,
        "target_name": target_name,
        "bet": bet,
        "status": "pending"
    })
    
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("⚔️ Принять вызов", callback_data=f"duel_accept_{duel_id}"),
        InlineKeyboardButton("🏃‍♂️ Струсить", callback_data=f"duel_decline_{duel_id}")
    )
    
    bot.send_message(
        message.chat.id,
        f"⚔️ **ДУЭЛЬ!**\n\n[{challenger_name}](tg://user?id={challenger_id}) бросает вызов [{target_name}](tg://user?id={target_id})!\n💰 **Ставка:** {bet} 💎\n\n_Победитель забирает всё (комиссия арены 5%)._",
        parse_mode="Markdown", reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith('duel_'))
def handle_duel_response(call):
    parts = call.data.split('_')
    action = parts[1]
    
    # 🔥 ИСПРАВЛЕННАЯ СТРОКА: Берем куски 2, 3, 4 и 5! 🔥
    duel_id = f"{parts[2]}_{parts[3]}_{parts[4]}_{parts[5]}"
    
    duel = db['active_duels'].find_one({"_id": duel_id, "status": "pending"})
    if not duel:
        bot.answer_callback_query(call.id, "Дуэль уже завершена или отменена!", show_alert=True)
        return
        
    target_id = duel['target_id']
    challenger_id = duel['challenger_id']
    
    if call.from_user.id != target_id and call.from_user.id != challenger_id:
        bot.answer_callback_query(call.id, "Это не ваша дуэль! Проходите мимо.", show_alert=True)
        return
        
    if action == "decline":
        if call.from_user.id == target_id:
            db['active_duels'].update_one({"_id": duel_id}, {"$set": {"status": "declined"}})
            bot.edit_message_text(f"🏃‍♂️ [{duel['target_name']}](tg://user?id={target_id}) испугался(лась) и сбежал(а) с арены. Дуэль отменена.", call.message.chat.id, call.message.message_id, parse_mode="Markdown")
        elif call.from_user.id == challenger_id:
            db['active_duels'].update_one({"_id": duel_id}, {"$set": {"status": "cancelled"}})
            bot.edit_message_text(f"🏳️ [{duel['challenger_name']}](tg://user?id={challenger_id}) отозвал(а) свой вызов.", call.message.chat.id, call.message.message_id, parse_mode="Markdown")
        return
        
    if action == "accept" and call.from_user.id != target_id:
        bot.answer_callback_query(call.id, "Только вызванный игрок может принять дуэль!", show_alert=True)
        return
        
    # === НАЧАЛО БОЯ ===
    bet = duel['bet']
    
    # Финальная проверка балансов перед боем
    ch_data = paid_collection.find_one({"uid": challenger_id}) or {}
    tg_data = paid_collection.find_one({"uid": target_id}) or {}
    
    if ch_data.get("bounty_points", 0) < bet or tg_data.get("bounty_points", 0) < bet:
        db['active_duels'].update_one({"_id": duel_id}, {"$set": {"status": "error"}})
        bot.edit_message_text("❌ У кого-то из участников не хватает очков для ставки! Дуэль аннулирована.", call.message.chat.id, call.message.message_id)
        return
        
    # Бросаем кубики
    import random
    ch_roll = random.randint(1, 100)
    tg_roll = random.randint(1, 100)
    
    # Если ничья - перебрасываем, чтобы был явный победитель
    while ch_roll == tg_roll:
        tg_roll = random.randint(1, 100)
        
    # Определяем победителя
    if ch_roll > tg_roll:
        winner_id, loser_id = challenger_id, target_id
        winner_name, loser_name = duel['challenger_name'], duel['target_name']
        win_roll, lose_roll = ch_roll, tg_roll
    else:
        winner_id, loser_id = target_id, challenger_id
        winner_name, loser_name = duel['target_name'], duel['challenger_name']
        win_roll, lose_roll = tg_roll, ch_roll
        
    # Расчет банка (комиссия 5% сгорает из экономики или уходит в синий сейф)
    # Расчет банка (комиссия 5% сгорает из экономики или уходит в синий сейф)
    commission = int((bet * 2) * 0.05)
    if commission < 1: commission = 1
    prize = (bet * 2) - commission
    pay_casino_owner(int(bet * 0.10))
    
    # Транзакции и ТРЕКЕРЫ
    import datetime, time
    now_date = datetime.datetime.now().strftime("%Y-%m-%d")
    
    paid_collection.update_one({"uid": winner_id}, {
        "$inc": {"bounty_points": (prize - bet), "duel_win_streak": 1},
        "$set": {"daily_duel_date": now_date}
    })
    
    l_data = paid_collection.find_one({"uid": loser_id}) or {}
    if l_data.get("daily_duel_date") != now_date:
        paid_collection.update_one({"uid": loser_id}, {"$set": {"daily_duel_losses": bet, "daily_duel_date": now_date, "duel_win_streak": 0}, "$inc": {"bounty_points": -bet}})
    else:
        paid_collection.update_one({"uid": loser_id}, {"$inc": {"bounty_points": -bet, "daily_duel_losses": bet}, "$set": {"duel_win_streak": 0}})

    w_data = paid_collection.find_one({"uid": winner_id})
    if w_data.get("duel_win_streak", 0) == 10:
        grant_achievement(winner_id, "gladiator", "Гладиатор", "⚔️", call.message.chat.id)
        
    l_data = paid_collection.find_one({"uid": loser_id})
    if l_data.get("daily_duel_losses", 0) >= 10000:
        grant_achievement(loser_id, "bankrupt", "Банкрот", "📉", call.message.chat.id)
        paid_collection.update_one({"uid": loser_id}, {"$set": {"bankrupt_until": time.time() + 86400}})
    
    # Комиссию кидаем в Синий Сейф, чтобы он рос быстрее!
    db['safes_state'].update_one({"_id": "safe_blue"}, {"$inc": {"balance": commission}})
    db['active_duels'].update_one({"_id": duel_id}, {"$set": {"status": "completed"}})
    
    result_text = (
        f"⚔️ **ДУЭЛЬ ЗАВЕРШЕНА!** ⚔️\n\n"
        f"🎲 [{duel['challenger_name']}](tg://user?id={challenger_id}) выбросил(а): **{ch_roll}**\n"
        f"🎲 [{duel['target_name']}](tg://user?id={target_id}) выбросил(а): **{tg_roll}**\n\n"
        f"🏆 **ПОБЕДИТЕЛЬ:** [{winner_name}](tg://user?id={winner_id})!\n"
        f"💰 Забрал(а) куш: **{prize} 💎** _(Комиссия: {commission} 💎)_"
    )
    
    bot.edit_message_text(result_text, call.message.chat.id, call.message.message_id, parse_mode="Markdown")

# 2. Пользовательские Аирдропы (Замена Мешкам Ириса)
@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith(('!раздача', '/раздача', 'раздача')))
def handle_user_airdrop(message):
    parts = message.text.split()
    if len(parts) != 3:
        bot.reply_to(message, "⚠️ **Формат:** `!раздача [сумма] [кол-во людей]`\n_Пример:_ `!раздача 1000 5` (1000 очков разделят 5 человек)", parse_mode="Markdown")
        return
        
    try:
        total_amount = int(parts[1])
        max_users = int(parts[2])
    except ValueError:
        bot.reply_to(message, "⚠️️ Сумма и количество должны быть числами!")
        return
        
    if total_amount < 50 or max_users < 2 or max_users > 50:
        bot.reply_to(message, "⚠️ Минимум 50 💎, от 2 до 50 человек!")
        return
        
    uid = message.from_user.id
    user_name = message.from_user.first_name
    
    # 🔥 ИСПРАВЛЕННАЯ ПРОВЕРКА БАЛАНСА 🔥
    updated_user = paid_collection.find_one_and_update(
        {"uid": uid, "bounty_points": {"$gte": total_amount}},
        {"$inc": {"bounty_points": -total_amount}}
    )
    
    if not updated_user:
        bot.reply_to(message, "❌ У вас недостаточно Очков Бдительности для такой раздачи! Проверьте баланс в Кабинете.")
        return
        
    # ТРЕКЕР: Санта-Клаус
    paid_collection.update_one({"uid": uid}, {"$inc": {"total_airdrop_given": total_amount}})
    new_data = paid_collection.find_one({"uid": uid})
    if new_data.get("total_airdrop_given", 0) >= 50000:
        grant_achievement(uid, "santa", "Санта-Клаус", "🎅", message.chat.id)
        
    import time
    piece = total_amount // max_users
    drop_id = f"userdrop_{int(time.time())}_{uid}"
    
    db['active_airdrops'].insert_one({
        "_id": drop_id,
        "sponsor_id": uid,
        "sponsor_name": user_name,
        "total": total_amount,
        "piece": piece,
        "max_users": max_users,
        "claimed_by": []
    })
    
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton(f"🎁 Забрать {piece} 💎", callback_data=f"claim_udrop_{drop_id}"))
    
    bot.send_message(
        message.chat.id, 
        f"👑 **КИТ В ЧАТЕ!**\n\n[{user_name}](tg://user?id={uid}) скинул мешок с Очками!\n💰 **Фонд:** {total_amount} 💎\n👥 **Хватит на:** {max_users} чел.\n\n_Жми кнопку, пока не разобрали!_", 
        parse_mode="Markdown", reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith('claim_udrop_'))
def handle_claim_userdrop(call):
    drop_id = call.data.replace('claim_udrop_', '')
    uid = call.from_user.id
    
    # Блокируем доступ для Ириса и других ботов
    if call.from_user.is_bot: return
    
    drop = db['active_airdrops'].find_one({"_id": drop_id})
    if not drop:
        bot.answer_callback_query(call.id, "Мешок уже пуст или исчез!", show_alert=True)
        return
        
    if uid in drop['claimed_by']:
        bot.answer_callback_query(call.id, "Вы уже взяли свою долю из этого мешка!", show_alert=True)
        return
        
    if len(drop['claimed_by']) >= drop['max_users']:
        bot.answer_callback_query(call.id, "Слишком поздно! Мешок уже расхватали.", show_alert=True)
        return
        
    # Выдаем награду нажавшему
    paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": drop['piece']}}, upsert=True)
    
    # Обновляем базу мешка
    db['active_airdrops'].update_one(
        {"_id": drop_id},
        {"$push": {"claimed_by": uid}}
    )
    
    bot.answer_callback_query(call.id, f"✅ Вы урвали {drop['piece']} 💎!", show_alert=True)
    
    # Если мешок опустел - меняем сообщение в чате
    if len(drop['claimed_by']) + 1 >= drop['max_users']:
        bot.edit_message_text(
            f"🎒 **МЕШОК ПУСТ!**\n\n[{drop['sponsor_name']}](tg://user?id={drop['sponsor_id']}) раздал {drop['total']} 💎!\nВсе {drop['max_users']} долей успешно разобраны.",
            call.message.chat.id, call.message.message_id, parse_mode="Markdown"
        )
        db['active_airdrops'].delete_one({"_id": drop_id})

@bot.message_handler(func=lambda m: m.reply_to_message and m.text and m.text.lower().startswith(('!донат', '!чаевые', '!перевести', '!pay', 'донат', 'чаевые', 'перевести', 'pay')))
def p2p_transfer(message):
    initiator_id = message.from_user.id
    initiator_name = message.from_user.first_name
    target_id = message.reply_to_message.from_user.id
    target_name = message.reply_to_message.from_user.first_name
    
    if initiator_id == target_id:
        return bot.reply_to(message, "🤡 Вы пытаетесь переложить деньги из одного кармана в другой.")
    if message.reply_to_message.from_user.is_bot:
        return bot.reply_to(message, "🤖 Скайнет не принимает чаевые. Мы принимаем только человеческие души.")
        
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        return bot.reply_to(message, "⚠️️ **Формат:** `!чаевые [сумма]` в ответ на сообщение.\n_Пример:_ `!чаевые 100`", parse_mode="Markdown")
        
    amount = int(parts[1])
    if amount < 10:
        return bot.reply_to(message, "📉 Минимальная сумма перевода: 10 💎")
        
    user_data = paid_collection.find_one({"uid": initiator_id}) or {}
    if user_data.get("bounty_points", 0) < amount:
        return bot.reply_to(message, f"💸 У вас нет {amount} 💎 для перевода!")
        
    # Налог Скайнета (5%)
    commission = int(amount * 0.05)
    if commission < 1: commission = 1
    final_amount = amount - commission
    
    # Атомарное списание (дополнительная защита)
    updated_user = paid_collection.find_one_and_update(
        {"uid": initiator_id, "bounty_points": {"$gte": amount}},
        {"$inc": {"bounty_points": -amount}}
    )
    if not updated_user:
        return bot.reply_to(message, "❌ Транзакция отклонена. Недостаточно средств.")
        
    # Начисление получателю
    paid_collection.update_one({"uid": target_id}, {"$inc": {"bounty_points": final_amount}}, upsert=True)
    
    # Комиссия улетает в Синий Сейф
    db['safes_state'].update_one({"_id": "safe_blue"}, {"$inc": {"balance": commission}})
    
    # Рандомные атмосферные фразы
    import random
    phrases = [
        f"💸 [{initiator_name}](tg://user?id={initiator_id}) щедро отблагодарил(а) [{target_name}](tg://user?id={target_id}) на **{amount} 💎**!",
        f"👙 [{initiator_name}](tg://user?id={initiator_id}) игриво засунул(а) **{amount} 💎** в трусики [{target_name}](tg://user?id={target_id}).",
        f"🤝 [{initiator_name}](tg://user?id={initiator_id}) жмет руку и переводит [{target_name}](tg://user?id={target_id}) **{amount} 💎**.",
        f"🔥 [{initiator_name}](tg://user?id={initiator_id}) спонсирует [{target_name}](tg://user?id={target_id}) на **{amount} 💎**!"
    ]
    
    msg_text = random.choice(phrases) + f"\n\n_Получено: {final_amount} 💎 (Комиссия Скайнета: {commission} 💎)_"
    bot.send_message(message.chat.id, msg_text, parse_mode="Markdown")

# ================= УБИЙЦА ИРИСА (МОДУЛЬ 4: СЕМЬИ И КЛАНЫ) =================

@bot.message_handler(func=lambda m: m.reply_to_message and m.text and m.text.lower().startswith(('!усыновить', '!удочерить')))
def adopt_child(message):
    parent_id = message.from_user.id
    parent_name = message.from_user.first_name
    child_id = message.reply_to_message.from_user.id
    child_name = message.reply_to_message.from_user.first_name
    
    if parent_id == child_id: return bot.reply_to(message, "🤡 Вы не бактерия, чтобы размножаться делением.")
    if message.reply_to_message.from_user.is_bot: return bot.reply_to(message, "🤖 Терминаторы не подлежат усыновлению.")
        
    child_data = paid_collection.find_one({"uid": child_id}) or {}
    if child_data.get("parent_id"):
        return bot.reply_to(message, f"👶 [{child_name}](tg://user?id={child_id}) уже находится под опекой другого человека!", parse_mode="Markdown")
        
    parent_data = paid_collection.find_one({"uid": parent_id}) or {}
    children = parent_data.get("children", [])
    if len(children) >= 3:
        return bot.reply_to(message, "🚫 Лимит: максимум 3 ребенка на одного опекуна! Частный детдом закрыт.")
        
    PRICE = 1000
    if parent_data.get("bounty_points", 0) < PRICE:
        return bot.reply_to(message, f"💸 Оформление документов стоит {PRICE} 💎! У вас нет денег на содержание ребенка.")
        
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
    markup = InlineKeyboardMarkup().add(
        InlineKeyboardButton("🍼 Войти в семью", callback_data=f"adopt_yes_{parent_id}_{child_id}"),
        InlineKeyboardButton("🏃‍♂️ Убежать", callback_data=f"adopt_no_{parent_id}_{child_id}")
    )
    bot.send_message(message.chat.id, f"🍼 **ПРОЦЕДУРА УСЫНОВЛЕНИЯ!**\n\n[{parent_name}](tg://user?id={parent_id}) хочет официально усыновить [{child_name}](tg://user?id={child_id}).\n\n_Пошлина ({PRICE} 💎) будет списана с опекуна._\n\n[{child_name}](tg://user?id={child_id}), вы согласны?", parse_mode="Markdown", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('adopt_'))
def handle_adopt_response(call):
    parts = call.data.split('_')
    action, parent_id, child_id = parts[1], int(parts[2]), int(parts[3])
    
    if call.from_user.id != child_id:
        return bot.answer_callback_query(call.id, "Вас не пытаются усыновить! Отойдите.", show_alert=True)
        
    if action == 'no':
        return bot.edit_message_text(f"🏃‍♂️ **ОТКАЗ!**\n[{call.from_user.first_name}](tg://user?id={child_id}) сбежал(а) из детдома. Усыновление отменено.", call.message.chat.id, call.message.message_id, parse_mode="Markdown")
        
    PRICE = 1000
    parent_data = paid_collection.find_one({"uid": parent_id}) or {}
    if parent_data.get("bounty_points", 0) < PRICE:
        return bot.edit_message_text("❌ У опекуна кончились деньги! Усыновление отменено.", call.message.chat.id, call.message.message_id)
        
    # Проверяем, не усыновили ли ребенка пока он думал
    if (paid_collection.find_one({"uid": child_id}) or {}).get("parent_id"):
        return bot.edit_message_text("❌ Ребенка уже забрала другая семья!", call.message.chat.id, call.message.message_id)
        
    # Жесткая запись в базу: списываем деньги, ставим parent_id ребенку, добавляем child_id в массив родителя
    paid_collection.update_one({"uid": parent_id}, {"$inc": {"bounty_points": -PRICE}, "$push": {"children": child_id}})
    paid_collection.update_one({"uid": child_id}, {"$set": {"parent_id": parent_id}})
    
    # ТРЕКЕР: Патриарх
    p_data = paid_collection.find_one({"uid": parent_id}) or {}
    if p_data.get("partner_id") and len(p_data.get("children", [])) >= 3:
        grant_achievement(parent_id, "patriarch", "Патриарх семьи", "👨‍👩‍👧‍👦", call.message.chat.id)
        grant_achievement(p_data["partner_id"], "patriarch", "Патриарх семьи", "👨‍👩‍‍👧‍👦", call.message.chat.id)

    bot.edit_message_text(f"🎊 **НОВАЯ КИБЕР-СЕМЬЯ!** 🎊\n\nСкайнет официально поздравляет!\n[{call.from_user.first_name}](tg://user?id={child_id}) теперь является наследником.\n\n_Напишите `!семья`, чтобы посмотреть ваше древо._", call.message.chat.id, call.message.message_id, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text and m.text.lower() in ['!семья', 'моя семья', '/family'])
def my_family_tree(message):
    uid = message.from_user.id
    user_data = paid_collection.find_one({"uid": uid}) or {}
    
    partner_id = user_data.get("partner_id")
    parent_id = user_data.get("parent_id")
    children = user_data.get("children", [])
    
    if not partner_id and not parent_id and not children:
        return bot.reply_to(message, "🕸 Вы сирота и одиночка. Ни мужа/жены, ни родителей, ни детей.\n\n_Напишите `!свадьба` в ответ кому-нибудь или `!усыновить`._", parse_mode="Markdown")
        
    text = f"🌳 **ГЕНЕАЛОГИЧЕСКОЕ ДРЕВО** 🌳\n\n👤 **Вы:** [{message.from_user.first_name}](tg://user?id={uid})\n"
    
    if parent_id:
        p_name = (db['chat_stats'].find_one({"uid": parent_id}) or {}).get("name", "Опекун")
        text += f"👑 **Родитель:** [{p_name}](tg://user?id={parent_id})\n"
        
        # 🔥 НОВОЕ: ИЩЕМ ОТЧИМА / РОДИТЕЛЯ №2 🔥
        p_data = paid_collection.find_one({"uid": parent_id}) or {}
        stepfather_id = p_data.get("partner_id")
        if stepfather_id:
            sf_name = (db['chat_stats'].find_one({"uid": stepfather_id}) or {}).get("name", "Отчим")
            text += f"👨‍👨‍👦 **Отчим / Родитель №2:** [{sf_name}](tg://user?id={stepfather_id})\n"
        
    if partner_id:
        part_name = (db['chat_stats'].find_one({"uid": partner_id}) or {}).get("name", "Супруг(а)")
        text += f"💍 **В браке с:** [{part_name}](tg://user?id={partner_id})\n"
        
    if children:
        text += f"👶 **Наследники ({len(children)}/3):**\n"
        for child_id in children:
            c_name = (db['chat_stats'].find_one({"uid": child_id}) or {}).get("name", "Ребенок")
            text += f" ├─ [{c_name}](tg://user?id={child_id})\n"
            
    bot.reply_to(message, text, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.reply_to_message and m.text and m.text.lower().startswith(('!выгнать', '!отказаться', '!детдом')))
def kick_child(message):
    parent_id = message.from_user.id
    child_id = message.reply_to_message.from_user.id
    
    p_data = paid_collection.find_one({"uid": parent_id}) or {}
    children = p_data.get("children", [])
    
    if child_id not in children:
        return bot.reply_to(message, "🤡 Этот пользователь не является вашим ребенком.")
        
    # Удаляем связи
    paid_collection.update_one({"uid": parent_id}, {"$pull": {"children": child_id}})
    paid_collection.update_one({"uid": child_id}, {"$unset": {"parent_id": ""}})
    
    bot.reply_to(message, f"💔 **СЕМЬЯ РАСПАЛАСЬ!**\n\n[{message.from_user.first_name}](tg://user?id={parent_id}) лишил(а) наследства [{message.reply_to_message.from_user.first_name}](tg://user?id={child_id}) и выгнал(а) на улицу.\n_Теперь он(а) снова сирота._", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text and m.text.lower() in ['!сбежать', 'сбежать'])
def run_away_child(message):
    child_id = message.from_user.id
    child_name = message.from_user.first_name
    
    # 1. Проверяем, есть ли вообще семья
    child_data = paid_collection.find_one({"uid": child_id}) or {}
    parent_id = child_data.get("parent_id")
    
    if not parent_id:
        return bot.reply_to(message, "🤡 Вы сирота! От кого сбегать-то?")
        
    parent_data = paid_collection.find_one({"uid": parent_id}) or {}
    partner_id = parent_data.get("partner_id")
    
    # 2. Удаляем связи (побег)
    paid_collection.update_one({"uid": parent_id}, {"$pull": {"children": child_id}})
    paid_collection.update_one({"uid": child_id}, {"$unset": {"parent_id": ""}})
    
    stolen_amount = 0
    # 3. Пытаемся обнести копилку (если родители в браке и есть общак)
    if partner_id:
        family_id = f"family_{min(parent_id, partner_id)}_{max(parent_id, partner_id)}"
        fam_db = db['family_banks'].find_one({"_id": family_id}) or {"balance": 0}
        current_bank = fam_db.get("balance", 0)
        
        if current_bank > 0:
            import random
            # Кубик от 10% до 50%
            steal_pct = random.uniform(0.10, 0.50)
            stolen_amount = int(current_bank * steal_pct)
            if stolen_amount < 1: stolen_amount = 1
            
            # Списываем из копилки, зачисляем ребенку на карман
            db['family_banks'].update_one({"_id": family_id}, {"$inc": {"balance": -stolen_amount}})
            paid_collection.update_one({"uid": child_id}, {"$inc": {"bounty_points": stolen_amount}})
            
    # 4. Объявляем на весь чат
    if stolen_amount > 0:
        msg = f"🏃‍♂️💨 **ПОБЕГ ИЗ ДОМА!**\n\nТрудный подросток [{child_name}](tg://user?id={child_id}) сбежал(а) из семьи!\nПеред уходом он(а) взломал(а) родительский сейф и украл(а) **{stolen_amount} 💎** из общего бюджета!\n\n_Родители в шоке, полиция разводит руками._"
    else:
        msg = f"🏃‍♂️💨 **ПОБЕГ ИЗ ДОМА!**\n\nПодросток [{child_name}](tg://user?id={child_id}) собрал(а) вещи и сбежал(а) из семьи.\nОн(а) хотел(а) обнести родительскую копилку, но там оказалось пусто...\n\n_Теперь он(а) снова на улицах._"
        
    bot.reply_to(message, msg, parse_mode="Markdown")
    
    # 5. Кидаем инфарктное уведомление родителю в ЛС
    try:
        from core.bot import bot
        bot.send_message(parent_id, f"🚨 **ВАШ РЕБЕНОК СБЕЖАЛ!**\n\n[{child_name}](tg://user?id={child_id}) покинул семью. Проверьте ваш Семейный Фонд, кажется, оттуда пропали сбережения...", parse_mode="Markdown")
    except:
        pass

# ================= ПРАВО ВЕТО (ДЛЯ СВЯТЫХ) =================
@bot.message_handler(func=lambda m: m.reply_to_message and m.text and m.text.lower().startswith(('!амнистия', 'амнистия')))
def elite_amnesty(message):
    uid = message.from_user.id
    target_id = message.reply_to_message.from_user.id
    
    user_data = paid_collection.find_one({"uid": uid}) or {}
    karma = user_data.get("social_rating", 0)
    
    if karma < 100:
        return bot.reply_to(message, "⚖️ Право Вето доступно только Святым (Карма 100+). Очистите свою душу!")
        
    import time
    # Списываем 20 кармы
    paid_collection.update_one({"uid": uid}, {"$inc": {"social_rating": -20}})
    # Снимаем все муты
    db['skynet_tasks'].insert_one({"uid": target_id, "action": "full_unban", "timestamp": time.time()})
    paid_collection.update_one({"uid": target_id}, {"$unset": {"guantanamo_until": ""}})
    
    bot.reply_to(message, f"🕊 <b>ПРАВО ВЕТО ПРИМЕНЕНО!</b>\n\nСвятой гражданин пожертвовал 20 Кармы, чтобы очистить грехи <a href='tg://user?id={target_id}'>{message.reply_to_message.from_user.first_name}</a>!\n\n<i>Тюремные замки открыты. Все блокировки сняты.</i>", parse_mode="HTML")

# ================= ИСКУПЛЕНИЕ ГРЕХОВ (ДЛЯ ИЗГОЕВ) =================
@bot.message_handler(func=lambda m: m.text and m.text.lower() in ['!искупление', 'искупление'])
def redeem_sins(message):
    uid = message.from_user.id
    user_data = paid_collection.find_one({"uid": uid}) or {}
    karma = user_data.get("social_rating", 0)
    
    if karma >= 0:
        return bot.reply_to(message, "😇 Ваша душа и так чиста. Искупление доступно только грешникам с отрицательной Кармой.")
        
    shields = user_data.get("immunity", 0)
    shards = user_data.get("jackpot_shards", 0)
    
    # Пытаемся забрать ресурсы за Карму
    if shields >= 1:
        paid_collection.update_one({"uid": uid}, {"$inc": {"immunity": -1, "social_rating": 20}})
        cost_text = "1 🛡 Щит Иммунитета"
    elif shards >= 5:
        paid_collection.update_one({"uid": uid}, {"$inc": {"jackpot_shards": -5, "social_rating": 20}})
        cost_text = "5 🧩 Осколков"
    else:
        return bot.reply_to(message, "⛓ <b>Вам нечем платить за свои грехи!</b>\nДля искупления требуется пожертвовать государству <b>1 🛡 Щит</b> или <b>5 🧩 Осколков</b>.", parse_mode="HTML")

    # 🔥 ЕСЛИ ВЫШЕЛ ИЗ МИНУСА ПОСЛЕ ИСКУПЛЕНИЯ - ВОССТАНАВЛИВАЕМ ТЕГ 🔥
    if karma <= -50 and (karma + 20) > -50:
        u_info = db['users'].find_one({"_id": uid}) or {}
        original_tag = u_info.get("custom_tag", "")
        try:
            from handlers.admin import safe_set_tag
            safe_set_tag(message.chat.id, uid, original_tag)
        except: pass
        
    bot.reply_to(message, f"⛪️ <b>ИСКУПЛЕНИЕ ПРОЙДЕНО!</b>\n\nВы пожертвовали {cost_text} на благо Империи.\nСкайнет списывает часть ваших грехов: <b>+20 к Карме</b>!", parse_mode="HTML")

# ================= ГЛОБАЛЬНЫЙ ИВЕНТ: АНАРХИЯ =================
@bot.message_handler(func=lambda m: m.text and m.text.lower() == '!анархия')
def trigger_anarchy(message):
    uid = message.from_user.id
    user_data = paid_collection.find_one({"uid": uid}) or {}
    
    PRICE = 10000
    if user_data.get("bounty_points", 0) < PRICE:
        return bot.reply_to(message, f"💀 Обрушение серверов Скайнета стоит {PRICE} 💎! Копите деньги, мистер Хакер.")
        
    import time
    active_anarchy = db['settings'].find_one({"_id": "anarchy_mode"})
    if active_anarchy and active_anarchy.get("end_time", 0) > time.time():
        return bot.reply_to(message, "🔥 Анархия УЖЕ идет! Хватайте вилы и бегите грабить!")
        
    # Списываем 10к, запускаем Анархию на 1 час
    paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": -PRICE}})
    db['settings'].update_one({"_id": "anarchy_mode"}, {"$set": {"active": True, "end_time": time.time() + 3600}}, upsert=True)
    
    # Оповещаем все чаты
    def broadcast_anarchy():
        from config import chat_ids_mk, chat_ids_parni, chat_ids_ns, chat_ids_gayznak, chat_ids_rainbow
        all_chats = list(set(list(chat_ids_mk.values()) + list(chat_ids_parni.values()) + list(chat_ids_ns.values()) + list(chat_ids_gayznak.values()) + list(chat_ids_rainbow.values())))
        
        msg_text = (
            f"🏴‍☠️ <b>КРИТИЧЕСКИЙ СБОЙ МАТРИЦЫ! АНАРХИЯ!</b> 🏴‍☠️\n\n"
            f"Хакер <a href='tg://user?id={uid}'>{message.from_user.first_name}</a> сжег 10 000 💎 и обрушил сервера Скайнета на 1 ЧАС!\n\n"
            f"<b>ПРАВИЛА СУДНОЙ НОЧИ:</b>\n"
            f"🩸 Кулдаун Взлома сейфов снижен до 15 минут!\n"
            f"🩸 Шанс успешного Взлома повышен до 80%!\n"
            f"🩸 Защиты Кармы больше не существует!\n\n"
            f"<i>Заходите в Web App (Рюкзак -> Взлом) и грабьте соседей, пока система не перезагрузится!</i>"
        )
        for cid in all_chats:
            try: bot.send_message(cid, msg_text, parse_mode="HTML"); time.sleep(0.3)
            except: pass
            
    import threading
    threading.Thread(target=broadcast_anarchy, daemon=True).start()

# ================= НАРОДНЫЙ СУД (СБОР НА КИЛЛЕРА) =================
@bot.message_handler(func=lambda m: m.reply_to_message and m.text and m.text.lower().startswith(('!суд', 'суд')))
def public_court(message):
    initiator_id = message.from_user.id
    target_id = message.reply_to_message.from_user.id
    target_name = message.reply_to_message.from_user.first_name
    
    if initiator_id == target_id:
        return bot.reply_to(message, "🤡 Вызывать полицию на самого себя? Оригинально.")
    if message.reply_to_message.from_user.is_bot:
        return bot.reply_to(message, "🤖 Скайнет не подсуден человеческим законам.")
        
    user_data = paid_collection.find_one({"uid": initiator_id}) or {}
    if user_data.get("bounty_points", 0) < 100:
        return bot.reply_to(message, "💸 У вас нет стартовых 100 💎 для открытия дела!")
        
    # Списываем 100 💎 у инициатора
    paid_collection.update_one({"uid": initiator_id}, {"$inc": {"bounty_points": -100}})

    # ТРЕКЕР: Крыса
    paid_collection.update_one({"uid": initiator_id}, {"$addToSet": {"sued_users": target_id}})
    u_data = paid_collection.find_one({"uid": initiator_id})
    if len(u_data.get("sued_users", [])) >= 5:
        grant_achievement(initiator_id, "rat", "Крыса", "🔪", message.chat.id)
    
    import time
    court_id = f"court_{int(time.time())}_{target_id}"
    target_data = paid_collection.find_one({"uid": target_id}) or {}
    target_karma = target_data.get("social_rating", 0)
    
    GOAL = 1000
    extra_msg = ""
    if target_karma >= 50:
        GOAL = 2000
        extra_msg = "\n🌟 <i>Цель имеет безупречную репутацию! Подкупить Скайнет будет в 2 раза дороже.</i>"
    elif target_karma <= -50:
        GOAL = 500
        extra_msg = "\n👿 <i>Обвиняемый — известная угроза обществу! Народ готов скинуться по дешевке.</i>"
    
    import time
    court_id = f"court_{int(time.time())}_{target_id}"
    
    db['active_courts'].insert_one({
        "_id": court_id,
        "target_id": target_id,
        "target_name": target_name,
        "collected": 100,
        "goal": GOAL,
        "investors": [initiator_id]
    })
    
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton(f"⚖️ Докинуть 100 💎 (Собрано: 100/{GOAL})", callback_data=f"court_fund_{court_id}"))
    
    bot.send_message(
        message.chat.id, 
        f"🚨 <b>НАРОДНЫЙ СУД ОТКРЫТ!</b> 🚨\n\n<a href='tg://user?id={initiator_id}'>{message.from_user.first_name}</a> требует забанить <a href='tg://user?id={target_id}'>{target_name}</a> на 1 час!\n\n💰 Цель сбора: <b>{GOAL} 💎</b> для подкупа Скайнета.{extra_msg}\n<i>Жмите кнопку, чтобы пожертвовать 100 очков на правосудие.</i>", 
        parse_mode="HTML", reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith('court_fund_'))
def handle_court_funding(call):
    court_id = call.data.replace('court_fund_', '')
    uid = call.from_user.id
    
    court = db['active_courts'].find_one({"_id": court_id})
    if not court:
        return bot.answer_callback_query(call.id, "Дело уже закрыто!", show_alert=True)
        
    if uid == court['target_id']:
        return bot.answer_callback_query(call.id, "Подсудимый не имеет права финансировать свой арест!", show_alert=True)
        
    if uid in court['investors']:
        return bot.answer_callback_query(call.id, "Вы уже внесли свою долю! Ждите других.", show_alert=True)
        
    user_data = paid_collection.find_one({"uid": uid}) or {}
    if user_data.get("bounty_points", 0) < 100:
        return bot.answer_callback_query(call.id, "Не хватает 100 💎 на балансе!", show_alert=True)
        
    # Списываем бабки и плюсуем в котел
    paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": -100}})
    new_collected = court['collected'] + 100
    db['active_courts'].update_one({"_id": court_id}, {"$set": {"collected": new_collected}, "$push": {"investors": uid}})
    
    if new_collected >= court['goal']:
        # ПРИГОВОР ИСПОЛНЕН! Выдаем мут на 1 час
        db['active_courts'].delete_one({"_id": court_id})
        
        # Сжигаем собранную сумму в экономике (или кидаем в синий сейф)
        db['safes_state'].update_one({"_id": "safe_blue"}, {"$inc": {"balance": new_collected}})
        
        bot.edit_message_text(f"⚖️ **СУД ВЕРШИЛСЯ!**\n\nНеобходимая сумма в **{court['goal']} 💎** собрана!\n[{court['target_name']}](tg://user?id={court['target_id']}) признан виновным народным голосованием и отправляется за решетку на 1 час.\n\n_Правосудие восторжествовало. Деньги переведены в Сейф Скайнета._", call.message.chat.id, call.message.message_id, parse_mode="Markdown")
        
        # 👇 ВОТ ЭТУ СТРОКУ НУЖНО ДОБАВИТЬ СЮДА 👇
        mute_user(call.message.chat.id, court['target_id'], 3600, "Осужден Народным Судом")
        
    else:
        # Обновляем кнопку
        from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
        markup = InlineKeyboardMarkup().add(InlineKeyboardButton(f"⚖️ Докинуть 100 💎 (Собрано: {new_collected}/{court['goal']})", callback_data=f"court_fund_{court_id}"))
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
        except:
            pass
        bot.answer_callback_query(call.id, "Ваши 100 💎 приняты в фонд правосудия!", show_alert=True)

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith(('!взятка', 'взятка')))
def bribe_court(message):
    uid = message.from_user.id
    parts = message.text.split()
    
    if len(parts) < 2 or not parts[1].isdigit():
        return bot.reply_to(message, "⚠️ **Формат:** `!взятка [сумма]`\n_Пример:_ `!взятка 1500`", parse_mode="Markdown")
        
    bribe_amount = int(parts[1])
    
    # Ищем, есть ли открытое дело на этого юзера
    court = db['active_courts'].find_one({"target_id": uid})
    if not court:
        return bot.reply_to(message, "🤡 Против вас нет открытых судебных дел. Кому вы собрались платить?")
        
    collected = court.get("collected", 0)
    if bribe_amount <= collected:
        return bot.reply_to(message, f"📉 Маловато будет! Народ уже собрал **{collected} 💎**. Взятка должна перебить эту сумму!")
        
    user_data = paid_collection.find_one({"uid": uid}) or {}
    if user_data.get("bounty_points", 0) < bribe_amount:
        return bot.reply_to(message, f"💸 У вас нет {bribe_amount} 💎 для взятки! Вас посадят.")
        
    # Списываем взятку
    paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": -bribe_amount}})
    
    # Удаляем суд
    db['active_courts'].delete_one({"_id": court["_id"]})
    
    # Взятка + собранные народом деньги улетают в Синий Сейф Скайнета (коррупция!)
    total_to_safe = bribe_amount + collected
    db['safes_state'].update_one({"_id": "safe_blue"}, {"$inc": {"balance": total_to_safe}})
    
    bot.send_message(
        message.chat.id,
        f"💼 **ДЕЛО ЗАКРЫТО ЗА НЕДОСТАТКОМ УЛИК!**\n\n[{message.from_user.first_name}](tg://user?id={uid}) занес Скайнету чемодан с **{bribe_amount} 💎**.\nКоррумпированный судья ударил молотком, толпа негодует, обвиняемый свободен!\n\n_Взятка и собранные народом деньги ({total_to_safe} 💎) отправлены в Синий Сейф._",
        parse_mode="Markdown"
    )

# ================= ТЕНЕВЫЕ АРТЕФАКТЫ (ХАОС) =================

def resolve_target_uid(target_info):
    """Секретный локатор: преобразует @username или ID в чистый UID"""
    t_info = str(target_info).strip()
    if t_info.isdigit(): return int(t_info)
    if t_info.startswith('@'):
        uname = t_info.replace('@', '').lower()
        u = db['users'].find_one({"username": uname})
        if u: return u['_id']
        cs = db['chat_stats'].find_one({"username": uname})
        if cs: return cs['uid']
    return None

@bot.message_handler(func=lambda m: m.text and m.text.lower() == '!щелчок')
def use_thanos_glove(message):
    uid = message.from_user.id
    user_data = paid_collection.find_one({"uid": uid}) or {}
    
    if "thanos_glove" not in user_data.get("elite_items", []):
        return bot.reply_to(message, "🕸 У вас нет Перчатки Таноса! Добудьте её на Теневом Аукционе.")
        
    # Забираем перчатку
    paid_collection.update_one({"uid": uid}, {"$pull": {"elite_items": "thanos_glove"}})
    
    active_users = list(db['chat_stats'].find({"chat_id": message.chat.id}).sort("msgs", -1).limit(30))
    if len(active_users) < 4:
        bot.reply_to(message, "🕸 В этом чате слишком мало людей для щелчка. Перчатка сгорела впустую.")
        return
        
    def execute_snap():
        import time, random
        targets = random.sample(active_users, k=len(active_users)//2)
        muted_names = []
        until = int(time.time()) + (15 * 60)
        
        bot.send_message(message.chat.id, f"🧤 <b>{message.from_user.first_name} надевает Перчатку Бесконечности...</b>", parse_mode="HTML")
        time.sleep(3)
        bot.send_message(message.chat.id, "🫰 <i>*ЩЕЛК*</i>", parse_mode="HTML")
        time.sleep(2)
        
        for t in targets:
            # Защита от мута админов (Telegram API само выдаст ошибку, мы ее гасим)
            if t['uid'] == uid: continue
            
            # 🔥 ИММУНИТЕТ ДЛЯ СВЯТЫХ 🔥
            t_data = paid_collection.find_one({"uid": t['uid']}) or {}
            if t_data.get("social_rating", 0) >= 100:
                bot.send_message(message.chat.id, f"🛡 <b>Сбой матрицы!</b> Гражданин {t.get('name', 'Аноним')} имеет статус Святого (Карма 100+). Перчатка не смогла стереть его!", parse_mode="HTML")
                continue
                
            success = mute_user(message.chat.id, t['uid'], 15 * 60, "Рассыпался от Щелчка Таноса")
            if success:
                muted_names.append(t.get('name', 'Аноним'))
            
        if muted_names:
            bot.send_message(message.chat.id, "💨 <b>Половина активных участников рассыпалась в прах (Мут 15 минут):</b>\n" + ", ".join(muted_names), parse_mode="HTML")
        else:
            bot.send_message(message.chat.id, "🛡 У Скайнета слишком сильная защита в этом чате. Никто не рассыпался.")
            
    import threading
    threading.Thread(target=execute_snap, daemon=True).start()

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('!развести'))
def homewrecker_action(message):
    uid = message.from_user.id
    user_data = paid_collection.find_one({"uid": uid}) or {}
    
    if "homewrecker" not in user_data.get("elite_items", []):
        return bot.reply_to(message, "💔 У вас нет артефакта «Разлучник»! Загляните на Аукцион.")
        
    parts = message.text.split()
    if len(parts) < 2:
        return bot.reply_to(message, "⚠️ Укажите цель: `!развести @username`", parse_mode="Markdown")
        
    target_info = parts[1]
    target_id = resolve_target_uid(target_info)
    
    if not target_id:
        return bot.reply_to(message, "❌ Пользователь не найден в базе!")
        
    target_data = paid_collection.find_one({"uid": target_id}) or {}
    partner_id = target_data.get("partner_id")
    
    if not partner_id:
        return bot.reply_to(message, "🤡 Этот пользователь и так одинок. Разводить некого.")
        
    # Забираем артефакт
    paid_collection.update_one({"uid": uid}, {"$pull": {"elite_items": "homewrecker"}})
    
    # Принудительный развод
    paid_collection.update_one({"uid": target_id}, {"$unset": {"partner_id": ""}})
    paid_collection.update_one({"uid": partner_id}, {"$unset": {"partner_id": ""}})
    
    # Вешаем клеймо Рогоносца на 7 дней
    import time
    paid_collection.update_one({"uid": target_id}, {
        "$set": {"cuckold_until": time.time() + (7 * 86400)},
        "$push": {"achievements": "cuckold"}
    })
    
    bot.send_message(message.chat.id, f"💔 <b>АРТЕФАКТ ПРИМЕНЕН!</b>\n\n[{message.from_user.first_name}](tg://user?id={uid}) использовал <b>«Разлучник»</b> на {target_info}!\n\nСиндикат разрушен. Брак аннулирован без согласия сторон.\n🦌 <i>Жертве выдан позорный статус «Рогоносец» на 7 дней.</i>", parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('!рейд'))
def raider_takeover(message):
    uid = message.from_user.id
    user_data = paid_collection.find_one({"uid": uid}) or {}
    
    if "raider" not in user_data.get("elite_items", []):
        return bot.reply_to(message, "🧲 У вас нет лицензии Рейдера! Ищите её на Теневом Аукционе.")
        
    parts = message.text.split()
    if len(parts) < 2:
        return bot.reply_to(message, "⚠️ Укажите цель: `!рейд @username`", parse_mode="Markdown")
        
    target_id = resolve_target_uid(parts[1])
    if not target_id:
        return bot.reply_to(message, "❌ Цель не найдена!")
        
    if target_id == uid:
        return bot.reply_to(message, "🤡 Вы не можете ограбить самого себя.")
        
    ready_plots = list(db['farm_plots'].find({"uid": target_id, "status": "ready"}))
    if not ready_plots:
        # Сжигаем артефакт впустую
        paid_collection.update_one({"uid": uid}, {"$pull": {"elite_items": "raider"}})
        return bot.reply_to(message, "🪹 <b>ПРОВАЛ!</b> У жертвы нет созревшего урожая. Рейдеры ушли ни с чем, а артефакт сгорел.", parse_mode="HTML")
        
    # Собираем урожай
    paid_collection.update_one({"uid": uid}, {"$pull": {"elite_items": "raider"}})
    total_pts = 0
    
    for plot in ready_plots:
        seed = plot['seed_type']
        # Пропускаем декор (каштаны, кактусы), воруем только то, что приносит очки!
        if seed in CROPS and not CROPS[seed].get('is_decor'):
            import random
            pts = random.randint(CROPS[seed]['reward_pts'][0], CROPS[seed]['reward_pts'][1])
            total_pts += pts
        
        db['farm_plots'].update_one({"_id": plot["_id"]}, {"$set": {"status": "empty", "seed_type": None, "fertilized": False}})
        
    if total_pts > 0:
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": total_pts}})
        bot.send_message(message.chat.id, f"🧲 <b>РЕЙДЕРСКИЙ ЗАХВАТ!</b>\n\n[{message.from_user.first_name}](tg://user?id={uid}) ворвался на ферму {parts[1]} пока тот спал!\n🚜 <b>Украдено грядок:</b> {len(ready_plots)}\n💰 <b>Награбленное:</b> {total_pts} 💎", parse_mode="HTML")
    else:
        bot.send_message(message.chat.id, f"🧲 <b>РЕЙДЕРСКИЙ ЗАХВАТ!</b>\n\n[{message.from_user.first_name}](tg://user?id={uid}) ворвался на ферму {parts[1]}, но там росли только кактусы да каштаны. Очков не заработано, но грядки разорены!", parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('!глас '))
def gods_voice(message):
    uid = message.from_user.id
    if "gods_voice" not in (paid_collection.find_one({"uid": uid}) or {}).get("elite_items", []): 
        return bot.reply_to(message, "📢 У вас нет артефакта «Глас Бога»!")
    text = message.text[6:].strip()
    if not text: return
    paid_collection.update_one({"uid": uid}, {"$pull": {"elite_items": "gods_voice"}})
    
    def broadcast():
        from config import chat_ids_mk, chat_ids_parni, chat_ids_ns, chat_ids_gayznak, chat_ids_rainbow
        import time
        all_chats = list(set(list(chat_ids_mk.values()) + list(chat_ids_parni.values()) + list(chat_ids_ns.values()) + list(chat_ids_gayznak.values()) + list(chat_ids_rainbow.values())))
        msg_text = f"👑 <b>Глобальное послание от {message.from_user.first_name}:</b>\n\n{html.escape(text)}"
        for cid in all_chats:
            try: bot.send_message(cid, msg_text, parse_mode="HTML"); time.sleep(0.3)
            except: pass
        bot.send_message(message.chat.id, "✅ Глас Бога услышан во всех чатах!")
    import threading
    threading.Thread(target=broadcast, daemon=True).start()

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('!гуантанамо'))
def guantanamo_order(message):
    uid = message.from_user.id
    if "guantanamo" not in (paid_collection.find_one({"uid": uid}) or {}).get("elite_items", []): 
        return bot.reply_to(message, "🚷 У вас нет Ордера Гуантанамо!")
    parts = message.text.split()
    if len(parts) < 2: return bot.reply_to(message, "Укажите цель: !гуантанамо @username")
    target_id = resolve_target_uid(parts[1])
    if not target_id: return bot.reply_to(message, "❌ Пользователь не найден!")
    
    paid_collection.update_one({"uid": uid}, {"$pull": {"elite_items": "guantanamo"}})
    import time
    until = int(time.time()) + 86400
    # Флаг, блокирующий снятие Ангелом
    paid_collection.update_one({"uid": target_id}, {"$set": {"guantanamo_until": until}})
    mute_user(message.chat.id, target_id, 86400, "Ордер Гуантанамо")
    bot.send_message(message.chat.id, f"🚷 <b>Ордер Гуантанамо применен!</b>\n\n{parts[1]} отправлен в изолятор на 24 часа. Ангелы и Индульгенции бессильны.", parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('!вскрыть'))
def master_key_safe(message):
    uid = message.from_user.id
    if "master_key" not in (paid_collection.find_one({"uid": uid}) or {}).get("elite_items", []): 
        return bot.reply_to(message, "🗝 У вас нет Мастер-Ключа!")
    parts = message.text.lower().split()
    if len(parts) < 2 or parts[1] not in ["синий", "красный"]:
        return bot.reply_to(message, "Формат: !вскрыть синий (или красный)")
    
    safe_color = "blue" if parts[1] == "синий" else "red"
    safe_id = f"safe_{safe_color}"
    safe = db['safes_state'].find_one({"_id": safe_id})
    prize = safe['balance']
    
    paid_collection.update_one({"uid": uid}, {"$pull": {"elite_items": "master_key"}})
    
    if safe_color == 'blue':
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": prize}})
        currency = "💎"
    else:
        paid_collection.update_one({"uid": uid}, {"$inc": {"cashback_balance": prize}})
        currency = "₽"
        
    import random
    pin_len = 3 if safe_color == 'blue' else 4
    new_pin = "".join([str(random.randint(0, 9)) for _ in range(pin_len)])
    db['safes_state'].update_one({"_id": safe_id}, {"$set": {"pin_code": new_pin, "balance": 10000 if safe_color == 'blue' else 500, "logs": []}})
    
    bot.send_message(message.chat.id, f"🗝 <b>Мастер-Ключ провернулся... ЩЕЛК!</b>\n\n[{message.from_user.first_name}](tg://user?id={uid}) вскрыл {parts[1]} сейф без пин-кода и забрал <b>{prize} {currency}</b>!", parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('!создать_нпс'))
def create_personal_npc(message):
    uid = message.from_user.id
    if "personal_npc" not in (paid_collection.find_one({"uid": uid}) or {}).get("elite_items", []): return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3: return bot.reply_to(message, "Формат: !создать_нпс [слово] [текст]\nПример: !создать_нпс леха [{name1}] позвал Леху, и тот налил [{name2}] пива")
    
    paid_collection.update_one({"uid": uid}, {"$pull": {"elite_items": "personal_npc"}})
    db['custom_rp'].insert_one({"uid": uid, "cmd": parts[1].lower(), "text": parts[2]})
    bot.reply_to(message, f"🤖 Ваш личный NPC создан! Теперь вы можете писать `!{parts[1].lower()}` в ответ на сообщения.")

# 6. ГЛАВНОЕ МЕНЮ КОМАНД СКАЙНЕТА
@bot.message_handler(func=lambda m: m.text and m.text.lower() in ['!команды', '/help', 'помощь', 'команды', '!help'])
def help_commands(message):
    text = (
        "🤖 **БАЗА ДАННЫХ СКАЙНЕТА (КОМАНДЫ)** 🤖\n\n"
        "💍 **КИБЕР-СЕМЬЯ:**\n"
        "• `!свадьба` *(в ответ)* — сделать предложение\n"
        "• `!развод` — расторгнуть брак (штраф 1000 💎)\n"
        "• `!усыновить` *(в ответ)* — взять ребенка в семью\n"
        "• `!выгнать` *(в ответ)* — лишить наследства и выгнать\n"
        "• `!сбежать` — покинуть семью (с шансом обнести копилку)\n"
        "• `!семья` — генеалогическое древо\n"
        "• `!копилка [сумма]` — положить Очки в семейный фонд\n"
        "• `!копилка снять [сумма]` — взять из фонда\n\n"
        "🎲 **АЗАРТ И ФИНАНСЫ:**\n"
        "• `!дуэль [ставка]` *(в ответ)* — битва на Очки\n"
        "• `!раздача [сумма] [кол-во]` — скинуть мешок с 💎 в чат\n"
        "• `!чаевые [сумма]` *(в ответ)* — подарить Очки юзеру\n"
        "• `!рулетка` — выжить или словить мут (награда 5-15 💎)\n"
        "• `!ограбление` — собрать банду для налета на Фин. Сейф\n"
        "• `!в деле` — присоединиться к банде (нужно 3 чел)\n"
        "• `!погасить` — досрочно оплатить кредит МФО\n\n"
        "⚖️ **ПРАВОСУДИЕ И ХАОС:**\n"
        "• `!суд` *(в ответ)* — начать сбор на арест юзера\n"
        "• `!взятка [сумма]` — откупиться от суда, перебив фонд\n"
        "• `!побег @user` — вытащить друга из мута/тюрьмы\n"
        "• `!кальмар` — запустить игру на выживание (1000 💎)\n"
        "• `!играю` — вступить в Игру в Кальмара\n"
        "• `+`, `-`, `лайк`, `дизлайк` *(в ответ)* — Карму\n"
        "• `!амнистия` *(в ответ)* — снять наказание с друга (Только для Святых)\n"
        "• `!искупление` — отмыть грехи и карму (за 1 Щит / 5 Осколков)\n"
        "• `!анархия` — запустить Судную Ночь во всех чатах (10 000 💎)\n\n"
        "🔮 **ТЕНЕВЫЕ АРТЕФАКТЫ (АУКЦИОН):**\n"
        "• `!щелчок` — замутить половину чата на 15 мин\n"
        "• `!развести @user` — расторгнуть чужой брак\n"
        "• `!рейд @user` — украсть урожай с чужой фермы\n"
        "• `!глас [текст]` — послание во все чаты сети\n"
        "• `!гуантанамо @user` — неснимаемый мут на 24ч\n"
        "• `!вскрыть [синий/красный]` — вскрыть сейф без пин-кода\n"
        "• `!создать_нпс [слово] [текст]` — создать личную RP-команду\n\n"
        "👤 **ПРОФИЛЬ И ОБЩЕНИЕ:**\n"
        "• `!профиль` *(можно в ответ)* — досье и балансы\n"
        "• `!топ` — топ болтунов чата\n"
        "• `!рп` — список интерактивных действий\n\n"
        "🎮 **ИГРОВОЙ КАБИНЕТ:**\n"
        "Напишите `/start` в личку боту, чтобы открыть Web App!"
    )
    
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
    markup = InlineKeyboardMarkup().add(
        InlineKeyboardButton("🎮 Открыть Кабинет", url=f"https://t.me/{bot.get_me().username}?start=app_profile")
    )
    
    bot.reply_to(message, text, parse_mode="Markdown", reply_markup=markup)

# ================= СОЦИАЛЬНЫЙ КРЕДИТ И КАРМА =================
# ================= СОЦИАЛЬНЫЙ КРЕДИТ И КАРМА =================
@bot.message_handler(func=lambda m: m.reply_to_message and m.text and m.text.strip().lower() in ['+', '-', '👍', '👎', 'лайк', 'дизлайк'])
def handle_karma_vote(message):
    vote = message.text.strip().lower()
    voter_id = message.from_user.id
    target_id = message.reply_to_message.from_user.id
    target_name = message.reply_to_message.from_user.first_name
    
    if voter_id == target_id:
        return bot.reply_to(message, "🚫 Скайнет запрещает накручивать рейтинг самому себе!")
    if message.reply_to_message.from_user.is_bot:
        return bot.reply_to(message, "🤖 У программного кода нет социальных прав!")
        
    import time
    now = time.time()
    vote_key = f"karma_{voter_id}_{target_id}"
    last_vote = db['settings'].find_one({"_id": vote_key})
    
    if last_vote and (now - last_vote.get('time', 0) < 3600):
        left_mins = int((3600 - (now - last_vote['time'])) / 60)
        return bot.reply_to(message, f"⏳ Вы уже оценивали этого гражданина! Система примет ваш следующий голос через {left_mins} мин.")
        
    db['settings'].update_one({"_id": vote_key}, {"$set": {"time": now}}, upsert=True)
    
    if vote in ['+', '👍', 'лайк']:
        paid_collection.update_one({"uid": target_id}, {"$inc": {"social_rating": 1}}, upsert=True)
        new_karma = (paid_collection.find_one({"uid": target_id}) or {}).get("social_rating", 0)
        
        # 🔥 ВОССТАНАВЛИВАЕМ ОБЫЧНЫЙ ТЕГ (ИЛИ ПУСТОТУ), ЕСЛИ КАРМА ВЫШЛА ИЗ МИНУСА 🔥
        if new_karma == -49:
            u_info = db['users'].find_one({"_id": target_id}) or {}
            original_tag = u_info.get("custom_tag", "")
            try:
                from handlers.admin import safe_set_tag
                safe_set_tag(message.chat.id, target_id, original_tag)
            except: pass
            
        bot.reply_to(message, f"📈 **Социальный Кредит повышен!**\nГражданин [{target_name}](tg://user?id={target_id}) получает +1 к карме.\n_Текущий рейтинг: {new_karma}_", parse_mode="Markdown")
    else:
        paid_collection.update_one({"uid": target_id}, {"$inc": {"social_rating": -1}}, upsert=True)
        new_karma = (paid_collection.find_one({"uid": target_id}) or {}).get("social_rating", 0)
        
        # 🔥 ВЕШАЕМ НАСТОЯЩЕЕ СИСТЕМНОЕ КЛЕЙМО В ТЕЛЕГРАМЕ 🔥
        if new_karma <= -50:
            bad_title = "Опущенный" if new_karma <= -100 else "Изгой"
            try:
                from handlers.admin import safe_set_tag
                safe_set_tag(message.chat.id, target_id, bad_title)
            except: pass
            
        bot.reply_to(message, f"📉 **Внимание, нарушение!**\nГражданин [{target_name}](tg://user?id={target_id}) получает -1 к карме.\n_Текущий рейтинг: {new_karma}_", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith(('!копилка', 'копилка')))
def family_piggy_bank(message):
    uid = message.from_user.id
    user_data = paid_collection.find_one({"uid": uid}) or {}
    partner_id = user_data.get("partner_id")
    
    if not partner_id:
        return bot.reply_to(message, "🕸 У вас нет Синдиката! Общий счет доступен только в браке.")
        
    # Формируем уникальный ID семьи (сортируем ID, чтобы у обоих был одинаковый ключ)
    family_id = f"family_{min(uid, partner_id)}_{max(uid, partner_id)}"
    fam_db = db['family_banks'].find_one({"_id": family_id}) or {"balance": 0}
    current_bank = fam_db.get("balance", 0)
    
    parts = message.text.lower().split()
    
    # ПРОСТО "!КОПИЛКА" - Баланс
    if len(parts) == 1:
        return bot.reply_to(message, f"💍 **СЕМЕЙНЫЙ ФОНД**\n\nТекущий баланс: **{current_bank} 💎**\n\n_Пополнить:_ `!копилка 100`\n_Снять:_ `!копилка снять 100`", parse_mode="Markdown")
        
    # СНЯТИЕ ДЕНЕГ
    if parts[1] == "снять":
        if len(parts) < 3 or not parts[2].isdigit():
            return bot.reply_to(message, "⚠️ Укажите сумму: `!копилка снять 500`")
            
        amount = int(parts[2])
        if amount > current_bank:
            return bot.reply_to(message, f"📉 В копилке нет столько денег! Там всего {current_bank} 💎.")
            
        db['family_banks'].update_one({"_id": family_id}, {"$inc": {"balance": -amount}})
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": amount}})
        bot.reply_to(message, f"💸 Вы забрали **{amount} 💎** из семейного фонда!\n_Остаток: {current_bank - amount} 💎_", parse_mode="Markdown")
        
    # ПОПОЛНЕНИЕ КОПИЛКИ
    elif parts[1].isdigit():
        amount = int(parts[1])
        if amount < 10:
            return bot.reply_to(message, "📉 Минимальный вклад: 10 💎")
            
        if user_data.get("bounty_points", 0) < amount:
            return bot.reply_to(message, "❌ У вас нет столько Очков на руках!")
            
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": -amount}})
        db['family_banks'].update_one({"_id": family_id}, {"$inc": {"balance": amount}}, upsert=True)
        bot.reply_to(message, f"🏦 Вы положили **{amount} 💎** в семейный фонд!\n_Всего накоплено: {current_bank + amount} 💎_", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text and m.text.lower() in ['!погасить', 'погасить'])
def pay_debt_chat(message):
    uid = message.from_user.id
    user_db = paid_collection.find_one({"uid": uid}) or {}
    debt = user_db.get("debt", 0)
    
    if debt <= 0:
        return bot.reply_to(message, "У вас нет активных кредитов. Спите спокойно.")
        
    if user_db.get("bounty_points", 0) < debt:
        return bot.reply_to(message, f"❌ У вас недостаточно Очков! Для погашения кредита требуется **{debt} 💎**.")
        
    paid_collection.update_one({"uid": uid}, {
        "$inc": {"bounty_points": -debt},
        "$unset": {"debt": "", "debt_deadline": "", "debt_notified": ""}
    })
    db['skynet_tasks'].insert_one({"uid": uid, "action": "full_unban", "timestamp": time.time()})
    
    bot.reply_to(message, f"✅ **КРЕДИТ ПОГАШЕН!**\n\nВы выплатили МФО **{debt} 💎**.\nДолгов нет, арест со счетов снят, коллекторы отозваны.", parse_mode="Markdown")

# ================= АДМИНСКОЕ: КИБЕР-ПРИСТАВ (ОЧИСТКА БАЗЫ) =================
@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '!пристав')
def cyber_bailiff(message):
    try:
        from config import ADMIN_CHAT_IDS, OWNER_ID
        if str(message.from_user.id) not in [str(x) for x in ADMIN_CHAT_IDS] and str(message.from_user.id) != str(OWNER_ID):
            return bot.reply_to(message, "❌ У вас нет лицензии Судебного Пристава Скайнета.")

        bot.send_message(message.chat.id, "👨‍⚖️ <i>Кибер-Пристав начал проверку архивов МФО...</i>", parse_mode="HTML")
        
        # Ищем всех должников
        debtors = list(paid_collection.find({"debt": {"$gt": 0}}))
        wiped_count = 0
        freed_money = 0
        
        for d in debtors:
            uid = d["uid"]
            debt = d.get("debt", 0)
            
            # Проверяем количество сообщений юзера в чатах
            chat_stat = db['chat_stats'].find_one({"uid": uid})
            msgs = chat_stat.get("msgs", 0) if chat_stat else 0
            
            # Если сообщений меньше 50 — это твинк. Уничтожаем!
            if msgs < 50:
                # Полностью удаляем финансовый профиль мошенника
                paid_collection.delete_one({"uid": uid})
                
                # Дополнительно вычищаем его билеты из лотерей, чтобы он не выиграл
                db['tickets_history'].delete_many({"uid": uid})
                
                wiped_count += 1
                freed_money += debt
                
        bot.send_message(
            message.chat.id, 
            f"👨‍⚖️ <b>ОТЧЕТ ПРИСТАВА:</b>\n\n"
            f"🗑 Дела безнадежных должников (твинков) уничтожены: <b>{wiped_count} шт.</b>\n"
            f"💸 Списано фиктивных долгов: <b>{freed_money} 💎</b>.\n"
            f"🎟 Аннулированы все их лотерейные билеты.\n\n"
            f"<i>Экономика очищена от мусора.</i>", 
            parse_mode="HTML"
        )
    except Exception as e:
        bot.reply_to(message, f"Системный сбой: {e}")

# ================= АДМИНСКОЕ: СПИСОК ДОЛЖНИКОВ =================
@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() == '!должники')
def show_debtors(message):
    try:
        from config import ADMIN_CHAT_IDS, OWNER_ID
        # Бронебойная проверка админов
        if str(message.from_user.id) not in [str(x) for x in ADMIN_CHAT_IDS] and str(message.from_user.id) != str(OWNER_ID):
            return bot.reply_to(message, "❌ У вас нет доступа к базе данных МФО.")

        debtors = list(paid_collection.find({"debt": {"$gt": 0}}).sort("debt", -1))
        
        if not debtors:
            return bot.reply_to(message, "📜 Должников нет. МФО работает в плюс, все кристально чисты.")

        text = "🚨 <b>СПИСОК ДОЛЖНИКОВ МФО</b> 🚨\n\n"
        import time, html
        now = time.time()
        
        for i, d in enumerate(debtors, 1):
            uid = d['uid']
            debt = d.get('debt', 0)
            deadline = d.get('debt_deadline', 0)
            
            u_info = db['users'].find_one({"_id": uid}) or {}
            c_info = db['chat_stats'].find_one({"uid": uid}) or {}
            name = u_info.get("first_name") or c_info.get("name") or f"ID {uid}"
            safe_name = html.escape(name) # Спасает от краша Markdown!

            if deadline > now:
                left_hrs = int((deadline - now) / 3600)
                status = f"🟢 До коллекторов: {left_hrs} ч."
            else:
                status = "🔴 ПРОСРОЧКА (В МУТЕ)"
                
            text += f"{i}. <a href='tg://user?id={uid}'>{safe_name}</a> — <b>{debt} 💎</b>\n└ {status}\n\n"

        bot.reply_to(message, text, parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, f"Системный сбой: {e}")

# ================= КРИМИНАЛ: ПОБЕГ ИЗ ТЮРЬМЫ =================
@bot.message_handler(func=lambda m: m.text and m.text.strip().lower().startswith(('!побег', 'побег')))
def prison_break(message):
    try:
        parts = message.text.strip().split()
        if len(parts) < 2:
            return bot.reply_to(message, "⚠️ Укажите цель для спасения: <code>!побег @username</code>", parse_mode="HTML")

        target_id = resolve_target_uid(parts[1])
        if not target_id:
            return bot.reply_to(message, "❌ Заключенный не найден!")

        uid = message.from_user.id
        if target_id == uid:
            return bot.reply_to(message, "🤡 Вытащить самого себя за волосы из тюрьмы мог только барон Мюнхгаузен. Ждите помощи от друзей!")

        import html
        safe_name = html.escape(message.from_user.first_name)
        safe_target = html.escape(parts[1])
        
        bot.send_message(message.chat.id, f"🚁 <a href='tg://user?id={uid}'>{safe_name}</a> подгоняет вертолет к стенам изолятора и кидает трос для {safe_target}...", parse_mode="HTML")
        
        import time, random
        time.sleep(3)

        if random.randint(1, 100) <= 40:
            # УСПЕХ: Снимаем мут и гуантанамо
            db['skynet_tasks'].insert_one({"uid": target_id, "action": "full_unban", "timestamp": time.time()})
            paid_collection.update_one({"uid": target_id}, {"$unset": {"guantanamo_until": ""}})
            paid_collection.update_one({"uid": uid}, {"$inc": {"social_rating": 2}})
            bot.send_message(message.chat.id, f"✅ <b>ПОБЕГ УДАЛСЯ!</b>\nОхрана не успела среагировать. {safe_target} на свободе!\n\n<i>Спасатель получает +2 к Карме за преданность братве.</i>", parse_mode="HTML")
        else:
            # ПРОВАЛ: Полиция вяжет спасателя
            bot.send_message(message.chat.id, f"🚨 <b>ПРОВАЛ! СНАЙПЕРЫ НА ВЫШКАХ!</b>\nВертолет сбит из РПГ. <a href='tg://user?id={uid}'>{safe_name}</a> арестован за пособничество и отправляется в карцер на 2 часа!", parse_mode="HTML")
            mute_user(message.chat.id, uid, 7200, "Провал попытки побега из тюрьмы")
    except Exception as e:
        bot.reply_to(message, f"Системный сбой: {e}")

# ================= СМЕРТЕЛЬНЫЙ ИВЕНТ: ИГРА В КАЛЬМАРА =================
@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() in ['!кальмар', 'кальмар'])
def start_squid_game(message):
    try:
        chat_id = message.chat.id
        uid = message.from_user.id
        import html
        user_name = html.escape(message.from_user.first_name)

        game = db['active_squid_games'].find_one({"_id": chat_id})
        if game:
            if game['status'] == 'playing':
                return bot.reply_to(message, "🦑 Игра уже идет! Ждите окончания, чтобы собрать трупы.")
            elif len(game['players']) >= 10:
                return bot.reply_to(message, "🦑 Мест нет! Набрано 10/10. Игра вот-вот начнется.")
            else:
                return bot.reply_to(message, f"🦑 Набор уже открыт! Пишите <code>!играю</code> (Собрано {len(game['players'])}/10)", parse_mode="HTML")

        user_data = paid_collection.find_one({"uid": uid}) or {}
        if user_data.get("bounty_points", 0) < 1000:
            return bot.reply_to(message, "💸 Участнику нужно 1000 💎 для входа в Игру в Кальмара!")

        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": -1000}})

        import time
        db['active_squid_games'].insert_one({
            "_id": chat_id,
            "status": "recruiting",
            "players": [{"id": uid, "name": message.from_user.first_name}], # Сохраняем сырое имя
            "start_time": time.time()
        })

        bot.send_message(message.chat.id, f"🦑 <b>ИГРА В КАЛЬМАРА НАЧАЛАСЬ!</b> 🦑\n\n<a href='tg://user?id={uid}'>{user_name}</a> открыл(а) набор смертников.\nВход: <b>1000 💎</b>.\nПризовой фонд: <b>10 000 💎</b> (Выживший забирает всё).\n\nНапишите <code>!играю</code>, чтобы вступить. Нужно ровно 10 человек. Кто готов рискнуть голосом?", parse_mode="HTML")
    except Exception as e:
        bot.reply_to(message, f"Системный сбой: {e}")

@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() in ['!играю', 'играю'])
def join_squid_game(message):
    try:
        chat_id = message.chat.id
        uid = message.from_user.id
        import html
        user_name = html.escape(message.from_user.first_name)

        game = db['active_squid_games'].find_one({"_id": chat_id, "status": "recruiting"})
        if not game: return

        if any(p['id'] == uid for p in game['players']):
            return bot.reply_to(message, "🦑 Ты уже в игре. Назад дороги нет.")

        user_data = paid_collection.find_one({"uid": uid}) or {}
        if user_data.get("bounty_points", 0) < 1000:
            return bot.reply_to(message, "💸 У тебя нет 1000 💎. Ты не подходишь для Игры.")

        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": -1000}})
        db['active_squid_games'].update_one({"_id": chat_id}, {"$push": {"players": {"id": uid, "name": message.from_user.first_name}}})

        current_players = len(game['players']) + 1

        if current_players < 10:
            bot.send_message(chat_id, f"👤 Игрок <a href='tg://user?id={uid}'>{user_name}</a> подписал контракт!\nСобрано: {current_players}/10", parse_mode="HTML")
        else:
            bot.send_message(chat_id, "🦑 <b>ИГРОКИ СОБРАНЫ! ИГРА НАЧИНАЕТСЯ!</b>\n\nПравила просты: каждые 30 секунд Скайнет будет случайным образом убивать одного участника.\nПобедитель только один.", parse_mode="HTML")
            db['active_squid_games'].update_one({"_id": chat_id}, {"$set": {"status": "playing"}})
            
            import threading
            threading.Thread(target=run_squid_game, args=(chat_id,), daemon=True).start()
    except Exception as e:
        bot.reply_to(message, f"Системный сбой: {e}")

def run_squid_game(chat_id):
    import time, random, html
    from core.bot import bot
    time.sleep(5)

    game = db['active_squid_games'].find_one({"_id": chat_id})
    if not game: return
    
    players = game['players']
    pot = len(players) * 1000

    while len(players) > 1:
        time.sleep(30) 
        loser = random.choice(players)
        players.remove(loser)
        
        db['active_squid_games'].update_one({"_id": chat_id}, {"$set": {"players": players}})
        mute_user(chat_id, loser['id'], 10800, "Устранен в Игре в Кальмара")

        # 👇 ВОТ ЭТА СТРОКА ВЕРНЕТ ШОУ В ЧАТ 👇
        try:
            bot.send_message(chat_id, f"🔫 <b>Игрок <a href='tg://user?id={loser['id']}'>{html.escape(loser['name'])}</a> устранен.</b> (Мут на 3 часа).\nОсталось игроков: {len(players)}", parse_mode="HTML")
        except: pass

    winner = players[0]
    paid_collection.update_one({"uid": winner['id']}, {"$inc": {"bounty_points": pot}})
    db['active_squid_games'].delete_one({"_id": chat_id})

    try:
        bot.send_message(chat_id, f"🏆 <b>ИГРА В КАЛЬМАРА ЗАВЕРШЕНА!</b> 🏆\n\nВыживший: <a href='tg://user?id={winner['id']}'>{html.escape(winner['name'])}</a>!\nОн забирает весь куш: <b>{pot} 💎</b>!\n\n<i>Поздравляем. Остальные отправлены в морг.</i>", parse_mode="HTML")
    except: pass

# ================= КРИМИНАЛ: ОГРАБЛЕНИЕ КАЗИНО =================
@bot.message_handler(func=lambda m: m.text and m.text.lower() in ['!ограбление', 'ограбление'])
def start_heist(message):
    chat_id = message.chat.id
    uid = message.from_user.id
    import time
    
    # Проверяем, не идет ли уже сбор банды в этом чате
    heist = db['active_heists'].find_one({"_id": chat_id})
    if heist:
        if time.time() - heist['start_time'] > 300: # 5 минут на сбор
            db['active_heists'].delete_one({"_id": chat_id})
        else:
            left = 3 - len(heist['members'])
            return bot.reply_to(message, f"🔫 Сбор банды уже идет! Не хватает еще **{left}** чел.\nПишите `!в деле`, чтобы присоединиться.", parse_mode="Markdown")
            
    # Запускаем новый сбор
    db['active_heists'].insert_one({
        "_id": chat_id,
        "members": [{"id": uid, "name": message.from_user.first_name}],
        "start_time": time.time()
    })
    
    bot.send_message(message.chat.id, f"🏴‍☠️ **ПЛАНИРУЕТСЯ ОГРАБЛЕНИЕ ФИНАНСОВОГО СЕЙФА!**\n\n[{message.from_user.first_name}](tg://user?id={uid}) собирает банду.\nДля налета нужно ровно **3 человека**. На сбор есть 5 минут!\n\n_Награда: 20% от всех рублей в Красном Сейфе._\n_Риск: Мут на 2 часа и штраф 500 💎 каждому._\n\nПишите `!в деле`, если готовы рискнуть!", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text and m.text.lower() in ['!в деле', 'в деле'])
def join_heist(message):
    chat_id = message.chat.id
    uid = message.from_user.id
    user_name = message.from_user.first_name
    import time, random
    
    heist = db['active_heists'].find_one({"_id": chat_id})
    if not heist:
        return # Нет активного сбора
        
    if time.time() - heist['start_time'] > 300:
        db['active_heists'].delete_one({"_id": chat_id})
        return bot.reply_to(message, "⏳ Время вышло. Полиция оцепила район, банда не собралась.")
        
    if any(m['id'] == uid for m in heist['members']):
        return bot.reply_to(message, "🔫 Ты и так уже в банде, держи пушку крепче!")
        
    # Добавляем юзера в банду
    db['active_heists'].update_one({"_id": chat_id}, {"$push": {"members": {"id": uid, "name": user_name}}})
    heist['members'].append({"id": uid, "name": user_name})
    
    if len(heist['members']) < 3:
        left = 3 - len(heist['members'])
        return bot.send_message(message.chat.id, f"🤝 [{user_name}](tg://user?id={uid}) надел(а) маску и присоединился(лась) к банде!\nОсталось найти еще **{left}** чел.", parse_mode="Markdown")
        
    # === БАНДА СОБРАНА. НАЧИНАЕМ НАЛЕТ! ===
    db['active_heists'].delete_one({"_id": chat_id})
    bot.send_message(message.chat.id, "🚐 **БАНДА В СБОРЕ! Налет начался...**\n_Стрельба, взломы серверов, визги сирен..._", parse_mode="Markdown")
    time.sleep(3)
    
    # 30% на успех
    success = random.randint(1, 100) <= 30
    names_str = ", ".join([f"[{m['name']}](tg://user?id={m['id']})" for m in heist['members']])
    
    if success:
        # УСПЕХ! Взламываем Красный Сейф
        red_safe = db['safes_state'].find_one({"_id": "safe_red"}) or {"balance": 500}
        total_loot = int(red_safe.get("balance", 500) * 0.20)
        if total_loot < 3: total_loot = 300
        
        share = total_loot // 3
        
        # Списываем из сейфа
        db['safes_state'].update_one({"_id": "safe_red"}, {"$inc": {"balance": -total_loot}})
        
        # Раздаем рубли (кэшбэк) грабителям
        for m in heist['members']:
            paid_collection.update_one({"uid": m['id']}, {"$inc": {"cashback_balance": share}})
            db['ruble_ledger'].insert_one({"uid": m['id'], "amount": share, "reason": "Успешное ограбление", "timestamp": time.time()})
            
        bot.send_message(message.chat.id, f"💰 **ОГРАБЛЕНИЕ УДАЛОСЬ!**\n\nСигнализация отключена, Сейф вскрыт болгаркой!\nБанда вынесла **{total_loot} ₽** наличными!\n\nГерои дня: {names_str}\n_Каждый получает свою долю: {share} ₽._", parse_mode="Markdown")
        
    else:
        # ПРОВАЛ! Полиция вяжет всех.
        for m in heist['members']:
            paid_collection.update_one({"uid": m['id']}, {"$inc": {"bounty_points": -500}})
            mute_user(chat_id, m['id'], 7200, "Пойман полицией на ограблении")
            
        bot.send_message(message.chat.id, f"🚨 **ПРОВАЛ! СПЕЦНАЗ НА МЕСТЕ!**\n\nКто-то нажал тревожную кнопку. Полиция повязала всю банду прямо в хранилище!\n\nАрестованы: {names_str}\n\n_Суд был скорым: конфискация 500 💎 у каждого и 2 часа тюрьмы (Мут)._", parse_mode="Markdown")


# ==============================================================================
@bot.message_handler(commands=['spawn_lot'])
def spawn_auction_lot(message):
    from config import STAFF_GROUP_ID, OWNER_ID
    if str(message.chat.id) != str(STAFF_GROUP_ID) and message.from_user.id != OWNER_ID: return
    
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "Формат: /spawn_lot [thanos|homewrecker|raider]")
        return
        
    lot_type = parts[1].strip()
    import time
    end_time = int(time.time()) + (24 * 3600) # Аукцион на 24 часа
    
    if lot_type == "thanos": name, desc, icon = "Перчатка Таноса", "Замутить половину чата на 15 мин (!щелчок).", "🧤"
    elif lot_type == "homewrecker": name, desc, icon = "Разлучник", "Расторгнуть чужой брак (!развести @user).", "💔"
    elif lot_type == "raider": name, desc, icon = "Рейдерский Захват", "Украсть урожай с чужой фермы (!рейд @user).", "🧲"
    elif lot_type == "gods_voice": name, desc, icon = "Глас Бога", "Сообщение во все чаты сети (!глас текст).", "📢"
    elif lot_type == "guantanamo": name, desc, icon = "Ордер Гуантанамо", "Неснимаемый мут на 24ч (!гуантанамо @user).", "🚷"
    elif lot_type == "casino_owner": name, desc, icon = "Владелец Казино", "3 дня: 10% с рулетки и дуэлей идут вам.", "🎰"
    elif lot_type == "offshore": name, desc, icon = "Офшорный Счет", "Неделя без комиссий на Черном Рынке.", "🏦"
    elif lot_type == "master_key": name, desc, icon = "Мастер-Ключ", "100% вскрытие сейфа (!вскрыть синий/красный).", "🗝"
    elif lot_type == "golden_frame": name, desc, icon = "Золотая Рамка", "Элитный статус в профиле навсегда.", "⚜️"
    elif lot_type == "personal_npc": name, desc, icon = "Личный NPC", "Создать свою RP-команду (!создать_нпс).", "🤖"
    else:
        bot.reply_to(message, "Неизвестный тип лота.")
        return
        
    db['auction_lots'].insert_one({
        "type_id": lot_type,
        "name": name,
        "desc": desc,
        "icon": icon,
        "current_bid": 1000, # Стартовая цена
        "leader_uid": None,
        "end_time": end_time,
        "status": "active"
    })
    bot.reply_to(message, f"✅ Лот «{name}» выставлен на Теневой Аукцион на 24 часа! Стартовая цена: 1000 💎")

from core.scheduler import schedule_message_deletion
from utils.logger import logger

# ================= УБОРЩИК ЗА ЧУЖИМИ БОТАМИ =================
@bot.message_handler(
    func=lambda m: m.from_user and m.from_user.id == 7195399721,
    content_types=['text', 'photo', 'video', 'animation', 'document', 'sticker', 'voice', 'video_note', 'audio']
)
def cleanup_lazy_bots(message):
    from config import STAFF_GROUP_ID
    logger.info(f"🧹 ПОЙМАЛ CPBlockerBot! chat={message.chat.id} msg_id={message.message_id}")
    
    # Сообщаем в админ-чат, что поймали
    try:
        bot.send_message(STAFF_GROUP_ID, f"🧹 Поймал сообщение CPBlockerBot\nchat={message.chat.id}\nmsg_id={message.message_id}")
    except: pass
    
    # Сразу пробуем удалить
    try:
        bot.delete_message(message.chat.id, message.message_id)
        logger.info("✅ Удалил сразу")
        try:
            bot.send_message(STAFF_GROUP_ID, "✅ Успешно удалил сообщение CPBlockerBot")
        except: pass
    except Exception as e:
        logger.error(f"❌ Не смог удалить сразу: {e}")
        try:
            bot.send_message(STAFF_GROUP_ID, f"❌ Не смог удалить: <code>{e}</code>", parse_mode="HTML")
        except: pass
    
    # И на всякий случай ещё через 5 минут
    schedule_message_deletion(message.chat.id, message.message_id, 300, bot)

# 4. НЕВИДИМЫЙ СБОРЩИК АКТИВНОСТИ И СОЦИАЛЬНЫЙ РЕЙТИНГ
@bot.message_handler(content_types=['text', 'photo', 'video', 'voice', 'sticker', 'animation'])
def track_global_activity(message):
    if message.text and message.text.startswith(('!', '/')): return
    
    uid = message.from_user.id
    user_data = paid_collection.find_one({"uid": uid}) or {}
    karma = user_data.get("social_rating", 0)
    import time, random

    # 💀 1. ЦИФРОВОЙ ГУЛАГ (Карма <= -100)
    if karma <= -100:
        try:
            bot.delete_message(message.chat.id, message.message_id)
        except: pass
        mute_user(message.chat.id, uid, 172800, "Цифровой ГУЛАГ (Карма <= -100)")
        paid_collection.update_one({"uid": uid}, {"$set": {"social_rating": -50}}) # Сброс до -50
        try:
            bot.send_message(message.chat.id, f"🚨 <b>ВРАГ НАРОДА УСТРАНЕН!</b>\nГражданин {message.from_user.first_name} лишен голоса на 48 часов за достижение Кармы -100. Рейтинг принудительно сброшен до -50.", parse_mode="HTML")
        except: pass
        return

    # 👻 2. ТЕНЕВОЙ БАН (Карма <= -90)
    if karma <= -90 and random.randint(1, 100) <= 25:
        try:
            bot.delete_message(message.chat.id, message.message_id)
            bot.send_message(message.chat.id, f"🗑 <i>Пакет данных утерян. Социальный рейтинг отправителя слишком низок для стабильной маршрутизации.</i>", parse_mode="HTML")
        except: pass
        return

    # 💸 3. НАЛОГ НА СЛОВА (Карма <= -75)
    if karma <= -75:
        pts = user_data.get("bounty_points", 0)
        if pts < 5:
            try:
                bot.delete_message(message.chat.id, message.message_id)
            except: pass
            mute_user(message.chat.id, uid, 43200, "Налог на слова: исчерпан баланс")
            try:
                bot.send_message(message.chat.id, f"🔇 <b>БАЛАНС СЛОВ ИСЧЕРПАН.</b>\nУ гражданина нет 5 💎 на оплату сообщения. Выдан мут на 12 часов. Молчание — золото.", parse_mode="HTML")
            except: pass
            return
        else:
            paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": -5}})
            db['safes_state'].update_one({"_id": "safe_blue"}, {"$inc": {"balance": 5}})

    # 📵 4. ПЕЙДЖЕР-РЕЖИМ (Карма <= -50, запрет медиа)
    if karma <= -50 and message.content_type != 'text':
        try:
            bot.delete_message(message.chat.id, message.message_id)
            bot.send_message(message.chat.id, f"📵 <b>ПЕЙДЖЕР-РЕЖИМ!</b>\nГражданину {message.from_user.first_name} запрещено отправлять фото, стикеры и войсы (Карма ниже -50). Только текст!", parse_mode="HTML")
        except: pass
        return

    # Стандартный сбор статистики (если прошел цензуру)
    set_fields = {"name": message.from_user.first_name}
    if message.from_user.username:
        set_fields["username"] = message.from_user.username.lower()
        db['users'].update_one({"_id": uid}, {"$set": {"username": message.from_user.username.lower()}}, upsert=True)
        
    db['chat_stats'].update_one({"chat_id": message.chat.id, "uid": uid}, {"$inc": {"msgs": 1}, "$set": set_fields}, upsert=True)

    # Трекер заданий ЕКБ
    import datetime
    tz_ekb = datetime.timezone(datetime.timedelta(hours=5))
    today_str = datetime.datetime.now(tz_ekb).strftime("%Y-%m-%d")
    db['tasks_progress'].update_one({"uid": uid, "date": today_str}, {"$inc": {"messages": 1}}, upsert=True)

# === ДАТЧИК ПУЛЬСА СЕКРЕТАРЯ ===
def heartbeat_sec():
    from database.mongo import db
    while True:
        try:
            db['settings'].update_one({"_id": "bot_status"}, {"$set": {"sec_last_seen": time.time()}}, upsert=True)
        except: pass
        time.sleep(60)

threading.Thread(target=heartbeat_sec, daemon=True).start()

def pay_casino_owner(amount):
    """Пассивный доход Владельца Казино (10% от слива)"""
    import time
    owner = paid_collection.find_one({"casino_owner_until": {"$gt": int(time.time())}})
    if owner:
        paid_collection.update_one({"uid": owner["uid"]}, {"$inc": {"bounty_points": amount}})

if not is_setup_done:
    threading.Thread(target=setup, daemon=True).start()
    is_setup_done = True
# ===============================

if __name__ == '__main__':
    # Получаем порт напрямую от серверов Render (если его нет - берем 5000 для локальных тестов)
    render_port = int(os.environ.get("PORT", 5000))
    
    # Запускаем Flask строго на порту Render'а
    app.run(host="0.0.0.0", port=render_port)