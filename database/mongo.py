import pymongo
from pymongo.errors import ConnectionFailure
from config import MONGO_URI
from utils.logger import logger # Подключаем наш логгер

try:
    # Создаем единое подключение для всего проекта
    client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    
    # Принудительно проверяем связь с сервером
    client.admin.command('ping')
    logger.info("✅ Успешное подключение к MongoDB (Secretary)")
    
except ConnectionFailure as e:
    logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Не удалось подключиться к MongoDB! Проверьте MONGO_URI.\n{e}")
    # Здесь можно даже вызвать sys.exit(1), если бот без базы вообще не должен работать

db = client['elite_bot_db']

# --- Экспортируем все нужные коллекции ---
paid_collection = db['paid_users']
archive_collection = db['grouphelp_archive']
promocodes_collection = db['promocodes']
casino_bank_collection = db['casino_bank']
daily_revenue_collection = db['daily_revenue']
skynet_tasks_collection = db['skynet_tasks']
temp_reports_collection = db['temp_reports']
ticket_ratings_collection = db['ticket_ratings']
temp_tags_collection = db['temp_tags']
fine_payments_collection = db['fine_payments']


# --- Индексы: ускоряют частые запросы, данные не меняют ---
def _ensure_indexes():
    specs = [
        ("paid_users", [("uid", 1)]),
        ("chat_stats", [("chat_id", 1), ("uid", 1)]),
        ("chat_stats", [("uid", 1)]),
        ("chat_stats", [("username", 1)]),
        ("users", [("username", 1)]),
        ("promocodes", [("owner_uid", 1)]),
        ("tickets_history", [("giveaway_id", 1)]),
        ("star_transactions", [("charge_id", 1)]),
        ("tasks_progress", [("uid", 1), ("date", 1)]),
    ]
    for coll_name, keys in specs:
        try:
            db[coll_name].create_index(keys)
        except Exception as e:
            logger.warning(f"Индекс {coll_name} {keys} не создан: {e}")

_ensure_indexes()