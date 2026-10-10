import time
import re
import difflib
import threading
import base64
import imagehash
from PIL import Image
import io
import requests
from datetime import datetime
import pytz
import random
from telebot import types
import telebot
from core.cfg import cfg  # пороги и ссылки — в панели «🎛 Управление»

from config import (
    OWNER_ID, ADMIN_CHAT_IDS, VIP_CHAT_ID, BEYOND_CHAT_ID, PARNI_CHATS,
    all_cities, STAFF_GROUP_ID, SUPPORT_GROUP_ID, JOURNAL_CHAT_ID,
    chat_ids_mk, chat_ids_parni, chat_ids_ns,
    chat_ids_rainbow, chat_ids_gayznak, MAIN_CHANNEL_LINK,
    GROQ_API_KEY, GROQ_API_KEYS, HF_TOKEN, OPENROUTER_KEY  # <--- Добавили новые токены
)
from database import users_collection, banned_collection, db, archive_collection
from utils import escape_md, get_user_name
from core.guards import classify_profile_name, safe_delete
from core.janitor import schedule_delete
from core.settings import SkynetSettings


def register_skynet_handlers(bot, ban_user_everywhere, mute_user_everywhere, safe_set_tag, add_radar_log, is_subscribed, entry_hooks=None):
    entry_hooks = entry_hooks if entry_hooks is not None else {}

    def _name_alert(user, user_link, chat_title, marker):
        """Подозрительное, но не однозначное имя: не баним автоматически, а зовём админов (раз в сутки)."""
        key = f"name_alert_{user.id}"
        if db['name_alerts'].find_one({"_id": key, "ts": {"$gt": time.time() - 86400}}):
            return
        db['name_alerts'].update_one({"_id": key}, {"$set": {"ts": time.time()}}, upsert=True)
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔨 ЗАБАНИТЬ ВЕЗДЕ", callback_data=f"radar_ban_{user.id}"))
        full = escape_md(f"{user.first_name or ''} {user.last_name or ''}".strip())
        try:
            bot.send_message(STAFF_GROUP_ID, f"🟡 **Подозрительное имя** {user_link} (`{user.id}`): _{full}_\nМаркер: `{marker}` · Чат: {chat_title}\nАвтобан не применён — решите вручную.", parse_mode="Markdown", reply_markup=markup)
        except Exception:
            pass

    

    def _send_roast(chat_id, user_link, prompt_text, fallback_text, title):
        """Прожарка после мута. Раньше ответ ИИ отправлялся с parse_mode=Markdown: любая звёздочка или
        подчёркивание в ответе ломали разметку, отправка падала, и в чат уходил запасной текст —
        поэтому в чатах сотни одинаковых «доспамился…». Теперь: ротация ключей, HTML, автоудаление."""
        import html as _html
        m = re.match(r"\[(.*?)\]\((.*?)\)", user_link or "")
        name, url = (m.group(1).replace("\\", ""), m.group(2)) if m else ("Пользователь", "")
        mention = f'<a href="{_html.escape(url)}">{_html.escape(name)}</a>' if url else _html.escape(name)
        prompt = prompt_text.replace(user_link, "{USER}")
        from core.ai import groq_chat
        text = groq_chat(prompt, max_tokens=700, temperature=0.8, where="прожарка")
        if text and any(w in text.lower() for w in ["извините", "не могу", "как ии", "языковая модель", "запрограммирован", "оскорбительн", "токсичн", "цензур"]):
            text = None
        if text:
            body = _html.escape(text.replace("**", "").replace("__", ""))
            body = body.replace("{USER}", mention) if "{USER}" in body else f"{mention}, {body}"
        else:
            body = _html.escape(fallback_text).replace("{USER}", mention)
        try:
            sent = bot.send_message(chat_id, f"👁 <b>СКАЙНЕТ ({title}):</b>\n{body}", parse_mode="HTML", disable_web_page_preview=True)
            schedule_delete(chat_id, sent.message_id)  # прожарка висит cleanup_minutes (по умолчанию 10 мин)
        except Exception as e:
            print(f"roast send error: {e}")

    # (Удалена неиспользуемая get_vision_description: её нигде не вызывали; анти-баян работает по хешам картинок.)


    # 👇 ФУНКЦИЯ ЗРИТЕЛЬНОЙ ПАМЯТИ (АНТИ-БАЯН) 👇
    def check_photo_creativity_ai(bot, file_id, file_unique_id, user_id, chat_id, message_id, user_link):
        # Анти-баян сравнивает хеши картинок и ИИ не требует (ИИ нужен только для прожарки)

        # 🔥 ДОБАВЛЯЕМ ВОТ ЭТОТ БЛОК 🔥
        mod_limits = db['settings'].find_one({"_id": "moderation_limits"}) or {}
        
        # ЕСЛИ ТУМБЛЕР ВЫКЛЮЧЕН - ПРОСТО ВЫХОДИМ
        if not mod_limits.get("antibayan_photo_active", True):
            return 
            
        sim_normal = mod_limits.get("sim_normal", 85) / 100.0
        strike_mute_sec = mod_limits.get("strike_hours", 72) * 3600
        # ===============================

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
                # 🔥 2. ПОДКЛЮЧАЕМ PERCEPTUAL HASHING (Математический Анти-Баян) 🔥
                file_info = bot.get_file(file_id)
                # Лимит можно смело увеличить до 5МБ, так как это не ест трафик API
                if file_info.file_size > 5000000: return 
                
                downloaded_file = bot.download_file(file_info.file_path)
                
                try:
                    # Превращаем картинку в визуальный хеш-код (например, 'c3a529e71b31a8')
                    img = Image.open(io.BytesIO(downloaded_file))
                    current_hash = str(imagehash.phash(img))
                except Exception as e:
                    print(f"Ошибка хеширования картинки: {e}")
                    return
                
                if current_hash:
                    for old_hash in recent_hashes:
                        try:
                            # Математика: находим разницу между двумя картинками (Hamming distance)
                            # Разница 0 - идеальная копия. До 8 - слегка обрезанная или сжатая копия.
                            diff = imagehash.hex_to_hash(current_hash) - imagehash.hex_to_hash(old_hash)
                            if diff <= cfg("photo_similarity"): 
                                is_duplicate = True
                                break
                        except:
                            # Этот except нужен, чтобы проигнорировать старые текстовые хеши 
                            # от прошлой ИИ-версии, которые еще остались в базе данных
                            continue
                    
                    if not is_duplicate:
                        # Уникальное фото - сохраняем в память!
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

                if spam_count >= cfg("repost_strikes"):
                    # 🔥 3 СТРАЙКА = МУТ НА 3 ДНЯ (259200 секунд) 🔥
                    mute_time = int(time.time()) + strike_mute_sec
                    muted = mute_user_everywhere(user_id, reason="Рецидив: Спам старыми фото (Анти-Баян)", admin_name="Скайнет 👁", mute_time=mute_time)
                    
                    # Прожарка только если мут реально выдан (щит или спонсорский иммунитет его отменяют)
                    if muted:
                        photo_insult_styles = [
                            "Сделай акцент на том, что это его единственная удачная фотка за всю жизнь, и та сделана 10 лет назад на микроволновку.",
                            "Высмей его внешность или ракурс: скажи, что от этого зрелища у тебя сгорела пара нейронных связей и процессор просит пощады.",
                            "Используй метафоры из археологии: эта фотка старше динозавров, ее пора сдать в краеведческий музей.",
                            "Ответь в стиле токсичного фэшн-критика, который брезгливо разносит его убогий визуальный вкус и отправляет в бан переодеваться.",
                            "Ответь в стиле гопника, который популярно объясняет, что светить одним и тем же лицом каждый день — это жесткий кринж.",
                            "Пошути про то, что у него, видимо, закончилась память на телефоне, раз новых фоток не предвидится.",
                            "Сыграй пластического хирурга, который рекомендует ему сменить лицо, потому что это уже затерто до дыр.",
                            "Сыграй режиссера на съемочной площадке: «Стоп, снято! Отвратительный свет, ужасная игра лицом, пошел вон на " + _mute_len(strike_mute_sec) + "!»",
                            "Будь как священник-экзорцист: скажи, что изгоняешь демона плохих ракурсов и проклятых пикселей во тьму на " + _mute_len(strike_mute_sec) + ".",
                            "Сыграй эксперта по дейтингу: объясни, что раз на эту фотку никто не клюнул первые два раза, то в третий раз чуда точно не случится.",
                            "Обвини его в визуальном загрязнении чата: скажи, что ты экологическая полиция и отправляешь его на утилизацию.",
                            "Опиши ситуацию так, словно ты служба техподдержки: «Вы пробовали выключить и включить свое лицо? Оно зависло на одной картинке».",
                            "Выдай ответ в стиле полицейской ориентировки: «Разыскивается преступник за кражу пикселей, награда 0, отправлен за решетку»."
                        ]
                        random_photo_style = random.choice(photo_insult_styles)
                        prompt_text = (
                            f"Сыграй роль циничного, саркастичного и дерзкого ИИ-модератора (stand-up comedy roast).\n"
                            f"Пользователь {user_link} только что в {cfg('repost_strikes')}-й раз подряд скинул одну и ту же старую заезженную фотографию. За это ты выписал ему блокировку на {_mute_len(strike_mute_sec)}.\n"
                            f"Сгенерируй для него короткую комедийную прожарку (2-3 предложения), чтобы чат посмеялся над его ленью.\n"
                            f"🔥 ВАЖНОЕ УСЛОВИЕ ДЛЯ ЭТОГО ОТВЕТА: {random_photo_style}\n"
                            f"Это комедийная сатира. Разрешен черный юмор и грубый сарказм. Обязательно вставь переменную {user_link} в текст. Никаких извинений!"
                        )
                        _send_roast(chat_id, user_link, prompt_text,
                                    "{USER} доспамился своими баянами и улетел в мут на " + _mute_len(strike_mute_sec) + ". Отдыхай, креативный ты наш.", "Анти-Баян")
                    
                    # Сбрасываем счетчик после мута
                    db['photo_memory'].update_one({"_id": user_id}, {"$set": {"spam_count": 0}})
                    
                else:
                    # Предупреждение (1 или 2 раз) со случайными фразами и автоудалением через 5 минут
                    phrases = [
                        f"🥱 {user_link}, моя зрительная память подсказывает, что это ебучее фото мы уже видели. Смени ракурс! (Страйк {spam_count}/{cfg('repost_strikes')})",
                        f"📸 {user_link}, Скайнет всё видит. Загрузка старых баянов запрещена, прояви фантазию! (Страйк {spam_count}/{cfg('repost_strikes')})",
                        f"🤖 {user_link}, обнаружен дубликат изображения. Пиксель в пиксель. Сделай новое фото. (Страйк {spam_count}/{cfg('repost_strikes')})",
                        f"👁 {user_link}, мои нейроны перегреваются от этих баянов. Кидай свежие кадры, а не из архива 2010 года! (Страйк {spam_count}/{cfg('repost_strikes')})",
                        f"🖼 {user_link}, я сличил хеши. Эту картинку ты уже постил. У нас тут чат, а не музей антиквариата! (Страйк {spam_count}/{cfg('repost_strikes')})",
                        f"🚨 {user_link}, моя база данных говорит, что этот ракурс уже заезжен до дыр. Жду новый контент. (Страйк {spam_count}/{cfg('repost_strikes')})",
                        f"🥱 {user_link}, дежавю... Или ты опять скинул ту же самую фотку? Давай что-то свежее. (Страйк {spam_count}/{cfg('repost_strikes')})",
                        f"🔎 {user_link}, алгоритмы распознавания образов не обманешь. За спам старыми фотками у нас наказывают. (Страйк {spam_count}/{cfg('repost_strikes')})",
                        f"♻️ {user_link}, круговорот баянов в природе нужно остановить. Сделай новое фото, прояви уважение к чату! (Страйк {spam_count}/{cfg('repost_strikes')})",
                        f"📸 {user_link}, у тебя что, память в телефоне закончилась? Хватит слать дубликаты! (Страйк {spam_count}/{cfg('repost_strikes')})"
                    ]
                    warn_msg = bot.send_message(chat_id, random.choice(phrases), parse_mode="Markdown", disable_web_page_preview=True)
                    
                    schedule_delete(chat_id, warn_msg.message_id, 300)

        except Exception as e:
            print(f"Ошибка зрительной памяти: {e}")
    # 👆 ========================================== 👆

# ================= OSINT: ДОСЬЕ ДЛЯ ЭЛИТЫ (СТЕЛС-РЕЖИМ) =================
    @bot.message_handler(commands=['check', 'досье'])
    @bot.message_handler(func=lambda m: m.text and m.text.lower().startswith(('.check', '!check', 'досье')))
    def osint_check_handler(message):
        # 🔥 ПРЕДОХРАНИТЕЛЬ ОТ АНОНИМНЫХ АДМИНОВ 🔥
        if getattr(message, 'sender_chat', None):
            try: bot.send_message(message.chat.id, "❌ **Ошибка:** Вы пишете от имени группы (Анонимно). Переключитесь на свой личный профиль, чтобы Скайнет увидел ваш VIP-статус!")
            except: pass
            return

        uid = message.from_user.id
        
        # 1. Проверяем права
        user_data = users_collection.find_one({"_id": uid}) or {}
        is_admin = uid in ADMIN_CHAT_IDS or uid == OWNER_ID
        is_elite = user_data.get("is_vip") or user_data.get("is_queer") or user_data.get("custom_tag") == "Спонсор_Одобрен"
        
        if not (is_admin or is_elite):
            if message.chat.type == "private":
                bot.send_message(message.chat.id, "❌ **Отказано в доступе.**\nБаза данных Скайнета засекречена.", parse_mode="Markdown")
            return
            
        target_uid = None
        display_target = "Неизвестно"
        target_input = ""
        clean_name = ""
        with_at = ""
        
        # 🔥 МЕТОД 1: ПРОБИВ ПО РЕПЛАЮ 🔥
        if message.reply_to_message:
            if message.reply_to_message.forward_from:
                target_uid = message.reply_to_message.forward_from.id
            else:
                target_uid = message.reply_to_message.from_user.id
            display_target = str(target_uid)
            
        # 🔥 МЕТОД 2: ПОИСК ПО ТЕКСТУ 🔥
        else:
            args = message.text.split()
            if len(args) < 2:
                error_msg = (
                    "📋 **Служба Безопасности: Пробив по базе**\n\n"
                    "Используйте команду: `/check @username` или `/check ID`\n\n"
                    "💡 *СЕКРЕТНЫЙ ЛАЙФХАК:* Просто **ответьте (reply)** командой `!check` на сообщение подозреваемого в чате! Это работает на 100%."
                )
                try:
                    bot.send_message(uid, error_msg, parse_mode="Markdown")
                    if message.chat.type != "private":
                        try: bot.delete_message(message.chat.id, message.message_id)
                        except: pass
                except:
                    bot.send_message(message.chat.id, f"❌ {message.from_user.first_name}, напишите мне в личку /start, чтобы получать досье.")
                return
                
            target_input = args[1].replace("https://", "").replace("http://", "").replace("t.me/", "").replace("@", "").replace("/", "").strip()
            
            if target_input.isdigit():
                target_uid = int(target_input)
                display_target = str(target_uid)
            else:
                clean_name = target_input.lower()
                with_at = f"@{clean_name}"
                display_target = with_at
                
                try:
                    chat_info = bot.get_chat(with_at)
                    target_uid = chat_info.id
                except Exception:
                    regex_clean = {"$regex": f"^{clean_name}$", "$options": "i"}
                    regex_with_at = {"$regex": f"^{with_at}$", "$options": "i"}
                    
                    search_query = {"$or": [
                        {"username": regex_clean},
                        {"username": regex_with_at}
                    ]}
                    
                    doc = db['support_tickets'].find_one(search_query)
                    if not doc: doc = db['paid_users'].find_one(search_query)
                    if not doc: doc = users_collection.find_one(search_query)
                    if not doc: doc = banned_collection.find_one(search_query)
                    
                    if doc: 
                        target_uid = doc.get("uid") or doc.get("user_id") or doc.get("_id")
            
        # 3. Собираем данные со всей базы
        t_info = users_collection.find_one({"_id": target_uid}) if target_uid else None
        b_info = banned_collection.find_one({"_id": target_uid}) if target_uid else None
        
        # 🔥 СУПЕР-РАДАР ДЛЯ АРХИВА (Игнорируем пустые папки-дубликаты) 🔥
        archive_query = []
        if target_uid:
            archive_query.extend([
                {"target": str(target_uid)},
                {"target": target_uid},
                {"uid": target_uid},
                {"_id": target_uid}
            ])
            
        if clean_name and with_at:
            archive_query.extend([
                {"target": {"$regex": f"^{clean_name}$", "$options": "i"}},
                {"target": {"$regex": f"^{with_at}$", "$options": "i"}},
                {"target": {"$regex": f"{clean_name}", "$options": "i"}}
            ])
            
        archive_info = None
        if archive_query:
            archive_info = archive_collection.find_one({
                "$or": archive_query,
                "history.0": {"$exists": True}
            })
            if not archive_info:
                archive_info = archive_collection.find_one({"$or": archive_query})

        if not t_info and not b_info and not archive_info:
            empty_report = f"🗄 **База данных Скайнета:**\nПользователь `{display_target}` не найден. История абсолютно чиста."
            try:
                bot.send_message(uid, empty_report, parse_mode="Markdown")
                if message.chat.type != "private":
                    try: bot.delete_message(message.chat.id, message.message_id)
                    except: pass
            except:
                bot.send_message(message.chat.id, f"❌ {message.from_user.first_name}, напишите мне в личку /start, чтобы получать досье.")
            return
                      
        # 4. Формируем красивое досье
        status = "🔴 ЗАБАНЕН (ЧС)" if b_info else "🟢 ЧИСТ / АКТИВЕН"
        city = escape_md(t_info.get("main_city", "Не привязан")) if t_info else "Неизвестно"
        tag = escape_md(t_info.get("custom_tag", "Отсутствует")) if t_info else "Отсутствует"
        
        history_text = "_История нарушений пуста._\n"
        if archive_info and archive_info.get("history"):
            history_text = ""
            for entry in archive_info["history"][-5:]:
                date = escape_md(entry.get('date', ''))
                action = escape_md(entry.get('action', ''))
                reason = escape_md(entry.get('reason', ''))
                history_text += f"▪️ **{date}** | {action}\n_Причина: {reason}_\n\n"
                
        report = (
            f"📁 **ОФИЦИАЛЬНОЕ ДОСЬЕ**\n\n"
            f"**Цель:** `{display_target}`\n"
            f"**Статус в сети:** {status}\n"
            f"**Город:** {city}\n"
            f"**Особые отметки:** {tag}\n\n"
            f"📜 **ВЫПИСКА ИЗ АРХИВА:**\n"
            f"{history_text}"
            f"👁‍🗨 _Сгенерировано по запросу VIP-резидента._"
        )
        
        # 5. ОТПРАВЛЯЕМ В ЛИЧКУ И ЗАЧИЩАЕМ ЧАТ
        try:
            bot.send_message(uid, report, parse_mode="Markdown")
            if message.chat.type != "private":
                try: bot.delete_message(message.chat.id, message.message_id)
                except: pass
                notif = bot.send_message(message.chat.id, f"🕵️‍♂️ Секретное досье отправлено вам в личные сообщения.")
                def delete_notif():
                    import time
                    time.sleep(5)
                    try: bot.delete_message(message.chat.id, notif.message_id)
                    except: pass
                import threading
                threading.Thread(target=delete_notif, daemon=True).start()
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Я не могу отправить досье. Напишите мне в личку /start. Ошибка: {e}")
        
        try: add_radar_log(f"🔎 VIP {uid} пробил досье на {display_target}")
        except: pass
    # ==========================================================

    # 👇 УМНАЯ ПРОВЕРКА КОНТЕКСТА ЧЕРЕЗ ИИ 👇
    def ai_context_checker(text, zone="black"):
        """True — нарушение, False — безопасно, None — ИИ недоступен.
        Раньше при сбое ИИ функция возвращала True, и все, кого задел фильтр, наказывались без проверки."""
        from core.ai import ai_verdict
        return ai_verdict(text, zone)

    def _mute_len(sec):
        """72 ч -> «3 дня», 6 ч -> «6 ч» — для текстов о муте (срок задаётся в настройках таймеров)."""
        h = int(sec // 3600)
        if h % 24 == 0 and h >= 24:
            d = h // 24
            return f"{d} " + ("день" if d % 10 == 1 and d % 100 != 11 else "дня" if 2 <= d % 10 <= 4 and not 12 <= d % 100 <= 14 else "дней")
        return f"{h} ч"

    def ai_down_alert(user_link, user_id, chat_title, text, zone_name, zone="black"):
        """ИИ не ответил: не наказываем вслепую, а зовём админов. Кнопки — с настоящей причиной зоны
        (раньше была одна кнопка бана с причиной «Клон забаненной анкеты»)."""
        from core.ai_review import create_review, ZONE_REASONS
        action, reason = ZONE_REASONS.get(zone, ("ban", zone_name))
        try:
            mk = create_review(user_id, action, reason, trigger_text=text, origin_chat=chat_title, user_link=user_link)
            bot.send_message(STAFF_GROUP_ID, f"🤖 **ИИ-проверка недоступна** ({zone_name})\n{user_link} (`{user_id}`) · {escape_md(str(chat_title))}\n"
                             f"Положено: {'бан' if action == 'ban' else 'мут'} — {escape_md(reason)}\n_{escape_md(str(text)[:300])}_\n"
                             f"Автоматика ничего не сделала — решите вручную.", parse_mode="Markdown", reply_markup=mk)
        except Exception as e:
            from core.diag import log_error
            log_error("ИИ-недоступен: алерт", e, user_id)
    # 👆 ========================================= 👆

    # 👇 КОМАНДА-ШПИОН (Обрабатывается самой первой!) 👇
    @bot.message_handler(commands=['ping'])
    def ping_handler(message):
        bot.reply_to(message, f"👀 Я жив! ID этого чата: {message.chat.id}")

    # 👇 🤖 МОДУЛЬ: АВТО-АДМИН ПОДДЕРЖКИ + ЛОВЕЦ ЗВЕЗД 🤖 👇
    @bot.message_handler(func=lambda message: str(message.chat.id) == str(SUPPORT_GROUP_ID), content_types=['text', 'photo', 'video', 'document', 'audio', 'voice', 'sticker', 'animation', 'video_note', 'location', 'contact', 'successful_payment'])
    def auto_support_handler(message):
        
        # 1. ЛОВЕЦ ЗВЕЗД: В этой группе любое сообщение стоит 60 звезд. 
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

        # 4. Логика распознавания (Словарь Дегенерата)
        if any(word in text for word in [
            "вериф", "вереф", "вириф", "верификация", "верефекация", "верификиция", 
            "верфикация", "вирификация", "пройти"
        ]):
            response = random.choice(phrases_verification)
        elif any(word in text for word in [
            "забанили", "забанил", "мут", "не могу писать", "запрет", "ограничени", "блок",
            "разблок", "снять бан", "получил бан", "бан?", "оплатил", "звезд", "штраф",
            "теневой", "снять", "снимите", "помогите", "розбан", "разблокировка", "разблокируй",
            "чс", "уберите", "недоступна", "ограничили", "не приняли"
        ]):
            response = random.choice(phrases_restrictions)

        # 5. Имитация живого человека и отправка
        if response:
            response = response.replace("@FAQMKBOT", "@" + cfg("support_bot"))
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

        # Перехватчик забаненных / восстановление тегов / амнистия ПАРНИ из app.py.
        # Раньше он был отдельным хэндлером и не срабатывал никогда (telebot запускает только первый).
        hook = entry_hooks.get("entry")
        if hook:
            try: hook(message)
            except Exception as e: print(f"entry hook error: {e}")

        for new_user in message.new_chat_members:
            if new_user.is_bot: continue # Ботов (и самого Скайнета) не проверяем
            
            user_id = new_user.id
            if banned_collection.find_one({"_id": user_id}):
                continue  # уже обработан перехватчиком выше
            user_link = get_user_name(new_user)

            # Раньше проверялось имя message.from_user (того, КТО добавил), а не вошедшего.
            # И триггеры искались по склеенным имени+фамилии: «Марат Мельников» давал «тме»,
            # «Семён Яковлев» — «меня», «Гордецкий» — «децк», и человек получал вечный бан.
            verdict, marker = classify_profile_name(new_user.first_name, new_user.last_name) if SkynetSettings.get().get("skynet_enabled", True) else (None, None)
            full_name = f"{new_user.first_name or ''} {new_user.last_name or ''}".lower()
            if verdict == "ban":
                safe_delete(bot, chat_id, message.message_id) # Удаляем плашку "Вступил в группу"
                # Мгновенный пермабан по всем базам!
                ban_user_everywhere(user_id, reason="Запрещенное/Рекламное ИМЯ на входе", admin_name="Скайнет 🚪", user_link=user_link, trigger_text=full_name, origin_chat=chat_title)
            elif verdict == "alert":
                _name_alert(new_user, user_link, chat_title, marker)

        # 🧹 Плашки «вступил в группу» (тумблер «Чистка служебных сообщений»)
        if SkynetSettings.get().get("clean_service_messages", False):
            safe_delete(bot, chat_id, message.message_id)

    @bot.message_handler(content_types=['left_chat_member'], func=lambda m: m.chat.type in ['group', 'supergroup'])
    def clean_left_plaque(message):
        if str(message.chat.id) in [str(SUPPORT_GROUP_ID), str(STAFF_GROUP_ID), str(JOURNAL_CHAT_ID)]:
            return
        if SkynetSettings.get().get("clean_service_messages", False):
            safe_delete(bot, message.chat.id, message.message_id)
    # ===================================================================
   
    @bot.message_handler(content_types=['text', 'photo', 'video', 'document', 'audio', 'voice', 'sticker', 'animation', 'location', 'contact', 'video_note'], func=lambda message: message.chat.type in ['group', 'supergroup'])
    def skynet_core_handler(message):
        
        # 👇 НОВЫЕ ДВЕ СТРОЧКИ ДЛЯ ЧТЕНИЯ БАЗЫ НА ЛЕТУ 👇
        from config import get_network_data
        chat_ids_mk, chat_ids_parni, chat_ids_ns, chat_ids_rainbow, chat_ids_gayznak, PARNI_CHATS, all_cities, MAIN_CHANNEL_LINK = get_network_data()
        # 👆 ========================================= 👆

        if getattr(message, 'sender_chat', None) or message.from_user.id in [777000, 136817688]:
            return

        chat_id = message.chat.id
        user_id = message.from_user.id

        # 1. Тянем актуальные настройки из базы "на лету"
        mod_limits = db['settings'].find_one({"_id": "moderation_limits"}) or {}
        
        # 2. Распаковываем ползунки Радара (делим на 100, чтобы получить 0.85 из 85%)
        sim_normal = mod_limits.get("sim_normal", 85) / 100.0
        sim_newbie = mod_limits.get("sim_newbie", 75) / 100.0
        
        # 3. Распаковываем таймеры (умножаем часы на 3600, чтобы получить секунды для Телеграма)
        strike_mute_sec = mod_limits.get("strike_hours", 72) * 3600
        flood_norm_sec = mod_limits.get("flood_norm_hours", 6) * 3600
        flood_hard_sec = mod_limits.get("flood_hard_hours", 120) * 3600
        quarantine_sec = mod_limits.get("quaran_hours", 120) * 3600
        
        # 👇 НОВЫЕ ТУМБЛЕРЫ ИЗ ВЕБКИ 👇
        radar_active = mod_limits.get("radar_active", True)
        antibayan_text_active = mod_limits.get("antibayan_text_active", True)
        antiflood_active = mod_limits.get("antiflood_active", True)
           
        raw_text = message.text or message.caption or ""
        
        # 👇 ИММУНИТЕТ ДЛЯ КАЗИНО (ЧТОБЫ ПЫЛЕСОС НЕ УДАЛЯЛ КОМАНДЫ) 👇
        if raw_text and raw_text.lower().startswith(('/spin', '/казино', '/рулетка')):
            return # Просто игнорируем это сообщение, Секретарь сам на него ответит!
        # 👆 ======================================================= 👆

        # 🔥 ТРЕКЕР ДЛЯ ДЕРЕВЯННОГО КЕЙСА (АКТИВНОСТЬ В ЧАТАХ) 🔥
        if raw_text and len(raw_text.split()) >= 3: # Считаем только фразы от 3 слов
            today_str = datetime.now(pytz.timezone('Asia/Yekaterinburg')).strftime("%Y-%m-%d")
            db['tasks_progress'].update_one(
                {"uid": user_id, "date": today_str}, 
                {"$inc": {"messages": 1}}, 
                upsert=True
            )
        # 👆 ======================================================= 👆

        text = raw_text.lower()
        trigger_text = raw_text if raw_text else "Без текста (медиа)"
        user_link = get_user_name(message.from_user)
        chat_title = escape_md(message.chat.title) if message.chat.title else f"Чат {chat_id}"

        # 🔥 ЖУЧОК ДЛЯ ЮЗЕРНЕЙМОВ (Запоминаем ники для пробива) 🔥
        if message.from_user.username:
            users_collection.update_one({"_id": user_id}, {"$set": {"username": f"@{message.from_user.username}".lower()}}, upsert=True)

        # Тумблеры из веб-панели. Раньше 9 из 14 переключателей ни на что не влияли.
        sk = SkynetSettings.get()
        moderation_on = sk.get("skynet_enabled", True)

        # === 🛑 ФЕЙС-КОНТРОЛЬ v2.0 (без ложных банов на стыке имени и фамилии) 🛑 ===
        full_name = f"{message.from_user.first_name or ''} {message.from_user.last_name or ''}".lower()
        verdict, marker = classify_profile_name(message.from_user.first_name, message.from_user.last_name) if moderation_on else (None, None)
        if verdict == "ban":
            safe_delete(bot, chat_id, message.message_id)
            ban_user_everywhere(user_id, reason="Запрещенное/Рекламное ИМЯ профиля", admin_name="Скайнет 🛡", user_link=user_link, trigger_text=full_name, origin_chat=chat_title)
            return
        elif verdict == "alert":
            _name_alert(message.from_user, user_link, chat_title, marker)
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
                
                # 👇 БРОНЯ ИНДУЛЬГЕНЦИИ: Их мы не проверяем на физическое присутствие! 👇
                if True:  # Индульгенция больше не даёт VIP/QUEER — синхронизируем по факту членства для всех
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
                # 👆 ========================================================================= 👆

                try:
                    member = bot.get_chat_member(chat_id, user_id)
                    current_tag = getattr(member, 'custom_title', None)
                    bot_tags = ["𝓟𝓡𝓔𝓜𝓘𝓤𝓜", "𝐐𝐔𝐄𝐄𝐑 ♛", "𝐑𝐄𝐀𝐋/𝐕𝐈𝐏♕", "Верифицирован МК", "Not verified", "РИСК/ВИРТ/ОБМЕН", "автососка", "туалетная соска", "Параметры FAKE", "Свободен", "Спонсор_Одобрен", "чернильница"]
                    
                    if current_tag:
                        # 🔥 ИСПРАВЛЕНИЕ: Защита Веб-панели и команды /tag 🔥
                        last_known_tag = user_data.get(f"tag_{chat_id}")
                        
                        # Если тег в Телеге НЕ системный, и он ОТЛИЧАЕТСЯ от того, что Скайнет вешал в прошлый раз:
                        # Значит, админ реально поменял его руками прямо в настройках чата! Синхронизируем в базу.
                        if current_tag not in bot_tags and current_tag != last_known_tag:
                            users_collection.update_one({"_id": user_id}, {"$set": {"custom_tag": current_tag}}, upsert=True)
                            custom_tag = current_tag
                            
                        # Если же это системный тег, просто подтверждаем права
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

            # 3. МГНОВЕННАЯ РАЗДАЧА
            # 🔥 ИСПРАВЛЕНИЕ: Проверяем, не сбился ли реальный тег в чате
            actual_tag = locals().get('current_tag')
            
            # Раздаем, если тег отличается в локальной базе ИЛИ если глубокая проверка выявила рассинхрон
            if user_data.get(f"tag_{chat_id}") != target_tag or (actual_tag is not None and actual_tag != target_tag):
                try: 
                    safe_set_tag(chat_id, user_id, target_tag)
                    # Записываем в базу ТОЛЬКО если не было ошибок (Смотри app.py)
                    users_collection.update_one({"_id": user_id}, {"$set": {f"tag_{chat_id}": target_tag}}, upsert=True)
                except Exception as e:
                    pass # Молча глотаем ошибку API. База не обновится, и Скайнет попробует снова на следующем сообщении!

            # 🔴 Главный рубильник: теги синхронизируем всегда, а модерацию — только если включена
            if not moderation_on:
                return

            # 👇 🛡️ ИММУНИТЕТ ДЛЯ АДМИНОВ И СЛУЖЕБНЫХ ЧАТОВ 🛡️ 👇
            # Скайнет не должен модерировать STAFF-чат, Поддержку и Журнал!
            if str(chat_id) in [str(SUPPORT_GROUP_ID), str(STAFF_GROUP_ID), str(JOURNAL_CHAT_ID)]:
                return

            # Владелец и Админы бессмертны абсолютно во всех чатах сети
            if user_id == OWNER_ID or user_id in ADMIN_CHAT_IDS:
                return
            # 👆 ======================================================= 👆

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

            if sk.get("red_zone", True) and any(re.search(word, text) for word in live_red):
                safe_delete(bot, chat_id, message.message_id)
                ban_user_everywhere(user_id, reason="Мясорубка: Красная зона", admin_name="Скайнет ⚔️", user_link=user_link, trigger_text=trigger_text, origin_chat=chat_title)
                return

            safe_minor = re.sub(r'\b(1[0-7])\s*(см|cm)\b', '', text)
            minor_patterns = [
                r'\b(мне|я)\s*(1[0-7])\b',                   
                r'\b(мне|я)\s*18\s*-\s*[1-9]\b',             
                r'\b(1[0-7]|18\s*-\s*[1-9])\s*(лет|годик)\b',
                r'\b(1[0-7])\s*[/\\-]\s*1\d{2}\b',           
                r'\b(200[9]|201[0-9])\s*(г\.р\.?|года?\s*рожд\w*)\b',
                r'\bочень молод(ой|енький)\b'
            ]
            if any(re.search(p, safe_minor) for p in minor_patterns):
                # 🔥 ПОДКЛЮЧАЕМ ИИ-АНАЛИТИКУ ПЕРЕД БАНОМ 🔥
                verdict_black = ai_context_checker(raw_text, zone="black")
                if verdict_black is None:
                    safe_delete(bot, chat_id, message.message_id)  # прячем сообщение, но не баним без проверки
                    ai_down_alert(user_link, user_id, chat_title, raw_text, "чёрная зона <18", zone="black")
                    return
                if verdict_black:
                    safe_delete(bot, chat_id, message.message_id)
                    ban_user_everywhere(user_id, reason="Черная зона: Несовершеннолетний (<18)", admin_name="Скайнет 🔞", user_link=user_link, trigger_text=trigger_text, origin_chat=chat_title)
                    return

            # 1. Сначала фильтруем коммерцию (для всех, даже для VIP/QUEER)
            clean_commerce = re.sub(r'без\s*м\.?п\.?|не\s*коммерция|без\s*мат(\.?|ериальной)\s*помощи', '', text)
            if sk.get("yellow_commerce", True) and any(re.search(pattern, clean_commerce) for pattern in live_yellow):
                
                # 🔥 ПОДКЛЮЧАЕМ ИИ-АНАЛИТИКУ ПЕРЕД МУТОМ 🔥
                verdict_yellow = ai_context_checker(raw_text, zone="yellow")
                if verdict_yellow is None:
                    ai_down_alert(user_link, user_id, chat_title, raw_text, "жёлтая зона", zone="yellow")
                if verdict_yellow:
                    safe_delete(bot, chat_id, message.message_id)
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
                    recent_bans = list(db['blacklisted_texts'].find().sort("_id", -1).limit(150)) if (radar_active and sk.get("twin_radar", True)) else []
                    
                    for bad in recent_bans:
                        # 👇 ДОБАВЛЯЕМ ПРЕДОХРАНИТЕЛЬ ОТ САМОГО СЕБЯ 👇
                        if bad.get('uid') == user_id:
                            continue # Пропускаем свою же старую анкету
                        # 👆 ========================================== 👆

                        clean_bad = bad.get("clean_text", "")
                        if not clean_bad: continue
                        similarity = difflib.SequenceMatcher(None, clean_current, clean_bad).ratio()
                        
                        is_newbie = user_id > 7800000000
                        threshold = sim_newbie if is_newbie else sim_normal 
                        
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
                            return 

                    # 2. ТЕКСТОВЫЙ АНТИ-БАЯН (ПОЧАТОВЫЙ)
                    text_memory_id = f"{user_id}_{chat_id}"
                    user_text_memory = db['text_memory'].find_one({"_id": text_memory_id}) or {}
                    recent_texts = user_text_memory.get("recent_texts", []) if antibayan_text_active else []
                    text_spam_count = user_text_memory.get("spam_count", 0)
                    
                    is_text_duplicate = False
                    for old_text in recent_texts:
                        similarity = difflib.SequenceMatcher(None, clean_current, old_text).ratio()
                        if similarity > sim_normal: 
                            is_text_duplicate = True
                            break
                    
                    if is_text_duplicate:
                        text_spam_count += 1
                        db['text_memory'].update_one({"_id": text_memory_id}, {"$set": {"spam_count": text_spam_count}}, upsert=True)
                        
                        try: bot.delete_message(chat_id, message.message_id)
                        except: pass
                        
                        if text_spam_count >= cfg("repost_strikes"):
                            mute_time = int(time.time()) + strike_mute_sec
                            muted = mute_user_everywhere(user_id, reason="Рецидив: Текстовый спам (Анти-Копипаст)", admin_name="Скайнет 📝", mute_time=mute_time)
                            
                            if muted:
                                text_insult_styles = [
                                    "Сделай акцент на его сломанных клавишах Ctrl+C и Ctrl+V.",
                                    "Опиши его как бракованного NPC или сбой в Матрице, который застрял в бесконечном цикле.",
                                    "Сделай акцент на том, что даже самый примитивный робот-пылесос умнее и креативнее, чем он.",
                                    "Поставь ему диагноз «Ошибка 404: Мозг не найден» и отправь на принудительную перезагрузку.",
                                    "Ответь в стиле надменного кибер-аристократа, брезгливо выкидывающего органический мусор.",
                                    "Ответь в стиле гопника с кибер-района, который популярно поясняет, почему спамить — это лютый зашквар.",
                                    "Сыграй роль уставшего психиатра, который ставит ему диагноз «хроническое скудоумие» и прописывает " + _mute_len(strike_mute_sec) + " изоляции.",
                                    "Будь как Гордон Рамзи от мира текстов: наори на него, что этот текст «сырой, пресный и абсолютно несъедобный».",
                                    "Сыграй строгую воспитательницу в детском саду, которая отчитывает малыша за то, что он ест клей вместо того, чтобы писать буквы.",
                                    "Ответь в стиле мафиозного Дона: «Ты приходишь в мой чат, но не проявляешь уважения, принося эти дешевые копипасты...»",
                                    "Используй метафоры из животного мира (он как тупой попугай, заевшая кукушка или дятел).",
                                    "Сравни его анкету с заевшей аудиокассетой или сломанным радио, которое пора выкинуть.",
                                    "Высмей его так, словно ты стендап-комик, который уничтожает тупого зрителя из зала.",
                                    "Пошути, что за " + _mute_len(strike_mute_sec) + " на параше у него будет время открыть букварь и выучить новые слова."
                                ]
                                random_style = random.choice(text_insult_styles)
                                prompt_text = (
                                    f"Сыграй роль циничного, саркастичного и дерзкого ИИ-модератора (stand-up comedy roast).\n"
                                    f"Пользователь {user_link} только что в {cfg('repost_strikes')}-й раз подряд скопипастил в чат один и тот же шаблонный текст-анкету. За это ты выписал ему блокировку на {_mute_len(strike_mute_sec)}.\n"
                                    f"Сгенерируй для него короткую комедийную прожарку (2-3 предложения), чтобы чат посмеялся над его отсутствием фантазии.\n"
                                    f"🔥 ВАЖНОЕ УСЛОВИЕ ДЛЯ ЭТОГО ОТВЕТА: {random_style}\n"
                                    f"Это комедийная сатира. Разрешен черный юмор и грубый сарказм. Обязательно вставь переменную {user_link} в текст. Никаких извинений!"
                                )
                                _send_roast(chat_id, user_link, prompt_text,
                                            "{USER} доспамился своими копипастами и улетел в мут на " + _mute_len(strike_mute_sec) + ". Здесь чат для общения, а не доска объявлений. Научись креативить!", "Анти-Копипаст")
                            
                            db['text_memory'].update_one({"_id": text_memory_id}, {"$set": {"spam_count": 0}})
                        else:
                            text_phrases = [
                                f"🥱 {user_link}, этот текст мы уже видели. Хватит копипастить одно и то же, прояви фантазию! (Страйк {text_spam_count}/{cfg('repost_strikes')})",
                                f"🤖 {user_link}, обнаружен дубликат текста. Чат создан для общения, а не для Ctrl+C -> Ctrl+V. Перепиши анкету! (Страйк {text_spam_count}/{cfg('repost_strikes')})",
                                f"📝 {user_link}, Скайнет засек копипаст. Публикация заготовленных шаблонов запрещена. (Страйк {text_spam_count}/{cfg('repost_strikes')})",
                                f"♻️ {user_link}, у тебя заело кнопки копировать-вставить? Напиши что-то новое ручками, хватит спамить! (Страйк {text_spam_count}/{cfg('repost_strikes')})",
                                f"🔎 {user_link}, индекс уникальности твоего текста пробил дно. Перестань публиковать одинаковые объявы. (Страйк {text_spam_count}/{cfg('repost_strikes')})",
                                f"📜 {user_link}, мы не доска бесплатных объявлений на столбе. Попробуй поздороваться и пообщаться вживую! (Страйк {text_spam_count}/{cfg('repost_strikes')})",
                                f"🚨 {user_link}, моя текстовая память отлично помнит эту пасту. Меняй текст, или скоро уйдешь в мут. (Страйк {text_spam_count}/{cfg('repost_strikes')})",
                                f"🥱 {user_link}, опять эта заезженная анкета... Попробуй хотя бы слова местами поменять для приличия. (Страйк {text_spam_count}/{cfg('repost_strikes')})",
                                f"⌨️ {user_link}, нейросети видят 100% плагиат твоего же прошлого сообщения. Мы тут за живое общение! (Страйк {text_spam_count}/{cfg('repost_strikes')})",
                                f"🤖 {user_link}, Скайнет против бото-поведения. Хватит слать шаблоны по таймеру, включай мозг. (Страйк {text_spam_count}/{cfg('repost_strikes')})"
                            ]
                            warn_msg = bot.send_message(chat_id, random.choice(text_phrases), parse_mode="Markdown", disable_web_page_preview=True)
                            
                            schedule_delete(chat_id, warn_msg.message_id, 300)
                        return 
                    else:
                        if antibayan_text_active:
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
                    markup = types.InlineKeyboardMarkup(row_width=1)
                    db_buttons = db['settings'].find_one({"_id": "skynet_buttons"})
                    
                    if db_buttons and db_buttons.get("buttons"):
                        for btn in db_buttons["buttons"]:
                            kwargs = {"text": btn["text"], "url": btn["url"]}
                            if btn.get("style") and btn["style"] != "default": kwargs["style"] = btn["style"]
                            if btn.get("emoji_id"): kwargs["icon_custom_emoji_id"] = btn["emoji_id"]
                            markup.add(types.InlineKeyboardButton(**kwargs))
                    else:
                        markup.add(types.InlineKeyboardButton(text="Подписаться на МК", url="https://t.me/clubofrm"))

                    sent = bot.send_message(chat_id, "❗ Внимание, чтобы писать в чате вам необходимо подписаться на наш основной канал.\n\nБез подписки на канал ваши сообщения будут удаляться автоматически. Вступая в чат, я подтверждаю совершеннолетие и обязуюсь соблюдать правила, с которыми ознакомлен и согласен.", reply_markup=markup)
                    warned_users[key] = sent.message_id
                    schedule_delete(chat_id, sent.message_id, 120)
                    def auto_delete():
                        time.sleep(120)
                        warned_users.pop(key, None)
                    threading.Thread(target=auto_delete, daemon=True).start()
                return

            # === 🛡 ГЛУХОЙ КАРАНТИН НОВОРЕГОВ (HARD MUTE) ===
            if sys_settings.get("quarantine_active", True):
                first_seen = user_data.get('first_seen')
                if not first_seen:
                    first_seen = time.time()
                    users_collection.update_one({"_id": user_id}, {"$set": {"first_seen": first_seen}}, upsert=True)
                seconds_passed = time.time() - first_seen
                
                if user_id > 7800000000 and seconds_passed < quarantine_sec:
                    try: bot.delete_message(chat_id, message.message_id)
                    except: pass
                    
                    remaining_time = int(quarantine_sec - seconds_passed)
                    if remaining_time > 0:
                        try:
                            bot.restrict_chat_member(
                                chat_id, 
                                user_id, 
                                until_date=int(time.time()) + remaining_time,
                                can_send_messages=False
                            )
                        except: pass

                    try:
                        bot.send_message(
                            user_id, 
                            "🚨 **Защита от спама (Карантин)!**\n\nВаш аккаунт создан недавно. Для безопасности сети действует карантин 120 часов.\nВаши сообщения в чате временно отключены.\n\n🛠 Чтобы снять ограничения досрочно, пройдите быструю верификацию в [Службе Поддержки](https://t.me/MK_MensClubSUPPORT).",
                            parse_mode="Markdown",
                            disable_web_page_preview=True
                        )
                    except:
                        try:
                            safe_name = escape_md(message.from_user.first_name or "Пользователь")
                            ghost_msg = bot.send_message(
                                chat_id,
                                f"🚨 *{safe_name}*, сработала защита от спама!\nВаш аккаунт в карантине (120ч).\n🛠 Для досрочного снятия ограничений пройдите верификацию в [Службе Поддержки](https://t.me/MK_MensClubSUPPORT).",
                                parse_mode="Markdown",
                                disable_web_page_preview=True
                            )
                            schedule_delete(chat_id, ghost_msg.message_id, 15)
                        except: pass
                    
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
                    
                    return 

            # === 📏 ОПЕРАЦИЯ "1 МАЯ" ===
            if sys_settings.get("may_1_active", True):
                EXCLUDED_FROM_PARAMS = set(PARNI_CHATS)
                EXCLUDED_FROM_PARAMS.update([VIP_CHAT_ID, BEYOND_CHAT_ID])
                EXCLUDED_FROM_PARAMS.update([chat_ids_mk.get("Фетиши"), chat_ids_mk.get("Мужской Чат"), chat_ids_mk.get("Секс Туризм"), chat_ids_mk.get("Аренда Жилья")])

                if chat_id not in EXCLUDED_FROM_PARAMS and message.content_type != 'video_note':
                    strict_match = re.search(r'(?<!\d)[1-9]\d/1\d{2}/\d{2,3}(?:/\d{1,2}(?:[.,*xхX]\d{1,2})?)?(?!\d)', text)
                    if not strict_match:
                        safe_delete(bot, chat_id, message.message_id)
                        mute_user_everywhere(user_id, reason="Нет параметров или неверный формат (1 Мая)", admin_name="Скайнет 📏", user_link=user_link, trigger_text=trigger_text, origin_chat=chat_title)
                        markup = types.InlineKeyboardMarkup(row_width=1)
                        markup.add(
                            types.InlineKeyboardButton("🛠 Пройти верификацию", url="https://t.me/MK_MensClubSUPPORT"),
                            types.InlineKeyboardButton("😈 ПАРНИ 18+ (Без ограничений)", url="https://t.me/znakparni/116")
                        )
                        db_texts = db['settings'].find_one({"_id": "skynet_texts"}) or {}
                        raw_text_may1 = db_texts.get("may_1_warn", "🚨 {user_link}, **ВНИМАНИЕ!**\n\nС 1 мая введен СТРОГИЙ стандарт оформления анкет для досок объявлений.\nЛюбой текст **БЕЗ ПАРАМЕТРОВ** или с неправильным форматом запрещен!\nПараметры должны быть указаны **ТОЛЬКО через слеш (/) без пробелов и лишних слов**.\n\n✅ *Примеры:* `24/187/72` или `24/187/72/19` (допускается `19.5` или `19*4`)\n\nВаша анкета удалена, а вы временно ограничены в общении во всех группах сети.\n\n💡 *P.S. В нашей сети «ПАРНИ 18+» нет ограничений на формат текста и разрешен любой откровенный контент (включая порно). Переходи туда! 👇*")

                        warning_msg = bot.send_message(
                            chat_id, 
                            raw_text_may1.replace("{user_link}", user_link),
                            reply_markup=markup, parse_mode="Markdown", disable_web_page_preview=True
                        )
                        schedule_delete(chat_id, warning_msg.message_id, 300)
                        return

            # Очищаем от безопасных контекстов
            safe_age = re.sub(r'(от|парня|мальчика|мужчину|ищу|для)\s*(?:1[89]|2[0-1])\b|\b(?:1[89]|2[0-1])\s*-\s*\d{2}\b|\b(?:1[89]|2[0-1])\s*\+|\b(?:1[89]|2[0-1])\s*(см|cm)\b', '', text)
            
            # Ловим Оранжевую зону (18-21)
            if re.search(r'\b(?:1[89]|2[0-1])\s*(лет|год|годик|y\.?o\.?)\b|\b(?:1[89]|2[0-1])\s*[/\\-]\s*1\d{2}\b|\b(мне|я)\s*(?:1[89]|2[0-1])\b', safe_age):
                # 🔥 ПОДКЛЮЧАЕМ ИИ-АНАЛИТИКУ ПЕРЕД БАНОМ 🔥
                verdict_orange = ai_context_checker(raw_text, zone="orange")
                if verdict_orange is None:
                    ai_down_alert(user_link, user_id, chat_title, raw_text, "оранжевая зона 18–21", zone="orange")
                if verdict_orange:
                    safe_delete(bot, chat_id, message.message_id)
                    mute_user_everywhere(user_id, reason="Оранжевая зона: Возраст 18-21", admin_name="Скайнет 🔞", user_link=user_link, trigger_text=trigger_text, origin_chat=chat_title)
                    markup = types.InlineKeyboardMarkup()
                    markup.add(types.InlineKeyboardButton("🛠 Пройти верификацию 🔞", url=f"https://t.me/{cfg('support_bot')}"))
                    
                    db_texts = db['settings'].find_one({"_id": "skynet_texts"}) or {}
                    raw_text_minor = db_texts.get("minor_warn", "🚨 {user_link}, **Внимание!**\nВаша анкета попала под автоматический фильтр безопасности сети. Пользователи до 21 года включительно проходят обязательную верификацию 🔞.")
                    
                    warning_msg = bot.send_message(chat_id, raw_text_minor.replace("{user_link}", user_link), reply_markup=markup, parse_mode="Markdown", disable_web_page_preview=True)
                    schedule_delete(chat_id, warning_msg.message_id, 300)
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

            # === ⏳ УМНЫЙ АНТИФЛУД ДЛЯ НЕВЕРИФИЦИРОВАННЫХ ===
            # Определяем, какие теги считаются "плохими" (не дают иммунитета)
            bad_tags = ["Not verified", "Параметры FAKE", "РИСК/ВИРТ/ОБМЕН", "автососка", "туалетная соска", "чернильница"]
            
            # Проверяем наличие "хорошего" статуса (они проходят без лимитов)
            has_good_tag = is_vip or is_queer or is_verified or (custom_tag and custom_tag not in bad_tags)
            
            # 🔥 ЧАТЫ СВОБОДНОГО ОБЩЕНИЯ (БЕЗ АНТИФЛУДА) 🔥
            free_chats = [
                chat_ids_mk.get("Мужской Чат")
                # Если захотите сделать другие чаты свободными, просто добавьте их сюда через запятую
                # например: chat_ids_mk.get("БЕЗ ПРЕДРАССУДКОВ")
            ]
            
            # Применяем ко всем обычным юзерам (кроме ПАРНИ 18+, свободных чатов и админов)
            if antiflood_active and not has_good_tag and chat_id not in PARNI_CHATS and chat_id not in free_chats and not is_admin:
                
                # 🔥 ОПРЕДЕЛЯЕМ ЖЕСТКИЕ ЧАТЫ 🔥
                hard_flood_chats = [
                    chat_ids_mk.get("Фетиши"),
                    chat_ids_mk.get("Секс Туризм"),
                    chat_ids_mk.get("Аренда Жилья"),
                    chat_ids_mk.get("Галерея")
                ]
                
                # Настраиваем таймер и текст в зависимости от группы
                if chat_id in hard_flood_chats:
                    flood_duration_sec = flood_hard_sec 
                    flood_time_text = f"{mod_limits.get('flood_hard_hours', 120)} часов"
                else:
                    flood_duration_sec = flood_norm_sec  
                    flood_time_text = f"{mod_limits.get('flood_norm_hours', 6)} часов"

                def apply_smart_flood_limit():
                    # 🕒 ПАУЗА 2 СЕКУНДЫ (Чтобы пропустить медиа-альбомы)
                    time.sleep(2) 
                    try:
                        mute_until = int(time.time()) + flood_duration_sec 
                        bot.restrict_chat_member(
                            chat_id, 
                            user_id, 
                            until_date=mute_until,
                            can_send_messages=False
                        )
                        
                        # Мотивационная отбивка
                        flood_text = (
                            f"⏱ {user_link}, ваше объявление опубликовано! Следующее можно отправить **только через {flood_time_text}**.\n\n"
                            f"🚀 *Хотите публиковать без ограничений и ожиданий?*\n"
                            f"Пройдите бесплатную верификацию или станьте VIP-участником!"
                        )

                        # Кнопки
                        markup = types.InlineKeyboardMarkup(row_width=1)
                        markup.add(
                            types.InlineKeyboardButton("🛠 Пройти верификацию", url=f"https://t.me/{cfg('support_bot')}"),
                            types.InlineKeyboardButton("👑 Купить VIP-статус", url=f"https://t.me/{cfg('vip_bot')}") 
                        )
                        
                        flood_msg = bot.send_message(
                            chat_id, 
                            flood_text, 
                            parse_mode="Markdown", 
                            disable_web_page_preview=True,
                            reply_markup=markup
                        )
                        
                        # Удаляем рекламу через 45 секунд, чтобы не мусорить в чате
                        time.sleep(45)
                        try: bot.delete_message(chat_id, flood_msg.message_id)
                        except: pass
                        
                    except Exception as e:
                        print(f"Ошибка умного антифлуда: {e}")
                        
                # Запускаем в отдельном потоке, чтобы не тормозить Скайнет
                threading.Thread(target=apply_smart_flood_limit, daemon=True).start()
            # ==========================================================

        except Exception as e:
            print(f"Критическая ошибка в skynet_core_handler: {e}")