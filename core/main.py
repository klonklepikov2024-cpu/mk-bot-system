import os
import time
import telebot
from flask import Flask, request
import threading

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
        if not APP_URL:
            logger.error("❌ ВНИМАНИЕ: APP_URL не задан!")
            return

        target_url = f"{APP_URL.rstrip('/')}/webhook"
        current_webhook = bot.get_webhook_info().url
        
        # 🔥 УМНАЯ УСТАНОВКА: Меняем вебхук ТОЛЬКО если он слетел
        if current_webhook != target_url:
            logger.info(f"🔄 Обновляем вебхук: {current_webhook} -> {target_url}")
            bot.remove_webhook()
            time.sleep(1) # Защита от лимитов Телеграма
            bot.set_webhook(url=target_url)
            logger.info(f"✅ Вебхук успешно установлен!")
        else:
            logger.info(f"✅ Вебхук уже настроен правильно, пропускаем установку.")
    except Exception as e:
        logger.error(f"❌ Ошибка установки вебхука: {e}")

# 🔥 ГЛАВНАЯ МАГИЯ ЗДЕСЬ 🔥
@app.before_request
def initialize_worker():
    global is_setup_done
    if not is_setup_done:
        is_setup_done = True 
        threading.Thread(target=setup, daemon=True).start()

# 👇 МАРШРУТ-ЗАГЛУШКА ДЛЯ ЗДОРОВЬЯ СЕРВЕРА (ЧТОБЫ НЕ БЫЛО ОШИБОК 404/500) 👇
@app.route('/')
def index():
    return "Secretary Bot is Online and Healthy!", 200

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

# === ДАТЧИК ПУЛЬСА СЕКРЕТАРЯ ===
def heartbeat_sec():
    from database.mongo import db
    import time
    while True:
        try:
            db['settings'].update_one({"_id": "bot_status"}, {"$set": {"sec_last_seen": time.time()}}, upsert=True)
        except: pass
        time.sleep(60)

threading.Thread(target=heartbeat_sec, daemon=True).start()
# ===============================

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=PORT)