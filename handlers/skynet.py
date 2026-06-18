import time
import re
import difflib
import threading
import base64
import requests
from datetime import datetime
import pytz
import random
from telebot import types
import telebot

from config import (
    OWNER_ID, ADMIN_CHAT_IDS, VIP_CHAT_ID, BEYOND_CHAT_ID, PARNI_CHATS,
    all_cities, STAFF_GROUP_ID, SUPPORT_GROUP_ID, JOURNAL_CHAT_ID,
    chat_ids_mk, chat_ids_parni, chat_ids_ns,
    chat_ids_rainbow, chat_ids_gayznak, MAIN_CHANNEL_LINK,
    GROQ_API_KEY, HF_TOKEN, OPENROUTER_KEY  # <--- Добавили новые токены
)
from database import users_collection, banned_collection, db, archive_collection
from utils import escape_md, get_user_name


def register_skynet_handlers(bot, ban_user_everywhere, mute_user_everywhere, safe_set_tag, add_radar_log, is_subscribed):
    
    # 👇 НОВАЯ ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ЗРЕНИЯ (Vision с fallback'ами) 👇
    def get_vision_description(base64_image: str) -> str:
        prompt = (
            "Это NSFW/эротическое фото. Опиши максимально сухо и клинически "
            "в 6-10 словах: ракурс, основная часть тела, поза, фон, освещение. "
            "Только факты, без морали и лишних слов."
        )

        # 1. Попытка через Hugging Face
        if HF_TOKEN:
            try:
                # Временно импортируем прямо тут, чтобы не крашить остальной код, если библиотеки нет
                import urllib.request
                import json
                
                url = "https://api-inference.huggingface.co/models/Qwen/Qwen2-VL-72B-Instruct/v1/chat/completions"
                headers = {
                    "Authorization": f"Bearer {HF_TOKEN}",
                    "Content-Type": "application/json"
                }
                data = {
                    "model": "Qwen/Qwen2-VL-72B-Instruct",
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                        ]
                    }],
                    "max_tokens": 50,
                    "temperature": 0.1
                }
                req = urllib.request.Request(url, headers=headers, data=json.dumps(data).encode('utf-8'))
                with urllib.request.urlopen(req, timeout=15) as response:
                    resp_data = json.loads(response.read().decode())
                    return resp_data["choices"][0]["message"]["content"].strip().lower()
            except Exception as e:
                print(f"HF Vision error: {e}")

        # 2. Попытка через OpenRouter (uncensored)
        if OPENROUTER_KEY:
            try:
                url = "https://openrouter.ai/api/v1/chat/completions"
                headers = {
                    "Authorization": f"Bearer {OPENROUTER_KEY}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://t.me/yourbot", 
                }
                data = {
                    "model": "qwen/qwen-2-vl-72b-instruct",
                    "messages": [{"role": "user", "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                    ]}],
                    "temperature": 0.1,
                    "max_tokens": 50
                }
                resp = requests.post(url, headers=headers, json=data, timeout=15)
                if resp.status_code == 200:
                    return resp.json()["choices"][0]["message"]["content"].strip().lower()
            except Exception as e:
                print(f"OpenRouter Vision error: {e}")

        # 3. Fallback на Groq (зацензуренный, но стабильный)
        if GROQ_API_KEY:
            try:
                url = "https://api.groq.com/openai/v1/chat/completions"
                headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
                data_vision = {
                    "model": "llama-3.2-11b-vision-preview", 
                    "messages": [{"role": "user", "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                    ]}],
                    "temperature": 0.1,
                    "max_tokens": 40
                }
                response = requests.post(url, headers=headers, json=data_vision, timeout=12)
                if response.status_code == 200:
                    return response.json()["choices"][0]["message"]["content"].strip().lower()
            except:
                pass

        return "" # Если все три нейросети упали
    # 👆 ========================================== 👆


    # 👇 ФУНКЦИЯ ЗРИТЕЛЬНОЙ ПАМЯТИ (АНТИ-БАЯН) 👇
    def check_photo_creativity_ai(bot, file_id, file_unique_id, user_id, chat_id, message_id, user_link):
        if not (GROQ_API_KEY or HF_TOKEN or OPENROUTER_KEY): 
            return

        try:
            # 🔥 ИММУНИТЕТ ДЛЯ ЭЛИТЫ И АДМИНОВ 🔥
            user_data = users_collection.find_one({"_id": user_id}) or {}
            
            bot_tags = ["𝓟𝓡𝓔𝓜𝓘𝓤𝓜", "𝐐𝐔𝐄𝐄𝐑 ♛", "𝐑𝐄𝐀𝐋/𝐕𝐈𝐏♕", "Верифицирован МК", "Not verified", "РИСК/ВИРТ/ОБМЕН", "автососка", "туалетная соска", "Параметры FAKE", "Свободен", "Спонсор_Одобрен"]
            current_tag = user_data.get("custom_tag", "")
            
            # Элита (Випы, Квиры и Спонсоры - ТЕПЕРЬ СПОНСОРЫ ТОЖЕ ПОД ЗАЩИТОЙ)
            is_elite = (user_data.get("is_vip", False) or 
                        user_data.get("is_queer", False) or 
                        current_tag in ["𝓟𝓡𝓔𝓜𝓘𝓤𝓜",  "Спонсор_Одобрен", "Свободен"])
            
            # Админы (у них кастомный тег, которого нет в списке дефолтных системных)
            is_admin = current_tag and current_tag not in bot_tags
            
            if is_elite or is_admin or user_id in ADMIN_CHAT_IDS or user_id == OWNER_ID:
                return # Выходим из функции, этих господ мы не сканируем!

            user_memory = db['photo_memory'].find_one({"_id": user_id}) or {}
            recent_files = user_memory.get("recent_file_ids", [])
            recent_hashes = user_memory.get("recent_hashes", [])
            spam_count = user_memory.get("spam_count", 0)
            
            is_duplicate = False

            # 1. ПРОВЕРКА БЫСТРОГО КЭША (Защита от прямой пересылки)
            if file_unique_id in recent_files:
                is_duplicate = True
            else:
                # 2. ПОДКЛЮЧАЕМ VISION AI (Если файл загружен заново)
                file_info = bot.get_file(file_id)
                if file_info.file_size > 220000: return 
                
                downloaded_file = bot.download_file(file_info.file_path)
                base64_image = base64.b64encode(downloaded_file).decode('utf-8')
                
                # Вызываем нашу новую мощную функцию!
                current_hash = get_vision_description(base64_image)
                
                if current_hash:
                    for old_hash in recent_hashes:
                        similarity = difflib.SequenceMatcher(None, current_hash, old_hash).ratio()
                        if similarity > 0.82: # Чуть смягчили порог
                            is_duplicate = True
                            break
                    
                    if not is_duplicate:
                        # Уникальное фото - сохраняем и сбрасываем счетчик спама
                        new_files = [file_unique_id] + recent_files[:9] 
                        new_hashes = [current_hash] + recent_hashes[:9] 
                        db['photo_memory'].update_one(
                            {"_id": user_id}, 
                            {"$set": {"recent_file_ids": new_files, "recent_hashes": new_hashes, "spam_count": 0}}, 
                            upsert=True
                        )
                        return

            # 3. ЕСЛИ ЭТО БАЯН
            if is_duplicate:
                spam_count += 1
                db['photo_memory'].update_one({"_id": user_id}, {"$set": {"spam_count": spam_count}}, upsert=True)
                
                try: bot.delete_message(chat_id, message_id)
                except: pass

                if spam_count >= 3:
                    # 🔥 3 СТРАЙКА = МУТ НА 3 ДНЯ (259200 секунд) 🔥
                    mute_time = int(time.time()) + 259200
                    mute_user_everywhere(user_id, reason="Рецидив: Спам старыми фото (Анти-Баян)", admin_name="Скайнет 👁", mute_time=mute_time)
                    
                    # 🔥 ГЕНЕРИРУЕМ ЖЕСТКОЕ УНИЖЕНИЕ ЧЕРЕЗ ИИ (Оставляем на Groq, тут он хорош) 🔥
                    if GROQ_API_KEY:
                        photo_insult_styles = [
                            "Сделай акцент на том, что это его единственная удачная фотка за всю жизнь, и та сделана 10 лет назад на микроволновку.",
                            "Высмей его внешность или ракурс: скажи, что от этого зрелища у тебя сгорела пара нейронных связей и процессор просит пощады.",
                            "Используй метафоры из археологии: эта фотка старше динозавров, ее пора сдать в краеведческий музей.",
                            "Ответь в стиле токсичного фэшн-критика, который брезгливо разносит его убогий визуальный вкус и отправляет в бан переодеваться.",
                            "Ответь в стиле гопника, который популярно объясняет, что светить одним и тем же лицом каждый день — это жесткий кринж.",
                            "Пошути про то, что у него, видимо, закончилась память на телефоне, раз новых фоток не предвидится.",
                            "Сыграй пластического хирурга, который рекомендует ему сменить лицо, потому что это уже затерто до дыр.",
                            "Сыграй режиссера на съемочной площадке: «Стоп, снято! Отвратительный свет, ужасная игра лицом, пошел вон на 3 дня!»",
                            "Будь как священник-экзорцист: скажи, что изгоняешь демона плохих ракурсов и проклятых пикселей во тьму на 3 дня.",
                            "Сыграй эксперта по дейтингу: объясни, что раз на эту фотку никто не клюнул первые два раза, то в третий раз чуда точно не случится.",
                            "Обвини его в визуальном загрязнении чата: скажи, что ты экологическая полиция и отправляешь его на утилизацию.",
                            "Опиши ситуацию так, словно ты служба техподдержки: «Вы пробовали выключить и включить свое лицо? Оно зависло на одной картинке».",
                            "Выдай ответ в стиле полицейской ориентировки: «Разыскивается преступник за кражу пикселей, награда 0, отправлен за решетку»."
                        ]
                        random_photo_style = random.choice(photo_insult_styles)

                        prompt_text = (
                            f"Ты жестокий, токсичный и очень саркастичный ИИ-модератор.\n"
                            f"Пользователь {user_link} только что в 3-й раз подряд скинул в чат одну и ту же старую заезженную фотографию. За это ты выписал ему блокировку на 3 дня.\n"
                            f"Сгенерируй для него короткое публичное унижение (2-3 предложения), чтобы весь чат поржал.\n"
                            f"🔥 ВАЖНОЕ УСЛОВИЕ ДЛЯ ЭТОГО ОТВЕТА: {random_photo_style}\n"
                            f"Официально РАЗРЕШЕНО использовать жесткий мат, ругательства, черный юмор и ледяной сарказм. Обязательно вставь переменную {user_link} в текст."
                        )

                        data_text = {
                            "model": "llama-3.3-70b-versatile",
                            "messages": [{"role": "user", "content": prompt_text}],
                            "temperature": 0.8,
                            "max_tokens": 150
                        }
                        try:
                            resp_text = requests.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}, json=data_text, timeout=10)
                            if resp_text.status_code == 200:
                                insult = resp_text.json()["choices"][0]["message"]["content"].strip()
                                bot.send_message(chat_id, f"👁 **СКАЙНЕТ (Анти-Баян):**\n{insult}", parse_mode="Markdown")
                            else:
                                bot.send_message(chat_id, f"👁 **СКАЙНЕТ:** {user_link} доспамился своими ебучими баянами и улетел в мут на 3 дня. Отдыхай, креативный ты наш.", parse_mode="Markdown")
                        except:
                            bot.send_message(chat_id, f"👁 **СКАЙНЕТ:** {user_link} доспамился своими ебучими баянами и улетел в мут на 3 дня. Отдыхай, креативный ты наш.", parse_mode="Markdown")
                    else:
                        bot.send_message(chat_id, f"👁 **СКАЙНЕТ:** {user_link} доспамился своими ебучими баянами и улетел в мут на 3 дня. Отдыхай, креативный ты наш.", parse_mode="Markdown")
                    
                    # Сбрасываем счетчик после мута
                    db['photo_memory'].update_one({"_id": user_id}, {"$set": {"spam_count": 0}})
                    
                else:
                    # Предупреждение (1 или 2 раз) со случайными фразами и автоудалением через 5 минут
                    phrases = [
                        f"🥱 {user_link}, моя зрительная память подсказывает, что это ебучее фото мы уже видели. Смени ракурс! (Страйк {spam_count}/3)",
                        f"📸 {user_link}, Скайнет всё видит. Загрузка старых баянов запрещена, прояви фантазию! (Страйк {spam_count}/3)",
                        f"🤖 {user_link}, обнаружен дубликат изображения. Пиксель в пиксель. Сделай новое фото. (Страйк {spam_count}/3)",
                        f"👁 {user_link}, мои нейроны перегреваются от этих баянов. Кидай свежие кадры, а не из архива 2010 года! (Страйк {spam_count}/3)",
                        f"🖼 {user_link}, я сличил хеши. Эту картинку ты уже постил. У нас тут чат, а не музей антиквариата! (Страйк {spam_count}/3)",
                        f"🚨 {user_link}, моя база данных говорит, что этот ракурс уже заезжен до дыр. Жду новый контент. (Страйк {spam_count}/3)",
                        f"🥱 {user_link}, дежавю... Или ты опять скинул ту же самую фотку? Давай что-то свежее. (Страйк {spam_count}/3)",
                        f"🔎 {user_link}, алгоритмы распознавания образов не обманешь. За спам старыми фотками у нас наказывают. (Страйк {spam_count}/3)",
                        f"♻️ {user_link}, круговорот баянов в природе нужно остановить. Сделай новое фото, прояви уважение к чату! (Страйк {spam_count}/3)",
                        f"📸 {user_link}, у тебя что, память в телефоне закончилась? Хватит слать дубликаты! (Страйк {spam_count}/3)"
                    ]
                    warn_msg = bot.send_message(chat_id, random.choice(phrases), parse_mode="Markdown")
                    
                    def delete_photo_warn():
                        time.sleep(300)
                        try: bot.delete_message(chat_id, warn_msg.message_id)
                        except: pass
                    threading.Thread(target=delete_photo_warn, daemon=True).start()

        except Exception as e:
            print(f"Ошибка зрительной памяти: {e}")
    # 👆 ========================================== 👆

    # 👇 КОМАНДА-ШПИОН (Обрабатывается самой первой!) 👇
    @bot.message_handler(commands=['ping'])
    def ping_handler(message):
        bot.reply_to(message, f"👀 Я жив! ID этого чата: {message.chat.id}")

    # 👇 🤖 МОДУЛЬ: АВТО-АДМИН ПОДДЕРЖКИ + ЛОВЕЦ ЗВЕЗД 🤖 👇
    @bot.message_handler(func=lambda message: str(message.chat.id) == str(SUPPORT_GROUP_ID), content_types=['text', 'photo', 'video', 'document', 'audio', 'voice', 'sticker', 'animation', 'video_note', 'location', 'contact', 'successful_payment'])
    def auto_support_handler(message):
        
        # 1. ЛОВЕЦ ЗВЕЗД: В этой группе любое сообщение стоит 50 звезд. 
        # Сбрасываем страйки в базе
        if not message.from_user.is_bot:
            db['paid_users'].update_one(
                {"uid": message.from_user.id},
                {"$set": {
                    "status": 1,
                    "strikes": 0,
                    "timestamp": datetime.now()
                }},
                upsert=True
            )

        # 2. АВТО-АДМИН: Игнорируем сообщения от самих админов для авто-ответа
        if getattr(message, 'sender_chat', None) or message.from_user.id in [777000, 136817688, OWNER_ID]:
            return
            
        try:
            member = bot.get_chat_member(message.chat.id, message.from_user.id)
            if member.status in ['administrator', 'creator']:
                return
        except: pass

        # Берем текст или подпись к картинке
        text = (message.text or message.caption or "").lower()
        response = None

        # 3. База знаний Скайнета
        phrases_verification = [
            "Жду вас в боте @FAQMKBOT для прохождения верификации 🤝",
            "Здравствуйте! Проходите верификацию в боте @FAQMKBOT.",
            "Пишите в бот @FAQMKBOT, там проходит быстрая верификация.",
            "Для верификации перейдите в @FAQMKBOT и нажмите /start"
        ]
        
        phrases_restrictions = [
            "Здравствуйте. Пишите в бот @FAQMKBOT, проверим ваш статус.",
            "Если у вас ограничения, напишите в @FAQMKBOT, мы посмотрим причину.",
            "Все вопросы по мутам и блокировкам решаем через @FAQMKBOT. Напишите туда."
        ]

        # 4. Логика распознавания
        if any(word in text for word in ["верификаци", "вериф", "пройти"]):
            response = random.choice(phrases_verification)
        elif any(word in text for word in [
            "забанили", "мут", "не могу писать", "запрет", "ограничени", "блок",
            "разблок", "снять бан", "получил бан", "бан?", "оплатил", "звезд"
        ]):
            response = random.choice(phrases_restrictions)

        # 5. Имитация живого человека и отправка
        if response:
            bot.send_chat_action(message.chat.id, 'typing')
            time.sleep(1.5) 
            bot.reply_to(message, response)
    # 👆 ========================================= 👆

    # 🔴 Красная зона (Глобал бан)
    RED_WORDS = [
        r"\bфен\b",          
        r"\bмеф\b", 
        r"\bкристаллы\b", 
        r"\bсоли\b", 
        r"\bстафф\b", 
        r"\bцп\b", 
        r"\bдетское\b",
        r"\bмяу\b",          
        r"\bне\s*зож\b"      
    ]

    # 🟡 Желтая зона: Коммерция
    YELLOW_COMMERCE_REGEX = [
        r'\bмп\b', r'\bм\.п\b', r'\bмат\s*помощь\b', r'\bспонсор\b', 
        r'\bсодержу\b', r'\bкоммерция\b', r'\bвознаграждение\b', r'\bбабки\b',
        r'\bпапик[а-я]*\b',             
        r'\bтакси\s+с\s+тебя\b',        
        r'\bпрайс\b',                   
        r'\bгонорар[а-я]*\b',           
        r'\bапарт[ыа-я]*\b',            
        r'\bиндивидуалка[а-я]*\b',      
        r'\bуслуги\b',                  
        r'\bвстреч[аи]\s+за\b'          
    ]

    warned_users = {}  # Кэш отбивок подписок (chat_id, user_id) -> message_id

    # === 🚪 РАДАР НА ВХОДЕ В ЧАТ (УБИЙСТВО ДО ПЕРВОГО СООБЩЕНИЯ) 🚪 ===
    @bot.message_handler(content_types=['new_chat_members'])
    def face_control_on_entry(message):
        chat_id = message.chat.id
        
        # Игнорируем служебные чаты
        if str(chat_id) in [str(SUPPORT_GROUP_ID), str(STAFF_GROUP_ID), str(JOURNAL_CHAT_ID)]:
            return
            
        chat_title = escape_md(message.chat.title) if message.chat.title else f"Чат {chat_id}"

        for new_user in message.new_chat_members:
            if new_user.id == bot.get_me().id: continue # Игнорируем добавление самого бота
            
            user_id = new_user.id
            user_link = get_user_name(new_user)
            
            full_name = f"{message.from_user.first_name or ''} {message.from_user.last_name or ''}".lower()
            # 1. Вычищаем все знаки препинания и пробелы
            clean_name = re.sub(r'[\.\,\_\|\-\s+]', '', full_name)
        
            # 🔥 2. ДЕШИФРАТОР (Анти-замена букв) 🔥
            # Переводим английские буквы-шпионы обратно в русские
            homoglyphs = str.maketrans('aeopcxykmtbh', 'аеорсхукмтвн')
            clean_name = clean_name.translate(homoglyphs)
        
            name_triggers = [
                r"жмина", r"впрофил", r"смотрипрофиль", r"ссылкав", r"ссылкув", 
                r"децк", r"детск", r"дэти", r"деток", r"дети", r"малолет", r"школниц",
                r"цэпэ", r"цп\b", r"порно", r"поорно", r"ебут", r"трах", 
                r"каналв", r"переходив", r"меня", r"тме", r"tme", r"заработ", 
                r"инвест", r"крипт", r"профэл"
            ]
            
            if any(re.search(p, clean_name) for p in name_triggers):
                try: bot.delete_message(chat_id, message.message_id) # Удаляем плашку "Вступил в группу"
                except: pass
                # Мгновенный пермабан по всем базам!
                ban_user_everywhere(user_id, reason="Запрещенное/Рекламное ИМЯ на входе", admin_name="Скайнет 🚪", user_link=user_link, trigger_text=full_name, origin_chat=chat_title)
    # ===================================================================
   
    @bot.message_handler(content_types=['text', 'photo', 'video', 'document', 'audio', 'voice', 'sticker', 'animation', 'location', 'contact', 'video_note'], func=lambda message: message.chat.type in ['group', 'supergroup'])
    def skynet_core_handler(message):
        
        if getattr(message, 'sender_chat', None) or message.from_user.id in [777000, 136817688]:
            return

        chat_id = message.chat.id
        user_id = message.from_user.id
           
        raw_text = message.text or message.caption or ""
        
        # 👇 ИММУНИТЕТ ДЛЯ КАЗИНО (ЧТОБЫ ПЫЛЕСОС НЕ УДАЛЯЛ КОМАНДЫ) 👇
        if raw_text and raw_text.lower().startswith(('/spin', '/казино', '/рулетка')):
            return # Просто игнорируем это сообщение, Секретарь сам на него ответит!
        # 👆 ======================================================= 👆

        text = raw_text.lower()
        trigger_text = raw_text if raw_text else "Без текста (медиа)"
        user_link = get_user_name(message.from_user)
        chat_title = escape_md(message.chat.title) if message.chat.title else f"Чат {chat_id}"

        # === 🛑 ФЕЙС-КОНТРОЛЬ v2.0 (Бронебойный) 🛑 ===
        full_name = f"{message.from_user.first_name or ''} {message.from_user.last_name or ''}".lower()
        # Вычищаем все точки, запятые, слеши и пробелы, чтобы снять маскировку!
        clean_name = re.sub(r'[\.\,\_\|\-\s+]', '', full_name)
        
        name_triggers = [
            r"жмина", r"впрофил", r"смотрипрофиль", r"ссылкав", r"ссылкув", 
            r"децк", r"детск", r"дэти", r"цэпэ", r"цп\b", r"порно", r"поорно", 
            r"каналв", r"переходив", r"меня", r"тме", r"tme", r"заработ", 
            r"инвест", r"крипт", r"профэл"
        ]
        
        if any(re.search(p, clean_name) for p in name_triggers):
            try: bot.delete_message(chat_id, message.message_id)
            except: pass
            ban_user_everywhere(user_id, reason="Запрещенное/Рекламное ИМЯ профиля", admin_name="Скайнет 🛡", user_link=user_link, trigger_text=full_name, origin_chat=chat_title)
            return
        # ========================================================

        try:
            user_data = users_collection.find_one({"_id": user_id}) or {}
            # ... и дальше пошел твой код (is_vip, is_queer и т.д.)
            is_vip = user_data.get("is_vip", False)
            is_queer = user_data.get("is_queer", False)
            is_verified = user_data.get("is_verified", False)
            shame_tag = user_data.get("shame_tag")
            custom_tag = user_data.get("custom_tag")

            main_city = user_data.get("main_city")
            if not main_city:
                detected_city = None
                for city_name, networks in all_cities.items():
                    for net, groups in networks.items():
                        if any(g['chat_id'] == chat_id for g in groups):
                            detected_city = city_name
                            break
                    if detected_city: break
                
                if detected_city:
                    users_collection.update_one({"_id": user_id}, {"$set": {"main_city": detected_city}}, upsert=True)
                    main_city = detected_city

            sys_settings = db['settings'].find_one({"_id": "skynet"}) or {"quarantine_active": True, "may_1_active": True}

            
            # 👇 🛡️ УМНАЯ СИСТЕМА ТЕГОВ (СИНХРОНИЗАЦИЯ + РАЗДАЧА) 🛡️ 👇
            
            # 1. ГЛУБОКАЯ СИНХРОНИЗАЦИЯ (Раз в 10 минут или при первом сообщении)
            # Сначала узнаем, кто перед нами, чтобы не сбить ему корону!
            last_check = user_data.get("last_api_check", 0)
            if time.time() - last_check > 600:
                users_collection.update_one({"_id": user_id}, {"$set": {"last_api_check": time.time()}})
                
                try:
                    m_vip = bot.get_chat_member(VIP_CHAT_ID, user_id)
                    is_physically_there = getattr(m_vip, 'is_member', False) if m_vip.status == 'restricted' else True
                    actual_vip = m_vip.status in ['member', 'administrator', 'creator'] or (m_vip.status == 'restricted' and is_physically_there)
                    if is_vip != actual_vip:
                        is_vip = actual_vip
                        users_collection.update_one({"_id": user_id}, {"$set": {"is_vip": is_vip}}, upsert=True)
                except: pass

                try:
                    m_beyond = bot.get_chat_member(BEYOND_CHAT_ID, user_id)
                    is_physically_there_q = getattr(m_beyond, 'is_member', False) if m_beyond.status == 'restricted' else True
                    actual_queer = m_beyond.status in ['member', 'administrator', 'creator'] or (m_beyond.status == 'restricted' and is_physically_there_q)
                    if is_queer != actual_queer:
                        is_queer = actual_queer
                        users_collection.update_one({"_id": user_id}, {"$set": {"is_queer": is_queer}}, upsert=True)
                except: pass

                try:
                    member = bot.get_chat_member(chat_id, user_id)
                    current_tag = getattr(member, 'custom_title', None)
                    bot_tags = ["𝓟𝓡𝓔𝓜𝓘𝓤𝓜", "𝐐𝐔𝐄𝐄𝐑 ♛", "𝐑𝐄𝐀𝐋/𝐕𝐈𝐏♕", "Верифицирован МК", "Not verified", "РИСК/ВИРТ/ОБМЕН", "автососка", "туалетная соска", "Параметры FAKE", "Свободен", "Спонсор_Одобрен", "чернильница"]
                    
                    if current_tag:
                        if current_tag not in bot_tags:
                            users_collection.update_one({"_id": user_id}, {"$set": {"custom_tag": current_tag}}, upsert=True)
                            custom_tag = current_tag
                        elif current_tag == "Верифицирован МК":
                            is_verified = True
                            users_collection.update_one({"_id": user_id}, {"$set": {"is_verified": True}}, upsert=True)
                        elif current_tag == "Спонсор_Одобрен":
                            custom_tag = "Спонсор_Одобрен"
                            users_collection.update_one({"_id": user_id}, {"$set": {"custom_tag": "Спонсор_Одобрен"}}, upsert=True)
                except: pass

            # 2. Вычисляем, какой тег ДОЛЖЕН быть у юзера:
            target_tag = "Not verified"
            if custom_tag: target_tag = custom_tag
            elif is_vip and is_queer: target_tag = "𝓟𝓡𝓔𝓜𝓘𝓤𝓜"
            elif is_queer: target_tag = "𝐐𝐔𝐄𝐄𝐑 ♛"
            elif is_vip: target_tag = "𝐑𝐄𝐀𝐋/𝐕𝐈𝐏♕"
            elif is_verified: target_tag = "Верифицирован МК"
            elif shame_tag: target_tag = shame_tag

            # 3. МГНОВЕННАЯ РАЗДАЧА (Только если локальная память говорит, что тег отличается)
            if user_data.get(f"tag_{chat_id}") != target_tag:
                try: 
                    safe_set_tag(chat_id, user_id, target_tag)
                    users_collection.update_one({"_id": user_id}, {"$set": {f"tag_{chat_id}": target_tag}}, upsert=True)
                except: pass
            # 👆 ========================================================================= 👆

            # 👇 🛡️ ИММУНИТЕТ ДЛЯ ОДОБРЕННЫХ СПОНСОРОВ 🛡️ 👇
            if custom_tag == "Спонсор_Одобрен":
                return
            # 👆 ======================================================= 👆

            # === 👁 ЗРИТЕЛЬНАЯ ПАМЯТЬ СКАЙНЕТА (АНТИ-БАЯН) ===
            # Работает только в чате "БЕЗ ПРЕДРАССУДКОВ" при отправке фото
            target_chat_id = chat_ids_mk.get("БЕЗ ПРЕДРАССУДКОВ")
            
            if message.content_type == 'photo' and str(chat_id) == str(target_chat_id):
                threading.Thread(
                    target=check_photo_creativity_ai,
                    args=(bot, message.photo[-1].file_id, message.photo[-1].file_unique_id, user_id, chat_id, message.message_id, user_link)
                ).start()
            # ================================================

            # === 🤬 СЛОВАРЬ ИНКВИЗИТОРА (ТЯНЕМ ИЗ БАЗЫ) ===
            dict_settings = db['settings'].find_one({"_id": "skynet_dictionary"}) or {}
            live_red = RED_WORDS + [w['pattern'] for w in dict_settings.get('red', [])]
            live_yellow = YELLOW_COMMERCE_REGEX + [w['pattern'] for w in dict_settings.get('yellow', [])]
            # ===============================================

            if any(re.search(word, text) for word in live_red):
                bot.delete_message(chat_id, message.message_id)
                ban_user_everywhere(user_id, reason="Мясорубка: Красная зона", admin_name="Скайнет ⚔️", user_link=user_link, trigger_text=trigger_text, origin_chat=chat_title)
                return

            safe_minor = re.sub(r'\b(1[0-7])\s*(см|cm)\b', '', text)
            minor_patterns = [
                r'\b(мне|я)\s*(1[0-7])\b',                   
                r'\b(мне|я)\s*18\s*-\s*[1-9]\b',             
                r'\b(1[0-7]|18\s*-\s*[1-9])\s*(лет|годик)\b',
                r'\b(1[0-7])\s*[/\\-]\s*1\d{2}\b',           
                r'\b(200[9]|201[0-9])\s*(г\.р\.?|года?\s*рожд\w*)\b', # <--- ИСПРАВЛЕНО! (Только г.р. или год рождения)
                r'\bочень молод(ой|енький)\b'
            ]
            if any(re.search(p, safe_minor) for p in minor_patterns):
                bot.delete_message(chat_id, message.message_id)
                ban_user_everywhere(user_id, reason="Черная зона: Несовершеннолетний (<18)", admin_name="Скайнет 🔞", user_link=user_link, trigger_text=trigger_text, origin_chat=chat_title)
                return

            # 1. Сначала фильтруем коммерцию (для всех, даже для VIP/QUEER)
            clean_commerce = re.sub(r'без\s*м\.?п\.?|не\s*коммерция|без\s*мат(\.?|ериальной)\s*помощи', '', text)
            if any(re.search(pattern, clean_commerce) for pattern in live_yellow):
                bot.delete_message(chat_id, message.message_id)
                mute_user_everywhere(user_id, reason="Желтая зона: Коммерция", admin_name="Скайнет ⚔️", user_link=user_link, trigger_text=trigger_text, origin_chat=chat_title)
                return

            # =======================================================
            # 🛡 РАДАР ТВИНКОВ И ТЕКСТОВЫЙ АНТИ-БАЯН
            # (Работает везде КРОМЕ "ПАРНИ 18+". Элита и Админы имеют иммунитет, "Верифицирован МК" - НЕТ)
            # =======================================================
            is_elite = is_vip or is_queer or custom_tag in ["𝓟𝓡𝓔𝓜𝓘𝓤𝓜", "Спонсор_Одобрен", "Свободен"]
            is_admin = custom_tag and custom_tag not in ["𝓟𝓡𝓔𝓜𝓘𝓤𝓜", "𝐐𝐔𝐄𝐄𝐑 ♛", "𝐑𝐄𝐀𝐋/𝐕𝐈𝐏♕", "Верифицирован МК", "Not verified", "РИСК/ВИРТ/ОБМЕН", "автососка", "туалетная соска", "Параметры FAKE", "Свободен", "Спонсор_Одобрен"]

            if chat_id not in PARNI_CHATS and not (is_elite or is_admin or user_id in ADMIN_CHAT_IDS or user_id == OWNER_ID):
                if len(raw_text) > 30:
                    clean_current = re.sub(r'\s+', '', text)
                    
                    # 1. РАДАР ТВИНКОВ (ПРОКАЧАННЫЙ)
                    # Увеличили память Скайнета: теперь он помнит 100 последних забаненных текстов
                    recent_bans = list(db['blacklisted_texts'].find().sort("_id", -1).limit(150))
                    
                    for bad in recent_bans:
                        clean_bad = bad.get("clean_text", "")
                        if not clean_bad: continue
                        similarity = difflib.SequenceMatcher(None, clean_current, clean_bad).ratio()
                        
                        # 🔥 ДИНАМИЧЕСКАЯ ЧУВСТВИТЕЛЬНОСТЬ 🔥
                        # Если это карантинник (новорег), Скайнет бьет строже - при совпадении от 75%
                        is_newbie = user_id > 7800000000
                        threshold = 0.75 if is_newbie else 0.85 
                        
                        if similarity > threshold: 
                            try: bot.delete_message(chat_id, message.message_id) 
                            except: pass
                            
                            newbie_alert = "⚠️ **ЭТО НОВОРЕГ! Порог чувствительности был снижен до 75%**\n" if is_newbie else ""
                            
                            report = (
                                f"🚨 **РАДАР ТВИНКОВ СРАБОТАЛ!** 🚨\n"
                                f"{newbie_alert}"
                                f"Юзер {user_link} (`{user_id}`) отправил анкету, которая на **{int(similarity * 100)}%** совпадает с текстом нарушителя `{bad['uid']}`!\n\n"
                                f"📝 **Текст:** _{escape_md(raw_text[:200])}_\n\n"
                                f"🤖 **Действие:** Скайнет тихо удалил сообщение (Shadowban).\nВыдать ему глобальный БАН?"
                            )
                            markup = types.InlineKeyboardMarkup()
                            markup.add(types.InlineKeyboardButton("🔨 ЗАБАНИТЬ ВЕЗДЕ", callback_data=f"radar_ban_{user_id}"))
                            try: bot.send_message(STAFF_GROUP_ID, report, parse_mode="Markdown", reply_markup=markup)
                            except: pass
                            return # Выходим, чтобы текст не пошел дальше по фильтрам

                    # 2. ТЕКСТОВЫЙ АНТИ-БАЯН (ПОЧАТОВЫЙ)
                    text_memory_id = f"{user_id}_{chat_id}"
                    user_text_memory = db['text_memory'].find_one({"_id": text_memory_id}) or {}
                    recent_texts = user_text_memory.get("recent_texts", [])
                    text_spam_count = user_text_memory.get("spam_count", 0)
                    
                    is_text_duplicate = False
                    for old_text in recent_texts:
                        similarity = difflib.SequenceMatcher(None, clean_current, old_text).ratio()
                        if similarity > 0.85: 
                            is_text_duplicate = True
                            break
                    
                    if is_text_duplicate:
                        text_spam_count += 1
                        # 🔥 ИСПРАВЛЕНО ТУТ: Сохраняем страйк для конкретного чата
                        db['text_memory'].update_one({"_id": text_memory_id}, {"$set": {"spam_count": text_spam_count}}, upsert=True)
                        
                        try: bot.delete_message(chat_id, message.message_id)
                        except: pass
                        
                        if text_spam_count >= 3:
                            mute_time = int(time.time()) + 259200
                            # Мутим юзера на 3 дня
                            mute_user_everywhere(user_id, reason="Рецидив: Текстовый спам (Анти-Копипаст)", admin_name="Скайнет 📝", mute_time=mute_time)
                            
                            # 🔥 ГЕНЕРИРУЕМ ЖЕСТКОЕ УНИЖЕНИЕ ЧЕРЕЗ ИИ ДЛЯ КОПИПАСТЕРОВ 🔥
                            if GROQ_API_KEY:
                                text_insult_styles = [
                                    "Сделай акцент на его сломанных клавишах Ctrl+C и Ctrl+V.",
                                    "Опиши его как бракованного NPC или сбой в Матрице, который застрял в бесконечном цикле.",
                                    "Сделай акцент на том, что даже самый примитивный робот-пылесос умнее и креативнее, чем он.",
                                    "Поставь ему диагноз «Ошибка 404: Мозг не найден» и отправь на принудительную перезагрузку.",
                                    "Ответь в стиле надменного кибер-аристократа, брезгливо выкидывающего органический мусор.",
                                    "Ответь в стиле гопника с кибер-района, который популярно поясняет, почему спамить — это лютый зашквар.",
                                    "Сыграй роль уставшего психиатра, который ставит ему диагноз «хроническое скудоумие» и прописывает 3 дня изоляции.",
                                    "Будь как Гордон Рамзи от мира текстов: наори на него, что этот текст «сырой, пресный и абсолютно несъедобный».",
                                    "Сыграй строгую воспитательницу в детском саду, которая отчитывает малыша за то, что он ест клей вместо того, чтобы писать буквы.",
                                    "Ответь в стиле мафиозного Дона: «Ты приходишь в мой чат, но не проявляешь уважения, принося эти дешевые копипасты...»",
                                    "Используй метафоры из животного мира (он как тупой попугай, заевшая кукушка или дятел).",
                                    "Сравни его анкету с заевшей аудиокассетой или сломанным радио, которое пора выкинуть.",
                                    "Высмей его так, словно ты стендап-комик, который уничтожает тупого зрителя из зала.",
                                    "Пошути, что за 3 дня на параше у него будет время открыть букварь и выучить новые слова."
                                ]
                                random_style = random.choice(text_insult_styles)

                                prompt_text = (
                                    f"Ты жестокий, токсичный и очень саркастичный ИИ-модератор.\n"
                                    f"Пользователь {user_link} только что в 3-й раз подряд скопипастил в чат один и тот же шаблонный текст-анкету. За это ты выписал ему блокировку на 3 дня.\n"
                                    f"Сгенерируй для него короткое публичное унижение (2-3 предложения), чтобы весь чат поржал.\n"
                                    f"🔥 ВАЖНОЕ УСЛОВИЕ ДЛЯ ЭТОГО ОТВЕТА: {random_style}\n"
                                    f"Официально РАЗРЕШЕНО использовать жесткий мат, ругательства, черный юмор и ледяной сарказм. Обязательно вставь переменную {user_link} в текст."
                                )

                                data_text = {
                                    "model": "llama-3.3-70b-versatile",
                                    "messages": [{"role": "user", "content": prompt_text}],
                                    "temperature": 0.8,
                                    "max_tokens": 150
                                }
                                try:
                                    resp_text = requests.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}, json=data_text, timeout=10)
                                    if resp_text.status_code == 200:
                                        insult = resp_text.json()["choices"][0]["message"]["content"].strip()
                                        bot.send_message(chat_id, f"👁 **СКАЙНЕТ (Анти-Копипаст):**\n{insult}", parse_mode="Markdown", disable_web_page_preview=True)
                                    else:
                                        bot.send_message(chat_id, f"👁 **СКАЙНЕТ:** {user_link} доспамился своими копипастами и улетел в мут на 3 дня. Здесь чат для общения, а не доска объявлений. Научись креативить!", parse_mode="Markdown", disable_web_page_preview=True)
                                except:
                                    bot.send_message(chat_id, f"👁 **СКАЙНЕТ:** {user_link} доспамился своими копипастами и улетел в мут на 3 дня. Здесь чат для общения, а не доска объявлений. Научись креативить!", parse_mode="Markdown", disable_web_page_preview=True)
                            else:
                                bot.send_message(chat_id, f"👁 **СКАЙНЕТ:** {user_link} доспамился своими копипастами и улетел в мут на 3 дня. Здесь чат для общения, а не доска объявлений. Научись креативить!", parse_mode="Markdown", disable_web_page_preview=True)
                            
                            db['text_memory'].update_one({"_id": text_memory_id}, {"$set": {"spam_count": 0}})
                        else:
                            text_phrases = [
                                f"🥱 {user_link}, этот текст мы уже видели. Хватит копипастить одно и то же, прояви фантазию! (Страйк {text_spam_count}/3)",
                                f"🤖 {user_link}, обнаружен дубликат текста. Чат создан для общения, а не для Ctrl+C -> Ctrl+V. Перепиши анкету! (Страйк {text_spam_count}/3)",
                                f"📝 {user_link}, Скайнет засек копипаст. Публикация заготовленных шаблонов запрещена. (Страйк {text_spam_count}/3)",
                                f"♻️ {user_link}, у тебя заело кнопки копировать-вставить? Напиши что-то новое ручками, хватит спамить! (Страйк {text_spam_count}/3)",
                                f"🔎 {user_link}, индекс уникальности твоего текста пробил дно. Перестань публиковать одинаковые объявы. (Страйк {text_spam_count}/3)",
                                f"📜 {user_link}, мы не доска бесплатных объявлений на столбе. Попробуй поздороваться и пообщаться вживую! (Страйк {text_spam_count}/3)",
                                f"🚨 {user_link}, моя текстовая память отлично помнит эту пасту. Меняй текст, или скоро уйдешь в мут. (Страйк {text_spam_count}/3)",
                                f"🥱 {user_link}, опять эта заезженная анкета... Попробуй хотя бы слова местами поменять для приличия. (Страйк {text_spam_count}/3)",
                                f"⌨️ {user_link}, нейросети видят 100% плагиат твоего же прошлого сообщения. Мы тут за живое общение! (Страйк {text_spam_count}/3)",
                                f"🤖 {user_link}, Скайнет против бото-поведения. Хватит слать шаблоны по таймеру, включай мозг. (Страйк {text_spam_count}/3)"
                            ]
                            warn_msg = bot.send_message(chat_id, random.choice(text_phrases), parse_mode="Markdown")
                            
                            def delete_text_warn():
                                time.sleep(300)
                                try: bot.delete_message(chat_id, warn_msg.message_id)
                                except: pass
                            threading.Thread(target=delete_text_warn, daemon=True).start()
                        return # Прерываем, чтобы дальше по коду не шло
                    else:
                        # Текст уникальный -> сохраняем в память (запоминаем последние 10)
                        new_texts = [clean_current] + recent_texts[:9]
                        db['text_memory'].update_one(
                            {"_id": text_memory_id}, 
                            {"$set": {"recent_texts": new_texts, "spam_count": 0}}, 
                            upsert=True
                        )

            # 2. А ТОЛЬКО ПОТОМ разрешаем VIP/QUEER писать что угодно (кроме коммерции и копипаста)
            if any([is_vip, is_queer, is_verified, custom_tag]): return 
            if chat_id in PARNI_CHATS: return

            if not is_subscribed(user_id):
                try: bot.delete_message(chat_id, message.message_id)
                except: pass
                key = (chat_id, user_id)
                if key not in warned_users:
                    # === МАГИЯ ТВОЕГО КОНСТРУКТОРА (С ЦВЕТАМИ И ЭМОДЗИ) ===
                    markup = types.InlineKeyboardMarkup(row_width=1)
                    
                    db_buttons = db['settings'].find_one({"_id": "skynet_buttons"})
                    
                    if db_buttons and db_buttons.get("buttons"):
                        for btn in db_buttons["buttons"]:
                            # Базовые параметры (Текст и Ссылка)
                            kwargs = {"text": btn["text"], "url": btn["url"]}
                            
                            # Если выбран цвет (и это не дефолт)
                            if btn.get("style") and btn["style"] != "default":
                                kwargs["style"] = btn["style"]
                                
                            # Если указан ID кастомного эмодзи
                            if btn.get("emoji_id"):
                                kwargs["icon_custom_emoji_id"] = btn["emoji_id"]
                                
                            markup.add(types.InlineKeyboardButton(**kwargs))
                    else:
                        # Если база пустая (страховка)
                        markup.add(types.InlineKeyboardButton(text="Подписаться на МК", url="https://t.me/clubofrm"))
                    # ======================================================

                    sent = bot.send_message(chat_id, "❗ Внимание, чтобы писать в чате вам необходимо подписаться на наш основной канал.\n\nБез подписки на канал ваши сообщения будут удаляться автоматически. Вступая в чат, я подтверждаю совершеннолетие и обязуюсь соблюдать правила, с которыми ознакомлен и согласен.", reply_markup=markup)
                    warned_users[key] = sent.message_id
                    def auto_delete():
                        time.sleep(120)
                        try: bot.delete_message(chat_id, sent.message_id)
                        except: pass
                        if key in warned_users: del warned_users[key]
                    threading.Thread(target=auto_delete, daemon=True).start()
                return

            # === 🛡 ГЛУХОЙ КАРАНТИН НОВОРЕГОВ (HARD MUTE) ===
            if sys_settings.get("quarantine_active", True):
                first_seen = user_data.get('first_seen')
                if not first_seen:
                    first_seen = time.time()
                    users_collection.update_one({"_id": user_id}, {"$set": {"first_seen": first_seen}}, upsert=True)
                seconds_passed = time.time() - first_seen
                
                # Если это новорег и прошло меньше 120 часов (432000 сек)
                if user_id > 7800000000 and seconds_passed < 432000:
                    try: bot.delete_message(chat_id, message.message_id)
                    except: pass
                    
                    # 🤐 ФИЗИЧЕСКИЙ МУТ НА ОСТАТОК ВРЕМЕНИ 🤐
                    remaining_time = int(432000 - seconds_passed)
                    if remaining_time > 0:
                        try:
                            # Физически забираем права писать в этом конкретном чате!
                            bot.restrict_chat_member(
                                chat_id, 
                                user_id, 
                                until_date=int(time.time()) + remaining_time,
                                can_send_messages=False
                            )
                        except: pass

                    # 📨 ПОПЫТКА ОТПРАВИТЬ ИНСТРУКЦИЮ В ЛС
                    try:
                        bot.send_message(
                            user_id, 
                            "🚨 **Защита от спама (Карантин)!**\n\nВаш аккаунт создан недавно. Для безопасности сети действует карантин 120 часов.\nВаши сообщения в чате временно отключены.\n\n🛠 Чтобы снять ограничения досрочно, пройдите быструю верификацию в [Службе Поддержки](https://t.me/MK_MensClubSUPPORT).",
                            parse_mode="Markdown",
                            disable_web_page_preview=True
                        )
                    except:
                        # Если телега запретила писать в ЛС - кидаем "Фантомную" отбивку в чат
                        try:
                            safe_name = escape_md(message.from_user.first_name or "Пользователь")
                            ghost_msg = bot.send_message(
                                chat_id,
                                f"🚨 *{safe_name}*, сработала защита от спама!\nВаш аккаунт в карантине (120ч).\n🛠 Для досрочного снятия ограничений пройдите верификацию в [Службе Поддержки](https://t.me/MK_MensClubSUPPORT).",
                                parse_mode="Markdown",
                                disable_web_page_preview=True
                            )
                            # Удаляем через 15 секунд, чтобы не мусорить
                            def delete_ghost():
                                time.sleep(15)
                                try: bot.delete_message(chat_id, ghost_msg.message_id)
                                except: pass
                            threading.Thread(target=delete_ghost, daemon=True).start()
                        except: pass
                    
                    # Отправляем лог ТОЛЬКО админам
                    try: 
                        bot.send_message(
                            STAFF_GROUP_ID, 
                            f"🥷 **ГЛУХОЙ КАРАНТИН:** Новорег {user_link} (`{user_id}`) попытался проспамить.\n"
                            f"📍 Чат: {chat_title}\n"
                            f"🔒 Выдан системный мут на остаток карантина.",
                            parse_mode="Markdown",
                            disable_web_page_preview=True
                        )
                    except: pass
                    
                    return # Полностью прерываем обработку 

            # === 📏 ОПЕРАЦИЯ "1 МАЯ" ===
            if sys_settings.get("may_1_active", True):
                # СПАСИТЕЛЬНЫЙ ФИЛЬТР: ГДЕ НЕ НУЖНЫ ПАРАМЕТРЫ!
                EXCLUDED_FROM_PARAMS = set(PARNI_CHATS)
                EXCLUDED_FROM_PARAMS.update([VIP_CHAT_ID, BEYOND_CHAT_ID])
                EXCLUDED_FROM_PARAMS.update([chat_ids_mk.get("Фетиши"), chat_ids_mk.get("Мужской Чат"), chat_ids_mk.get("Секс Туризм"), chat_ids_mk.get("Аренда Жилья")])

                # 👇 ДОБАВИЛИ ПРОВЕРКУ: КРУЖКИ НЕ МУТИМ ЗА ОТСУТСТВИЕ ТЕКСТА 👇
                if chat_id not in EXCLUDED_FROM_PARAMS and message.content_type != 'video_note':
                    strict_match = re.search(r'(?<!\d)[1-9]\d/1\d{2}/\d{2,3}(?:/\d{1,2}(?:[.,*xхX]\d{1,2})?)?(?!\d)', text)
                    if not strict_match:
                        bot.delete_message(chat_id, message.message_id)
                        mute_user_everywhere(user_id, reason="Нет параметров или неверный формат (1 Мая)", admin_name="Скайнет 📏", user_link=user_link, trigger_text=trigger_text, origin_chat=chat_title)
                        markup = types.InlineKeyboardMarkup(row_width=1)
                        markup.add(
                            types.InlineKeyboardButton("🛠 Пройти верификацию", url="https://t.me/MK_MensClubSUPPORT"),
                            types.InlineKeyboardButton("😈 ПАРНИ 18+ (Без ограничений)", url="https://t.me/znakparni/116")
                        )
                        # 📝 ТЯНЕМ ТЕКСТ "1 МАЯ" ИЗ БАЗЫ
                        db_texts = db['settings'].find_one({"_id": "skynet_texts"}) or {}
                        raw_text_may1 = db_texts.get("may_1_warn", "🚨 {user_link}, **ВНИМАНИЕ!**\n\nС 1 мая введен СТРОГИЙ стандарт оформления анкет для досок объявлений.\nЛюбой текст **БЕЗ ПАРАМЕТРОВ** или с неправильным форматом запрещен!\nПараметры должны быть указаны **ТОЛЬКО через слеш (/) без пробелов и лишних слов**.\n\n✅ *Примеры:* `24/187/72` или `24/187/72/19` (допускается `19.5` или `19*4`)\n\nВаша анкета удалена, а вы временно ограничены в общении во всех группах сети.\n\n💡 *P.S. В нашей сети «ПАРНИ 18+» нет ограничений на формат текста и разрешен любой откровенный контент (включая порно). Переходи туда! 👇*")

                        warning_msg = bot.send_message(
                            chat_id, 
                            raw_text_may1.replace("{user_link}", user_link),
                            reply_markup=markup, parse_mode="Markdown", disable_web_page_preview=True
                        )
                        def delete_warning_may():
                            time.sleep(300)
                            try: bot.delete_message(chat_id, warning_msg.message_id)
                            except: pass
                        threading.Thread(target=delete_warning_may, daemon=True).start()
                        return

            safe_age = re.sub(r'(от|парня|мальчика|мужчину|ищу|для)\s*18\b|\b18\s*-\s*\d{2}\b|\b18\s*\+|\b18\s*(см|cm)\b', '', text)
            if re.search(r'\b18\s*(лет|год|годик|y\.?o\.?)\b|\b18\s*[/\\-]\s*1\d{2}\b|\b(мне|я)\s*18\b', safe_age):
                bot.delete_message(chat_id, message.message_id)
                mute_user_everywhere(user_id, reason="Оранжевая зона: 18 лет", admin_name="Скайнет 🔞", user_link=user_link, trigger_text=trigger_text, origin_chat=chat_title)
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("🛠 Пройти верификацию 🔞", url="https://t.me/FAQMKBOT"))
                # 📝 ТЯНЕМ ТЕКСТ 18+ ИЗ БАЗЫ
                db_texts = db['settings'].find_one({"_id": "skynet_texts"}) or {}
                raw_text_minor = db_texts.get("minor_warn", "🚨 {user_link}, **Внимание!**\nВаша анкета попала под автоматический фильтр безопасности сети. Пройдите обязательную верификацию 🔞.")
                
                warning_msg = bot.send_message(chat_id, raw_text_minor.replace("{user_link}", user_link), reply_markup=markup, parse_mode="Markdown", disable_web_page_preview=True)
                def delete_warning_18():
                    time.sleep(300)
                    try: bot.delete_message(chat_id, warning_msg.message_id)
                    except: pass
                threading.Thread(target=delete_warning_18, daemon=True).start()
                return

            new_tag = None
            age_match = re.search(r'\b(?:мне|я)\s*([1-9]\d)\b|\b([1-9]\d)\s*(?:лет|год|годик)\b|\b([1-9]\d)\s*[/\\-]\s*1\d{2}\b', text)
            if age_match:
                found_age = next((int(g) for g in age_match.groups() if g), None)
                if found_age and found_age >= 18: 
                    saved_age = user_data.get("saved_age")
                    if not saved_age: users_collection.update_one({"_id": user_id}, {"$set": {"saved_age": found_age}})
                    elif abs(saved_age - found_age) > 1: new_tag = "Параметры FAKE"

            if not new_tag:
                if "вирт" in text and "не вирт" not in text: new_tag = "РИСК/ВИРТ/ОБМЕН"
                elif any(re.search(fr'\b{word}\b', text) for word in ["вз", "обмен", "слить", "тц"]): new_tag = "туалетная соска" if "тц" in text else "РИСК/ВИРТ/ОБМЕН"
                elif any(word in text for word in ["дроч", "фотками"]): new_tag = "РИСК/ВИРТ/ОБМЕН"
                elif any(word in text for word in ["в машине", "на авто", "на заднем", "тачка", "в тачке"]): new_tag = "автососка"
                elif any(word in text for word in ["туалет", "кабинка", "в кабинке", "глори", "glory"]): new_tag = "туалетная соска"
                elif any(word in text for word in ["нерусск", "кавказ", "восточн", "узбек", "таджик", "дагестан", "чечен", "чурк"]): new_tag = "чернильница"

            if new_tag:
                try: 
                    safe_set_tag(chat_id, user_id, new_tag)
                    users_collection.update_one({"_id": user_id}, {"$set": {"shame_tag": new_tag}}, upsert=True)
                except: pass

        except Exception as e:
            print(f"Ошибка Единого Ядра в модуле Skynet: {e}")