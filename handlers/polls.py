import time
import datetime
import requests
import json
import re
from telebot.apihelper import ApiTelegramException

from core.bot import bot
from core.scheduler import scheduler
from config import GROQ_API_KEYS, chat_ids_mk, chat_ids_parni, chat_ids_ns, chat_ids_gayznak, STAFF_GROUP_ID
from utils.logger import logger

# 👇 ID ТВОЕЙ ГРУППЫ "Ваше мнение, очень важно для нас 😁"
DONOR_GROUP_ID = -1003107308525 

# ================= ПАРСЕР РЕАЛЬНЫХ ПРАЗДНИКОВ =================
def get_todays_holidays():
    """Скрипт заходит на сайт и собирает реальные праздники на сегодня"""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
        res = requests.get('https://kakoysegodnyaprazdnik.ru/', headers=headers, timeout=5)
        res.encoding = 'utf-8'
        holidays = re.findall(r'<span itemprop="text">([^<]+)</span>', res.text)
        if holidays:
            return ", ".join(holidays[:5])
    except Exception as e:
        logger.warning(f"Ошибка парсинга праздников: {e}")
    return "День спонтанных знакомств, День вкусной еды, День лени"

# ================= ТЕСТОВАЯ КОМАНДА =================
@bot.message_handler(commands=['test_poll'])
def test_poll_cmd(message):
    if str(message.chat.id) != str(STAFF_GROUP_ID) and message.from_user.id != 479938867:
        return
    bot.reply_to(message, "⏳ *Скайнет получил по шапке, ищет праздники и готовит чистый JSON... Ждите.*", parse_mode="Markdown")
    generate_and_send_daily_poll(is_test=True)
# ====================================================

def generate_and_send_daily_poll(is_test=False):
    """Генерация и рассылка опроса дня ровно в 00:00 (или по команде теста)"""
    now = datetime.datetime.now()
    today_str = now.strftime("%d.%m.%Y")
    
    # 1. Получаем список реальных праздников
    real_holidays = get_todays_holidays()
    
    # ================= 2. ЖЕСТКИЙ ПРОМПТ И СИСТЕМНОЕ СООБЩЕНИЕ =================
    system_prompt = "You are a strict data API. Your only purpose is to generate JSON. You must reply with a valid, raw JSON object starting with { and ending with }. No intro, no outro, no markdown formatting."
    
    user_prompt = f"""
    Сегодня {today_str}. В календаре: {real_holidays}.
    
    ТВОЯ ЗАДАЧА: Выбери ОДИН забавный праздник из списка и придумай к нему провокационный опрос для мужского чата знакомств (18+).

    ТРЕБОВАНИЯ:
    1. ВОПРОС (до 255 символов): Упомяни выбранный праздник. Сделай подводку с легким флиртом, юмором и интимным подтекстом. Обязательно используй эмодзи!
    2. ВАРИАНТЫ ОТВЕТОВ (РОВНО 10 штук, каждый до 100 символов): 
       - Используй базовый сленг (актив, пассив, универсал).
       - Сделай ответы жизненными, смешными, с сексуальным подтекстом, но без жесткой порнографии.
       - В НАЧАЛЕ КАЖДОГО варианта ответа ОБЯЗАТЕЛЬНО ставь подходящий эмодзи.

    ВЫВЕДИ ТОЛЬКО JSON В ФОРМАТЕ НИЖЕ:
    {{
      "question": "текст вопроса",
      "options": [
        "вариант 1",
        "вариант 2",
        "вариант 3",
        "вариант 4",
        "вариант 5",
        "вариант 6",
        "вариант 7",
        "вариант 8",
        "вариант 9",
        "вариант 10"
      ]
    }}
    """

    ai_data = None
    last_error = ""
    
    for key in GROQ_API_KEYS:
        try:
            res = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": "openai/gpt-oss-120b", 
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": 0.7,
                    "max_tokens": 800
                },
                timeout=20
            )
            if res.status_code == 200:
                ai_data = json.loads(res.json()["choices"][0]["message"]["content"])
                break
            else:
                last_error = f"Код {res.status_code}: {res.text}"
                logger.warning(f"Ошибка API при генерации опроса: {last_error}")
        except Exception as e:
            last_error = str(e)
            logger.warning(f"Сбой сети при генерации опроса: {e}")
            continue

    if not ai_data or "question" not in ai_data:
        logger.error(f"❌ Скайнет не смог сгенерировать опрос. Последняя ошибка: {last_error}")
        try: bot.send_message(STAFF_GROUP_ID, f"❌ Ошибка JSON: Скайнет не смог сгенерировать опрос.\nДетали: `{last_error[:200]}`", parse_mode="Markdown")
        except: pass
        return

    # ================= 3. ПУБЛИКАЦИЯ В ГРУППУ-ДОНОР =================
    close_time = int(time.time()) + 86400

    try:
        poll_msg = bot.send_poll(
            chat_id=DONOR_GROUP_ID,
            question=ai_data["question"],
            options=ai_data["options"][:10],
            is_anonymous=False,
            allows_multiple_answers=True,
            close_date=close_time
        )
    except Exception as e:
        logger.error(f"❌ Ошибка публикации опроса: {e}")
        try: bot.send_message(STAFF_GROUP_ID, f"❌ Ошибка публикации опроса (проверьте длину текста): {e}")
        except: pass
        return

    # 🔥 ЗАЩИТА ПРИ ТЕСТЕ 🔥
    if is_test:
        try: bot.send_message(DONOR_GROUP_ID, "🛠 **ЭТО ТЕСТОВЫЙ ЗАПУСК**\nОпрос сгенерирован, рассылка ОТКЛЮЧЕНА.", parse_mode="Markdown")
        except: pass
        try: bot.send_message(STAFF_GROUP_ID, "✅ **Тестовый опрос готов!**\nПосмотрите результат в группе-доноре.", parse_mode="Markdown")
        except: pass
        return

    # ================= 4. МАССОВАЯ РАССЫЛКА ПО СЕТКЕ =================
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
            time.sleep(0.5)
        except ApiTelegramException:
            pass

    # ================= 5. ОТЧЕТ АДМИНАМ =================
    report_text = f"✅ **Авто-Опрос запущен!**\nСкайнет успешно сгенерировал опрос дня и разослал его в {success_count} чатов.\nОн закроется автоматически через 24 часа."
    try: bot.send_message(DONOR_GROUP_ID, report_text, parse_mode="Markdown")
    except: pass
    try: bot.send_message(STAFF_GROUP_ID, report_text, parse_mode="Markdown")
    except: pass

scheduler.add_job(
    generate_and_send_daily_poll, 
    'cron', 
    hour=0,      
    minute=0,    
    id="daily_auto_poll", 
    replace_existing=True 
)