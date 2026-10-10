import time
import uuid
import json
import threading
from datetime import datetime
from flask import request, render_template, session, redirect, url_for, jsonify
from core.settings import SkynetSettings
from core.cfg import cfg

_LEGACY_RATE_UNTIL = datetime(2026, 10, 25).timestamp()  # см. _rub_ok
from database import db, users_collection, banned_collection, withdrawals_collection, proxy_sessions, archive_collection

def register_main_routes(app, bot, add_radar_log, ban_user_everywhere, mute_user_everywhere,
                         unban_user_everywhere, unmute_user_everywhere, background_corpse_removal,
                         WEB_USER, WEB_PASS, OWNER_ID, ADMIN_CHAT_IDS, ROOT_PIN, STAFF_GROUP_ID):

    # login / 2fa / logout теперь живут в web_auth.py
    from pymongo.errors import DuplicateKeyError


    @app.route('/glaz', methods=['GET', 'POST'])
    def admin_panel():
        if not session.get('logged_in'): return redirect(url_for('login'))
        
        total_users = users_collection.count_documents({})
        vip_users = users_collection.count_documents({"is_vip": True})
        queer_users = users_collection.count_documents({"is_queer": True})
        banned_users = banned_collection.count_documents({})
        
        # Тянем ТОЛЬКО активные заявки, чтобы они не терялись за старыми
        all_withdrawals = list(db['withdrawals'].find({"status": "pending"}).sort("timestamp", -1))
        all_promos = list(db['promocodes'].find().sort("_id", -1))
        
        # 👇 ДОБАВИЛИ ЭТО:
        all_tags = list(db['temp_tags'].find())
        all_premium = list(db['premium_claims'].find())
        
        user_data = None
        search_error = None
        
        search_id = request.args.get('search_id') or request.form.get('search_id')
        if search_id:
            try:
                q = search_id.strip()
                if not q.lstrip('-').isdigit():
                    # Поиск по @username (раньше только по ID)
                    uname = "@" + q.lstrip("@").lower()
                    found = users_collection.find_one({"username": uname})
                    if not found:
                        raise ValueError
                    q = str(found["_id"])
                uid = int(q)
                u_info = users_collection.find_one({"_id": uid})
                b_info = banned_collection.find_one({"_id": uid})
                archive_info = archive_collection.find_one({"target": str(uid)})
                
                if u_info or b_info or archive_info:
                    history_list = []
                    if archive_info and archive_info.get("history"):
                        for entry in archive_info["history"]:
                            history_list.append(f"[{entry.get('date', '')}] {entry.get('action', '')} — {entry.get('reason', '')}")
                    
                    detected_reason = "Нет активных блокировок"
                    if b_info and b_info.get("reason"): detected_reason = b_info.get("reason")
                    elif u_info and u_info.get("last_mute_reason"): detected_reason = u_info.get("last_mute_reason")
                    elif archive_info and archive_info.get("history"): detected_reason = archive_info["history"][-1].get("reason", "Не указана")

                    is_admin_system = (uid == OWNER_ID or uid in ADMIN_CHAT_IDS)

                    is_quarantine = False
                    first_seen = u_info.get("first_seen", 0) if u_info else 0
                    _qh = int((db['settings'].find_one({"_id": "moderation_limits"}) or {}).get("quaran_hours", 120))
                    if uid > 7800000000 and first_seen > 0 and (time.time() - first_seen) < _qh * 3600:
                        is_quarantine = True

                    # 💎 ВЫТАСКИВАЕМ СОКРОВИЩА ИЗ ПЛАТЕЖНОЙ БАЗЫ СКАЙНЕТА
                    p_info = db['paid_users'].find_one({"uid": uid}) or {}
                    
                    # Расчет остатка таймера кружка верификации
                    v_timer = p_info.get("verif_timer")
                    seconds_left = 0
                    if v_timer:
                        diff = (datetime.now() - v_timer).total_seconds()
                        seconds_left = max(0, int(300 - diff))

                    # 👇 НОВОЕ: ВЫЧИСЛЯЕМ ДАТУ ВЕРБОВКИ 👇
                    first_seen_ts = u_info.get("first_seen", 0) if u_info else 0
                    if first_seen_ts > 0:
                        first_seen_str = datetime.fromtimestamp(first_seen_ts).strftime('%d.%m.%Y')
                    else:
                        first_seen_str = "Старожил (До 28.04)"

                    # 👇 ВЫТАСКИВАЕМ ФИНАНСОВУЮ ИСТОРИЮ ЮЗЕРА 👇
                    ledger_logs = list(db['ruble_ledger'].find({"uid": uid}).sort("timestamp", -1).limit(10))
                    fin_history = []
                    for log in ledger_logs:
                        dt = datetime.fromtimestamp(log['timestamp']).strftime('%d.%m %H:%M')
                        sign = "+" if log['amount'] > 0 else ""
                        fin_history.append(f"[{dt}] {sign}{log['amount']}₽ — {log.get('reason', '')}")
                        
                    wd_logs = list(db['withdrawals'].find({"user_id": uid}).sort("timestamp", -1).limit(5))
                    for wd in wd_logs:
                        dt = datetime.fromtimestamp(wd['timestamp']).strftime('%d.%m %H:%M')
                        status_map = {"pending": "⏳ В обработке", "paid": "✅ Выплачено", "rejected": "❌ Отклонено"}
                        status_ru = status_map.get(wd.get('status'), wd.get('status'))
                        method_str = wd.get('method', 'Неизвестно')
                        fin_history.append(f"[{dt}] ВЫВОД {wd['amount']}₽ ({method_str}) — {status_ru}")

                    # 👇 Расширенная карточка: имя, мут, воронки, платежи, анкеты
                    _mute_until = (u_info or {}).get("net_mute_until")
                    mute_str = None
                    if _mute_until is not None and (u_info or {}).get("net_mute_reason") is not None:
                        mute_str = "бессрочно" if not _mute_until else (
                            datetime.fromtimestamp(_mute_until).strftime('%d.%m %H:%M') if _mute_until > time.time() else None)
                    vf = db['vip_funnel'].find_one({"_id": uid}) or {}
                    bf = db['beyond_funnel'].find_one({"_id": uid}) or {}
                    pay_rows = []
                    for r in db['daily_revenue'].find({"uid": uid}).sort("timestamp", -1).limit(12):
                        cur = "₽" if r.get("currency") == "RUB" else "⭐️"
                        ts = r.get("timestamp")
                        pay_rows.append({"date": datetime.fromtimestamp(ts).strftime('%d.%m.%y') if ts else r.get("date", ""),
                                         "type": r.get("type", ""), "amount": f"{r.get('amount', 0)} {cur}"})
                    for r in db['star_transactions'].find({"uid": uid}).sort("timestamp", -1).limit(12):
                        if r.get("status") == "refunded":
                            pay_rows.append({"date": datetime.fromtimestamp(r.get("timestamp", 0)).strftime('%d.%m.%y'),
                                             "type": "возврат", "amount": f"−{r.get('amount', 0)} ⭐️"})
                    pts_rows = []
                    for r in db['points_ledger'].find({"uid": uid}).sort("ts", -1).limit(10):
                        d = r.get("delta", 0)
                        pts_rows.append(f"[{datetime.fromtimestamp(r.get('ts', 0)).strftime('%d.%m %H:%M')}] "
                                        f"{'+' if d > 0 else ''}{d} {r.get('field', '')} — {r.get('reason', '')}")
                    user_data_extra = {
                        "first_name": (u_info or {}).get("first_name") or "Без имени",
                        "username": (u_info or {}).get("username") or "",
                        "mute_str": mute_str,
                        "mute_reason": (u_info or {}).get("net_mute_reason") or "",
                        "indulgence": bool((u_info or {}).get("indulgence")),
                        "vip_stage": vf.get("stage"),
                        "beyond_step": bf.get("step"),
                        "posts_count": db['posts'].count_documents({"user_id": uid}),
                        "invites": int(p_info.get("cpa_refs", 0) or 0) + int(p_info.get("invites", 0) or 0),
                        "payments": pay_rows[:15],
                        "points_history": pts_rows,
                    }

                    user_data = {
                        "id": uid,
                        "is_quarantine": is_quarantine,
                        "is_vip": u_info.get("is_vip", False) if u_info else False,
                        "is_queer": u_info.get("is_queer", False) if u_info else False,
                        "is_verified": u_info.get("is_verified", False) if u_info else False,
                        "is_admin": is_admin_system,
                        "main_city": u_info.get("main_city", "Не привязан") if u_info else "Не привязан",
                        "purchased_cities": u_info.get("purchased_cities", []) if u_info else [],
                        "first_seen_str": first_seen_str,
                        "custom_tag": u_info.get("custom_tag", "Отсутствует") if u_info else "Отсутствует",
                        "shame_tag": u_info.get("shame_tag", "Отсутствует") if u_info else "Отсутствует",
                        "banned": True if b_info else False,
                        "ban_reason": detected_reason,
                        "history": history_list,
                        "points": p_info.get("bounty_points", 0),
                        "shards": p_info.get("jackpot_shards", 0),
                        "cashback": p_info.get("cashback_balance", 0),
                        "immunity": p_info.get("immunity", 0),
                        "strikes": p_info.get("strikes", 0),
                        "admin_notes": p_info.get("admin_notes", ""),
                        "ai_memory": p_info.get("dialog_history", [])[-6:], 
                        "secret_code": p_info.get("secret_code", ""),
                        "verif_seconds_left": seconds_left,
                        "active_chats": p_info.get("active_chats", []),
                        "fin_history": fin_history # <--- ВОТ ТУТ ДОБАВЛЕНА ВЫПИСКА
                    }
                    user_data.update(user_data_extra)
                    add_radar_log(f"🔎 Обыск досье: {uid}")
                else:
                    search_error = f"Юзер {uid} не найден в матрице базы данных."
            except ValueError:
                search_error = "Не найдено: введите ID или @username, который бот уже видел в чатах."

        # 👇 ПРАВИЛЬНЫЙ ВОЗВРАТ В САМОМ КОНЦЕ ФУНКЦИИ 👇
        return render_template(
            'index.html', 
            total=total_users, vips=vip_users, queers=queer_users, banned=banned_users,
            user_data=user_data, search_error=search_error, search_id=search_id or "",
            withdrawals=all_withdrawals, promos=all_promos,
            tags=all_tags, premiums=all_premium
        )

    @app.route('/glaz/api/stats')
    def api_stats():
        if not session.get('logged_in'): return jsonify({"error": "Unauthorized"}), 401
        return jsonify({
            "total": users_collection.count_documents({}),
            "vips": users_collection.count_documents({"is_vip": True}),
            "queers": users_collection.count_documents({"is_queer": True}),
            "banned": banned_collection.count_documents({}),
            "unanswered_tickets": db['support_tickets'].count_documents({"is_answered": False, "is_closed": {"$ne": True}})
        })

    @app.route('/glaz/api/xray')
    def api_xray():
        if not session.get('logged_in'): 
            return jsonify({"error": "Unauthorized"}), 401
        
        total = users_collection.count_documents({})
        vips = users_collection.count_documents({"is_vip": True})
        queers = users_collection.count_documents({"is_queer": True})
        verified_only = users_collection.count_documents({
            "is_verified": True, 
            "is_vip": {"$ne": True}, 
            "is_queer": {"$ne": True}
        })
        banned = banned_collection.count_documents({})

        # === ВОРОНКА ВЕРИФИКАЦИИ ===
        in_verification = db['vip_funnel'].count_documents({})
        
        # 👇 ДОБАВЬ ВОТ ЭТУ СТРОЧКУ 👇
        beyond_verification = db['beyond_funnel'].count_documents({})

        # === УМНАЯ КЛАССИФИКАЦИЯ ОБЫЧНЫХ ПОЛЬЗОВАТЕЛЕЙ ===
        # Активные (у кого бот определил город или кто нажимал на кнопки создания рекламы/саппорта)
        active_regular = users_collection.count_documents({
            "is_vip": {"$ne": True},
            "is_queer": {"$ne": True},
            "is_verified": {"$ne": True},
            "$or": [
                {"main_city": {"$exists": True, "$ne": "Не привязан"}},
                {"intent_post_ads": True},
                {"intent_support": True}
            ]
        })

        # В процессе верификации (заходили в меню, но не завершили кружок)
        pending_vip = users_collection.count_documents({
            "is_vip": {"$ne": True},
            "is_queer": {"$ne": True},
            "intent_vip": True
        })

        # Зеваки (зашли, получили первый контакт, но ничего не нажимали и город не привязался)
        just_viewed = users_collection.count_documents({
            "is_vip": {"$ne": True},
            "is_queer": {"$ne": True},
            "is_verified": {"$ne": True},
            "first_seen": {"$exists": True},
            "main_city": {"$exists": False},
            "intent_vip": {"$ne": True},
            "intent_support": {"$ne": True},
            "intent_post_ads": {"$ne": True}
        })

        # Настоящие мертвые души (старые аккаунты до внедрения логирования)
        real_ghosts = users_collection.count_documents({
            "is_vip": {"$ne": True},
            "is_queer": {"$ne": True},
            "is_verified": {"$ne": True},
            "first_seen": {"$exists": False},
            "main_city": {"$exists": False},
            "intent_vip": {"$ne": True},
            "intent_support": {"$ne": True},
            "intent_post_ads": {"$ne": True}
        })

        # Данные для счетчиков кнопок
        intent_vip = users_collection.count_documents({"intent_vip": True})
        intent_support = users_collection.count_documents({"intent_support": True})
        intent_ads = users_collection.count_documents({"intent_post_ads": True})

        return jsonify({
            "total": total,
            "vips": vips,
            "queers": queers,
            "verified": verified_only,
            "banned": banned,
            "in_verification": in_verification,
            "beyond_verification": beyond_verification, # <--- ДОБАВЬ ЭТО!
            "active_regular": active_regular,
            "just_viewed": just_viewed,
            "ghosts": real_ghosts,
            "intent_vip": intent_vip,
            "intent_support": intent_support,
            "intent_ads": intent_ads,
            "pending_vip": pending_vip
        })

    @app.route('/glaz/api/chart_data')
    def api_chart_data():
        if not session.get('logged_in'): return jsonify({"error": "Unauthorized"}), 401
        total = users_collection.count_documents({})
        vips = users_collection.count_documents({"is_vip": True})
        queers = users_collection.count_documents({"is_queer": True})
        banned = banned_collection.count_documents({})
        regular = max(0, total - vips - queers)

        pipeline = [
            {"$match": {"main_city": {"$exists": True, "$ne": "Не привязан"}}},
            {"$group": {"_id": "$main_city", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 10}
        ]
        city_stats = list(users_collection.aggregate(pipeline))
        
        return jsonify({
            "status_values": [regular, vips, queers, banned],
            "city_labels": [item["_id"] for item in city_stats],
            "city_values": [item["count"] for item in city_stats]
        })

    @app.route('/glaz/api/radar')
    def api_radar():
        if not session.get('logged_in'): return jsonify({"error": "Unauthorized"}), 401
        logs = db['radar_logs'].find().sort("ts", -1).limit(100)
        return jsonify([log["text"] for log in logs])

    @app.route('/glaz/api/get_list')
    def api_get_list():
        if not session.get('logged_in'): return jsonify({"error": "Unauthorized"}), 401
        list_type = request.args.get('type')
        results = []
        
        if list_type == 'vip':
            for doc in users_collection.find({"is_vip": True}):
                results.append({"id": doc["_id"], "info": doc.get("main_city", "Не указан"), "tag": doc.get("custom_tag", "VIP")})
        elif list_type == 'queer':
            for doc in users_collection.find({"is_queer": True}):
                results.append({"id": doc["_id"], "info": doc.get("main_city", "Не указан"), "tag": doc.get("custom_tag", "QUEER")})
        elif list_type == 'banned':
            for doc in banned_collection.find().limit(1000):
                results.append({"id": doc["_id"], "info": doc.get("reason", "Забанен"), "tag": "ЧС"})
                
        return jsonify(results)

    @app.route('/glaz/api/proxy_sessions')
    def api_proxy_sessions():
        if not session.get('logged_in'): return jsonify({"error": "Unauthorized"}), 401
        sessions = list(proxy_sessions.find().sort("_id", -1).limit(30))
        result = []
        for s in sessions:
            result.append({
                "id": s["_id"],
                "vip_id": s.get("vip_id", "Неизвестно"),
                "guest_id": s.get("guest_id", "Неизвестно"),
                "is_active": s.get("is_active", False),
                "msg_count": len(s.get("history", []))
            })
        return jsonify(result)

    @app.route('/glaz/api/proxy_chat')
    def api_proxy_chat():
        if not session.get('logged_in'): return jsonify({"error": "Unauthorized"}), 401
        session_id = request.args.get('session_id')
        s = proxy_sessions.find_one({"_id": session_id})
        if not s: return jsonify({"error": "Not found"})
        return jsonify({
            "vip_id": s.get("vip_id"),
            "guest_id": s.get("guest_id"),
            "is_active": s.get("is_active", False),
            "history": s.get("history", [])
        })

    @app.route('/glaz/mass_action', methods=['POST'])
    def glaz_mass_action():
        if not session.get('logged_in'): return jsonify({"success": False}), 401
        raw_ids = request.form.get('uids', '')
        action = request.form.get('action', 'ban')
        reason = request.form.get('reason', '').strip()
        if not reason: reason = "Массовая репрессия (Web)"
        uids = []
        for line in raw_ids.replace(',', '\n').split('\n'):
            clean_id = line.strip()
            if clean_id.isdigit(): uids.append(int(clean_id))
        if not uids: return jsonify({"success": False, "message": "Не найдено валидных ID!"})
        add_radar_log(f"⚡ Запущен МАССОВЫЙ {action.upper()} ({reason}) для {len(uids)} юзеров!")
        def background_mass_task(id_list, act, rsn):
            for uid in id_list:
                if act == 'ban': ban_user_everywhere(uid, reason=rsn, admin_name="Web-Саурон")
                elif act == 'mute': mute_user_everywhere(uid, reason=rsn, admin_name="Web-Саурон")
                elif act == 'unban': unban_user_everywhere(uid); unmute_user_everywhere(uid)
                time.sleep(0.5)
            add_radar_log(f"✅ Массовый {act.upper()} успешно завершен!")
            try: bot.send_message(STAFF_GROUP_ID, f"🚀 **ВЕБ-АДМИНКА:** Завершен массовый {act.upper()} для {len(id_list)} пользователей!\n📝 Причина: {rsn}")
            except: pass
        threading.Thread(target=background_mass_task, args=(uids, action, reason), daemon=True).start()
        return jsonify({"success": True, "message": f"🔥 Процесс пошел! Наказание займет около {len(uids)//2} сек."})

    @app.route('/glaz/user_action', methods=['POST'])
    def glaz_user_action():
        if not session.get('logged_in'): return jsonify({"success": False, "error": "Unauthorized"}), 401
        uid = int(request.form.get('uid'))
        action = request.form.get('action')
        msg = "Действие выполнено"
        log_to_staff = True
        staff_msg = ""
        if action == 'ban':
            ban_user_everywhere(uid, reason="Ликвидация через Web-Панель", admin_name="Web-Саурон 👁️")
            msg = f"💥 Пользователь {uid} ликвидирован!"
            log_to_staff = False 
        elif action == 'unban':
            unban_user_everywhere(uid); unmute_user_everywhere(uid)
            msg = f"🕊️ С {uid} сняты баны."
            add_radar_log(f"🕊️ АМНИСТИЯ: {uid}")
            staff_msg = f"🕊️ **ВЕБ-АДМИНКА: ПОЛНАЯ АМНИСТИЯ**\n\n• **Пользователь:** `{uid}`\n• **Действие:** Глобально разбанен!"
        elif action == 'make_vip':
            users_collection.update_one({"_id": uid}, {"$set": {"is_vip": True}}, upsert=True)
            msg = f"👑 {uid} коронован!"
            add_radar_log(f"👑 ВЫДАН VIP: {uid}")
            staff_msg = f"👑 **ВЕБ-АДМИНКА: КОРОНАЦИЯ (VIP)**\n\n• **Пользователь:** `{uid}`"
            try: bot.send_message(uid, "👑 Администрация выдала вам статус VIP через панель управления!")
            except: pass
        elif action == 'remove_vip':
            users_collection.update_one({"_id": uid}, {"$set": {"is_vip": False}})
            msg = f"❌ VIP снят с {uid}."
            add_radar_log(f"❌ СНЯТ VIP: {uid}")
            staff_msg = f"❌ **ВЕБ-АДМИНКА: СНЯТИЕ VIP**\n\n• **Пользователь:** `{uid}`"
            try: bot.send_message(uid, "❌ Ваш статус VIP аннулирован администрацией.")
            except: pass
        elif action == 'make_queer':
            users_collection.update_one({"_id": uid}, {"$set": {"is_queer": True}}, upsert=True)
            msg = f"🏳️‍🌈 {uid} добавлен в BEYOND!"
            add_radar_log(f"🏳️‍🌈 ВЫДАН QUEER: {uid}")
            staff_msg = f"🏳️‍🌈 **ВЕБ-АДМИНКА: ДОСТУП BEYOND**\n\n• **Пользователь:** `{uid}`"
            try: bot.send_message(uid, "🏳️‍🌈 Администрация предоставила вам доступ к клубу BEYOND!")
            except: pass
        elif action == 'remove_queer':
            users_collection.update_one({"_id": uid}, {"$set": {"is_queer": False}})
            msg = f"⚠️ {uid} удален из BEYOND."
            add_radar_log(f"⚠️ СНЯТ QUEER: {uid}")
            staff_msg = f"⚠️ **ВЕБ-АДМИНКА: ИСКЛЮЧЕНИЕ ИЗ BEYOND**\n\n• **Пользователь:** `{uid}`"
        elif action == 'set_tag':
            new_tag = request.form.get('tag', '').strip()
            if new_tag and new_tag.lower() != 'none':
                users_collection.update_one({"_id": uid}, {"$set": {"custom_tag": new_tag}}, upsert=True)
                msg = f"🎖️ Выдан тег: {new_tag}"
                add_radar_log(f"🎖️ ТЕГ [{new_tag}]: {uid}")
                staff_msg = f"🎖️ **ВЕБ-АДМИНКА: ВЫДАЧА ПОГОН**\n\n• **Пользователь:** `{uid}`\n• **Тег:** `{new_tag}`"
            else:
                users_collection.update_one({"_id": uid}, {"$unset": {"custom_tag": ""}})
                msg = "🧹 Тег аннулирован."
                add_radar_log(f"🧹 СНЯТ ТЕГ: {uid}")
                staff_msg = f"🧹 **ВЕБ-АДМИНКА: СБРОС ТЕГА**\n\n• **Пользователь:** `{uid}`"
        elif action == 'set_city':
            new_city = request.form.get('city', '').strip()
            users_collection.update_one({"_id": uid}, {"$set": {"main_city": new_city}}, upsert=True)
            msg = f"📍 Город изменен на: {new_city}"
            add_radar_log(f"📍 ГОРОД [{new_city}]: {uid}")
            staff_msg = f"📍 **ВЕБ-АДМИНКА:** Изменен город для `{uid}` на `{new_city}`"
        elif action == 'mute_temp':
            hours = request.form.get('hours', '24')
            try: hours_i = max(1, int(float(hours)))
            except ValueError: hours_i = 24
            # Раньше срок не передавался, и «мут на 24 ч» становился вечным
            mute_user_everywhere(uid, reason=f"Профилактический Web-мут на {hours_i} ч.", admin_name="Web-Саурон 👁️",
                                 mute_time=int(time.time()) + hours_i * 3600)
            msg = f"🤐 Пользователь {uid} отправлен в мут на {hours} ч."
            add_radar_log(f"🤐 МУТ [{hours} ч.]: {uid}")
            staff_msg = f"🤐 **ВЕБ-АДМИНКА: ПРОФИЛАКТИЧЕСКИЙ МУТ**\n\n• **Пользователь:** `{uid}`\n• **Срок:** `{hours} ч.`"
        if log_to_staff and staff_msg:
            try: bot.send_message(STAFF_GROUP_ID, staff_msg, parse_mode="Markdown")
            except: pass
        return jsonify({"success": True, "message": msg})

    @app.route('/glaz/api/add_balance', methods=['POST'])
    def api_add_balance():
        if not session.get('logged_in'): 
            return jsonify({"success": False, "error": "Unauthorized"}), 401
        
        uid = int(request.form.get('uid'))
        currency = request.form.get('currency') # Ожидаем 'points' или 'shards'
        amount = int(request.form.get('amount', 0))
        
        admin_info = "Web-Саурон 👁️"
        
        if amount == 0:
            return jsonify({"success": False, "error": "Сумма не может быть нулем"})

        from core.janitor import log_points
        if currency == 'points':
            db['paid_users'].update_one({"uid": uid}, {"$inc": {"bounty_points": amount}}, upsert=True)
            log_points(uid, "bounty_points", amount, reason=f"web_admin:{session.get('login', '?')}")
            msg = f"💰 Очки Бдительности {'добавлены' if amount > 0 else 'списаны'}: {amount}"
            add_radar_log(f"💰 БАЛАНС ОЧКОВ [{amount}]: {uid}")
            staff_msg = f"💰 **ВЕБ-АДМИНКА: ИЗМЕНЕНИЕ ОЧКОВ**\n\n• **Юзер:** `{uid}`\n• **Изменение:** `{amount}` очков"
            user_msg = f"🎁 **Уведомление!**\nАдминистрация изменила ваш баланс Очков Бдительности на **{amount}**."
            
        elif currency == 'shards':
            db['paid_users'].update_one({"uid": uid}, {"$inc": {"jackpot_shards": amount}}, upsert=True)
            log_points(uid, "jackpot_shards", amount, reason=f"web_admin:{session.get('login', '?')}")
            msg = f"🧩 Осколки {'добавлены' if amount > 0 else 'списаны'}: {amount}"
            add_radar_log(f"🧩 БАЛАНС ОСКОЛКОВ [{amount}]: {uid}")
            staff_msg = f"🧩 **ВЕБ-АДМИНКА: ИЗМЕНЕНИЕ ОСКОЛКОВ**\n\n• **Юзер:** `{uid}`\n• **Изменение:** `{amount}` осколков"
            user_msg = f"🧩 **Уведомление!**\nАдминистрация изменила ваш баланс Осколков на **{amount}**."
            
        else:
            return jsonify({"success": False, "error": "Неизвестная валюта"})

        # Отправляем уведомления
        try: bot.send_message(STAFF_GROUP_ID, staff_msg, parse_mode="Markdown")
        except: pass
        
        # Если начисляем в плюс, радуем юзера сообщением
        if amount > 0:
            try: bot.send_message(uid, user_msg, parse_mode="Markdown")
            except: pass

        return jsonify({"success": True, "message": msg})

    @app.route('/glaz/api/system_settings')
    def api_system_settings():
        if not session.get('logged_in'): return jsonify({"error": "Unauthorized"}), 401
        return jsonify(SkynetSettings.get())

    @app.route('/glaz/toggle_setting', methods=['POST'])
    def toggle_setting():
        if not session.get('logged_in'): return jsonify({"success": False}), 401
        setting_name = request.form.get('setting')
        if not setting_name: return jsonify({"success": False, "error": "No setting specified"})
        current = SkynetSettings.get()
        new_val = not current.get(setting_name, True)
        SkynetSettings.set(setting_name, new_val)
        add_radar_log(f"⚙️ Тумблер: {setting_name} ➡️ {'ВКЛ' if new_val else 'ВЫКЛ'}")
        return jsonify({"success": True, "state": new_val, "setting": setting_name})

    @app.route('/glaz/api/quarantine_list')
    def api_quarantine_list():
        if not session.get('logged_in'): return jsonify({"error": "Unauthorized"}), 401
        
        # 1. Достаем динамический лимит из базы (по умолчанию 120, если вдруг пусто)
        mod_settings = db['settings'].find_one({"_id": "moderation_limits"}) or {}
        quaran_hours = int(mod_settings.get("quaran_hours", 120))
        quaran_seconds = quaran_hours * 3600
        
        now = time.time()
        threshold = now - quaran_seconds
        
        newbies = list(users_collection.find({"_id": {"$gt": 7800000000}, "first_seen": {"$gt": threshold}}))
        
        result = []
        for u in newbies:
            sec_left = quaran_seconds - (now - u['first_seen'])
            if sec_left > 0:
                result.append({
                    "id": u["_id"], 
                    "seconds_left": int(sec_left)
                })
                
        # Возвращаем и список юзеров, и сам лимит для текста на сайте
        return jsonify({
            "limit_hours": quaran_hours,
            "users": result
        })

    @app.route('/glaz/release_quarantine', methods=['POST'])
    def release_quarantine():
        if not session.get('logged_in'): return jsonify({"success": False}), 401
        uid = int(request.form.get('uid'))
        users_collection.update_one({"_id": uid}, {"$set": {"first_seen": 0}}) 
        add_radar_log(f"🥷 Амнистия новорега: {uid}")
        try: bot.send_message(uid, "Base Администрация досрочно сняла с вас карантин! Можете писать в чаты.")
        except: pass
        return jsonify({"success": True})

    @app.route('/glaz/api/preview_broadcast', methods=['POST'])
    def api_preview_broadcast():
        if not session.get('logged_in'): return jsonify({"success": False, "error": "Unauthorized"}), 401
        
        data = request.json
        txt = data.get('text', '')
        buttons_list = data.get('buttons', [])
        
        from telebot import types
        markup = None
        if buttons_list:
            markup = types.InlineKeyboardMarkup(row_width=1)
            for btn in buttons_list:
                kwargs = {"text": btn["text"], "url": btn["url"]}
                if btn.get("style") and btn["style"] != "default": kwargs["style"] = btn["style"]
                if btn.get("emoji_id"): kwargs["icon_custom_emoji_id"] = btn["emoji_id"]
                markup.add(types.InlineKeyboardButton(**kwargs))
                
        try:
            # Отправляем тестовое сообщение создателю (OWNER_ID берется из config.py)
            bot.send_message(
                OWNER_ID, 
                f"👀 **ПРЕДПРОСМОТР РАССЫЛКИ:**\n\n{txt}", 
                parse_mode="HTML", 
                disable_web_page_preview=True, 
                reply_markup=markup
            )
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    # 👇 НОВЫЙ БЛОК: ТРЕКИНГ КЛИКОВ, ТАЙМЕР И РАССЫЛКА 👇
    @app.route('/t/<link_id>')
    def track_link(link_id):
        # Находим шпионскую ссылку, плюсуем клик и перекидываем юзера
        link_data = db['tracked_links'].find_one_and_update(
            {"_id": link_id}, {"$inc": {"clicks": 1}}
        )
        if link_data: return redirect(link_data["url"])
        return "Ссылка не найдена или устарела", 404

    def execute_broadcast(txt, tgt, btns_list):
        from telebot import types
        import time
        from config import all_cities # <-- Достаем список всех городов и чатов
        
        markup = None
        if btns_list:
            markup = types.InlineKeyboardMarkup(row_width=1)
            for btn in btns_list:
                kwargs = {"text": btn["text"], "url": btn["url"]}
                if btn.get("style") and btn["style"] != "default": kwargs["style"] = btn["style"]
                if btn.get("emoji_id"): kwargs["icon_custom_emoji_id"] = btn["emoji_id"]
                markup.add(types.InlineKeyboardButton(**kwargs))
                
        add_radar_log(f"🚀 Запуск рассылки для: {tgt}")
        
        count = 0
        dead_count = 0

        # 👇 НОВЫЙ БЛОК: РАССЫЛКА ПО ВСЕМ ГРУППАМ СЕТИ 👇
        if tgt == 'groups_all':
            group_ids = set() # Используем set, чтобы исключить дубликаты
            for city, networks in all_cities.items():
                for net_key, groups in networks.items():
                    for group in groups:
                        group_ids.add(group['chat_id'])
            
            for chat_id in group_ids:
                try:
                    bot.send_message(chat_id, txt, parse_mode="HTML", disable_web_page_preview=True, reply_markup=markup)
                    count += 1
                    time.sleep(0.5) # Пауза для групп чуть больше (0.5 сек), чтобы ТГ не дал Flood Wait
                except Exception as e:
                    print(f"Ошибка рассылки в чат {chat_id}: {e}")
                    
            add_radar_log(f"✅ Рассылка по группам: Доставлено в {count} чатов.")
            try: bot.send_message(STAFF_GROUP_ID, f"🚀 **Рассылка по ГРУППАМ завершена!**\n✅ Опубликовано в: {count} чатов.")
            except: pass
            return
        # 👆 ========================================== 👆

        # --- СТАРАЯ ЛОГИКА ДЛЯ ЛС ПОЛЬЗОВАТЕЛЕЙ ---
        cursor = users_collection.find({}) if tgt == 'all' else users_collection.find({"is_vip": True}) if tgt == 'vip' else users_collection.find({"is_queer": True}) if tgt == 'queer' else None
        if not cursor: return
        
        for u in cursor:
            try:
                bot.send_message(u['_id'], txt, parse_mode="HTML", disable_web_page_preview=True, reply_markup=markup)
                count += 1
                time.sleep(0.05) # Искусственная пауза (~20 сообщений в секунду)
                
            except Exception as e:
                err_text = str(e).lower()
                
                # 1. Обработка лимитов Telegram (ждем, если просят)
                if "too many requests" in err_text:
                    try:
                        import re
                        wait_time = int(re.search(r'retry after (\d+)', err_text).group(1))
                    except:
                        wait_time = 3 
                    time.sleep(wait_time)
                    
                # 2. Пользователь УДАЛИЛ аккаунт (настоящий "труп")
                elif "deactivated" in err_text:
                    users_collection.delete_one({"_id": u['_id']})
                    dead_count += 1
                    # 🔥 Тихо выносим удалённый аккаунт из чатов (тумблер «Автоочистка трупов»).
                    # Раньше на каждый труп шёл полноценный #BAN с отчётами в журнал и STAFF.
                    if SkynetSettings.get().get("auto_corpse_removal", True):
                        threading.Thread(target=background_corpse_removal, args=(u['_id'],), daemon=True).start()
                        
                # 3. Пользователь просто заблокировал бота в ЛС (он жив, но рассылку не хочет)
                elif "blocked" in err_text:
                    pass # <--- МЫ ПРОСТО ИГНОРИРУЕМ ОШИБКУ. Из базы он НЕ удаляется!
                    
                # 4. Ошибка форматирования текста
                elif "parse entities" in err_text:
                    try:
                        bot.send_message(u['_id'], txt, disable_web_page_preview=True, reply_markup=markup)
                        count += 1
                        time.sleep(0.05)
                    except Exception:
                        pass

        add_radar_log(f"✅ Рассылка: Доставлено {count}. 💀 Вывезено трупов: {dead_count}")
        try: bot.send_message(STAFF_GROUP_ID, f"🚀 **Рассылка завершена!**\nЦель: {tgt}\n✅ Доставлено: {count} чел.\n💀 Мертвых душ удалено: {dead_count}")
        except: pass

    # Демон, который проверяет отложенные рассылки каждую минуту
    def scheduled_daemon():
        while True:
            try:
                now = time.time()
                # Атомарно «забираем» рассылку: при нескольких воркерах она уходила дважды
                while True:
                    t = db['scheduled_broadcasts'].find_one_and_update(
                        {"status": "pending", "run_at": {"$lte": now}},
                        {"$set": {"status": "done", "started_at": now}}
                    )
                    if not t:
                        break
                    execute_broadcast(t["text"], t["target"], t.get("buttons", []))
            except Exception as e: print(e)
            time.sleep(30)
            
    threading.Thread(target=scheduled_daemon, daemon=True).start()

    @app.route('/glaz/broadcast', methods=['POST'])
    def glaz_broadcast():
        if not session.get('logged_in'): return jsonify({"success": False}), 401
        text = request.form.get('text')
        target = request.form.get('target')
        run_at_str = request.form.get('run_at')
        buttons_raw = request.form.get('buttons', '[]')
        
        try: buttons_list = json.loads(buttons_raw)
        except: buttons_list = []

        # 🎯 ТРЕКЕР КЛИКОВ: Превращаем обычные ссылки в наши трекеры
        host_url = request.url_root.replace('http://', 'https://')
        for btn in buttons_list:
            if btn.get("url") and btn["url"].startswith("http"):
                link_id = str(uuid.uuid4())[:8]
                db['tracked_links'].insert_one({
                    "_id": link_id, "url": btn["url"], "clicks": 0, 
                    "name": btn["text"], "timestamp": time.time()
                })
                btn["url"] = f"{host_url}t/{link_id}"

        # ⏰ ТАЙМЕР: Проверка на отложенный запуск
        if run_at_str:
            try:
                import pytz # На всякий случай импортируем библиотеку
                
                # Указываем ваш часовой пояс (если нужна Москва, напишите 'Europe/Moscow')
                tz = pytz.timezone('Asia/Yekaterinburg') 
                
                # Читаем время и привязываем к нему часовой пояс
                naive_dt = datetime.strptime(run_at_str, "%Y-%m-%dT%H:%M")
                run_at_ts = tz.localize(naive_dt).timestamp()
                
                if run_at_ts > time.time():
                    db['scheduled_broadcasts'].insert_one({
                        "text": text, "target": target, "buttons": buttons_list,
                        "run_at": run_at_ts, "status": "pending"
                    })
                    add_radar_log(f"⏰ Рассылка отложена до {run_at_str.replace('T', ' ')}")
                    return jsonify({"success": True, "message": "⏰ Рассылка успешно поставлена в очередь!"})
            except Exception as e: print("Ошибка таймера:", e)

        # 🚀 ЗАПУСК СЕЙЧАС (если дата не указана)
        threading.Thread(target=execute_broadcast, args=(text, target, buttons_list), daemon=True).start()
        return jsonify({"success": True, "message": "🚀 Рассылка запущена в фоне!"})

    @app.route('/glaz/api/broadcast_stats')
    def api_broadcast_stats():
        if not session.get('logged_in'): return jsonify({"error": "Unauthorized"}), 401
        # Собираем очередь
        scheduled = list(db['scheduled_broadcasts'].find({"status": "pending"}).sort("run_at", 1))
        sched_res = [{"id": str(s["_id"]), "target": s["target"], "time": datetime.fromtimestamp(s["run_at"]).strftime("%d.%m.%Y %H:%M")} for s in scheduled]
        # Собираем статистику кликов
        links = list(db['tracked_links'].find().sort("timestamp", -1).limit(50))
        links_res = [{"id": L["_id"], "name": L.get("name", "Кнопка"), "url": L.get("url", ""), "clicks": L.get("clicks", 0), "time": datetime.fromtimestamp(L.get("timestamp", time.time())).strftime("%d.%m.%Y %H:%M")} for L in links]
            
        return jsonify({"scheduled": sched_res, "links": links_res})
    
    @app.route('/glaz/api/cancel_broadcast', methods=['POST'])
    def api_cancel_broadcast():
        if not session.get('logged_in'): return jsonify({"success": False}), 401
        from bson import ObjectId
        db['scheduled_broadcasts'].delete_one({"_id": ObjectId(request.json.get("id"))})
        return jsonify({"success": True})
    # 👆 ================================================== 👆

    @app.route('/glaz/api/dictionary', methods=['GET'])
    def get_dictionary():
        data = db['settings'].find_one({"_id": "skynet_dictionary"}) or {"red": [], "yellow": [], "black": []}
        return jsonify({
            "red": data.get("red", []), 
            "yellow": data.get("yellow", []), 
            "black": data.get("black", [])
        })

    @app.route('/glaz/api/dictionary/add', methods=['POST'])
    def add_dictionary_word():
        if not session.get('logged_in'): return jsonify({"success": False, "error": "Unauthorized"}), 401
        data = request.json
        word = data.get('word', '').strip().lower()
        zone = data.get('zone')
        exact = data.get('exact', False)
        if not word or zone not in ['red', 'yellow', 'black']: return jsonify({"success": False, "error": "Некорректные данные"})
        if exact: pattern = rf"\b{word}\b"
        else: pattern = rf"\b{word}[а-я]*\b"
        new_entry = {"word": word, "pattern": pattern, "exact": exact}
        db['settings'].update_one({"_id": "skynet_dictionary"}, {"$push": {zone: new_entry}}, upsert=True)
        return jsonify({"success": True, "message": f"Слово '{word}' добавлено в {zone} zone!"})

    @app.route('/glaz/api/dictionary/remove', methods=['POST'])
    def remove_dictionary_word():
        if not session.get('logged_in'): return jsonify({"success": False}), 401
        data = request.json
        word = data.get('word')
        zone = data.get('zone')
        db['settings'].update_one({"_id": "skynet_dictionary"}, {"$pull": {zone: {"word": word}}})
        return jsonify({"success": True})

    @app.route('/glaz/api/system_texts', methods=['GET'])
    def api_get_system_texts():
        if not session.get('logged_in'): return jsonify({"error": "Unauthorized"}), 401
        data = db['settings'].find_one({"_id": "skynet_texts"}) or {}
        return jsonify({
            "quarantine_warn": data.get("quarantine_warn", "🚨 {user_link}, **Защита от спама!**\nВаш аккаунт создан недавно. Для безопасности сети действует карантин 48 часов.\nПодождите, или пройдите верификацию в [Службе Поддержки](https://t.me/MK_MensClubSUPPORT)."),
            "may_1_warn": data.get("may_1_warn", "🚨 {user_link}, **ВНИМАНИЕ!**\n\nС 1 мая введен СТРОГИЙ стандарт оформления анкет для досок объявлений.\nЛюбой текст **БЕЗ ПАРАМЕТРОВ** или с неправильным форматом запрещен!\nПараметры должны быть указаны **ТОЛЬКО через слеш (/) без пробелов и лишних слов**.\n\n✅ *Примеры:* `24/187/72` или `24/187/72/19` (допускается `19.5` или `19*4`)\n\nВаша анкета удалена, а вы временно ограничены в общении во всех группах сети.\n\n💡 *P.S. В нашей сети «ПАРНИ 18+» нет ограничений на формат текста и разрешен любой откровенный контент (включая порно). Переходи туда! 👇*"),
            "minor_warn": data.get("minor_warn", "🚨 {user_link}, **Внимание!**\nВаша анкета попала под автоматический фильтр безопасности сети. Пройдите обязательную верификацию 🔞.")
        })

    @app.route('/glaz/api/system_texts/save', methods=['POST'])
    def api_save_system_texts():
        if not session.get('logged_in'): return jsonify({"success": False}), 401
        data = request.json
        db['settings'].update_one(
            {"_id": "skynet_texts"},
            {"$set": {
                "quarantine_warn": data.get("quarantine_warn"),
                "may_1_warn": data.get("may_1_warn"),
                "minor_warn": data.get("minor_warn")
            }},
            upsert=True
        )
        add_radar_log("📝 Тексты системных предупреждений обновлены!")
        return jsonify({"success": True, "message": "✅ Тексты успешно вшиты в нейросеть!"})

    @app.route('/glaz/api/send_sauron_msg', methods=['POST'])
    def api_send_sauron_msg():
        if not session.get('logged_in'): return jsonify({"success": False, "error": "Unauthorized"}), 401
        uid = request.form.get('uid')
        message_text = request.form.get('message')
        if not uid or not message_text: return jsonify({"success": False, "error": "Пустые данные"}), 400
        try:
            bot.send_message(chat_id=int(uid), text=f"👑 **Сообщение от Администрации:**\n\n{message_text}", parse_mode="Markdown")
            add_radar_log(f"👁‍🗨 Голос Саурона: Отправлено ЛС юзеру {uid}")
            return jsonify({"success": True})
        except Exception as e:
            err_str = str(e).lower()
            if "bot was blocked" in err_str or "deactivated" in err_str:
                return jsonify({"success": False, "error": "Пользователь заблокировал бота или удален 💀"})
            return jsonify({"success": False, "error": "Ошибка API Telegram"})

    @app.route('/glaz/api/templates', methods=['GET'])
    def api_get_templates():
        templates = list(db['templates'].find({}, {"_id": 0}))
        return jsonify(templates)

    @app.route('/glaz/api/templates/save', methods=['POST'])
    def api_save_template():
        if not session.get('logged_in'): return jsonify({"success": False}), 401
        data = request.json
        new_template = {
            "id": str(uuid.uuid4())[:8],
            "name": data.get("name", "Новый шаблон"),
            "text": data.get("text", ""),
            "target": data.get("target", "all"),
            "buttons": data.get("buttons", []),
            "autopilot_interval": 0,
            "last_run": 0
        }
        db['templates'].insert_one(new_template)
        return jsonify({"success": True, "message": "Шаблон сохранен!"})

    @app.route('/glaz/api/templates/delete', methods=['POST'])
    def api_delete_template():
        if not session.get('logged_in'): return jsonify({"success": False}), 401
        db['templates'].delete_one({"id": request.json.get("id")})
        return jsonify({"success": True})

    @app.route('/glaz/api/templates/toggle_autopilot', methods=['POST'])
    def api_toggle_autopilot():
        if not session.get('logged_in'): return jsonify({"success": False}), 401
        tid = request.json.get("id")
        interval = int(request.json.get("interval", 0))
        db['templates'].update_one({"id": tid}, {"$set": {"autopilot_interval": interval, "last_run": 0}})
        return jsonify({"success": True})

    @app.route('/glaz/api/root/buttons', methods=['POST'])
    def api_get_root_buttons():
        data = request.json
        if data.get('pin') != ROOT_PIN:
            return jsonify({"error": "Access Denied"}), 403
        
        btns = db['settings'].find_one({"_id": "skynet_buttons"})
        if not btns or not btns.get("buttons"):
            default_btns = [
                {"text": "Подписаться на МК", "url": "https://t.me/своя_ссылка"},
                {"text": "ПАРНИ 18+", "url": "https://t.me/znakparni"}
            ]
            return jsonify({"buttons": default_btns})
        return jsonify({"buttons": btns.get("buttons", [])})

    @app.route('/glaz/api/root/save_buttons', methods=['POST'])
    def api_save_root_buttons():
        data = request.json
        if data.get('pin') != ROOT_PIN:
            return jsonify({"error": "Access Denied"}), 403
        
        new_buttons = data.get('buttons', [])
        # Теперь кнопки сохраняются правильно в общую таблицу настроек!
        db['settings'].update_one(
            {"_id": "skynet_buttons"}, 
            {"$set": {"buttons": new_buttons}}, 
            upsert=True
        )
        return jsonify({"status": "ok"})

    # Мини-приложение публикации переехало в web/poster.py

# 👇 УНИВЕРСАЛЬНЫЙ КРИПТО-КАССИР ДЛЯ VIP, ШТРАФОВ И ГОРОДОВ 👇
    @app.route('/glaz/api/cryptobot_webhook', methods=['POST'])
    def cryptobot_webhook():
        from web_auth import verify_cryptobot_signature
        raw = request.get_data()
        if not verify_cryptobot_signature(raw, request.headers.get("crypto-pay-api-signature", "")):
            return jsonify({"status": "forbidden"}), 403
        try:
            data = json.loads(raw)
        except ValueError:
            return jsonify({"status": "bad json"}), 400
        if not data or data.get("update_type") != "invoice_paid":
            return jsonify({"status": "ignored"}), 200

        invoice = data.get("payload", {}) or {}
        payload_str = str(invoice.get("payload", ""))
        try:
            amount_rub = float(invoice.get("amount", 0))
        except (TypeError, ValueError):
            amount_rub = 0.0
        invoice_id = str(invoice.get("invoice_id") or data.get("update_id") or "")

        # CryptoBot повторяет вебхук, если ответ не дошёл. Раньше повтор выдавал доступ/ссылки
        # и писал доход ещё раз. Теперь каждый счёт обрабатывается один раз.
        if invoice_id:
            try:
                db['crypto_payments'].insert_one({"_id": invoice_id, "payload": payload_str, "amount_rub": amount_rub,
                                                  "asset": invoice.get("paid_asset"), "ts": time.time()})
            except DuplicateKeyError:
                return jsonify({"status": "duplicate"}), 200

        try:
            _process_crypto_invoice(payload_str, amount_rub)
        except Exception as e:
            # Снимаем отметку, чтобы CryptoBot прислал вебхук ещё раз
            if invoice_id:
                db['crypto_payments'].delete_one({"_id": invoice_id})
            add_radar_log(f"⚠️ Ошибка крипто-кассы ({payload_str}): {e}")
            from core.diag import log_error; log_error("Крипто-касса (упала)", f"{payload_str}: {e}")
            print(f"cryptobot_webhook error: {e}")
            return jsonify({"status": "error"}), 500
        return jsonify({"status": "ok"}), 200

    def _rub_ok(amount_rub, price_stars):
        """Крипто-счёт выставляется в рублях: звёзды × курс из панели. Допуск 1₽ на округление.
        До 25.10.2026 принимаем и старый курс 1,8: счета, выставленные до перехода на 2 ₽, ещё гуляют."""
        rate = cfg("rub_per_star")
        if time.time() < _LEGACY_RATE_UNTIL:
            rate = min(rate, 1.8)
        return amount_rub + 1 >= int(price_stars * rate)

    def _rub(price_stars):
        return int(price_stars * cfg("rub_per_star"))

    def _staff(text):
        if text.startswith("⚠️") or text.startswith("🚨"):
            from core.diag import log_error
            log_error("Крипто-касса", text.replace("**", "").replace("`", ""))
        try: bot.send_message(STAFF_GROUP_ID, text, parse_mode="Markdown")
        except Exception:
            try: bot.send_message(STAFF_GROUP_ID, text)
            except Exception: pass

    def _process_crypto_invoice(payload_str, amount_rub):
        import random
        from config import BEYOND_CHAT_ID
        from handlers.vip import grant_vip_access, expected_vip_price, is_fine_eligible
        # ВАЖНО: раньше внутри функции стоял `import time` (в ветке BEYOND), из-за чего Python считал
        # time локальной переменной, и ветки рекламы и донатов падали с UnboundLocalError:
        # оплаченная криптой реклама не записывалась вообще.

        # 👑 1. ОПЛАТА VIP-КЛУБА
        if payload_str.startswith("vip_"):
            uid = int(payload_str.replace("vip_", ""))
            expected = expected_vip_price(uid)
            if not _rub_ok(amount_rub, expected):
                # Цена в кнопках раньше подделывалась — сверяем с серверной ценой заявки
                _staff(f"⚠️ **Крипто-оплата VIP меньше цены!**\nЮзер `{uid}` заплатил {amount_rub}₽, ожидалось {_rub(expected)}₽. Доступ НЕ выдан, решите вручную.")
                try: bot.send_message(uid, "⚠️ Сумма оплаты не совпала с ценой VIP. Администрация свяжется с вами.")
                except Exception: pass
                return
            db['daily_revenue'].insert_one({"type": "vip", "amount": amount_rub, "currency": "RUB", "uid": uid, "timestamp": time.time(), "date": datetime.now().strftime("%d.%m.%Y")})
            grant_vip_access(bot, uid, unmute_user_everywhere, unban_user_everywhere, f"крипта: {amount_rub}₽")

        # 🏳️‍🌈 1.4. ШТРАФ BEYOND (раньше попадал в ветку beyond_ и падал на int("fine_123"))
        elif payload_str.startswith("beyond_fine_"):
            uid = int(payload_str.replace("beyond_fine_", ""))
            pricing = db['settings'].find_one({"_id": "skynet_pricing"}) or {}
            price = int(pricing.get("beyond_price", 250))
            ban = banned_collection.find_one({"_id": uid})
            if not _rub_ok(amount_rub, price):
                _staff(f"⚠️ **Штраф BEYOND (крипта) меньше цены!** Юзер `{uid}`: {amount_rub}₽ вместо {_rub(price)}₽. Бан НЕ снят.")
                return
            if not ban or not is_fine_eligible(ban.get("reason")):
                _staff(f"⚠️ **Штраф BEYOND оплачен криптой ({amount_rub}₽)**, но у `{uid}` " + ("нет бана" if not ban else "тяжёлый бан") + ". Ничего не снято — решите вручную.")
                try: bot.send_message(uid, "ℹ️ Оплата штрафа получена. Администрация проверит её вручную и свяжется с вами.")
                except Exception: pass
                return
            db['daily_revenue'].insert_one({"type": "fine", "amount": amount_rub, "currency": "RUB", "uid": uid, "timestamp": time.time(), "date": datetime.now().strftime("%d.%m.%Y"), "source": "crypto_beyond"})
            banned_collection.delete_one({"_id": uid})
            db['skynet_tasks'].insert_one({"uid": uid, "action": "fine_unban", "amount": price, "timestamp": time.time(), "source": "beyond_crypto"})
            users_collection.update_one({"_id": uid}, {"$set": {"fine_paid_pending_beyond": True}}, upsert=True)
            _staff(f"🏳️‍🌈 **ШТРАФ BEYOND ОПЛАЧЕН КРИПТОЙ!**\nЮзер: `{uid}`\nСумма: {amount_rub} руб.")
            try: bot.send_message(uid, "✅ **Штраф оплачен!**\nВернитесь в бот BEYOND и нажмите /start, чтобы подать анкету с чистого листа.", parse_mode="Markdown")
            except Exception: pass

        # 🏳️‍🌈 1.5. ОПЛАТА BEYOND КЛУБА
        elif payload_str.startswith("beyond_"):
            uid = int(payload_str.replace("beyond_", ""))
            funnel = db['beyond_funnel'].find_one({"_id": uid}) or {}
            pricing = db['settings'].find_one({"_id": "skynet_pricing"}) or {}
            price = int(funnel.get("price") or pricing.get("beyond_price", 250))
            step = funnel.get("step")
            problem = None
            if not _rub_ok(amount_rub, price):
                problem = f"сумма {amount_rub}₽ меньше цены {_rub(price)}₽"
            elif banned_collection.find_one({"_id": uid}):
                problem = "активный бан в сети"   # как в BEYOND-боте: оплата больше не снимает любой бан
            elif not funnel or (step is not None and step not in ("waiting_payment", "paying", "decided")):
                problem = f"заявка не на шаге оплаты (шаг: {step or 'нет анкеты'})"
            if problem:
                _staff(f"⚠️ **Оплата BEYOND криптой ({amount_rub}₽) от** `{uid}`: доступ НЕ выдан — {problem}. Решите вручную.")
                try: bot.send_message(uid, "ℹ️ Оплата получена, но доступ требует ручной проверки. Администрация свяжется с вами.")
                except Exception: pass
                return

            users_collection.update_one(
                {"_id": uid},
                {"$set": {"is_queer": True, "beyond_access": True, "beyond_joined_at": time.time(), "custom_tag": "𝐐𝐔𝐄𝐄𝐑 ♛"},
                 "$unset": {"shame_tag": "", "fine_paid_pending_beyond": "", "beyond_verdict": "", "beyond_verdict_at": ""}},
                upsert=True
            )
            db['beyond_funnel'].delete_one({"_id": uid})
            db['daily_revenue'].insert_one({"type": "beyond", "amount": amount_rub, "currency": "RUB", "uid": uid, "timestamp": time.time(), "date": datetime.now().strftime("%d.%m.%Y")})
            _staff(f"🏳️‍🌈 **ОПЛАТА BEYOND (КРИПТА)!**\nЮзер: `{uid}`\nСумма: {amount_rub} руб.")
            try:
                invite = bot.create_chat_invite_link(BEYOND_CHAT_ID, member_limit=1, expire_date=int(time.time()) + 7 * 86400)
                bot.send_message(uid, f"🎉 **Крипто-оплата BEYOND успешно получена!**\n\n👉 [ВХОД В BEYOND]({invite.invite_link})", parse_mode="Markdown", disable_web_page_preview=True)
            except Exception as e:
                _staff(f"🚨 Не удалось выдать ссылку BEYOND юзеру `{uid}`: {e}")

        # 🚨 2. ОПЛАТА ШТРАФА (АМНИСТИЯ)
        elif payload_str.startswith("fine_"):
            uid = int(payload_str.replace("fine_", ""))
            # Секретарь теперь запоминает сумму выставленного штрафа (pay_offers). Сверяем.
            offer = db['pay_offers'].find_one({"_id": f"{uid}:fine"})
            if offer and not _rub_ok(amount_rub, int(offer.get("amount", 0))):
                _staff(f"⚠️ **Штраф криптой меньше выставленного!** `{uid}`: {amount_rub}₽ вместо {_rub(offer['amount'])}₽. Разбан НЕ выполнен.")
                return
            db['pay_offers'].delete_one({"_id": f"{uid}:fine"})
            db['daily_revenue'].insert_one({"type": "fine", "amount": amount_rub, "currency": "RUB", "uid": uid, "timestamp": time.time(), "date": datetime.now().strftime("%d.%m.%Y")})
            now = datetime.now()
            ticket_num = now.strftime("%d%m%Y%H%M%S") + f"-{random.randint(100, 999)}"
            # Сумму передаём в звёздах: по ней Скайнет выдаёт теги «Свободен» (650⭐️) / «Спонсор» (750⭐️).
            # Раньше её не было, и тег брался по последнему ЗВЁЗДНОМУ платежу юзера.
            # Если Секретарь запомнил сумму штрафа в звёздах — берём её (он считает рубли по своему курсу).
            stars_equiv = int(offer["amount"]) if offer and offer.get("amount") else int(round(amount_rub / cfg("rub_per_star")))
            db['skynet_tasks'].insert_one({"uid": uid, "action": "fine_unban", "amount": stars_equiv, "timestamp": now})
            archive_collection.update_one({"target": str(uid)}, {"$push": {"history": {"date": now.strftime("%d.%m.%Y %H:%M"), "action": "Разблокировка (Крипта)", "reason": "Штраф оплачен"}}}, upsert=True)

            user_data = db['paid_users'].find_one({"uid": uid})
            thread_id = user_data.get("thread_id") if user_data else None
            db['paid_users'].update_one({"uid": uid}, {"$set": {"status": 0}, "$unset": {"topic_type": ""}})

            try:
                if thread_id:
                    bot.send_message(STAFF_GROUP_ID, f"🤑 **ШТРАФ ОПЛАЧЕН КРИПТОЙ!**\nЮзер: `{uid}`\nСумма: {amount_rub} руб.", message_thread_id=thread_id, parse_mode="Markdown")
                    bot.close_forum_topic(STAFF_GROUP_ID, thread_id)
                else:
                    bot.send_message(STAFF_GROUP_ID, f"🤑 **ШТРАФ ОПЛАЧЕН КРИПТОЙ!**\nЮзер: `{uid}`\nСумма: {amount_rub} руб.", parse_mode="Markdown")
                bot.send_message(uid, f"✅ **Оплата штрафа получена!**\n\nОграничения сняты. Уникальный номер: `{ticket_num}`\n*Больше не нарушайте правила!*", parse_mode="Markdown")
            except Exception: pass

        # 📜 2.5 ИНДУЛЬГЕНЦИЯ КРИПТОЙ (Секретарь выставлял такой счёт, но Скайнет его не обрабатывал:
        # деньги приходили, а человеку ничего не выдавалось). Логика — как у оплаты звёздами в Секретаре.
        elif payload_str.startswith("indulgence_"):
            uid = int(payload_str.replace("indulgence_", ""))
            offer = db['pay_offers'].find_one({"_id": f"{uid}:indulgence"})
            price = int(offer.get("amount") or cfg("indulgence_price")) if offer else cfg("indulgence_price")
            if not _rub_ok(amount_rub, price):
                _staff(f"⚠️ **Индульгенция криптой меньше цены!** `{uid}`: {amount_rub}₽ вместо {_rub(price)}₽. Не выдана.")
                return
            db['pay_offers'].delete_one({"_id": f"{uid}:indulgence"})
            db['daily_revenue'].insert_one({"type": "indulgence", "amount": amount_rub, "currency": "RUB", "uid": uid, "timestamp": time.time(), "date": datetime.now().strftime("%d.%m.%Y")})
            db['paid_users'].update_one({"uid": uid}, {"$set": {"status": 0, "strikes": 0}, "$inc": {"immunity": 10}, "$unset": {"topic_type": ""}}, upsert=True)
            from core.janitor import log_points
            log_points(uid, "immunity", 10, reason="indulgence_crypto")
            # Только снятие бана + иммунитет. Раньше ставились is_vip/is_queer → доступ в VIP-чат и BEYOND
            users_collection.update_one({"_id": uid}, {"$set": {"custom_tag": "Индульгенция", "indulgence": True}}, upsert=True)
            db['skynet_tasks'].insert_one({"uid": uid, "action": "full_unban", "timestamp": datetime.now()})
            now = datetime.now()
            archive_collection.update_one({"target": str(uid)}, {"$push": {"history": {"date": now.strftime("%d.%m.%Y %H:%M"), "action": "📜 Куплена Индульгенция (крипта)", "reason": f"Оплата {amount_rub}₽"}}}, upsert=True)
            _staff(f"📜 **ИНДУЛЬГЕНЦИЯ КРИПТОЙ!** Юзер `{uid}`, {amount_rub}₽. Скайнет снимает ограничения.")
            try: bot.send_message(uid, "🎉 **Грехи отпущены!**\n\nОплата получена, все блокировки снимаются, начислено 10 Щитов Иммунитета.", parse_mode="Markdown")
            except Exception: pass

        # 🏙 3. ОПЛАТА ДОСТУПА К ГОРОДУ
        elif payload_str.startswith("city_"):
            # Формат: city_123456789_Екатеринбург
            parts = payload_str.split("_", 2)
            uid = int(parts[1])
            purchased_city = parts[2]
            if not _rub_ok(amount_rub, cfg("city_pass_price")):
                _staff(f"⚠️ **Пропуск в город криптой меньше цены!** `{uid}`: {amount_rub}₽ вместо {_rub(cfg('city_pass_price'))}₽. Доступ выдан, проверьте вручную.")
            users_collection.update_one({"_id": uid}, {"$addToSet": {"purchased_cities": purchased_city}}, upsert=True)
            db['daily_revenue'].insert_one({"type": "city", "amount": amount_rub, "currency": "RUB", "uid": uid, "timestamp": time.time(), "date": datetime.now().strftime("%d.%m.%Y")})
            try:
                bot.send_message(STAFF_GROUP_ID, f"🤑 **ПРОПУСК В ГОРОД КРИПТОЙ!**\nЮзер: `{uid}`\nГород: {purchased_city}", parse_mode="Markdown")
                bot.send_message(uid, f"🎉 **Оплата получена!** Доступ к городу **{purchased_city}** открыт.\n\n*Так как ссылки одноразовые, отправьте боту команду /start или нажмите на кнопку выбора города еще раз, чтобы получить их.*", parse_mode="Markdown")
            except Exception: pass

        # 📢 4. ОПЛАТА РЕКЛАМЫ
        elif payload_str.startswith("ad_access_"):
            # Расшифровываем маячок: ad_access_7_mk_Екатеринбург___123456789
            actual_payload, uid_str = payload_str.split("___")
            uid = int(uid_str)

            # Пишем доход
            db['daily_revenue'].insert_one({"type": "ads", "amount": amount_rub, "currency": "RUB", "uid": uid, "timestamp": time.time(), "date": datetime.now().strftime("%d.%m.%Y")})

            # Разбор — один в один как в боте МП (mpserv.py). Раньше VIP-реклама (ad_access_vip_...)
            # роняла этот обработчик, а у купленной криптой рекламы не было can_post_links и напоминаний.
            has_pin = "_pin" in actual_payload
            is_vip_ad = "_vip" in actual_payload
            clean_payload = actual_payload.replace("ad_access_vip_", "").replace("ad_access_", "").replace("_pin", "")
            parts = clean_payload.split('_')

            if parts[0] == "discount":
                days = int(parts[1])
                net_key = parts[2]
                city = parts[3]
                promo_code = parts[4]
                db['promocodes'].update_one({"_id": promo_code}, {"$inc": {"used_count": 1}})
            else:
                days = int(parts[0])
                net_key = parts[1]
                city = parts[2]

            names = {"mk": "Мужской Клуб", "parni": "ПАРНИ 18+", "ns": "НС", "rainbow": "Радуга", "gayznak": "Гей Знакомства", "all": "Все сети"}
            network = names.get(net_key, net_key)

            # Вычисляем срок годности по Екатеринбургу (как в mpserv.py)
            from datetime import timedelta
            import pytz
            ekb_tz = pytz.timezone('Asia/Yekaterinburg')
            now_ekb = datetime.now(ekb_tz)
            end_date = now_ekb + timedelta(days=days)

            # 💥 ЗАПИСЬ В БАЗУ ДАННЫХ
            db['ad_subscriptions'].insert_one({
                "user_id": uid,
                "network": network,
                "city": city,
                "end_date": end_date,
                "purchase_date": now_ekb,
                "has_pin": has_pin,
                "can_post_links": is_vip_ad,
                "notified_72h": days <= 3,
                "notified_24h": days <= 1,
                "notified_3h": False,
            })
            db['users'].update_one({"_id": uid}, {"$unset": {"temp_ad_type": ""}})

            try:
                bot.send_message(STAFF_GROUP_ID, f"💰 **Новая продажа Рекламы (КРИПТА)!**\nЮзер: `{uid}`\nСеть: **{network}**\nГород: **{city}**\nСрок: **{days}** дн.", parse_mode="Markdown")
            except Exception: pass

            try:
                bot.send_message(uid, f"✅ **Оплата успешно получена!**\n\nДоступ к сети **{network}** ({city}) открыт на {days} дней.\nНажмите «Создать новое объявление».", parse_mode="Markdown")
            except Exception: pass

        # 💖 5. ДОНАТЫ (ЧАЕВЫЕ)
        elif payload_str.startswith("donation_"):
            uid = int(payload_str.replace("donation_", ""))

            # Записываем деньги в кассу (amount_rub получаем от CryptoBot)
            db['daily_revenue'].insert_one({
                "type": "donation",
                "amount": amount_rub,
                "currency": "RUB",
                "uid": uid,
                "timestamp": time.time(),
                "date": datetime.now().strftime("%d.%m.%Y")
            })

            try:
                bot.send_message(
                    uid,
                    f"💖 **Огромное спасибо за ваш крипто-донат ({amount_rub} руб.)!**\nЭти средства очень помогут нашему проекту развиваться.",
                    parse_mode="Markdown"
                )
                bot.send_message(
                    STAFF_GROUP_ID,
                    f"💸 **КРИПТО-ДОНАТ!** Пользователь `{uid}` только что отправил чаевые: **{amount_rub} руб.**! 🎉",
                    parse_mode="Markdown"
                )
            except Exception:
                pass
        else:
            _staff(f"⚠️ Неизвестный крипто-платёж `{payload_str}` на {amount_rub}₽. Проверьте вручную.")

    # 👆 ============================================================== 👆

    # 👇 РОУТЫ ДЛЯ РЕДАКТОРА ШАБЛОНОВ И БАЗЫ ЗНАНИЙ ИИ 👇

    @app.route('/glaz/api/get_bot_template', methods=['GET'])
    def api_get_bot_template():
        if not session.get('logged_in'): return jsonify({"error": "Unauthorized"}), 401
        name = request.args.get('name')
        if not name: return jsonify({"error": "No name"}), 400
        
        # 1. Сначала всегда ищем текст в общей базе Mongo (Синхронизация с Секретарем)
        doc = db['bot_templates'].find_one({"_id": name})
        if doc:
            return jsonify({"text": doc["text"]})
            
        # 2. 🚨 ХАРДКОД-ФОЛЛБЕК (так как templates.py физически лежит на другом сервере) 🚨
        NETWORK_LINKS = (
            "📍 **Ссылки для возврата в чаты:**\n"
            "• [МК (Мужской Клуб)](https://t.me/clubofrm/44)\n"
            "• [ПАРНИ 18+](https://t.me/znakparni/116)\n"
            "• [ГЕЙ чаты (Инфо)](https://t.me/gaychatcities_info/4)\n"
            "• [НС (Урал)](https://t.me/uralns/118)"
        )
        
        TEMPLATES = {
            "tpl_18": "🛑 **Внимание: Проверка возраста**\n\nУ администрации сети возникли подозрения относительно вашего совершеннолетия.\n\nℹ️ **Правило:** Находиться в сети чатов МК, ПАРНИ 18+, ГЕЙ чаты, НС, Радуга разрешено *исключительно* лицам, достигшим 18 лет.\n\n🛡 **Как снять ограничения:**\nВам необходимо предоставить фото одного из официальных документов, подтверждающих возраст:\n• Паспорт (РФ или заграничный)\n• Водительское удостоверение\n• Военный билет\n• Паспорт иностранного гражданина или ВНЖ\n*(Студенческие билеты, банковские карты и пропуски не принимаются!)*\n\nВ целях вашей безопасности мы просим **закрасить или скрыть** все персональные данные, оставив видимыми только **фотографию лица и дату рождения**.\n\n*После отправки фото ожидайте, администратор укажет дальнейший порядок действий.*",
            "tpl_nark_react": "⛔️ **БЛОКИРОВКА: Реакция на запрещенные вещества**\n\nВы были заблокированы за положительную реакцию (смайлик) на сообщение, связанное с наркотическими веществами.\n\nℹ️ В нашей сети действует нулевая терпимость к любым формам поддержки запрещенных веществ.\n\n🔓 Разблокировка возможна только на платной основе (штраф).",
            "tpl_verif": "⚠️ **Сработала система защиты**\n\nМы временно ограничили ваш доступ к сети МК из-за подозрительной активности аккаунта.\n\nℹ️ **Как снять ограничения:**\nДля подтверждения необходимо пройти видео-верификацию (записать видео-кружок). На видео должно быть четко видно ваше лицо, и вам нужно будет произнести специальную фразу.\n\n👉 Если вы готовы пройти проверку, напишите сюда: **«Готов»**.",
            "tpl_mp": "💰 **Ограничение: Коммерческая деятельность**\n\nВаши ограничения связаны с публикацией объявлений об оказании услуг за материальную помощь (МП).\n\nℹ️ Согласно правилам сети: любая коммерческая деятельность допускается *только после оплаты рекламного взноса*.\n\n🔓 **Для снятия ограничений** необходимо оплатить штраф за нарушение правил + оплатить рекламный пакет. Напишите «+» или «ДА», если хотите узнать условия.",
            "tpl_sponsor": "💎 **Ограничение: Предложение спонсорства**\n\nВаши ограничения связаны с публикацией сообщений, в которых вы предлагаете финансовую поддержку (выступаете в роли спонсора).\n\nℹ️ В нашей сети подобные предложения приравниваются к платной коммерческой деятельности. Публикация таких объявлений допускается только после оплаты специального взноса.\n\n🔓 **Для снятия ограничений** и получения официального разрешения необходимо оплатить штраф-взнос в размере 750⭐️. Напишите «Готов оплатить», чтобы мы выставили счет.",
            "tpl_nark": "⛔️ **БЛОКИРОВКА: Наркотические вещества**\n\nПричина вашей блокировки — упоминание наркотиков. Любые вещества и их эвфемизмы (смайлики, сленг, положительные реакции, комментарии) строго запрещены.\n\n⚖️ **Условия разблокировки:**\nРазбан возможен только после предоставления справки от врача-нарколога либо справки от МВД.\n\n*В исключительных случаях возможен разбан после оплаты штрафа (сумма определяется старшим администратором).*.",
            "tpl_flood": "🔇 **Ограничение: Флуд в чатах**\n\nВы получили временный мут за флуд (однотипные сообщения более 3-х раз подряд).\n\n⏳ **Ограничение снимется автоматически** (точное время указано в системном сообщении внутри чата).\n\n⚡️ Если вы не хотите ждать, возможно досрочное снятие мута на платной основе (от 100₽).",
            "tpl_vip": "⚠️ **Служебное уведомление системы**\n\nВы были заблокированы по внутренней сети партнерских проектов.\n\nℹ️ **Причина:** Вы заблокировали VIP или ТРАНС-бота в момент проведения диалога и не отправили ключевую фразу.\n\n🔓 Разблокировка возможна только на платной основе.",
            "tpl_bio": "🛑 **Ограничение: Ссылка в профиле**\n\nАвтомодератор обнаружил в вашем профиле (BIO) стороннюю ссылку или тег канала.\n\nℹ️ **Порядок действий:**\n1. Полностью уберите ссылку/канал из профиля Telegram.\n2. Не возвращайте ее на всё время пребывания в сетях МК, ПАРНИ 18+, ГЕЙ чаты, НС, Радуга.\n3. Оплатите штраф 250⭐️.\n\n*После оплаты и проверки профиля администратором ограничения будут сняты.*",
            "tpl_minor": "⛔️ **БЛОКИРОВКА: Несовершеннолетние**\n\nВы заблокированы за то, что оставили реакцию на объявление несовершеннолетнего пользователя.\n\nℹ️ Мы строго следим за возрастным цензом. Это грубое нарушение правил безопасности.\n\n🔓 Разблокировка возможна только на платной основе (штраф)."
        }

        if name == "network_links":
            return jsonify({"text": NETWORK_LINKS})
        elif name == "ai_system_prompt":
            return jsonify({"text": "Ты строгий, но понимающий ИИ-модератор поддержки.\nТебе нужно выслушать проблему пользователя и помочь ему. Если ситуация сложная — переводи на оператора."}) 
        else:
            return jsonify({"text": TEMPLATES.get(name, "Шаблон не найден")})

    @app.route('/glaz/api/save_bot_template', methods=['POST'])
    def api_save_bot_template():
        if not session.get('logged_in'): return jsonify({"success": False}), 401
        data = request.json
        name = data.get("name")
        text = data.get("text")
        
        if name and text:
            db['bot_templates'].update_one(
                {"_id": name},
                {"$set": {"text": text}},
                upsert=True
            )
            add_radar_log(f"📝 Администратор обновил системный текст: {name}")
            return jsonify({"success": True})
        return jsonify({"error": "Bad data"}), 400

    @app.route('/glaz/api/ai_prompt', methods=['GET'])
    def api_get_prompt_legacy():
        if not session.get('logged_in'): return jsonify({"error": "Unauthorized"}), 401
        doc = db['bot_templates'].find_one({"_id": "ai_system_prompt"})
        return jsonify({"prompt": doc["text"] if doc else ""})

    @app.route('/glaz/api/ai_prompt/save', methods=['POST'])
    def api_save_prompt_legacy():
        if not session.get('logged_in'): return jsonify({"success": False}), 401
        text = request.json.get("prompt")
        db['bot_templates'].update_one({"_id": "ai_system_prompt"}, {"$set": {"text": text}}, upsert=True)
        add_radar_log("🧠 Инструкции нейросети обновлены!")
        return jsonify({"success": True})
        
    # 👆 ======================================================== 👆

        
    @app.route('/glaz/api/user/save_inventory', methods=['POST'])
    def api_user_save_inventory():
        if not session.get('logged_in'): return jsonify({"success": False}), 401
        data = request.json
        uid = int(data.get("uid"))
        
        db['paid_users'].update_one(
            {"uid": uid},
            {"$set": {
                "bounty_points": int(data.get("points", 0)),
                "jackpot_shards": int(data.get("shards", 0)),
                "cashback_balance": int(data.get("cashback", 0)),
                "immunity": int(data.get("immunity", 0))
            }},
            upsert=True
        )
        from core.janitor import log_points
        for fld, key in (("bounty_points", "points"), ("jackpot_shards", "shards"), ("immunity", "immunity")):
            log_points(uid, fld, op="set", value=int(data.get(key, 0)), reason=f"web_admin:{session.get('login', '?')}")
        add_radar_log(f"💰 Web-Изменение инвентаря у юзера {uid}")
        return jsonify({"success": True})

    @app.route('/glaz/api/user/reset_strikes', methods=['POST'])
    def api_user_reset_strikes():
        if not session.get('logged_in'): return jsonify({"success": False}), 401
        uid = int(request.json.get("uid"))
        db['paid_users'].update_one({"uid": uid}, {"$set": {"strikes": 0}})
        add_radar_log(f"🕊️ Счетчик страйков обнулен для {uid}")
        return jsonify({"success": True})

    @app.route('/glaz/api/user/clear_ai_memory', methods=['POST'])
    def api_user_clear_ai_memory():
        if not session.get('logged_in'): return jsonify({"success": False}), 401
        uid = int(request.json.get("uid"))
        db['paid_users'].update_one({"uid": uid}, {"$unset": {"dialog_history": ""}})
        add_radar_log(f"🧠 Память ИИ стерта для юзера {uid} (Люди в черном 🕶️)")
        return jsonify({"success": True})

    @app.route('/glaz/api/user/save_notes', methods=['POST'])
    def api_user_save_notes():
        if not session.get('logged_in'): return jsonify({"success": False}), 401
        data = request.json
        uid = int(data.get("uid"))
        notes = data.get("notes", "").strip()
        
        db['paid_users'].update_one({"uid": uid}, {"$set": {"admin_notes": notes}}, upsert=True)
        return jsonify({"success": True})

# 👇 СИСТЕМА КОНТРОЛЯ КАЧЕСТВА И АНАЛИТИКИ ОЦЕНОК (ТИКЕТЫ) 👇

    @app.route('/glaz/api/ratings_analytics', methods=['GET'])
    def api_ratings_analytics():
        if not session.get('logged_in'): return jsonify({"error": "Unauthorized"}), 401
        
        # 1. Считаем средний балл и количество закрытых тикетов по каждому админу и ИИ
        pipeline = [
            {"$group": {
                "_id": "$admin_id",
                "avg_score": {"$avg": "$rating"},
                "total_tickets": {"$sum": 1}
            }}
        ]
        raw_stats = list(db['ticket_ratings'].aggregate(pipeline))
        
        stats = []
        for item in raw_stats:
            stats.append({
                "admin": item["_id"],
                "avg": round(item.get("avg_score") or 0, 2), # 🔥 ПРЕДОХРАНИТЕЛЬ УСТАНОВЛЕН 🔥
                "count": item["total_tickets"]
            })
            
        # 2. Вытаскиваем последние 5 критических жалоб (1-2 звезды) для Радара Гнева
        bad_ratings = list(db['ticket_ratings'].find({"rating": {"$lte": 2}}).sort("timestamp", -1).limit(5))
        bad_list = []
        for r in bad_ratings:
            bad_list.append({
                "uid": r.get("uid"),
                "admin_id": r.get("admin_id"),
                "rating": r.get("rating"),
                "time": datetime.fromtimestamp(r.get("timestamp", time.time())).strftime("%d.%m %H:%M")
            })
            
        return jsonify({
            "stats": stats,
            "bad_list": bad_list
        })

    # === ИНФРАСТРУКТУРА И СЕТИ (ЭТАП 1) ===
    @app.route('/glaz/api/infrastructure', methods=['GET'])
    def api_get_infra():
        if not session.get('logged_in'): return jsonify({"error": "Unauthorized"}), 401
        data = db['settings'].find_one({"_id": "infrastructure"}) or {}
        
        # 👇 СТРАХОВКА ОТ КРАША JSON 👇
        if "_id" in data:
            del data["_id"] 
            
        return jsonify(data)

    @app.route('/glaz/api/infrastructure/save', methods=['POST'])
    def api_save_infrastructure():
        if not session.get('logged_in'): return jsonify({"error": "Unauthorized"}), 401
        
        data = request.json
        db['settings'].update_one(
            {"_id": "infrastructure"},
            {"$set": data},
            upsert=True
        )
        add_radar_log("🗺 Архитектура сети была изменена в ЦУП!")
        return jsonify({"success": True, "message": "✅ Инфраструктура сети успешно сохранена в MongoDB!"})
    # =======================================

    @app.route('/glaz/api/infrastructure/ping', methods=['GET'])
    def api_ping_infrastructure():
        if not session.get('logged_in'): return jsonify({"error": "Unauthorized"}), 401
        
        data = db['settings'].find_one({"_id": "infrastructure"}) or {}
        networks = data.get("networks", {})
        statuses = {}
        
        # Точные ID из твоих токенов
        BOTS_TO_CHECK = {
            "Скайнет": 7674203992,
            "Реклама": 7492221662,
            "Секретарь": 7229467953
        }
        
        for net_key, chats in networks.items():
            for chat in chats:
                cid = chat.get("id")
                if not cid: continue
                try:
                    status_lines = []
                    bots_missing = 0
                    
                    # 🔥 Пробиваем каждого бота персонально в лоб
                    for bot_name, b_id in BOTS_TO_CHECK.items():
                        member = bot.get_chat_member(cid, b_id)
                        # Статус может быть administrator, creator, member, restricted, left, kicked
                        if member.status in ['administrator', 'creator']:
                            status_lines.append(f"🟢 {bot_name}")
                        else:
                            status_lines.append(f"🔴 {bot_name}")
                            bots_missing += 1
                            
                    final_text = "<br>".join(status_lines)
                    
                    if bots_missing == 0:
                        statuses[cid] = {"code": "ok", "text": final_text}
                    elif bots_missing == len(BOTS_TO_CHECK):
                        statuses[cid] = {"code": "err", "text": final_text}
                    else:
                        statuses[cid] = {"code": "warn", "text": final_text}
                        
                except Exception as e:
                    err_str = str(e).lower()
                    if "too many requests" in err_str:
                        statuses[cid] = {"code": "warn", "text": "🟡 ТГ просит подождать (Лимит)"}
                    elif "chat not found" in err_str or "forbidden" in err_str or "member list is inaccessible" in err_str:
                        statuses[cid] = {"code": "err", "text": "🔴 Скайнет кикнут / Ослеп"}
                    else:
                        # Обрезаем системную ошибку для красоты таблицы
                        safe_err = str(e).replace('"', "'")[:40] 
                        statuses[cid] = {"code": "err", "text": f"🔴 Ошибка: {safe_err}"}
                        
                time.sleep(0.3) # Отдыхаем долю секунды перед следующим чатом
                        
        return jsonify(statuses)

    @app.route('/glaz/api/moderation', methods=['GET'])
    def api_get_mod_settings():
        if not session.get('logged_in'): return jsonify({"error": "Unauthorized"}), 401
        data = db['settings'].find_one({"_id": "moderation_limits"}) or {}
        return jsonify(data)

    @app.route('/glaz/api/moderation/save', methods=['POST'])
    def api_save_mod_settings():
        if not session.get('logged_in'): return jsonify({"success": False}), 401
        raw = request.get_json(silent=True) or {}
        # Только известные поля: раньше сюда можно было записать что угодно, в т.ч. сломать
        # bump_hours / cleanup_minutes из вкладки «Управление».
        ints = ("sim_normal", "sim_newbie", "strike_hours", "flood_norm_hours", "flood_hard_hours", "quaran_hours")
        bools = ("radar_active", "antibayan_photo_active", "antibayan_text_active", "antiflood_active")
        data = {}
        for k in ints:
            if k in raw:
                try: data[k] = max(0, int(raw[k]))
                except (TypeError, ValueError): return jsonify({"success": False, "error": f"{k}: нужно число"}), 400
        for k in bools:
            if k in raw: data[k] = bool(raw[k])
        if not data: return jsonify({"success": False, "error": "пусто"}), 400
        db['settings'].update_one({"_id": "moderation_limits"}, {"$set": data}, upsert=True)
        add_radar_log("⚙️ Таймеры и настройки модерации изменены!")
        return jsonify({"success": True})

# 👇 МАГИЧЕСКАЯ МИГРАЦИЯ ИЗ CONFIG.PY В MONGODB 👇
    @app.route('/glaz/api/infrastructure/migrate', methods=['GET'])
    def api_migrate_infrastructure():
        if not session.get('logged_in'): return jsonify({"error": "Unauthorized"}), 401
        
        # Загружаем старые данные из конфига
        from config import all_cities, chat_ids_parni, chat_ids_mk, chat_ids_ns, chat_ids_rainbow, chat_ids_gayznak, MAIN_CHANNEL_LINK
        
        # Умный конвертер словарей в нужный нам формат
        def convert_dict_to_list(chat_dict):
            return [{"name": name, "id": str(chat_id)} for name, chat_id in chat_dict.items()]

        migrated_data = {
            "cities": ", ".join(all_cities),
            "global_links": {"main_channel": MAIN_CHANNEL_LINK, "faq": ""},
            "networks": {
                "parni": convert_dict_to_list(chat_ids_parni),
                "mk": convert_dict_to_list(chat_ids_mk),
                "ns": convert_dict_to_list(chat_ids_ns),
                "rainbow": convert_dict_to_list(chat_ids_rainbow),
                "gayznak": convert_dict_to_list(chat_ids_gayznak)
            },
            "competitors": []
        }
        
        # Записываем в базу
        db['settings'].update_one(
            {"_id": "infrastructure"},
            {"$set": migrated_data},
            upsert=True
        )
        return "✅ Миграция успешно завершена! Вернитесь в ЦУП и обновите страницу."

    # === ПРЯМОЕ УПРАВЛЕНИЕ АНДРЮШЕНЬКОЙ (ЦЕЛИ) ===
    @app.route('/glaz/api/spy', methods=['GET'])
    def api_get_spy():
        if not session.get('logged_in'): return jsonify([])
        doc = db['settings'].find_one({"_id": "spy_settings"}) or {}
        chats = doc.get("chats", [])
        
        # Подтягиваем здоровье из базы Андрюши
        health_data = list(db['spy_health'].find({"chat_id": {"$in": [str(c) for c in chats]}}))
        health_map = {h["chat_id"]: h for h in health_data}
        
        result = []
        for c in chats:
            c_str = str(c)
            h = health_map.get(c_str, {})
            result.append({
                "id": c_str,
                "title": h.get("title", c_str),
                "status": h.get("status", "🟡 Ждем пинга...")
            })
        return jsonify(result)

    @app.route('/glaz/api/spy/add', methods=['POST'])
    def api_add_spy():
        if not session.get('logged_in'): return jsonify({"success": False})
        chat = request.json.get('chat')
        try:
            if str(chat).lstrip('-').isdigit(): chat = int(chat)
        except: pass
        db['settings'].update_one({"_id": "spy_settings"}, {"$addToSet": {"chats": chat}}, upsert=True)
        add_radar_log(f"🎯 Новая цель для Бульдозера: {chat}")
        return jsonify({"success": True})

    @app.route('/glaz/api/spy/del', methods=['POST'])
    def api_del_spy():
        if not session.get('logged_in'): return jsonify({"success": False})
        chat = request.json.get('chat')
        try:
            if str(chat).lstrip('-').isdigit(): chat = int(chat)
        except: pass
        db['settings'].update_one({"_id": "spy_settings"}, {"$pull": {"chats": chat}})
        return jsonify({"success": True})

    # === ОТДАЕМ ПУЛЬС БОТОВ В ВЕБ-ПАНЕЛЬ ===
    @app.route('/glaz/api/system_status', methods=['GET'])
    def api_get_system_status():
        if not session.get('logged_in'): return jsonify({"error": "Unauthorized"}), 401
        status_data = db['settings'].find_one({"_id": "bot_status"}) or {}
        if "_id" in status_data:
            del status_data["_id"]
        return jsonify(status_data)

    @app.route('/glaz/api/scan_networks/<int:uid>')
    def api_scan_networks(uid):
        if not session.get('logged_in'): return jsonify({"error": "Unauthorized"}), 401

        all_chats = []
        
        # 🚀 Достаем актуальную инфраструктуру сетей прямо из базы!
        infra = db['settings'].find_one({"_id": "infrastructure"}) or {}
        networks = infra.get("networks", {})
        
        # Формируем список чатов для сканирования из базы
        net_prefixes = {"parni": "ПАРНИ 18+", "mk": "МК", "ns": "НС", "rainbow": "Радуга", "gayznak": "Гей Знакомства"}
        
        for net_name, chats_dict in networks.items():
            prefix = net_prefixes.get(net_name, net_name.upper())
            
            # Если chats_dict — это словарь {Город: ID}
            if isinstance(chats_dict, dict):
                for city, cid in chats_dict.items():
                    all_chats.append({"name": f"{prefix} | {city}", "id": cid})
                    
            # Если это просто список словарей
            elif isinstance(chats_dict, list):
                for item in chats_dict:
                    if isinstance(item, dict) and "id" in item:
                        city_name = item.get("name", "Неизвестно")
                        all_chats.append({"name": f"{prefix} | {city_name}", "id": item["id"]})

        # Сам процесс сканирования
        active_chats = []
        for chat in all_chats:
            try:
                member = bot.get_chat_member(chat['id'], uid)
                
                # 🛡️ ИСПРАВЛЕННАЯ ЛОГИКА ОПРЕДЕЛЕНИЯ ПРИСУТСТВИЯ 🛡️
                is_in_chat = False
                
                if member.status in ['creator', 'administrator', 'member']:
                    is_in_chat = True
                elif member.status == 'restricted':
                    # Превентивный мут дает статус 'restricted' даже тем, кого нет в чате.
                    # Поэтому проверяем флаг is_member (появился в новых версиях Telegram API)
                    if getattr(member, 'is_member', False):
                        is_in_chat = True

                if is_in_chat:
                    active_chats.append(chat['name'])
                    
            except Exception:
                pass # Игнорируем ошибки (бота нет в чате, чат удален и т.д.)
                
            # ⏳ Обязательная пауза, чтобы Telegram не выдал FloodWait (ограничение 30 запросов в сек)
            time.sleep(0.04)

        # Сохраняем актуальный список в досье пользователя
        db['paid_users'].update_one({"uid": uid}, {"$set": {"active_chats": active_chats}}, upsert=True)
        
        return jsonify({"chats": active_chats})

    # === ПРЯМОЕ УПРАВЛЕНИЕ АНДРЮШЕНЬКОЙ (ЦЕЛИ РО) ===
    @app.route('/glaz/api/spy_ro', methods=['GET'])
    def api_get_spy_ro():
        if not session.get('logged_in'): return jsonify([])
        doc = db['settings'].find_one({"_id": "spy_settings"}) or {}
        ro_chats = doc.get("ro_chats", [])
        
        # Подтягиваем здоровье из базы Андрюши (как и для обычных)
        health_data = list(db['spy_health'].find({"chat_id": {"$in": [str(c) for c in ro_chats]}}))
        health_map = {h["chat_id"]: h for h in health_data}
        
        result = []
        for c in ro_chats:
            c_str = str(c)
            h = health_map.get(c_str, {})
            result.append({
                "id": c_str,
                "title": h.get("title", c_str),
                "status": h.get("status", "🟡 Ждем пинга...")
            })
        return jsonify(result)

    @app.route('/glaz/api/spy_ro/add_mass', methods=['POST'])
    def api_add_spy_ro_mass():
        if not session.get('logged_in'): return jsonify({"success": False})
        raw_chats = request.json.get('chats', [])
        
        processed_chats = []
        for chat in raw_chats:
            try:
                # Если это чисто цифры или цифры с минусом — сохраняем как число, иначе как текст (юзернейм)
                if str(chat).lstrip('-').isdigit(): processed_chats.append(int(chat))
                else: processed_chats.append(str(chat))
            except: pass
            
        if processed_chats:
            # $addToSet с $each добавляет сразу весь список без дубликатов!
            db['settings'].update_one(
                {"_id": "spy_settings"}, 
                {"$addToSet": {"ro_chats": {"$each": processed_chats}}}, 
                upsert=True
            )
            add_radar_log(f"💀 Массовое добавление целей РО ({len(processed_chats)} шт.)")
            
        return jsonify({"success": True, "added": len(processed_chats)})

    @app.route('/glaz/api/spy_ro/del', methods=['POST'])
    def api_del_spy_ro():
        if not session.get('logged_in'): return jsonify({"success": False})
        chat = request.json.get('chat')
        try:
            if str(chat).lstrip('-').isdigit(): chat = int(chat)
        except: pass
        db['settings'].update_one({"_id": "spy_settings"}, {"$pull": {"ro_chats": chat}})
        return jsonify({"success": True})

    @app.route('/glaz/api/tags_action', methods=['POST'])
    def api_tags_action():
        if not session.get('logged_in'): return jsonify({"success": False}), 401
        uid = int(request.form.get('uid'))
        action = request.form.get('action')
        
        tag_data = db['temp_tags'].find_one({"uid": uid})
        if not tag_data: return jsonify({"success": False, "error": "Заявка не найдена"})
        
        tag_text = tag_data["tag"]
        if action == "ok":
            db['users'].update_one({"_id": uid}, {"$set": {"custom_tag": tag_text}}, upsert=True)
            try: bot.send_message(uid, f"🎉 **Поздравляем!**\nВаш личный тег **«{tag_text}»** успешно одобрен и установлен во всех чатах сети!", parse_mode="Markdown")
            except: pass
            msg = f"✅ Тег '{tag_text}' одобрен!"
        else:
            try: bot.send_message(uid, f"❌ **Ваш тег «{tag_text}» был отклонен.**\nПридумайте что-то другое в разделе рулетки.", parse_mode="Markdown")
            except: pass
            msg = "❌ Тег отклонен."
            
        db['temp_tags'].delete_one({"uid": uid})
        return jsonify({"success": True, "message": msg})

    @app.route('/glaz/api/premium_action', methods=['POST'])
    def api_premium_action():
        if not session.get('logged_in'): return jsonify({"success": False}), 401
        uid = int(request.form.get('uid'))
        
        try: bot.send_message(uid, "🎉 **Администрация подтвердила выдачу Telegram Premium!** Наслаждайтесь!", parse_mode="Markdown")
        except: pass
        
        db['premium_claims'].delete_one({"uid": uid})
        return jsonify({"success": True, "message": "✅ Premium выдан, тикет закрыт!"})

    @app.route('/glaz/api/verif_list')
    def api_verif_list():
        if not session.get('logged_in'): return jsonify({"error": "Unauthorized"}), 401
        
        now = datetime.now()
        
        # Ищем тех, у кого сейчас открыт тикет (status: 1) и либо есть секретный код, либо они провалили вериф
        in_verif = list(db['paid_users'].find({
            "status": 1,
            "$or": [
                {"secret_code": {"$exists": True}},
                {"failed_verification": True}
            ]
        }))
        
        result = []
        for u in in_verif:
            uid = u.get("uid")
            code = u.get("secret_code", "Нет кода")
            failed = u.get("failed_verification", False)
            last_activity = u.get("last_activity") or u.get("verif_timer", now)
            verif_timer = u.get("verif_timer")
            
            # --- 👇 ИСПРАВЛЕННАЯ ЛОГИКА ТАЙМЕРОВ 👇 ---
            if verif_timer and not failed:
                diff = (now - verif_timer).total_seconds()
                sec_left = int(300 - diff) # 300 секунд = 5 минут
                
                if sec_left <= 0:
                    # Магия: 5 минут вышли! Переводим визуально в фазу ликвидации
                    phase = "death"
                    # Отсчитываем 24 часа. 86400 (24ч) + 300 (5м) = 86700
                    sec_left = int(86700 - diff) 
                else:
                    phase = "video"
            else:
                # Если провалил (24 часа до ликвидации Санитаром Архивов)
                diff = (now - last_activity).total_seconds()
                sec_left = int(86400 - diff)
                phase = "death"
                
            if sec_left < 0: 
                sec_left = 0
            
            result.append({
                "id": uid,
                "code": code,
                "phase": phase,
                "seconds_left": sec_left
            })
            
        return jsonify({"users": result})
