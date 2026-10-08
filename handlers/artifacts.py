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
    target_info = str(target_info).strip()
    target_uid = None
    
    # 1. Пытаемся распарсить, что ввел юзер (ID или Юзернейм)
    if target_info.isdigit(): 
        target_uid = int(target_info)
    elif target_info.startswith('@'):
        uname = target_info.replace('@', '').lower()
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
        "reason": f"Ордер на Арест (от {first_name})",
        "ignore_shield": True  # Приказ главному боту игнорировать щиты!
    })

    # 5. Уведомляем жертву в ЛС
    try:
        bot.send_message(
            target_uid, 
            f"🚓 **ОРДЕР НА АРЕСТ!**\n\nГражданин {first_name} применил против вас артефакт.\nСкайнет лишил вас права голоса во всех чатах сети на **1 ЧАС**.\n\n_⚠️ Ордер пробивает любые Щиты Иммунитета! Ограничения будут сняты автоматически._", 
            parse_mode="Markdown"
        )
    except Exception as e: logger.warning(f"Не удалось отправить сообщение: {e}")
    
    # 6. Отчет админам в ЦУП (Тихо)
    try:
        bot.send_message(
            STAFF_GROUP_ID, 
            f"🚓 <b>ХАОС: ПРИМЕНЕНИЕ ОРДЕРА (WEB APP)</b>\n\n👤 Исполнитель: {first_name} (<code>{uid}</code>)\n🎯 Жертва: <code>{target_uid}</code>\n\n✅ <i>Жертва отправлена в мут на 1 час (Щиты пробиты).</i>", 
            parse_mode="HTML"
        )
    except Exception as e: logger.warning(f"Не удалось отправить сообщение: {e}")

    return True, "🚓 АРЕСТ ПРОШЕЛ УСПЕШНО!\nЖертва отправлена в мут на 1 час (Щиты пробиты!)."

def expire_temp_tags():
    """Фоновая задача: Снимает временные клейма (Троллинг-теги)"""
    now = int(time.time())
    
    # Находим все протухшие теги (время которых истекло)
    expired = list(db['temp_troll_tags'].find({"expire_at": {"$lte": now}}))
    
    for t in expired:
        target_uid = t['uid']
        old_tag = t.get('old_tag', "")
        
        # Возвращаем старый тег (или удаляем кастомный тег вообще, если его не было)
        if old_tag:
            db['users'].update_one({"_id": target_uid}, {"$set": {"custom_tag": old_tag}})
        else:
            db['users'].update_one({"_id": target_uid}, {"$unset": {"custom_tag": ""}})
            
        # Очищаем запись из темп-базы
        db['temp_troll_tags'].delete_one({"_id": t['_id']})
        
        try:
            bot.send_message(target_uid, "✨ Время действия временного статуса истекло! Ваш старый тег восстановлен.")
        except Exception as e: logger.warning(f"Не удалось отправить сообщение: {e}")

def promo_expiry_job():
    """Фоновая задача: Сжигает протухшие элитные промокоды (например, на VIP)"""
    now = datetime.datetime.now()
    db['promocodes'].delete_many({"expires_at": {"$lte": now}})