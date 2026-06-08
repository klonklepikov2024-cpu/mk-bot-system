import os
import telebot
from flask import Flask, request

from config import APP_URL, PORT
from core.bot import bot
from core.scheduler import start_scheduler
from utils.logger import logger

# Импорт хэндлеров (ПОРЯДОК КРИТИЧЕСКИ ВАЖЕН)
import handlers.security
import handlers.admin
import handlers.casino
import handlers.payments
import handlers.start_menu # <--- ГЛАВНОЕ МЕНЮ ВСЕГДА В САМОМ НИЗУ!

app = Flask(__name__)

# Флаг для одноразового запуска внутри воркера Gunicorn
is_setup_done = False

def setup():
    """Настройка бота (выполняется внутри воркера)"""
    start_scheduler() # Моторчик запустится там, где нужно!
    
    try:
        bot.remove_webhook()
        bot.set_webhook(url=f"{APP_URL}/webhook")
        logger.info(f"✅ Вебхук успешно установлен на: {APP_URL}/webhook")
    except Exception as e:
        logger.error(f"❌ Ошибка установки вебхука: {e}")

# 🔥 ГЛАВНАЯ МАГИЯ ЗДЕСЬ 🔥
# Запускаем setup() только в момент первого пинга от сервера.
# Теперь планировщик будет жить внутри рабочего процесса!
@app.before_request
def initialize_worker():
    global is_setup_done
    if not is_setup_done:
        setup()
        is_setup_done = True

@app.route('/webhook', methods=['POST'])
def webhook():
    """Прием обновлений от серверов Telegram"""
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return 'ok', 200
    else:
        return 'error', 403

@app.route('/ping')
def ping():
    """Линия жизни для мониторинга (UptimeRobot)"""
    return "I am alive!", 200

if __name__ == '__main__':
    # Этот блок теперь используется только для локального тестирования
    logger.info("🚀 Бот Секретарь запускается локально...")
    setup()
    app.run(host='0.0.0.0', port=PORT)