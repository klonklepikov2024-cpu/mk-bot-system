from apscheduler.schedulers.background import BackgroundScheduler
import datetime
from zoneinfo import ZoneInfo

# Жестко задаем часовой пояс (МСК)
# Если захочешь Урал, напиши: tz = ZoneInfo("Asia/Yekaterinburg")
tz = ZoneInfo("Europe/Moscow")

# Создаем экземпляр планировщика с привязкой к нашему поясу
scheduler = BackgroundScheduler(timezone=tz)

def start_scheduler():
    """Запускает планировщик при старте сервера"""
    if not scheduler.running:
        scheduler.start()
        print("⏰ APScheduler успешно запущен (Часовой пояс: МСК)!")

def schedule_message_deletion(chat_id, message_id, delay_seconds, bot_instance):
    """
    Удаляет сообщение через заданное количество секунд.
    """
    # Берем точное время по МСК и прибавляем секунды
    run_date = datetime.datetime.now(tz) + datetime.timedelta(seconds=delay_seconds)
    
    def delete_task():
        try:
            bot_instance.delete_message(chat_id, message_id)
        except Exception:
            pass 
            
    scheduler.add_job(delete_task, 'date', run_date=run_date)