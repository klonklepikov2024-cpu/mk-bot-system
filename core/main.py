import os
import time
import telebot
import threading
import requests
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

is_setup_done = False

def setup():
    """Настройка бота"""
    global is_setup_done
    start_scheduler() 
    time.sleep(2)
    
    try:
        if not APP_URL:
            logger.error("❌ APP_URL не задан!")
            return

        target_url = f"{APP_URL.rstrip('/')}/webhook"
        bot_token = os.getenv('BOT_TOKEN')
        
        logger.info(f"🔄 Устанавливаем вебхук: {target_url}")
        
        # Упрощённый вариант
        requests.get(
            f"https://api.telegram.org/bot{bot_token}/deleteWebhook?drop_pending_updates=True",
            timeout=8
        )
        time.sleep(1)
        
        res = requests.get(
            f"https://api.telegram.org/bot{bot_token}/setWebhook?url={target_url}&drop_pending_updates=True",
            timeout=15
        )
        
        if res.status_code == 200:
            logger.info("✅ Вебхук успешно установлен!")
        else:
            logger.error(f"❌ Telegram ответил: {res.text}")
            
    except Exception as e:
        logger.error(f"❌ Сбой вебхука: {e}")

@app.route('/')
def index():
    return "Secretary Bot is Online and Healthy!", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    # 🔥 Бронебойный прием без проверки заголовков
    update = telebot.types.Update.de_json(request.stream.read().decode('utf-8'))
    bot.process_new_updates([update])
    return 'ok', 200

@app.route('/ping')
def ping():
    return "I am alive!", 200

# === ДАТЧИК ПУЛЬСА СЕКРЕТАРЯ ===
def heartbeat_sec():
    from database.mongo import db
    while True:
        try:
            db['settings'].update_one({"_id": "bot_status"}, {"$set": {"sec_last_seen": time.time()}}, upsert=True)
        except: pass
        time.sleep(60)

threading.Thread(target=heartbeat_sec, daemon=True).start()
# ===============================

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=PORT)