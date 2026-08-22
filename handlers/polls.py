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
import os

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# 👇 ID ТВОЕЙ ГРУППЫ "Ваше мнение, очень важно для нас 😁"
DONOR_GROUP_ID = -1003107308525 

# ================= ПАРСЕР РЕАЛЬНЫХ ПРАЗДНИКОВ =================
def get_todays_holidays():
    """Скрипт заходит на сайт и собирает реальные праздники на сегодня"""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
        res = requests.get('https://kakoysegodnyaprazdnik.ru/', headers=headers, timeout=5)
        res.encoding = 'utf-8'
        
        # Делаем поиск более гибким: игнорируем другие атрибуты в теге
        holidays = re.findall(r'<span[^>]*itemprop="text"[^>]*>(.*?)</span>', res.text)
        
        if holidays:
            # Очищаем от случайных вложенных тегов, если они появились
            clean_holidays = [re.sub(r'<[^>]+>', '', h).strip() for h in holidays]
            return ", ".join(clean_holidays[:5])
            
    except Exception as e:
        logger.warning(f"Ошибка парсинга праздников: {e}")
        
    # Запасной вариант, если сайт недоступен (чтобы не было сюрпризов)
    today_str = datetime.datetime.now().strftime("%d.%m")
    return f"День мужской солидарности, Пятница-развратница, День отличного настроения ({today_str})"

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
    system_prompt = (
        "You are an API strictly generating raw JSON. "
        "Output ONLY valid JSON. No markdown, no preambles, no explanations."
    )

    user_prompt = f"""
    Сегодня {today_str}. Праздники: {real_holidays}.

    ТВОЯ РОЛЬ: Ты — циничный и пошлый стендап-комик, выступающий в закрытом мужском гей-клубе (18+). Тебе нужно написать сценарий интерактивного опроса для зрителей. Твой стиль: жесткий юмор, сарказм, клубный сленг.

    Правила:
    - Выбери один праздник из списка для темы опроса.
    - Вопрос: упомяни праздник, добавь грязный флирт и эмодзи.
    - Ровно 10 вариантов ответа (каждый начинается с эмодзи).
    - Используй клубный сленг зрителей (актив, пассив, универсал, топ, боттом).
    - Используй нецензурную лексику для усиления шуток (пидор, хуй, трахать, дрочить и т.д.).
    - Сделай ответы жизненными и сексуальными. Никакой романтики, только суровая мужская комедия!

    Выведи ТОЛЬКО JSON В ФОРМАТЕ НИЖЕ:
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

    # Ключ берется из окружения (убедись, что он там есть!)
    if not OPENROUTER_API_KEY:
        logger.error("OPENROUTER_API_KEY не найден в переменных окружения")
        try:
            bot.send_message(STAFF_GROUP_ID, "❌ Нет ключа OpenRouter в env")
        except:
            pass
        return

    # Список бесплатных моделей OpenRouter, которые поддерживают JSON
    models_to_try = [
        "meta-llama/llama-3.3-70b-instruct:free", # Llama лучше всего понимает JSON
        "nvidia/nemotron-4-340b-instruct:free",   # Запасная мощная модель
        "qwen/qwen-2-72b-instruct:free"           # Хорошо понимает русский и менее цензурирована
    ]

    for model in models_to_try:
        try:
            res = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://t.me/", # Обязательно для OpenRouter
                    "X-Title": "Skynet Daily Poll",  # Обязательно для OpenRouter
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": 0.55,
                    "max_tokens": 1200,
                    # OpenRouter тоже поддерживает строгий JSON-режим для многих моделей
                    "response_format": {"type": "json_object"} 
                },
                timeout=30
            )

            if res.status_code == 200:
                content = res.json()["choices"][0]["message"]["content"]
                content = content.strip()
                
                # Если модель всё же выдала Markdown-разметку, счищаем её
                if content.startswith("```"):
                    content = re.sub(r"^```(?:json)?\s*", "", content)
                    content = re.sub(r"\s*```$", "", content)
                    
                ai_data = json.loads(content)
                logger.info(f"✅ Успех на модели: {model}")
                break
            else:
                last_error = f"[{model}] Код {res.status_code}: {res.text[:300]}"
                logger.warning(f"Ошибка OpenRouter: {last_error}")

        except Exception as e:
            last_error = f"[{model}] {str(e)}"
            logger.warning(f"Сбой: {last_error}")
            continue
                
    if not ai_data or "question" not in ai_data:
        logger.error(f"❌ Скайнет не смог сгенерировать опрос. Последняя ошибка: {last_error}")
        try: 
            bot.send_message(STAFF_GROUP_ID, f"❌ Ошибка JSON: Скайнет не смог сгенерировать опрос.\nДетали: `{last_error[:200]}`", parse_mode="Markdown")
        except: 
            pass
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