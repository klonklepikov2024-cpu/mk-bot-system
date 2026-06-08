import telebot
from config import BOT_TOKEN

# Создаем экземпляр бота
# threaded=False критически важно для работы вебхуков через Flask/Gunicorn!
bot = telebot.TeleBot(BOT_TOKEN, threaded=False)