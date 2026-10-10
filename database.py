import os
import pymongo

# Рвем связь с config.py, берем ключ напрямую из системы
MONGO_URI = os.getenv('MONGO_URI')

if not MONGO_URI:
    raise ValueError("❌ КРИТИЧЕСКАЯ ОШИБКА: MONGO_URI не найден в переменных окружения!")

mongo_client = pymongo.MongoClient(MONGO_URI)
db = mongo_client['elite_bot_db']

users_collection = db['users']               
pending_refs_collection = db['pending_refs'] 
banned_collection = db['banned']             
posts_collection = db['posts']
archive_collection = db['grouphelp_archive']
temp_posts = db['temp_posts']
proxy_sessions = db['proxy_sessions'] 
withdrawals_collection = db['withdrawals']   

def update_user_stats(user_id, invites_add=0, balance_add=0, clicks_add=0):
    users_collection.update_one(
        {"_id": user_id},
        {"$inc": {"invites": invites_add, "balance": balance_add, "clicks": clicks_add}},
        upsert=True
    )

def get_user_stats(user_id):
    user = users_collection.find_one({"_id": user_id})
    if user: return {'invites': user.get('invites', 0), 'balance': user.get('balance', 0), 'clicks': user.get('clicks', 0)}
    return {'invites': 0, 'balance': 0, 'clicks': 0}

def set_pending_ref(new_user_id, ref_id):
    pending_refs_collection.update_one({"_id": new_user_id}, {"$set": {"ref_id": ref_id}}, upsert=True)

def get_pending_ref(new_user_id):
    doc = pending_refs_collection.find_one({"_id": new_user_id})
    return doc['ref_id'] if doc else None

def delete_pending_ref(new_user_id):
    pending_refs_collection.delete_one({"_id": new_user_id})