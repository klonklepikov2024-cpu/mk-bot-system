import time
import datetime
import requests
import json
import re
import cloudscraper
import random
import threading
from telebot.apihelper import ApiTelegramException

from core.bot import bot
from core.scheduler import scheduler
# УБРАЛИ КЛЮЧ ИЗ ИМПОРТА ОТСЮДА 👇
from config import GROQ_API_KEYS, chat_ids_mk, chat_ids_parni, chat_ids_ns, chat_ids_gayznak, STAFF_GROUP_ID
from utils.logger import logger
import os

# Скайнет сам возьмет ключ из настроек Render 👇 (И ПОЛУЧИТ ЕГО ЗДЕСЬ)
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# 👇 ID ТВОЕЙ ГРУППЫ "Ваше мнение, очень важно для нас 😁"
DONOR_GROUP_ID = -1003107308525 

# ================= ПАРСЕР РЕАЛЬНЫХ ПРАЗДНИКОВ (RSS) =================
def get_todays_holidays():
    """Неубиваемый парсер через RSS-ленты (никаких меню и стран)"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/rss+xml, application/xml, text/xml'
    }

    # Наш выстраданный фильтр от религии и трагедий
    stop_words = [
        'свят', 'церков', 'православ', 'икон', 'бог', 'собор', 'мученик', 'памят',
        'христ', 'господ', 'богородиц', 'апостол', 'преподоб', 'религ', 'жертв',
        'трагед', 'войн', 'смерт', 'погибш', 'скорб', 'террор', 'ислам', 'аллах', 'иудей',
        'именин', 'ангел'
    ]

    def is_good(text: str) -> bool:
        text = text.strip()
        # Если строка слишком короткая — это мусор (реальные праздники длиннее 8 букв)
        if len(text) < 8 or len(text) > 80:
            return False
        lower = text.lower()
        if any(sw in lower for sw in stop_words):
            return False
        if "202" in text:
            return False
        return True

    found = []

    # === 1. RSS главного сайта ===
    try:
        res = requests.get('https://kakoysegodnyaprazdnik.ru/rss/', headers=headers, timeout=10)
        res.encoding = 'utf-8'
        
        # В RSS названия лежат строго в тегах <title> внутри <item>
        matches = re.findall(r'<item>\s*<title>(.*?)</title>', res.text, re.IGNORECASE | re.DOTALL)
        for m in matches:
            clean = re.sub(r'<[^>]+>', '', m).replace('<![CDATA[', '').replace(']]>', '').strip()
            if is_good(clean) and clean not in found:
                found.append(clean)
                
        if found:
            logger.info(f"✅ Праздники (RSS 1): {found[:5]}")
            return ", ".join(found[:5])
    except Exception as e:
        logger.warning(f"Ошибка RSS 1: {e}")

    # === 2. RSS Calend.ru ===
    try:
        res = requests.get('https://www.calend.ru/img/export/calend.rss', headers=headers, timeout=10)
        res.encoding = 'utf-8'
        
        matches = re.findall(r'<item>\s*<title>(.*?)</title>', res.text, re.IGNORECASE | re.DOTALL)
        for m in matches:
            clean = re.sub(r'<[^>]+>', '', m).replace('<![CDATA[', '').replace(']]>', '').strip()
            if is_good(clean) and clean not in found:
                found.append(clean)
                
        if found:
            logger.info(f"✅ Праздники (RSS 2): {found[:5]}")
            return ", ".join(found[:5])
    except Exception as e:
        logger.warning(f"Ошибка RSS 2: {e}")

    # === 3. ЖЕЛЕЗОБЕТОННЫЙ РЕЗЕРВ ===
    logger.warning("🌐 RSS недоступны! Включаю встроенную заначку.")
    backup_holidays = [
        "День спонтанных сюрпризов", "День горячих поцелуев", "День мужской солидарности",
        "День беззаботности и лени", "День откровенных разговоров", "День экспериментов в постели",
        "Ночь тайных желаний", "День без запретов и правил"
    ]
    import random
    selected = random.sample(backup_holidays, 3)
    return ", ".join(selected)

# ================= ТЕСТОВАЯ КОМАНДА =================
@bot.message_handler(commands=['test_poll'])
def test_poll_cmd(message):
    if str(message.chat.id) != str(STAFF_GROUP_ID) and message.from_user.id != 479938867:
        return
    bot.reply_to(message, "⏳ *Скайнет получил по шапке... Генерирую в фоне*", parse_mode="Markdown")
    
    # запускаем в отдельном потоке, чтобы webhook не висел
    threading.Thread(target=generate_and_send_daily_poll, args=(True,), daemon=True).start()
# ====================================================

def generate_and_send_daily_poll(is_test=False):
    """Генерация и рассылка опроса дня ровно в 00:00 (или по команде теста)"""
    now = datetime.datetime.now()
    today_str = now.strftime("%d.%m.%Y")
    
    # 1. Получаем список реальных праздников и ВЫБИРАЕМ ОДИН СЛУЧАЙНЫЙ
    all_holidays_str = get_todays_holidays()
    holidays_list = [h.strip() for h in all_holidays_str.split(",") if h.strip()]
    selected_holiday = random.choice(holidays_list) if holidays_list else "День без запретов"
    
    # ================= 2. ЖЕСТКИЙ ПРОМПТ И СИСТЕМНОЕ СООБЩЕНИЕ =================
    system_prompt = (
        "You are an API strictly generating raw JSON. "
        "Output ONLY valid JSON. No markdown, no preambles, no explanations."
    )

    user_prompt = f"""
    Сегодня {today_str}. Тема дня: {selected_holiday}.

    ТВОЯ РОЛЬ: Ты — харизматичный и дерзкий ведущий в закрытом мужском клубе (18+). Тебе нужно написать провокационный опрос для участников. Твой стиль: взрослый юмор, флирт, сарказм и клубный сленг.

    Правила:
    - Вопрос: ОБЯЗАТЕЛЬНО используй РОВНО ТУ ТЕМУ, которую я тебе дал ({selected_holiday}). НЕ ВЫДУМЫВАЙ свои праздники (никаких гороскопов и знаков зодиака!). Сделай горячую подводку к этой теме и добавь эмодзи.
    - Ровно 12 вариантов ответа (каждый начинается с эмодзи). ОЧЕНЬ КОРОТКО! Максимум 10-12 слов на ответ. Телеграм запрещает ответы длиннее 100 символов, будь лаконичен!
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
        "вариант 10",
        "вариант 11",
        "вариант 12"
      ]
    }}
    """

    ai_data = None
    last_error = ""

    if not OPENROUTER_API_KEY:
        logger.error("OPENROUTER_API_KEY не найден в переменных окружения")
        try:
            bot.send_message(STAFF_GROUP_ID, "❌ Нет ключа OpenRouter в env")
        except:
            pass
        return

    # 🔥 Актуальный список бесплатных моделей OpenRouter
    models_to_try = [
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "nvidia/nemotron-3.5-lightning:free",
        "google/gemma-4-31b-it:free",
        "google/gemma-4-26b-a4b-it:free",
        "z-ai/glm-5.2:free",
        "poolside/laguna-s-2.1:free",
        "openrouter/free",
    ]

    for model in models_to_try:
        try:
            res = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://t.me/", 
                    "X-Title": "Skynet Daily Poll",  
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": 0.55,
                    "max_tokens": 1200,
                    "response_format": {"type": "json_object"} 
                },
                timeout=30
            )

            if res.status_code == 200:
                response_data = res.json()
                if "choices" not in response_data or not response_data["choices"]:
                    last_error = f"[{model}] Нет ключа 'choices' в ответе API"
                    logger.warning(f"Ошибка парсинга ответа: {response_data}")
                    continue
                
                content = response_data["choices"][0]["message"]["content"]
                if not content:
                    last_error = f"[{model}] Пустой контент"
                    continue
                    
                # 🔥 УМНЫЙ ПОИСК JSON 🔥
                # Ищем всё, что находится между первой { и последней }
                match = re.search(r'\{[\s\S]*\}', content)
                if match:
                    clean_json_str = match.group(0)
                else:
                    clean_json_str = content # Если не нашли скобок, пробуем как есть
                    
                try:
                    ai_data = json.loads(clean_json_str)
                except json.JSONDecodeError:
                    last_error = f"[{model}] Невалидный JSON от модели: {clean_json_str[:100]}"
                    logger.warning(last_error)
                    continue 
                
                # 🔥 НАЧАЛО: АВТОМАТИЧЕСКАЯ ОБРЕЗКА ПОД ЛИМИТЫ ТЕЛЕГРАМА 🔥
                if len(ai_data.get("question", "")) > 255:
                    ai_data["question"] = ai_data["question"][:250] + "..."
                
                # 2. Режем ответы, если они больше 100 символов (и берем максимум 12)
                safe_options = []
                for opt in ai_data.get("options", [])[:12]: # <-- ЗДЕСЬ СТАВИМ 12
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
            last_error = f"[{model}] {type(e).__name__}: {str(e)}"
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
            options=ai_data["options"][:12], # <-- И ЗДЕСЬ СТАВИМ 12
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
        try: bot.send_message(STAFF_GROUP_ID, f"✅ **Тестовый опрос готов!**\nТема: {selected_holiday}\nПосмотрите результат в группе-доноре.", parse_mode="Markdown")
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
    report_text = f"✅ **Авто-Опрос запущен!**\nТема: {selected_holiday}\nСкайнет разослал его в {success_count} чатов."
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