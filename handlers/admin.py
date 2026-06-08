import random
import datetime
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

from core.bot import bot
from config import STAFF_GROUP_ID
from database.mongo import paid_collection, archive_collection, db
from utils.logger import logger
from utils.templates import TEMPLATES, NETWORK_LINKS
from utils.cryptobot import get_crypto_pay_url

# ================= СООБЩЕНИЯ ОТ ЮЗЕРА -> АДМИНАМ =================
@bot.message_handler(func=lambda message: message.chat.type == 'private' and not (message.text and message.text.startswith('/')), content_types=['text', 'photo', 'document', 'video_note', 'voice', 'video', 'sticker', 'audio'])
def handle_user_messages(message):
    uid = message.from_user.id
    user_data = paid_collection.find_one({"uid": uid}) or {}
    
    if user_data.get("strikes", 0) >= 3 and user_data.get("status") != 1:
        return 
        
    topic_type = user_data.get("topic_type")
    if not topic_type:
        if user_data.get("status") == 1:
            bot.send_message(message.chat.id, "✅ Вижу вашу оплату! Пожалуйста, нажмите /start и выберите нужный раздел, чтобы мы начали.")
        else:
            bot.send_message(message.chat.id, "🏁 Ваше обращение закрыто.\nДля создания нового выберите нужный раздел в меню /start.")
        return

    thread_id = user_data.get("thread_id")
    if not thread_id:
        bot.send_message(message.chat.id, "⚠️ Ошибка связи: Топик не найден. Пожалуйста, нажмите /start и выберите раздел заново.")
        return

    def cleanup_old_buttons():
        last_msg_id = user_data.get("last_admin_msg_id")
        if last_msg_id:
            try: bot.edit_message_reply_markup(STAFF_GROUP_ID, last_msg_id, reply_markup=None)
            except: pass

    try:
        # ТЕКСТ
        if message.content_type == 'text':
            if message.text.lower() in ["готов", "готова", "готовы", "готов(а)"]:
                code_words = ["ЯБЛОКО", "ТИГР", "СОЛНЦЕ", "МОРЕ", "СОКОЛ", "РАКЕТА", "ВЕТЕР", "МАЯК"]
                secret_code = f"{random.choice(code_words)}-{random.randint(10, 99)}"
                
                paid_collection.update_one({"uid": uid}, {"$set": {"verif_timer": datetime.datetime.now(), "secret_code": secret_code}})
                
                text_phrase = f"⏳ **Таймер запущен! У вас ровно 10 минут.**\n\nЗапишите **видео-кружок**, на котором четко видно лицо, и произнесите:\n\n💬 *«Привет команде МК, я из *города* на часах: *хх:хх* часов. Мой код: {secret_code}»*."
                bot.send_message(uid, text_phrase, parse_mode="Markdown")
                bot.send_message(STAFF_GROUP_ID, f"⏳ *Пользователь написал «Готов». Бот выдал код: {secret_code} и запустил таймер 10 минут! Ждем кружок.*", message_thread_id=thread_id, parse_mode="Markdown")
                return 

            cleanup_old_buttons()
            
            if topic_type in ["unban", "ads"]:
                markup = InlineKeyboardMarkup(row_width=2)
                markup.add(InlineKeyboardButton("💳 250⭐️", callback_data="fine_250"), InlineKeyboardButton("💳 650⭐️", callback_data="fine_650"), InlineKeyboardButton("💳 1563⭐️", callback_data="fine_1563"))
                markup.add(InlineKeyboardButton("✍️ Указать свою сумму", callback_data="fine_custom"))
                markup.add(
                    InlineKeyboardButton("🔞 Запрос /18", callback_data="tpl_18"),
                    InlineKeyboardButton("🎥 Верификация", callback_data="tpl_verif"),
                    InlineKeyboardButton("💰 /мп", callback_data="tpl_mp"),
                    InlineKeyboardButton("💊 /нарк", callback_data="tpl_nark"),
                    InlineKeyboardButton("💉 Реакция нарк", callback_data="tpl_nark_react"),
                    InlineKeyboardButton("🔇 /флуд", callback_data="tpl_flood"),
                    InlineKeyboardButton("🤖 Блок VIP", callback_data="tpl_vip"),
                    InlineKeyboardButton("🔗 Ссылка БИО", callback_data="tpl_bio"),
                    InlineKeyboardButton("👶 Реакция 18-", callback_data="tpl_minor")
                )
                markup.add(InlineKeyboardButton("🔓 РАЗБАН (Снять ограничения)", callback_data="force_unban"), InlineKeyboardButton("🏁 Закрыть (Без разбана)", callback_data="close_ticket"))
            else:
                markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🏁 Закрыть диалог", callback_data="close_ticket"))
                
            sent_msg = bot.send_message(STAFF_GROUP_ID, f"📩 {message.text}", message_thread_id=thread_id, reply_markup=markup)
            paid_collection.update_one({"uid": uid}, {"$set": {"last_admin_msg_id": sent_msg.message_id}})
        
        # ФОТО И ДОКУМЕНТЫ
        elif message.content_type in ['photo', 'document']:
            markup = InlineKeyboardMarkup(row_width=1)
            markup.add(InlineKeyboardButton("✅ Документ принят (Запросить видео)", callback_data="doc_ok"), InlineKeyboardButton("❌ Плохое фото (Перезапросить)", callback_data="doc_bad"))
            if message.photo:
                bot.send_photo(STAFF_GROUP_ID, message.photo[-1].file_id, caption="📸 **Пользователь прислал фото!**\nПроверьте документ:", message_thread_id=thread_id, parse_mode="Markdown", reply_markup=markup)
            else:
                bot.send_document(STAFF_GROUP_ID, message.document.file_id, caption="📄 **Пользователь прислал документ!**\nПроверьте:", message_thread_id=thread_id, parse_mode="Markdown", reply_markup=markup)
        
        # КРУЖКИ
        elif message.content_type == 'video_note':
            verif_timer = user_data.get("verif_timer")
            expected_code = user_data.get("secret_code", "Неизвестно (Старая заявка)")
            
            if verif_timer:
                time_diff = (datetime.datetime.now() - verif_timer).total_seconds()
                if time_diff > 600:
                    bot.send_message(uid, "❌ **Время вышло!** Вы не уложились в 10 минут. Ожидайте решения администратора.")
                    bot.send_message(STAFF_GROUP_ID, "⚠️ **ВНИМАНИЕ! Юзер просрочил таймер.**", message_thread_id=thread_id)
                paid_collection.update_one({"uid": uid}, {"$unset": {"verif_timer": ""}})
            
            markup = InlineKeyboardMarkup(row_width=1).add(InlineKeyboardButton("✅ Кружок принят (РАЗБАН)", callback_data="vid_ok"), InlineKeyboardButton("❌ Плохое видео (Перезапросить)", callback_data="vid_bad"))
            bot.send_video_note(STAFF_GROUP_ID, message.video_note.file_id, message_thread_id=thread_id, reply_markup=markup)
            bot.send_message(STAFF_GROUP_ID, f"🎥 **Пользователь прислал кружок!**\n\n🗣 **ОН ДОЛЖЕН СКАЗАТЬ:**\n_{expected_code}_", message_thread_id=thread_id, parse_mode="Markdown")

        # ПРОЧЕЕ
        elif message.content_type in ['voice', 'video', 'sticker', 'audio', 'animation']:
            bot.copy_message(STAFF_GROUP_ID, message.chat.id, message.message_id, message_thread_id=thread_id)

    except Exception as e:
        logger.error(f"СИСТЕМНАЯ ОШИБКА ДОСТАВКИ (Юзер -> Админ): {e}")
        try: bot.send_message(message.chat.id, "⚠️ Произошла ошибка при отправке сообщения. Пожалуйста, отправьте его еще раз.")
        except: pass

# ================= ПРОВЕРКА ДОКУМЕНТОВ =================
@bot.callback_query_handler(func=lambda call: call.data in ['doc_ok', 'doc_bad'])
def handle_doc_check(call):
    if str(call.message.chat.id) != STAFF_GROUP_ID: return
    try: bot.answer_callback_query(call.id)
    except: pass
        
    thread_id = call.message.message_thread_id
    user_data = paid_collection.find_one({"thread_id": thread_id})
    if not user_data: return 
    target_uid = user_data["uid"]
        
    if call.data == 'doc_ok':
        code_words = ["ЯБЛОКО", "ТИГР", "СОЛНЦЕ", "МОРЕ", "СОКОЛ", "РАКЕТА", "ВЕТЕР", "МАЯК"]
        secret_code = f"{random.choice(code_words)}-{random.randint(10, 99)}"
        paid_collection.update_one({"uid": target_uid}, {"$set": {"verif_timer": datetime.datetime.now(), "secret_code": secret_code}})
        
        text_to_user = f"✅ **Документ принят! Отлично.**\n\nВторой этап верификации:\nЗапишите **видео-кружок**, на котором будет четко видно ваше лицо, и произнесите фразу:\n\n💬 *«Привет команде МК, я из *города* на часах: *хх:хх* часов. Мой код: {secret_code}»*.\n\nУ вас есть 10 минут на отправку видео."
        try: bot.send_message(target_uid, text_to_user, parse_mode="Markdown")
        except: pass
        try: bot.edit_message_caption(f"✅ *Документ одобрен. Запрошен видео-кружок с кодом: {secret_code}.*", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=None)
        except: pass
        
    elif call.data == 'doc_bad':
        markup = InlineKeyboardMarkup(row_width=1).add(
            InlineKeyboardButton("🔎 Размыто / Засветы", callback_data="rej_doc_blur"),
            InlineKeyboardButton("🙈 Скрыты нужные данные", callback_data="rej_doc_hidden"),
            InlineKeyboardButton("📄 Не тот документ", callback_data="rej_doc_wrong")
        )
        try: bot.edit_message_caption("❓ **Укажите причину отказа:**", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=markup)
        except: pass

# ================= КНОПКИ АДМИНА И ШТРАФЫ =================
@bot.callback_query_handler(func=lambda call: call.data.startswith('tpl_') or call.data.startswith('fine_'))
def handle_admin_templates(call):
    if str(call.message.chat.id) != STAFF_GROUP_ID: return
    try: bot.answer_callback_query(call.id)
    except: pass
        
    thread_id = call.message.message_thread_id
    user_data = paid_collection.find_one({"thread_id": thread_id})
    if not user_data: return
    target_uid = user_data["uid"]

    if call.data == 'fine_custom':
        msg = bot.send_message(STAFF_GROUP_ID, "✍️ **Введите сумму штрафа (от 1 до 10000):**\n_Просто отправьте число сообщением сюда._", message_thread_id=thread_id, parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_custom_fine, target_uid=target_uid, thread_id=thread_id, call_msg=call.message)
        return

    if call.data.startswith('fine_'):
        amount = int(call.data.split('_')[1])
        try:
            user_data_pay = paid_collection.find_one({"uid": target_uid}) or {}
            cb_balance = user_data_pay.get("cashback_balance", 0)
            cost_in_rub = amount * 2
            
            url_usdt = get_crypto_pay_url(f"fine_{target_uid}", amount, f"Оплата штрафа ({amount}⭐️)", asset="USDT")
            url_ton = get_crypto_pay_url(f"fine_{target_uid}", amount, f"Оплата штрафа ({amount}⭐️)", asset="TON")
            
            markup = InlineKeyboardMarkup(row_width=1).add(InlineKeyboardButton("🎫 У меня есть промокод", callback_data=f"checkout_promo_fine_{amount}"))
            
            if cb_balance >= cost_in_rub:
                markup.add(InlineKeyboardButton(f"💰 Оплатить с баланса ({cost_in_rub}₽)", callback_data=f"checkout_balance_fine_{amount}"))
            elif cb_balance > 0:
                remaining_stars = amount - (cb_balance // 2)
                markup.add(InlineKeyboardButton(f"💳 Списать {cb_balance}₽ и доплатить {remaining_stars}⭐️", callback_data=f"checkout_partial_fine_{amount}_{cb_balance}"))
            else:
                markup.add(InlineKeyboardButton(f"💳 Оплатить {amount}⭐️", callback_data=f"checkout_pay_fine_{amount}"))
            
            if url_usdt: markup.add(InlineKeyboardButton("🟢 USDT (CryptoBot)", url=url_usdt))
            if url_ton: markup.add(InlineKeyboardButton("💎 TON (CryptoBot)", url=url_ton))
                
            bot.send_message(target_uid, f"🧾 **Вам выставлен счет на оплату штрафа.**\n\nСумма к оплате: **{amount}⭐️**\nПосле оплаты ограничения будут сняты автоматически.", reply_markup=markup, parse_mode="Markdown")
            bot.send_message(STAFF_GROUP_ID, f"🟢 *Скайнет отправил кассу на штраф ({amount}⭐️)*", message_thread_id=thread_id, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Ошибка выставления штрафа: {e}")
        return

    template_text = TEMPLATES.get(call.data)
    if template_text:
        try:
            bot.send_message(target_uid, template_text, parse_mode="Markdown")
            bot.send_message(STAFF_GROUP_ID, f"🟢 *Скайнет отправил шаблон:*\n_{template_text.splitlines()[0]}_", message_thread_id=thread_id, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Ошибка отправки шаблона: {e}")
            try: bot.send_message(STAFF_GROUP_ID, "⚠️ **ОШИБКА:** Невозможно отправить шаблон. Пользователь заблокировал бота!", message_thread_id=thread_id, parse_mode="Markdown")
            except: pass

def process_custom_fine(message, target_uid, thread_id, call_msg):
    if not message.text or not message.text.isdigit():
        try: bot.send_message(STAFF_GROUP_ID, "❌ **Ошибка:** Нужно было отправить только число (например: 350).", message_thread_id=thread_id, parse_mode="Markdown")
        except: pass
        return
        
    amount = int(message.text)
    if amount < 1 or amount > 10000:
        try: bot.send_message(STAFF_GROUP_ID, "❌ **Ошибка:** Сумма должна быть от 1 до 10000 звезд.", message_thread_id=thread_id, parse_mode="Markdown")
        except: pass
        return
        
    try:
        user_data_pay = paid_collection.find_one({"uid": target_uid}) or {}
        cb_balance = user_data_pay.get("cashback_balance", 0)
        cost_in_rub = amount * 2
        
        url_usdt = get_crypto_pay_url(f"fine_{target_uid}", amount, f"Оплата штрафа ({amount}⭐️)", asset="USDT")
        url_ton = get_crypto_pay_url(f"fine_{target_uid}", amount, f"Оплата штрафа ({amount}⭐️)", asset="TON")
        
        markup = InlineKeyboardMarkup(row_width=1).add(InlineKeyboardButton("🎫 У меня есть промокод", callback_data=f"checkout_promo_fine_{amount}"))
        
        if cb_balance >= cost_in_rub:
            markup.add(InlineKeyboardButton(f"💰 Оплатить с баланса ({cost_in_rub}₽)", callback_data=f"checkout_balance_fine_{amount}"))
        elif cb_balance > 0:
            remaining_stars = amount - (cb_balance // 2)
            markup.add(InlineKeyboardButton(f"💳 Списать {cb_balance}₽ и доплатить {remaining_stars}⭐️", callback_data=f"checkout_partial_fine_{amount}_{cb_balance}"))
        else:
            markup.add(InlineKeyboardButton(f"💳 Оплатить {amount}⭐️", callback_data=f"checkout_pay_fine_{amount}"))
        
        if url_usdt: markup.add(InlineKeyboardButton("🟢 USDT (CryptoBot)", url=url_usdt))
        if url_ton: markup.add(InlineKeyboardButton("💎 TON (CryptoBot)", url=url_ton))
            
        bot.send_message(target_uid, f"🧾 **Вам выставлен счет на оплату штрафа.**\n\nСумма к оплате: **{amount}⭐️**\nПосле оплаты ограничения будут сняты автоматически.", reply_markup=markup, parse_mode="Markdown")
        bot.send_message(STAFF_GROUP_ID, f"🟢 *Скайнет отправил кассу на штраф ({amount}⭐️) по вашему поручению.*", message_thread_id=thread_id, parse_mode="Markdown")
    except Exception as e:
        logger.warning(f"Ошибка выставления кастомного штрафа: {e}")

# ================= ПРОВЕРКА КРУЖКА =================
@bot.callback_query_handler(func=lambda call: call.data in ['vid_ok', 'vid_bad'])
def handle_vid_check(call):
    if str(call.message.chat.id) != STAFF_GROUP_ID: return
    try: bot.answer_callback_query(call.id)
    except: pass
        
    thread_id = call.message.message_thread_id
    user_data = paid_collection.find_one({"thread_id": thread_id})
    if not user_data: return 
    target_uid = user_data["uid"]
        
    if call.data == 'vid_ok':
        now = datetime.datetime.now()
        ticket_num = now.strftime("%d%m%Y%H%M%S") + f"-{random.randint(100, 999)}"
        
        db['skynet_tasks'].insert_one({"uid": target_uid, "action": "full_unban", "timestamp": now})
        db['users'].update_one({"_id": target_uid}, {"$set": {"custom_tag": "Верифицирован МК"}}, upsert=True)
        
        admin_username = call.from_user.username or call.from_user.first_name
        db['ticket_ratings'].update_one({"thread_id": thread_id}, {"$set": {"admin": admin_username, "uid": target_uid}}, upsert=True)
        
        try:
            bot.send_message(target_uid, f"🎉 **Ограничения удалены, выдан тег верифицированного участника!** ❤️\n\n🔒 **Обращение закрыто. Уникальный номер:** `{ticket_num}`\n\n{NETWORK_LINKS}", parse_mode="Markdown", disable_web_page_preview=True)
            markup = InlineKeyboardMarkup(row_width=5).add(
                InlineKeyboardButton("1⭐", callback_data=f"rate_1_{thread_id}"), InlineKeyboardButton("2⭐", callback_data=f"rate_2_{thread_id}"),
                InlineKeyboardButton("3⭐", callback_data=f"rate_3_{thread_id}"), InlineKeyboardButton("4⭐", callback_data=f"rate_4_{thread_id}"),
                InlineKeyboardButton("5⭐", callback_data=f"rate_5_{thread_id}")
            )
            markup.add(InlineKeyboardButton("💸 Отправить чаевые админам (Донат) ⭐️", callback_data="start_donate"))
            bot.send_message(target_uid, "🏁 Пожалуйста, оцените работу службы поддержки. Нам важно ваше мнение! 👇", reply_markup=markup)
        except Exception as e:
            logger.warning(f"Ошибка уведомления о разбане (вид_ок): {e}")
        
        archive_collection.update_one({"target": str(target_uid)}, {"$push": {"history": {"date": now.strftime("%d.%m.%Y %H:%M"), "action": "Успешная верификация", "reason": "Кружок принят админом"}}}, upsert=True)
        
        try: bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
        except: pass
        try: bot.send_message(call.message.chat.id, f"✅ *Видео-кружок одобрен!*\nЮзер верифицирован. Приказ на размут передан Скайнету. Тикет закрыт: `{ticket_num}`", message_thread_id=thread_id, parse_mode="Markdown")
        except: pass
        try: bot.close_forum_topic(STAFF_GROUP_ID, thread_id)
        except: pass 
        
        paid_collection.update_one({"uid": target_uid}, {"$set": {"status": 0}, "$unset": {"topic_type": ""}})
        
    elif call.data == 'vid_bad':
        markup = InlineKeyboardMarkup(row_width=1).add(
            InlineKeyboardButton("👤 Не видно лицо", callback_data="rej_vid_face"),
            InlineKeyboardButton("🤐 Не та фраза / Нет времени", callback_data="rej_vid_phrase"),
            InlineKeyboardButton("🔇 Нет звука / Тишина", callback_data="rej_vid_sound")
        )
        try: bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
        except: pass

@bot.callback_query_handler(func=lambda call: call.data.startswith('rej_'))
def handle_rejections(call):
    if str(call.message.chat.id) != STAFF_GROUP_ID: return
    try: bot.answer_callback_query(call.id)
    except: pass
        
    thread_id = call.message.message_thread_id
    user_data = paid_collection.find_one({"thread_id": thread_id})
    if not user_data: return 
    target_uid = user_data["uid"]
        
    reasons_user = {
        "rej_doc_blur": "❌ **Документ не принят.**\nФотография размыта или имеет сильные засветы. Пожалуйста, сделайте более четкое фото и отправьте снова.",
        "rej_doc_hidden": "❌ **Документ не принят.**\nСкрыты необходимые данные. Повторите отправку, оставив открытыми **дату рождения и лицо**.",
        "rej_doc_wrong": "❌ **Документ не принят.**\nПредоставленный документ не входит в официальный перечень. Пришлите паспорт, ВУ, ВНЖ или военный билет.",
        "rej_vid_face": "❌ **Видео-кружок не принят.**\nНа видео плохо видно ваше лицо (темно или обрезано). Запишите кружок при хорошем освещении.",
        "rej_vid_phrase": "❌ **Видео-кружок не принят.**\nВы произнесли не ту фразу. Пожалуйста, посмотрите вашу секретную фразу выше и запишите кружок снова.",
        "rej_vid_sound": "❌ **Видео-кружок не принят.**\nНа видео отсутствует звук. Проверьте микрофон устройства и отправьте кружок повторно."
    }
    
    reasons_admin = {"rej_doc_blur": "Размыто", "rej_doc_hidden": "Скрыты данные", "rej_doc_wrong": "Не тот документ", "rej_vid_face": "Не видно лицо", "rej_vid_phrase": "Неверная фраза", "rej_vid_sound": "Нет звука"}
    text_to_user = reasons_user.get(call.data)
    admin_report = reasons_admin.get(call.data)
    
    if text_to_user:
        try: bot.send_message(target_uid, text_to_user, parse_mode="Markdown")
        except: pass
        try: bot.edit_message_caption(f"❌ *Отклонено (Причина: {admin_report}). Запрошено повторно.*", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=None)
        except Exception:
            try:
                bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
                bot.send_message(call.message.chat.id, f"❌ *Отклонено (Причина: {admin_report}). Запрошено повторно.*", message_thread_id=thread_id, parse_mode="Markdown")
            except: pass

# ================= КАПКАНЫ И БАНЫ =================
@bot.callback_query_handler(func=lambda call: call.data.startswith('trap_'))
def handle_trap(call):
    if str(call.message.chat.id) != STAFF_GROUP_ID: return
    try: bot.answer_callback_query(call.id, "Обработка...")
    except: pass
    
    target_uid = int(call.data.split('_')[1])
    thread_id = call.message.message_thread_id
    user_data = paid_collection.find_one({"uid": target_uid}) or {"uid": target_uid, "strikes": 0, "immunity": 0}
    
    if user_data.get("immunity", 0) > 0:
        paid_collection.update_one({"uid": target_uid}, {"$inc": {"immunity": -1}, "$unset": {"topic_type": ""}})
        try: bot.send_message(target_uid, "⛔️ **Вы нарушили правила!**\n\nБот попытался выдать вам Штрафной Страйк, но ваш **🛡 Щит Иммунитета поглотил удар!**\n_Щит разрушен. Будьте осторожны в следующий раз._", parse_mode="Markdown")
        except: pass
        try: bot.edit_message_text(f"{call.message.html}\n\n🛡 <b>Юзер спасен Иммунитетом!</b> Страйк поглощен щитом. Топик закрыт.", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML", reply_markup=None)
        except: pass
        try: bot.close_forum_topic(STAFF_GROUP_ID, thread_id)
        except: pass
        return

    new_strikes = user_data.get("strikes", 0) + 1
    paid_collection.update_one({"uid": target_uid}, {"$set": {"strikes": new_strikes}, "$unset": {"topic_type": ""}}, upsert=True)
    
    try: bot.send_message(target_uid, f"⛔️ **Вы выбрали раздел 'Реклама' для обхода системы.**\nВам начислен штрафной страйк за спам ({new_strikes}/3)! Для разбана используйте платную поддержку.")
    except: pass
    try: bot.edit_message_text(f"{call.message.html}\n\n🚨 <b>Хитрец пойман!</b> Ему начислен страйк ({new_strikes}/3). Топик закрыт.", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML", reply_markup=None)
    except: pass
    try: bot.close_forum_topic(STAFF_GROUP_ID, thread_id)
    except: pass

@bot.callback_query_handler(func=lambda call: call.data.startswith('ban_'))
def handle_fast_ban(call):
    if str(call.message.chat.id) != STAFF_GROUP_ID: return
    try: bot.answer_callback_query(call.id)
    except: pass
    
    target_uid = int(call.data.split('_')[1])
    thread_id = call.message.message_thread_id
    paid_collection.update_one({"uid": target_uid}, {"$set": {"strikes": 3, "status": 0}, "$unset": {"topic_type": ""}}, upsert=True)
    
    try: bot.send_message(target_uid, "⛔️ **Вы были заблокированы администратором за нарушение правил общения.**")
    except: pass
    try: bot.edit_message_text(f"{call.message.html}\n\n🚷 <b>Юзер заблокирован администратором! Топик закрыт.</b>", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML")
    except: pass
    try: bot.close_forum_topic(STAFF_GROUP_ID, thread_id)
    except: pass

# ================= РАЗНОЕ (ОЦЕНКА, ЗАКРЫТИЕ, АРТЕФАКТЫ, ПРЕМИУМ) =================
@bot.callback_query_handler(func=lambda call: call.data == "close_ticket")
def handle_close_ticket(call):
    if str(call.message.chat.id) != STAFF_GROUP_ID: return
    try: bot.answer_callback_query(call.id)
    except: pass
        
    thread_id = call.message.message_thread_id
    user_data = paid_collection.find_one({"thread_id": thread_id})
    if not user_data: return 
    target_uid = user_data["uid"]
        
    markup = InlineKeyboardMarkup(row_width=5).add(
        InlineKeyboardButton("1⭐", callback_data=f"rate_1_{thread_id}"), InlineKeyboardButton("2⭐", callback_data=f"rate_2_{thread_id}"),
        InlineKeyboardButton("3⭐", callback_data=f"rate_3_{thread_id}"), InlineKeyboardButton("4⭐", callback_data=f"rate_4_{thread_id}"),
        InlineKeyboardButton("5⭐", callback_data=f"rate_5_{thread_id}")
    )
    admin_username = call.from_user.username or call.from_user.first_name
    db['ticket_ratings'].update_one({"thread_id": thread_id}, {"$set": {"admin": admin_username, "uid": target_uid}}, upsert=True)
    
    try: bot.send_message(target_uid, "🏁 **Ваше обращение закрыто.**\n\nПожалуйста, оцените работу службы поддержки. Нам важно ваше мнение! 👇", reply_markup=markup)
    except: pass

    now_str = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
    archive_collection.update_one({"target": str(target_uid)}, {"$push": {"history": {"date": now_str, "action": "Обращение закрыто", "reason": "Вопрос решен админом"}}}, upsert=True)
    
    try: bot.edit_message_text(f"{call.message.html}\n\n🏁 <b>Тикет закрыт.</b> Пользователю отправлен запрос оценки.", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML")
    except: 
        try: bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
        except: pass
    
    try: bot.close_forum_topic(STAFF_GROUP_ID, thread_id)
    except: pass
    paid_collection.update_one({"uid": target_uid}, {"$set": {"status": 0}, "$unset": {"topic_type": ""}})

@bot.callback_query_handler(func=lambda call: call.data.startswith('rate_'))
def handle_rating(call):
    _, rating, t_id = call.data.split('_')
    t_id = int(t_id)
    try: bot.answer_callback_query(call.id, f"Спасибо за вашу оценку {rating}⭐!")
    except: pass
    
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("💸 Отправить чаевые админам (Донат) ⭐️", callback_data="start_donate"))
    try: bot.edit_message_text(f"🙏 Спасибо за оценку {rating}⭐! Мы работаем для вас.", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
    except: pass
    
    rating_data = db['ticket_ratings'].find_one({"thread_id": t_id})
    admin_name = rating_data["admin"] if rating_data else "Неизвестный герой"
    mood = "🎉 Отличная работа!" if rating in ['4', '5'] else "⚠️ Нужно обратить внимание."
        
    try: bot.send_message(STAFF_GROUP_ID, f"🌟 **Получена новая оценка!**\n\n👨‍💻 Админ: @{admin_name}\n⭐️ Оценка: **{rating} из 5**\n{mood}", message_thread_id=t_id)
    except: pass

@bot.callback_query_handler(func=lambda call: call.data == "force_unban")
def handle_force_unban(call):
    if str(call.message.chat.id) != STAFF_GROUP_ID: return
    try: bot.answer_callback_query(call.id)
    except: pass
        
    thread_id = call.message.message_thread_id
    user_data = paid_collection.find_one({"thread_id": thread_id})
    if not user_data: return
    target_uid = user_data["uid"]
    
    now = datetime.datetime.now()
    ticket_num = now.strftime("%d%m%Y%H%M%S") + f"-{random.randint(100, 999)}"
    
    db['skynet_tasks'].insert_one({"uid": target_uid, "action": "full_unban", "timestamp": now})
    admin_username = call.from_user.username or call.from_user.first_name
    db['ticket_ratings'].update_one({"thread_id": thread_id}, {"$set": {"admin": admin_username, "uid": target_uid}}, upsert=True)
    
    try:
        bot.send_message(target_uid, f"🎉 **Ваши ограничения успешно сняты!** ❤️\n\n🔒 **Обращение закрыто. Уникальный номер:** `{ticket_num}`\n\n{NETWORK_LINKS}", parse_mode="Markdown", disable_web_page_preview=True)
        markup = InlineKeyboardMarkup(row_width=5).add(
            InlineKeyboardButton("1⭐", callback_data=f"rate_1_{thread_id}"), InlineKeyboardButton("2⭐", callback_data=f"rate_2_{thread_id}"),
            InlineKeyboardButton("3⭐", callback_data=f"rate_3_{thread_id}"), InlineKeyboardButton("4⭐", callback_data=f"rate_4_{thread_id}"),
            InlineKeyboardButton("5⭐", callback_data=f"rate_5_{thread_id}")
        )
        markup.add(InlineKeyboardButton("💸 Отправить чаевые админам (Донат) ⭐️", callback_data="start_donate"))
        bot.send_message(target_uid, "🏁 Пожалуйста, оцените работу службы поддержки. Нам важно ваше мнение! 👇", reply_markup=markup)
    except: pass
    
    archive_collection.update_one({"target": str(target_uid)}, {"$push": {"history": {"date": now.strftime("%d.%m.%Y %H:%M"), "action": "Разблокировка (Ручная)", "reason": "Вопрос решен админом"}}}, upsert=True)
    
    try: bot.edit_message_text(f"{call.message.html}\n\n🔓 <b>Пользователь разбанен!</b> Приказ передан Скайнету. Тикет закрыт: {ticket_num}", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML")
    except: 
        try: bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
        except: pass
    
    try: bot.close_forum_topic(STAFF_GROUP_ID, thread_id)
    except: pass
    paid_collection.update_one({"uid": target_uid}, {"$set": {"status": 0}, "$unset": {"topic_type": ""}})

@bot.message_handler(func=lambda message: str(message.chat.id) == STAFF_GROUP_ID and message.is_topic_message and not message.from_user.is_bot, content_types=['text', 'photo', 'video', 'document', 'voice', 'audio', 'sticker', 'video_note', 'animation'])
def handle_admin_replies(message):
    thread_id = message.message_thread_id
    user_data = paid_collection.find_one({"thread_id": thread_id})
    if not user_data: return
    target_uid = user_data["uid"]

    paid_collection.update_one({"uid": target_uid}, {"$set": {"topic_type": "manual"}})
    try: bot.copy_message(target_uid, STAFF_GROUP_ID, message.message_id)
    except: logger.warning(f"Ошибка ручного ответа админа юзеру {target_uid}")

@bot.message_handler(commands=['give'])
def handle_give_cmd(message):
    if str(message.chat.id) != STAFF_GROUP_ID: return
        
    args = message.text.split()
    if len(args) != 4:
        try: bot.reply_to(message, "❌ **Ошибка формата!**\nИспользуйте: `/give [ID] [points/shards] [сумма]`\n\n*Пример:* `/give 123456789 points 100`", parse_mode="Markdown")
        except: pass
        return
        
    try:
        target_uid = int(args[1])
        currency = args[2].lower()
        amount = int(args[3])
        
        if currency in ['points', 'очки']:
            paid_collection.update_one({"uid": target_uid}, {"$inc": {"bounty_points": amount}}, upsert=True)
            bot.reply_to(message, f"✅ Выдано **{amount} Очков Бдительности** пользователю `{target_uid}`.", parse_mode="Markdown")
            try: bot.send_message(target_uid, f"🎁 **Бонус от администрации!**\nВам начислено: **{amount} Очков Бдительности**.", parse_mode="Markdown")
            except: pass
            
        elif currency in ['shards', 'осколки']:
            paid_collection.update_one({"uid": target_uid}, {"$inc": {"jackpot_shards": amount}}, upsert=True)
            bot.reply_to(message, f"✅ Выдано **{amount} Осколков** пользователю `{target_uid}`.", parse_mode="Markdown")
            try: bot.send_message(target_uid, f"🧩 **Бонус от администрации!**\nВам начислено: **{amount} Осколков рулетки**.", parse_mode="Markdown")
            except: pass
        else:
            bot.reply_to(message, "❌ Неизвестная валюта. Используйте `points` (очки) или `shards` (осколки).")
    except ValueError:
        try: bot.reply_to(message, "❌ Ошибка: ID пользователя и сумма должны быть числами.")
        except: pass

# ================= АРТЕФАКТЫ И ТЕГИ =================
@bot.callback_query_handler(func=lambda call: call.data == 'claim_custom_tag')
def handle_claim_tag(call):
    try: bot.answer_callback_query(call.id)
    except: pass
    try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except: pass

    try:
        msg = bot.send_message(call.message.chat.id, "✍️ **Создание личного тега**\n\nПридумайте и напишите ваш новый статус (максимум 15 символов).\n_Внимание: Тег будет проверен модератором!_")
        bot.register_next_step_handler(msg, process_tag_input)
    except: pass

def process_tag_input(message):
    if message.text == '/start':
        send_welcome(message)
        return
        
    tag_text = message.text.strip()
    if len(tag_text) > 15:
        try: bot.send_message(message.chat.id, "❌ **Слишком длинный тег!** Максимум 15 символов. Нажмите на кнопку в сообщении с выигрышем еще раз.")
        except: pass
        return
        
    uid = message.from_user.id
    name = message.from_user.first_name
    db['temp_tags'].update_one({"uid": uid}, {"$set": {"tag": tag_text, "name": name}}, upsert=True)
    
    try: bot.send_message(message.chat.id, f"⏳ Ваш тег **«{tag_text}»** отправлен на проверку администраторам. Ожидайте!")
    except: pass
    
    markup = InlineKeyboardMarkup(row_width=2).add(InlineKeyboardButton("✅ Одобрить", callback_data=f"adm_tag_ok_{uid}"), InlineKeyboardButton("❌ Отклонить", callback_data=f"adm_tag_rej_{uid}"))
    try: bot.send_message(STAFF_GROUP_ID, f"👑 **ЗАПРОС НА КАСТОМНЫЙ ТЕГ**\n\n👤 От: {name} (`{uid}`)\n📝 Желаемый тег: **{tag_text}**\n\nОдобрить установку?", parse_mode="Markdown", reply_markup=markup)
    except: pass

@bot.callback_query_handler(func=lambda call: call.data.startswith('adm_tag_'))
def handle_admin_tag_decision(call):
    if str(call.message.chat.id) != STAFF_GROUP_ID: return
    try: bot.answer_callback_query(call.id)
    except: pass
    
    action = call.data.split('_')[2]
    target_uid = int(call.data.split('_')[3])
    
    tag_data = db['temp_tags'].find_one({"uid": target_uid})
    if not tag_data:
        try: bot.edit_message_text("❌ Данные устарели или уже обработаны.", chat_id=call.message.chat.id, message_id=call.message.message_id)
        except: pass
        return
        
    tag_text = tag_data["tag"]
    if action == "ok":
        db['users'].update_one({"_id": target_uid}, {"$set": {"custom_tag": tag_text}}, upsert=True)
        try: bot.edit_message_text(f"{call.message.text}\n\n✅ **ВЕРДИКТ: ОДОБРЕНО**", chat_id=call.message.chat.id, message_id=call.message.message_id)
        except: pass
        try: bot.send_message(target_uid, f"🎉 **Поздравляем!**\nВаш личный тег **«{tag_text}»** успешно одобрен и установлен во всех чатах сети!")
        except: pass
    elif action == "rej":
        try: bot.edit_message_text(f"{call.message.text}\n\n❌ **ВЕРДИКТ: ОТКЛОНЕНО**", chat_id=call.message.chat.id, message_id=call.message.message_id)
        except: pass
        markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✍️ Придумать другой тег", callback_data="claim_custom_tag"))
        try: bot.send_message(target_uid, f"❌ **Ваш тег «{tag_text}» был отклонен модератором.**\nПожалуйста, придумайте что-то другое, не нарушающее правила.", reply_markup=markup)
        except: pass
    db['temp_tags'].delete_one({"uid": target_uid})

@bot.callback_query_handler(func=lambda call: call.data == 'claim_premium')
def handle_claim_premium(call):
    try: bot.answer_callback_query(call.id)
    except: pass
    try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except: pass

    try:
        msg = bot.send_message(call.message.chat.id, "🎁 **Получение Telegram Premium**\n\nПожалуйста, напишите ваш @username или номер телефона (привязанный к Telegram), чтобы администратор смог отправить вам подарок:")
        bot.register_next_step_handler(msg, process_premium_claim)
    except: pass

def process_premium_claim(message):
    if message.text == '/start':
        send_welcome(message)
        return
    uid, name, username = message.from_user.id, message.from_user.first_name, f"@{message.from_user.username}" if message.from_user.username else f"ID {message.from_user.id}"
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ Выдано", callback_data=f"prem_done_{uid}"))
    try:
        bot.send_message(STAFF_GROUP_ID, f"🏆 **СОРВАН ДЖЕКПОТ (TELEGRAM PREMIUM)** 🏆\n\n👤 Победитель: {name} ({username})\n📝 Реквизиты для подарка:\n`{message.text}`\n\nАдмины, подарите подписку и закройте тикет!", reply_markup=markup)
        bot.send_message(message.chat.id, "✅ Заявка на получение Premium отправлена администрации! С вами скоро свяжутся.")
    except: pass

@bot.callback_query_handler(func=lambda call: call.data.startswith('prem_done_'))
def handle_prem_done(call):
    if str(call.message.chat.id) != STAFF_GROUP_ID: return
    try: bot.answer_callback_query(call.id)
    except: pass
    target_uid = int(call.data.split('_')[2])
    try: bot.edit_message_text(f"{call.message.text}\n\n✅ **ВЫДАНО**", chat_id=call.message.chat.id, message_id=call.message.message_id)
    except: pass
    try: bot.send_message(target_uid, "🎉 Администрация подтвердила выдачу Telegram Premium! Наслаждайтесь!")
    except: pass

@bot.callback_query_handler(func=lambda call: call.data.startswith('use_arrest_'))
def handle_use_arrest(call):
    try: bot.answer_callback_query(call.id)
    except: pass
    try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except: pass

    code = call.data.split('_')[2]
    promo = db['promocodes'].find_one({"_id": code, "is_active": True, "used_count": 0})
    if not promo:
        try: bot.send_message(call.message.chat.id, "❌ Этот ордер уже был использован или не существует.")
        except: pass
        return
        
    try:
        msg = bot.send_message(call.message.chat.id, f"🚓 **Использование Ордера: {code}**\n\nНапишите @username или ID пользователя, которого нужно отправить в мут на 1 час (и укажите причину):")
        bot.register_next_step_handler(msg, process_arrest_claim, code=code)
    except: pass

def process_arrest_claim(message, code):
    if message.text == '/start':
        send_welcome(message)
        return
    uid, name, username = message.from_user.id, message.from_user.first_name, f"@{message.from_user.username}" if message.from_user.username else f"ID {message.from_user.id}"
    db['promocodes'].update_one({"_id": code}, {"$inc": {"used_count": 1}})
    
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ Исполнить (Замутить)", callback_data=f"arrest_done_{uid}"), InlineKeyboardButton("❌ Отклонить (Вернуть ордер)", callback_data=f"arrest_rej_{code}_{uid}"))
    try:
        bot.send_message(STAFF_GROUP_ID, f"🚓 **ПРИМЕНЕНИЕ АРТЕФАКТА (ОРДЕР)** 🚓\n\n👤 Исполнитель: {name} ({username})\n🔑 Код: `{code}`\n🎯 Цель и причина:\n`{message.text}`\n\nАдмины, проверьте цель и выдайте мут на 1 час!", reply_markup=markup)
        bot.send_message(message.chat.id, "✅ Ордер передан Администрации! Если всё верно, цель скоро получит мут.")
    except: pass

@bot.callback_query_handler(func=lambda call: call.data.startswith('arrest_'))
def handle_arrest_decision(call):
    if str(call.message.chat.id) != STAFF_GROUP_ID: return
    try: bot.answer_callback_query(call.id)
    except: pass
    parts = call.data.split('_')
    action = parts[1]
    
    if action == "done":
        target_uid = int(parts[2])
        try: bot.edit_message_text(f"{call.message.text}\n\n✅ **ИСПОЛНЕНО**", chat_id=call.message.chat.id, message_id=call.message.message_id)
        except: pass
        try: bot.send_message(target_uid, "⚖️ Ваш ордер на арест успешно исполнен. Нарушитель наказан!")
        except: pass
    elif action == "rej":
        code, target_uid = parts[2], int(parts[3])
        db['promocodes'].update_one({"_id": code}, {"$inc": {"used_count": -1}})
        try: bot.edit_message_text(f"{call.message.text}\n\n❌ **ОТКЛОНЕНО (Код возвращен юзеру)**", chat_id=call.message.chat.id, message_id=call.message.message_id)
        except: pass
        try: bot.send_message(target_uid, f"❌ Администрация отклонила применение ордера (возможно, вы попытались замутить админа). Ваш ордер `{code}` снова активен!")
        except: pass