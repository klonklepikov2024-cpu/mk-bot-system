import time
from pymongo import ReturnDocument
from database.mongo import paid_collection, db

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
    """Действующий сетевой мут: последняя задача Скайнета - global_mute, срок не вышел"""
    last = db['skynet_tasks'].find_one(
        {"uid": uid, "action": {"$in": ["global_mute", "full_unban"]}},
        sort=[("_id", -1)]
    )
    if not last or last["action"] == "full_unban":
        return False
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
    return min(amount, int(old.get("bounty_points", 0)))