"""
core/janitor.py — «уборщик» Скайнета.

1) schedule_delete(): бот ставит своё сообщение в очередь на удаление. Очередь живёт в Mongo
   (bot_trash), поэтому сообщения удаляются и после рестарта/деплоя. Раньше удаление делалось
   потоком со sleep(300): любой деплой оставлял сообщения в чатах навсегда, а «прожарки» после
   мута не удалялись вообще (в одном Красноярске их накопилось 300+).
2) Раз в сутки чистит служебные коллекции, которые раньше росли бесконечно.
3) points_ledger: изменения очков/щитов из Скайнета теперь пишутся в журнал Секретаря (/pts).
"""
import datetime
import time

from database import db
from core.guards import acquire_lease

DEFAULT_CLEANUP_MINUTES = 10


def cleanup_seconds():
    """Через сколько удалять «громкие» сообщения бота (настройка moderation_limits.cleanup_minutes)."""
    try:
        lim = db['settings'].find_one({"_id": "moderation_limits"}) or {}
        return max(1, int(lim.get("cleanup_minutes", DEFAULT_CLEANUP_MINUTES))) * 60
    except Exception:
        return DEFAULT_CLEANUP_MINUTES * 60


def schedule_delete(chat_id, message_id, after_seconds=None):
    if not chat_id or not message_id:
        return
    try:
        db['bot_trash'].insert_one({
            "chat_id": chat_id, "message_id": message_id,
            "due": time.time() + (after_seconds if after_seconds is not None else cleanup_seconds()),
        })
    except Exception as e:
        print(f"[janitor] schedule_delete: {e}")


def log_points(uid, field, delta=None, reason="skynet", op="inc", value=None):
    """Запись в points_ledger в формате Секретаря (database/mongo.py → log_ledger)."""
    try:
        now = time.time()
        doc = {"uid": uid, "field": field, "reason": f"skynet.{reason}", "ts": now,
               "dt": datetime.datetime.fromtimestamp(now, datetime.timezone.utc)}
        if op == "inc":
            doc["delta"] = delta
        else:
            doc["op"] = "set"
            doc["value"] = value
        db['points_ledger'].insert_one(doc)
    except Exception:
        pass


# Что и сколько хранить. Поля времени — float (time.time()).
_RETENTION = [
    ("radar_logs", "ts", 30 * 86400),
    ("name_alerts", "ts", 3 * 86400),
    ("mini_app_rate", None, None),          # чистится целиком: ключи живут 2 минуты
    ("login_attempts", "last", 2 * 86400),
    ("alert_throttle", "ts", 2 * 86400),
    ("blacklisted_texts", "timestamp", 180 * 86400),
    ("skynet_errors", "ts", 30 * 86400),
    ("admin_audit", "ts", 180 * 86400),
    ("post_replies", "ts", 60 * 86400),
    ("ai_reviews", "ts", 14 * 86400),
]


def _daily_db_cleanup():
    now = time.time()
    report = {}
    for coll, field, ttl in _RETENTION:
        try:
            if field is None:
                res = db[coll].delete_many({})
            else:
                res = db[coll].delete_many({field: {"$lt": now - ttl}})
            report[coll] = res.deleted_count
        except Exception as e:
            report[coll] = f"err: {e}"
    # протухшие одноразовые токены веб-панели
    for coll in ("setpass_tokens", "web_login_codes"):
        try:
            report[coll] = db[coll].delete_many({"exp": {"$lt": now}}).deleted_count
        except Exception:
            pass
    return report


def janitor_loop(bot):
    while True:
        try:
            if acquire_lease("janitor", 120):
                now = time.time()
                batch = list(db['bot_trash'].find({"due": {"$lte": now}}).sort("due", 1).limit(200))
                for doc in batch:
                    try:
                        bot.delete_message(doc["chat_id"], doc["message_id"])
                    except Exception:
                        pass  # уже удалено руками / нет прав / старше лимита — просто забываем
                    db['bot_trash'].delete_one({"_id": doc["_id"]})
                    time.sleep(0.05)

                last = (db['settings'].find_one({"_id": "janitor_state"}) or {}).get("db_cleanup_ts", 0)
                if now - last > 86400:
                    db['settings'].update_one({"_id": "janitor_state"}, {"$set": {"db_cleanup_ts": now}}, upsert=True)
                    rep = _daily_db_cleanup()
                    print(f"[janitor] суточная чистка базы: {rep}")
        except Exception as e:
            print(f"[janitor] {e}")
        time.sleep(20)
