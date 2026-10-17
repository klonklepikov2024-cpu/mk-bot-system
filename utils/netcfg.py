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
    "rub_per_star": 2.0,          # рублей за 1 ⭐️ (кэшбэк, крипта, рынок)
    "points_per_star": 5,         # очков за 1 ⭐️
    "support_price": 50,          # платный доступ к поддержке, ⭐️
    "spam_fine": 111,             # штраф за спам кнопками, ⭐️
    "indulgence_price": 2000,     # индульгенция, ⭐️
    "shop_price_50": 50,          # магазин: 50 очков, ⭐️
    "shop_price_300": 200,         # магазин: 300 очков, ⭐️
    "shop_price_1000": 500,        # магазин: 1000 очков + щит, ⭐️
    "cpa_hold_days": 14,          # холд лида CPA (для текстов статистики)
    "support_group_price": "от 60 до 150 звёзд",  # текст: сколько стоит вопрос в платной группе поддержки

    # ⚖️ Прайс штрафов (ИИ поддержки и кнопки админов). fine_tag_free / fine_tag_sponsor — общие со Скайнетом:
    # оплата ровно этой суммы даёт тег «Свободен» / «Спонсор_Одобрен».
    "fine_tag_free": 650, "fine_tag_sponsor": 750,
    "fine_nark": 2000, "fine_minor": 2000, "fine_nark_react": 1563, "fine_minor_react": 1563,
    "fine_commerce": 1563, "fine_bio": 250, "fine_flood": 500, "fine_bot_block": 250, "fine_manual": 1500,
    "fine_quick": "250, 650, 1563",

    # 💸 Кэшбэк и вывод
    "cb_min_phone": 500, "cb_min_crypto": 1000, "cb_min_card": 3500,
    "cb_exchange_packs": "50:50, 150:175, 250:300, 500:650",   # рубли:очки

    # 🎰 Казино, Кузница и Обменник
    "casino_spin_price": 50, "casino_premium_cost": 1500, "casino_cert_cost": 1250, "casino_cert_rub": 1000,
    "casino_mini_points": 1000, "casino_mini_shards": 5, "casino_jackpot_points": 300,
    "casino_tax_pct": 30, "casino_tax_outcast_pct": 50,
    "casino_points_wins": "100, 150, 250", "casino_cash_wins": "100:75, 250:20, 500:5",
    "forge_shield_cost": 10, "forge_shield_limit": 8,
    "ex_shard_buy": 150, "ex_shard_sell": 100, "ex_shard_limit": 30,
    "ex_shield_buy": 1500, "ex_shield_sell": 1000, "ex_shield_limit": 3,
    "craft_shards": 50, "craft_odds": "60, 30, 10",
    "chest_price": 1000, "chest_odds": "45, 35, 15, 5", "chest_jackpot_rub": 500,
    "bonus_min": 15, "bonus_max": 50, "bonus_shard_pct": 5, "bonus_streak": "15, 25, 40, 50, 50, 50, 100",
    "donate_buttons": "50, 150, 500",

    # ⏰ Автособытия в чатах
    "job_bomb": True, "job_bomb_every_min": 120,
    "job_cat": True, "job_cat_time": "12:00",
    "job_beetle": True, "job_beetle_every_min": 30,
    "job_auction": True, "job_auction_when": "пт 18:00",
    "job_bonus_push": True, "job_bonus_push_time": "20:00",
    "job_scout": True, "job_scout_time": "10:00",
    "job_funnel": True, "job_funnel_every_h": 6,
    "job_farm_dm": True, "job_collectors": True,

    # 🏛 Экономика: краны и налоги (раньше было зашито в коде)
    "potato_seed": 60, "potato_yield": "70, 110", "potato_grow_h": 8,
    "potato_dust": 100, "potato_spray": 150, "potato_spray_h": 12,
    "safe_blue_tick": 5, "safe_blue_start": 10000, "safe_red_start": 500,
    "hack_cost": 200, "hack_chance": 30, "hack_cooldown_min": 60,
    "wedding_price": 500, "divorce_price": 1000, "anniv_reward_pct": 100,
    "karma_vote_cooldown_s": 120,
    "lux_tax_on": False, "lux_tax_tiers": "30000:1, 100000:2, 150000:3",
    "lux_tax_wallets": True, "lux_tax_time": "04:00", "lux_tax_notify": True,
}

# Где 0 — осмысленное значение (выключено / без шанса), а не «пусто»
_ZERO_OK = {"bonus_shard_pct", "casino_tax_pct", "casino_tax_outcast_pct", "casino_mini_shards", "casino_jackpot_points",
            "safe_blue_tick", "safe_blue_start", "safe_red_start", "hack_chance", "anniv_reward_pct"}

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
    if isinstance(default, bool):  # bool раньше шёл через float: «выключено» превращалось в значение по умолчанию
        return raw if isinstance(raw, bool) else str(raw).strip().lower() in ("1", "true", "да", "on")
    if isinstance(default, str):
        return str(raw).strip() or default
    try:
        v = type(default)(float(raw))
        return v if (v > 0 or (v == 0 and key in _ZERO_OK)) else default
    except (TypeError, ValueError):
        return default


def _ints(key):
    """«250, 650, 1563» -> [250, 650, 1563]; кривое значение -> значение по умолчанию"""
    for src in (cfg(key), DEFAULTS[key]):
        try:
            out = [int(float(x)) for x in str(src).replace(";", ",").split(",") if x.strip()]
            if out and all(v >= 0 for v in out):
                return out
        except ValueError:
            pass
    return []


def _pairs(key):
    """«100:75, 250:20» -> [(100, 75), (250, 20)]"""
    for src in (cfg(key), DEFAULTS[key]):
        try:
            out = []
            for part in str(src).replace(";", ",").split(","):
                if not part.strip():
                    continue
                a, b = part.split(":")
                out.append((int(float(a)), int(float(b))))
            if out and all(a > 0 and b >= 0 for a, b in out):
                return out
        except ValueError:
            pass
    return []


def range_pair(key):
    """«70, 110» -> (70, 110); перевёрнутое или кривое — значение по умолчанию"""
    v = _ints(key)
    if len(v) >= 2 and v[0] <= v[1]:
        return v[0], v[1]
    d = [int(x) for x in DEFAULTS[key].split(",")]
    return d[0], d[1]


def lux_tax_amount(balance):
    """Налог на роскошь за сутки: «30000:1, 100000:2» — с части выше 30 000 берётся 1 %, выше 100 000 — 2 %
    (как подоходный по ступеням: каждая ставка только со своей части)."""
    tiers = sorted(_pairs("lux_tax_tiers"))
    tax = 0.0
    for i, (thr, pct) in enumerate(tiers):
        if balance <= thr:
            break
        top = min(balance, tiers[i + 1][0]) if i + 1 < len(tiers) else balance
        tax += (top - thr) * pct / 100
    return int(tax)


def lux_tax_free_limit():
    tiers = sorted(_pairs("lux_tax_tiers"))
    return tiers[0][0] if tiers else 0


def fine_quick_buttons():
    return [v for v in _ints("fine_quick") if v > 0][:4]


def exchange_packs():
    """рубли -> очки (курс обмена задаёт сервер, не кнопка)"""
    return dict(_pairs("cb_exchange_packs"))


def weighted(key):
    """«100:75, 250:20, 500:5» -> (значения, веса) для random.choices"""
    p = _pairs(key)
    return [a for a, _ in p], [b for _, b in p]


def odds(key, n):
    """«45, 35, 15, 5» -> накопительные пороги [45, 80, 95] для random.randint(1, 100)"""
    v = _ints(key)
    if len(v) != n:
        v = [int(x) for x in DEFAULTS[key].split(",")]
    out, acc = [], 0
    for x in v[:-1]:
        acc += x
        out.append(min(acc, 100))
    return out


def withdraw_min(method):
    """method: phone / card / crypto (trc20, bep20, ton, «USDT …», «TON», «На карту», «На телефон»)"""
    m = str(method).lower()
    if "карт" in m or m == "card":
        return cfg("cb_min_card")
    if m in ("trc20", "bep20", "ton") or "usdt" in m or "ton" in m or "крипт" in m:
        return max(cfg("cb_min_crypto"), cfg("cb_min_phone"))
    return cfg("cb_min_phone")


def job_on(name):
    """Тумблер автособытия из панели (job_bomb, job_cat, …)"""
    try:
        return bool(cfg(name))
    except KeyError:
        return True


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