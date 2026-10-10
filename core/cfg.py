"""
core/cfg.py — чтение настроек из веб-панели. cfg("vip_remind_days") → значение из базы или
значение по умолчанию из core/config_schema.py. Кэш 20 секунд, поэтому правка в панели
доходит до бота без деплоя.
"""
import time

from database import db
from core.config_schema import BY_KEY

_cache = {}
TTL = 20


def _doc(name):
    c = _cache.get(name)
    if c and time.time() - c[0] < TTL:
        return c[1]
    try:
        d = db['settings'].find_one({"_id": name}) or {}
    except Exception:
        d = c[1] if c else {}
    _cache[name] = (time.time(), d)
    return d


def invalidate():
    _cache.clear()


def coerce(entry, value):
    t = entry["type"]
    if t == "int":
        v = int(float(value))
    elif t == "float":
        v = float(value)
    elif t == "bool":
        v = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "да", "on")
    else:
        v = str(value).strip()
    if t in ("int", "float"):
        if "min" in entry and v < entry["min"]:
            raise ValueError(f"не меньше {entry['min']}")
        if "max" in entry and v > entry["max"]:
            raise ValueError(f"не больше {entry['max']}")
    return v


def cfg(key):
    e = BY_KEY[key]
    raw = _doc(e.get("doc", "config")).get(key)
    if raw is None or raw == "":
        return e["default"]
    try:
        return coerce(e, raw)
    except (TypeError, ValueError):
        return e["default"]


def ref_tiers():
    """'10:10, 30:13, *:20' -> [(10, 0.10), (30, 0.13), (None, 0.20)]"""
    out = []
    for part in str(cfg("ref_tiers")).split(","):
        if ":" not in part:
            continue
        lim, pct = [x.strip() for x in part.split(":", 1)]
        try:
            out.append((None if lim == "*" else int(lim), float(pct) / 100))
        except ValueError:
            continue
    return out or [(None, 0.10)]
