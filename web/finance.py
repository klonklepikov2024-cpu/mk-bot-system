from flask import request, jsonify, session, redirect, url_for
from database import db, withdrawals_collection, update_user_stats
import time
from bson.objectid import ObjectId
from datetime import datetime

INTERNAL_REVENUE_TYPES = {"vip_points", "vip_rub_balance", "beyond_rub", "beyond_pts", "ads_points", "ads_rub_balance"}
RUB_REVENUE_TYPES = {"ads_crypto"}  # бот МП пишет крипто-рекламу в рублях без пометки валюты  # оплата очками/кэшбэком — не живые деньги

def _rev_kind(r):
    """stars | rub | internal. Новые крипто-записи помечены currency=RUB."""
    if r.get("type") in INTERNAL_REVENUE_TYPES:
        return "internal"
    if r.get("currency") == "RUB" or r.get("type") in RUB_REVENUE_TYPES:
        return "rub"
    return "stars"

def register_finance_routes(app, bot, add_radar_log, OWNER_ID, ROOT_PIN):

    # ================= 📡 LIVE ФИНАНСОВЫЙ ЦЕНТР СЕТИ =================
    @app.route('/glaz/api/live_finance', methods=['GET'])
    def api_live_finance():
        if not session.get('logged_in'): 
            return jsonify({"error": "Unauthorized"}), 401
        
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 25))
        currency_filter = request.args.get('currency', 'all')
        search_uid = request.args.get('uid', '').strip()
        
        rub_query = {}
        wd_query = {}
        fine_query = {}
        star_query = {}
        
        if search_uid and search_uid.isdigit():
            u_int = int(search_uid)
            rub_query["uid"] = u_int
            wd_query["user_id"] = u_int
            fine_query["uid"] = u_int
            star_query["uid"] = u_int
            
        items = []
        
        try:
            target_db = db.client['elite_bot_db']
        except:
            target_db = db
            
        # 1. Рублевый леджер (Кэшбэк, Квесты, Ферма, Рынок, Сейфы)
        if currency_filter in ['all', 'rub']:
            rub_records = list(target_db['ruble_ledger'].find(rub_query).sort("timestamp", -1).limit(300))
            for r in rub_records:
                amt = r.get("amount", 0)
                is_plus = amt > 0
                items.append({
                    "id": str(r.get("_id")),
                    "timestamp": r.get("timestamp", 0),
                    "uid": r.get("uid"),
                    "currency": "₽",
                    "amount": amt,
                    "amount_str": f"{'+' if is_plus else ''}{amt} ₽",
                    "is_positive": is_plus,
                    "type": "Кэшбэк / Баланс",
                    "reason": r.get("reason", "Операция баланса"),
                    "badge": "green" if is_plus else "red",
                    "status": "Выполнено"
                })
                
        # 2. Выводы средств (Ожидающие, выплаченные, отклоненные)
        if currency_filter in ['all', 'rub', 'payout']:
            wd_records = list(target_db['withdrawals'].find(wd_query).sort("timestamp", -1).limit(200))
            for w in wd_records:
                st = w.get("status", "pending")
                st_map = {
                    "pending": ("Ожидает выплаты", "yellow"),
                    "paid": ("Выплачено", "green"),
                    "rejected": ("Отклонено", "red")
                }
                status_label, badge_color = st_map.get(st, (st, "gray"))
                items.append({
                    "id": str(w.get("_id")),
                    "timestamp": w.get("timestamp", 0),
                    "uid": w.get("user_id"),
                    "currency": "₽",
                    "amount": -w.get("amount", 0),
                    "amount_str": f"-{w.get('amount', 0)} ₽",
                    "is_positive": False,
                    "type": f"Вывод ({w.get('method', 'Реквизиты')})",
                    "reason": f"Реквизиты: {w.get('details', '')}",
                    "badge": badge_color,
                    "status": status_label
                })
                
        # 3. Звезды Telegram Stars (Штрафы, Разбаны, Донаты)
        if currency_filter in ['all', 'stars']:
            fine_records = list(target_db['fine_payments'].find(fine_query).sort("timestamp", -1).limit(200))
            for f in fine_records:
                amt = f.get("amount", 0)
                items.append({
                    "id": str(f.get("_id")),
                    "timestamp": f.get("timestamp", 0),
                    "uid": f.get("uid"),
                    "currency": "⭐️",
                    "amount": amt,
                    "amount_str": f"+{amt} ⭐️",
                    "is_positive": True,
                    "type": "Штраф / Разбан",
                    "reason": f"Оплата штрафа ({f.get('date', '')})",
                    "badge": "purple",
                    "status": "Оплачено"
                })
                
            star_records = list(target_db['star_transactions'].find(star_query).sort("timestamp", -1).limit(200))
            fine_ts = {f.get("timestamp") for f in fine_records}
            for s in star_records:
                if s.get("timestamp") not in fine_ts:
                    amt = s.get("amount", 0)
                    items.append({
                        "id": str(s.get("_id")),
                        "timestamp": s.get("timestamp", 0),
                        "uid": s.get("uid"),
                        "currency": "⭐️",
                        "amount": amt,
                        "amount_str": f"+{amt} ⭐️",
                        "is_positive": True,
                        "type": "Покупка Stars",
                        "reason": f"Чек: {str(s.get('charge_id', ''))[:16]}...",
                        "badge": "blue",
                        "status": s.get("status", "paid")
                    })

        # 4. 🔥 КРИПТО-КАССА (Реклама, VIP, BEYOND) 🔥
        if currency_filter in ['all', 'rub'] and not search_uid:
            rev_records = list(target_db['daily_revenue'].find({"currency": "RUB"}).sort("timestamp", -1).limit(200))
            for rev in rev_records:
                r_type = rev.get("type", "")
                if True:  # только крипто-записи (раньше сюда попадали и звёздные VIP с подписью «₽ CryptoBot»)
                    # Переводим технические названия в красивые для панели
                    type_ru = "Донат / Чаевые"
                    if r_type == 'ads': type_ru = "Покупка Рекламы"
                    elif r_type == 'vip': type_ru = "Покупка VIP"
                    elif r_type == 'beyond': type_ru = "Клуб BEYOND"
                    elif r_type == 'city': type_ru = "Доступ к городу"
                    elif r_type == 'fine': type_ru = "Штраф"
                    elif r_type == 'indulgence': type_ru = "Индульгенция"
                    
                    amt = rev.get("amount", 0)
                    items.append({
                        "id": str(rev.get("_id")),
                        "timestamp": rev.get("timestamp", 0),
                        "uid": rev.get("uid", "Крипто-Шлюз"),
                        "currency": "₽",
                        "amount": amt,
                        "amount_str": f"+{amt} ₽",
                        "is_positive": True,
                        "type": type_ru,
                        "reason": "Прямое пополнение (CryptoBot)",
                        "badge": "purple",
                        "status": "Успешно"
                    })

        # Мгновенная сортировка общего пула по времени (свежие — сверху)
        items.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        
        total_items = len(items)
        total_pages = max(1, (total_items + limit - 1) // limit)
        if page < 1: page = 1
        if page > total_pages: page = total_pages
        
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        paginated_items = items[start_idx:end_idx]
        
        import pytz
        from datetime import datetime
        tz_ekb = pytz.timezone('Asia/Yekaterinburg')
        
        for item in paginated_items:
            ts = item.get("timestamp", 0)
            if ts:
                try:
                    dt = datetime.fromtimestamp(ts, tz_ekb)
                    item["date_str"] = dt.strftime("%d.%m %H:%M:%S")
                except:
                    item["date_str"] = "---"
            else:
                item["date_str"] = "---"

        return jsonify({
            "items": paginated_items,
            "page": page,
            "total_pages": total_pages,
            "total_items": total_items
        })

    @app.route('/glaz/withdrawal_action', methods=['POST'])
    def glaz_withdrawal_action():
        if not session.get('logged_in'): return redirect(url_for('login'))
        wd_id = request.form.get('wd_id')
        action = request.form.get('action')
        
        # Два вида заявок: рублёвые из Секретаря (ObjectId) и реферальные звёздные из Скайнета (w_...).
        # Раньше реферальные из панели не обрабатывались вообще (ошибка ObjectId → тихий редирект).
        is_ref = bool(wd_id) and wd_id.startswith("w_")
        if is_ref:
            wd_obj_id = wd_id
        else:
            try:
                from bson.objectid import ObjectId
                wd_obj_id = ObjectId(wd_id)
            except: return redirect(url_for('admin_panel'))
            
        try:
            target_db = db.client['elite_bot_db']
            collection = target_db['withdrawals']
        except:
            collection = db['withdrawals']
            
        if action not in ('pay', 'reject'):
            return redirect(url_for('admin_panel'))

        # Атомарно: заявка меняет статус ровно один раз. Раньше двойной клик по «Отклонить»
        # возвращал деньги на баланс дважды.
        new_status = "paid" if action == 'pay' else "rejected"
        wd = collection.find_one_and_update(
            {"_id": wd_obj_id, "status": "pending"},
            {"$set": {"status": new_status, "notify_status": action, "processed_at": time.time()}}
        )
        
        if wd:
            uid = wd['user_id']
            amount = wd['amount']
            
            if is_ref:
                # реферальный баланс в звёздах: списываем только при выплате, при отказе возвращать нечего
                if action == 'pay':
                    update_user_stats(uid, balance_add=-amount)
                try: bot.send_message(uid, f"✅ Ваш запрос на вывод {amount} звезд выплачен!" if action == 'pay' else "❌ Ваш запрос на вывод средств был отклонен администрацией.")
                except Exception: pass
                add_radar_log(f"💸 Реферальная заявка {wd_id}: {'оплачена' if action == 'pay' else 'отклонена'}")
            elif action == 'pay':
                add_radar_log(f"💸 ОПЛАЧЕНА ЗАЯВКА: {wd_id}")
                
            elif action == 'reject':
                try:
                    paid_coll = target_db['paid_users']
                    rub_ledger = target_db['ruble_ledger']
                except:
                    paid_coll = db['paid_users']
                    rub_ledger = db['ruble_ledger']
                    
                paid_coll.update_one({"uid": uid}, {"$inc": {"cashback_balance": amount}})
                
                rub_ledger.insert_one({
                    "uid": uid,
                    "amount": amount,
                    "reason": "Возврат средств (Отмена вывода админом)",
                    "timestamp": time.time()
                })
                add_radar_log(f"🚫 ОТКЛОНЕНА ЗАЯВКА: {wd_id}")
                
        return redirect(url_for('admin_panel'))

    @app.route('/glaz/add_promo', methods=['POST'])
    def glaz_add_promo():
        if not session.get('logged_in'): return redirect(url_for('login'))
        code = request.form.get('code').strip().upper()
        discount = int(request.form.get('discount'))
        target = request.form.get('target')
        limit = int(request.form.get('limit'))
        db['promocodes'].update_one(
            {"_id": code},
            {"$set": {"type": "percent", "value": discount, "target": target, "usage_limit": limit, "used_count": 0, "owner_uid": OWNER_ID, "is_active": True}},
            upsert=True
        )
        add_radar_log(f"🎫 СОЗДАН ПРОМОКОД: {code} ({discount}%)")
        return redirect(url_for('admin_panel'))

    @app.route('/glaz/delete_promo', methods=['POST'])
    def glaz_delete_promo():
        """Уничтожитель промокодов"""
        if not session.get('logged_in'): return redirect(url_for('login'))
        
        code = request.form.get('code')
        if code:
            db['promocodes'].delete_one({"_id": code})
            add_radar_log(f"🗑 ПРОМОКОД УНИЧТОЖЕН: {code}")
            
        return redirect(url_for('admin_panel'))

    # === API РОУТЫ ===
    @app.route('/glaz/api/root/finance', methods=['POST'])
    def api_get_root_finance():
        data = request.json
        if data.get('pin') != ROOT_PIN:
            return jsonify({"error": "Access Denied"}), 403
            
        today_str = datetime.now().strftime("%d.%m.%Y")
        
        # 🔥 ТЕПЕРЬ СЧИТАЕМ ВСЕ ТИПЫ ДОХОДОВ ЗА СЕГОДНЯ ИЗ DAILY_REVENUE
        today_revenue = list(db['daily_revenue'].find({"date": today_str}))
        # Раньше в одну сумму «звёзд» складывались и рубли с крипты, и оплаты очками/кэшбэком
        # (это не деньги), а потом всё умножалось на 1.6. Теперь раздельно.
        total_stars = sum(r.get('amount', 0) for r in today_revenue if _rev_kind(r) == "stars")
        crypto_rub = sum(r.get('amount', 0) for r in today_revenue if _rev_kind(r) == "rub")
        internal = sum(r.get('amount', 0) for r in today_revenue if _rev_kind(r) == "internal")
        
        # Оставляем детальный список логов для авторазбанов внизу блока
        today_payments = list(db['fine_payments'].find({"date": today_str}))
        formatted_list = []
        import time
        for p in today_payments:
            formatted_list.append({
                "uid": p["uid"],
                "amount": p["amount"],
                "time": time.strftime("%H:%M:%S", time.localtime(p["timestamp"]))
            })
            
        return jsonify({
            "total_today": total_stars,
            "crypto_rub_today": round(crypto_rub),
            "internal_today": internal,
            "payments": formatted_list
        })

    @app.route('/glaz/api/analytics/revenue', methods=['POST'])
    def api_get_revenue_stats():
        data = request.json
        if data.get('pin') != ROOT_PIN: 
            return jsonify({"error": "Unauthorized"}), 403
        
        # 1. Получаем период из запроса вебки (по умолчанию неделя)
        period = data.get('period', 'week')
        pipeline = []
        
        # 2. Фильтр машины времени Скайнета
        if period != 'all':
            from datetime import timedelta, datetime
            days = 7 if period == 'week' else 30
            # Генерируем список правильных дат за последние 7 или 30 дней
            valid_dates = [(datetime.now() - timedelta(days=i)).strftime("%d.%m.%Y") for i in range(days)]
            
            # Говорим базе: отдай только те доходы, дата которых есть в нашем списке
            pipeline.append({"$match": {"date": {"$in": valid_dates}}})
            
        # 3. Суммируем доходы по дням и типам
        pipeline.extend([
            {"$group": {
                "_id": {"date": "$date", "type": "$type"},
                "total": {"$sum": "$amount"}
            }}
        ])
        
        stats = list(db['daily_revenue'].aggregate(pipeline))
        
        # 4. Умная сортировка! Выстраиваем даты в правильном календарном порядке
        try:
            from datetime import datetime
            stats.sort(key=lambda x: datetime.strptime(x['_id']['date'], "%d.%m.%Y"))
        except:
            pass # Если попалась битая дата - игнорируем
            
        return jsonify(stats)

    @app.route('/glaz/api/get_prices', methods=['GET'])
    def api_get_prices():
        if not session.get('logged_in'): return jsonify({"error": "Unauthorized"}), 401
        
        prices = db['settings'].find_one({"_id": "skynet_pricing"})
        if not prices:
            prices = {
                "vip_price": 250,
                "beyond_price": 250,
                "reg_small_1": 105, "reg_small_7": 490, "reg_small_15": 720, "reg_small_30": 938,
                "reg_big_1": 105, "reg_big_7": 656, "reg_big_15": 1288, "reg_big_30": 1563,
                "vip_big_chat_1": 1095, "vip_big_chat_7": 7656
            }
        return jsonify(prices)

    @app.route('/glaz/api/save_prices', methods=['POST'])
    def api_save_prices():
        if not session.get('logged_in'): return jsonify({"success": False, "error": "Unauthorized"}), 401
        
        data = request.json
        db['settings'].update_one(
            {"_id": "skynet_pricing"},
            {"$set": {
                "vip_price": int(data.get("vip_price", 250)),
                "beyond_price": int(data.get("beyond_price", 250)),
                "reg_small_1": int(data.get("reg_small_1", 105)),
                "reg_small_7": int(data.get("reg_small_7", 490)),
                "reg_small_15": int(data.get("reg_small_15", 720)),
                "reg_small_30": int(data.get("reg_small_30", 938)),
                "reg_big_1": int(data.get("reg_big_1", 105)),
                "reg_big_7": int(data.get("reg_big_7", 656)),
                "reg_big_15": int(data.get("reg_big_15", 1288)),
                "reg_big_30": int(data.get("reg_big_30", 1563)),
                "vip_big_chat_1": int(data.get("vip_big_chat_1", 1095)),
                "vip_big_chat_7": int(data.get("vip_big_chat_7", 7656))
            }},
            upsert=True
        )
        add_radar_log("💰 Сетка тарифов (VIP, BEYOND и Реклама) изменена администратором")
        return jsonify({"success": True, "message": "Финансовая матрица успешно обновлена!"})