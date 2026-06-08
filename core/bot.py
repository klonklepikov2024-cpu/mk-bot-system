import telebot
import traceback
import html
from config import BOT_TOKEN, STAFF_GROUP_ID

# 🔥 Создаем Глобальный Ловец Ошибок
class TelegramExceptionHandler(telebot.ExceptionHandler):
    def handle(self, exception):
        error_trace = traceback.format_exc()
        safe_trace = html.escape(error_trace[-3500:])
        error_msg = f"🚨 <b>СИСТЕМНАЯ ОШИБКА (В хэндлере)</b>\n\n<pre>{safe_trace}</pre>"
        
        try:
            bot.send_message(STAFF_GROUP_ID, error_msg, parse_mode="HTML")
        except:
            print("Не удалось отправить ошибку в ТГ:", error_trace)
            
        return True

bot = telebot.TeleBot(
    BOT_TOKEN, 
    threaded=False, 
    exception_handler=TelegramExceptionHandler()
)