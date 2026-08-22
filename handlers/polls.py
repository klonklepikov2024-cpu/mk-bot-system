import time
import datetime
import requests
import json
from telebot.apihelper import ApiTelegramException

from core.bot import bot
from core.scheduler import scheduler
from config import GROQ_API_KEYS, chat_ids_mk, chat_ids_parni, chat_ids_ns, chat_ids_gayznak, STAFF_GROUP_ID
from utils.logger import logger

# 👇 ID ТВОЕЙ ГРУППЫ "Ваше мнение, очень важно для нас 😁"
DONOR_GROUP_ID = -1003107308525 

# ================= ТЕСТОВАЯ КОМАНДА =================
@bot.message_handler(commands=['test_poll'])
def test_poll_cmd(message):
    # Защита: команду может вызвать только владелец (ты) или кто-то из админского чата
    if str(message.chat.id) != str(STAFF_GROUP_ID) and message.from_user.id != 479938867:
        return
    bot.reply_to(message, "⏳ *Скайнет генерирует тестовый опрос... Пожалуйста, подождите пару секунд.*", parse_mode="Markdown")
    generate_and_send_daily_poll(is_test=True)
# ====================================================

def generate_and_send_daily_poll(is_test=False):
    """Генерация и рассылка опроса дня ровно в 00:00 (или по команде теста)"""
    now = datetime.datetime.now()
    today_str = now.strftime("%d.%m")
    
    # ================= 1. ГЕНЕРАЦИЯ ИДЕИ ЧЕРЕЗ ИИ (GROQ) =================
    prompt = f"""
    Сегодня {today_str}. Узнай или придумай забавный неофициальный праздник на этот день.
    Придумай смешной, жизненный опрос для мужских и гей-чатов с легким эротическим или провокационным подтекстом.
    
    Требования:
    1. 1 Вопрос (строго до 255 символов) с упоминанием праздника.
    2. Ровно 10 вариантов ответа (каждый строго ДО 100 символов, иначе Телеграм выдаст ошибку!), включая шуточные.
    
    Верни ответ СТРОГО в формате JSON:
    {{"question": "текст вопроса", "options": ["вариант 1", "вариант 2", ..., "вариант 10"]}}
    """

    ai_data = None
    for key in GROQ_API_KEYS:
        try:
            res = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": "openai/gpt-oss-120b", 
                    "response_format": {"type": "json_object"},
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.7,
                    "max_tokens": 800
                },
                timeout=20
            )
            if res.status_code == 200:
                ai_data = json.loads(res.json()["choices"][0]["message"]["content"])
                break
        except Exception as e:
            logger.warning(f"Ошибка ИИ при генерации опроса: {e}")
            continue

    if not ai_data or "question" not in ai_data:
        logger.error("❌ Не удалось сгенерировать опрос ИИ")
        try: bot.send_message(STAFF_GROUP_ID, "❌ Ошибка: Скайнет не смог сгенерировать ежедневный опрос. API не ответил.")
        except: pass
        return

    # ================= 2. ПУБЛИКАЦИЯ В ГРУППУ-ДОНОР =================
    close_time = int(time.time()) + 86400 # Ровно 24 часа жизни опроса

    try:
        poll_msg = bot.send_poll(
            chat_id=DONOR_GROUP_ID,
            question=ai_data["question"],
            options=ai_data["options"][:10], # Страховка: берем максимум 10 ответов
            is_anonymous=False,              # Имена участников открыты
            allows_multiple_answers=True,    # Несколько ответов
            close_date=close_time            # Таймер до закрытия
        )
    except Exception as e:
        logger.error(f"❌ Ошибка публикации опроса в донор: {e}")
        try: bot.send_message(STAFF_GROUP_ID, f"❌ Ошибка публикации опроса (проверьте длину текста): {e}")
        except: pass
        return

    # 🔥 ЗАЩИТА ПРИ ТЕСТЕ: ЕСЛИ ЭТО ТЕСТ - ОСТАНАВЛИВАЕМ КОД ЗДЕСЬ 🔥
    if is_test:
        try: bot.send_message(DONOR_GROUP_ID, "🛠 **ЭТО ТЕСТОВЫЙ ЗАПУСК**\nОпрос сгенерирован, но рассылка по сетке **ОТКЛЮЧЕНА**.", parse_mode="Markdown")
        except: pass
        try: bot.send_message(STAFF_GROUP_ID, "✅ **Тестовый опрос готов!**\nПосмотрите результат в группе «Ваше мнение...». Рассылка по группам отключена.", parse_mode="Markdown")
        except: pass
        return

    # ================= 3. МАССОВАЯ РАССЫЛКА ПО СЕТКЕ =================
    all_target_chats = []
    all_target_chats.extend(chat_ids_mk.values())
    all_target_chats.extend(chat_ids_parni.values())
    all_target_chats.extend(chat_ids_ns.values())
    all_target_chats.extend(chat_ids_gayznak.values())
    
    success_count = 0
    for chat_id in all_target_chats:
        try:
            bot.forward_message(
                chat_id=chat_id,
                from_chat_id=DONOR_GROUP_ID,
                message_id=poll_msg.message_id
            )
            success_count += 1
            time.sleep(0.5) # Защита от флуд-контроля Telegram
        except ApiTelegramException as e:
            logger.debug(f"Игнор ошибки пересылки в {chat_id}: {e}")

    # ================= 4. ОТЧЕТ АДМИНАМ =================
    report_text = f"✅ **Авто-Опрос запущен!**\nСкайнет успешно сгенерировал опрос дня и разослал его в {success_count} чатов.\nОн закроется автоматически через 24 часа."
    try: bot.send_message(DONOR_GROUP_ID, report_text, parse_mode="Markdown")
    except: pass
    try: bot.send_message(STAFF_GROUP_ID, report_text, parse_mode="Markdown")
    except: pass

# Планировщик по-прежнему работает и ждет полночи для боевого запуска
scheduler.add_job(
    generate_and_send_daily_poll, 
    'cron', 
    hour=0,      # Час запуска (Полночь)
    minute=0,    # Минуты
    id="daily_auto_poll", 
    replace_existing=True 
)