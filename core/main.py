import os
import telebot
from flask import Flask, request

from config import APP_URL, PORT
from core.bot import bot
from core.scheduler import start_scheduler
from utils.logger import logger

--- ЗДЕСЬ БУДЕТ ИМПОРТ ХЭНДЛЕРОВ ---
Мы раскомментируем их на следующем шаге, когда создадим!
import handlers.start_menu
import handlers.security
import handlers.admin
import handlers.casino
import handlers.payments

# Создаем Flask-приложение
app = Flask(__name__)

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

def setup():
    """Настройка бота перед запуском сервера"""
    start_scheduler()
    
    try:
        bot.remove_webhook()
        bot.set_webhook(url=f"{APP_URL}/webhook")
        logger.info(f"✅ Вебхук успешно установлен на: {APP_URL}/webhook")
    except Exception as e:
        logger.error(f"❌ Ошибка установки вебхука: {e}")

if __name__ == '__main__':
    setup()
    logger.info("🚀 Бот Секретарь запускается...")
    # Запускаем Flask
    app.run(host='0.0.0.0', port=PORT)