import random
import datetime
import time
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from database.mongo import db
from core.bot import bot
from config import STAFF_GROUP_ID

def mint_tag_coupon(uid):
    """Создает купон на личный тег и кладет его в базу"""
    code = f"TAG-{random.randint(1000, 9999)}"
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
    """Использование Ордера на Арест напрямую из Web App"""
    target_info = str(target_info).strip()
    target_uid = None
    
    # Пытаемся распарсить, что ввел юзер (ID или Юзернейм)
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
        
    # Блокируем ордер
    db['promocodes'].update_one({"_id": promo_id}, {"$inc": {"used_count": 1}})
    
    markup = InlineKeyboardMarkup().add(
        InlineKeyboardButton("✅ Замутить", callback_data=f"arrest_done_{target_uid}"),
        InlineKeyboardButton("❌ Отклонить (Вернуть)", callback_data=f"arrest_rej_{promo_id}_{target_uid}")
    )
    
    try:
        bot.send_message(
            STAFF_GROUP_ID, 
            f"🚓 <b>ПРИМЕНЕНИЕ ОРДЕРА (WEB APP)</b>\n\n👤 От: {first_name} (<code>{uid}</code>)\n🔑 Код: <code>{promo_id}</code>\n🎯 Цель (ID):\n<code>{target_uid}</code>", 
            parse_mode="HTML", 
            reply_markup=markup
        )
    except Exception as e: pass
    
    return True, "🚓 Заявка на арест передана Спецназу Скайнета!"

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
        except: pass

def promo_expiry_job():
    """Фоновая задача: Сжигает протухшие элитные промокоды (например, на VIP)"""
    now = datetime.datetime.now()
    db['promocodes'].delete_many({"expires_at": {"$lte": now}})