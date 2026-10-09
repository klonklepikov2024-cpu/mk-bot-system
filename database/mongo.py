import sys
import time
import datetime
import pymongo
from pymongo import ReturnDocument
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

# ================= 📒 ЖУРНАЛ ОЧКОВ / ОСКОЛКОВ / ЩИТОВ =================
# Любое изменение этих полей у игрока через paid_collection автоматически пишется в points_ledger:
# кто, что, на сколько, остаток (если известен) и ПРИЧИНА (модуль.функция, откуда пришёл вызов).
# Записи хранятся 180 дней (TTL-индекс). Ошибка журнала никогда не ломает игру.
LEDGER_FIELDS = ("bounty_points", "jackpot_shards", "immunity")
_LEDGER_SKIP_FILES = ("mongo.py", "validators.py")

def _caller_reason():
    """Имя вызывающей функции вне mongo.py / validators.py, например main.api_open_chest"""
    try:
        f = sys._getframe(1)
        while f is not None:
            fn = f.f_code.co_filename.replace("\\", "/").rsplit("/", 1)[-1]
            if fn not in _LEDGER_SKIP_FILES:
                return f"{fn[:-3] if fn.endswith('.py') else fn}.{f.f_code.co_name}"
            f = f.f_back
    except Exception:
        pass
    return "unknown"

def log_ledger(uid, field, delta=None, reason=None, balance_after=None, op="inc", value=None):
    try:
        now = time.time()
        doc = {
            "uid": uid, "field": field, "reason": reason or _caller_reason(),
            "ts": now, "dt": datetime.datetime.fromtimestamp(now, datetime.timezone.utc)
        }
        if op == "inc":
            doc["delta"] = delta
        else:
            doc["op"] = "set"
            doc["value"] = value
        if balance_after is not None:
            doc["bal"] = balance_after
        db['points_ledger'].insert_one(doc)
    except Exception:
        pass

def _log_update(flt, update, doc=None, doc_is_after=False):
    if not isinstance(update, dict):
        return  # update-пайплайны (take_points_capped) пишут в журнал сами
    uid = flt.get("uid") if isinstance(flt, dict) else None
    inc = update.get("$inc") or {}
    st = update.get("$set") or {}
    for fld in LEDGER_FIELDS:
        v = inc.get(fld)
        if isinstance(v, (int, float)) and v != 0:
            bal = None
            if doc is not None and isinstance(doc.get(fld), (int, float)):
                bal = doc[fld] if doc_is_after else doc[fld] + v
            log_ledger(uid, fld, v, balance_after=bal)
        if fld in st:
            log_ledger(uid, fld, op="set", value=st[fld])

class LedgerCollection:
    """Обёртка над коллекцией paid_users: всё остальное проксируется как есть."""
    def __init__(self, coll):
        object.__setattr__(self, "_c", coll)

    def __getattr__(self, name):
        return getattr(self._c, name)

    def update_one(self, flt, update, *a, **k):
        res = self._c.update_one(flt, update, *a, **k)
        if res.matched_count or res.upserted_id is not None:
            _log_update(flt, update)
        return res

    def update_many(self, flt, update, *a, **k):
        res = self._c.update_many(flt, update, *a, **k)
        if res.modified_count and isinstance(update, dict):
            inc = update.get("$inc") or {}
            for fld in LEDGER_FIELDS:
                v = inc.get(fld)
                if isinstance(v, (int, float)) and v != 0:
                    log_ledger(None, fld, v * res.modified_count,
                               reason=f"{_caller_reason()} (массово, {res.modified_count} игроков)")
        return res

    def find_one_and_update(self, flt, update, *a, **k):
        doc = self._c.find_one_and_update(flt, update, *a, **k)
        if doc is not None or k.get("upsert"):
            _log_update(flt, update, doc, doc_is_after=(k.get("return_document") == ReturnDocument.AFTER))
        return doc

# --- Экспортируем все нужные коллекции ---
paid_collection = LedgerCollection(db['paid_users'])
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
        ("points_ledger", [("uid", 1), ("ts", -1)]),
        ("points_ledger", [("ts", -1)]),
    ]
    for coll_name, keys in specs:
        try:
            db[coll_name].create_index(keys)
        except Exception as e:
            logger.warning(f"Индекс {coll_name} {keys} не создан: {e}")
    try:
        db['points_ledger'].create_index("dt", expireAfterSeconds=180 * 86400)   # журнал живёт 180 дней
    except Exception as e:
        logger.warning(f"TTL-индекс журнала очков не создан: {e}")

_ensure_indexes()
