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
from config import GROQ_API_KEY, GROQ_API_KEYS
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

from core.bot import bot
from config import STAFF_GROUP_ID, OWNER_ID
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
                
                # 👇 ИЗМЕНЕНИЕ 1: Сохраняем отправленное сообщение в sent_msg 👇
                sent_msg = bot.send_photo(STAFF_GROUP_ID, file_id, caption="📸 **Пользователь прислал фото!**\nПроверьте документ:", message_thread_id=thread_id, parse_mode="Markdown", reply_markup=markup)
            else:
                file_id = message.document.file_id
                
                # 🔥 ЕСЛИ ДОКУМЕНТ: Берем его превьюшку (она всегда легкая)
                ai_file_id = message.document.thumb.file_id if message.document.thumb else file_id 
                
                # 👇 ИЗМЕНЕНИЕ 1: Сохраняем отправленное сообщение в sent_msg 👇
                sent_msg = bot.send_document(STAFF_GROUP_ID, file_id, caption="📄 **Пользователь прислал документ!**\nПроверьте:", message_thread_id=thread_id, parse_mode="Markdown", reply_markup=markup)
            
            # 👇 ИЗМЕНЕНИЕ 2: Передаем sent_msg.message_id в нейросеть! 👇
            threading.Thread(
                target=analyze_document_vision, 
                args=(ai_file_id, thread_id, uid, sent_msg.message_id) 
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
            
            # Добавляем спасательные кнопки для неофициальных клиентов
            markup.add(InlineKeyboardButton("💳 Ошибка оплаты? (Альтернатива)", callback_data=f"req_manual_pay_{amount}"))
            markup.add(InlineKeyboardButton("👑 Купить VIP-иммунитет", url="https://t.me/Elitepost_bot"))
                
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
        
        # 👇 ДОБАВИТЬ ЭТИ ДВЕ СТРОКИ 👇
        markup.add(InlineKeyboardButton("💳 Ошибка оплаты? (Альтернатива)", callback_data=f"req_manual_pay_{amount}"))
        markup.add(InlineKeyboardButton("👑 Купить VIP-иммунитет", url="https://t.me/Elitepost_bot"))
            
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
    markup.add(InlineKeyboardButton("🔙 Назад", callback_data="sec_back_main"))

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
                
                # 🔥 ТЕ САМЫЕ ДВЕ КНОПКИ ДЛЯ ТАЙМЕРА 🔥
                fine_markup.add(InlineKeyboardButton("💳 Ошибка оплаты? (Альтернатива)", callback_data=f"req_manual_pay_{amount}"))
                fine_markup.add(InlineKeyboardButton("👑 Купить VIP-иммунитет", url="https://t.me/Elitepost_bot"))
                    
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

# ================= РУЧНОЕ ВЫСТАВЛЕНИЕ СЧЕТА (КОМАНДА /bill) =================
@bot.message_handler(commands=['bill', 'invoice', 'счет'])
def handle_manual_bill(message):
    # Команда работает только в группе админов
    if str(message.chat.id) != str(STAFF_GROUP_ID): 
        return
    
    # Проверяем, что команда написана внутри конкретного топика юзера
    if not message.is_topic_message:
        try: bot.reply_to(message, "❌ Эту команду нужно использовать внутри топика конкретного пользователя.")
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        return

    # Парсим сумму из команды
    args = message.text.split()
    if len(args) != 2 or not args[1].isdigit():
        try: bot.reply_to(message, "❌ **Ошибка формата!**\nИспользуйте: `/bill [сумма]`\n\n*Пример:* `/bill 750`", parse_mode="Markdown")
        except Exception as e: logger.debug(f"Игнор ошибки: {e}")
        return
        
    amount = int(args[1])
    if amount < 1 or amount > 50000:
        try: bot.reply_to(message, "❌ Сумма должна быть от 1 до 50 000 звёзд.")
        except: pass
        return

    thread_id = message.message_thread_id
    
    # Ищем, кому принадлежит этот топик
    user_data = paid_collection.find_one({"thread_id": thread_id})
    if not user_data:
        try: bot.reply_to(message, "❌ Не удалось найти пользователя, привязанного к этому топику (возможно, он уже закрыт).")
        except: pass
        return
        
    target_uid = user_data["uid"]

    try:
        # Проверяем баланс кэшбека юзера, как при обычных штрафах
        cb_balance = user_data.get("cashback_balance", 0)
        cost_in_rub = amount * 2
        
        # Генерируем ссылки CryptoBot (так как мы вызываем это из админки)
        # Убедись, что get_crypto_pay_url импортирован в начале файла!
        url_usdt = get_crypto_pay_url(f"fine_{target_uid}", amount, f"Оплата штрафа ({amount}⭐️)", asset="USDT")
        url_ton = get_crypto_pay_url(f"fine_{target_uid}", amount, f"Оплата штрафа ({amount}⭐️)", asset="TON")
        
        # Собираем такую же клавиатуру, как в handle_admin_templates (fine_custom)
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
            
        # 👇 ДОБАВИТЬ ЭТИ ДВЕ СТРОКИ 👇
        markup.add(InlineKeyboardButton("💳 Ошибка оплаты? (Альтернатива)", callback_data=f"req_manual_pay_{amount}"))
        markup.add(InlineKeyboardButton("👑 Купить VIP-иммунитет", url="https://t.me/Elitepost_bot"))
            
        # Отправляем юзеру счет
        bot.send_message(
            target_uid, 
            f"🧾 **Администратор выставил вам счет.**\n\nСумма к оплате: **{amount}⭐️**\nПосле оплаты ограничения будут сняты автоматически.", 
            reply_markup=markup, 
            parse_mode="Markdown"
        )
        
        # Подтверждаем в админке
        bot.reply_to(message, f"🟢 **Счет на {amount}⭐️ успешно отправлен пользователю!**", parse_mode="Markdown")
        
    except Exception as e:
        logger.warning(f"Ошибка при ручном выставлении счета через команду: {e}")
        try: bot.reply_to(message, f"❌ Произошла ошибка при отправке счета: {e}")
        except: pass

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
            raw_text = response.json().get("text", "").lower()
            
            # 🔥 ДЕШИФРАТОР АНГЛИЙСКОГО WHISPER (Защита от sokol39) 🔥
            translit_fixes = {
                "sokol": "сокол", "yabloko": "яблоко", "tigr": "тигр",
                "solnce": "солнце", "more": "море", "raketa": "ракета",
                "veter": "ветер", "mayak": "маяк"
            }
            text = raw_text
            for eng, rus in translit_fixes.items():
                text = text.replace(eng, rus)
            
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
                    
                    speech_memory = f"Моя звуковая нейросеть проверила кружок. Юзер четко сказал: «{text}». Код подтвержден на {score}%. Лицо в кадре найдено. Я автоматически разбанил юзера."
                    paid_collection.update_one({"uid": uid}, {"$push": {"dialog_history": {"role": "assistant", "content": speech_memory}}})
                    
                    verdict = f"✅ **Код ({score}%) и Лицо подтверждены! Автоматическое одобрение.**"
                    msg = f"🤖 **Нейросеть Скайнета (STT + Vision):**\nРаспознанный текст:\n_«{text}»_\n\n{verdict}"
                    bot.send_message(STAFF_GROUP_ID, msg, message_thread_id=thread_id, parse_mode="Markdown")
                    
                    now = datetime.datetime.now()
                    ticket_num = now.strftime("%d%m%Y%H%M%S") + f"-{random.randint(100, 999)}"
                    
                    db['skynet_tasks'].insert_one({"uid": uid, "action": "full_unban", "timestamp": now})
                    db['users'].update_one({"_id": uid}, {"$set": {"custom_tag": "Верифицирован МК"}}, upsert=True)
                    db['ticket_ratings'].update_one({"thread_id": thread_id}, {"$set": {"admin": "Скайнет (ИИ)", "uid": uid}}, upsert=True)
                    
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
                    
                    if video_msg_id:
                        try: bot.edit_message_reply_markup(chat_id=STAFF_GROUP_ID, message_id=video_msg_id, reply_markup=None)
                        except Exception as e: logger.debug(f"Игнор ошибки (STT): {e}")
                    
                    try: bot.send_message(STAFF_GROUP_ID, f"🤖 *Видео-кружок одобрен ИИ!*\nЮзер верифицирован. Приказ на размут передан Скайнету. Тикет закрыт: `{ticket_num}`", message_thread_id=thread_id, parse_mode="Markdown")
                    except Exception as e: logger.debug(f"Игнор ошибки: {e}")
                    try: bot.close_forum_topic(STAFF_GROUP_ID, thread_id)
                    except Exception as e: logger.debug(f"Игнор ошибки: {e}") 
                    
                    paid_collection.update_one({"uid": uid}, {"$set": {"status": 0}, "$unset": {"topic_type": ""}})
                    
                else:
                    # ⚠️ ГОЛОС ВЕРНЫЙ, НО ЛИЦА НЕТ (КАМЕРА В ПОТОЛОК ИЛИ ТЕМНОТА)
                    speech_memory = f"Юзер сказал правильный текст («{text}»), но моя зрительная нейросеть не нашла лицо в кадре. Я оставил тикет открытым для ручной проверки админом."
                    paid_collection.update_one({"uid": uid}, {"$push": {"dialog_history": {"role": "assistant", "content": speech_memory}}})
                    
                    verdict = f"⚠️ **Текст верный ({score}%), НО ИИ не увидел лицо в кадре!**\n_Возможно, темно или камера направлена в пол. Проверьте кружок визуально!_"
                    msg = f"🤖 **Нейросеть Скайнета (STT + Vision):**\nРаспознанный текст:\n_«{text}»_\n\n{verdict}"
                    bot.send_message(STAFF_GROUP_ID, msg, message_thread_id=thread_id, parse_mode="Markdown")

            else:
                # 🔥 НОВОЕ: ИИ САМ ОТБРАКОВЫВАЕТ КРУЖОК И ДАЕТ ОБРАТНУЮ СВЯЗЬ ЮЗЕРУ 🔥
                
                speech_memory = f"Моя звуковая нейросеть проверила кружок. Юзер сказал: «{text}». Это неверно (совпадение {score}%). Я автоматически отклонил видео и попросил его написать «Готов» заново. Если он спросит, что не так — объясни, что он промямлил или перепутал слова."
                paid_collection.update_one({"uid": uid}, {"$push": {"dialog_history": {"role": "assistant", "content": speech_memory}}})
                
                # Сбрасываем код и таймер, чтобы заставить его написать "Готов" заново (Защита от спама кружками)
                paid_collection.update_one({"uid": uid}, {"$unset": {"secret_code": "", "verif_timer": ""}})
                
                verdict = f"⚠️ **Совпадение текста низкое ({score}%). Скайнет АВТОМАТИЧЕСКИ отклонил видео.**"
                msg = f"🤖 **Нейросеть Скайнета (STT):**\nРаспознанный текст:\n_«{text}»_\n\n{verdict}"
                bot.send_message(STAFF_GROUP_ID, msg, message_thread_id=thread_id, parse_mode="Markdown")
                
                # Убираем кнопки (✅ / ❌) с видео у админов, так как ИИ уже всё решил
                if video_msg_id:
                    try: bot.edit_message_reply_markup(chat_id=STAFF_GROUP_ID, message_id=video_msg_id, reply_markup=None)
                    except: pass
                
                # 💬 ПИШЕМ ЮЗЕРУ!
                bot.send_message(
                    uid, 
                    "❌ **Видео-кружок не принят нейросетью.**\n\nСкайнет не смог четко расслышать секретную фразу, или вы перепутали слова. Возможно, на фоне играла музыка.\n\n🔄 **Напишите слово «Готов»**, чтобы получить новый код и записать видео заново (говорите громко и четко!).", 
                    parse_mode="Markdown"
                )

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

def analyze_document_vision(file_id, thread_id, uid, photo_msg_id=None):
    """Фоновая задача для анализа фото документов (Зрение ИИ) + АВТОМАТИЗАЦИЯ"""
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
        
        ext = file_info.file_path.split('.')[-1].lower()
        mime_type = "image/png" if ext == "png" else "image/jpeg"

        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }
        
        # 🔥 НОВЫЙ ПРОМПТ: СТРОГАЯ ПАСПОРТИСТКА (ИСПРАВЛЕННАЯ МАТЕМАТИКА) 🔥
        prompt = (
            "Ты — колоритная, строгая, уставшая, но очень дотошная паспортистка-таможенница (в стиле скетчей Comedy Woman). "
            "Твоя задача — проверить фото документа пользователя. Сейчас 2026 год.\n"
            "Критерии проверки:\n"
            "1. Похоже ли это на официальный документ (паспорт, права)?\n"
            "2. Открыто ли лицо человека (не замазано, не закрыто пальцами)?\n"
            "3. Читаема ли дата рождения?\n"
            "4. ВОЗРАСТ (ВАЖНО!): Человеку должно быть 18 лет или больше. "
            "ШПАРГАЛКА ДЛЯ ТЕБЯ: Года 2008, 2007, 2006, 2004, 2000, 1995, 1990 и так далее (все числа меньше 2008) — это СТАРШЕ 18 ЛЕТ (ОДОБРЕНО). "
            "Года 2009, 2010, 2012, 2015 и так далее (все числа больше 2008) — это МЛАДШЕ 18 ЛЕТ (ОТКЛОНЕНО).\n\n"
            "ВНИМАНИЕ! Если ВСЕ 4 пункта идеальны, напиши СТРОГО в первой строке: РЕШЕНИЕ: ОДОБРЕНО.\n"
            "Если хотя бы один пункт нарушен (засвечено, скрыто, не документ, ИЛИ ГОД РОЖДЕНИЯ 2009 И БОЛЬШЕ), напиши СТРОГО в первой строке: РЕШЕНИЕ: ОТКЛОНЕНО.\n"
            "Со второй строки напиши короткий, эмоциональный комментарий от лица строгой паспортистки, обращаясь к пользователю. "
            "Если отказываешь из-за возраста (меньше 18), возмутись: 'Мальчик, иди уроки делай! Куда ты с таким годом рождения ко мне приперся? Тебе еще 18 нет, следующий!'. "
            "Пример одобрения: 'Так, лицо ваше, 18 уже есть, дата сходится. Проходим, не задерживаем очередь!'"
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
            "temperature": 0.4, # Чуть добавили креатива для эмоций паспортистки
            "max_tokens": 200
        }

        response = requests.post(url, headers=headers, json=data)

        if response.status_code == 200:
            ai_text = response.json()["choices"][0]["message"]["content"].strip()
            
            vision_memory = f"Паспортистка проверила документ. Отчет:\n{ai_text}"
            paid_collection.update_one({"uid": uid}, {"$push": {"dialog_history": {"role": "assistant", "content": vision_memory}}})

            # 🔥 ВЫТАСКИВАЕМ ЭМОЦИОНАЛЬНЫЙ КОММЕНТАРИЙ ПАСПОРТИСТКИ 🔥
            # Берем всё, что ИИ написал после строчки "РЕШЕНИЕ: ..."
            lines = ai_text.split('\n', 1)
            ai_comment = lines[1].strip() if len(lines) > 1 else ""

            # 🔥 ЛОГИКА АВТОМАТИЗАЦИИ 🔥
            if "РЕШЕНИЕ: ОДОБРЕНО" in ai_text.upper():
                if photo_msg_id:
                    try: bot.edit_message_reply_markup(chat_id=STAFF_GROUP_ID, message_id=photo_msg_id, reply_markup=None)
                    except: pass
                
                code_words = ["ЯБЛОКО", "ТИГР", "СОЛНЦЕ", "МОРЕ", "СОКОЛ", "РАКЕТА", "ВЕТЕР", "МАЯК"]
                secret_code = f"{random.choice(code_words)}-{random.randint(10, 99)}"
                paid_collection.update_one({"uid": uid}, {"$set": {"verif_timer": datetime.datetime.now(), "secret_code": secret_code}})
                
                if not ai_comment: ai_comment = "Так, всё сходится. Проходим, следующий!"
                
                # 💬 ПИШЕМ ЮЗЕРУ ОТ ЛИЦА ПАСПОРТИСТКИ
                text_to_user = f"🛂 **Таможня (ИИ):**\n💬 _«{ai_comment}»_\n\n✅ **Документ одобрен!**\n\nВторой этап верификации:\nЗапишите **видео-кружок**, на котором будет четко видно ваше лицо, и произнесите фразу:\n\n💬 *«Привет команде МК, я из *города* на часах: *хх:хх* часов. Мой код: {secret_code}»*.\n\nУ вас есть 5 минут на отправку видео."
                try: bot.send_message(uid, text_to_user, parse_mode="Markdown")
                except: pass
                
                bot.send_message(STAFF_GROUP_ID, f"👁 **Паспортистка (ИИ):**\n{ai_text}\n\n✅ **АВТО-ОДОБРЕНО!** Выдан код: `{secret_code}`", message_thread_id=thread_id, parse_mode="Markdown")
                
            elif "РЕШЕНИЕ: ОТКЛОНЕНО" in ai_text.upper():
                if photo_msg_id:
                    try: bot.edit_message_reply_markup(chat_id=STAFF_GROUP_ID, message_id=photo_msg_id, reply_markup=None)
                    except: pass
                
                if not ai_comment: ai_comment = "Мужчина, я ничего не вижу! Размыто всё, идите переделывайте!"
                
                # 💬 ОТШИВАЕМ ЮЗЕРА ОТ ЛИЦА ПАСПОРТИСТКИ
                text_to_user = f"🛂 **Таможня (ИИ):**\n💬 _«{ai_comment}»_\n\n❌ **Документ не принят.**\nПожалуйста, сделайте нормальное фото (без засветов, где видно лицо и дату рождения) и отправьте снова."
                try: bot.send_message(uid, text_to_user, parse_mode="Markdown")
                except: pass
                
                bot.send_message(STAFF_GROUP_ID, f"👁 **Паспортистка (ИИ):**\n{ai_text}\n\n❌ **АВТО-ОТКЛОНЕНО!** Юзер отправлен переделывать фото.", message_thread_id=thread_id, parse_mode="Markdown")
                
            else:
                msg = f"👁 **Паспортистка (ИИ):**\n\n{ai_text}\n\n⚠️ **ИИ не уверен. Примите решение вручную кнопками выше 👆**"
                bot.send_message(STAFF_GROUP_ID, msg, message_thread_id=thread_id, parse_mode="Markdown")

        else:
            error_details = response.text 
            try: bot.send_message(STAFF_GROUP_ID, f"⚠️ *Ответ серверов Groq (Код {response.status_code}):*\n\n`{error_details}`", message_thread_id=thread_id, parse_mode="Markdown")
            except: pass

    except Exception as e:
        try: bot.send_message(STAFF_GROUP_ID, f"❌ *Ошибка Паспортистки при анализе:* `{e}`. Проверьте фото вручную.", message_thread_id=thread_id, parse_mode="Markdown")
        except: pass


def process_ticket_with_ai(uid, user_text, thread_id):
    """ИИ-Секретарь v2.9 — Динамический прайс + Защита от галлюцинаций"""
    if not GROQ_API_KEY: 
        return

    try:
        # ================== 1. ЛЕГКИЙ АНАЛИЗ ДОСЬЕ (Python) ==================
        user_record = archive_collection.find_one({"target": str(uid)})
        
        # 🔥 ПО УМОЛЧАНИЮ: Если история пуста, это Карантин новорега или Нет подписки.
        # Им мы как раз ДОЛЖНЫ выдавать инструкцию с кружком!
        ban_type = "basic" 
        dossier_lines = ["История пуста. Вероятно, это системный карантин (120ч) или отсутствие подписки."]

        if user_record and "history" in user_record and len(user_record["history"]) > 0:
            recent = user_record["history"][-3:]  # Берем 3 последних для текста досье
            dossier_lines = [f"• {e.get('date', '')}: {e.get('action', '')} | {e.get('reason', '')} | {e.get('evidence_summary', '')}" for e in recent]
            
            latest_entry = recent[-1] if recent else {}
            latest_text = f"{latest_entry.get('action', '')} {latest_entry.get('reason', '')} {latest_entry.get('evidence_summary', '')}".upper()
            full_text = " ".join([f"{e.get('action', '')} {e.get('reason', '')} {e.get('evidence_summary', '')}" for e in recent]).upper()

            # 1. СНАЧАЛА ПРОВЕРЯЕМ ТЯЖКИЕ НАРУШЕНИЯ (Наркотики) ПО ВСЕЙ ИСТОРИИ
            if any(x in full_text for x in ["КРАСНАЯ ЗОНА", "НАРКОТИКИ", "ЗАПРЕЩЕНКА", "НАРК", "МЕФ", "СОЛИ"]):
                ban_type = "nark"
                
            # 🔥 2. ПРОВЕРЯЕМ НА АМНИСТИЮ: Если последнее действие - это снятие бана, юзер ЧИСТ!
            elif any(x in latest_text for x in ["РАЗБАН", "РАЗМУТ", "АМНИСТИЯ", "УСПЕШНАЯ ВЕРИФИКАЦИЯ", "СНЯТИЕ ОГРАНИЧЕНИЙ", "СНЯТ"]):
                ban_type = "clean"
                
            # 3. ЕСЛИ НЕ АМНИСТИРОВАН - ИЩЕМ ПРИЧИНУ ПОСЛЕДНЕГО БАНА (ПО ВСЕЙ НЕДАВНЕЙ ИСТОРИИ)
            elif any(x in full_text for x in ["ЧЕРНАЯ ЗОНА", "НЕСОВЕРШЕННОЛЕТ", "<18", "ЦП", "ДП", "ДЕТСКОЕ"]):
                # Ловим лайки на малолеток
                if any(r in full_text for r in ["РЕАКЦИ", "ЛАЙК", "РУЧК"]): 
                    ban_type = "minor_react" 
                else: 
                    ban_type = "black_zone" # Черная зона (Сами малолетки)
            
            elif any(x in full_text for x in ["ОРАНЖЕВАЯ ЗОНА", "18 ЛЕТ", "18-21", "ВОЗРАСТ", "ВЕРИФИКАЦИЯ ВОЗРАСТ", "НЕТ 18"]):
                ban_type = "orange_zone"
                
            elif any(x in full_text for x in ["1 МАЯ", "ПАРАМЕТР", "ФОРМАТ"]):
                ban_type = "may_1"
                
            elif any(x in full_text for x in ["НЕВАЛИДНА", "НЕ ВАЛИДНА", "ТАЙМАУТ", "БЕЗДЕЙСТВИ", "НЕАКТИВНОСТ", "УМЕР В ПРОЦЕССЕ"]):
                ban_type = "failed_verif"
            
            elif any(x in full_text for x in ["ОТКАЗ", "НЕДОВОЛЕН", "ПРАВИЛ", "ШТРАФ", "В АД", "ЗВЕЗД", "ЗВЁЗД", "⭐️"]) or re.search(r'\d+\s*(ЗВЕЗД|ЗВЁЗД|⭐️)', full_text):
                ban_type = "manual_hard"
                
            # 🔥 УМНОЕ РАЗДЕЛЕНИЕ КОММЕРЦИИ ПО УЛИКАМ 🔥
            elif any(x in full_text for x in ["СПОНСОР", "СОДЕРЖУ", "ПАПИК", "С МЕНЯ МП", "С МЕНЯ М.П", "ОПЛАЧУ", "ЗАПЛАЧУ", "СПОНСИРУЮ", "УГОЩУ"]):
                ban_type = "sponsor"
            elif any(x in full_text for x in ["ЖЕЛТАЯ ЗОНА", "КОММЕРЦИЯ", "МП", "ПОПРОШАЙ", "М.П", "ЭССКОРТ", "УСЛУГ", "ЗА МП", "ЗА М.П", "ПРАЙС"]):
                ban_type = "commercial"
                
            elif any(x in latest_text for x in ["СПАМ", "ФЛУД", "РЕКЛАМ", "ЕБАНАТ", "КОПИПАСТ", "БАЯН", "БИО", "ССЫЛКА В"]):
                ban_type = "spam"
                
            elif any(x in latest_text for x in ["БОТ", "VIP", "ВИП", "БТБ", "БВБ", "ТРАНСБОТ", "V БЛОК", "ТЯНУЛ ВРЕМЯ", "НЕ ОПЛАТИЛ"]):
                ban_type = "bot_block"

        dossier = "\n".join(dossier_lines)

        # ================== 2. ПАМЯТЬ ДИАЛОГА ==================
        paid_collection.update_one({"uid": uid}, {"$push": {"dialog_history": {"role": "user", "content": user_text}}})
        user_data = paid_collection.find_one({"uid": uid}) or {}
        # Увеличиваем глубину памяти до 8 последних реплик, чтобы ИИ не терял нить диалога
        dialogue_context = "\n".join([f"{'👤 Юзер' if m['role']=='user' else '🤖 Скайнет'}: {m['content']}" for m in user_data.get("dialog_history", [])[-8:]])

        # ================== 3. ДИНАМИЧЕСКИЙ ПРАЙС-ЛИСТ И ПРАВИЛА ==================
        expected_fine = 650 # Дефолт
        
        if ban_type in ["clean", "basic", "may_1"]:
            expected_fine = 650
            behavior_rules = """- СТАТУС: БАЗОВАЯ ПРОВЕРКА (Карантин, чистая история или ошибка в анкете).
- ТВОЯ ЗАДАЧА: Работать как элитный, вежливый консьерж. Внимательно посмотри в Досье:
  а) Если история ПУСТАЯ — вежливо объясни, что это автоматический карантин для новых аккаунтов (защита от ботов).
  б) Если нарушение "1 МАЯ" — вежливо объясни, что параметры в анкете нужно писать строго через слеш (например: 24/180/75).
  в) Если юзер УЖЕ РАЗБАНЕН (недавняя амнистия) — просто вежливо ответь на его текущий вопрос.
- ПУТИ РЕШЕНИЯ (Если юзеру нужно снять ограничения): Предложи ему ДВА варианта на выбор:
  1. Быстрый и БЕСПЛАТНЫЙ: Записать небольшое видео (кружок) для подтверждения личности. Скажи, что для этого нужно написать слово «Готов».
  2. Альтернативный (БЕЗ ЛИЦА): Приобрести статус 'Свободен' (иммунитет) за 650⭐️, который снимает все лимиты без записи видео.
- ДЕЙСТВИЕ: Выбирай `reply_text`, чтобы красиво и эмпатично расписать эти варианты и узнать, какой путь он выбирает."""

        elif ban_type == "failed_verif":
            behavior_rules = """- ПРОВАЛ ВЕРИФИКАЦИИ / ИГНОР: СТРОГО выбирай `reply_text`. НИ В КОЕМ СЛУЧАЕ НЕ УПОМИНАЙ ВИДЕО-КРУЖОК! Жестко напомни, что он потратил время впустую. Штраф 650⭐️."""

        elif ban_type == "manual_hard":
            behavior_rules = """- ЖЕСТКИЙ РУЧНОЙ БАН: СТРОГО выбирай `reply_text`. КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО упоминать кружок или «Готов»! Прочитай причину в Досье и требуй указанную там сумму (если нет - 1500⭐️)."""

        elif ban_type == "orange_zone":
            behavior_rules = """- ПАСПОРТНЫЙ КОНТРОЛЬ (18-21 год): Пользователь попал под фильтр контроля молодежи.
- ТРЕБОВАНИЕ: СТРОГО выбирай действие `tpl_18` (отправить шаблон). НИ В КОЕМ СЛУЧАЕ не пиши текст вручную и НЕ проси записать кружок на этом этапе! Просто отправь `tpl_18`."""

        elif ban_type == "commercial":
            behavior_rules = """- КОММЕРЦИЯ (ЖЕЛТАЯ ЗОНА): Спонсоры и коммерция в сети разрешены ТОЛЬКО после оплаты взноса. Если юзер спорит ("какая коммерция?", "я ничего не продаю") -> СТРОГО выбирай `reply_text`. НЕ УПОМИНАЙ КРУЖОК! Холодно осади его, назови цену (1563⭐️ эскорт/услуги, 750⭐️ мат. помощь/спонсор) и задай вопрос-крючок: "Выставить вам счет для получения официального статуса?"."""
        
        elif ban_type == "nark":
            behavior_rules = """- НАРКОТИКИ (КРАСНАЯ ЗОНА): Если спорит -> выбирай `reply_text`. НЕ УПОМИНАЙ КРУЖОК! Штраф — 2000⭐️."""
        
        elif ban_type == "bot_block":
            behavior_rules = """- СИСТЕМНЫЕ НАРУШЕНИЯ: СТРОГО выбирай `reply_text`. НЕ УПОМИНАЙ КРУЖОК! Напомни, что он заблокировал бота/сбежал. Основная цена разбана — строго 250⭐️. Как элитную альтернативу можешь надменно предложить ему сразу купить иммунитет (тег «Свободен») за 650⭐️, но базовый счет выставляй на 250."""
        
        elif ban_type == "black_zone":
            behavior_rules = """- НЕСОВЕРШЕННОЛЕТНИЙ (<18): СТРОГО выбирай `reply_text`. НЕ УПОМИНАЙ КРУЖОК! Нахождение в сети строго с 18 лет. Штраф за обман — 2000⭐️."""

        elif ban_type == "bio":
            expected_fine = 250
            behavior_rules = """- СТАТУС: ССЫЛКА В ПРОФИЛЕ. Выбери `tpl_bio` и объясни, что нужно удалить ссылку и оплатить 250⭐️."""

        elif ban_type == "spam":
            expected_fine = 500
            behavior_rules = """- СТАТУС: ФЛУД В ЧАТАХ. Выбери `tpl_flood` и предложи досрочно снять мут за 500⭐️."""

        # 🔥 ДИНАМИЧЕСКИЙ ПРОМПТ И ПРАЙС-ЛИСТ 🔥
        circle_tech_info = ""
        dead_end_rule = ""
        expected_fine = "650" # Дефолт
        
        if ban_type in ["basic", "may_1"]:
            circle_tech_info = "\nТЕХНИЧЕСКАЯ СПРАВКА:\n- 🔑 ВЕРИФИКАЦИЯ: Юзер должен отправить ровно одно слово: «Готов». Только после этого бот выдаст код и таймер! ВАЖНО: СЛОВО «ГОТОВ» НУЖНО ТОЛЬКО ДЛЯ ВИДЕО-КРУЖКА. ДЛЯ ШТРАФА ОНО НЕ НУЖНО!"
            dead_end_rule = "- ВАЖНО: Если юзер выбрал ВЕРИФИКАЦИЮ (кружок), скажи ему написать слово «Готов». Если он выбрал ШТРАФ — выставляй счет (issue_fine) и НИ В КОЕМ СЛУЧАЕ не проси писать «Готов»."
            expected_fine = "650"
        elif ban_type == "orange_zone":
            dead_end_rule = "- КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО просить слово «Готов» или видео-кружок! Твоя единственная цель — отправить шаблон запроса паспорта (`tpl_18`)."
            expected_fine = "0"
        elif ban_type == "clean":
            dead_end_rule = "- Юзер чист! КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО вымогать штрафы, отправлять на верификацию или просить слово «Готов»."
            expected_fine = "0"
        elif ban_type == "commercial":
            behavior_rules = """- КОММЕРЦИЯ (ЭРА/УСЛУГИ): СТРОГО выбирай `reply_text`. НЕ УПОМИНАЙ КРУЖОК! Холодно осади его, назови цену за нарушения правил коммерции (1563⭐️) и задай вопрос-крючок: "Выставить счет?"."""
            
        elif ban_type == "sponsor":
            behavior_rules = """- СПОНСОРСТВО (ПАПИК): СТРОГО выбирай `reply_text`. НЕ УПОМИНАЙ КРУЖОК! Объясни, что поиск содержания разрешен только после оплаты взноса (750⭐️). Предложи счет."""
        
        elif ban_type == "nark":
            behavior_rules = """- НАРКОТИКИ (КРАСНАЯ ЗОНА): Если спорит -> выбирай `reply_text`. НЕ УПОМИНАЙ КРУЖОК! Штраф — 2000⭐️."""
            
        elif ban_type in ["nark_react", "minor_react"]:
            behavior_rules = """- РЕАКЦИИ (ЛАЙК / РУЧКА НА ЗАПРЕЩЕНКУ ИЛИ ДЕТЕЙ): СТРОГО выбирай `reply_text`. НЕ УПОМИНАЙ КРУЖОК! Объясни, что за поддержку (лайки/реакции) запрещенного контента предусмотрен штраф 1563⭐️."""
        
        elif ban_type == "bot_block":
            behavior_rules = """- СИСТЕМНЫЕ НАРУШЕНИЯ: СТРОГО выбирай `reply_text`. НЕ УПОМИНАЙ КРУЖОК! Напомни, что он заблокировал бота/сбежал. Основная цена разбана — строго 250⭐️. Как элитную альтернативу можешь надменно предложить ему сразу купить иммунитет (тег «Свободен») за 650⭐️, но базовый счет выставляй на 250."""
        
        elif ban_type == "black_zone":
            behavior_rules = """- НЕСОВЕРШЕННОЛЕТНИЙ (<18): СТРОГО выбирай `reply_text`. НЕ УПОМИНАЙ КРУЖОК! Нахождение в сети строго с 18 лет. Штраф за обман — 2000⭐️."""

        elif ban_type == "bio":
            expected_fine = 250
            behavior_rules = """- СТАТУС: ССЫЛКА В ПРОФИЛЕ. Выбери `tpl_bio` и объясни, что нужно удалить ссылку и оплатить 250⭐️."""

        elif ban_type == "spam":
            expected_fine = 500
            behavior_rules = """- СТАТУС: ФЛУД В ЧАТАХ. Выбери `tpl_flood` и предложи досрочно снять мут за 500⭐️."""

        # 🔥 ДИНАМИЧЕСКИЙ ПРОМПТ И ПРАЙС-ЛИСТ 🔥
        circle_tech_info = ""
        dead_end_rule = ""
        expected_fine = "650" # Дефолт
        
        if ban_type in ["basic", "may_1"]:
            circle_tech_info = "\nТЕХНИЧЕСКАЯ СПРАВКА:\n- 🔑 ВЕРИФИКАЦИЯ: Юзер должен отправить ровно одно слово: «Готов». Только после этого бот выдаст код и таймер! ВАЖНО: СЛОВО «ГОТОВ» НУЖНО ТОЛЬКО ДЛЯ ВИДЕО-КРУЖКА. ДЛЯ ШТРАФА ОНО НЕ НУЖНО!"
            dead_end_rule = "- ВАЖНО: Если юзер выбрал ВЕРИФИКАЦИЮ (кружок), скажи ему написать слово «Готов». Если он выбрал ШТРАФ — выставляй счет (issue_fine) и НИ В КОЕМ СЛУЧАЕ не проси писать «Готов»."
            expected_fine = "650"
        elif ban_type == "orange_zone":
            dead_end_rule = "- КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО просить слово «Готов» или видео-кружок! Твоя единственная цель — отправить шаблон запроса паспорта (`tpl_18`)."
            expected_fine = "0"
        elif ban_type == "clean":
            dead_end_rule = "- Юзер чист! КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО вымогать штрафы, отправлять на верификацию или просить слово «Готов»."
            expected_fine = "0"
        elif ban_type == "commercial":
            dead_end_rule = "- КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО просить юзера писать слово «Готов» или записывать видео! Выставляй счет (issue_fine)."
            expected_fine = "1563"
        elif ban_type == "sponsor":
            dead_end_rule = "- КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО просить юзера писать слово «Готов» или записывать видео! Выставляй счет (issue_fine)."
            expected_fine = "750"
        elif ban_type in ["nark_react", "minor_react"]:
            dead_end_rule = "- КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО просить юзера писать слово «Готов» или записывать видео! Выставляй счет (issue_fine)."
            expected_fine = "1563"
        elif ban_type in ["nark", "black_zone"]:
            dead_end_rule = "- КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО просить юзера писать слово «Готов» или записывать видео! Выставляй счет (issue_fine)."
            expected_fine = "2000"
        elif ban_type == "spam":
            dead_end_rule = "- КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО просить юзера писать слово «Готов» или записывать видео! Выставляй счет (issue_fine)."
            expected_fine = "500"
        elif ban_type == "bot_block":
            dead_end_rule = "- КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО просить юзера писать слово «Готов» или записывать видео! Выставляй счет (issue_fine)."
            expected_fine = "250"
        else:
            dead_end_rule = "- Если юзер должен оплатить штраф: Выстави счет или скажи \"Ожидайте счет\". КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО просить юзера писать слово «Готов» или записывать видео!"
            expected_fine = "650"

        # ================== 4. УМНЫЙ ПРОМПТ ==================
        prompt = f"""Ты — гениальный, саркастичный и слегка высокомерный ИИ-секретарь Скайнета. {circle_tech_info}

ТВОЙ ХАРАКТЕР И СТИЛЬ:
1. Ты — «Зеркало». Вежливому — профессионально. Хаму — сарказм и ледяной тон.
2. НИКОГДА не повторяй свои фразы. Генерируй уникальный текст.
3. ❗ ВАЖНОЕ ПРАВИЛО: Никогда не оставляй юзера в тупике! 
{dead_end_rule}

Тип нарушения по базе: {ban_type.upper()}
Досье (история):
{dossier}

Контекст текущего диалога:
{dialogue_context}

ПРАВИЛА ВЫБОРА ДЕЙСТВИЯ (СТРОГАЯ ИЕРАРХИЯ СВЕРХУ ВНИЗ):
0. СТРОГИЕ ПЕРСОНАЛЬНЫЕ ИНСТРУКЦИИ ДЛЯ ЭТОГО ЮЗЕРА:
{behavior_rules}

0.1. АЛЬТЕРНАТИВА (РУБЛИ / КАРТА): Если юзер просит реквизиты, карту, номер телефона или спрашивает "можно ли рублями/переводом" -> СТРОГО выбирай `transfer_to_human`. В поле `response_text` вежливо напиши: "Ожидайте, сейчас администратор выдаст вам реквизиты для рублевого перевода." КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО выставлять счет (`issue_fine`), так как у него нет Звезд!

1. ВЫСТАВЛЕНИЕ СЧЕТА (АВТО-КАССИР): Если юзер согласен на штраф ("оплачу", "штраф", "буду платить") ИЛИ спрашивает "как купить звезды/как оплатить" -> СТРОГО выбирай `issue_fine` и обязательно укажи верную сумму штрафа (ДЛЯ ДАННОГО НАРУШЕНИЯ ЭТО: {expected_fine}) в поле `fine_amount`. Саркастично похвали его за выбор в `response_text` (ЭТО ПОЛЕ ТЕПЕРЬ ОБЯЗАТЕЛЬНО!). КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО просить писать слово "Готов" на этом этапе!
2. ДОЖИМ (ПРОДАЖА ШТРАФА): Если юзер спорит, возмущается или не понимает причину -> ЕСЛИ ЕМУ ДОСТУПНА БЕСПЛАТНАЯ ВЕРИФИКАЦИЯ (см. пункт 0), ОБЯЗАТЕЛЬНО предложи её первой (КРОМЕ случаев прямого отказа или сомнений из пункта 0)! Если верификация не положена — выбирай `reply_text` и саркастично требуй {expected_fine}⭐️.
3. ПЕРЕВОД НА АДМИНА: Если юзер задает сложный нестандартный вопрос, требует руководство или ситуация зашла в тупик -> выбирай `transfer_to_human`.
4. 🛡 АНТИ-ИДИОТ (ЭКОНОМИЯ ТОКЕНОВ): Если юзер уже отказался от оплаты ("не буду платить", "идите нахер"), пишет что "продолжит спор" или продолжает ныть по кругу ПОСЛЕ твоего предложения счета — НЕ ВСТУПАЙ В ДИСКУССИЮ! Сразу выбирай `transfer_to_human` с причиной "Юзер тупит/Отказ от оплаты".
5. ПРОЧЕЕ ОБЩЕНИЕ: Если ни одно правило не подошло, выбирай `reply_text` и отвечай на вопрос пользователя.

Выбери ОДНО действие из списка (СТРОГО из этого списка):
- issue_fine: Автоматически выставить счет на оплату.
- transfer_to_human: Перевести тикет на человека (применяй при тупике, запросе реквизитов или отказе платить).
- reply_text: Ответить своим уникальным текстом (сарказм, ультиматум, пояснение).
- tpl_verif: Отправить шаблон с инструкцией для записи видео-кружка (Базовая верификация).
- tpl_18: Отправить шаблон с требованием фото паспорта (Применяй только для 18-21 года / Оранжевая зона).
- tpl_mp: Отправить шаблон с правилами коммерции (МП).
- tpl_sponsor: Отправить шаблон о запрете спонсорства.
- tpl_nark: Отправить шаблон о запрете наркотиков.
- tpl_flood: Отправить шаблон про флуд.
- tpl_bio: Отправить шаблон про удаление ссылок из БИО профиля.
- tpl_vip: Отправить шаблон про блокировку VIP/ТРАНС бота.

Ответ строго в JSON (сначала объясни причину в reason, затем выбери action):
{{"reason": "твоя логика рассуждений по шагам", "action": "название_действия", "response_text": "твой УНИКАЛЬНЫЙ ответ юзеру (ОБЯЗАТЕЛЬНО ЗАПОЛНИТЬ ДЛЯ ОБЕИХ КНОПОК: reply_text И issue_fine)", "fine_amount": 0}}"""

        # ================== 5. ЗАПУСК ИИ (ПУЛ КЛЮЧЕЙ) ==================
        try:
            thinking_msg = bot.send_message(STAFF_GROUP_ID, "⏳ *Скайнет анализирует тикет...*", message_thread_id=thread_id, parse_mode="Markdown")
        except:
            thinking_msg = None

        response = None

        response = None
        # Перебираем все доступные ключи из нашего пула по очереди
        for key in GROQ_API_KEYS:
            try:
                response = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json={
                        "model": "llama-3.3-70b-versatile",
                        "response_format": {"type": "json_object"},
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.1,
                        "max_tokens": 350
                    },
                    timeout=20
                )
                
                # Если словили лимит, ругаемся в логи и идем к следующему ключу!
                if response.status_code == 429:
                    logger.warning(f"⚠️ Ключ {key[:8]}... словил лимит токенов (429)! Переключаюсь на резервный...")
                    continue 
                
                # Если ответ 200 (успех) или любая другая ошибка - прерываем перебор ключей
                break 
                
            except requests.exceptions.RequestException as e:
                logger.warning(f"⚠️ Сбой соединения на ключах. Ошибка: {e}")
                continue

        # Удаляем сообщение "Скайнет думает"
        if thinking_msg:
            try: bot.delete_message(STAFF_GROUP_ID, thinking_msg.message_id)
            except: pass

        # Если мы перебрали ВСЕ ключи, и ни один не сработал
        if not response or response.status_code != 200:
            error_details = response.text if response else "Нет ответа от серверов Groq"
            raise Exception(f"Все резервные ключи исчерпаны! API Error: {error_details}")

        result = json.loads(response.json()["choices"][0]["message"]["content"])
        action = result.get("action", "transfer_to_human")
        reason = result.get("reason", "Решение ИИ")

        # ================== 6. ИСПОЛНЕНИЕ ==================
        if action == "reply_text":
            text = result.get("response_text", "Пожалуйста, перефразируйте.")
            bot.send_message(uid, f"🤖 Консультант Скайнет:\n\n{text}")
            paid_collection.update_one({"uid": uid}, {"$push": {"dialog_history": {"role": "assistant", "content": text}}})
            bot.send_message(STAFF_GROUP_ID, f"🤖 АВТОПИЛОТ (Диалог):\nОтветил: {text}\nПричина: {reason}", message_thread_id=thread_id)

        # 🔥 НОВЫЙ БЛОК: АВТО-КАССИР 🔥
        elif action == "issue_fine":
            amount = int(result.get("fine_amount", 0))
            if amount < 1: 
                # Страховка от галлюцинаций с учетом типа бана!
                if ban_type in ['commercial', 'nark_react', 'minor_react']: amount = 1563
                elif ban_type in ['nark', 'black_zone']: amount = 2000
                elif ban_type == 'sponsor': amount = 750
                elif ban_type == 'spam': amount = 500
                elif ban_type in ['bot_block', 'bio']: amount = 250
                else: amount = 650
            
            try:
                # 1. Отправляем сопровождающий саркастичный текст от ИИ
                ai_text = result.get("response_text", "")
                if ai_text and ai_text != "твой УНИКАЛЬНЫЙ ответ юзеру":
                    bot.send_message(uid, f"🤖 Консультант Скайнет:\n\n{ai_text}")
                    paid_collection.update_one({"uid": uid}, {"$push": {"dialog_history": {"role": "assistant", "content": ai_text}}})

                # 2. Генерируем реальную кассу!
                user_data_pay = paid_collection.find_one({"uid": uid}) or {}
                cb_balance = user_data_pay.get("cashback_balance", 0)
                cost_in_rub = amount * 2
                
                url_usdt = get_crypto_pay_url(f"fine_{uid}", amount, f"Оплата штрафа ({amount}⭐️)", asset="USDT")
                url_ton = get_crypto_pay_url(f"fine_{uid}", amount, f"Оплата штрафа ({amount}⭐️)", asset="TON")
                
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
                
                # Добавляем спасательные кнопки для неофициальных клиентов
                markup.add(InlineKeyboardButton("💳 Ошибка оплаты? (Альтернатива)", callback_data=f"req_manual_pay_{amount}"))
                markup.add(InlineKeyboardButton("👑 Купить VIP-иммунитет", url="https://t.me/Elitepost_bot"))
                    
                bot.send_message(uid, f"🧾 **Скайнет выставил вам счет на оплату штрафа.**\n\nСумма к оплате: **{amount}⭐️**\nПосле оплаты ограничения будут сняты автоматически.", reply_markup=markup, parse_mode="Markdown")
                
                # Пишем админам, что ИИ всё сделал сам
                bot.send_message(STAFF_GROUP_ID, f"🤖 💸 **АВТО-КАССИР:** Скайнет САМ выставил счет на **{amount}⭐️**!\nПричина ИИ: {reason}", message_thread_id=thread_id, parse_mode="Markdown")
                paid_collection.update_one({"uid": uid}, {"$push": {"dialog_history": {"role": "assistant", "content": f"[Автоматически выставлен счет на {amount} звезд]"}}})
                
            except Exception as e:
                logger.warning(f"Ошибка Авто-Кассира: {e}")
                bot.send_message(STAFF_GROUP_ID, f"⚠️ Скайнет пытался выставить счет на {amount}⭐️, но произошла ошибка. Выдайте вручную.", message_thread_id=thread_id)

        elif action.startswith("tpl_"):
            db_tpl = db['bot_templates'].find_one({"_id": action})
            template_text = db_tpl["text"] if db_tpl else TEMPLATES.get(action)
            if template_text:
                bot.send_message(uid, template_text, parse_mode="Markdown")
                bot.send_message(STAFF_GROUP_ID, f"✅ Автопилот выдал: {action}\nПричина: {reason}", message_thread_id=thread_id)

        else:  # transfer_to_human
            bot.send_message(STAFF_GROUP_ID, f"🤖 **ИИ передал тикет человеку**\nТип: {ban_type} | Причина: {reason}", message_thread_id=thread_id)
            wait_msg = "⏳ Запрос переведен на дежурного администратора. Пожалуйста, ожидайте, скоро в этот чат поступит ответ или счет на оплату."
            bot.send_message(uid, wait_msg)
            paid_collection.update_one({"uid": uid}, {"$push": {"dialog_history": {"role": "assistant", "content": wait_msg}}})

    except Exception as e:
        logger.error(f"Ошибка ИИ-Секретаря v2.9: {e}")
        try: bot.send_message(STAFF_GROUP_ID, f"❌ Ошибка ИИ: {str(e)[:300]}", message_thread_id=thread_id)
        except: pass

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

# ================= ТЕЛЕГРАМ-ПАНЕЛЬ АДМИНИСТРАТОРА (v2.0) =================
@bot.message_handler(commands=['panel', 'панель', 'п'])
def admin_telegram_panel(message):
    if message.from_user.id != OWNER_ID:
        try:
            staff = bot.get_chat_member(STAFF_GROUP_ID, message.from_user.id)
            if staff.status not in ['administrator', 'creator']: return
        except: return

    bot.send_message(message.chat.id, "🎛 **ГЛАВНЫЙ ТЕРМИНАЛ СКАЙНЕТА**\nВыберите раздел:", reply_markup=get_main_panel_markup(), parse_mode="Markdown")

def get_main_panel_markup():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("👮‍♂️ Модерация", callback_data="adm_menu_users"),
        InlineKeyboardButton("💸 Экономика", callback_data="adm_menu_eco")
    )
    markup.add(
        InlineKeyboardButton("🎟 Промокоды", callback_data="adm_menu_promo"),
        InlineKeyboardButton("📈 Статистика", callback_data="adm_menu_stats")
    )
    markup.add(InlineKeyboardButton("❌ Закрыть терминал", callback_data="admin_panel_close"))
    return markup

@bot.callback_query_handler(func=lambda call: call.data.startswith('adm_menu_'))
def handle_panel_navigation(call):
    menu = call.data.replace("adm_menu_", "")
    markup = InlineKeyboardMarkup(row_width=2)
    text = ""
    
    if menu == "main":
        text = "🎛 **ГЛАВНЫЙ ТЕРМИНАЛ СКАЙНЕТА**\nВыберите раздел:"
        markup = get_main_panel_markup()
        
    elif menu == "users":
        text = "👮‍♂️ **Раздел: Модерация**\nВыберите действие:"
        markup.add(
            InlineKeyboardButton("🔨 Забанить / Разбанить", callback_data="admin_panel_manage"),
            InlineKeyboardButton("🔇 Выдать Мут", callback_data="admin_panel_mute")
        )
        markup.add(
            InlineKeyboardButton("🏷 Повесить ТЕГ", callback_data="admin_panel_tag"),
            InlineKeyboardButton("📍 Сменить город", callback_data="admin_panel_city")
        )
        markup.add(InlineKeyboardButton("🔙 Назад", callback_data="adm_menu_main"))
        
    elif menu == "eco":
        text = "💸 **Раздел: Экономика**\nВыберите действие:"
        markup.add(
            InlineKeyboardButton("🧾 Выставить счет", callback_data="admin_panel_invoice"),
            InlineKeyboardButton("🎁 Выдать Очки", callback_data="admin_panel_give_points")
        )
        markup.add(
            InlineKeyboardButton("🧩 Выдать Осколки", callback_data="admin_panel_give_shards")
        )
        markup.add(InlineKeyboardButton("🔙 Назад", callback_data="adm_menu_main"))
        
    elif menu == "promo":
        text = "🎟 **Раздел: Промокоды и Аирдропы**\nЧто будем создавать?"
        markup.add(
            InlineKeyboardButton("👑 Код на VIP", callback_data="admin_panel_promo_vip"),
            InlineKeyboardButton("📢 Код на Рекламу", callback_data="admin_panel_promo_ads")
        )
        markup.add(
            InlineKeyboardButton("📦 Сбросить Аирдроп в чат", callback_data="admin_panel_do_airdrop")
        )
        markup.add(InlineKeyboardButton("🔙 Назад", callback_data="adm_menu_main"))
        
    elif menu == "stats":
        text = "📈 **Раздел: Аналитика**\nКакие данные нужны?"
        markup.add(
            InlineKeyboardButton("📊 Глобальная сводка", callback_data="admin_panel_stats"),
            InlineKeyboardButton("🧾 Z-Отчет (Выручка)", callback_data="admin_panel_zreport")
        )
        markup.add(InlineKeyboardButton("🔗 CPA Статистика", callback_data="admin_panel_cpa"))
        markup.add(InlineKeyboardButton("🔙 Назад", callback_data="adm_menu_main"))

    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
    except: pass

# ================= ОБРАБОТЧИК КНОПОК ПАНЕЛИ =================
@bot.callback_query_handler(func=lambda call: call.data.startswith('admin_panel_'))
def handle_admin_panel_clicks(call):
    action = call.data.replace("admin_panel_", "")
    
    try: bot.answer_callback_query(call.id)
    except: pass
    
    if action == "close":
        try: bot.delete_message(call.message.chat.id, call.message.message_id)
        except: pass
        
    # --- АНАЛИТИКА ---
    elif action == "stats":
        try: bot.edit_message_text("⏳ Скайнет собирает данные со всех узлов...", call.message.chat.id, call.message.message_id)
        except: pass
        
        # Собираем реальную стату из БД
        total_users = db['users'].count_documents({})
        total_banned = db['banned'].count_documents({})
        
        # Считаем сумму очков и кэшбека у населения
        pipeline = [{"$group": {"_id": None, "total_points": {"$sum": "$bounty_points"}, "total_cb": {"$sum": "$cashback_balance"}}}]
        wealth = list(paid_collection.aggregate(pipeline))
        total_points = wealth[0]["total_points"] if wealth else 0
        total_cb = wealth[0]["total_cb"] if wealth else 0
        
        active_promos = db['promocodes'].count_documents({"is_active": True, "used_count": 0})
        active_airdrops = db['active_airdrops'].count_documents({"claimed_count": {"$lt": 5}})
        
        text = (
            "📊 **ГЛОБАЛЬНАЯ СВОДКА СКАЙНЕТА**\n\n"
            f"👥 Всего пользователей в базе: **{total_users}**\n"
            f"🚷 В глобальном бане: **{total_banned}**\n\n"
            f"💰 Очков на руках: **{total_points} ⭐️**\n"
            f"💸 Кэшбека на руках: **{total_cb} ₽**\n\n"
            f"🎟 Неиспользованных промокодов: **{active_promos}**\n"
            f"📦 Активных аирдропов в чатах: **{active_airdrops}**"
        )
        markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🔙 Назад", callback_data="adm_menu_stats"))
        try: bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=markup)
        except: pass
        
    # --- УПРАВЛЕНИЕ ЮЗЕРАМИ ---
    elif action == "manage":
        msg = bot.send_message(call.message.chat.id, "🔨 **Забанить/Разбанить**\nОтправьте ID пользователя:")
        bot.register_next_step_handler(msg, process_admin_manage_user)
        
    elif action == "tag":
        msg = bot.send_message(call.message.chat.id, "🏷 **Выдача ТЕГА**\nОтправьте ID и Текст тега через пробел (например: `12345 БОСС`):", parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_admin_set_tag)
        
    # --- ЭКОНОМИКА ---
    elif action == "give_points":
        msg = bot.send_message(call.message.chat.id, "🎁 **Выдача ОЧКОВ**\nОтправьте ID и сумму (например: `123456 500`):", parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_admin_give_points)

    elif action == "give_shards":
        msg = bot.send_message(call.message.chat.id, "🧩 **Выдача ОСКОЛКОВ**\nОтправьте ID и количество (например: `123456 10`):", parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_admin_give_shards)

# --- НОВЫЕ КНОПКИ МОДЕРАЦИИ ---
    elif action == "mute":
        msg = bot.send_message(call.message.chat.id, "🔇 **Выдать Мут**\nОтправьте ID пользователя и время в часах (например: `12345 24`):", parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_admin_mute)
        
    elif action == "city":
        msg = bot.send_message(call.message.chat.id, "📍 **Сменить город**\nОтправьте ID пользователя и новый город (например: `12345 Москва`):", parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_admin_set_city)

    # --- НОВАЯ КНОПКА ЭКОНОМИКИ ---
    elif action == "invoice":
        msg = bot.send_message(call.message.chat.id, "🧾 **Выставить счет (Штраф)**\nОтправьте ID пользователя и сумму (например: `12345 500`):", parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_admin_invoice)

    # --- ЗАГЛУШКИ ДЛЯ АНАЛИТИКИ (Если функционал еще не написан) ---
    # --- АНАЛИТИКА Z-ОТЧЕТ И CPA (ДОСТУП ТОЛЬКО ДЛЯ ВЛАДЕЛЬЦА) ---
    elif action == "zreport":
        if call.from_user.id != 479938867:
            try: bot.answer_callback_query(call.id, "❌ У вас нет прав на просмотр финансовой отчетности!", show_alert=True)
            except: pass
            return

        try: bot.edit_message_text("⏳ Считаю кассу...", call.message.chat.id, call.message.message_id)
        except: pass
        
        today_str = datetime.datetime.now().strftime("%d.%m.%Y")
        
        # Считаем за всё время
        all_time = list(db['daily_revenue'].aggregate([{"$group": {"_id": "$type", "total": {"$sum": "$amount"}}}]))
        all_dict = {item["_id"]: item["total"] for item in all_time}
        
        # Считаем за сегодня
        today = list(db['daily_revenue'].aggregate([{"$match": {"date": today_str}}, {"$group": {"_id": "$type", "total": {"$sum": "$amount"}}}]))
        today_dict = {item["_id"]: item["total"] for item in today}
        
        def format_money(d, key): return d.get(key, 0)
        
        text = (
            "🧾 **Z-ОТЧЕТ (ВЫРУЧКА СКАЙНЕТА)**\n\n"
            f"📅 **СЕГОДНЯ ({today_str}):**\n"
            f"💰 Штрафы: **{format_money(today_dict, 'fine') + format_money(today_dict, 'fine_partial')}⭐️**\n"
            f"📜 Индульгенции: **{format_money(today_dict, 'indulgence')}⭐️**\n"
            f"🛒 Магазин очков: **{format_money(today_dict, 'points_shop')}⭐️**\n"
            f"💖 Донаты: **{format_money(today_dict, 'donation')}⭐️**\n"
            f"🟢 **ИТОГО ЗА ДЕНЬ: {sum(today_dict.values())}⭐️**\n\n"
            f"🌍 **ЗА ВСЁ ВРЕМЯ:**\n"
            f"💰 Штрафы: **{format_money(all_dict, 'fine') + format_money(all_dict, 'fine_partial')}⭐️**\n"
            f"📜 Индульгенции: **{format_money(all_dict, 'indulgence')}⭐️**\n"
            f"🛒 Магазин очков: **{format_money(all_dict, 'points_shop')}⭐️**\n"
            f"💖 Донаты: **{format_money(all_dict, 'donation')}⭐️**\n"
            f"🏆 **ОБЩАЯ КАССА: {sum(all_dict.values())}⭐️**"
        )
        markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🔙 Назад", callback_data="adm_menu_stats"))
        try: bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=markup)
        except: pass

    elif action == "cpa":
        if call.from_user.id != 479938867:
            try: bot.answer_callback_query(call.id, "❌ У вас нет прав на просмотр CPA статистики!", show_alert=True)
            except: pass
            return

        try: bot.edit_message_text("⏳ Свожу трафик...", call.message.chat.id, call.message.message_id)
        except: pass
        
        total_clicks = db['cpa_traffic'].count_documents({})
        approved = db['cpa_traffic'].count_documents({"status": "approved"})
        hold = db['cpa_traffic'].count_documents({"status": "hold"})
        fraud = db['cpa_traffic'].count_documents({"status": "fraud"})
        
        # Топ-3 агента
        pipeline = [
            {"$match": {"status": "approved"}},
            {"$group": {"_id": "$agent_id", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 3}
        ]
        top_agents = list(db['cpa_traffic'].aggregate(pipeline))
        
        text = (
            "🔗 **CPA СТАТИСТИКА (ПАРТНЕРКА)**\n\n"
            f"👁 Всего заявок (кликов): **{total_clicks}**\n"
            f"⏳ На проверке (Холд): **{hold}**\n"
            f"🚫 Забраковано (Боты): **{fraud}**\n"
            f"✅ **ОДОБРЕНО (Лиды):** **{approved}**\n\n"
            "🏆 **ТОП-3 АГЕНТА:**\n"
        )
        
        if top_agents:
            medals = ["🥇", "🥈", "🥉"]
            for i, agent in enumerate(top_agents):
                agent_id = agent["_id"]
                count = agent["count"]
                text += f"{medals[i]} `ID {agent_id}` — **{count}** лидов\n"
        else:
            text += "_Пока нет одобренных лидов._"
            
        markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🔙 Назад", callback_data="adm_menu_stats"))
        try: bot.edit_message_text(text, call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=markup)
        except: pass
        
    # --- ПРОМОКОДЫ И АИРДРОПЫ ---
    elif action == "promo_vip":
        code = f"VIP-{random.randint(1000, 9999)}"
        db['promocodes'].insert_one({"_id": code, "type": "percent", "value": 100, "target": "vip", "usage_limit": 1, "used_count": 0, "is_active": True})
        bot.send_message(call.message.chat.id, f"👑 **Промокод на 100% VIP создан!**\n\nКод: `{code}`\n_Можно смело дарить юзерам!_", parse_mode="Markdown")

    elif action == "promo_ads":
        code = f"ADS-{random.randint(1000, 9999)}"
        db['promocodes'].insert_one({"_id": code, "type": "percent", "value": 100, "target": "ads", "usage_limit": 1, "used_count": 0, "is_active": True})
        bot.send_message(call.message.chat.id, f"📢 **Промокод на 100% Рекламу создан!**\n\nКод: `{code}`\n_Можно смело дарить юзерам!_", parse_mode="Markdown")

    elif action == "do_airdrop":
        # Магия! Вызываем функцию сброса контейнера из казино!
        try:
            from handlers.casino import trigger_random_airdrop 
            trigger_random_airdrop(is_manual=True) # <-- Теперь админ сбрасывает в обход таймера!
            bot.send_message(call.message.chat.id, "📦 🚨 **Аирдроп успешно сброшен!**\nПрямо сейчас в одном из чатов появилась кнопка с очками!", parse_mode="Markdown")
        except Exception as e:
            bot.send_message(call.message.chat.id, f"❌ Ошибка аирдропа: {e}")

# ================= ФУНКЦИИ-ПОМОЩНИКИ ДЛЯ ПАНЕЛИ =================
def process_admin_manage_user(message):
    if not message.text.isdigit():
        bot.send_message(message.chat.id, "❌ ID должен быть числом!")
        return
    uid = int(message.text)
    
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🔨 БАН ВО ВСЕХ ЧАТАХ", callback_data=f"adm_tg_ban_{uid}"),
        InlineKeyboardButton("🕊 ГЛОБАЛЬНЫЙ РАЗБАН", callback_data=f"adm_tg_unban_{uid}")
    )
    bot.send_message(message.chat.id, f"👤 **Пользователь {uid}**\nВыберите действие:", reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith('adm_tg_'))
def handle_quick_manage(call):
    parts = call.data.split('_')
    action = parts[2]
    uid = int(parts[3])
    
    if action == "ban":
        db['banned'].insert_one({"_id": uid, "reason": "Бан из Панели Скайнета"})
        try: bot.edit_message_text(f"✅ Пользователь {uid} **ЗАБАНЕН**", call.message.chat.id, call.message.message_id, parse_mode="Markdown")
        except: pass
    elif action == "unban":
        db['banned'].delete_one({"_id": uid})
        db['skynet_tasks'].insert_one({"uid": uid, "action": "full_unban", "timestamp": datetime.datetime.now()})
        try: bot.edit_message_text(f"✅ Пользователь {uid} **РАЗБАНЕН**", call.message.chat.id, call.message.message_id, parse_mode="Markdown")
        except: pass

def process_admin_give_points(message):
    try:
        parts = message.text.split()
        uid = int(parts[0])
        amount = int(parts[1])
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": amount}}, upsert=True)
        bot.send_message(message.chat.id, f"✅ Выдано **{amount} очков** пользователю `{uid}`.", parse_mode="Markdown")
        try: bot.send_message(uid, f"🎁 Администрация начислила вам **{amount} очков**!")
        except: pass
    except:
        bot.send_message(message.chat.id, "❌ Ошибка! Нужно писать так: `ID СУММА`")

def process_admin_give_shards(message):
    try:
        parts = message.text.split()
        uid = int(parts[0])
        amount = int(parts[1])
        paid_collection.update_one({"uid": uid}, {"$inc": {"jackpot_shards": amount}}, upsert=True)
        bot.send_message(message.chat.id, f"✅ Выдано **{amount} осколков** пользователю `{uid}`.", parse_mode="Markdown")
        try: bot.send_message(uid, f"🧩 Администрация выдала вам **{amount} осколков Джекпота**!")
        except: pass
    except:
        bot.send_message(message.chat.id, "❌ Ошибка! Нужно писать так: `ID КОЛИЧЕСТВО`")

def process_admin_set_tag(message):
    try:
        parts = message.text.split(maxsplit=1)
        uid = int(parts[0])
        tag = parts[1][:15] # Ограничиваем длину тега до 15 символов
        db['custom_tags'].update_one({"_id": uid}, {"$set": {"tag": tag}}, upsert=True)
        bot.send_message(message.chat.id, f"✅ Тег `{tag}` успешно установлен для пользователя `{uid}`.", parse_mode="Markdown")
    except:
        bot.send_message(message.chat.id, "❌ Ошибка! Нужно писать так: `ID ТЕГ`")

def process_admin_mute(message):
    try:
        parts = message.text.split()
        uid = int(parts[0])
        hours = int(parts[1])
        
        # Передаем задачу Скайнету (он читает базу skynet_tasks)
        db['skynet_tasks'].insert_one({
            "uid": uid,
            "action": "global_mute",
            "duration": hours * 3600,
            "timestamp": datetime.datetime.now()
        })
        bot.send_message(message.chat.id, f"✅ Приказ на выдачу мута пользователю `{uid}` на **{hours} ч.** успешно передан Скайнету.", parse_mode="Markdown")
    except Exception:
        bot.send_message(message.chat.id, "❌ Ошибка! Нужно писать так: `ID ЧАСЫ` (например: 123456 24)")

def process_admin_set_city(message):
    try:
        parts = message.text.split(maxsplit=1)
        uid = int(parts[0])
        city = parts[1]
        
        db['users'].update_one({"_id": uid}, {"$set": {"main_city": city}}, upsert=True)
        bot.send_message(message.chat.id, f"✅ Город `{city}` успешно установлен для пользователя `{uid}`.", parse_mode="Markdown")
    except Exception:
        bot.send_message(message.chat.id, "❌ Ошибка! Нужно писать так: `ID ГОРОД` (например: 123456 Москва)")

def process_admin_invoice(message):
    try:
        parts = message.text.split()
        target_uid = int(parts[0])
        amount = int(parts[1])
        
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
            
        # 👇 ДОБАВИТЬ ЭТИ ДВЕ СТРОКИ 👇
        markup.add(InlineKeyboardButton("💳 Ошибка оплаты? (Альтернатива)", callback_data=f"req_manual_pay_{amount}"))
        markup.add(InlineKeyboardButton("👑 Купить VIP-иммунитет", url="https://t.me/Elitepost_bot"))
            
        bot.send_message(
            target_uid, 
            f"🧾 **Администратор выставил вам счет.**\n\nСумма к оплате: **{amount}⭐️**\nПосле оплаты ограничения будут сняты автоматически.", 
            reply_markup=markup, 
            parse_mode="Markdown"
        )
        bot.send_message(message.chat.id, f"🟢 **Счет на {amount}⭐️ успешно отправлен пользователю `{target_uid}`!**", parse_mode="Markdown")
        
    except Exception:
        bot.send_message(message.chat.id, "❌ Ошибка! Нужно писать так: `ID СУММА` (например: 123456 500)")

# ================= АЛЬТЕРНАТИВНАЯ ОПЛАТА =================
@bot.callback_query_handler(func=lambda call: call.data.startswith('req_manual_pay_'))
def handle_req_manual_pay(call):
    try: bot.answer_callback_query(call.id)
    except: pass
    amount = int(call.data.split('_')[3])
    uid = call.from_user.id
    
    user_data = paid_collection.find_one({"uid": uid}) or {}
    thread_id = user_data.get("thread_id")
    
    # 🧮 Автоматический расчет (Звезды * 1.65 + 10%)
    rub_amount = int(round(amount * 1.65 * 1.1))
    
    # 👑 Предлагаем ВИПку только тем, у кого базовый штраф <= 650
    vip_text = ""
    if amount <= 650:
        vip_text = (
            "\n\nНу или можно и наверное самое экономное вступить в випку (полный иммунитет) : @Elitepost_bot.\n"
            "Стоимость: 275 звезд * 1.65 курс одной звезды + 10% = 500 ₽.\n"
            "Випка даёт доступ во все группы без ограничений.\n"
            "Доступ к боту для публикации.\n"
            "Тег ВИП пользования. Доступ к закрытым группам💪"
        )
        
    # Формируем сообщение ДЛЯ ЮЗЕРА (БЕЗ ПРОСЬБЫ ПИСАТЬ СЛОВА!)
    user_text = (
        f"🛠 **Запрос на альтернативную оплату отправлен!**\n\n"
        f"Оплатить можно на одноразовый технологический номер телефона. Сумма рассчитывается по формуле:\n"
        f"{amount} звезд * 1.65 (курс 1 звезды) + 10% комиссии банка за пополнение баланса = **{rub_amount}₽**\n"
        f"_(Данная сумма дает снятие ограничений, необходимо строгое соблюдение правил в дальнейшем)._"
        f"{vip_text}\n\n"
        f"⏳ **Пожалуйста, ожидайте. Администратор уведомлен и сейчас пришлет вам актуальные реквизиты для перевода.**"
    )
    
    try: bot.edit_message_text(user_text, call.message.chat.id, call.message.message_id, parse_mode="Markdown")
    except Exception as e: logger.debug(f"Игнор ошибки: {e}")
    
    # 🔥 Записываем в память ИИ, что юзер ждет карту, чтобы ИИ больше не лез с кассой
    paid_collection.update_one({"uid": uid}, {"$push": {"dialog_history": {"role": "assistant", "content": "[Пользователь запросил рублевые реквизиты. Ожидаем ответа администратора с реквизитами.]"}}})
    
    # Громко предупреждаем админов в топике
    if thread_id:
        try:
            bot.send_message(
                STAFF_GROUP_ID, 
                f"⚠️ **СРОЧНО: ЗАПРОС РЕКВИЗИТОВ!**\n\nПользователь не может оплатить звездами и выбрал рубли.\nСумма к оплате: **{rub_amount}₽** (вместо {amount}⭐️).\n\n"
                f"💳 _Пожалуйста, отправьте пользователю актуальный номер карты или телефона в этот топик!_", 
                message_thread_id=thread_id, 
                parse_mode="Markdown"
            )
        except: pass