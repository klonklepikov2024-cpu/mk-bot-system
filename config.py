import os

# --- Токены и доступы ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
CRYPTO_TOKEN = os.getenv("CRYPTO_TOKEN")
APP_URL = os.getenv("APP_URL") # Ссылка на твой апп на Render (например, https://my-bot.onrender.com)

# --- ID групп и каналов ---
STAFF_GROUP_ID = "-1002196190507" # ID админской группы (Служба Поддержки)

# --- Настройки сервера ---
PORT = int(os.environ.get('PORT', 5000))



# Проверка критических переменных при запуске
if not BOT_TOKEN or not MONGO_URI:
    raise ValueError("❌ КРИТИЧЕСКАЯ ОШИБКА: BOT_TOKEN или MONGO_URI не найдены в переменных окружения!")