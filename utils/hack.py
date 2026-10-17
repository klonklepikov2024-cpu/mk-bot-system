"""
utils/hack.py — механика «Взлом» (Web App → Рюкзак → Хакер), вынесена из main.py.

run_hack(uid, target_uid) -> (is_error, text)
    is_error=True  — запрос отклонён (ничего не списано), text — причина;
    is_error=False — попытка состоялась (успех, провал или отражение щитом), text — итог для игрока.
Текст для Web App — ПЛОСКИЙ (фронт выводит его через innerText, HTML-теги там видны буквально).
Сообщения в Telegram — HTML.
"""
import random
import time

from core.bot import bot
from database.mongo import paid_collection, db
from utils.gameids import game_no, family_bank_id
from utils.logger import logger
from utils.validators import take_points_capped
from utils.netcfg import cfg

HACK_COST = 200            # цена попытки, 💎 (по умолчанию; живое значение — из панели /glaz)
MIN_WALLET = 100           # меньше этого у жертвы в кошельке — красть нечего (если копилка тоже пуста)
COOLDOWN = 3600            # кулдаун хакера, сек
COOLDOWN_ANARCHY = 900     # при Судной ночи
CHANCE = 30                # шанс взлома, %
CHANCE_RAT = 40            # если у жертвы ачивка «rat»
CHANCE_ANARCHY = 80
ANARCHY_SHIELD_PIERCE = 50 # шанс пробить щит насквозь, %


def _notify(chat_id, text):
    if not chat_id:
        return
    try:
        bot.send_message(chat_id, text, parse_mode="HTML")
    except Exception as e:
        logger.debug(f"Игнор ошибки: {e}")


def _anarchy_active(now):
    a = db['settings'].find_one({"_id": "anarchy_mode"}) or {}
    return bool(a.get("active", False) and a.get("end_time", 0) > now)


def _immunity_seconds(karma):
    """Сколько жертва неприкосновенна после прошлого взлома (вне анархии)."""
    if karma >= 10000:
        return 43200   # 12 часов (Бессмертный и выше)
    if karma >= 50:
        return 21600   # 6 часов
    if karma <= -50:
        return 7200    # 2 часа
    return 14400       # 4 часа


def _steal_family_bank(family_id, hacker_uid):
    """Забирает 10–25% семейной копилки. Атомарно: параллельные взломы не уводят её в минус."""
    balance = (db['family_banks'].find_one({"_id": family_id}) or {}).get("balance", 0)
    if balance <= 0:
        return 0
    amount = max(1, int(balance * random.uniform(0.10, 0.25)))
    if not db['family_banks'].find_one_and_update({"_id": family_id, "balance": {"$gte": amount}},
                                                  {"$inc": {"balance": -amount}}):
        return 0
    paid_collection.update_one({"uid": hacker_uid}, {"$inc": {"bounty_points": amount}})
    return amount


def run_hack(uid, target_uid):
    # Цена, шанс и кулдаун — из панели /glaz («🏛 Экономика: краны и налоги»)
    HACK_COST = cfg("hack_cost")
    COOLDOWN = cfg("hack_cooldown_min") * 60
    CHANCE = cfg("hack_chance")
    CHANCE_RAT = min(100, CHANCE + 10)
    if target_uid == uid:
        return True, "Нельзя взломать самого себя!"

    now = time.time()
    user_data = paid_collection.find_one({"uid": uid}) or {}
    target_data = paid_collection.find_one({"uid": target_uid}) or {}
    target_karma = target_data.get("social_rating", 0)

    # Члены одной семьи (супруги, дети, родители) делят копилку: взлом «своих» = вывод денег из семьи себе в карман
    my_fam = family_bank_id(uid, user_data)
    target_fam = family_bank_id(target_uid, target_data)
    if my_fam and my_fam == target_fam:
        return True, "Нельзя взломать члена своей семьи: копилка у вас общая!"

    is_anarchy = _anarchy_active(now)

    # 1. Кулдаун и баланс хакера
    cooldown = COOLDOWN_ANARCHY if is_anarchy else COOLDOWN
    left = cooldown - (now - user_data.get("last_hack_time", 0))
    if left > 0:
        return True, f"Ваш вирус ещё компилируется! Ждите {int(left / 60)} мин."
    if user_data.get("bounty_points", 0) < HACK_COST:
        return True, f"У вас нет {HACK_COST} 💎 для запуска вируса!"

    # 2. Есть ли что красть (копилка жертвы грабится только в Судную ночь)
    family_id = target_fam if is_anarchy else None
    family_bal = (db['family_banks'].find_one({"_id": family_id}) or {}).get("balance", 0) if family_id else 0
    if target_data.get("bounty_points", 0) < MIN_WALLET and family_bal <= 0:
        return True, "Жертва слишком бедна, нечего красть!"

    # 3. Иммунитет жертвы после прошлого взлома (в Судную ночь не действует)
    if not is_anarchy:
        imm_left = _immunity_seconds(target_karma) - (now - target_data.get("last_hacked_time", 0))
        if imm_left > 0:
            return True, f"Сервер жертвы под защитой! Её недавно взламывали, попробуйте через {int(imm_left / 60) + 1} мин."

    # 4. Списываем цену попытки: атомарно + защита от двойного тапа
    paid = paid_collection.find_one_and_update(
        {"uid": uid, "bounty_points": {"$gte": HACK_COST},
         "$or": [{"last_hack_time": {"$exists": False}}, {"last_hack_time": {"$lte": now - cooldown}}]},
        {"$inc": {"bounty_points": -HACK_COST}, "$set": {"last_hack_time": now}}
    )
    if not paid:
        return True, f"Вирус ещё компилируется или нет {HACK_COST} 💎!"

    hacker_no = game_no(uid)

    # 5. Щит жертвы (в Судную ночь с шансом 50% пробивается насквозь)
    shield_pierced = False
    if target_data.get("immunity", 0) > 0 and paid_collection.find_one_and_update(
            {"uid": target_uid, "immunity": {"$gte": 1}},
            {"$inc": {"immunity": -1}, "$set": {"last_hacked_time": now}}):
        if is_anarchy and random.randint(1, 100) <= ANARCHY_SHIELD_PIERCE:
            shield_pierced = True   # щит уничтожен, но атака проходит
        else:
            _notify(target_uid, f"🛡 <b>ВАШ СЕРВЕР АТАКОВАЛИ!</b>\nХакер <code>№{hacker_no}</code> пытался украсть ваши Очки, "
                                f"но Щит Иммунитета ударил его током!\n<i>(Щит разрушен, система в безопасности на 4 часа)</i>")
            return False, f"❌ АТАКА ОТРАЖЕНА!\nУ жертвы был Щит. Вирус уничтожен, вы потеряли {HACK_COST} 💎."

    # 6. Бросок кубика
    if is_anarchy:
        chance = CHANCE_ANARCHY
    else:
        chance = CHANCE_RAT if "rat" in target_data.get("achievements", []) else CHANCE

    if random.randint(1, 100) > chance:
        text = f"📉 Атака провалилась: вирус не нашёл уязвимость и был стёрт. Вы потеряли {HACK_COST} 💎."
        if shield_pierced:
            text += "\n🛡 Но щит жертвы вы всё-таки разрушили."
        return False, text

    # 7. Успех: личный кошелёк
    extra = []
    if is_anarchy:
        extra.append("🏴‍☠️ СУДНАЯ НОЧЬ! Брандмауэры отключены, грабёж прошёл как по маслу!")
        if shield_pierced:
            extra.append("⚠️ ЩИТ ПРОБИТ! Перегрузка сети разорвала щит жертвы, вирус прошёл насквозь!")

    if target_karma <= -50:
        steal_pct = random.uniform(0.15, 0.30)
        if not is_anarchy:
            extra.append("📉 Цель неблагонадёжна! Защита сервера ослаблена из-за плохой кармы, вы украли в 2 раза больше очков!")
    else:
        steal_pct = random.uniform(0.05, 0.15)

    wallet = max(0, int(target_data.get("bounty_points", 0)))
    stolen = min(wallet, max(10, int(wallet * steal_pct)))
    stolen = take_points_capped(target_uid, stolen)   # жертва не уйдёт в минус
    paid_collection.update_one({"uid": target_uid}, {"$set": {"last_hacked_time": now}})
    if stolen > 0:
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": stolen}})

    victim_msg = f"🚨 <b>СИСТЕМА ВЗЛОМАНА!</b>\nХакер <code>№{hacker_no}</code> пробил вашу защиту"
    victim_msg += f" и украл <b>{stolen} 💎</b> с личного счёта!" if stolen > 0 else "!"

    # 8. Успех: семейная копилка (только Судная ночь)
    family_stolen = _steal_family_bank(family_id, uid) if family_id else 0
    if family_stolen > 0:
        extra.append(f"🏦 ВЗЛОМ КОПИЛКИ! Вирус добрался до семейного счёта жертвы и выкачал оттуда ещё {family_stolen} 💎!")
        victim_msg += f"\n\n🏦 <b>ВНИМАНИЕ!</b> Вирус проник в ваш Семейный Фонд и вывел оттуда <b>{family_stolen} 💎</b>!"
        _notify(target_data.get("partner_id"),
                f"🚨 <b>СЕМЕЙНЫЙ СЧЁТ ВЗЛОМАН!</b>\nВ рамках Судной Ночи хакер атаковал вашу семью и украл "
                f"<b>{family_stolen} 💎</b> из вашей общей копилки!")

    victim_msg += "\n<i>Срочно покупайте Щиты на Ферме или в Рюкзаке.</i>"
    _notify(target_uid, victim_msg)

    total = stolen + family_stolen
    head = (f"💻 ВЗЛОМ УСПЕШЕН!\nВы обошли защиту и украли {total} 💎 у жертвы!" if total > 0
            else "💻 Вирус прошёл, но красть оказалось нечего.")
    return False, "\n".join([head] + extra)
