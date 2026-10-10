"""
core/guards.py — общие предохранители Скайнета.

  * is_staff / is_staff_admin  — проверка прав на админские кнопки и команды
    (callback_data можно подделать кастомным клиентом, поэтому права проверяем всегда).
  * acquire_lease              — «замок» через Mongo, чтобы фоновые демоны не дублировались,
    если gunicorn запущен с несколькими воркерами.
  * safe_delete                — удаление сообщения без падения обработчика.
  * check_webapp_init_data     — проверка подписи Telegram Mini App (initData).
  * classify_profile_name      — фейс-контроль имени без ложных банов на стыке имени и фамилии.
"""
import hashlib
import hmac
import json
import os
import re
import socket
import time
import uuid
from urllib.parse import parse_qsl

from pymongo.errors import DuplicateKeyError

from database import db

_PROCESS_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


# ---------------- ПРАВА ----------------
def is_staff(user_id):
    """Владелец и главные админы (ADMIN_CHAT_IDS)."""
    from config import ADMIN_CHAT_IDS, OWNER_ID
    return user_id == OWNER_ID or user_id in ADMIN_CHAT_IDS


def is_staff_admin(bot, user_id):
    """Главные админы ИЛИ администраторы служебной группы."""
    if is_staff(user_id):
        return True
    from config import STAFF_GROUP_ID
    try:
        m = bot.get_chat_member(STAFF_GROUP_ID, user_id)
        return m.status in ("administrator", "creator")
    except Exception:
        return False


def deny_callback(bot, call, text="⛔️ Недостаточно прав."):
    try:
        bot.answer_callback_query(call.id, text, show_alert=True)
    except Exception:
        pass


# ---------------- ЗАМОК ДЛЯ ДЕМОНОВ ----------------
def acquire_lease(name, ttl_seconds):
    """True, если этот процесс держит замок `name` (берёт или продлевает).
    Другие воркеры получат False, пока замок не протухнет."""
    now = time.time()
    try:
        db["locks"].find_one_and_update(
            {"_id": name, "$or": [{"until": {"$lt": now}}, {"owner": _PROCESS_ID}]},
            {"$set": {"owner": _PROCESS_ID, "until": now + ttl_seconds}},
            upsert=True,
        )
        return True
    except DuplicateKeyError:
        return False
    except Exception as e:
        print(f"[lease:{name}] {e}")
        return False


# ---------------- TELEGRAM ----------------
def safe_delete(bot, chat_id, message_id):
    try:
        bot.delete_message(chat_id, message_id)
        return True
    except Exception:
        return False


def check_webapp_init_data(init_data, bot_token, max_age=24 * 3600):
    """Проверяет подпись initData из Telegram Mini App.
    Возвращает dict пользователя ({'id': ..., 'first_name': ...}) или None."""
    if not init_data or not bot_token:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None
    check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        return None
    try:
        if max_age and time.time() - int(pairs.get("auth_date", 0)) > max_age:
            return None
        user = json.loads(pairs.get("user", "{}"))
    except (ValueError, TypeError):
        return None
    return user if isinstance(user, dict) and user.get("id") else None


# ---------------- ФЕЙС-КОНТРОЛЬ ИМЕНИ ----------------
_HOMOGLYPHS = str.maketrans("aeopcxykmtbh", "аеорсхукмтвн")

# Длинные однозначные маркеры: ищем по склеенному имени (маскировку пробелами/точками снимаем).
_STRONG_JOINED = [
    r"жмина", r"впрофил", r"смотрипрофил", r"смотрименя", r"ссылкав", r"ссылкув",
    r"каналв", r"переходив", r"профэл", r"порно", r"поорно", r"цэпэ",
    r"малолет", r"школниц", r"детскоепорн", r"децкое",
]
# Короткие маркеры: только как начало отдельного слова, иначе ловят обычные фамилии
# («Гордецкий», «Страхов», «Марат Мельников», «Семён Яковлев»).
_STRONG_WORD_START = [r"децк", r"детск", r"дэти", r"деток"]
_STRONG_WORD_EXACT = [r"цп"]
_LINK_RE = re.compile(r"(t|т)\s*[\.\,]\s*(me|ме)\s*/|@[a-z0-9_]{4,}bot\b|https?://", re.I)
# Слабые маркеры: сами по себе не повод для пермабана, только сигнал админам.
_WEAK_WORD = [r"дети", r"меня", r"tme", r"тме", r"ебут", r"трах(ну|аю|ни|ай|ать|ает)?\w{0,3}", r"заработ\w*", r"инвест\w*", r"крипт(а|о|у|ы|е|овалют\w*)?"]


def classify_profile_name(first_name, last_name=""):
    """-> ("ban", маркер) | ("alert", маркер) | (None, None)."""
    raw = f"{first_name or ''} {last_name or ''}".lower()
    if _LINK_RE.search(raw):
        return "ban", "ссылка в имени"
    norm = raw.translate(_HOMOGLYPHS)
    joined = re.sub(r"[\.\,_\|\-\s\*]+", "", norm)
    for p in _STRONG_JOINED:
        if re.search(p, joined):
            return "ban", p
    words = [re.sub(r"[\.\,_\|\-\*]+", "", w) for w in norm.split()]
    words = [w for w in words if w]
    for w in words:
        for p in _STRONG_WORD_START:
            if re.match(p, w):
                return "ban", p
        for p in _STRONG_WORD_EXACT:
            if re.fullmatch(p, w):
                return "ban", p
        for p in _WEAK_WORD:
            if re.fullmatch(p, w):
                return "alert", p
    return None, None
