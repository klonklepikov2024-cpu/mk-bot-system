import time
from telebot.apihelper import ApiTelegramException
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from core.bot import bot
from config import STAFF_GROUP_ID
from database.mongo import paid_collection, archive_collection, db
from utils.logger import logger, notify_admin_on_error

@bot.message_handler(commands=['start'])
def send_welcome(message):
    try:
        # Обработка диплинк-ссылки на магазин (например, /start shop)
        if len(message.text.split()) > 1 and message.text.split()[1] == "shop":
            markup = InlineKeyboardMarkup(row_width=1)
            markup.add(
                InlineKeyboardButton("📦 50 очков (1 прокрут) — 50⭐️", callback_data="shop_points_buy_50_50"),
                InlineKeyboardButton("🔥 300 очков (6 прокрутов) — 200⭐️", callback_data="shop_points_buy_300_200"),
                InlineKeyboardButton("💎 1000 очк. + 🛡 Щит — 500⭐️", callback_data="shop_points_buy_1000_500"),
                InlineKeyboardButton("🔙 В главное меню", callback_data="sec_back_main")
            )
            shop_text = "🎰 **Магазин Очков Бдительности**\n\nОчки можно тратить на скидки в кабинете или использовать для игры в Гача-Рулетку (`/spin`).\n\n🏆 _Посмотреть список призов: /prizes_\n\nВыберите нужный пакет:"
            bot.send_message(message.chat.id, shop_text, reply_markup=markup, parse_mode="Markdown")
            return

        # Стандартное Главное Меню
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(
            InlineKeyboardButton("💰 Купить рекламу / Сотрудничество", callback_data="btn_ads"),
            InlineKeyboardButton("🆘 Разблокировка / Верификация", callback_data="btn_unban"),
            InlineKeyboardButton("🛡 Кабинет Агента / Жалобы", callback_data="btn_report")
        )
        bot.send_message(message.chat.id, f"Привет, {message.from_user.first_name}! 👋\nВыберите нужный раздел:", reply_markup=markup)
        
    except Exception as e:
        # УБИЛИ PRINT, ВНЕДРИЛИ КРАСИВЫЙ ЛОГГЕР С УВЕДОМЛЕНИЕМ АДМИНА
        notify_admin_on_error(bot, e, context="Ошибка в /start (Главное меню)")

@bot.callback_query_handler(func=lambda call: call.data.startswith('btn_'))
def handle_user_query(call):
    # Обязательно снимаем часики на кнопке
    try:
        bot.answer_callback_query(call.id)
    except Exception as e:
        logger.warning(f"Не удалось снять ожидание с кнопки: {e}")

    # --- УБИРАЕМ КНОПКИ У ЮЗЕРА, ЧТОБЫ НЕ СПАМИЛ ---
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except Exception as e:
        # УБИЛИ except: pass! Теперь мы видим, если телеграм не дал убрать кнопку
        logger.warning(f"Не удалось убрать кнопки в главном меню у {call.from_user.id}: {e}")
    
    uid = call.from_user.id
    
    # ЗАЩИТА ОТ "НЕВИДИМОК"
    name = call.from_user.first_name
    if not name or name == '\u3164' or name == 'ㅤ':
        name = f"Без Имени (ID {uid})"

    username = f"@{call.from_user.username}" if call.from_user.username else f"ID {uid}"
    safe_username = username.replace('_', '\\_')

    user_data = paid_collection.find_one({"uid": uid}) or {"uid": uid, "status": 0, "strikes": 0, "thread_id": None}
    thread_id = user_data.get("thread_id")

    # ================= ЛОГИКА КНОПКИ РАЗБАНА =================
    if call.data == "btn_unban":
        if user_data.get("strikes", 0) >= 3 and user_data.get("status") != 1:
            bot.send_message(call.message.chat.id, "⛔️ Вы заблокированы за спам. Лимит обращений исчерпан.")
            return 

        # ПРОВЕРКА ЩИТА ИММУНИТЕТА
        if user_data.get("status") != 1 and user_data.get("immunity", 0) > 0:
            paid_collection.update_one({"uid": uid}, {"$inc": {"immunity": -1}, "$set": {"status": 1}}) 
            user_data["status"] = 1 
            bot.send_message(call.message.chat.id, "🛡 **Сработал Щит Иммунитета!**\nОдно бесплатное обращение в поддержку активировано. Щит разрушен.", parse_mode="Markdown")

        if user_data.get("status") == 1:
            paid_collection.update_one({"uid": uid}, {"$set": {"strikes": 0}}) 
            
            # Поиск истории в досье и базах Скайнета
            user_record = archive_collection.find_one({"target": username}) or \
                          archive_collection.find_one({"target": str(uid)}) or \
                          archive_collection.find_one({"target": uid})
            
            skynet_ban = db['banned'].find_one({"_id": uid})
            
            history_text = "🟢 История чиста."
            if user_record and "history" in user_record:
                history_text = "⚠️ **Досье пользователя:**\n"
                for entry in user_record["history"][-10:]:
                    history_text += f"• {entry['date']} — {entry['action']}\nПричина: {entry.get('reason', 'Не указана')}\n"
            
            if skynet_ban:
                history_text += f"\n🚨 **АКТИВНЫЙ БАН СКАЙНЕТА:**\nПричина: {skynet_ban.get('reason', 'Не указана')}"
            
            bot.send_message(call.message.chat.id, "✅ **Доступ открыт.** Напишите вашу проблему ниже, и мы начнем процесс верификации.")

            markup_ban = InlineKeyboardMarkup()
            markup_ban.add(InlineKeyboardButton("🚷 Заблокировать (Спам)", callback_data=f"ban_{uid}"))
            
            if thread_id:
                try:
                    bot.reopen_forum_topic(STAFF_GROUP_ID, thread_id)
                except Exception as e:
                    logger.warning(f"Топик {thread_id} не удалось переоткрыть: {e}")
                    
                caption = f"🔄 **Повторное обращение (ОПЛАЧЕНО ⭐️/🛡):**\n• ID: `{uid}`\n• Юзер: {safe_username}\n\n{history_text}"
                bot.send_message(STAFF_GROUP_ID, caption, message_thread_id=thread_id, parse_mode="Markdown", reply_markup=markup_ban)
                paid_collection.update_one({"uid": uid}, {"$set": {"topic_type": "unban"}})
            else:
                # 🔥 Бронебойное создание топика с защитой от ошибки 429 🔥
                try:
                    topic = bot.create_forum_topic(chat_id=STAFF_GROUP_ID, name=f"🆘 | {name}")
                except ApiTelegramException as e:
                    if e.error_code == 429:
                        # Телеграм просит подождать. Достаем время из ответа (обычно 3-5 сек)
                        retry_after = e.result_json.get('parameters', {}).get('retry_after', 3)
                        time.sleep(retry_after + 0.5) # Спим указанное время + полсекунды
                        # Пробуем создать топик еще раз после паузы
                        topic = bot.create_forum_topic(chat_id=STAFF_GROUP_ID, name=f"🆘 | {name}")
                    else:
                        raise e # Если ошибка другая, пропускаем её дальше

                thread_id = topic.message_thread_id
                paid_collection.update_one({"uid": uid}, {"$set": {"thread_id": thread_id, "topic_type": "unban"}}, upsert=True)
                caption = f"🆕 **Новое обращение (ОПЛАЧЕНО ⭐️/🛡):**\n• ID: `{uid}`\n• Юзер: {safe_username}\n\n{history_text}"
                bot.send_message(STAFF_GROUP_ID, caption, message_thread_id=thread_id, parse_mode="Markdown", reply_markup=markup_ban)
                 
        else:
            new_strikes = user_data.get("strikes", 0) + 1
            paid_collection.update_one({"uid": uid}, {"$set": {"strikes": new_strikes}}, upsert=True)
            
            if new_strikes >= 3:
                bot.send_message(call.message.chat.id, "⛔️ Вы заблокированы за спам. Обращения без оплаты игнорируются.")
            else:
                warning_text = f"⚠️ **Внимание!** Сначала необходимо задать вопрос в платной группе [СЛУЖБЫ ПОДДЕРЖКИ](https://t.me/MK_MensClubSUPPORT) и оплатить 50 звезд.\n\nПопытка {new_strikes} из 3. После 3-й попытки бот вас заблокирует."
                bot.send_message(call.message.chat.id, warning_text, parse_mode="Markdown", disable_web_page_preview=True)
        return

    # ================= ЛОГИКА КНОПКИ РЕКЛАМЫ =================
    elif call.data == "btn_ads":
        bot.send_message(call.message.chat.id, "Вы выбрали раздел 'Купить рекламу'. Пожалуйста, напишите ваше предложение ниже:")
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🚨 Это хитрец (Впаять страйк)", callback_data=f"trap_{uid}"))
        
        if thread_id:
            try: 
                bot.reopen_forum_topic(STAFF_GROUP_ID, thread_id)
            except Exception as e: 
                logger.warning(f"Не удалось переоткрыть топик для рекламы {thread_id}: {e}")
            
            bot.send_message(STAFF_GROUP_ID, f"🔄 **Повторный запрос на РЕКЛАМУ:**\n• ID: `{uid}`\n• Юзер: {safe_username}\n\nЕсли он просит разбан, жмите кнопку ниже 👇", message_thread_id=thread_id, parse_mode="Markdown", reply_markup=markup)
            paid_collection.update_one({"uid": uid}, {"$set": {"topic_type": "ads"}})
        else:
            topic = bot.create_forum_topic(chat_id=STAFF_GROUP_ID, name=f"💰 РЕКЛАМА | {name}")
            thread_id = topic.message_thread_id
            paid_collection.update_one({"uid": uid}, {"$set": {"thread_id": thread_id, "topic_type": "ads"}}, upsert=True)
            bot.send_message(STAFF_GROUP_ID, f"🆕 **Новый запрос на РЕКЛАМУ:**\n• ID: `{uid}`\n• Юзер: {safe_username}\n\nЕсли он просит разбан, жмите кнопку ниже 👇", message_thread_id=thread_id, parse_mode="Markdown", reply_markup=markup)
        
        return

    # ================= ЛОГИКА КНОПКИ СЛУЖБЫ БЕЗОПАСНОСТИ =================
    elif call.data == "btn_report":
        points = user_data.get("bounty_points", 0)
        if points <= -50:
            bot.send_message(call.message.chat.id, "⛔️ **Доступ закрыт.**\nСлужба безопасности отключила вам доступ к системе жалоб из-за большого количества ложных доносов.")
            return

        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(
            InlineKeyboardButton("🚨 Подать жалобу", callback_data="sec_submit_report"),
            InlineKeyboardButton(f"🏆 Кабинет Агента ({points} очков)", callback_data="sec_agent_cabinet"),
            InlineKeyboardButton("🔙 В главное меню", callback_data="sec_back_main")
        )
        
        text = (
            "🛡 **Служба Безопасности МК**\n\n"
            "Помогайте нам очищать чаты от спамеров, мошенников и нарушителей, и получайте за это баллы!\n\n"
            "Накопленные баллы можно обменять на ценные скидки или VIP-статус."
        )
        bot.send_message(call.message.chat.id, text, reply_markup=markup)
        return