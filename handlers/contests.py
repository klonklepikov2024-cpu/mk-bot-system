import os  # <--- ВОТ ЭТА СТРОЧКА РЕШИТ ПРОБЛЕМУ
import time
import html
import requests
import threading
import json
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from core.bot import bot
from database.mongo import db, paid_collection
from config import STAFF_GROUP_ID, chat_ids_mk, chat_ids_parni, chat_ids_ns, chat_ids_rainbow, chat_ids_gayznak, CONTESTS_THREAD_ID

# --- 1. ПРИЕМ РАБОТ ---
@bot.message_handler(commands=['contest', 'конкурс'])
def start_contest(message):
    uid = message.from_user.id
    
    # 🔥 Тянем активный конкурс из базы (тот самый, который мы утвердили)
    active = db['active_contest'].find_one({"_id": "current_event", "status": "running"})
    if not active:
        bot.send_message(uid, "😴 Пока что активных конкурсов нет! Скайнет готовит что-то грандиозное к следующему празднику.")
        return
        
    active_contest = active['contest_id']
    
    works_count = db['contests'].count_documents({"uid": uid, "contest_id": active_contest, "status": {"$ne": "rejected"}})
    if works_count >= 3:
        bot.send_message(uid, "❌ Вы уже отправили максимальное количество работ (3) на этот конкурс!")
        return
        
    title = active.get('title', 'Конкурс')
    desc = active.get('description', 'Пришлите ваше фото.')
        
    msg = bot.send_message(
        uid,
        f"🎉 <b>{title}</b>\n\n{desc}\n\n👇 <b>Отправьте ОДНО ФОТО вашей работы.</b>\n<i>Убедитесь, что фото загружено как картинка, а не файлом.</i>",
        parse_mode="HTML"
    )
    bot.register_next_step_handler(msg, process_contest_photo, active_contest)

# --- 2. МОДЕРАЦИЯ И ПУБЛИКАЦИЯ ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('cmod_'))
def handle_contest_moderation(call):
    if str(call.message.chat.id) != str(STAFF_GROUP_ID): return
    
    parts = call.data.split('_')
    action = parts[1]
    work_id = "_".join(parts[2:]) 
    
    work = db['contests'].find_one({"_id": work_id})
    if not work or work['status'] != 'pending':
        try: bot.answer_callback_query(call.id, "❌ Работа уже обработана!", show_alert=True)
        except: pass
        return
        
    uid = work['uid']
    safe_title = html.escape(work['title'])
    
    if action == "rej":
        db['contests'].update_one({"_id": work_id}, {"$set": {"status": "rejected"}})
        bot.edit_message_caption(f"{call.message.caption}\n\n❌ <b>ОТКЛОНЕНО</b>", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None, parse_mode="HTML")
        try: bot.send_message(uid, f"❌ Ваша конкурсная работа «{safe_title}» отклонена модератором.", parse_mode="HTML")
        except: pass
        return
        
    target_chat = None
    if action == "gal":
        target_chat = chat_ids_mk.get("Галерея")
        chat_name = "Галерею МК"
    elif action == "nsfw":
        target_chat = chat_ids_mk.get("БЕЗ ПРЕДРАССУДКОВ")
        chat_name = "чат Без предрассудков"
        
    if not target_chat:
        try: bot.answer_callback_query(call.id, f"❌ Ошибка! Чат '{chat_name}' не найден в базе ЦУПа!", show_alert=True)
        except: pass
        return
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("❤️ Отдать голос (0)", callback_data=f"cvote_{work_id}"))
    
    post_caption = f"🎃 <b>Конкурс: ХЭЛЛОУИН-2026</b>\n\n🏷 Название: «{safe_title}»\n\n👇 <i>Нажми на кнопку, чтобы отдать голос за этот образ!</i>"
    
    try:
        pub_msg = bot.send_photo(target_chat, work['photo_id'], caption=post_caption, reply_markup=markup, parse_mode="HTML")
        
        db['contests'].update_one({"_id": work_id}, {
            "$set": {
                "status": "published",
                "target_chat": target_chat,
                "message_id": pub_msg.message_id
            }
        })
        
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": 200, "immunity": 1, "jackpot_shards": 1}}, upsert=True)
        
        bot.edit_message_caption(f"{call.message.caption}\n\n✅ <b>ОПУБЛИКОВАНО в {chat_name}</b>", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None, parse_mode="HTML")
        try: bot.send_message(uid, f"🎉 <b>Ваша работа «{safe_title}» одобрена и опубликована в {chat_name}!</b>\n\nСкайнет начислил вам бонус за смелость: <b>200 💎, 1 🛡 Щит и 1 🧩 Осколок!</b>", parse_mode="HTML")
        except: pass
        
    except Exception as e:
        bot.send_message(STAFF_GROUP_ID, f"❌ Ошибка публикации: {e}. Бот точно админ в этом чате?")

# --- 3. АНОНИМНОЕ ГОЛОСОВАНИЕ ---
@bot.callback_query_handler(func=lambda call: call.data.startswith('cvote_'))
def handle_contest_vote(call):
    work_id = call.data[6:] 
    uid = call.from_user.id
    
    work = db['contests'].find_one({"_id": work_id})
    if not work:
        try: bot.answer_callback_query(call.id, "❌ Работа не найдена!", show_alert=True)
        except: pass
        return
        
    if uid in work.get('votes', []):
        try: bot.answer_callback_query(call.id, "Вы уже отдали свой голос за эту работу! ❤️", show_alert=True)
        except: pass
        return
        
    db['contests'].update_one({"_id": work_id}, {"$push": {"votes": uid}})
    new_count = len(work.get('votes', [])) + 1
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton(f"❤️ Отдать голос ({new_count})", callback_data=f"cvote_{work_id}"))
    
    try:
        bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
        bot.answer_callback_query(call.id, "Ваш голос учтен! ✨")
    except:
        try: bot.answer_callback_query(call.id, "Голос учтен, обновляю счетчик...")
        except: pass

# --- 4. ПОДВЕДЕНИЕ ИТОГОВ И РАЗДАЧА ПРИЗОВ ---
@bot.message_handler(commands=['end_contest'])
def end_contest_cmd(message):
    # Команду может писать только админ в служебной группе
    if str(message.chat.id) != str(STAFF_GROUP_ID): return
    
    parts = message.text.split()
    if len(parts) < 2:
        bot.send_message(message.chat.id, "❌ Укажите ID конкурса.\nПример: `/end_contest halloween_2026`", parse_mode="Markdown", message_thread_id=message.message_thread_id)
        return
        
    contest_id = parts[1]
    
    # Достаем все работы, которые были опубликованы
    works = list(db['contests'].find({"contest_id": contest_id, "status": "published"}))
    if not works:
        bot.send_message(message.chat.id, f"❌ Нет активных работ для конкурса `{contest_id}`.", parse_mode="Markdown", message_thread_id=message.message_thread_id)
        return
        
    # Считаем длину массива голосов для каждой работы
    for w in works:
        w['vote_count'] = len(w.get('votes', []))
        
    # Сортируем от большего к меньшему
    works.sort(key=lambda x: x['vote_count'], reverse=True)
    
    # Берем ТОП-3 (если участников меньше 3, Питон просто возьмет сколько есть)
    top_works = works[:3] 
    
    report = f"🏆 <b>ИТОГИ КОНКУРСА: {contest_id}</b> 🏆\n\n"
    
    for i, work in enumerate(top_works):
        place = i + 1
        uid = work['uid']
        safe_username = html.escape(work['username'])
        safe_title = html.escape(work['title'])
        votes = work['vote_count']
        
        # --- ВЫДАЧА ПРИЗОВ ПО МЕСТАМ ---
        if place == 1:
            u_info = db['users'].find_one({"_id": uid}) or {}
            has_vip = u_info.get("is_vip", False)
            has_beyond = u_info.get("is_queer", False) # is_queer — это флаг BEYOND в базе
            
            if has_beyond:
                # Максимальный статус уже есть -> Компенсируем деньгами (+1000₽ сверху)
                paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": 5000, "cashback_balance": 2500}}, upsert=True)
                prize_text = "5000 💎 + 2500 ₽ (Компенсация за макс. статус)"
            elif has_vip:
                # Есть VIP -> Апаем до BEYOND
                db['users'].update_one({"_id": uid}, {"$set": {"is_queer": True}}, upsert=True)
                paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": 5000, "cashback_balance": 1500}}, upsert=True)
                prize_text = "5000 💎 + Апгрейд до BEYOND + 1500 ₽"
            else:
                # Нет ничего -> Даем базовый VIP
                db['users'].update_one({"_id": uid}, {"$set": {"is_vip": True}}, upsert=True)
                paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": 5000, "cashback_balance": 1500}}, upsert=True)
                prize_text = "5000 💎 + Пожизненный VIP + 1500 ₽"
                
            medal = "🥇"
            
        elif place == 2:
            import random
            # Генерируем 3 Ордера на арест с ЗАЩИТОЙ от дубликатов
            for _ in range(3):
                while True:
                    code = f"ARREST-{random.randint(10000, 999999)}" # Увеличили диапазон
                    # Проверяем, есть ли уже такой код в базе
                    if not db['promocodes'].find_one({"_id": code}):
                        db['promocodes'].insert_one({"_id": code, "type": "artifact", "value": 0, "target": "mute", "usage_limit": 1, "used_count": 0, "is_active": True, "owner_uid": uid})
                        break # Выходим из цикла while, код уникален
            
            paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": 3000}}, upsert=True)
            prize_text = "3000 💎 + 3 Ордера на Арест"
            medal = "🥈"
            
        elif place == 3:
            paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": 1000, "immunity": 2}}, upsert=True)
            prize_text = "1000 💎 + 2 Щита Иммунитета"
            medal = "🥉"
            
        report += f"<b>{place} МЕСТО {medal}</b>\n👤 От: {safe_username} (<code>{uid}</code>)\n🏷 «{safe_title}»\n❤️ Голосов: <b>{votes}</b>\n🎁 Приз: <i>{prize_text}</i>\n\n"
        
        # Радуем победителя в ЛС
        try: bot.send_message(uid, f"🏆 <b>ПОЗДРАВЛЯЕМ!</b> 🏆\n\nВаш образ «{safe_title}» занял <b>{place} место</b> в конкурсе!\n\nВаша награда: <b>{prize_text}</b> успешно зачислена на ваш баланс Скайнета. Можете проверить в Кабинете!", parse_mode="HTML")
        except: pass
        
    # Меняем статус всем участникам, чтобы заморозить конкурс
    db['contests'].update_many({"contest_id": contest_id, "status": "published"}, {"$set": {"status": "completed"}})
    
    # Отправляем отчет тебе в админскую тему
    bot.send_message(STAFF_GROUP_ID, report, parse_mode="HTML", message_thread_id=message.message_thread_id)

# --- 5. ГЕНЕРАЦИЯ КОНКУРСА ЧЕРЕЗ ИИ (GEMINI) ---
@bot.message_handler(commands=['ai_contest'])
def generate_ai_contest(message):
    if str(message.chat.id) != str(STAFF_GROUP_ID): return
    
    theme = message.text.replace('/ai_contest', '').strip()
    if not theme:
        bot.send_message(message.chat.id, "❌ Укажите праздник или тему.\nПример: `/ai_contest Октоберфест`", parse_mode="Markdown", message_thread_id=message.message_thread_id)
        return

    msg = bot.send_message(message.chat.id, f"🧠 <i>Скайнет переключает мощности на Gemini и придумывает концепт для «{theme}»...</i>", parse_mode="HTML", message_thread_id=message.message_thread_id)

    # Запускаем тяжелую задачу в фоне, чтобы Telegram не паниковал
    threading.Thread(target=process_ai_contest_task, args=(message.chat.id, msg.message_id, theme)).start()

def process_ai_contest_task(chat_id, msg_id, theme):
    gemini_key = os.getenv("GEMINI_API_KEY")
    if not gemini_key:
        bot.edit_message_text("❌ Ошибка: GEMINI_API_KEY не найден в переменных окружения!", chat_id, msg_id)
        return

    # 🔥 ПРОМПТ ПРОКАЧАН: Теперь требуем правила и готовый пост-анонс
    system_prompt = """Ты креативный директор мужского Telegram-сообщества. 
    Твоя задача — придумать тематический фотоконкурс. 
    Верни СТРОГО валидный JSON без маркдауна и лишнего текста (без ```json).
    Формат ответа:
    {
      "contest_id": "уникальный_id_на_английском",
      "title": "Яркое название с эмодзи",
      "description": "Короткое системное описание для меню бота.",
      "announcement_text": "ПОЛНЫЙ текст поста-анонса для рассылки по чатам (в HTML тегах <b> и <i>). Обязательно пропиши тут правила: 1. Что нужно сфоткать. 2. Никаких чужих фото. 3. Для участия перейдите в личку бота и отправьте команду /contest.",
      "prizes": {
         "1": {"text": "5000 💎 + VIP + 1500 ₽"},
         "2": {"text": "3000 💎 + 3 Ордера на Арест"},
         "3": {"text": "1000 💎 + 2 Щита Иммунитета"}
      }
    }"""
    
    models_queue = ["gemini-3.7-flash", "gemini-3.6-flash"]
    ai_data = None
    last_error = ""

    for model_name in models_queue:
        url = f"[https://generativelanguage.googleapis.com/v1beta/models/](https://generativelanguage.googleapis.com/v1beta/models/){model_name}:generateContent?key={gemini_key}"
        for attempt in range(3):
            try:
                payload = {
                    "systemInstruction": {"parts": [{"text": system_prompt}]},
                    "contents": [{"parts": [{"text": f"Сгенерируй конкурс на тему: {theme}"}]}],
                    "generationConfig": {"temperature": 0.8, "responseMimeType": "application/json"}
                }
                res = requests.post(url, headers={"Content-Type": "application/json"}, json=payload, timeout=30)
                if res.status_code == 200:
                    content = res.json()["candidates"][0]["content"]["parts"][0]["text"]
                    try:
                        ai_data = json.loads(content)
                        break 
                    except json.JSONDecodeError:
                        last_error = "Невалидный JSON от модели"
                elif res.status_code in [503, 429]:
                    last_error = f"Код {res.status_code}. Ждем..."
                    time.sleep(5)
                else:
                    last_error = f"Код {res.status_code}"
                    break 
            except Exception as e:
                last_error = str(e)
                time.sleep(3)
        if ai_data: break

    if not ai_data:
        try: bot.edit_message_text(f"❌ Ошибка генерации: {last_error}", chat_id, msg_id)
        except: pass
        return

    try:
        ai_data['status'] = 'draft' 
        db['active_contest'].update_one({"_id": "current_event"}, {"$set": ai_data}, upsert=True)
        
        text = f"💡 <b>ИДЕЯ ОТ СКАЙНЕТА (Gemini)</b>\n\n"
        text += f"🏷 <b>Название:</b> {ai_data.get('title', 'Без названия')}\n"
        text += f"🆔 <b>ID:</b> <code>{ai_data.get('contest_id', 'unknown')}</code>\n\n"
        text += f"📢 <b>Текст анонса для рассылки:</b>\n{ai_data.get('announcement_text', '')}\n\n"
        
        prizes = ai_data.get('prizes', {})
        text += f"🎁 <b>Призы:</b>\n1. {prizes.get('1', {}).get('text', '')}\n2. {prizes.get('2', {}).get('text', '')}\n3. {prizes.get('3', {}).get('text', '')}"
        
        markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ Запустить и РАЗОСЛАТЬ анонс", callback_data="ai_start_contest"))
        bot.edit_message_text(text, chat_id, msg_id, reply_markup=markup, parse_mode="HTML")
    except Exception as e:
        try: bot.edit_message_text(f"❌ Ошибка вывода интерфейса: {e}", chat_id, msg_id)
        except: pass

# --- Активация черновика и КОВРОВАЯ БОМБАРДИРОВКА ---
@bot.callback_query_handler(func=lambda call: call.data == 'ai_start_contest')
def handle_ai_start(call):
    if str(call.message.chat.id) != str(STAFF_GROUP_ID): return
    
    active = db['active_contest'].find_one({"_id": "current_event", "status": "draft"})
    if not active:
        bot.answer_callback_query(call.id, "Конкурс уже запущен или не найден!", show_alert=True)
        return
        
    # Меняем статус в базе
    db['active_contest'].update_one({"_id": "current_event"}, {"$set": {"status": "running"}})
    bot.edit_message_text(f"{call.message.text}\n\n🚀 <b>СТАТУС: ЗАПУЩЕН! Начинаю рассылку анонса по всем чатам...</b>", call.message.chat.id, call.message.message_id, parse_mode="HTML")
    
    # Собираем все чаты из матрицы ЦУПа
    all_target_chats = []
    all_target_chats.extend(chat_ids_mk.values())
    all_target_chats.extend(chat_ids_parni.values())
    all_target_chats.extend(chat_ids_ns.values())
    all_target_chats.extend(chat_ids_gayznak.values())
    all_target_chats.extend(chat_ids_rainbow.values())
    
    unique_chats = set(all_target_chats)
    
    # Формируем пост (добавляем призы к анонсу)
    prizes = active.get('prizes', {})
    prize_block = f"\n\n🎁 <b>ПРИЗОВОЙ ФОНД:</b>\n🥇 1 место: {prizes.get('1', {}).get('text', '')}\n🥈 2 место: {prizes.get('2', {}).get('text', '')}\n🥉 3 место: {prizes.get('3', {}).get('text', '')}"
    
    announcement = active.get('announcement_text', "У нас новый конкурс!") + prize_block
    
    # Фоновая рассылка, чтобы бот не завис
    def broadcast_announcement():
        success_count = 0
        for chat_id in unique_chats:
            try:
                bot.send_message(chat_id, announcement, parse_mode="HTML")
                success_count += 1
                time.sleep(0.3) # Пауза против лимитов ТГ
            except: pass
            
        bot.send_message(
            STAFF_GROUP_ID, 
            f"📢 <b>Анонс конкурса «{active.get('title')}» успешно разослан в {success_count} чатов!</b>", 
            message_thread_id=call.message.message_thread_id, 
            parse_mode="HTML"
        )
        
    import threading
    threading.Thread(target=broadcast_announcement, daemon=True).start()

# --- 6. РУЧНАЯ КОРРЕКТИРОВКА ПРИЗОВ В ЧЕРНОВИКЕ ---
@bot.message_handler(commands=['set_prize'])
def edit_draft_prize(message):
    if str(message.chat.id) != str(STAFF_GROUP_ID): return
    
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        bot.send_message(message.chat.id, "❌ Формат: `/set_prize [место 1-3] [новый текст приза]`\nПример: `/set_prize 1 10000 💎 + Супер-VIP`", parse_mode="Markdown")
        return
        
    place = parts[1]
    new_prize = parts[2]
    
    active = db['active_contest'].find_one({"_id": "current_event", "status": "draft"})
    if not active:
        bot.send_message(message.chat.id, "❌ Нет активного черновика! Сначала сгенерируйте конкурс через `/ai_contest` или создайте вручную.")
        return
        
    # Точечно обновляем конкретный приз в базе
    db['active_contest'].update_one({"_id": "current_event"}, {"$set": {f"prizes.{place}.text": new_prize}})
    bot.send_message(message.chat.id, f"✅ <b>Приз за {place} место успешно изменен на:</b>\n{new_prize}\n\n<i>Теперь можете нажать кнопку запуска под карточкой конкурса!</i>", parse_mode="HTML")

# --- 7. РУЧНОЕ СОЗДАНИЕ КОНКУРСА (БЕЗ ИИ) ---
@bot.message_handler(commands=['manual_contest'])
def manual_contest_start(message):
    if str(message.chat.id) != str(STAFF_GROUP_ID): return
    msg = bot.send_message(message.chat.id, "🛠 <b>Ручное создание конкурса</b>\n\nВведите уникальный ID (на английском, без пробелов, например: `summer_2027`):", parse_mode="HTML")
    bot.register_next_step_handler(msg, process_manual_id)

def process_manual_id(message):
    contest_id = message.text.strip()
    msg = bot.send_message(message.chat.id, "Отлично. Теперь введите <b>Название конкурса</b> (с эмодзи):", parse_mode="HTML")
    bot.register_next_step_handler(msg, process_manual_title, contest_id)

def process_manual_title(message, contest_id):
    title = message.text.strip()
    msg = bot.send_message(message.chat.id, "Теперь отправьте <b>Текст анонса и правила</b> (этот текст уйдет в рассылку по всем чатам):", parse_mode="HTML")
    bot.register_next_step_handler(msg, process_manual_desc, contest_id, title)

def process_manual_desc(message, contest_id, title):
    desc = message.text.strip()
    
    # Создаем базовый черновик со стандартными призами
    draft_data = {
        "contest_id": contest_id,
        "title": title,
        "description": desc,
        "announcement_text": desc,
        "status": "draft",
        "prizes": {
             "1": {"text": "5000 💎 + VIP + 1500 ₽"},
             "2": {"text": "3000 💎 + 3 Ордера на Арест"},
             "3": {"text": "1000 💎 + 2 Щита Иммунитета"}
        }
    }
    db['active_contest'].update_one({"_id": "current_event"}, {"$set": draft_data}, upsert=True)
    
    text = f"💡 <b>РУЧНОЙ ЧЕРНОВИК СОЗДАН</b>\n\n🏷 <b>Название:</b> {title}\n🆔 <b>ID:</b> <code>{contest_id}</code>\n\n📢 <b>Анонс:</b>\n{desc}\n\n"
    text += "🎁 <b>Призы по умолчанию:</b>\n1. 5000 💎 + VIP + 1500 ₽\n2. 3000 💎 + 3 Ордера на Арест\n3. 1000 💎 + 2 Щита Иммунитета\n\n"
    text += "<i>Если хотите изменить призы, используйте команду `/set_prize 1 Новый приз`. Если всё ок — жмите запуск!</i>"
    
    # Та самая кнопка, которая запустит рассылку!
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ Запустить и РАЗОСЛАТЬ анонс", callback_data="ai_start_contest"))
    bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML")