import os
import telebot
from flask import Flask, request

from config import APP_URL, PORT
from core.bot import bot
from core.scheduler import start_scheduler
from utils.logger import logger

# Импорт хэндлеров
import handlers.start_menu
import handlers.security
import handlers.admin
import handlers.casino
import handlers.payments

app = Flask(__name__)

def setup():
    """Настройка бота перед запуском сервера"""
    start_scheduler() # Запускаем фоновые задачи!
    
    try:
        bot.remove_webhook()
        bot.set_webhook(url=f"{APP_URL}/webhook")
        logger.info(f"✅ Вебхук успешно установлен на: {APP_URL}/webhook")
    except Exception as e:
        logger.error(f"❌ Ошибка установки вебхука: {e}")

# 🔥 КРИТИЧЕСКОЕ ИЗМЕНЕНИЕ: 
# Вызываем setup() на уровне модуля, чтобы Gunicorn выполнил его при старте.
setup()

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
    app.run(host='0.0.0.0', port=PORT)