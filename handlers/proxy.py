from telebot import types
from datetime import datetime
import pytz
import time

from config import STAFF_GROUP_ID, ADMIN_CHAT_IDS
from database import proxy_sessions, posts_collection, banned_collection, archive_collection
from utils import get_user_name, escape_md
from core.guards import is_staff, deny_callback

# Локальное хранилище для жалоб на скам
scam_reports = {}

def register_proxy_handlers(bot, ban_user_everywhere):

    def log_proxy_message(session_id, sender_id, message):
        content = message.text if message.text else f"[{message.content_type.upper()}]"
        if message.caption: content += f" | {message.caption}"
        
        timestamp = datetime.now(pytz.timezone('Asia/Yekaterinburg')).strftime("%H:%M:%S")
        proxy_sessions.update_one(
            {"_id": session_id},
            {"$push": {"history": {
                "time": timestamp, 
                "sender": sender_id, 
                "text": content,
                "chat_id": message.chat.id,        
                "message_id": message.message_id,  
                "is_media": message.content_type != 'text' 
            }}}
        )

    @bot.callback_query_handler(func=lambda call: call.data == "respond")
    def handle_respond(call):
        chat_id = call.message.chat.id
        msg_id = call.message.message_id
        user_id = call.from_user.id
        
        # 🔥 ИСПРАВЛЕНИЕ: Ищем chat_id в обоих форматах
        post = posts_collection.find_one({
            "$or": [{"chat_id": chat_id}, {"chat_id": str(chat_id)}], 
            "message_ids": msg_id
        })
        
        if not post:
            bot.answer_callback_query(call.id, "Ошибка: Объявление устарело или было удалено.", show_alert=True)
            return

        vip_id = post["user_id"]
        if user_id == vip_id:
            bot.answer_callback_query(call.id, "❌ Вы не можете откликнуться на свое же объявление!", show_alert=True)
            return

        # Тумблер «Черный Ящик» в веб-панели (раньше ни на что не влиял)
        from core.settings import SkynetSettings
        if not SkynetSettings.get().get("proxy_system", True):
            bot.answer_callback_query(call.id, "Анонимные отклики временно отключены.", show_alert=True)
            return

        # 👇 ЩИТ СКАЙНЕТА: Проверяем гостя по базе глобальных банов 👇
        if banned_collection.find_one({"_id": user_id}):
            bot.answer_callback_query(
                call.id, 
                "🚫 Доступ ограничен. Вы заблокированы в сети за нарушения правил. За что? Ответ тут: @FAQMKBOT", 
                show_alert=True
            )
            return
        # 👆 ================================================== 👆

        bot.answer_callback_query(call.id)
        msg = bot.send_message(
            user_id, 
            "🛡 **Защищенный чат с VIP-пользователем**\n\n"
            "Напишите ваше первое сообщение (можно прикрепить фото или видео). Оно будет передано анонимно.\n"
            "👉 _Жду ваше сообщение:_ ",
            parse_mode="Markdown"
        )
        bot.register_next_step_handler(msg, process_proxy_first_message, vip_id, post.get("_id"))

    def process_proxy_first_message(message, vip_id, post_oid=None):
        if not message.text and not message.photo and not message.video and not message.voice:
            bot.send_message(message.chat.id, "❌ Ошибка: Поддерживается только текст, photo, video или голос.")
            return

        guest_id = message.from_user.id
        session_id = f"proxy_{vip_id}_{guest_id}"

        # 📊 Счётчик откликов для статистики мини-приложения (один гость = один отклик на анкету)
        if post_oid is not None:
            try:
                import time as _t
                p = posts_collection.find_one({"_id": post_oid}) or {}
                pub_id = p.get("pub_id") or str(post_oid)
                from database import db as _db
                _db['post_replies'].insert_one({"_id": f"{pub_id}:{guest_id}", "pub_id": pub_id, "owner": vip_id,
                                                "chat_id": p.get("chat_id"), "uid": guest_id, "ts": _t.time()})
                posts_collection.update_one({"_id": post_oid}, {"$inc": {"replies": 1}})
            except Exception:
                pass  # повторный отклик того же гостя на ту же анкету не считаем
        
        proxy_sessions.update_one(
            {"_id": session_id},
            {"$set": {"vip_id": vip_id, "guest_id": guest_id, "is_active": True, "history": []}},
            upsert=True
        )
        log_proxy_message(session_id, guest_id, message)
        
        # 👇 НОВОЕ: ИНТЕЛЛЕКТУАЛЬНОЕ ДОСЬЕ НА ГОСТЯ 👇
        user_record = archive_collection.find_one({"target": str(guest_id)})
        ban_count = 0
        if user_record and "history" in user_record:
            ban_count = len(user_record["history"])

        if ban_count == 0:
            reputation = "🟢 <b>Репутация:</b> Чисто (Нарушений: 0)"
        else:
            reputation = f"⚠️ <b>Внимание:</b> Подозрительный аккаунт (Нарушений: {ban_count})"
        # 👆 ========================================== 👆
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("🔚 Завершить диалог (Мирно)", callback_data=f"px_close_{session_id}"),
            types.InlineKeyboardButton("🛑 Заблокировать диалог", callback_data=f"px_block_{session_id}"),
            types.InlineKeyboardButton("🚨 Заблокировать и сообщить администратору", callback_data=f"px_report_{session_id}")
        )
        
        clean_name = message.from_user.first_name.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        if message.from_user.username:
            guest_link = f'<a href="https://t.me/{message.from_user.username}">{clean_name}</a>'
        else:
            guest_link = f'<a href="tg://user?id={guest_id}">{clean_name}</a>'
        
        # 👇 ОБНОВЛЕННЫЙ ТЕКСТ УВЕДОМЛЕНИЯ С ДОСЬЕ 👇
        msg_alert = bot.send_message(
            vip_id, 
            f"💌 <b>Новый отклик от {guest_link}!</b>\n{reputation}\n\n<i>Просто введите ответ ниже (он отправится автоматически, свайпать влево не обязательно).</i>", 
            parse_mode="HTML",
            reply_markup=types.ForceReply(selective=True)
        )
        
        sent_msg = bot.copy_message(vip_id, message.chat.id, message.message_id, reply_markup=markup)
        
        proxy_sessions.update_one({"_id": session_id}, {"$set": {
            f"msgs_{vip_id}_{sent_msg.message_id}": True,
            f"msgs_{vip_id}_{msg_alert.message_id}": True
        }})
        
        bot.send_message(message.chat.id, "✅ Сообщение доставлено. Ждите ответа (он придет прямо в этот чат).")

    @bot.message_handler(func=lambda m: m.chat.type == "private" and m.reply_to_message is not None, content_types=['text', 'photo', 'video', 'voice', 'video_note', 'document'])
    def handle_proxy_reply(message):
        reply_to_id = message.reply_to_message.message_id
        user_id = message.from_user.id
        
        session = proxy_sessions.find_one({f"msgs_{user_id}_{reply_to_id}": {"$exists": True}, "is_active": True})
        if not session: return 
            
        session_id = session["_id"]
        vip_id = session["vip_id"]
        guest_id = session["guest_id"]
        
        recipient_id = guest_id if user_id == vip_id else vip_id
            
        log_proxy_message(session_id, user_id, message)
        
        if user_id == guest_id:
            # 👇 ТУТ ТОЖЕ ОБНОВИЛИ НАБОР КНОПОК ДЛЯ VIP 👇
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(
                types.InlineKeyboardButton("🔚 Завершить диалог (Мирно)", callback_data=f"px_close_{session_id}"),
                types.InlineKeyboardButton("🛑 Заблокировать диалог", callback_data=f"px_block_{session_id}"),
                types.InlineKeyboardButton("🚨 Заблокировать и сообщить администратору", callback_data=f"px_report_{session_id}")
            )
            sent_msg = bot.copy_message(recipient_id, message.chat.id, message.message_id, reply_markup=markup)
        else:
            # 👇 ДОБАВИЛИ ForceReply ДЛЯ ГОСТЯ — его поле ввода автоматически привяжется к сообщению VIP! 👇
            sent_msg = bot.copy_message(recipient_id, message.chat.id, message.message_id, reply_markup=types.ForceReply(selective=True))
            
        proxy_sessions.update_one({"_id": session_id}, {"$set": {f"msgs_{recipient_id}_{sent_msg.message_id}": True}})

    @bot.callback_query_handler(func=lambda call: call.data.startswith(("px_block_", "px_report_", "px_close_")))
    def handle_proxy_actions(call):
        # Оцифровываем действие
        if call.data.startswith("px_block_"): action = "block"
        elif call.data.startswith("px_report_"): action = "report"
        else: action = "close"
        
        session_id = call.data.split("_", 2)[2]
        
        session = proxy_sessions.find_one({"_id": session_id})
        if not session or not session.get("is_active"):
            bot.answer_callback_query(call.id, "Диалог уже закрыт.", show_alert=True)
            return
            
        vip_id = session["vip_id"]
        guest_id = session["guest_id"]

        # Кнопки есть только у VIP. Подделанной кнопкой посторонний мог закрыть чужой диалог
        # или отправить чужую переписку админам «жалобой».
        if call.from_user.id != vip_id:
            return deny_callback(bot, call, "Это не ваш диалог.")
        
        proxy_sessions.update_one({"_id": session_id}, {"$set": {"is_active": False}})
        
        try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except: pass
        
        # 👇 ДЕЛИМ УВЕДОМЛЕНИЯ НА ВЕЖЛИВЫЕ И ЖЕСТКИЕ 👇
        if action == "close":
            bot.send_message(vip_id, "🔚 Диалог успешно завершен. Сессия связи закрыта.")
            try: bot.send_message(guest_id, "🔚 VIP-пользователь завершил диалог. Сессия связи закрыта.")
            except: pass
        else:
            bot.send_message(vip_id, "🛑 Диалог завершен. Собеседник отключен от канала связи.")
            try: bot.send_message(guest_id, "🛑 VIP-пользователь завершил диалог. Вы больше не можете ему написать.")
            except: pass
        
        # Логика репорта (оставляем без изменений)
        if action == "report":
            history = session.get("history", [])
            log_text = f"🗄 **СКАЙНЕТ: ПЕРЕХВАТ ДИАЛОГА (ЖАЛОБА VIP)**\n\n"
            log_text += f"**👑 VIP:** `{vip_id}`\n**👤 Гость:** `{guest_id}`\n\n💬 **ЛОГ ПЕРЕПИСКИ:**\n"
            
            for h in history:
                sender_label = "VIP" if h["sender"] == vip_id else "Гость"
                log_text += f"`[{h['time']}] {sender_label}:` {escape_md(h['text'])}\n"
                
            try: 
                bot.send_message(STAFF_GROUP_ID, log_text, parse_mode="Markdown")
                for h in history:
                    if h.get("is_media") and "chat_id" in h and "message_id" in h:
                        try:
                            sender_label = "VIP" if h["sender"] == vip_id else "Гость"
                            bot.send_message(STAFF_GROUP_ID, f"📎 **Медиа от {sender_label} ({h['time']}):**", parse_mode="Markdown")
                            bot.copy_message(STAFF_GROUP_ID, from_chat_id=h["chat_id"], message_id=h["message_id"])
                        except: pass
                bot.send_message(vip_id, "✅ Жалоба отправлена администрации. Меры будут приняты!")
            except: pass

    @bot.message_handler(func=lambda m: m.chat.type == "private" and not m.reply_to_message and not (m.text or '').startswith('/'))
    def catch_forgotten_reply(message):
        menu_buttons = ["Создать новое объявление", "Удалить объявление", "Удалить все объявления", "👑 Вступить в VIP-чат", "👤 Партнерская программа", "Мужской Клуб", "ПАРНИ 18+", "НС", "Радуга", "Гей Знакомства", "Все сети", "Назад", "Выбрать другую сеть", "Да", "Нет"]
        if message.text in menu_buttons: return
        active_session = proxy_sessions.find_one({"$or": [{"vip_id": message.from_user.id}, {"guest_id": message.from_user.id}], "is_active": True})
        if active_session:
            bot.send_message(message.chat.id, "⚠️ **Внимание!**\nЧтобы отправить сообщение собеседнику в анонимном чате, вам нужно сделать **Reply (свайп влево)** на его сообщение!\n\nИначе я не пойму, кому именно вы хотите ответить.", parse_mode="Markdown")

    @bot.callback_query_handler(func=lambda call: call.data.startswith("report_scam_"))
    def handle_report_scam(call):
        try:
            parts = call.data.split("_")
            if len(parts) < 5 or parts[0] != "report" or parts[1] != "scam":
                bot.answer_callback_query(call.id, "Неверный формат", show_alert=True)
                return

            chat_id     = int(parts[2])
            msg_id      = int(parts[3])
            responder_id = int(parts[4])

            reporter = call.from_user
            reporter_link = get_user_name(reporter)

            post = posts_collection.find_one({
                "$or": [{"chat_id": chat_id}, {"chat_id": str(chat_id)}],
                "message_ids": msg_id
            })
            found_vip_id = post["user_id"] if post else None

            channel_part = str(chat_id)[4:] if str(chat_id).startswith("-100") else str(chat_id)
            ann_link = f"https://t.me/c/{channel_part}/{msg_id}"
            user_link = f"tg://user?id={responder_id}"

            report_time = datetime.now(pytz.timezone('Asia/Yekaterinburg')).strftime('%Y-%m-%d %H:%M:%S')

            text = (
                f"🚨 ЖАЛОБА НА СПАМ/СКАМ\n\n"
                f"От: {reporter_link}\n"
                f"На пользователя: [{responder_id}]({user_link})\n"
                f"Объявление: {ann_link}\n"
                f"Время: {report_time}\n\n"
                f"👇 Выберите действие:"
            )

            report_id = f"{chat_id}_{msg_id}_{responder_id}_{int(time.time())}"

            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton("✅ Принять жалобу", callback_data=f"scam_accept_{report_id}"),
                types.InlineKeyboardButton("❌ Отклонить",      callback_data=f"scam_reject_{report_id}"),
            )
            markup.add(types.InlineKeyboardButton("ℹ️ Нужны детали",   callback_data=f"scam_details_{report_id}"))

            scam_reports[report_id] = {
                "reporter_id": reporter.id,
                "vip_id": found_vip_id,
                "chat_id": chat_id,
                "msg_id": msg_id,
                "responder_id": responder_id,
                "reporter_link": reporter_link,
                "ann_link": ann_link,
                "time": report_time
            }

            for admin_id in ADMIN_CHAT_IDS:
                try: bot.send_message(admin_id, text, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=markup)
                except: pass

            bot.answer_callback_query(call.id, "Жалоба отправлена администрации", show_alert=False)

        except Exception as e:
            bot.answer_callback_query(call.id, f"Ошибка обработки жалобы\n{e}", show_alert=True)

    @bot.callback_query_handler(func=lambda call: call.data.startswith(("scam_accept_", "scam_reject_", "scam_details_")))
    def handle_scam_admin_response(call):
        if not is_staff(call.from_user.id):
            return deny_callback(bot, call)
        try:
            parts = call.data.split("_", 2)
            action = parts[1]
            report_id = parts[2]

            if report_id not in scam_reports:
                bot.answer_callback_query(call.id, "Жалоба уже обработана или не найдена", show_alert=True)
                return

            report = scam_reports[report_id]
            vip_id = report.get("vip_id")
            responder_id = report.get("responder_id") 
            admin_info = get_user_name(call.from_user) 

            if not vip_id:
                bot.answer_callback_query(call.id, "Не удалось определить автора объявления", show_alert=True)
                return

            ann_link = report["ann_link"]

            if action == "accept":
                bot.answer_callback_query(call.id, "🚀 Запускаю mass-бан скамера...")
                reason = f"Жалоба на скам в объявлении {ann_link}"
                count = ban_user_everywhere(responder_id, reason=reason, admin_name=f"Admin: {admin_info}")
                reply_text = f"✅ Ваша жалоба на пользователя в объявлении {ann_link} **принята**.\n\nМошенник заблокирован в {count} чатах сети! Спасибо за бдительность. 🛡️"
                
            elif action == "reject":
                reply_text = f"❌ Жалоба на пользователя в объявлении {ann_link} **отклонена**.\nСпасибо за сигнал, но оснований для блокировки недостаточно."
                
            elif action == "details":
                reply_text = f"ℹ️ По жалобе на объявление {ann_link} нужны детали.\nПожалуйста, напишите нам в саппорт, что именно вызвало подозрения."

            try: bot.send_message(vip_id, reply_text, parse_mode="Markdown", disable_web_page_preview=True)
            except: pass

            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=call.message.text + f"\n\n✅ **Обработано админом:** {admin_info}\n**Результат:** {action.upper()}",
                parse_mode="Markdown",
                disable_web_page_preview=True
            )

            del scam_reports[report_id]

        except Exception as e:
            bot.answer_callback_query(call.id, f"Ошибка: {str(e)}", show_alert=True)