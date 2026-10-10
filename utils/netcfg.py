"""
utils/netcfg.py — настройки из веб-панели Скайнета (/glaz → «🎛 Управление»).

Панель пишет значения в общую базу (settings, _id="config"), Секретарь читает их отсюда.
Кэш 20 секунд: правка в панели доходит до бота без деплоя. Если в базе пусто или база
недоступна — берутся значения по умолчанию (как было в коде).
"""
import math
import time

from database.mongo import db

DEFAULTS = {
    "rub_per_star": 2.0,       # рублей за 1 ⭐️ (кэшбэк, крипта, рынок)
    "points_per_star": 5,      # очков за 1 ⭐️
    "support_price": 50,       # платный доступ к поддержке, ⭐️
    "spam_fine": 111,          # штраф за спам кнопками, ⭐️
    "indulgence_price": 2000,  # индульгенция, ⭐️
    "shop_price_50": 50,       # магазин: 50 очков, ⭐️
    "shop_price_300": 200,     # магазин: 300 очков, ⭐️
    "shop_price_1000": 500,    # магазин: 1000 очков + щит, ⭐️
}

_cache = {"t": 0.0, "d": {}}
TTL = 20


def _doc():
    if time.time() - _cache["t"] < TTL:
        return _cache["d"]
    try:
        _cache["d"] = db['settings'].find_one({"_id": "config"}) or {}
    except Exception:
        pass  # база моргнула — работаем на прошлых значениях
    _cache["t"] = time.time()
    return _cache["d"]


def cfg(key):
    default = DEFAULTS[key]
    raw = _doc().get(key)
    if raw is None or raw == "":
        return default
    try:
        v = type(default)(float(raw)) if isinstance(default, (int, float)) else raw
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


def rub_per_star():
    return float(cfg("rub_per_star"))


def points_per_star():
    return int(cfg("points_per_star"))


def to_rub(stars):
    """Звёзды -> рубли (для оплаты кэшбэком и крипто-счетов)."""
    return int(stars * rub_per_star())


def to_points(stars):
    return int(stars * points_per_star())


def rub_to_stars(rub):
    """Рубли -> сколько звёзд они покрывают (с округлением вниз)."""
    return int(rub // rub_per_star())


def rub_to_stars_ceil(rub):
    return math.ceil(rub / rub_per_star())


def points_per_rub():
    return points_per_star() / rub_per_star()


def shop_packs():
    """очки -> цена в звёздах"""
    return {50: cfg("shop_price_50"), 300: cfg("shop_price_300"), 1000: cfg("shop_price_1000")}


def support_prices():
    """Допустимые цены «support»-счетов: доступ к поддержке и штраф за спам."""
    return {cfg("support_price"), cfg("spam_fine")}
