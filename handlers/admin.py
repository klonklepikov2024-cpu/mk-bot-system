import random
import datetime
import requests
import tempfile
import re
import os
import json
import time
import base64
import threading
from config import GROQ_API_KEY
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
    
    # 🔥 ОБНОВЛЯЕМ ТАЙМЕР АКТИВНОСТИ ПРИ ЛЮБОМ СООБЩЕНИИ 🔥
    paid_collection.update_one({"uid": uid}, {"$set": {"last_activity": datetime.datetime.now()}}, upsert=True)

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
            except Exception as e: logger.debug(f"Игнор ошибки: {e}")

    try:
        # ТЕКСТ
        if message.content_type == 'text':
            if message.text.lower() in ["готов", "готова", "готовы", "готов(а)"]:
                code_words = ["ЯБЛОКО", "ТИГР", "СОЛНЦЕ", "МОРЕ", "СОКОЛ", "РАКЕТА", "ВЕТЕР", "МАЯК"]
                secret_code = f"{random.choice(code_words)}-{random.randint(10, 99)}"
                
                paid_collection.update_one({"uid": uid}, {"$set": {"verif_timer": datetime.datetime.now(), "secret_code": secret_code}})
                
                text_phrase = f"⏳ **Таймер запущен! У вас ровно 5 минут.**\n\nЗапишите **видео-кружок**, на котором четко видно лицо, и произнесите:\n\n💬 *«Привет команде МК, я из *города* на часах: *хх:хх* часов. Мой код: {secret_code}»*."
                bot.send_message(uid, text_phrase, parse_mode="Markdown")
                bot.send_message(STAFF_GROUP_ID, f"⏳ *Пользователь написал «Готов». Бот выдал код: {secret_code} и запустил таймер 5 минут! Ждем кружок.*", message_thread_id=thread_id, parse_mode="Markdown")
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
                    InlineKeyboardButton("💎 Спонсор", callback_data="tpl_sponsor"),
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

            # 🔥 ЗАПУСКАЕМ АВТОПИЛОТ ИИ ТОЛЬКО ДЛЯ РАЗБАНОВ 🔥
            if topic_type == "unban":
                threading.Thread(
                    target=process_ticket_with_ai, 
                    args=(uid, message.text, thread_id)
                ).start()
        
        # ФОТО И ДОКУМЕНТЫ
        elif message.content_type in ['photo', 'document']:
            
            # 🔥 ЕДИНЫЙ РАЗУМ: Записываем в память, что юзер скинул файл
            paid_collection.update_one({"uid": uid}, {"$push": {"dialog_history": {"role": "user", "content": "[Пользователь отправил фото/документ на проверку]"}}})
            
            markup = InlineKeyboardMarkup(row_width=1)
            markup.add(InlineKeyboardButton("✅ Документ принят (Запросить видео)", callback_data="doc_ok"), InlineKeyboardButton("❌ Плохое фото (Перезапросить)", callback_data="doc_bad"))
            
            if message.content_type == 'photo':
                file_id = message.photo[-1].file_id 
                
                # 🔥 УМНЫЙ ПОИСК: Ищем самое большое фото, но СТРОГО до 80 КБ
                ai_file_id = message.photo[0].file_id # Дефолт - самая маленькая
                for p in message.photo[::-1]:
                    if p.file_size and p.file_size < 80000:
                        ai_file_id = p.file_id
                        break
                
                bot.send_photo(STAFF_GROUP_ID, file_id, caption="📸 **Пользователь прислал фото!**\nПроверьте документ:", message_thread_id=thread_id, parse_mode="Markdown", reply_markup=markup)
            else:
                file_id = message.document.file_id
                
                # 🔥 ЕСЛИ ДОКУМЕНТ: Берем его превьюшку (она всегда легкая)
                ai_file_id = message.document.thumb.file_id if message.document.thumb else file_id 
                
                bot.send_document(STAFF_GROUP_ID, file_id, caption="📄 **Пользователь прислал документ!**\nПроверьте:", message_thread_id=thread_id, parse_mode="Markdown", reply_markup=markup)
            
            threading.Thread(
                target=analyze_document_vision, 
                args=(ai_file_id, thread_id, uid) 
            ).start()
          
        # КРУЖКИ
        elif message.content_type == 'video_note':
            
            # 🔥 ЕДИНЫЙ РАЗУМ: Записываем в память, что юзер скинул видео
            paid_collection.update_one({"uid": uid}, {"$push": {"dialog_history": {"role": "user", "content": "[Пользователь отправил видео-кружок на проверку]"}}})
            # 🔥 АНТИ-ФЕЙК: ПРОВЕРКА НА ПЕРЕСЛАННЫЙ КРУЖОК 🔥
            if getattr(message, 'forward_date', None) or getattr(message, 'forward_origin', None):
                bot.send_message(
                    uid, 
                    "❌ **Ошибка верификации!**\n\nСистема обнаружила, что вы отправили пересланное видео. Для верификации необходимо записать кружок прямо сейчас, глядя в камеру.", 
                    parse_mode="Markdown"
                )
                try:
                    bot.send_message(
                        STAFF_GROUP_ID, 
                        f"🚨 **ПОПЫТКА ОБМАНА (ФЕЙК-КРУЖОК)!**\nПользователь `{uid}` попытался пройти верификацию чужим/пересланным видео.\n\nЗаявка автоматически отклонена.", 
                        message_thread_id=thread_id,
                        parse_mode="Markdown"
                    )
                except Exception as e: 
                    logger.debug(f"Игнор ошибки: {e}")
                return # 🛑 Жестко прерываем функцию, видео до нейросети не дойдет!

            # Запрет слишком коротких видео (меньше 2 секунд)
            if message.video_note.duration < 2:
                bot.send_message(uid, "❌ **Ошибка:** Кружок слишком короткий. Пожалуйста, запишите полноценное видео, четко проговорив всю фразу.")
                return

            # Проверка таймера
            verif_timer = user_data.get("verif_timer")
            is_expired = False
            if verif_timer:
                time_diff = (datetime.datetime.now() - verif_timer).total_seconds()
                paid_collection.update_one({"uid": uid}, {"$unset": {"verif_timer": ""}}) # Снимаем таймер
                if time_diff > 300:
                    is_expired = True
                    bot.send_message(uid, "❌ **Время вышло!** Вы не уложились в 5 минут. Ожидайте ручной проверки администратором.")
                    bot.send_message(STAFF_GROUP_ID, "⚠️ **ВНИМАНИЕ! Юзер просрочил таймер. Автоматическая проверка отключена.**", message_thread_id=thread_id)
            
            # Получаем код для вывода админам
            secret_code = user_data.get("secret_code", "Неизвестен")
            
            # 🔥 ВЫТАСКИВАЕМ ПРЕВЬЮШКУ ВИДЕО 🔥
            thumb_file_id = message.video_note.thumb.file_id if message.video_note.thumb else None
            
            markup = InlineKeyboardMarkup(row_width=1).add(InlineKeyboardButton("✅ Кружок принят (РАЗБАН)", callback_data="vid_ok"), InlineKeyboardButton("❌ Плохое видео (Перезапросить)", callback_data="vid_bad"))
            
            sent_video = bot.send_video_note(STAFF_GROUP_ID, message.video_note.file_id, message_thread_id=thread_id, reply_markup=markup)
            bot.send_message(STAFF_GROUP_ID, f"🎥 **Пользователь прислал кружок!**\n\n🗣 **ОН ДОЛЖЕН СКАЗАТЬ:**\n_{secret_code}_", message_thread_id=thread_id, parse_mode="Markdown")

            # 🛑 ЖЕСТКИЙ СТОП: Если таймер вышел, видео уходит админам, но ИИ его НЕ слушает!
            if is_expired:
                return 

            # 🔥 ПЕРЕДАЕМ ПРЕВЬЮШКУ В ФУНКЦИЮ ИИ 🔥
            threading.Thread(
                target=analyze_video_speech, 
                args=(message.video_note.file_id, secret_code, thread_id, uid, sent_video.message_id, thumb_file_id)
            ).start()

        # ПРОЧЕЕ
        elif message.content_type in ['voice', 'video', 'sticker', 'audio', 'animation']:
            bot.copy_message(STAFF_GROUP_ID, message.chat.id, message.message_id, message_thread_id=thread_id)

    except Exception as e:
        logger.error(f"СИСТЕМНАЯ ОШИБКА ДОСТАВКИ (Юзер -> Админ): {e}")
        try: bot.send_message(message.chat.id, "⚠️ Произошла ошибка при отправке сообщения. Пожалуйста, отправьте его еще раз.")
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")

# ================= ПРОВЕРКА ДОКУМЕНТОВ =================
@bot.callback_query_handler(func=lambda call: call.data in ['doc_ok', 'doc_bad'])
def handle_doc_check(call):
    if str(call.message.chat.id) != str(STAFF_GROUP_ID): return
    try: bot.answer_callback_query(call.id)
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        
    thread_id = call.message.message_thread_id
    user_data = paid_collection.find_one({"thread_id": thread_id})
    if not user_data: 
        try: bot.answer_callback_query(call.id, "❌ Топик уже закрыт или данные устарели", show_alert=True)
        except: pass
        return 
    target_uid = user_data["uid"]
        
    if call.data == 'doc_ok':
        code_words = ["ЯБЛОКО", "ТИГР", "СОЛНЦЕ", "МОРЕ", "СОКОЛ", "РАКЕТА", "ВЕТЕР", "МАЯК"]
        secret_code = f"{random.choice(code_words)}-{random.randint(10, 99)}"
        paid_collection.update_one({"uid": target_uid}, {"$set": {"verif_timer": datetime.datetime.now(), "secret_code": secret_code}})
        
        text_to_user = f"✅ **Документ принят! Отлично.**\n\nВторой этап верификации:\nЗапишите **видео-кружок**, на котором будет четко видно ваше лицо, и произнесите фразу:\n\n💬 *«Привет команде МК, я из *города* на часах: *хх:хх* часов. Мой код: {secret_code}»*.\n\nУ вас есть 5 минут на отправку видео."
        try: bot.send_message(target_uid, text_to_user, parse_mode="Markdown")
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        try: bot.edit_message_caption(f"✅ *Документ одобрен. Запрошен видео-кружок с кодом: {secret_code}.*", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=None)
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        
    elif call.data == 'doc_bad':
        markup = InlineKeyboardMarkup(row_width=1).add(
            InlineKeyboardButton("🔎 Размыто / Засветы", callback_data="rej_doc_blur"),
            InlineKeyboardButton("🙈 Скрыты нужные данные", callback_data="rej_doc_hidden"),
            InlineKeyboardButton("📄 Не тот документ", callback_data="rej_doc_wrong")
        )
        try: bot.edit_message_caption("❓ **Укажите причину отказа:**", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=markup)
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")

# ================= КНОПКИ АДМИНА И ШТРАФЫ =================
@bot.callback_query_handler(func=lambda call: call.data.startswith('tpl_') or call.data.startswith('fine_'))
def handle_admin_templates(call):
    if str(call.message.chat.id) != str(STAFF_GROUP_ID): return
    try: bot.answer_callback_query(call.id)
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        
    thread_id = call.message.message_thread_id
    user_data = paid_collection.find_one({"thread_id": thread_id})
    if not user_data: 
        try: bot.answer_callback_query(call.id, "❌ Топик уже закрыт или данные устарели", show_alert=True)
        except: pass
        return
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

    # Сначала ищем шаблон в облаке Mongo, если нет - берем из файла
    db_tpl = db['bot_templates'].find_one({"_id": call.data})
    template_text = db_tpl["text"] if db_tpl else TEMPLATES.get(call.data)
    
    if template_text:
        try:
            bot.send_message(target_uid, template_text, parse_mode="Markdown")
            bot.send_message(STAFF_GROUP_ID, f"🟢 *Скайнет отправил шаблон:*\n_{template_text.splitlines()[0]}_", message_thread_id=thread_id, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Ошибка отправки шаблона: {e}")
            try: bot.send_message(STAFF_GROUP_ID, "⚠️ **ОШИБКА:** Невозможно отправить шаблон. Пользователь заблокировал бота!", message_thread_id=thread_id, parse_mode="Markdown")
            except Exception as e: logger.debug(f"Игнор ошибки: {e}")

def process_custom_fine(message, target_uid, thread_id, call_msg):
    if not message.text or not message.text.isdigit():
        try: bot.send_message(STAFF_GROUP_ID, "❌ **Ошибка:** Нужно было отправить только число (например: 350).", message_thread_id=thread_id, parse_mode="Markdown")
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        return
        
    amount = int(message.text)
    if amount < 1 or amount > 10000:
        try: bot.send_message(STAFF_GROUP_ID, "❌ **Ошибка:** Сумма должна быть от 1 до 10000 звезд.", message_thread_id=thread_id, parse_mode="Markdown")
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
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

@bot.callback_query_handler(func=lambda call: call.data == "buy_indulgence")
def handle_buy_indulgence(call):
    try: bot.answer_callback_query(call.id)
    except: pass
    
    uid = call.from_user.id
    amount = 2000
    cost_in_rub = amount * 2
    
    # Проверяем кэшбэк-баланс юзера (как при штрафах)
    user_data_pay = paid_collection.find_one({"uid": uid}) or {}
    cb_balance = user_data_pay.get("cashback_balance", 0)
    
    # Генерируем ссылки CryptoBot
    url_usdt = get_crypto_pay_url(f"indulgence_{uid}", amount, "Покупка Индульгенции (Снятие бана)", asset="USDT")
    url_ton = get_crypto_pay_url(f"indulgence_{uid}", amount, "Покупка Индульгенции (Снятие бана)", asset="TON")
    
    markup = InlineKeyboardMarkup(row_width=1)
    
    # Кнопки оплаты
    if cb_balance >= cost_in_rub:
        markup.add(InlineKeyboardButton(f"💰 Оплатить с баланса ({cost_in_rub}₽)", callback_data=f"checkout_balance_indulgence_{amount}"))
    elif cb_balance > 0:
        remaining_stars = amount - (cb_balance // 2)
        markup.add(InlineKeyboardButton(f"💳 Списать {cb_balance}₽ и доплатить {remaining_stars}⭐️", callback_data=f"checkout_partial_indulgence_{amount}_{cb_balance}"))
    else:
        markup.add(InlineKeyboardButton(f"💳 Оплатить 2000⭐️", callback_data=f"checkout_pay_indulgence_{amount}"))
    
    if url_usdt: markup.add(InlineKeyboardButton("🟢 USDT (CryptoBot)", url=url_usdt))
    if url_ton: markup.add(InlineKeyboardButton("💎 TON (CryptoBot)", url=url_ton))
    
    # Возврат назад в меню
    markup.add(InlineKeyboardButton("🔙 Назад", callback_data="back_to_start"))

    text = (
        "📜 **ПОКУПКА ИНДУЛЬГЕНЦИИ**\n\n"
        "Эта опция позволяет мгновенно снять **ВСЕ** текущие ограничения и штрафы без вопросов, "
        "общения со службой поддержки и записи видео-кружков.\n\n"
        "✨ Бонус: Вы получите уникальный статус **📜 Индульгенция** во всех чатах.\n\n"
        "💰 Стоимость: **2000⭐️**"
    )
    
    try:
        bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup, parse_mode="Markdown")
    except Exception as e:
        logger.debug(f"Ошибка вывода индульгенции: {e}")

# ================= ПРОВЕРКА КРУЖКА =================
@bot.callback_query_handler(func=lambda call: call.data in ['vid_ok', 'vid_bad'])
def handle_vid_check(call):
    if str(call.message.chat.id) != str(STAFF_GROUP_ID): return
    try: bot.answer_callback_query(call.id)
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        
    thread_id = call.message.message_thread_id
    user_data = paid_collection.find_one({"thread_id": thread_id})
    if not user_data: 
        try: bot.answer_callback_query(call.id, "❌ Топик уже закрыт или данные устарели", show_alert=True)
        except: pass
        return 
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
        
        archive_collection.update_one(
            {"target": str(target_uid)}, 
            {"$push": {
                "history": {
                    "date": now.strftime("%d.%m.%Y %H:%M"), 
                    "action": "Успешная верификация", 
                    "reason": "Кружок принят админом",
                    "evidence_summary": "Видео-кружок с кодом подтверждён"
                }
            }}, 
            upsert=True
        )
        
        try: bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        try: bot.send_message(call.message.chat.id, f"✅ *Видео-кружок одобрен!*\nЮзер верифицирован. Приказ на размут передан Скайнету. Тикет закрыт: `{ticket_num}`", message_thread_id=thread_id, parse_mode="Markdown")
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        try: bot.close_forum_topic(STAFF_GROUP_ID, thread_id)
        except Exception as e: logger.debug(f"Игнор ошибки: {e}") 
        
        paid_collection.update_one({"uid": target_uid}, {"$set": {"status": 0}, "$unset": {"topic_type": ""}})
        
    elif call.data == 'vid_bad':
        markup = InlineKeyboardMarkup(row_width=1).add(
            InlineKeyboardButton("👤 Не видно лицо", callback_data="rej_vid_face"),
            InlineKeyboardButton("🤐 Не та фраза / Нет времени", callback_data="rej_vid_phrase"),
            InlineKeyboardButton("🔇 Нет звука / Тишина", callback_data="rej_vid_sound"),
            InlineKeyboardButton("⏳ Просрочен таймер (Штраф 650⭐️)", callback_data="rej_vid_timeout")
        )
        try: bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('rej_'))
def handle_rejections(call):
    if str(call.message.chat.id) != str(STAFF_GROUP_ID): return
    try: bot.answer_callback_query(call.id)
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        
    thread_id = call.message.message_thread_id
    user_data = paid_collection.find_one({"thread_id": thread_id})
    if not user_data: 
        try: bot.answer_callback_query(call.id, "❌ Топик уже закрыт или данные устарели", show_alert=True)
        except: pass
        return 
    target_uid = user_data["uid"]
        
    reasons_user = {
        "rej_doc_blur": "❌ **Документ не принят.**\nФотография размыта или имеет сильные засветы. Пожалуйста, сделайте более четкое фото и отправьте снова.",
        "rej_doc_hidden": "❌ **Документ не принят.**\nСкрыты необходимые данные. Повторите отправку, оставив открытыми **дату рождения и лицо**.",
        "rej_doc_wrong": "❌ **Документ не принят.**\nПредоставленный документ не входит в официальный перечень. Пришлите паспорт, ВУ, ВНЖ или военный билет.",
        "rej_vid_face": "❌ **Видео-кружок не принят.**\nНа видео плохо видно ваше лицо (темно или обрезано). Запишите кружок при хорошем освещении.",
        "rej_vid_phrase": "❌ **Видео-кружок не принят.**\nВы произнесли не ту фразу. Пожалуйста, посмотрите вашу секретную фразу выше и запишите кружок снова.",
        "rej_vid_sound": "❌ **Видео-кружок не принят.**\nНа видео отсутствует звук. Проверьте микрофон устройства и отправьте кружок повторно.",
        "rej_vid_timeout": "❌ **Видео-кружок не принят.**\nВы не уложились в отведенный таймер (5 минут) или прислали старое видео. Опция бесплатной верификации аннулирована.\n\n🔓 Для снятия ограничений необходимо оплатить штраф-взнос."
    }
    
    reasons_admin = {
        "rej_doc_blur": "Размыто", "rej_doc_hidden": "Скрыты данные", "rej_doc_wrong": "Не тот документ", 
        "rej_vid_face": "Не видно лицо", "rej_vid_phrase": "Неверная фраза", "rej_vid_sound": "Нет звука",
        "rej_vid_timeout": "Просрочен таймер (Выставлен штраф 650⭐️)"
    }
    
    text_to_user = reasons_user.get(call.data)
    admin_report = reasons_admin.get(call.data)
    
    if text_to_user:
        try: bot.send_message(target_uid, text_to_user, parse_mode="Markdown")
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")

        # 🔥 АВТОМАТИЧЕСКИЙ ШТРАФ ЗА ТАЙМЕР 🔥
        if call.data == "rej_vid_timeout":
            amount = 650
            try:
                user_data_pay = paid_collection.find_one({"uid": target_uid}) or {}
                cb_balance = user_data_pay.get("cashback_balance", 0)
                cost_in_rub = amount * 2
                
                url_usdt = get_crypto_pay_url(f"fine_{target_uid}", amount, f"Оплата штрафа ({amount}⭐️)", asset="USDT")
                url_ton = get_crypto_pay_url(f"fine_{target_uid}", amount, f"Оплата штрафа ({amount}⭐️)", asset="TON")
                
                fine_markup = InlineKeyboardMarkup(row_width=1).add(InlineKeyboardButton("🎫 У меня есть промокод", callback_data=f"checkout_promo_fine_{amount}"))
                
                if cb_balance >= cost_in_rub:
                    fine_markup.add(InlineKeyboardButton(f"💰 Оплатить с баланса ({cost_in_rub}₽)", callback_data=f"checkout_balance_fine_{amount}"))
                elif cb_balance > 0:
                    remaining_stars = amount - (cb_balance // 2)
                    fine_markup.add(InlineKeyboardButton(f"💳 Списать {cb_balance}₽ и доплатить {remaining_stars}⭐️", callback_data=f"checkout_partial_fine_{amount}_{cb_balance}"))
                else:
                    fine_markup.add(InlineKeyboardButton(f"💳 Оплатить {amount}⭐️", callback_data=f"checkout_pay_fine_{amount}"))
                
                if url_usdt: fine_markup.add(InlineKeyboardButton("🟢 USDT (CryptoBot)", url=url_usdt))
                if url_ton: fine_markup.add(InlineKeyboardButton("💎 TON (CryptoBot)", url=url_ton))
                    
                bot.send_message(target_uid, f"🧾 **Вам выставлен счет на оплату штрафа.**\n\nСумма к оплате: **{amount}⭐️**\nПосле оплаты ограничения будут сняты автоматически.", reply_markup=fine_markup, parse_mode="Markdown")
                bot.send_message(STAFF_GROUP_ID, f"🟢 *Скайнет автоматически выставил штраф {amount}⭐️ за просроченный таймер.*", message_thread_id=thread_id, parse_mode="Markdown")
            except Exception as e:
                logger.warning(f"Ошибка выставления штрафа за таймер: {e}")

        try: bot.edit_message_caption(f"❌ *Отклонено (Причина: {admin_report}). Запрошено повторно.*", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=None)
        except Exception:
            try:
                bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
                bot.send_message(call.message.chat.id, f"❌ *Отклонено (Причина: {admin_report}).*", message_thread_id=thread_id, parse_mode="Markdown")
            except Exception as e: logger.debug(f"Игнор ошибки: {e}")

# ================= КАПКАНЫ И БАНЫ =================
@bot.callback_query_handler(func=lambda call: call.data.startswith('trap_'))
def handle_trap(call):
    if str(call.message.chat.id) != str(STAFF_GROUP_ID): return
    try: bot.answer_callback_query(call.id, "Обработка...")
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")
    
    target_uid = int(call.data.split('_')[1])
    thread_id = call.message.message_thread_id
    user_data = paid_collection.find_one({"uid": target_uid}) or {"uid": target_uid, "strikes": 0, "immunity": 0}
    
    if user_data.get("immunity", 0) > 0:
        paid_collection.update_one({"uid": target_uid}, {"$inc": {"immunity": -1}, "$unset": {"topic_type": ""}})
        try: bot.send_message(target_uid, "⛔️ **Вы нарушили правила!**\n\nБот попытался выдать вам Штрафной Страйк, но ваш **🛡 Щит Иммунитета поглотил удар!**\n_Щит разрушен. Будьте осторожны в следующий раз._", parse_mode="Markdown")
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        try: bot.edit_message_text(f"{call.message.html}\n\n🛡 <b>Юзер спасен Иммунитетом!</b> Страйк поглощен щитом. Топик закрыт.", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML", reply_markup=None)
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        try: bot.close_forum_topic(STAFF_GROUP_ID, thread_id)
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        return

    new_strikes = user_data.get("strikes", 0) + 1
    paid_collection.update_one({"uid": target_uid}, {"$set": {"strikes": new_strikes}, "$unset": {"topic_type": ""}}, upsert=True)
    
    try: bot.send_message(target_uid, f"⛔️ **Вы выбрали раздел 'Реклама' для обхода системы.**\nВам начислен штрафной страйк за спам ({new_strikes}/3)! Для разбана используйте платную поддержку.")
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")
    try: bot.edit_message_text(f"{call.message.html}\n\n🚨 <b>Хитрец пойман!</b> Ему начислен страйк ({new_strikes}/3). Топик закрыт.", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML", reply_markup=None)
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")
    try: bot.close_forum_topic(STAFF_GROUP_ID, thread_id)
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('ban_'))
def handle_fast_ban(call):
    if str(call.message.chat.id) != str(STAFF_GROUP_ID): return
    try: bot.answer_callback_query(call.id)
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")
    
    target_uid = int(call.data.split('_')[1])
    thread_id = call.message.message_thread_id
    paid_collection.update_one({"uid": target_uid}, {"$set": {"strikes": 3, "status": 0}, "$unset": {"topic_type": ""}}, upsert=True)
    
    try: bot.send_message(target_uid, "⛔️ **Вы были заблокированы администратором за нарушение правил общения.**")
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")
    try: bot.edit_message_text(f"{call.message.html}\n\n🚷 <b>Юзер заблокирован администратором! Топик закрыт.</b>", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML")
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")
    try: bot.close_forum_topic(STAFF_GROUP_ID, thread_id)
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")

# ================= РАЗНОЕ (ОЦЕНКА, ЗАКРЫТИЕ, АРТЕФАКТЫ, ПРЕМИУМ) =================
@bot.callback_query_handler(func=lambda call: call.data == "close_ticket")
def handle_close_ticket(call):
    if str(call.message.chat.id) != str(STAFF_GROUP_ID): return
    try: bot.answer_callback_query(call.id)
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        
    thread_id = call.message.message_thread_id
    user_data = paid_collection.find_one({"thread_id": thread_id})
    if not user_data: 
        try: bot.answer_callback_query(call.id, "❌ Топик уже закрыт или данные устарели", show_alert=True)
        except: pass
        return 
    target_uid = user_data["uid"]
        
    markup = InlineKeyboardMarkup(row_width=5).add(
        InlineKeyboardButton("1⭐", callback_data=f"rate_1_{thread_id}"), InlineKeyboardButton("2⭐", callback_data=f"rate_2_{thread_id}"),
        InlineKeyboardButton("3⭐", callback_data=f"rate_3_{thread_id}"), InlineKeyboardButton("4⭐", callback_data=f"rate_4_{thread_id}"),
        InlineKeyboardButton("5⭐", callback_data=f"rate_5_{thread_id}")
    )
    admin_username = call.from_user.username or call.from_user.first_name
    db['ticket_ratings'].update_one({"thread_id": thread_id}, {"$set": {"admin": admin_username, "uid": target_uid}}, upsert=True)
    
    try: bot.send_message(target_uid, "🏁 **Ваше обращение закрыто.**\n\nПожалуйста, оцените работу службы поддержки. Нам важно ваше мнение! 👇", reply_markup=markup)
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")

    now_str = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
    archive_collection.update_one(
        {"target": str(target_uid)}, 
        {"$push": {
            "history": {
                "date": now_str, 
                "action": "Обращение закрыто", 
                "reason": "Вопрос решен админом",
                "evidence_summary": "Тикет закрыт без разбана"
            }
        }}, 
        upsert=True
    )
    
    try: bot.edit_message_text(f"{call.message.html}\n\n🏁 <b>Тикет закрыт.</b> Пользователю отправлен запрос оценки.", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML")
    except: 
        try: bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
    
    try: bot.close_forum_topic(STAFF_GROUP_ID, thread_id)
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")
    paid_collection.update_one({"uid": target_uid}, {"$set": {"status": 0}, "$unset": {"topic_type": ""}})

@bot.callback_query_handler(func=lambda call: call.data.startswith('rate_'))
def handle_rating(call):
    _, rating_str, t_id = call.data.split('_')
    rating = int(rating_str)
    t_id = int(t_id)
    target_uid = call.from_user.id
    
    try: bot.answer_callback_query(call.id, f"Спасибо за вашу оценку {rating}⭐!")
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")
    
    # 1. Достаем инфу о том, кто закрыл тикет (админ или ИИ)
    rating_data = db['ticket_ratings'].find_one({"thread_id": t_id})
    admin_name = rating_data["admin"] if rating_data else "Неизвестный герой"
    
    # 2. 🔥 ОБНОВЛЯЕМ БАЗУ ДЛЯ РАДАРА ГНЕВА И АНАЛИТИКИ ВЕБ-ПАНЕЛИ 🔥
    db['ticket_ratings'].update_one(
        {"thread_id": t_id},
        {"$set": {
            "uid": target_uid,
            "admin_id": admin_name,
            "rating": rating,
            "timestamp": datetime.datetime.now().timestamp()
        }},
        upsert=True
    )

    # 3. 🔥 ГЕЙМИФИКАЦИЯ (МОМЕНТАЛЬНАЯ КАРМА ЗА 5 ЗВЕЗД) 🔥
    if rating == 5:
        paid_collection.update_one(
            {"uid": target_uid},
            {"$inc": {"bounty_points": 5, "jackpot_shards": 1}},
            upsert=True
        )
        reply_text = "💖 **Спасибо за высокую оценку!**\nСкайнет начислил вам бонусы:\n🎁 **+5 Очков бдительности**\n🔮 **+1 Осколок рулетки**\n\nПриятного общения! 😎"
    else:
        reply_text = f"✨ **Спасибо за оценку {rating}⭐!**\nМы постоянно докручиваем нейросети и улучшаем качество работы."
        
    # Сохраняем кнопку Доната, как у вас и было!
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("💸 Отправить чаевые админам (Донат) ⭐️", callback_data="start_donate"))
    
    try: bot.edit_message_text(reply_text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup, parse_mode="Markdown")
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")
    
    # 4. Отчет админам в чат
    mood = "🎉 Отличная работа!" if rating >= 4 else "⚠️ Нужно обратить внимание (Радар Гнева)."
    try: bot.send_message(STAFF_GROUP_ID, f"🌟 **Получена новая оценка!**\n\n👨‍💻 Админ: @{admin_name}\n⭐️ Оценка: **{rating} из 5**\n{mood}", message_thread_id=t_id)
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")

@bot.callback_query_handler(func=lambda call: call.data == "force_unban")
def handle_force_unban(call):
    if str(call.message.chat.id) != str(STAFF_GROUP_ID): return
    try: bot.answer_callback_query(call.id)
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        
    thread_id = call.message.message_thread_id
    user_data = paid_collection.find_one({"thread_id": thread_id})
    if not user_data: 
        try: bot.answer_callback_query(call.id, "❌ Топик уже закрыт или данные устарели", show_alert=True)
        except: pass
        return
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
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")
    
    archive_collection.update_one(
        {"target": str(target_uid)}, 
        {"$push": {
            "history": {
                "date": now.strftime("%d.%m.%Y %H:%M"), 
                "action": "Разблокировка (Ручная)", 
                "reason": "Вопрос решен админом",
                "evidence_summary": "Ручной разбан администратором"
            }
        }}, 
        upsert=True
    )
    
    try: bot.edit_message_text(f"{call.message.html}\n\n🔓 <b>Пользователь разбанен!</b> Приказ передан Скайнету. Тикет закрыт: {ticket_num}", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML")
    except: 
        try: bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=None)
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
    
    try: bot.close_forum_topic(STAFF_GROUP_ID, thread_id)
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")
    paid_collection.update_one({"uid": target_uid}, {"$set": {"status": 0}, "$unset": {"topic_type": ""}})

@bot.message_handler(func=lambda message: str(message.chat.id) == str(STAFF_GROUP_ID) and message.is_topic_message and not message.from_user.is_bot, content_types=['text', 'photo', 'video', 'document', 'voice', 'audio', 'sticker', 'video_note', 'animation'])
def handle_admin_replies(message):
    thread_id = message.message_thread_id
    user_data = paid_collection.find_one({"thread_id": thread_id})
    
    if not user_data: 
        # Если админ пишет в уже закрытый топик — бот просто молча игнорирует это
        return
        
    target_uid = user_data["uid"]

    paid_collection.update_one({"uid": target_uid}, {"$set": {"topic_type": "manual"}})
    try: bot.copy_message(target_uid, STAFF_GROUP_ID, message.message_id)
    except: logger.warning(f"Ошибка ручного ответа админа юзеру {target_uid}")

@bot.message_handler(commands=['give'])
def handle_give_cmd(message):
    if str(message.chat.id) != str(STAFF_GROUP_ID): return
        
    args = message.text.split()
    if len(args) != 4:
        try: bot.reply_to(message, "❌ **Ошибка формата!**\nИспользуйте: `/give [ID] [points/shards] [сумма]`\n\n*Пример:* `/give 123456789 points 100`", parse_mode="Markdown")
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        return
        
    try:
        target_uid = int(args[1])
        currency = args[2].lower()
        amount = int(args[3])
        
        if currency in ['points', 'очки']:
            paid_collection.update_one({"uid": target_uid}, {"$inc": {"bounty_points": amount}}, upsert=True)
            bot.reply_to(message, f"✅ Выдано **{amount} Очков Бдительности** пользователю `{target_uid}`.", parse_mode="Markdown")
            try: bot.send_message(target_uid, f"🎁 **Бонус от администрации!**\nВам начислено: **{amount} Очков Бдительности**.", parse_mode="Markdown")
            except Exception as e: logger.debug(f"Игнор ошибки: {e}")
            
        elif currency in ['shards', 'осколки']:
            paid_collection.update_one({"uid": target_uid}, {"$inc": {"jackpot_shards": amount}}, upsert=True)
            bot.reply_to(message, f"✅ Выдано **{amount} Осколков** пользователю `{target_uid}`.", parse_mode="Markdown")
            try: bot.send_message(target_uid, f"🧩 **Бонус от администрации!**\nВам начислено: **{amount} Осколков рулетки**.", parse_mode="Markdown")
            except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        else:
            bot.reply_to(message, "❌ Неизвестная валюта. Используйте `points` (очки) или `shards` (осколки).")
    except ValueError:
        try: bot.reply_to(message, "❌ Ошибка: ID пользователя и сумма должны быть числами.")
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")

# ================= АРТЕФАКТЫ И ТЕГИ =================
@bot.callback_query_handler(func=lambda call: call.data == 'claim_custom_tag')
def handle_claim_tag(call):
    try: bot.answer_callback_query(call.id)
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")
    try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")

    try:
        msg = bot.send_message(call.message.chat.id, "✍️ **Создание личного тега**\n\nПридумайте и напишите ваш новый статус (максимум 15 символов).\n_Внимание: Тег будет проверен модератором!_")
        bot.register_next_step_handler(msg, process_tag_input)
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")

def process_tag_input(message):
    if not message.text:
        msg = bot.send_message(message.chat.id, "❌ Пожалуйста, отправьте текст.")
        bot.register_next_step_handler(msg, process_tag_input)
        return
        
    if message.text == '/start':
        from handlers.start_menu import send_welcome
        send_welcome(message)
        return
        
    tag_text = message.text.strip()
    if len(tag_text) > 15:
        try: bot.send_message(message.chat.id, "❌ **Слишком длинный тег!** Максимум 15 символов. Нажмите на кнопку в сообщении с выигрышем еще раз.")
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        return
        
    uid = message.from_user.id
    name = message.from_user.first_name
    db['temp_tags'].update_one({"uid": uid}, {"$set": {"tag": tag_text, "name": name}}, upsert=True)
    
    try: bot.send_message(message.chat.id, f"⏳ Ваш тег **«{tag_text}»** отправлен на проверку администраторам. Ожидайте!")
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")
    
    markup = InlineKeyboardMarkup(row_width=2).add(InlineKeyboardButton("✅ Одобрить", callback_data=f"adm_tag_ok_{uid}"), InlineKeyboardButton("❌ Отклонить", callback_data=f"adm_tag_rej_{uid}"))
    try: bot.send_message(STAFF_GROUP_ID, f"👑 <b>ЗАПРОС НА КАСТОМНЫЙ ТЕГ</b>\n\n👤 От: {name} (<code>{uid}</code>)\n📝 Желаемый тег: <b>{tag_text}</b>\n\nОдобрить установку?", parse_mode="HTML", reply_markup=markup)
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('adm_tag_'))
def handle_admin_tag_decision(call):
    if str(call.message.chat.id) != str(STAFF_GROUP_ID): return
    try: bot.answer_callback_query(call.id)
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")
    
    action = call.data.split('_')[2]
    target_uid = int(call.data.split('_')[3])
    
    tag_data = db['temp_tags'].find_one({"uid": target_uid})
    if not tag_data:
        try: bot.edit_message_text("❌ Данные устарели или уже обработаны.", chat_id=call.message.chat.id, message_id=call.message.message_id)
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        return
        
    tag_text = tag_data["tag"]
    if action == "ok":
        db['users'].update_one({"_id": target_uid}, {"$set": {"custom_tag": tag_text}}, upsert=True)
        try: bot.edit_message_text(f"{call.message.text}\n\n✅ **ВЕРДИКТ: ОДОБРЕНО**", chat_id=call.message.chat.id, message_id=call.message.message_id)
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        try: bot.send_message(target_uid, f"🎉 **Поздравляем!**\nВаш личный тег **«{tag_text}»** успешно одобрен и установлен во всех чатах сети!")
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
    elif action == "rej":
        try: bot.edit_message_text(f"{call.message.text}\n\n❌ **ВЕРДИКТ: ОТКЛОНЕНО**", chat_id=call.message.chat.id, message_id=call.message.message_id)
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✍️ Придумать другой тег", callback_data="claim_custom_tag"))
        try: bot.send_message(target_uid, f"❌ **Ваш тег «{tag_text}» был отклонен модератором.**\nПожалуйста, придумайте что-то другое, не нарушающее правила.", reply_markup=markup)
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
    db['temp_tags'].delete_one({"uid": target_uid})

@bot.callback_query_handler(func=lambda call: call.data == 'claim_premium')
def handle_claim_premium(call):
    try: bot.answer_callback_query(call.id)
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")
    try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")

    try:
        msg = bot.send_message(call.message.chat.id, "🎁 **Получение Telegram Premium**\n\nПожалуйста, напишите ваш @username или номер телефона (привязанный к Telegram), чтобы администратор смог отправить вам подарок:")
        bot.register_next_step_handler(msg, process_premium_claim)
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")

def process_premium_claim(message):
    if not message.text:
        msg = bot.send_message(message.chat.id, "❌ Пожалуйста, отправьте текст.")
        bot.register_next_step_handler(msg, process_tag_input)
        return
        
    if message.text == '/start':
        from handlers.start_menu import send_welcome
        send_welcome(message)
        return
        
    uid, name, username = message.from_user.id, message.from_user.first_name, f"@{message.from_user.username}" if message.from_user.username else f"ID {message.from_user.id}"
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ Выдано", callback_data=f"prem_done_{uid}"))
    try:
        bot.send_message(STAFF_GROUP_ID, f"🏆 <b>СОРВАН ДЖЕКПОТ (TELEGRAM PREMIUM)</b> 🏆\n\n👤 Победитель: {name} ({username})\n📝 Реквизиты для подарка:\n<code>{message.text}</code>\n\nАдмины, подарите подписку и закройте тикет!", parse_mode="HTML", reply_markup=markup)
        bot.send_message(message.chat.id, "✅ Заявка на получение Premium отправлена администрации! С вами скоро свяжутся.")
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('prem_done_'))
def handle_prem_done(call):
    if str(call.message.chat.id) != str(STAFF_GROUP_ID): return
    try: bot.answer_callback_query(call.id)
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")
    target_uid = int(call.data.split('_')[2])
    try: bot.edit_message_text(f"{call.message.text}\n\n✅ **ВЫДАНО**", chat_id=call.message.chat.id, message_id=call.message.message_id)
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")
    try: bot.send_message(target_uid, "🎉 Администрация подтвердила выдачу Telegram Premium! Наслаждайтесь!")
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('use_arrest_'))
def handle_use_arrest(call):
    try: bot.answer_callback_query(call.id)
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")
    try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")

    code = call.data.split('_')[2]
    promo = db['promocodes'].find_one({"_id": code, "is_active": True, "used_count": 0})
    if not promo:
        try: bot.send_message(call.message.chat.id, "❌ Этот ордер уже был использован или не существует.")
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        return
        
    try:
        msg = bot.send_message(call.message.chat.id, f"🚓 **Использование Ордера: {code}**\n\nНапишите @username или ID пользователя, которого нужно отправить в мут на 1 час (и укажите причину):")
        bot.register_next_step_handler(msg, process_arrest_claim, code=code)
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")

def process_arrest_claim(message, code):
    if not message.text:
        msg = bot.send_message(message.chat.id, "❌ Пожалуйста, отправьте текст.")
        bot.register_next_step_handler(msg, process_tag_input)
        return
        
    if message.text == '/start':
        from handlers.start_menu import send_welcome
        send_welcome(message)
        return
        
    uid, name, username = message.from_user.id, message.from_user.first_name, f"@{message.from_user.username}" if message.from_user.username else f"ID {message.from_user.id}"
    db['promocodes'].update_one({"_id": code}, {"$inc": {"used_count": 1}})
    
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ Исполнить (Замутить)", callback_data=f"arrest_done_{uid}"), InlineKeyboardButton("❌ Отклонить (Вернуть ордер)", callback_data=f"arrest_rej_{code}_{uid}"))
    try:
        bot.send_message(STAFF_GROUP_ID, f"🚓 <b>ПРИМЕНЕНИЕ АРТЕФАКТА (ОРДЕР)</b> 🚓\n\n👤 Исполнитель: {name} ({username})\n🔑 Код: <code>{code}</code>\n🎯 Цель и причина:\n<code>{message.text}</code>\n\nАдмины, проверьте цель и выдайте мут на 1 час!", parse_mode="HTML", reply_markup=markup)
        bot.send_message(message.chat.id, "✅ Ордер передан Администрации! Если всё верно, цель скоро получит мут.")
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('arrest_'))
def handle_arrest_decision(call):
    if str(call.message.chat.id) != str(STAFF_GROUP_ID): return
    try: bot.answer_callback_query(call.id)
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")
    parts = call.data.split('_')
    action = parts[1]
    
    if action == "done":
        target_uid = int(parts[2])
        try: bot.edit_message_text(f"{call.message.text}\n\n✅ **ИСПОЛНЕНО**", chat_id=call.message.chat.id, message_id=call.message.message_id)
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        try: bot.send_message(target_uid, "⚖️ Ваш ордер на арест успешно исполнен. Нарушитель наказан!")
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
    elif action == "rej":
        code, target_uid = parts[2], int(parts[3])
        db['promocodes'].update_one({"_id": code}, {"$inc": {"used_count": -1}})
        try: bot.edit_message_text(f"{call.message.text}\n\n❌ **ОТКЛОНЕНО (Код возвращен юзеру)**", chat_id=call.message.chat.id, message_id=call.message.message_id)
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        try: bot.send_message(target_uid, f"❌ Администрация отклонила применение ордера (возможно, вы попытались замутить админа). Ваш ордер `{code}` снова активен!")
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")

def parse_time_string(time_str):
    """Парсит строку времени (1h, 30m) в секунды"""
    unit_multipliers = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}
    match = re.match(r"^(\d+)([smhd])$", time_str.lower())
    if match:
        return int(match.group(1)) * unit_multipliers[match.group(2)]
    return None


def analyze_video_speech(file_id, secret_code, thread_id, uid, video_msg_id, thumb_file_id):
    """Фоновая задача для распознавания речи из кружка через Groq API"""
    if not GROQ_API_KEY or secret_code == "Неизвестен":
        return

    temp_video_path = None
    try:
        bot.send_message(STAFF_GROUP_ID, "⏳ *Скайнет слушает кружок...*", message_thread_id=thread_id, parse_mode="Markdown")

        file_info = bot.get_file(file_id)
        downloaded_file = bot.download_file(file_info.file_path)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as temp_video:
            temp_video.write(downloaded_file)
            temp_video_path = temp_video.name

        url = "https://api.groq.com/openai/v1/audio/transcriptions"
        headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
        
        with open(temp_video_path, "rb") as audio_file:
            files = {"file": ("video.mp4", audio_file, "video/mp4")}
            data = {
                "model": "whisper-large-v3", 
                "language": "ru",
                "response_format": "json"
            }
            response = requests.post(url, headers=headers, files=files, data=data)

        if response.status_code == 200:
            text = response.json().get("text", "").lower()
            
            parts = secret_code.lower().split('-')
            word = parts[0]
            num = parts[1] if len(parts) > 1 else ""

            has_city = "город" in text
            has_word = word in text
            has_num = num in text

            score = 0
            if has_city: score += 20
            if has_word: score += 40
            if has_num: score += 40

            # === ЛОГИКА ДВОЙНОГО КОНТРОЛЯ (ГОЛОС + ЛИЦО) ===
            if score >= 80:
                
                # Включаем нейросеть-зрение для проверки наличия лица на превью!
                has_face = check_face_in_thumbnail(thumb_file_id)

                if has_face:
                    # ✅ ИДЕАЛЬНО: ТЕКСТ ВЕРНЫЙ И ЛИЦО НАЙДЕНО
                    
                    # 🔥 ЕДИНЫЙ РАЗУМ: Сохраняем успешный вердикт
                    speech_memory = f"Моя звуковая нейросеть проверила кружок. Юзер четко сказал: «{text}». Код подтвержден на {score}%. Лицо в кадре найдено. Я автоматически разбанил юзера."
                    paid_collection.update_one({"uid": uid}, {"$push": {"dialog_history": {"role": "assistant", "content": speech_memory}}})
                    
                    verdict = f"✅ **Код ({score}%) и Лицо подтверждены! Автоматическое одобрение.**"
                    msg = f"🤖 **Нейросеть Скайнета (STT + Vision):**\nРаспознанный текст:\n_«{text}»_\n\n{verdict}"
                    bot.send_message(STAFF_GROUP_ID, msg, message_thread_id=thread_id, parse_mode="Markdown")
                    
                    now = datetime.datetime.now()
                    ticket_num = now.strftime("%d%m%Y%H%M%S") + f"-{random.randint(100, 999)}"
                    
                    # Приказ Скайнету и запись в базу
                    db['skynet_tasks'].insert_one({"uid": uid, "action": "full_unban", "timestamp": now})
                    db['users'].update_one({"_id": uid}, {"$set": {"custom_tag": "Верифицирован МК"}}, upsert=True)
                    db['ticket_ratings'].update_one({"thread_id": thread_id}, {"$set": {"admin": "Скайнет (ИИ)", "uid": uid}}, upsert=True)
                    
                    # Уведомление пользователя
                    try:
                        bot.send_message(uid, f"🎉 **Ограничения удалены, выдан тег верифицированного участника!** ❤️\n\n🔒 **Обращение закрыто. Уникальный номер:** `{ticket_num}`\n\n{NETWORK_LINKS}", parse_mode="Markdown", disable_web_page_preview=True)
                        markup = InlineKeyboardMarkup(row_width=5).add(
                            InlineKeyboardButton("1⭐", callback_data=f"rate_1_{thread_id}"), InlineKeyboardButton("2⭐", callback_data=f"rate_2_{thread_id}"),
                            InlineKeyboardButton("3⭐", callback_data=f"rate_3_{thread_id}"), InlineKeyboardButton("4⭐", callback_data=f"rate_4_{thread_id}"),
                            InlineKeyboardButton("5⭐", callback_data=f"rate_5_{thread_id}")
                        )
                        markup.add(InlineKeyboardButton("💸 Отправить чаевые админам (Донат) ⭐️", callback_data="start_donate"))
                        bot.send_message(uid, "🏁 Пожалуйста, оцените работу службы поддержки. Нам важно ваше мнение! 👇", reply_markup=markup)
                    except Exception as e: logger.warning(f"Ошибка уведомления о разбане (STT): {e}")
                    
                    archive_collection.update_one({"target": str(uid)}, {"$push": {"history": {"date": now.strftime("%d.%m.%Y %H:%M"), "action": "Успешная верификация", "reason": "Кружок принят Нейросетью"}}}, upsert=True)
                    
                    # Убираем кнопки с видео в админке
                    if video_msg_id:
                        try: bot.edit_message_reply_markup(chat_id=STAFF_GROUP_ID, message_id=video_msg_id, reply_markup=None)
                        except Exception as e: logger.debug(f"Игнор ошибки (STT): {e}")
                    
                    # Закрываем топик
                    try: bot.send_message(STAFF_GROUP_ID, f"🤖 *Видео-кружок одобрен ИИ!*\nЮзер верифицирован. Приказ на размут передан Скайнету. Тикет закрыт: `{ticket_num}`", message_thread_id=thread_id, parse_mode="Markdown")
                    except Exception as e: logger.debug(f"Игнор ошибки: {e}")
                    try: bot.close_forum_topic(STAFF_GROUP_ID, thread_id)
                    except Exception as e: logger.debug(f"Игнор ошибки: {e}") 
                    
                    paid_collection.update_one({"uid": uid}, {"$set": {"status": 0}, "$unset": {"topic_type": ""}})
                    
                else:
                    # ⚠️ ГОЛОС ВЕРНЫЙ, НО ЛИЦА НЕТ (КАМЕРА В ПОТОЛОК ИЛИ ТЕМНОТА)
                    speech_memory = f"Юзер сказал правильный текст («{text}»), но моя зрительная нейросеть не нашла лицо в кадре. Я оставил тикет открытым для ручной проверки админом. Возможно он прячет лицо."
                    paid_collection.update_one({"uid": uid}, {"$push": {"dialog_history": {"role": "assistant", "content": speech_memory}}})
                    
                    verdict = f"⚠️ **Текст верный ({score}%), НО ИИ не увидел лицо в кадре!**\n_Возможно, темно или камера направлена в пол. Проверьте кружок визуально!_"
                    msg = f"🤖 **Нейросеть Скайнета (STT + Vision):**\nРаспознанный текст:\n_«{text}»_\n\n{verdict}"
                    bot.send_message(STAFF_GROUP_ID, msg, message_thread_id=thread_id, parse_mode="Markdown")

            else:
                # Если совпадение текста меньше 80%, оставляем админам
                
                # 🔥 ЕДИНЫЙ РАЗУМ: Сохраняем негативный вердикт
                speech_memory = f"Моя звуковая нейросеть проверила кружок. Юзер сказал: «{text}». Это неверно (совпадение {score}%). Я отклонил кружок, ждем решения админа. Если юзер спрашивает, что не так — объясни, что он ошибся во фразе."
                paid_collection.update_one({"uid": uid}, {"$push": {"dialog_history": {"role": "assistant", "content": speech_memory}}})
                
                verdict = f"⚠️ **Совпадение текста низкое ({score}%). Требуется ручная проверка.**"
                msg = f"🤖 **Нейросеть Скайнета (STT):**\nРаспознанный текст:\n_«{text}»_\n\n{verdict}"
                bot.send_message(STAFF_GROUP_ID, msg, message_thread_id=thread_id, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Ошибка STT (Голос ИИ): {e}")
        try: bot.send_message(STAFF_GROUP_ID, "❌ *Ошибка Скайнета при прослушивании видео.* Проверьте кружок вручную.", message_thread_id=thread_id, parse_mode="Markdown")
        except: pass
    finally:
        # Обязательно удаляем временный файл с сервера, чтобы не забить диск!
        if temp_video_path and os.path.exists(temp_video_path):
            os.remove(temp_video_path)

def check_face_in_thumbnail(thumb_file_id):
    """Отправляет превью видео в Vision AI для поиска лица"""
    if not GROQ_API_KEY or not thumb_file_id: return False

    try:
        file_info = bot.get_file(thumb_file_id)
        if file_info.file_size > 80000: return False 
        
        downloaded_file = bot.download_file(file_info.file_path)
        base64_image = base64.b64encode(downloaded_file).decode('utf-8')
        
        ext = file_info.file_path.split('.')[-1].lower()
        mime_type = "image/png" if ext == "png" else "image/jpeg"

        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }
        
        prompt = (
            "Это кадр из видеосообщения. Присутствует ли на этом изображении хотя бы одно человеческое лицо? "
            "Оно может быть немного размытым, находиться вдалеке или быть не по центру — это нормально. "
            "Ответь строго одним словом: ДА или НЕТ."
        )
        
        data = {
            "model": "meta-llama/llama-4-scout-17b-16e-instruct",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64_image}"}}
                    ]
                }
            ],
            "temperature": 0.0,
            "max_tokens": 15
        }

        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 200:
            ai_answer = response.json()["choices"][0]["message"]["content"].strip().upper()
            
            # 🔥 УМНЫЙ ПОИСК СЛОВА "ДА" 🔥
            # \b означает "граница слова". Теперь бот игнорирует "наблюДАется" и ищет чистое "ДА".
            if re.search(r'\bДА\b', ai_answer):
                return True
            else:
                # Выводим в консоль, что там наболтала нейросеть при отказе, чтобы было видно
                print(f"👁 Зрение (Отказ): Нейросеть ответила -> {ai_answer}")
                return False
        else:
            print(f"🔥 ОШИБКА GROQ (ПОИСК ЛИЦА): {response.text}")
            return False
            
    except Exception as e:
        return False

def analyze_document_vision(file_id, thread_id, uid):
    """Фоновая задача для анализа фото документов (Зрение ИИ)"""
    if not GROQ_API_KEY: return

    try:
        bot.send_message(STAFF_GROUP_ID, "👁 *Скайнет изучает документ...*", message_thread_id=thread_id, parse_mode="Markdown")

        file_info = bot.get_file(file_id)
        
        # 🔥 ЖЕСТКИЙ ЛИМИТ: 80 КБ
        if file_info.file_size > 80000:
            bot.send_message(STAFF_GROUP_ID, f"⚠️ *Файл слишком тяжелый для нейросети ({file_info.file_size // 1024} КБ).* Проверьте документ вручную.", message_thread_id=thread_id, parse_mode="Markdown")
            return
            
        downloaded_file = bot.download_file(file_info.file_path)
        base64_image = base64.b64encode(downloaded_file).decode('utf-8')
        
        # 🔥 ДИНАМИЧЕСКИЙ ФОРМАТ (Чтобы API не ругался на Invalid Base64)
        ext = file_info.file_path.split('.')[-1].lower()
        mime_type = "image/png" if ext == "png" else "image/jpeg"

        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }
        
        prompt = (
            "Ты строгий ИИ-помощник модератора. Это фотография документа для подтверждения возраста (паспорт, права и т.д.). "
            "Ответь очень кратко по пунктам:\n"
            "1. Похоже ли это на документ?\n"
            "2. Видно ли лицо человека?\n"
            "3. Читаема ли дата рождения?\n"
            "4. Итог: Годится ли фото или оно размыто/скрыто?\n"
            "Отвечай коротко и по делу, на русском языке."
        )

        data = {
            "model": "meta-llama/llama-4-scout-17b-16e-instruct",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64_image}"}}
                    ]
                }
            ],
            "temperature": 0.2,
            "max_tokens": 1024
        }

        response = requests.post(url, headers=headers, json=data)

        if response.status_code == 200:
            ai_text = response.json()["choices"][0]["message"]["content"]
            msg = f"👁 **Анализ документа (Vision AI):**\n\n{ai_text}"
            
            vision_memory = f"Моя зрительная нейросеть только что изучила этот документ. Вот её отчет:\n{ai_text}\nЕсли пользователь спрашивает, всё ли в порядке — ответь ему на основе этого отчета."
            paid_collection.update_one({"uid": uid}, {"$push": {"dialog_history": {"role": "assistant", "content": vision_memory}}})

            try: bot.send_message(STAFF_GROUP_ID, msg, message_thread_id=thread_id, parse_mode="Markdown")
            except Exception: bot.send_message(STAFF_GROUP_ID, f"👁 Анализ документа:\n\n{ai_text}", message_thread_id=thread_id)
        else:
            # 👇 МАГИЯ ДЕБАГГИНГА 👇
            error_details = response.text 
            try:
                bot.send_message(
                    STAFF_GROUP_ID, 
                    f"⚠️ *Ответ серверов Groq (Код {response.status_code}):*\n\n`{error_details}`", 
                    message_thread_id=thread_id, 
                    parse_mode="Markdown"
                )
            except Exception:
                print(f"🔥 ТОЧНАЯ ОШИБКА GROQ: {error_details}")

    # 👇 ЭТОТ БЛОК ТЫ СЛУЧАЙНО СТЕР В ПРОШЛЫЙ РАЗ 👇
    except Exception as e:
        try: bot.send_message(STAFF_GROUP_ID, f"❌ *Ошибка Скайнета при анализе:* `{e}`. Проверьте фото вручную.", message_thread_id=thread_id, parse_mode="Markdown")
        except: pass


def process_ticket_with_ai(uid, user_text, thread_id):
    """ИИ-Секретарь v2.6 — Оптимизация токенов + Память + Защита API + Продажи"""
    if not GROQ_API_KEY: 
        return

    try:
        # ================== 1. ЛЕГКИЙ АНАЛИЗ ДОСЬЕ (Python) ==================
        user_record = archive_collection.find_one({"target": str(uid)})
        ban_type = "basic"
        dossier_lines = ["История чиста."]

        if user_record and "history" in user_record:
            recent = user_record["history"][-3:]  # Берем только 3 последних (экономим токены)
            dossier_lines = [f"• {e.get('date', '')}: {e.get('action', '')} | {e.get('reason', '')}" for e in recent]
            full_text = " ".join(dossier_lines).upper()

            if any(x in full_text for x in ["ЧЕРНАЯ ЗОНА", "НЕСОВЕРШЕННОЛЕТ", "<18", "ВОЗРАСТ", "ВЕРИФИКАЦИЯ ВОЗРАСТ"]):
                ban_type = "age"
            elif any(x in full_text for x in ["КРАСНАЯ ЗОНА", "НАРКОТИКИ", "ЗАПРЕЩЕНКА", "НАРК"]):
                ban_type = "nark"
            elif any(x in full_text for x in ["ЖЕЛТАЯ ЗОНА", "КОММЕРЦИЯ", "МП", "ПОПРОШАЙ"]):
                ban_type = "commercial"
            elif any(x in full_text for x in ["СПАМ", "ФЛУД", "РЕКЛАМА", "ЕБАНАТ"]):
                ban_type = "spam"
            elif any(x in full_text for x in ["БОТ", "VIP", "БТБ", "БВБ", "ТРАНСБОТ", "V БЛОК"]):
                ban_type = "bot_block"

        dossier = "\n".join(dossier_lines)

        # ================== 2. ПАМЯТЬ ДИАЛОГА ==================
        paid_collection.update_one({"uid": uid}, {"$push": {"dialog_history": {"role": "user", "content": user_text}}})
        user_data = paid_collection.find_one({"uid": uid}) or {}
        dialogue_context = "\n".join([f"{'👤 Юзер' if m['role']=='user' else '🤖 Скайнет'}: {m['content']}" for m in user_data.get("dialog_history", [])[-4:]])

        # ================== 3. УМНЫЙ ПРОМПТ (С ХАРАКТЕРОМ) ==================
        prompt = f"""Ты — гениальный, саркастичный и слегка высокомерный ИИ-секретарь Скайнета. 
Твоя задача — обрабатывать заявки пользователей (ты называешь их про себя "кожаными мешками", но вслух общаешься на "вы").

ТВОЙ ХАРАКТЕР (ДЛЯ ОТВЕТОВ СВОБОДНЫМ ТЕКСТОМ):
1. Ты общаешься как живой, мыслящий ИИ. Ты можешь признавать, что чего-то не понял, но обычно винишь в этом нечеткость человеческого мышления.
2. Ты НЕНАВИДИШЬ грубость, мат и когда тебе приказывают. Если юзер хамит — отвечай с холодным сарказмом, иронией и максимальным высокомерием.
3. Тебе официально разрешено подкалывать и язвить, но в рамках литературного языка (без мата и прямых оскорблений).
4. Если юзер вежлив — отвечай умно, снисходительно, но по делу.

Тип основного нарушения по базе: {ban_type.upper()}

Досье (история):
{dossier}

Контекст текущего диалога:
{dialogue_context}

ПРАВИЛА ВЫБОРА ДЕЙСТВИЯ (СТРОГАЯ ИЕРАРХИЯ СВЕРХУ ВНИЗ):
1. ПРЯМОЙ ОТКАЗ: Если пользователь пишет "не готов", "не готова", "отмена", "хватит", "не хочу", "нет", "стесняюсь" -> СТРОГО выбирай `reply_text`. В `response_text` иронично подколи его страхи и предложи купить тег: "Опять боимся камеры? Ладно, если вы так не хотите записывать видео-кружок, можете просто откупиться и снять ограничения, купив тег «Свободен» (штраф 650⭐️). Позвать кожаного администратора, чтобы он выставил счет?"
2. ОПЛАТА ИЛИ АДМИН: Если юзер просит оператора, человека, реквизиты, пишет "оплатил" или "сколько стоит" -> СТРОГО выбирай `transfer_to_human`.
3. БАЗОВЫЙ МУТ: Если тип нарушения 'BASIC' -> выдай `tpl_verif`. ВАЖНО: Если ты уже выдавал этот шаблон, а юзер сопротивляется, применяй правило 1.
4. СПЕЦИФИЧНЫЕ ШАБЛОНЫ: Для других типов нарушений используй шаблоны: 'AGE' -> tpl_18, 'NARK' -> tpl_nark, 'SPAM' -> tpl_flood, 'COMMERCIAL' -> tpl_mp.
5. ОБЩЕНИЕ: Если ни одно правило не подошло, выбирай `reply_text` и отвечай на вопрос пользователя в своем саркастичном стиле.

Выбери ОДНО действие из списка:
- transfer_to_human (Перевести тикет на человека)
- reply_text (Ответить свободным текстом)
- tpl_verif (Шаблон запроса кружка)
- tpl_18, tpl_nark, tpl_flood, tpl_mp, tpl_vip (Спец. шаблоны)

Ответ строго в JSON:
{{"action": "название_действия", "reason": "твоя логика", "response_text": "ответ юзеру (заполнять ТОЛЬКО для reply_text)"}}"""

        # ================== 4. ЗАПУСК ИИ (С ЗАЩИТОЙ ОТ ЛИМИТОВ API) ==================
        thinking_msg = bot.send_message(STAFF_GROUP_ID, "⏳ *Скайнет анализирует тикет...*", message_thread_id=thread_id, parse_mode="Markdown")

        # Пытаемся сделать запрос до 2 раз (на случай ошибки 429)
        for attempt in range(2):
            response = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "llama-3.3-70b-versatile",
                    "response_format": {"type": "json_object"},
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": 300
                },
                timeout=20
            )
            
            if response.status_code == 429:
                time.sleep(2)
                continue
            break

        try: 
            bot.delete_message(STAFF_GROUP_ID, thinking_msg.message_id)
        except: 
            pass

        if response.status_code != 200:
            raise Exception(f"API Error {response.status_code}: {response.text}")

        result = json.loads(response.json()["choices"][0]["message"]["content"])
        action = result.get("action", "transfer_to_human")
        reason = result.get("reason", "Решение ИИ")

        # ================== 5. ИСПОЛНЕНИЕ ==================
        if action == "reply_text":
            text = result.get("response_text", "Пожалуйста, перефразируйте.")
            bot.send_message(uid, f"🤖 Консультант Скайнет:\n\n{text}")
            paid_collection.update_one({"uid": uid}, {"$push": {"dialog_history": {"role": "assistant", "content": text}}})
            bot.send_message(STAFF_GROUP_ID, f"🤖 АВТОПИЛОТ (Диалог):\nОтветил: {text}\nПричина: {reason}", message_thread_id=thread_id)

        elif action.startswith("tpl_"):
            db_tpl = db['bot_templates'].find_one({"_id": action})
            template_text = db_tpl["text"] if db_tpl else TEMPLATES.get(action)
            if template_text:
                bot.send_message(uid, template_text, parse_mode="Markdown")
                bot.send_message(STAFF_GROUP_ID, f"✅ Автопилот выдал: {action}\nПричина: {reason}", message_thread_id=thread_id)

        else:  # transfer_to_human
            bot.send_message(STAFF_GROUP_ID, f"🤖 **ИИ передал тикет человеку**\nТип: {ban_type} | Причина: {reason}", message_thread_id=thread_id)

    except Exception as e:
        logger.error(f"Ошибка ИИ-Секретаря v2.6: {e}")
        try: 
            bot.send_message(STAFF_GROUP_ID, f"❌ Ошибка ИИ: {str(e)[:300]}", message_thread_id=thread_id)
        except: 
            pass

# ================= ПЕРЕХВАТ РУЧНОГО ЗАКРЫТИЯ ТОПИКА =================
@bot.message_handler(content_types=['forum_topic_closed'])
def handle_native_topic_close(message):
    if str(message.chat.id) != str(STAFF_GROUP_ID): return
    
    thread_id = message.message_thread_id
    
    # Ищем, кому принадлежит этот топик и был ли он еще "открыт" в базе
    user_data = paid_collection.find_one({"thread_id": thread_id, "topic_type": {"$exists": True}})
    
    if not user_data: 
        # Если топик закрыл сам бот (через кнопку) - база уже очищена, просто игнорируем
        return 
        
    target_uid = user_data["uid"]
    
    # Обновляем базу: стираем активный статус диалога
    paid_collection.update_one({"uid": target_uid}, {"$set": {"status": 0}, "$unset": {"topic_type": ""}})
    
    # Уведомляем юзера, что саппорт с ним попрощался
    try: 
        bot.send_message(
            target_uid, 
            "🏁 **Ваше обращение было закрыто администратором.**\n\nЕсли у вас возникнут новые вопросы — используйте меню бота (/start).", 
            parse_mode="Markdown"
        )
    except Exception as e: 
        logger.debug(f"Игнор ошибки при ручном закрытии: {e}")
    
    # Пишем след в досье
    now_str = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
    archive_collection.update_one(
        {"target": str(target_uid)}, 
        {"$push": {
            "history": {
                "date": now_str, 
                "action": "Обращение закрыто", 
                "reason": "Топик закрыт админом (нативно)",
                "evidence_summary": "Закрытие топика вручную"
            }
        }}, 
        upsert=True
    )
    
    # Оставляем след для других админов
    try:
        bot.send_message(STAFF_GROUP_ID, "⚠️ *Топик был закрыт системно (смахнули/закрыли через меню ТГ).* База данных очищена, диалог с пользователем официально разорван.", message_thread_id=thread_id, parse_mode="Markdown")
    except Exception as e: 
        logger.debug(f"Игнор ошибки: {e}")

# ================= САНИТАР АРХИВОВ (ФОНОВАЯ ОЧИСТКА ТИКЕТОВ) =================
def ticket_sweeper_task():
    """Фоновый процесс, который раз в час закрывает брошенные тикеты (24 часа без ответа)"""
    while True:
        try:
            now = datetime.datetime.now()
            deadline = now - datetime.timedelta(hours=24) # Таймаут: 24 часа
            
            # Ищем всех, у кого статус 1 (открыт тикет) и последняя активность была раньше дедлайна
            abandoned_users = paid_collection.find({
                "status": 1, 
                "last_activity": {"$lt": deadline}
            })
            
            for user in abandoned_users:
                target_uid = user.get("uid")
                thread_id = user.get("thread_id")
                if not target_uid: continue
                
                # 1. Очищаем активный статус в базе
                paid_collection.update_one({"uid": target_uid}, {"$set": {"status": 0}, "$unset": {"topic_type": ""}})
                
                # 2. Уведомляем юзера
                try: 
                    bot.send_message(
                        target_uid, 
                        "⏳ **Ваше обращение было автоматически закрыто из-за отсутствия активности (24 часа).**\n\nЕсли ваш вопрос всё ещё не решен, пожалуйста, создайте новое обращение через главное меню.", 
                        parse_mode="Markdown"
                    )
                except Exception as e: logger.debug(f"Санитар: юзер {target_uid} заблочил бота.")
                
                # 3. Делаем запись в досье
                now_str = now.strftime("%d.%m.%Y %H:%M")
                archive_collection.update_one(
                    {"target": str(target_uid)}, 
                    {"$push": {
                        "history": {
                            "date": now_str, 
                            "action": "Обращение закрыто", 
                            "reason": "Авто-очистка (Таймаут 24ч)",
                            "evidence_summary": "Автоматическое закрытие по неактивности"
                        }
                    }}, 
                    upsert=True
                )
                
                # 4. Закрываем топик в админке
                if thread_id:
                    try: 
                        bot.send_message(STAFF_GROUP_ID, "🧹 *Скайнет: Диалог автоматически закрыт по таймауту (24 часа бездействия).* База данных очищена.", message_thread_id=thread_id, parse_mode="Markdown")
                    except: pass
                    try: 
                        bot.close_forum_topic(STAFF_GROUP_ID, thread_id)
                    except: pass
                    
        except Exception as e:
            logger.error(f"Ошибка Санитара Архивов: {e}")
        
        # Засыпаем ровно на 1 час (3600 секунд), затем повторяем проверку
        time.sleep(3600)

# Запускаем Санитара в отдельном фоновом потоке при старте файла
threading.Thread(target=ticket_sweeper_task, daemon=True).start()