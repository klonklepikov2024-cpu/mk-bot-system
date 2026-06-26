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
        # 🔥 ЖУЧОК ДЛЯ ЮЗЕРНЕЙМОВ 🔥
        if message.from_user.username:
            db['users'].update_one({"_id": message.from_user.id}, {"$set": {"username": f"@{message.from_user.username}".lower()}}, upsert=True)
            
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

       # Обновленное Главное Меню
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("💰 Реклама", callback_data="btn_ads"),
            InlineKeyboardButton("🆘 СНЯТЬ БЛОК/ОПЕРАТОРЫ/ПОДДЕЖКА", callback_data="btn_unban")
        )
        markup.add(
            InlineKeyboardButton("🚨 Подать жалобу на нарушителя", callback_data="sec_submit_report"), # <-- Жалобы теперь тут!
            InlineKeyboardButton("🎰 Игровой Кабинет", callback_data="btn_game_club")    # <-- Вход в Метавселенную
        )
        markup.add(
            InlineKeyboardButton("📜 Снять бан без вопросов (2000⭐️)", callback_data="buy_indulgence")
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
    
    # 🔥 ЖУЧОК ДЛЯ ЮЗЕРНЕЙМОВ 🔥
    if call.from_user.username:
        db['users'].update_one({"_id": uid}, {"$set": {"username": f"@{call.from_user.username}".lower()}}, upsert=True)
          
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
            bot.send_message(call.message.chat.id, "⛔️ Вы заблокированы за спам. Лимит обращений исчерпан.\n\nДля разблокировки доступа напишите в службу поддержки: @FAQMKBOT")
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
                    caption = f"🔄 **Повторное обращение (ОПЛАЧЕНО ⭐️/🛡):**\n• ID: `{uid}`\n• Юзер: {safe_username}\n\n{history_text}"
                    bot.send_message(STAFF_GROUP_ID, caption, message_thread_id=thread_id, parse_mode="Markdown", reply_markup=markup_ban)
                    paid_collection.update_one({"uid": uid}, {"$set": {"topic_type": "unban"}, "$unset": {"dialog_history": ""}})
                except ApiTelegramException as e:
                    if "not closed" in e.description.lower() or "not modified" in e.description.lower():
                        # Топик и так открыт, просто пишем в него!
                        caption = f"🔄 **Повторное обращение (ОПЛАЧЕНО ⭐️/🛡):**\n• ID: `{uid}`\n• Юзер: {safe_username}\n\n{history_text}"
                        bot.send_message(STAFF_GROUP_ID, caption, message_thread_id=thread_id, parse_mode="Markdown", reply_markup=markup_ban)
                        paid_collection.update_one({"uid": uid}, {"$set": {"topic_type": "unban"}, "$unset": {"dialog_history": ""}})
                    else:
                        logger.warning(f"Топик {thread_id} мертв, создаем новый: {e}")
                        topic = bot.create_forum_topic(chat_id=STAFF_GROUP_ID, name=f"🆘 | {name}")
                        thread_id = topic.message_thread_id
                        paid_collection.update_one({"uid": uid}, {"$set": {"thread_id": thread_id, "topic_type": "unban"}, "$unset": {"dialog_history": ""}}, upsert=True)
                        caption = f"🆕 **Новое обращение (ОПЛАЧЕНО ⭐️/🛡) [ТОПИК ПЕРЕСОЗДАН]:**\n• ID: `{uid}`\n• Юзер: {safe_username}\n\n{history_text}"
                        bot.send_message(STAFF_GROUP_ID, caption, message_thread_id=thread_id, parse_mode="Markdown", reply_markup=markup_ban)
            else:
                try:
                    topic = bot.create_forum_topic(chat_id=STAFF_GROUP_ID, name=f"🆘 | {name}")
                except ApiTelegramException as e:
                    if e.error_code == 429:
                        retry_after = e.result_json.get('parameters', {}).get('retry_after', 3)
                        time.sleep(retry_after + 0.5) 
                        topic = bot.create_forum_topic(chat_id=STAFF_GROUP_ID, name=f"🆘 | {name}")
                    else:
                        raise e 

                thread_id = topic.message_thread_id
                # 🔥 Добавили очистку памяти
                paid_collection.update_one({"uid": uid}, {"$set": {"thread_id": thread_id, "topic_type": "unban"}, "$unset": {"dialog_history": ""}}, upsert=True)
                caption = f"🆕 **Новое обращение (ОПЛАЧЕНО ⭐️/🛡):**\n• ID: `{uid}`\n• Юзер: {safe_username}\n\n{history_text}"
                bot.send_message(STAFF_GROUP_ID, caption, message_thread_id=thread_id, parse_mode="Markdown", reply_markup=markup_ban)
                 
        else:
            new_strikes = user_data.get("strikes", 0) + 1
            paid_collection.update_one({"uid": uid}, {"$set": {"strikes": new_strikes}}, upsert=True)
            
            if new_strikes >= 3:
                cost_fine = 111 # Штраф за спам - 111 Звезд
                cost_rub_fine = cost_fine * 2
                cost_pts_fine = cost_fine * 5
                
                user_rub = user_data.get("cashback_balance", 0)
                user_points = user_data.get("bounty_points", 0)
                
                markup = InlineKeyboardMarkup(row_width=1)
                
                # Кнопка штрафа Звездами
                markup.add(InlineKeyboardButton(f"⭐️ Штраф за спам ({cost_fine}⭐️)", callback_data=f"checkout_pay_support_{cost_fine}"))
                
                # Если хватает Рублей или Очков - даем оплатить из заначки казино
                if user_rub >= cost_rub_fine:
                    markup.add(InlineKeyboardButton(f"💳 Оплатить штраф с баланса ({cost_rub_fine}₽)", callback_data=f"support_rub_{cost_rub_fine}"))
                if user_points >= cost_pts_fine:
                    markup.add(InlineKeyboardButton(f"🎰 Оплатить штраф очками ({cost_pts_fine} очк.)", callback_data=f"support_pts_{cost_pts_fine}"))
                    
                # Всегда предлагаем купить Индульгенцию
                markup.add(InlineKeyboardButton("📜 Купить Индульгенцию (2000⭐️)", callback_data="buy_indulgence"))
                
                bot.send_message(
                    call.message.chat.id, 
                    "⛔️ **Вы заблокированы за спам кнопками.**\n\nЛимит ошибок исчерпан, и вы попали в системный карантин.\n\nДля восстановления доступа необходимо оплатить штраф или приобрести полную Индульгенцию.", 
                    parse_mode="Markdown", 
                    reply_markup=markup
                )
            else:
                    cost_stars = 50
                    cost_points = cost_stars * 5
                    cost_rub = cost_stars * 2
                    
                    user_rub = user_data.get("cashback_balance", 0)
                    user_points = user_data.get("bounty_points", 0)
                    
                    markup = InlineKeyboardMarkup(row_width=1)
                    
                    # 👇 КНОПКА ПРЯМОЙ ОПЛАТЫ ЗВЕЗДАМИ В БОТЕ 👇
                    markup.add(InlineKeyboardButton(f"⭐️ Оплатить {cost_stars}⭐️ (Telegram)", callback_data=f"checkout_pay_support_{cost_stars}"))
                    
                    # Кнопка РУБЛЕЙ
                    if user_rub >= cost_rub:
                        markup.add(InlineKeyboardButton(f"💳 Списать с баланса ({cost_rub}₽)", callback_data=f"support_rub_{cost_rub}"))
                    else:
                        markup.add(InlineKeyboardButton(f"💳 Баланс: {user_rub}₽ (Надо {cost_rub}₽)", callback_data="insufficient_funds"))
                    
                    # Кнопка ОЧКОВ
                    if user_points >= cost_points:
                        markup.add(InlineKeyboardButton(f"🎰 Оплатить очками ({cost_points} очк.)", callback_data=f"support_pts_{cost_points}"))
                    else:
                        missing = cost_points - user_points
                        markup.add(InlineKeyboardButton(f"🎰 Не хватает {missing} Очков (Играть)", url="https://t.me/FAQMKBOT"))
                    
                    # 👇 ВЕРНУЛИ ТВОЙ ТЕКСТ ДЛЯ "НЕ УМНЫХ", НО ДОБАВИЛИ ЛАЙФХАК 👇
                    warning_text = (
                        f"⚠️ **Внимание!** Сначала необходимо задать вопрос в платной группе [СЛУЖБЫ ПОДДЕРЖКИ](https://t.me/MK_MensClubSUPPORT) и оплатить 60 звезд.\n\n"
                        f"💡 **ИЛИ:** Вы можете не переходить в группу, а оплатить обращение прямо здесь (Звездами, кэшбеком или очками казино)!\n\n"
                        f"_Попытка {new_strikes} из 3. После 3-й ошибки бот заблокирует вас._"
                    )
                    bot.send_message(call.message.chat.id, warning_text, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=markup)
        return

    # ================= ЛОГИКА КНОПКИ РЕКЛАМЫ =================
    elif call.data == "btn_ads":
        bot.send_message(call.message.chat.id, "Вы выбрали раздел 'Купить рекламу'. Пожалуйста, напишите ваше предложение ниже:")
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🚨 Это хитрец (Впаять страйк)", callback_data=f"trap_{uid}"))
        
        if thread_id:
            try: 
                bot.reopen_forum_topic(STAFF_GROUP_ID, thread_id)
                bot.send_message(STAFF_GROUP_ID, f"🔄 **Повторный запрос на РЕКЛАМУ:**\n• ID: `{uid}`\n• Юзер: {safe_username}\n\nЕсли он просит разбан, жмите кнопку ниже 👇", message_thread_id=thread_id, parse_mode="Markdown", reply_markup=markup)
                paid_collection.update_one({"uid": uid}, {"$set": {"topic_type": "ads"}})
            except ApiTelegramException as e: 
                if "not closed" in e.description.lower() or "not modified" in e.description.lower():
                    bot.send_message(STAFF_GROUP_ID, f"🔄 **Повторный запрос на РЕКЛАМУ:**\n• ID: `{uid}`\n• Юзер: {safe_username}\n\nЕсли он просит разбан, жмите кнопку ниже 👇", message_thread_id=thread_id, parse_mode="Markdown", reply_markup=markup)
                    paid_collection.update_one({"uid": uid}, {"$set": {"topic_type": "ads"}})
                else:
                    logger.warning(f"Топик рекламы {thread_id} мертв, создаем новый: {e}")
                    topic = bot.create_forum_topic(chat_id=STAFF_GROUP_ID, name=f"💰 РЕКЛАМА | {name}")
                    thread_id = topic.message_thread_id
                    paid_collection.update_one({"uid": uid}, {"$set": {"thread_id": thread_id, "topic_type": "ads"}}, upsert=True)
                    bot.send_message(STAFF_GROUP_ID, f"🆕 **Новый запрос на РЕКЛАМУ [ТОПИК ПЕРЕСОЗДАН]:**\n• ID: `{uid}`\n• Юзер: {safe_username}\n\nЕсли он просит разбан, жмите кнопку ниже 👇", message_thread_id=thread_id, parse_mode="Markdown", reply_markup=markup)
        else:
            topic = bot.create_forum_topic(chat_id=STAFF_GROUP_ID, name=f"💰 РЕКЛАМА | {name}")
            thread_id = topic.message_thread_id
            paid_collection.update_one({"uid": uid}, {"$set": {"thread_id": thread_id, "topic_type": "ads"}}, upsert=True)
            bot.send_message(STAFF_GROUP_ID, f"🆕 **Новый запрос на РЕКЛАМУ:**\n• ID: `{uid}`\n• Юзер: {safe_username}\n\nЕсли он просит разбан, жмите кнопку ниже 👇", message_thread_id=thread_id, parse_mode="Markdown", reply_markup=markup)
        
        return

# 👇 НОВЫЕ ОБРАБОТЧИКИ ОПЛАТЫ ПОДДЕРЖКИ 👇
@bot.callback_query_handler(func=lambda call: call.data.startswith('support_rub_') or call.data.startswith('support_pts_'))
def handle_support_payment(call):
    try: bot.answer_callback_query(call.id)
    except: pass

    is_points = call.data.startswith('support_pts_')
    cost = int(call.data.split('_')[2])
    uid = call.from_user.id

    user_data = paid_collection.find_one({"uid": uid}) or {}

    if is_points:
        if user_data.get("bounty_points", 0) < cost:
            bot.send_message(call.message.chat.id, "❌ Недостаточно очков!")
            return
        # 👇 ДОБАВЛЯЕМ "strikes": 0 ВОТ СЮДА 👇
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": -cost}, "$set": {"status": 1, "strikes": 0}})
        currency = "очков"
    else:
        if user_data.get("cashback_balance", 0) < cost:
            bot.send_message(call.message.chat.id, "❌ Недостаточно рублей!")
            return
        # 👇 И ВОТ СЮДА 👇
        paid_collection.update_one({"uid": uid}, {"$inc": {"cashback_balance": -cost}, "$set": {"status": 1, "strikes": 0}})
        currency = "₽"

    try: bot.delete_message(call.message.chat.id, call.message.message_id)
    except: pass

    bot.send_message(
        call.message.chat.id,
        f"✅ **Оплата прошла успешно!** (Списано {cost} {currency})\nДоступ к поддержке открыт.\n\n👇 Нажмите кнопку ниже, чтобы написать обращение.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🆘 НАПИСАТЬ ОБРАЩЕНИЕ", callback_data="btn_unban"))
    )

@bot.callback_query_handler(func=lambda call: call.data == "insufficient_funds")
def handle_insufficient_funds_start(call):
    bot.answer_callback_query(call.id, "На вашем счету не хватает средств! 😔 Поиграйте в Гача-Рулетку.", show_alert=True)