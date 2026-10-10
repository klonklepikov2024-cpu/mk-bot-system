import time
from pymongo import ReturnDocument
from database.mongo import paid_collection, db, log_ledger

def is_user_locked(uid):
    """Глобальный предохранитель: проверяет, не в бане ли юзер"""
    user_data = paid_collection.find_one({"uid": uid}) or {}
    if user_data.get("status") == 1: return True
    if db['banned'].find_one({"_id": uid}): return True
    return False

def _ts(v):
    """timestamp бывает float или datetime"""
    if hasattr(v, "timestamp"): return v.timestamp()
    try: return float(v)
    except (TypeError, ValueError): return 0.0

def has_active_group_restriction(uid):
    """Действующий сетевой мут.
    1) Флаг Скайнета users.net_mute_until (0 = бессрочно): видит и муты, которые Скайнет выдал сам
       (зоны, 1 Мая, копипаст, /mute). Раньше Секретарь их не видел.
    2) Последний приказ в skynet_tasks. Снятием считаются ВСЕ разблокирующие приказы (раньше только
       full_unban: после оплаты штрафа вывод оставался заблокирован)."""
    u = db['users'].find_one({"_id": uid}, {"net_mute_until": 1}) or {}
    if "net_mute_until" in u:
        until = int(u.get("net_mute_until") or 0)
        if until == 0 or until > time.time():
            return True
    last = db['skynet_tasks'].find_one(
        {"uid": uid, "action": {"$in": ["global_mute", "full_unban", "fine_unban", "auto_heal", "global_unmute"]}},
        sort=[("_id", -1)]
    )
    if not last or last["action"] != "global_mute":
        return False
    if not last.get("duration"):
        return False  # бессрочные муты Скайнет отмечает флагом net_mute_until (п.1); приказ мог быть отменён ИИ-проверкой
    return _ts(last.get("timestamp")) + last.get("duration", 0) > time.time()

def can_withdraw(uid):
    """Единая проверка для ВСЕХ путей вывода: (можно?, причина)"""
    u = paid_collection.find_one({"uid": uid}) or {}
    if is_user_locked(uid):
        return False, "на аккаунте действует блокировка. Оплатите штраф или снимите её через /start."
    if u.get("bounty_points", 0) < 0:
        return False, "отрицательный баланс очков. Сначала закройте минус."
    if u.get("debt", 0) > 0:
        return False, "есть непогашенный кредит МФО."
    if has_active_group_restriction(uid):
        return False, "в чатах сети действует мут или блокировка."
    return True, ""

def take_points_capped(uid, amount):
    """Атомарно забирает до `amount` очков, но НЕ глубже нуля. Возвращает реально снятое."""
    old = paid_collection.find_one_and_update(
        {"uid": uid, "bounty_points": {"$gt": 0}},
        [{"$set": {"bounty_points": {"$max": [0, {"$subtract": ["$bounty_points", amount]}]}}}],
        return_document=ReturnDocument.BEFORE
    )
    if not old: return 0
    taken = min(amount, int(old.get("bounty_points", 0)))
    if taken > 0:
        log_ledger(uid, "bounty_points", -taken)   # update-пайплайн обёртка не видит, пишем в журнал сами
    return taken


def _craft_key(uid, kind):
    import datetime
    d = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5))).strftime("%Y-%m-%d")
    return f"{uid}_{kind}_{d}"

def craft_slot(uid, kind, limit):
    """Занимает слот дневного лимита (сутки по UTC+5). True - можно, False - лимит исчерпан."""
    doc = db['craft_limits'].find_one_and_update(
        {"_id": _craft_key(uid, kind)},
        {"$inc": {"n": 1}, "$setOnInsert": {"uid": uid, "kind": kind}},
        upsert=True, return_document=ReturnDocument.AFTER
    )
    if doc.get("n", 0) > limit:
        db['craft_limits'].update_one({"_id": _craft_key(uid, kind)}, {"$inc": {"n": -1}})
        return False
    return True

def craft_release(uid, kind):
    """Возвращает слот, если крафт не состоялся (не хватило ресурсов)."""
    db['craft_limits'].update_one({"_id": _craft_key(uid, kind)}, {"$inc": {"n": -1}})
