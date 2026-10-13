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


# ================= 👼 ИГРОВЫЕ НАКАЗАНИЯ: АНГЕЛ-ХРАНИТЕЛЬ И ПОБЕГ =================
# Белый список. Ангел и побег снимают ТОЛЬКО игровые ограничения:
#   • «Ордер на Арест» (Скайнет пишет его причину в users.net_mute_reason; любой более поздний
#     мут — админский, зоны, копипаст — перезаписывает её);
#   • локальные игровые муты из mute_user() (бомба, рулетка, суд, кальмар…): метка paid_users.game_mute_until;
#   • Гуантанамо — только побегом («Ангелы бессильны»).
# Баны, 3 страйка, долговой/админский/авто-мут не снимает ни то, ни другое.
GAME_NET_PREFIXES = ("Ордер на Арест",)
ANGEL_PROOF_PREFIXES = ("Ордер Гуантанамо",)

def game_restriction(uid):
    """Что реально держит человека: banned / guantanamo / net_game / local_game / foreign (чужой сетевой мут)."""
    now = time.time()
    t = paid_collection.find_one({"uid": uid}) or {}
    g_active = _ts(t.get("game_mute_until", 0)) > now
    g_proof = str(t.get("game_mute_reason") or "").startswith(ANGEL_PROOF_PREFIXES)
    u = db['users'].find_one({"_id": uid}, {"net_mute_until": 1, "net_mute_reason": 1}) or {}
    try:
        nm = int(u["net_mute_until"]) if u.get("net_mute_until") is not None else None
    except (TypeError, ValueError):
        nm = None
    net_active = nm is not None and (nm == 0 or nm > now)
    net_game = net_active and str(u.get("net_mute_reason") or "").startswith(GAME_NET_PREFIXES)
    return {
        "banned": bool(db['banned'].find_one({"_id": uid})),
        "guantanamo": _ts(t.get("guantanamo_until", 0)) > now or (g_active and g_proof),
        "net_game": bool(net_game),
        "local_game": bool(g_active and not g_proof),
        "foreign": bool(net_active and not net_game),
    }

def angel_check(uid, target_uid):
    """(можно?, причина отказа). Вызывать ДО списания щита."""
    now = time.time()
    me = paid_collection.find_one({"uid": uid}) or {}
    # 1) Спасатель сам не должен быть ограничен/забанен
    if (is_user_locked(uid) or has_active_group_restriction(uid)
            or me.get("strikes", 0) >= 3 or me.get("hard_punish")
            or _ts(me.get("guantanamo_until", 0)) > now
            or _ts(me.get("game_mute_until", 0)) > now):
        return False, "🚫 Пока вы сами под ограничением, Ангела призвать нельзя."

    # 2) Цель
    t = paid_collection.find_one({"uid": target_uid}) or {}
    st = game_restriction(target_uid)
    if st["guantanamo"]:
        return False, "🚷 Ордер Гуантанамо: Ангелы бессильны."
    if st["banned"] or is_user_locked(target_uid) or t.get("strikes", 0) >= 3 or t.get("hard_punish"):
        return False, "⛔ Это наказание наложено администрацией или системой — Ангел бессилен."
    if st["foreign"]:
        return False, "⛔ На цели мут от администрации или системы — Ангел бессилен."
    if not (st["net_game"] or st["local_game"]):
        return False, "👼 Ангел снимает только игровые муты (арест, карцер и т.п.). Это ограничение другого происхождения."
    return True, ""

def escape_check(target_uid):
    """(можно?, причина отказа) для !побег: спасать можно только из игровой тюрьмы/ареста/карцера."""
    st = game_restriction(target_uid)
    if st["banned"] or st["foreign"]:
        return False, "🚫 Это не игровая тюрьма, а наказание администрации или системы. Побег невозможен."
    if not (st["guantanamo"] or st["net_game"] or st["local_game"]):
        return False, "🤷 Этот гражданин и так на свободе, спасать некого."
    return True, ""
