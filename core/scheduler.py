from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.mongodb import MongoDBJobStore
from database.mongo import client # Импортируем клиент базы данных
import datetime
from zoneinfo import ZoneInfo

tz = ZoneInfo("Europe/Moscow")

# 🔥 Подключаем хранилище задач к нашей MongoDB
jobstores = {
    'default': MongoDBJobStore(client=client, database='elite_bot_db', collection='apscheduler_jobs')
}

# Создаем планировщик с базой данных
scheduler = BackgroundScheduler(jobstores=jobstores, timezone=tz)

def start_scheduler():
    if not scheduler.running:
        scheduler.start()
        print("⏰ APScheduler запущен (Память: MongoDB, Пояс: МСК)!")

def schedule_message_deletion(chat_id, message_id, delay_seconds, bot_instance):
    run_date = datetime.datetime.now(tz) + datetime.timedelta(seconds=delay_seconds)
    
    # ⚠️ ВАЖНО: Функции для базы данных не должны использовать "замыкания" (вложенные функции)
    # Поэтому мы передаем chat_id и message_id как аргументы (args)
    scheduler.add_job(
        delete_task_executor, 
        'date', 
        run_date=run_date, 
        args=[chat_id, message_id],
        id=f"del_{chat_id}_{message_id}", # Уникальный ID задачи
        replace_existing=True
    )

def delete_task_executor(chat_id, message_id):
    """Глобальная функция удаления (её APScheduler легко достанет из базы)"""
    from core.bot import bot # Локальный импорт инстанса бота
    try:
        bot.delete_message(chat_id, message_id)
    except Exception:
        pass

import random
from database.mongo import db

def check_giveaways_task():
    """Фоновый судья: проверяет дедлайны и крутит барабан розыгрышей"""
    now = datetime.datetime.now()
    
    # Ищем активные розыгрыши, у которых вышло время
    ended_gws = db['giveaways'].find({"status": "active", "end_date": {"$lte": now}})
    
    for gw in ended_gws:
        gw_id = gw["_id"]
        last_ticket = gw.get("last_ticket_num", 0)
        
        if last_ticket == 0:
            # Никто не купил билеты
            db['giveaways'].update_one({"_id": gw_id}, {"$set": {"status": "completed", "winner": "Нет участников"}})
            continue
            
        # 1. ГЕНЕРИРУЕМ ЧЕСТНЫЙ ВЫИГРЫШНЫЙ НОМЕР
        winning_number = random.randint(1, last_ticket)
        
        # 2. Ищем, кому принадлежит этот номер (Прозрачная таблица)
        # Логика: номер должен быть больше или равен началу диапазона (start_num)
        # Так как мы сохранили range как строку "1-10", проще найти победителя перебором последних транзакций
        tickets = db['tickets_history'].find({"giveaway_id": gw_id})
        winner_uid = None
        winner_name = None
        
        for t in tickets:
            r_start, r_end = map(int, t['range'].split('-'))
            if r_start <= winning_number <= r_end:
                winner_uid = t['uid']
                winner_name = t['name']
                break
                
        # 3. Закрываем розыгрыш
        db['giveaways'].update_one(
            {"_id": gw_id}, 
            {"$set": {"status": "completed", "winner_uid": winner_uid, "winning_number": winning_number}}
        )
        
        # 4. Уведомляем админов и победителя!
        from core.bot import bot
        from config import STAFF_GROUP_ID, PRIZES_THREAD_ID
        
        msg = f"🎉 **РОЗЫГРЫШ ЗАВЕРШЕН!**\n\nПриз: {gw['title']}\n🎟 Выиграл билет № **{winning_number}**!\nПобедитель: {winner_name} (`{winner_uid}`)"
        
        try:
            bot.send_message(STAFF_GROUP_ID, msg, message_thread_id=PRIZES_THREAD_ID, parse_mode="Markdown")
            bot.send_message(winner_uid, f"🏆 **ВЫ СОРВАЛИ КУШ В РОЗЫГРЫШЕ!** 🏆\n\nВаш билет №{winning_number} оказался победным! Скоро с вами свяжутся администраторы для выдачи приза: **{gw['title']}**.", parse_mode="Markdown")
        except:
            pass

# Добавляем джобу в твой start_scheduler():
def start_scheduler():
    if not scheduler.running:
        scheduler.start()
        # Добавляем проверку розыгрышей каждую минуту
        scheduler.add_job(check_giveaways_task, 'interval', minutes=1, id='gw_checker', replace_existing=True)
        print("⏰ APScheduler запущен (Память: MongoDB, Пояс: МСК)!")