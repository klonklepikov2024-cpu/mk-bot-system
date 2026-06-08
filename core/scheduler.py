from apscheduler.schedulers.background import BackgroundScheduler
import datetime

# Создаем экземпляр планировщика
scheduler = BackgroundScheduler()

def start_scheduler():
    """Запускает планировщик при старте сервера"""
    if not scheduler.running:
        scheduler.start()
        print("⏰ APScheduler успешно запущен!")

def schedule_message_deletion(chat_id, message_id, delay_seconds, bot_instance):
    """
    Удаляет сообщение через заданное количество секунд.
    Безопасная замена threading.Timer!
    """
    run_date = datetime.datetime.now() + datetime.timedelta(seconds=delay_seconds)
    
    def delete_task():
        try:
            bot_instance.delete_message(chat_id, message_id)
        except Exception:
            pass # Если сообщение уже удалено юзером, просто игнорируем
            
    # Добавляем одноразовую задачу ('date') в планировщик
    scheduler.add_job(delete_task, 'date', run_date=run_date)