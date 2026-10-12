"""
utils/textvars.py — {переменные} в шаблонах (та же схема, что в Скайнете: core/textvars.py).
В шаблоне пишем {fine_sponsor} — Секретарь подставит текущее значение из панели /glaz.
"""
import re

from database.mongo import db

_KEYS = ("quarantine_hours", "strike_hours", "strikes", "flood_hours", "fine_free", "fine_sponsor",
         "indulgence_price", "support_price", "spam_fine", "vip_price", "beyond_price", "rub_per_star",
         "fine_nark", "fine_minor", "fine_nark_react", "fine_minor_react", "fine_commerce", "fine_bio", "fine_flood", "fine_bot_block", "fine_manual")
_RX = re.compile(r"\{(" + "|".join(_KEYS) + r")\}")


def _num(v, d):
    try:
        f = float(v)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return d


def values():
    cfg = db['settings'].find_one({"_id": "config"}) or {}
    lim = db['settings'].find_one({"_id": "moderation_limits"}) or {}
    price = db['settings'].find_one({"_id": "skynet_pricing"}) or {}
    g = lambda d, k, dv: _num(d.get(k) if d.get(k) not in (None, "") else dv, dv)
    return {
        "quarantine_hours": g(lim, "quaran_hours", 120), "strike_hours": g(lim, "strike_hours", 72),
        "strikes": g(cfg, "repost_strikes", 3), "flood_hours": g(lim, "flood_norm_hours", 6),
        "fine_free": g(cfg, "fine_tag_free", 650), "fine_sponsor": g(cfg, "fine_tag_sponsor", 750),
        "indulgence_price": g(cfg, "indulgence_price", 2000), "support_price": g(cfg, "support_price", 50),
        "spam_fine": g(cfg, "spam_fine", 111), "vip_price": g(price, "vip_price", 250),
        "beyond_price": g(price, "beyond_price", 250), "rub_per_star": g(cfg, "rub_per_star", 2),
        "fine_nark": g(cfg, "fine_nark", 2000),
        "fine_minor": g(cfg, "fine_minor", 2000),
        "fine_nark_react": g(cfg, "fine_nark_react", 1563),
        "fine_minor_react": g(cfg, "fine_minor_react", 1563),
        "fine_commerce": g(cfg, "fine_commerce", 1563),
        "fine_bio": g(cfg, "fine_bio", 250),
        "fine_flood": g(cfg, "fine_flood", 500),
        "fine_bot_block": g(cfg, "fine_bot_block", 250),
        "fine_manual": g(cfg, "fine_manual", 1500),
    }


def fill(text):
    if not text or "{" not in text:
        return text
    try:
        v = values()
        return _RX.sub(lambda m: str(v.get(m.group(1), m.group(0))), text)
    except Exception:
        return text
