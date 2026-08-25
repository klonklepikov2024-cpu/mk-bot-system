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
from config import GROQ_API_KEYS, chat_ids_mk, chat_ids_parni, chat_ids_ns, chat_ids_gayznak, chat_ids_rainbow, STAFF_GROUP_ID
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

    ТВОЯ РОЛЬ: Ты — харизматичный, остроумный и стильный ведущий в мужском гей-комьюнити (18+). Твоя задача — написать жизненный, смешной и вовлекающий опрос для парней.

    СТРОГИЕ ПРАВИЛА:
    1. ВОПРОС (до 255 символов): 
       - Начни строго с фразы: "Сегодня {today_str} отмечается [Название праздника]!"
       - Сделай креативную и дерзкую подводку к теме.

    2. СЕКРЕТ КРЕАТИВНЫХ ОТВЕТОВ (РОВНО 12 штук):
       - ГЛАВНОЕ ПРАВИЛО: АБСОЛЮТНЫЙ РАНДОМ! Генерируй уникальные сценарии каждый день.
       - 🛑 СТРОГИЙ ЗАПРЕТ НА КОПИРОВАНИЕ: Список ситуаций ниже — это ТОЛЬКО абстрактные направления для твоей фантазии. КАТЕГОРИЧЕСКИ ЗАПРЕЩАЕТСЯ использовать точные слова из моего списка (не пиши слова "аквариум", "костер", "драники", "палатки" и т.д.). Каждый раз придумывай НОВЫЕ блюда, НОВЫЕ хобби и НОВЫЕ локации!
       - ТОНКОСТЬ (БЕЗ «БОРЩА»): Юмор должен быть с отсылками к гей-культуре, но без тупой пошлятины. Самоирония, эстетика, сарказм и жизненный быт.
       - Обыгрывай тему праздника через МЕТАФОРЫ. В НАЧАЛЕ КАЖДОГО варианта ставь подходящий эмодзи. Длина ответа — до 100 символов.
       
       Используй этот расширенный спектр ситуаций (выбирай 4-5 РАЗНЫХ категорий каждый день, постоянно миксуй их):
       * Дейтинг и драма: приложения для знакомств, игнор в чатах, внезапные сообщения от бывших, поиск актива/пассива (метафорично!), неловкие первые свидания.
       * Эстетика и уход: спортзал и сушка, барбершопы, косметология, муки выбора парфюма или лука на вечер, онлайн-шопинг.
       * Отношения и совместный быт: ленивый выходной с парнем, споры о том, кто сегодня моет посуду, уютные вечера в обнимку, планирование отпуска.
       * Светская жизнь и ивенты: ночные клубы, бары, громкие музыкальные фестивали, концерты, сплетни с друзьями за коктейлем.
       * Гастрономия и гедонизм: ночной жор, кулинарные шедевры своими руками, ожидание курьера с едой, походы в рестораны.
       * Диджитал и поп-культура: запойный просмотр триллеров или драм на большом экране, онлайн-работа, администрирование чатов.
       * Природа и перезагрузка: побег от цивилизации, выезды за город, пикники, долгие прогулки по лесопаркам.
       * Домашний уют и питомцы: наглые домашние животные, требующие внимания, медитативные хобби, наведение порядка.
       * Рутина и выгорание: токсичное начальство, кофе литрами, пробки, горящие дедлайны, желание всё бросить.

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

    # 🔥 СИСТЕМА ЗАПАСНЫХ ПАРАШЮТОВ 🔥
    # Сначала стучимся в самую умную, если она лежит - идем к проверенным
    models_queue = [
        "gemini-3.7-flash", # Новинка (самая умная)
        "gemini-2.5-flash", # Надежный танк (квоты 100% есть)
    ]

    for model_name in models_queue:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}"
        
        # Делаем до 3 попыток для каждой модели
        for attempt in range(3):
            try:
                payload = {
                    "systemInstruction": {"parts": [{"text": system_prompt}]},
                    "contents": [{"parts": [{"text": user_prompt}]}],
                    "generationConfig": {
                        "temperature": 0.9, 
                        "responseMimeType": "application/json"
                    }
                }

                res = requests.post(url, headers={"Content-Type": "application/json"}, json=payload, timeout=40)

                if res.status_code == 200:
                    response_data = res.json()
                    content = response_data["candidates"][0]["content"]["parts"][0]["text"]
                    
                    try:
                        ai_data = json.loads(content)
                        
                        # Автоматическая обрезка под лимиты Телеграма
                        if len(ai_data.get("question", "")) > 255:
                            ai_data["question"] = ai_data["question"][:250] + "..."
                        
                        safe_options = [opt[:96] + "..." if len(opt) > 100 else opt for opt in ai_data.get("options", [])[:12]]
                        ai_data["options"] = safe_options
                        
                        logger.info(f"✅ Успех: Опрос сгенерирован через модель {model_name} (попытка {attempt + 1})")
                        break # Успех! Вырываемся из цикла попыток

                    except json.JSONDecodeError:
                        last_error = "Невалидный JSON от модели"
                        logger.warning(last_error)
                        
                elif res.status_code in [503, 429]:
                    last_error = f"Код {res.status_code} на {model_name}. Ждем 15 сек..."
                    logger.warning(last_error)
                    time.sleep(15) # Ждем пока Гугл продышится
                else:
                    last_error = f"Код {res.status_code}: {res.text[:300]}"
                    logger.warning(f"Ошибка Gemini API ({model_name}): {last_error}")
                    break # Если ошибка другая (например, 404) - сразу идем к следующей модели

            except Exception as e:
                last_error = f"{type(e).__name__}: {str(e)}"
                logger.warning(f"Сбой подключения ({model_name}): {last_error}")
                time.sleep(5)
                
        if ai_data:
            break # Вырываемся из цикла моделей, если всё получилось!

    # 🔥 ВОТ ЭТОТ БЛОК НУЖНО ВЕРНУТЬ 🔥
    if not ai_data or "question" not in ai_data:
        logger.error(f"❌ Скайнет не смог сгенерировать опрос. Последняя ошибка: {last_error}")
        try: 
            bot.send_message(STAFF_GROUP_ID, f"❌ Ошибка JSON: Скайнет не смог сгенерировать опрос.\nДетали: `{last_error[:200]}`", parse_mode="Markdown")
        except: 
            pass
        return
    # 🔥 КОНЕЦ БЛОКА 🔥

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
    all_target_chats.extend(chat_ids_rainbow.values()) # <--- ДОБАВИЛИ РАДУГУ 🌈
    
    # 🔥 УБИРАЕМ ВСЕ ДУБЛИКАТЫ ЧАТОВ 🔥
    unique_chats = set(all_target_chats)
    
    success_count = 0
    for chat_id in unique_chats:
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