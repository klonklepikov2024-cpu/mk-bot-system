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