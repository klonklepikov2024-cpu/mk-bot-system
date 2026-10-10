"""
utils/offers.py — серверные цены счетов («офферы»).

Раньше сумма штрафа/индульгенции жила только в кнопке (checkout_pay_fine_650). Кнопку можно
подделать кастомным клиентом и снять бан за 1⭐️ / 2₽ / 5 очков. Теперь, когда бот выставляет
счёт, сумма запоминается здесь (коллекция pay_offers, общая со Скайнетом), а оплата сверяется с ней.
"""
import time
from database.mongo import db

pay_offers = db['pay_offers']

# Фиксированные цены: их не нужно запоминать, достаточно белого списка
FIXED_PRICES = {
    "support": {50, 111},   # платный доступ к поддержке / штраф за спам кнопками
}


def register_offer(uid, kind, amount):
    """Запомнить сумму счёта. Повтор с той же суммой ничего не меняет (сохраняет промокод)."""
    try:
        uid, amount = int(uid), int(amount)
    except (TypeError, ValueError):
        return
    key = f"{uid}:{kind}"
    cur = pay_offers.find_one({"_id": key})
    if cur and int(cur.get("amount", -1)) == amount:
        return
    pay_offers.update_one({"_id": key}, {"$set": {"uid": uid, "kind": kind, "amount": amount, "base": amount,
                                                   "promo": None, "ts": time.time()}}, upsert=True)


def apply_discount(uid, kind, new_amount, promo_code):
    pay_offers.update_one({"_id": f"{int(uid)}:{kind}"}, {"$set": {"amount": int(new_amount), "promo": promo_code}})


def get_offer(uid, kind):
    return pay_offers.find_one({"_id": f"{int(uid)}:{kind}"})


def expected_amount(uid, kind):
    """Минимально допустимая сумма. None — счёт не выставлялся (кнопка подделана или устарела)."""
    if kind in FIXED_PRICES:
        return None
    o = get_offer(uid, kind)
    return int(o["amount"]) if o else None


def is_amount_ok(uid, kind, amount):
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        return False
    if kind in FIXED_PRICES:
        return amount in FIXED_PRICES[kind]
    exp = expected_amount(uid, kind)
    return exp is not None and amount >= exp


def close_offer(uid, kind):
    pay_offers.delete_one({"_id": f"{int(uid)}:{kind}"})


def register_from_payload(custom_payload, amount_stars):
    """Вызывается из get_crypto_pay_url: payload вида fine_123 / indulgence_123."""
    try:
        kind, uid = str(custom_payload).rsplit("_", 1)
        if uid.isdigit() and kind in ("fine", "indulgence"):
            register_offer(int(uid), kind, amount_stars)
    except Exception:
        pass
