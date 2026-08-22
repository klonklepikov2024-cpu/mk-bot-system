import time
import datetime
import requests
import random
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
    """Скрипт заходит на сайты и собирает реальные праздники на сегодня"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7'
    }

    # Попытка 1: Основной сайт
    try:
        res = requests.get('https://kakoysegodnyaprazdnik.ru/', headers=headers, timeout=5)
        res.encoding = 'utf-8'
        
        # Ищем любые блоки с классом, похожим на название праздника (они часто меняют верстку)
        holidays = re.findall(r'<span[^>]*itemprop="text"[^>]*>(.*?)</span>', res.text)
        if not holidays: # Альтернативный поиск по заголовкам
             holidays = re.findall(r'<h4[^>]*>(.*?)</h4>', res.text)
             
        if holidays:
            clean_holidays = [re.sub(r'<[^>]+>', '', h).strip() for h in holidays if h.strip()]
            # Отфильтровываем слишком короткие или технические строки
            valid_holidays = [h for h in clean_holidays if len(h) > 5 and "праздник" not in h.lower()][:5]
            if valid_holidays:
                return ", ".join(valid_holidays)
    except Exception as e:
        logger.warning(f"Ошибка парсинга kakoysegodnyaprazdnik: {e}")

    # Попытка 2: Запасной сайт (если первый не ответил или поменял дизайн)
    try:
        res = requests.get('https://my-calend.ru/holidays', headers=headers, timeout=5)
        res.encoding = 'utf-8'
        
        # Ищем названия праздников в списках на my-calend
        holidays = re.findall(r'<li[^>]*><a[^>]*>(.*?)</a></li>', res.text)
        
        if holidays:
            clean_holidays = [re.sub(r'<[^>]+>', '', h).strip() for h in holidays if h.strip()]
            if clean_holidays:
                return ", ".join(clean_holidays[:5])
    except Exception as e:
        logger.warning(f"Ошибка парсинга my-calend: {e}")

    # Попытка 3: Экстренный резерв (формируется из текущей даты)
    now = datetime.datetime.now()
    months = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
    today_date = f"{now.day} {months[now.month - 1]}"
    
    return f"День спонтанных сюрпризов, Праздник отличного настроения, День общения ({today_date})"

# ================= ТЕСТОВАЯ КОМАНДА =================
@bot.message_handler(commands=['test_poll'])
def test_poll_cmd(message):
    if str(message.chat.id) != str(STAFF_GROUP_ID) and message.from_user.id != 479938867:
        return
    bot.reply_to(message, "⏳ *Скайнет получил по шапке... Генерирую в фоне*", parse_mode="Markdown")
    
    # запускаем в отдельном потоке, чтобы webhook не висел
    import threading
    threading.Thread(target=generate_and_send_daily_poll, args=(True,), daemon=True).start()
# ====================================================

def generate_and_send_daily_poll(is_test=False):
    """Генерация и рассылка опроса дня ровно в 00:00 (или по команде теста)"""
    now = datetime.datetime.now()
    today_str = now.strftime("%d.%m.%Y")
    
    # 1. Получаем список реальных праздников и ВЫБИРАЕМ ОДИН СЛУЧАЙНЫЙ
    all_holidays_str = get_todays_holidays()
    holidays_list = [h.strip() for h in all_holidays_str.split(",")]
    selected_holiday = random.choice(holidays_list) # <-- Магия рандома здесь
    
    # ================= 2. ЖЕСТКИЙ ПРОМПТ И СИСТЕМНОЕ СООБЩЕНИЕ =================
    system_prompt = (
        "You are an API strictly generating raw JSON. "
        "Output ONLY valid JSON. No markdown, no preambles, no explanations."
    )

    user_prompt = f"""
    Сегодня {today_str}. Тема дня: {selected_holiday}.

    ТВОЯ РОЛЬ: Ты — харизматичный и дерзкий ведущий в закрытом мужском клубе (18+). Тебе нужно написать провокационный опрос для участников. Твой стиль: взрослый юмор, флирт, сарказм и клубный сленг.

    Правила:
    - Вопрос: ОБЯЗАТЕЛЬНО упомяни праздник ({selected_holiday}), сделай горячую подводку к опросу и добавь эмодзи.
    - Ровно 10 вариантов ответа (каждый начинается с эмодзи). ОЧЕНЬ КОРОТКО! Максимум 10-12 слов на ответ. Телеграм запрещает ответы длиннее 100 символов, будь лаконичен!
    - Используй сленг чата знакомств (актив, пассив, универсал, топ, боттом).
    - Сделай ответы жизненными, смешными и с явным интимным подтекстом (18+).
    - ВАЖНО: Будь пошлым и сексуальным, но БЕЗ откровенной чернухи, токсичности и прямых оскорблений. Держи баланс между страстью и юмором!

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
        "nvidia/nemotron-3-ultra-550b-a55b:free", # Надежный работяга
        "nvidia/nemotron-3.5-lightning:free",     # Быстрый дублер
        "meta-llama/llama-3.3-70b-instruct:free", # Классика, отлично пишет JSON
        "z-ai/glm-5.2:free",                      # Резерв
        "openrouter/free",                        # Рулетка на самый крайний случай
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
                
                # 🔥 НАЧАЛО: АВТОМАТИЧЕСКАЯ ОБРЕЗКА ПОД ЛИМИТЫ ТЕЛЕГРАМА 🔥
                # 1. Режем вопрос, если он больше 255 символов
                if len(ai_data.get("question", "")) > 255:
                    ai_data["question"] = ai_data["question"][:250] + "..."
                
                # 2. Режем ответы, если они больше 100 символов
                safe_options = []
                for opt in ai_data.get("options", [])[:10]:
                    if len(opt) > 100:
                        safe_options.append(opt[:96] + "...")
                    else:
                        safe_options.append(opt)
                ai_data["options"] = safe_options
                # 🔥 КОНЕЦ ОБРЕЗКИ 🔥

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