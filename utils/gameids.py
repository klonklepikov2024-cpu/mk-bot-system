"""
utils/gameids.py — игровые идентификаторы: номер игрока и ID семейной копилки.
Вынесено из main.py, чтобы их могли использовать и main.py, и utils/hack.py без циклических импортов.
"""
from pymongo import ReturnDocument
from database.mongo import paid_collection, db


def game_no(uid):
    """Игровой номер игрока (№1575): выдаётся один раз, по порядку, не повторяется и не переиспользуется."""
    d = paid_collection.find_one({"uid": uid}, {"game_no": 1}) or {}
    if d.get("game_no"):
        return d["game_no"]
    n = db['counters'].find_one_and_update({"_id": "game_no"}, {"$inc": {"seq": 1}}, upsert=True,
                                           return_document=ReturnDocument.AFTER)["seq"]
    r = paid_collection.update_one({"uid": uid, "game_no": {"$exists": False}}, {"$set": {"game_no": n}})
    if not r.matched_count and not paid_collection.find_one({"uid": uid}):
        paid_collection.update_one({"uid": uid}, {"$setOnInsert": {"game_no": n}}, upsert=True)
    return (paid_collection.find_one({"uid": uid}, {"game_no": 1}) or {}).get("game_no", n)


def family_bank_id(uid, user_data):
    """ID общей копилки: у супругов общий, у детей - копилка родителей (если родитель в браке)."""
    partner_id = user_data.get("partner_id")
    if partner_id:
        return f"family_{min(uid, partner_id)}_{max(uid, partner_id)}"
    parent_id = user_data.get("parent_id")
    if parent_id:
        pp = (paid_collection.find_one({"uid": parent_id}) or {}).get("partner_id")
        if pp:
            return f"family_{min(parent_id, pp)}_{max(parent_id, pp)}"
    return None
