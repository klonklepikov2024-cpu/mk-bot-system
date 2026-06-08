import logging
import traceback
import html
from config import STAFF_GROUP_ID

# Настраиваем базовый логгер консоли
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(name)s] - %(message)s'
)
logger = logging.getLogger("SecretaryBot")

def log_error(e, context="Неизвестная ошибка"):
    """Пишет ошибку и трейсбек только в консоль сервера (для некритичных сбоев)"""
    error_trace = traceback.format_exc()
    logger.error(f"{context}: {e}\n{error_trace}")

def notify_admin_on_error(bot, e, context="Критический сбой"):
    """Пишет ошибку в консоль И отправляет в админский чат Telegram"""
    log_error(e, context)
    error_trace = traceback.format_exc()
    
    # Обрезаем трейсбек, чтобы влез в лимит Telegram (4096 символов)
    safe_trace = html.escape(error_trace[-3500:])
    error_msg = f"🚨 <b>СИСТЕМНАЯ ОШИБКА ({context})</b>\n\n<pre>{safe_trace}</pre>"
    
    try:
        bot.send_message(STAFF_GROUP_ID, error_msg, parse_mode="HTML")
    except Exception as send_err:
        logger.error(f"Не удалось отправить лог админам. Причина: {send_err}")