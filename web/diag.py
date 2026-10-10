"""Вкладка «🩺 Диагностика»: журнал админов, что сломалось, выручка по источникам за 30 дней."""
import time
from datetime import datetime, timedelta
from flask import jsonify, session, request
from database import db

TYPE_NAMES = {
    "vip": "VIP", "fine": "Штрафы", "city_access": "Пропуск в город", "city": "Пропуск в город",
    "ads": "Реклама", "donation": "Донаты", "beyond": "BEYOND", "indulgence": "Индульгенции",
    "support": "Платная поддержка", "points_shop": "Магазин очков", "fine_partial": "Штрафы (смешанная оплата)",
    "vip_points": "VIP за очки", "vip_rub_balance": "VIP за кэшбэк", "beyond_rub": "BEYOND за кэшбэк",
    "beyond_pts": "BEYOND за очки", "market": "Чёрный рынок", "market_fee": "Комиссия рынка",
    "ads_points": "Реклама за очки", "ads_rub_balance": "Реклама за кэшбэк", "ads_crypto": "Реклама (крипта)",
}


def register_diag_routes(app, bot):
    from web.finance import _rev_kind

    def _fmt(ts):
        try: return datetime.fromtimestamp(ts).strftime("%d.%m %H:%M")
        except Exception: return "—"

    @app.route('/glaz/api/diag/audit')
    def api_diag_audit():
        if not session.get('logged_in'): return jsonify({"error": "Unauthorized"}), 401
        rows = db['admin_audit'].find().sort("ts", -1).limit(200)
        return jsonify([{"time": _fmt(r.get("ts")), "login": r.get("login"), "text": r.get("text")} for r in rows])

    @app.route('/glaz/api/diag/errors')
    def api_diag_errors():
        if not session.get('logged_in'): return jsonify({"error": "Unauthorized"}), 401
        rows = list(db['skynet_errors'].find().sort("ts", -1).limit(200))
        now = time.time()
        # Пульс ботов (heartbeat пишут сами боты в settings.bot_status)
        status = db['settings'].find_one({"_id": "bot_status"}) or {}
        pulses = []
        for k, v in status.items():
            if k.endswith("_last_seen") and isinstance(v, (int, float)):
                age = int(now - v)
                pulses.append({"bot": k.replace("_last_seen", ""), "ago_sec": age, "ok": age < 300})
        stuck = db['skynet_tasks'].count_documents({"status": {"$nin": ["done", "error"]},
                                                    "action": {"$in": ["full_unban", "fine_unban", "auto_heal", "global_unmute", "global_ban", "global_mute"]}})
        failed = db['skynet_tasks'].count_documents({"status": "error"})
        return jsonify({
            "pulses": pulses,
            "tasks_pending": stuck,
            "tasks_failed": failed,
            "trash_queue": db['bot_trash'].count_documents({}),
            "errors_24h": db['skynet_errors'].count_documents({"ts": {"$gt": now - 86400}}),
            "errors": [{"time": _fmt(r.get("ts")), "kind": r.get("kind"), "uid": r.get("uid"), "text": r.get("text")} for r in rows],
        })

    @app.route('/glaz/api/diag/revenue_month')
    def api_diag_revenue_month():
        if not session.get('logged_in'): return jsonify({"error": "Unauthorized"}), 401
        days = int(request.args.get("days", 30))
        dates = [(datetime.now() - timedelta(days=i)).strftime("%d.%m.%Y") for i in range(days)]
        agg = {}
        for r in db['daily_revenue'].find({"date": {"$in": dates}}):
            t = r.get("type", "?")
            kind = _rev_kind(r)
            key = (t, kind)
            a = agg.setdefault(key, {"type": t, "name": TYPE_NAMES.get(t, t), "kind": kind, "sum": 0, "count": 0})
            a["sum"] += r.get("amount", 0) or 0
            a["count"] += 1
        rows = sorted(agg.values(), key=lambda x: (x["kind"], -x["sum"]))
        totals = {"stars": 0, "rub": 0, "internal": 0}
        for r in rows:
            r["sum"] = round(r["sum"])
            totals[r["kind"]] += r["sum"]
        return jsonify({"days": days, "rows": rows, "totals": totals})
