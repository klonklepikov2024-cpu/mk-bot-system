import telebot
import traceback
import html
from config import BOT_TOKEN, STAFF_GROUP_ID

# 🔥 Создаем Глобальный Ловец Ошибок
class TelegramExceptionHandler(telebot.ExceptionHandler):
    _last = {"key": None, "t": 0.0}

    def handle(self, exception):
        import time
        error_trace = traceback.format_exc()
        
        # Анти-спам: одинаковую ошибку чаще раза в минуту не повторяем
        key = error_trace[-300:]
        now = time.time()
        if self._last["key"] == key and now - self._last["t"] < 60:
            return True
        self._last["key"] = key
        self._last["t"] = now
        
        safe_trace = html.escape(error_trace)[-3500:]
        error_msg = f"🚨 <b>СИСТЕМНАЯ ОШИБКА (В хэндлере)</b>\n\n<pre>{safe_trace}</pre>"
        
        try:
            bot.send_message(STAFF_GROUP_ID, error_msg, parse_mode="HTML")
        except Exception:
            print("Не удалось отправить ошибку в ТГ:", error_trace)
            
        return True

bot = telebot.TeleBot(
    BOT_TOKEN, 
    threaded=False, 
    exception_handler=TelegramExceptionHandler()
)