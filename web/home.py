"""
Главная «🔥 Что горит»: всё, что ждёт человека, одним запросом + решения по ИИ-делам прямо из панели.
"""
import time
from datetime import datetime

from flask import jsonify, request, session

from database import db, users_collection, banned_collection


BOTS = [  # ключ пульса в settings.bot_status -> подпись
    ("skynet_last_seen", "Скайнет"),
    ("sec_last_seen", "Секретарь"),
    ("spy_last_seen", "Шпион"),
    ("ads_last_seen", "МП"),
    ("beyond_last_seen", "BEYOND"),
]


def register_home_routes(app, add_radar_log):
    from web_auth import is_owner

    def _auth():
        return session.get('logged_in')

    @app.route('/glaz/api/attention')
    def api_attention():
        if not _auth(): return jsonify({"error": "Unauthorized"}), 401
        now = time.time()
        day_ago = now - 86400
        today = datetime.now().strftime("%d.%m.%Y")

        def cnt(coll, q):
            try: return db[coll].count_documents(q)
            except Exception: return 0

        items = [
            {"key": "tickets", "icon": "📩", "label": "Тикеты без ответа", "tab": "tab-support",
             "count": cnt('support_tickets', {"is_answered": False, "is_closed": {"$ne": True}}), "level": "hot"},
            {"key": "ai", "icon": "🤖", "label": "ИИ не смог проверить — решить вручную", "tab": "tab-home",
             "count": cnt('ai_reviews', {"status": "pending", "ts": {"$gt": now - 7 * 86400}}), "level": "hot"},
            {"key": "verif", "icon": "🎥", "label": "Проходят Live-верификацию", "tab": "tab-verif",
             "count": cnt('paid_users', {"status": 1, "$or": [{"secret_code": {"$exists": True}}, {"failed_verification": True}]}), "level": "warm"},
            {"key": "vip", "icon": "👑", "label": "VIP: кружки ждут решения (в Telegram)", "tab": None,
             "count": cnt('vip_funnel', {"stage": {"$in": ["admin_review", "deciding"]}}), "level": "warm"},
            {"key": "beyond", "icon": "🏳️‍🌈", "label": "BEYOND: анкеты ждут решения (в Telegram)", "tab": None,
             "count": cnt('beyond_funnel', {"step": "admin_review"}), "level": "warm"},
            {"key": "wd", "icon": "💸", "label": "Заявки на вывод", "tab": "tab-withdrawals",
             "count": cnt('withdrawals', {"status": "pending"}), "level": "hot"},
            {"key": "prizes", "icon": "🎁", "label": "Призы и теги ждут выдачи", "tab": "tab-withdrawals",
             "count": cnt('premium_claims', {"status": "pending"}) + cnt('temp_tags', {}), "level": "warm"},
            {"key": "errors", "icon": "🩺", "label": "Ошибки за сутки", "tab": "tab-diag",
             "count": cnt('skynet_errors', {"ts": {"$gt": day_ago}}), "level": "info"},
            {"key": "tasks", "icon": "⚠️", "label": "Приказы ботов с ошибкой", "tab": "tab-diag",
             "count": cnt('skynet_tasks', {"status": "error"}), "level": "hot"},
        ]

        status = db['settings'].find_one({"_id": "bot_status"}) or {}
        pulses = []
        for key, name in BOTS:
            ts = status.get(key)
            pulses.append({"bot": name, "ago": int(now - ts) if ts else None})

        today_stats = {
            "new_users": cnt('users', {"first_seen": {"$gt": now - 86400}}),
            "bans_total": banned_collection.count_documents({}),
            "vips": users_collection.count_documents({"is_vip": True}),
            "radar_24h": cnt('radar_logs', {"ts": {"$gt": day_ago}}),
        }
        if is_owner():
            stars = 0
            from web.finance import _rev_kind
            for r in db['daily_revenue'].find({"date": today}, {"type": 1, "amount": 1, "currency": 1}):
                if _rev_kind(r) == "stars":
                    stars += int(r.get("amount") or 0)
            today_stats["stars_today"] = stars

        return jsonify({"items": items, "pulses": pulses, "today": today_stats})

    @app.route('/glaz/api/ai_reviews')
    def api_ai_reviews():
        if not _auth(): return jsonify({"error": "Unauthorized"}), 401
        rows = db['ai_reviews'].find({"status": "pending", "ts": {"$gt": time.time() - 7 * 86400}}).sort("ts", -1).limit(30)
        out = []
        for r in rows:
            u = users_collection.find_one({"_id": r["uid"]}, {"first_name": 1, "username": 1}) or {}
            out.append({"id": str(r["_id"]), "uid": r["uid"], "name": u.get("first_name") or "", "username": u.get("username") or "",
                        "action": r.get("action"), "reason": r.get("reason"), "text": (r.get("trigger_text") or "")[:400],
                        "chat": r.get("origin_chat") or "", "source": r.get("source") or "",
                        "time": datetime.fromtimestamp(r.get("ts", 0)).strftime("%d.%m %H:%M")})
        return jsonify(out)

    @app.route('/glaz/api/ai_reviews/decide', methods=['POST'])
    def api_ai_review_decide():
        if not _auth(): return jsonify({"success": False}), 401
        from core.ai_review import claim_review
        d = request.get_json(silent=True) or {}
        decision = {"ban": "ban", "mute": "mute", "dismiss": "dismissed"}.get(d.get("decision"))
        if not decision:
            return jsonify({"success": False, "error": "неизвестное решение"}), 400
        admin = f"{session.get('login', 'admin')} (веб)"
        case = claim_review(str(d.get("id", "")), decision, admin)
        if not case:
            return jsonify({"success": False, "error": "Дело уже решено другим админом"})
        if decision != "dismissed":
            # Исполняет Скайнет (там функции бана/мута); skip_ai — ИИ уже не спрашиваем, решил человек
            db['skynet_tasks'].insert_one({
                "uid": int(case["uid"]), "action": "global_ban" if decision == "ban" else "global_mute",
                "reason": case.get("reason") or "Решение администратора", "admin_name": admin,
                "trigger_text": case.get("trigger_text"), "origin_chat": case.get("origin_chat", ""),
                "duration": int(case.get("duration") or 0), "skip_ai": True, "timestamp": datetime.now()})
        add_radar_log(f"🤖→👤 Веб-решение по {case['uid']}: {decision} ({case.get('reason')}) — {admin}")
        return jsonify({"success": True})
