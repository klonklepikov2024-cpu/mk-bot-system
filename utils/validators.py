from database.mongo import paid_collection, db

def is_user_locked(uid):
    """Глобальный предохранитель: проверяет, не в бане ли юзер"""
    user_data = paid_collection.find_one({"uid": uid}) or {}
    if user_data.get("status") == 1: return True
    if db['banned'].find_one({"_id": uid}): return True
    return False