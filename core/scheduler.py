import random
import datetime
import time
from zoneinfo import ZoneInfo

# 🔥 ПРАВИЛЬНЫЕ ИМПОРТЫ ПОД ТВОЙ TELEBOT 🔥
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.mongodb import MongoDBJobStore
from database.mongo import client, db 

# БЕРЕМ ТВОЙ АДРЕС НАПРЯМУЮ ИЗ КОНФИГА
from config import APP_URL
WEBAPP_URL = f"{APP_URL.rstrip('/')}/webapp"

tz = ZoneInfo("Europe/Moscow")

# Подключаем хранилище задач к нашей MongoDB
jobstores = {
    'default': MongoDBJobStore(client=client, database='elite_bot_db', collection='apscheduler_jobs')
}

# Создаем планировщик с базой данных
scheduler = BackgroundScheduler(jobstores=jobstores, timezone=tz)

# ================= ФУНКЦИИ УДАЛЕНИЯ И РОЗЫГРЫШЕЙ =================

def schedule_message_deletion(chat_id, message_id, delay_seconds, bot_instance):
    run_date = datetime.datetime.now(tz) + datetime.timedelta(seconds=delay_seconds)
    scheduler.add_job(
        delete_task_executor, 
        'date', 
        run_date=run_date, 
        args=[chat_id, message_id],
        id=f"del_{chat_id}_{message_id}",
        replace_existing=True
    )

def delete_task_executor(chat_id, message_id):
    from core.bot import bot
    try:
        bot.delete_message(chat_id, message_id)
    except Exception:
        pass

def check_giveaways_task():
    now = datetime.datetime.now()
    ended_gws = db['giveaways'].find({"status": "active", "end_date": {"$lte": now}})
    
    for gw in ended_gws:
        gw_id = gw["_id"]
        last_ticket = gw.get("last_ticket_num", 0)
        
        if last_ticket == 0:
            db['giveaways'].update_one({"_id": gw_id}, {"$set": {"status": "completed", "winner": "Нет участников"}})
            continue
            
        winning_number = random.randint(1, last_ticket)
        tickets = db['tickets_history'].find({"giveaway_id": gw_id})
        winner_uid = None
        winner_name = None
        
        for t in tickets:
            r_start, r_end = map(int, t['range'].split('-'))
            if r_start <= winning_number <= r_end:
                winner_uid = t['uid']
                winner_name = t['name']
                break
                
        db['giveaways'].update_one(
            {"_id": gw_id}, 
            {"$set": {"status": "completed", "winner_uid": winner_uid, "winning_number": winning_number}}
        )
        
        from core.bot import bot
        from config import STAFF_GROUP_ID, PRIZES_THREAD_ID
        
        msg = f"🎉 **РОЗЫГРЫШ ЗАВЕРШЕН!**\n\nПриз: {gw['title']}\n🎟 Выиграл билет № **{winning_number}**!\nПобедитель: {winner_name} (`{winner_uid}`)"
        try:
            bot.send_message(STAFF_GROUP_ID, msg, message_thread_id=PRIZES_THREAD_ID, parse_mode="Markdown")
            bot.send_message(winner_uid, f"🏆 **ВЫ СОРВАЛИ КУШ В РОЗЫГРЫШЕ!** 🏆\n\nВаш билет №{winning_number} оказался победным! Скоро с вами свяжутся администраторы для выдачи приза: **{gw['title']}**.", parse_mode="Markdown")
        except:
            pass


# ================= НОВЫЕ ФУНКЦИИ ЗАЗЫВАЛЫ (ПРОГРЕВ ЧАТОВ) =================

def broadcast_teaser(text, button_text, tab_name):
    from core.bot import bot
    
    url_with_tab = f"{WEBAPP_URL}?tab={tab_name}"
    
    # Правильное создание клавиатуры для telebot
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton(text=button_text, web_app=WebAppInfo(url=url_with_tab)))
    
    chats = db['chats'].find({}) 
    for chat in chats:
        try:
            bot.send_message(chat_id=chat['_id'], text=text, reply_markup=keyboard, parse_mode='HTML')
        except Exception:
            continue

def tease_roulette():
    fund = db['casino_bank'].find_one({"_id": "premium_fund"})
    current_rubles = fund.get('rubles', 0) if fund else 0

    if current_rubles > 500: 
        text = (
            "🎰 <b>ДЖЕКПОТ НА ПОДХОДЕ!</b>\n\n"
            f"Фонд рулетки уже превысил <b>{current_rubles} ₽</b>!\n"
            "Следующие несколько прокрутов могут стать решающими. Кто заберет кэшбэк или Telegram Premium?\n\n"
            "<i>Стоимость прокрута: всего 50 💎</i>"
        )
        broadcast_teaser(text, "🎰 Испытать удачу", "profile")

def tease_ending_giveaways():
    now = int(time.time())
    
    ending_giveaways = db['giveaways'].find({
        "status": "active",
        "end_time": {"$gt": now, "$lt": now + 7200}
    })

    for gw in ending_giveaways:
        time_left_mins = int((gw['end_time'] - now) / 60)
        tickets_sold = gw.get('total_tickets', 0)
        
        text = (
            f"🔥 <b>ГОРИТ РОЗЫГРЫШ: {gw['title']}!</b>\n\n"
            f"⏳ Осталось всего <b>{time_left_mins} минут</b>!\n"
            f"🎟 Куплено билетов: {tickets_sold}. Шансы на победу АНОМАЛЬНО ВЫСОКИЕ!\n\n"
            "Залетай, пока время не вышло!"
        )
        broadcast_teaser(text, "🎫 Забрать билет", "giveaways")

# ================= ЗАПУСК ПЛАНИРОВЩИКА =================

def start_scheduler():
    if not scheduler.running:
        scheduler.add_job(check_giveaways_task, 'interval', minutes=1, id='gw_checker', replace_existing=True)
        scheduler.add_job(tease_roulette, 'interval', hours=4, id='tease_roulette', replace_existing=True)
        scheduler.add_job(tease_ending_giveaways, 'interval', hours=1, id='tease_gws', replace_existing=True)
        
        scheduler.start()
        print("⏰ APScheduler запущен (Память: MongoDB, Пояс: МСК)!")