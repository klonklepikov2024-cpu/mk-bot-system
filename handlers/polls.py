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

# Скайнет сам возьмет ключ из настроек Render 👇 
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") # <--- ДОБАВИЛИ ЭТУ СТРОКУ

# 👇 ID ТВОЕЙ ГРУППЫ "Ваше мнение, очень важно для нас 😁"
DONOR_GROUP_ID = -1003107308525 

# ================= ПАРСЕР РЕАЛЬНЫХ ПРАЗДНИКОВ (СУПЕР-ФИЛЬТР) =================
def get_todays_holidays():
    """Парсер с умным фильтром: ищет только слова 'День/Праздник' и игнорирует меню/войну/религию"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept-Language': 'ru-RU,ru;q=0.9'
    }

    def is_good(text: str) -> bool:
        text = text.strip()
        # 1. Отсекаем слишком короткое и длинное
        if len(text) < 8 or len(text) > 80:
            return False
            
        lower = text.lower()
        
        # 2. Черный список: религия, война, политика, страны, меню
        stop_words = [
            'свят', 'церков', 'православ', 'икон', 'бог', 'собор', 'мученик', 'памят',
            'христ', 'господ', 'богородиц', 'апостол', 'преподоб', 'религ', 'жертв',
            'трагед', 'войн', 'смерт', 'погибш', 'скорб', 'террор', 'ислам', 'аллах', 'иудей',
            'именин', 'ангел', 'битв', 'войск', 'арми', 'фашист', 'ссср', 'геро', 
            'отечеств', 'государств', 'национальн', 'флаг', 'герб', 'независимост', 
            'конституци', 'полици', 'вдв', 'мвд', 'фсб', 'президент', 'календар',
            'россия', 'росси', 'республик', 'мире', 'времени'
        ]
        if any(sw in lower for sw in stop_words):
            return False
            
        if "202" in text: # Года нам не нужны
            return False
            
        # 🔥 3. ВОЛШЕБНАЯ ПУЛЯ: Это вообще праздник? 
        # Должно содержать эти слова, иначе это мусор из меню сайта (Магнитные бури, Таро и т.д.)
        valid_starts = ['день ', 'ночь ', 'праздник ', 'всемирный ', 'международный ']
        if not any(v in lower for v in valid_starts) and not lower.startswith('день') and not lower.startswith('ночь'):
            return False
            
        return True

    found = []

    # Функция-помощник: выдирает ВЕСЬ текст со страницы и жестко фильтрует
    def parse_site(url):
        try:
            res = requests.get(url, headers=headers, timeout=8)
            res.encoding = 'utf-8'
            # Ищем любой текст между HTML-тегами >текст<
            matches = re.findall(r'>([^<]+)<', res.text)
            for m in matches:
                clean = m.strip()
                if is_good(clean) and clean not in found:
                    found.append(clean)
        except Exception as e:
            logger.warning(f"Ошибка парсинга {url}: {e}")

    # Проходимся по трем главным сайтам (именно СЕГОДНЯШНИЕ даты)
    parse_site('https://my-calend.ru/holidays')
    parse_site('https://www.calend.ru/holidays/')
    parse_site('https://kakoysegodnyaprazdnik.ru/')

    if found:
        logger.info(f"✅ Найдены праздники: {found[:5]}")
        return ", ".join(found[:5])

    # Если все 3 сайта упали
    logger.warning("🌐 Все сайты недоступны! Включаю заначку.")
    backup_holidays = [
        "День спонтанных сюрпризов", "День горячих поцелуев", "День мужской солидарности",
        "День беззаботности и лени", "День откровенных разговоров", "День экспериментов в постели"
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
    
    # 1. Делаем красивую дату (например: "23 августа")
    months = ['', 'января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря']
    today_str = f"{now.day} {months[now.month]}"
    
    # Получаем список реальных праздников и ВЫБИРАЕМ ОДИН СЛУЧАЙНЫЙ
    all_holidays_str = get_todays_holidays()
    holidays_list = [h.strip() for h in all_holidays_str.split(",") if h.strip()]
    selected_holiday = random.choice(holidays_list) if holidays_list else "День без запретов"
    
    # ================= 2. ЖЕСТКИЙ ПРОМПТ И СИСТЕМНОЕ СООБЩЕНИЕ =================
    system_prompt = "You are an API generating raw JSON. Respond with valid JSON only."

    user_prompt = f"""
    Сегодня {today_str}. Тема дня: {selected_holiday}.

    ТВОЯ РОЛЬ: Ты — харизматичный и креативный ведущий в мужском клубе (18+). Твоя задача — написать жизненный, смешной и вовлекающий опрос.

    СТРОГИЕ ПРАВИЛА:
    1. ВОПРОС (до 255 символов): 
       - Начни строго с фразы: "Сегодня {today_str} отмечается [Название праздника]!"
       - Сделай креативную подводку к теме.

    2. СЕКРЕТ КРЕАТИВНЫХ ОТВЕТОВ (РОВНО 12 штук):
       - НЕ зацикливайся на одном и том же! Никаких шаблонов "только диван" или "только секс". 
       - Обыгрывай тему праздника через МЕТАФОРЫ в самых РАЗНЫХ сферах жизни.
       - В НАЧАЛЕ КАЖДОГО варианта ставь подходящий эмодзи. Длина ответа — до 100 символов.
       
       Используй этот микс сфер (адаптируй их под праздник {selected_holiday}):
       * Активный отдых: выезды с палатками, рыбалка, жарка мяса на шампурах у костра.
       * Уютные хобби: залипание на рыбок в аквариуме, домашний ремонт и обустройство, готовка классных ужинов.
       * Контент: просмотр напряженных триллеров или драм под одеялом.
       * Отношения: уютный домашний вечер с любимым парнем.
       * Игривый флирт (18+): поиск актива/пассива на вечер, грязные переписки, обмен горячими фото.
       * Самоирония: рабочие дедлайны, абсолютная лень или когда всё идет не по плану.

    ПРИМЕР ЛОГИКИ: Если праздник "День Ветра", то ответ про хобби: "🐟 В голове гуляет ветер, просто сижу и залипаю на свой аквариум", а про отношения: "🌪 Сносит крышу от чувств, провожу время со своим парнем".

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

    if not GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY не найден в переменных окружения")
        return

    try:
        # Обращаемся напрямую к мозгу Gemini 1.5 Pro
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:generateContent?key={GEMINI_API_KEY}"
        
        # Специальный формат запроса для Google API
        payload = {
            "systemInstruction": {
                "parts": [{"text": system_prompt}]
            },
            "contents": [{
                "parts": [{"text": user_prompt}]
            }],
            "generationConfig": {
                "temperature": 0.8, # Делаем его чуть более креативным
                "responseMimeType": "application/json" # 🔥 МАГИЯ: Заставляем выдавать чистый JSON!
            }
        }

        res = requests.post(
            url,
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=30
        )

        if res.status_code == 200:
            response_data = res.json()
            
            # Вытаскиваем текст ответа из структуры Gemini
            content = response_data["candidates"][0]["content"]["parts"][0]["text"]
            
            try:
                ai_data = json.loads(content)
                
                # 🔥 НАЧАЛО: АВТОМАТИЧЕСКАЯ ОБРЕЗКА ПОД ЛИМИТЫ ТЕЛЕГРАМА 🔥
                if len(ai_data.get("question", "")) > 255:
                    ai_data["question"] = ai_data["question"][:250] + "..."
                
                safe_options = []
                for opt in ai_data.get("options", [])[:12]:
                    if len(opt) > 100:
                        safe_options.append(opt[:96] + "...")
                    else:
                        safe_options.append(opt)
                ai_data["options"] = safe_options
                # 🔥 КОНЕЦ ОБРЕЗКИ 🔥
                
                logger.info("✅ Успех: Опрос сгенерирован через Gemini API!")
                
            except json.JSONDecodeError:
                last_error = "Невалидный JSON от модели"
                logger.warning(last_error)
        else:
            last_error = f"Код {res.status_code}: {res.text[:300]}"
            logger.warning(f"Ошибка Gemini API: {last_error}")

    except Exception as e:
        last_error = f"{type(e).__name__}: {str(e)}"
        logger.warning(f"Сбой: {last_error}")
                
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