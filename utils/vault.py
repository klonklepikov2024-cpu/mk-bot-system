"""
utils/vault.py — 🏦 Банковская ячейка: личный «сейф» от грабежа в Судную ночь.

Как работает (все цифры — в панели /glaz → «🏛 Экономика: краны и налоги»):
  • Игрок кладёт очки в ячейку. Только что положенное защищено не сразу, а через vault_fresh_h часов
    («деньги в пути») — чтобы нельзя было спрятаться в момент объявления Судной ночи.
  • Тариф «очки»: абонентка списывается очками, защищено vault_points_protect_pct % (по умолчанию 70 %).
    Тариф «кэшбэк»: абонентка списывается рублями кэшбэка, защищено 100 %.
  • Абонентка — раз в сутки, по прогрессивной шкале от БОЛЬШЕГО из (текущий остаток, максимум за сутки):
    так нельзя вынуть всё перед списанием и положить обратно. Очки и рубли абонентки СГОРАЮТ.
  • Не хватает на абонентку: «кэшбэк» → переводим на тариф «очки»; «очки» → берём из самой ячейки;
    если и там не хватает — ячейка закрывается, остаток возвращается в кошелёк (уже без защиты).
  • Во время Судной ночи ячейку нельзя ни пополнить, ни опустошить.
  • Налог на роскошь ячейку не трогает: её абонентка и так выше налога.
"""
import math
import random
import time

from pymongo.errors import DuplicateKeyError

from database.mongo import paid_collection, db, log_ledger
from utils.logger import logger
from utils.netcfg import cfg, points_per_rub, vault_fee_points

COLL = db['vaults']


def _anarchy(now=None):
    now = now or time.time()
    a = db['settings'].find_one({"_id": "anarchy_mode"}) or {}
    return bool(a.get("active") and a.get("end_time", 0) > now)


def _fresh(v, now):
    """Сколько из остатка ещё «в пути» (не защищено)."""
    if float(v.get("fresh_until") or 0) <= now:
        return 0
    return max(0, min(int(v.get("fresh") or 0), int(v.get("balance") or 0)))


def protect_pct(v):
    return 100 if (v or {}).get("mode") == "rub" else int(cfg("vault_points_protect_pct"))


def split(v, now=None):
    """-> (защищено, уязвимо в Судную ночь)"""
    now = now or time.time()
    bal = int((v or {}).get("balance") or 0)
    settled = bal - _fresh(v, now)
    safe = int(settled * protect_pct(v) / 100)
    return safe, bal - safe


def fee_for(v):
    """-> (очков в сутки, рублей в сутки) по текущему остатку и максимуму за сутки."""
    base = max(int((v or {}).get("balance") or 0), int((v or {}).get("peak") or 0))
    pts = vault_fee_points(base) if base > 0 else 0
    rub = math.ceil(pts / points_per_rub() * cfg("vault_rub_discount_pct") / 100) if pts else 0
    return pts, rub


def info(uid):
    now = time.time()
    v = COLL.find_one({"_id": uid}) or {"balance": 0, "mode": "points"}
    safe, exposed = split(v, now)
    pts, rub = fee_for(v)
    return {
        "on": bool(cfg("vault_on")),
        "balance": int(v.get("balance") or 0),
        "mode": v.get("mode", "points"),
        "safe": safe, "exposed": exposed,
        "fresh": _fresh(v, now), "fresh_left": max(0, int(float(v.get("fresh_until") or 0) - now)),
        "protect_pct": protect_pct(v), "points_pct": int(cfg("vault_points_protect_pct")),
        "fee_points": pts, "fee_rub": rub,
        "min_deposit": int(cfg("vault_min_deposit")), "fresh_h": int(cfg("vault_fresh_h")),
        "tiers": str(cfg("vault_tiers")), "fee_min": int(cfg("vault_fee_min")),
        "anarchy": _anarchy(now),
    }


def deposit(uid, amount):
    """-> (ok, текст)"""
    if not cfg("vault_on"):
        return False, "Банк временно закрыт."
    if _anarchy():
        return False, "🔥 Идёт Судная ночь: банк закрыт до утра!"
    if amount < int(cfg("vault_min_deposit")):
        return False, f"Минимальный вклад — {cfg('vault_min_deposit')} 💎."
    from utils.validators import is_newbie_without_status, NEWBIE_MSG, is_user_locked
    if is_newbie_without_status(uid):
        return False, NEWBIE_MSG
    if is_user_locked(uid):
        return False, "На аккаунте блокировка — банк недоступен."
    if not paid_collection.find_one_and_update({"uid": uid, "bounty_points": {"$gte": amount}},
                                               {"$inc": {"bounty_points": -amount}}):
        return False, "Недостаточно очков на кошельке!"
    now = time.time()
    fresh_h = int(cfg("vault_fresh_h"))
    v = COLL.find_one_and_update(
        {"_id": uid},
        {"$inc": {"balance": amount, "fresh": amount},
         "$set": {"fresh_until": now + fresh_h * 3600, "ts": now},
         "$setOnInsert": {"mode": "points", "opened_at": now}},
        upsert=True, return_document=True)
    COLL.update_one({"_id": uid}, {"$max": {"peak": int(v.get("balance") or 0)}})
    return True, (f"🏦 В ячейку положено {amount} 💎. Они под защитой через {fresh_h} ч"
                  f" (деньги в пути). Остаток ячейки: {v.get('balance')} 💎.")


def withdraw(uid, amount):
    if _anarchy():
        return False, "🔥 Идёт Судная ночь: банк закрыт до утра!"
    if amount <= 0:
        return False, "Укажите сумму."
    v = COLL.find_one_and_update({"_id": uid, "balance": {"$gte": amount}},
                                 {"$inc": {"balance": -amount}}, return_document=True)
    if not v:
        return False, "В ячейке нет такой суммы."
    COLL.update_one({"_id": uid}, {"$set": {"fresh": min(int(v.get("fresh") or 0), int(v.get("balance") or 0))}})
    paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": amount}})
    return True, f"💸 Из ячейки снято {amount} 💎. Осталось: {v.get('balance')} 💎."


def set_mode(uid, mode):
    if mode not in ("points", "rub"):
        return False, "Неизвестный тариф."
    if not COLL.find_one({"_id": uid, "balance": {"$gt": 0}}):
        return False, "Сначала положите очки в ячейку."
    COLL.update_one({"_id": uid}, {"$set": {"mode": mode}})
    if mode == "rub":
        return True, "💳 Тариф «Кэшбэк»: защита 100 %, абонентка списывается рублями кэшбэка раз в сутки."
    return True, f"💎 Тариф «Очки»: защита {cfg('vault_points_protect_pct')} %, абонентка списывается очками раз в сутки."


def steal(target_uid, hacker_uid):
    """Судная ночь: хакер выносит 10–25 % НЕзащищённой части ячейки. -> украдено"""
    v = COLL.find_one({"_id": target_uid})
    if not v:
        return 0
    _, exposed = split(v)
    if exposed <= 0:
        return 0
    amount = max(1, int(exposed * random.uniform(0.10, 0.25)))
    if not COLL.find_one_and_update({"_id": target_uid, "balance": {"$gte": amount}}, {"$inc": {"balance": -amount}}):
        return 0
    paid_collection.update_one({"uid": hacker_uid}, {"$inc": {"bounty_points": amount}})
    return amount


def exposed_of(uid):
    v = COLL.find_one({"_id": uid})
    return split(v)[1] if v else 0


def _close(uid, v):
    """Закрыть ячейку: остаток — обратно в кошелёк (перемещение, не выпуск)."""
    if COLL.delete_one({"_id": uid, "balance": v.get("balance")}).deleted_count and int(v.get("balance") or 0) > 0:
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": int(v["balance"])}})


def _fee_from_vault(uid, pts):
    """Абонентка из самой ячейки (если на кошельке не хватило). Очки сгорают."""
    if COLL.find_one_and_update({"_id": uid, "balance": {"$gte": pts}}, {"$inc": {"balance": -pts}}):
        log_ledger(uid, "bounty_points", -pts, reason="vault.fee_from_vault")
        return True
    return False


def _fee_from_wallet(uid, pts):
    return bool(paid_collection.find_one_and_update({"uid": uid, "bounty_points": {"$gte": pts}},
                                                    {"$inc": {"bounty_points": -pts}}))


def charge_fees(bot=None, today=None):
    """Раз в сутки (планировщик): абонентка со всех ячеек. -> отчёт"""
    if not cfg("vault_on"):
        return {}
    today = today or time.strftime("%Y-%m-%d")
    try:   # один раз в сутки на всю сеть
        db['settings'].find_one_and_update({"_id": "vault_fee_run", "day": {"$ne": today}},
                                           {"$set": {"day": today, "ts": time.time()}}, upsert=True)
    except DuplicateKeyError:
        return {}

    def _dm(uid, text):
        if bot:
            try:
                bot.send_message(uid, text, parse_mode="HTML")
            except Exception:
                pass

    rep = {"vaults": 0, "points": 0, "rub": 0, "closed": 0, "to_points": 0}
    for v in list(COLL.find({"balance": {"$gt": 0}})):
        uid = v["_id"]
        pts, rub = fee_for(v)
        if pts <= 0:
            continue
        rep["vaults"] += 1
        if v.get("mode") == "rub":
            if rub > 0 and paid_collection.find_one_and_update({"uid": uid, "cashback_balance": {"$gte": rub}},
                                                               {"$inc": {"cashback_balance": -rub}}):
                db['ruble_ledger'].insert_one({"uid": uid, "amount": -rub, "reason": "Абонентка банковской ячейки", "timestamp": time.time()})
                rep["rub"] += rub
                COLL.update_one({"_id": uid}, {"$set": {"peak": int(v.get("balance") or 0), "last_fee": time.time()}})
                continue
            COLL.update_one({"_id": uid}, {"$set": {"mode": "points"}})
            rep["to_points"] += 1
            _dm(uid, f"🏦 <b>Банковская ячейка</b>\n\nНа кэшбэке не хватило {rub} ₽ на абонентку — ячейка переведена на тариф «Очки» "
                     f"(защита {cfg('vault_points_protect_pct')} %).")
        if _fee_from_wallet(uid, pts) or _fee_from_vault(uid, pts):
            rep["points"] += pts
            nv = COLL.find_one({"_id": uid}) or {}
            COLL.update_one({"_id": uid}, {"$set": {"peak": int(nv.get("balance") or 0), "last_fee": time.time()}})
            continue
        # не хватает нигде — закрываем
        nv = COLL.find_one({"_id": uid}) or v
        _close(uid, nv)
        rep["closed"] += 1
        _dm(uid, f"🏦 <b>Банковская ячейка закрыта</b>\n\nНе хватило {pts} 💎 на абонентку. Остаток {int(nv.get('balance') or 0)} 💎 "
                 f"возвращён в кошелёк — теперь он без защиты.")
    db['settings'].update_one({"_id": "vault_fee_run"}, {"$set": {"report": rep}})
    logger.info(f"🏦 Абонентка ячеек: {rep}")
    return rep
