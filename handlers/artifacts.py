import random
import datetime
import time
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from database.mongo import db
from core.bot import bot
from config import STAFF_GROUP_ID
from utils.logger import logger

def mint_tag_coupon(uid):
    """Создает купон на личный тег и кладет его в базу с защитой от дубликатов"""
    while True:
        # Генерируем 6-значный код для минимизации коллизий
        code = f"TAG-{random.randint(100000, 999999)}"
        # Проверяем, свободен ли такой код в базе
        if not db['promocodes'].find_one({"_id": code}):
            db['promocodes'].insert_one({
                "_id": code, 
                "type": "artifact", 
                "value": 0, 
                "target": "tag",
                "usage_limit": 1, 
                "used_count": 0, 
                "owner_uid": uid, 
                "is_active": True
            })
            return code

def tag_prize_markup(code):
    """Клавиатура для управления купоном из ЛС"""
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("🏷 Использовать купон", callback_data=f"claim_custom_tag"))
    markup.add(InlineKeyboardButton("♻️ Обменять на 5 Осколков", callback_data=f"trade_promo_{code}_shards_5"))
    return markup

def execute_arrest(uid, first_name, promo_id, target_info):
    """МГНОВЕННОЕ использование Ордера на Арест из Web App (Игнорирует Щиты, без спама в чаты)"""
    import html as _html
    raw = str(target_info).strip()
    # Первое слово - цель (@username, ID или ссылка t.me/...), всё остальное - причина
    parts = raw.split(None, 1)
    token = parts[0] if parts else ""
    reason_text = parts[1].strip()[:150] if len(parts) > 1 else ""
    for pref in ("https://t.me/", "http://t.me/", "t.me/"):
        if token.startswith(pref): token = "@" + token[len(pref):]
    target_uid = None
    
    # 1. Пытаемся распарсить цель (ID или Юзернейм)
    if token.isdigit(): 
        target_uid = int(token)
    elif token.startswith('@'):
        uname = token.replace('@', '').lower()
        u = db['users'].find_one({"username": uname})
        if u: target_uid = u['_id']
        else:
            cs = db['chat_stats'].find_one({"username": uname})
            if cs: target_uid = cs['uid']
            
    if not target_uid:
        return False, "Пользователь не найден в базе! Пусть напишет что-то в чат."
        
    if target_uid == uid:
        return False, "Нельзя арестовать самого себя! Выберите другую жертву."
        
    # 2. Проверяем статус жертвы (Нельзя сажать админов)
    from config import ADMIN_CHAT_IDS, OWNER_ID
    if target_uid in ADMIN_CHAT_IDS or target_uid == OWNER_ID:
        return False, "❌ Ошибка доступа: Цель обладает дипломатической неприкосновенностью (Админ)!"

    # 2.5 Нельзя «накладывать» арест на того, кто уже в муте: ордер не тратится и срок не обнуляется
    from utils.validators import has_active_group_restriction
    if has_active_group_restriction(target_uid):
        return False, "🚓 Эта цель уже под арестом или в муте! Ордер НЕ потрачен, дождитесь окончания ограничения."

    # 3. Атомарно «сжигаем» ордер: сработает только один раз и только у владельца
    claimed = db['promocodes'].find_one_and_update(
        {"_id": promo_id, "owner_uid": uid, "is_active": True, "used_count": 0},
        {"$inc": {"used_count": 1}}
    )
    if not claimed:
        return False, "Этот ордер уже использован!"

    # 4. МГНОВЕННЫЙ АРЕСТ НА 1 ЧАС! (ПРОБИВАЕТ ЩИТЫ)
    import time
    now = time.time()
    db['skynet_tasks'].insert_one({
        "uid": target_uid, 
        "action": "global_mute", 
        "duration": 3600, 
        "timestamp": now,
        "reason": f"Ордер на Арест (от {first_name})" + (f": {reason_text}" if reason_text else ""),
        "ignore_shield": True  # Приказ главному боту игнорировать щиты!
    })

    # 5. Уведомляем жертву в ЛС
    try:
        bot.send_message(
            target_uid, 
            f"🚓 <b>ОРДЕР НА АРЕСТ!</b>\n\nГражданин {_html.escape(first_name)} применил против вас артефакт.\nСкайнет лишил вас права голоса во всех чатах сети на <b>1 ЧАС</b>." + (f"\n📝 Причина: <i>{_html.escape(reason_text)}</i>" if reason_text else "") + "\n\n<i>⚠️ Ордер пробивает любые Щиты Иммунитета! Ограничения будут сняты автоматически.</i>", 
            parse_mode="HTML"
        )
    except Exception as e: logger.warning(f"Не удалось отправить сообщение: {e}")
    
    # 6. Отчет админам в ЦУП (Тихо)
    try:
        bot.send_message(
            STAFF_GROUP_ID, 
            f"🚓 <b>ХАОС: ПРИМЕНЕНИЕ ОРДЕРА (WEB APP)</b>\n\n👤 Исполнитель: {first_name} (<code>{uid}</code>)\n🎯 Жертва: <code>{target_uid}</code>\n📝 Причина: {_html.escape(reason_text) or '—'}\n\n✅ <i>Жертва отправлена в мут на 1 час (Щиты пробиты).</i>", 
            parse_mode="HTML"
        )
    except Exception as e: logger.warning(f"Не удалось отправить сообщение: {e}")

    return True, "🚓 АРЕСТ ПРОШЕЛ УСПЕШНО!\nЖертва отправлена в мут на 1 час (Щиты пробиты!)."

def expire_temp_tags():
    """Фоновая задача: Снимает временные клейма (Троллинг-теги).
    Правила: на игроке виден тег САМОГО ПОСЛЕДНЕГО из активных наложений.
    Истёк не верхний слой - тег не меняется. Истёк верхний - показываем предыдущий активный.
    Истекли все - возвращается базовый (исходный) тег игрока."""
    now = int(time.time())
    expired = list(db['temp_troll_tags'].find({"expire_at": {"$lte": now}}))
    by_uid = {}
    for t in expired:
        by_uid.setdefault(t['uid'], []).append(t)

    for target_uid, items in by_uid.items():
        db['temp_troll_tags'].delete_many({"_id": {"$in": [t['_id'] for t in items]}})
        remaining = list(db['temp_troll_tags'].find({"uid": target_uid, "expire_at": {"$gt": now}}).sort("created", 1))
        cur_tag = (db['users'].find_one({"_id": target_uid}) or {}).get("custom_tag", "")

        if remaining:
            top_tag = remaining[-1].get("tag")  # у старых записей тега нет - ничего не трогаем
            if top_tag and cur_tag != top_tag:
                db['users'].update_one({"_id": target_uid}, {"$set": {"custom_tag": top_tag}})
            continue

        first = sorted(items, key=lambda x: x.get("created", 0))[0]
        base = first.get("base_tag", first.get("old_tag", ""))
        if base:
            db['users'].update_one({"_id": target_uid}, {"$set": {"custom_tag": base}})
        else:
            db['users'].update_one({"_id": target_uid}, {"$unset": {"custom_tag": ""}})
        try:
            bot.send_message(target_uid, "✨ Время действия временного статуса истекло! Ваш прежний тег восстановлен.")
        except Exception as e: logger.warning(f"Не удалось отправить сообщение: {e}")

def promo_expiry_job():
    """Фоновая задача: Сжигает протухшие элитные промокоды (например, на VIP)"""
    now = datetime.datetime.now()
    db['promocodes'].delete_many({"expires_at": {"$lte": now}})