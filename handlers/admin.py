from telebot import types
import time
import re
from config import (
    STAFF_GROUP_ID, OWNER_ID, PARNI_CHATS, VIP_CHAT_ID, BEYOND_CHAT_ID,
    chat_ids_mk, chat_ids_parni, chat_ids_ns, chat_ids_rainbow, chat_ids_gayznak
)
from database import users_collection, db, banned_collection, archive_collection
from utils import get_user_name

def parse_time_string(time_str):
    match = re.match(r"^(\d+)([smhd])$", time_str.lower())
    if not match: return None
    val, unit = int(match.group(1)), match.group(2)
    if unit == 's': return val
    elif unit == 'm': return val * 60
    elif unit == 'h': return val * 3600
    elif unit == 'd': return val * 86400
    return None

def register_admin_handlers(bot, ban_user_everywhere, mute_user_everywhere, unban_user_everywhere, unmute_user_everywhere, unmute_in_parni_only):

    @bot.message_handler(commands=['ban'])
    def handle_manual_ban(message):
        if message.chat.id != STAFF_GROUP_ID: return
        args = message.text.split(maxsplit=2)
        if len(args) < 2:
            bot.send_message(message.chat.id, "❌ Формат: `/ban [ID] [Причина]`\nПример: `/ban 123456789 Реклама`", parse_mode="Markdown")
            return
        try: target_id = int(args[1])
        except ValueError:
            bot.send_message(message.chat.id, "❌ Ошибка: ID должен состоять только из цифр!")
            return
        reason = args[2] if len(args) > 2 else "Не указана"
        admin_info = get_user_name(message.from_user)
        bot.send_message(message.chat.id, "🚀 Глобальный бан запущен...")
        count = ban_user_everywhere(target_id, reason, admin_info)
        bot.send_message(message.chat.id, f"✅ Готово! Юзер `{target_id}` забанен в {count} чатах. Отчет отправлен в Журнал.", parse_mode="Markdown")

    @bot.message_handler(commands=['mute'])
    def handle_unified_mute(message):
        if message.chat.id != STAFF_GROUP_ID: return
        
        args = message.text.split(maxsplit=2)
        if len(args) < 2:
            bot.send_message(message.chat.id, "❌ Формат: `/mute [ID] [Время(необяз)] [Причина]`\nПример: `/mute 12345 1h Спам`", parse_mode="Markdown")
            return
            
        try: target_id = int(args[1])
        except ValueError:
            bot.send_message(message.chat.id, "❌ Ошибка: ID должен быть числом!")
            return
            
        reason = "Не указана"
        mute_time = 0
        duration_text = "навсегда"
        
        # Если есть 3-й аргумент, проверяем, время это или просто причина
        if len(args) > 2:
            sub_args = args[2].split(maxsplit=1)
            parsed_seconds = parse_time_string(sub_args[0])
            
            if parsed_seconds:
                # Нашли время!
                mute_time = int(time.time()) + parsed_seconds
                duration_text = f"на {sub_args[0]}"
                if len(sub_args) > 1:
                    reason = sub_args[1] # Причина идет после времени
            else:
                # Времени нет, всё остальное — это причина
                reason = args[2]

        admin_info = get_user_name(message.from_user)
        
        # Обновляем причину в базе (для ПАРНИ 18+)
        users_collection.update_one({"_id": target_id}, {"$set": {"last_mute_reason": reason}}, upsert=True)
        
        bot.send_message(message.chat.id, f"🤐 Запускаю глобальный мут {duration_text}...")
        
        # Передаем параметр mute_time в ядро Скайнета
        count = mute_user_everywhere(target_id, reason=reason, admin_name=admin_info, mute_time=mute_time)
        bot.send_message(message.chat.id, f"✅ Юзер `{target_id}` замучен в {count} чатах {duration_text}. Причина сохранена.")

    @bot.message_handler(commands=['addpromo'])
    def create_custom_promo(message):
        try:
            staff_member = bot.get_chat_member(STAFF_GROUP_ID, message.from_user.id)
            if staff_member.status not in ['administrator', 'creator']: return
        except Exception: return 
        args = message.text.split()
        if len(args) < 5:
            text = (
                "🛠 **Генератор промокодов**\n\n"
                "Формат: `/addpromo [КОД] [СКИДКА_В_%] [ЦЕЛЬ] [ЛИМИТ]`\n\n"
                "🎯 **Цели:**\n`vip` - только на VIP\n`fine` - только на штрафы\n`ads` - на рекламу\n`all` - работает везде\n\n"
                "Пример: `/addpromo VESNA 50 vip 100`"
            )
            bot.send_message(message.chat.id, text, parse_mode="Markdown")
            return
        code = args[1].upper()
        try:
            discount = int(args[2])
            target = args[3].lower()
            limit = int(args[4])
        except ValueError:
            bot.send_message(message.chat.id, "❌ Ошибка: Скидка и лимит должны быть числами!")
            return
        db['promocodes'].update_one(
            {"_id": code},
            {"$set": {"type": "percent", "value": discount, "target": target, "usage_limit": limit, "used_count": 0, "owner_uid": message.from_user.id, "is_active": True}},
            upsert=True
        )
        bot.send_message(message.chat.id, f"🎉 **Промокод создан!**\n\nКод: `{code}`\nСкидка: **{discount}%**\nДействует на: **{target}**\nЛимит: **{limit}** активаций.", parse_mode="Markdown")

    @bot.message_handler(commands=['airdrop'])
    def handle_create_airdrop(message):
        try:
            staff_member = bot.get_chat_member(STAFF_GROUP_ID, message.from_user.id)
            if staff_member.status not in ['administrator', 'creator']: return
        except Exception: return 
        
        args = message.text.split()
        if len(args) < 4:
            bot.reply_to(message, "❌ **Ошибка!** Формат: `/airdrop [ИМЯ_КОДА] [СУММА_ОЧКОВ] [КОЛ-ВО_АКТИВАЦИЙ]`\n\n*Пример:* `/airdrop START50 50 10`", parse_mode="Markdown")
            return

        code_name = args[1].upper()
        try:
            points = int(args[2])
            limit = int(args[3])
        except ValueError:
            bot.reply_to(message, "❌ Ошибка: сумма и количество должны быть числами.")
            return

        # Создаем многоразовый код на очки в базе
        db['promocodes'].update_one(
            {"_id": code_name},
            {"$set": {
                "type": "airdrop",
                "value": points,
                "target": "points",
                "usage_limit": limit,
                "used_count": 0,
                "activated_by": [], # Список тех, кто уже ввел код (чтобы не абузили)
                "is_active": True
            }},
            upsert=True
        )

        bot.reply_to(message, f"🎁 **Аирдроп успешно создан!**\n\nКод: `{code_name}`\nДает: **{points} очков**\nЛимит: **{limit} активаций**\n\n_Кидайте его в чаты!_", parse_mode="Markdown")

    @bot.message_handler(commands=['get_report'])
    def get_detailed_report(message):
        if message.from_user.id != OWNER_ID: return
        stats = db['network_stats'].find_one({"_id": "current_period"})
        if not stats:
            bot.send_message(message.chat.id, "📊 Статистика за этот период пуста.")
            return
        total = stats.get('total', 0)
        approved = stats.get('approved', 0)
        vip_tickets = stats.get('vip_tickets', 0)
        manual_pending = total - approved 
        total_bot_users = users_collection.count_documents({})
        chat_names = {}
        for city, cid in chat_ids_mk.items(): chat_names[cid] = f"МК | {city}"
        for city, cid in chat_ids_parni.items(): chat_names[cid] = f"ПАРНИ | {city}"
        for city, cid in chat_ids_ns.items(): chat_names[cid] = f"НС | {city}"
        for city, cid in chat_ids_rainbow.items(): chat_names[cid] = f"Радуга | {city}"
        for city, cid in chat_ids_gayznak.items(): chat_names[cid] = f"Гей Знакомства | {city}"
        chat_names[VIP_CHAT_ID] = "VIP Клуб"
        chat_names[BEYOND_CHAT_ID] = "BEYOND"
        city_details = ""
        chats_data = stats.get('chats', {})
        for cid_str, data in chats_data.items():
            cid = int(cid_str)
            name = chat_names.get(cid, f"Неизвестный чат ({cid})")
            c_total = data.get('total', 0)
            c_appr = data.get('approved', 0)
            c_manual = c_total - c_appr
            city_details += f"📍 {name}: {c_total} (авто: {c_appr} | ручками: {c_manual})\n"
        report_text = (
            f"📋 **Z-ОТЧЕТ СЕТИ (Период)**\n\n"
            f"🤖 **ЮЗЕРОВ В БОТЕ:** {total_bot_users}\n"
            f"📈 **ЗАЯВОК ВСЕГО:** {total}\n"
            f"✅ **АВТО-ВХОД:** {approved}\n"
            f"👑 **ЗОЛОТОЙ БИЛЕТ:** {vip_tickets}\n"
            f"⏳ **РУЧНОЕ ОДОБРЕНИЕ:** {manual_pending}\n\n"
            f"🏙 **ДЕТАЛИЗАЦИЯ:**\n{city_details}\n\n"
            f"📅 *Следующая выгрузка: 01.05.2026*"
        )
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🗑 Сбросить счетчики", callback_data="reset_stats"))
        bot.send_message(message.chat.id, report_text, reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data == "reset_stats")
    def reset_network_stats(call):
        if call.from_user.id != OWNER_ID: return
        db['network_stats'].delete_one({"_id": "current_period"})
        db['period_joins'].drop() 
        bot.edit_message_text("✅ Статистика и память заявок обнулены. Начинаем новый отсчет!", call.message.chat.id, call.message.message_id)

    @bot.message_handler(commands=['setcity'])
    def admin_set_city(message):
        try:
            staff_member = bot.get_chat_member(STAFF_GROUP_ID, message.from_user.id)
            if staff_member.status not in ['administrator', 'creator']: return
        except Exception: return 
        args = message.text.split(maxsplit=2)
        if len(args) < 3:
            bot.send_message(message.chat.id, "❌ Формат: `/setcity [ID] [Город]`", parse_mode="Markdown")
            return
        try:
            target_id = int(args[1])
            new_city = args[2]
            users_collection.update_one({"_id": target_id}, {"$set": {"main_city": new_city}}, upsert=True)
            bot.send_message(message.chat.id, f"✅ Город для пользователя `{target_id}` успешно изменен на **{new_city}**.")
        except ValueError:
            bot.send_message(message.chat.id, "❌ Ошибка: ID должен быть числом.")

    @bot.message_handler(commands=['stats'])
    def global_bot_stats(message):
        if message.from_user.id != OWNER_ID: return
        bot.send_message(message.chat.id, "🔄 Собираю данные по всей базе, подождите...")
        total_users = users_collection.count_documents({})
        vips = users_collection.count_documents({"is_vip": True})
        queers = users_collection.count_documents({"is_queer": True})
        verified = users_collection.count_documents({"custom_tag": "Верифицирован МК"})
        custom_admins = users_collection.count_documents({"custom_tag": {"$ne": "Верифицирован МК", "$exists": True}})
        banned = banned_collection.count_documents({})
        text = (
            "📊 **ГЛОБАЛЬНАЯ СТАТИСТИКА ИМПЕРИИ**\n\n"
            f"👥 **Всего уникальных юзеров:** {total_users}\n"
            f"👑 **В клубе VIP:** {vips}\n"
            f"🌈 **В клубе BEYOND:** {queers}\n"
            f"✅ **Обычных верификаций:** {verified}\n"
            f"🎖 **Админов / Кастомных тегов:** {custom_admins}\n"
            f"🔨 **В глобальном бане (ЧС):** {banned}\n"
        )
        bot.send_message(message.chat.id, text, parse_mode="Markdown")

    @bot.message_handler(commands=['parni_amnesty'])
    def send_amnesty_button(message):
        if message.chat.id != STAFF_GROUP_ID: return
        bot.send_message(message.chat.id, "🔄 Начинаю рассылку кнопок амнистии по сети ПАРНИ...")
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🕊 Снять мут (Только для 18+)", callback_data="claim_parni_amnesty"))
        text = (
            "⚠️ **ОБЪЯВЛЕНИЕ ОТ АДМИНИСТРАЦИИ** ⚠️\n\n"
            "Если ранее вы получили автоматический мут за отсутствие параметров в анкете, "
            "вы можете снять ограничения **специально для сети ПАРНИ 18+**, где эти правила не действуют.\n\n"
            "👇 Нажмите на кнопку ниже, чтобы вернуть себе право голоса в этих чатах!"
        )
        success_count = 0
        error_list = []
        for cid in PARNI_CHATS:
            try: 
                bot.send_message(cid, text, reply_markup=markup, parse_mode="Markdown")
                success_count += 1
                time.sleep(1) 
            except Exception as e: 
                error_list.append(f"`{cid}`: {e}")
        report_msg = f"✅ Кнопка амнистии успешно отправлена в {success_count} чатов сети ПАРНИ 18+."
        if error_list:
            report_msg += "\n\n⚠️ **Ошибки отправки:**\n" + "\n".join(error_list)
        bot.send_message(message.chat.id, report_msg, parse_mode="Markdown")

    @bot.callback_query_handler(func=lambda call: call.data == "claim_parni_amnesty")
    def process_amnesty_click(call):
        user_id = call.from_user.id
        if banned_collection.find_one({"_id": user_id}):
            bot.answer_callback_query(call.id, "❌ Отказано. Ваш аккаунт находится в черном списке.", show_alert=True)
            return
        is_eligible = False
        user_data = users_collection.find_one({"_id": user_id}) or {}
        last_reason = user_data.get("last_mute_reason", "")
        if any(word in last_reason for word in ["1 Мая", "параметр"]):
            is_eligible = True
        if not is_eligible:
            archive = archive_collection.find_one({"target": str(user_id)}) or {}
            history = archive.get("history", [])
            for entry in history:
                if entry.get("action") == "Глобальный МУТ (Скайнет)":
                    if any(word in entry.get("reason", "") for word in ["1 Мая", "параметр"]):
                        is_eligible = True
                        break
        if is_eligible:
            unmute_in_parni_only(user_id)
            users_collection.update_one({"_id": user_id}, {"$unset": {"last_mute_reason": ""}})
            try: bot.send_message(STAFF_GROUP_ID, f"🕊 **ИНТЕРАКТИВНАЯ АМНИСТИЯ:** Юзер `{user_id}` нажал кнопку и вернул себе голос в сети ПАРНИ 18+.")
            except: pass
            bot.answer_callback_query(call.id, "🕊 Амнистия применена!\nТеперь вы можете писать в сети ПАРНИ 18+.", show_alert=True)
        else:
            bot.answer_callback_query(call.id, "❌ Отказано. Амнистия действует только на блокировки за формат анкеты (параметры).", show_alert=True)

    @bot.message_handler(commands=['tag'])
    def set_custom_user_tag(message):
        try:
            staff_member = bot.get_chat_member(STAFF_GROUP_ID, message.from_user.id)
            if staff_member.status not in ['administrator', 'creator']:
                bot.send_message(message.chat.id, "❌ Отказано. У вас нет прав доступа Скайнета.")
                return
        except Exception: return 
        args = message.text.split(maxsplit=2)
        if len(args) < 3:
            bot.send_message(message.chat.id, "❌ Формат: `/tag [ID] [ТЕГ]`\nЧтобы убрать: `/tag [ID] none`", parse_mode="Markdown")
            return
        try: target_id = int(args[1])
        except ValueError:
            bot.send_message(message.chat.id, "❌ Ошибка: ID должен состоять только из цифр!")
            return
        new_tag = args[2]
        if new_tag.lower() == "none":
            users_collection.update_one({"_id": target_id}, {"$unset": {"custom_tag": ""}})
            bot.send_message(message.chat.id, f"✅ Глобальный тег для `{target_id}` успешно удален.")
        else:
            users_collection.update_one({"_id": target_id}, {"$set": {"custom_tag": new_tag}}, upsert=True)
            unmuted_count = unmute_user_everywhere(target_id)
            bot.send_message(message.chat.id, f"✅ Юзер `{target_id}` верифицирован как `{new_tag}` и глобально размучен в {unmuted_count} чатах!")

    @bot.message_handler(commands=['cpa'])
    def cpa_admin_stats(message):
        # Доступ только для Руководства и Staff-группы
        try:
            staff_member = bot.get_chat_member(STAFF_GROUP_ID, message.from_user.id)
            if staff_member.status not in ['administrator', 'creator'] and message.from_user.id != OWNER_ID: 
                return
        except Exception: 
            return 
            
        args = message.text.split()
        
        # =================================================================
        # 1. ДЕТАЛЬНАЯ СТАТИСТИКА ПО КОНКРЕТНОМУ АГЕНТУ (/cpa 123456789)
        # =================================================================
        if len(args) > 1:
            try:
                target_agent_id = int(args[1])
            except ValueError:
                bot.send_message(message.chat.id, "❌ Ошибка: ID агента должен состоять только из цифр.")
                return
                
            bot.send_message(message.chat.id, f"🔄 Собираю досье на агента `{target_agent_id}`...")
            
            # Считаем воронку прямиком из базы Скайнета
            agent_hold = db['cpa_traffic'].count_documents({"agent_id": target_agent_id, "status": "hold"})
            agent_approved = db['cpa_traffic'].count_documents({"agent_id": target_agent_id, "status": "approved"})
            agent_fraud = db['cpa_traffic'].count_documents({"agent_id": target_agent_id, "status": "fraud"})
            
            # Достаем дубликаты из базы Секретаря
            agent_data = db['paid_users'].find_one({"uid": target_agent_id}) or {}
            agent_duplicates = agent_data.get("cpa_duplicates", 0)
            
            total_agent_leads = agent_hold + agent_approved + agent_fraud + agent_duplicates
            
            report_text = (
                f"🕵️‍♂️ **ДОСЬЕ АГЕНТА: `{target_agent_id}`**\n\n"
                f"👁 Всего переходов по его ссылкам: **{total_agent_leads}**\n"
                f"🔄 Дубликаты (уже были в сети): **{agent_duplicates}**\n"
                f"⏳ На проверке Скайнета (48ч): **{agent_hold}**\n"
                f"🚫 Отбраковано (боты/спам): **{agent_fraud}**\n"
                f"✅ **Одобрено (живые):** **{agent_approved}**\n\n"
                f"💡 _Одобрено = количество человек, за которых агент получил выплату._"
            )
            bot.send_message(message.chat.id, report_text, parse_mode="Markdown")
            return

        # =================================================================
        # 2. ОБЩАЯ СТАТИСТИКА (Если написали просто /cpa)
        # =================================================================
        bot.send_message(message.chat.id, "🔄 Собираю общую аналитику по CPA-сети, подождите...")

        total_hold = db['cpa_traffic'].count_documents({"status": "hold"})
        total_approved = db['cpa_traffic'].count_documents({"status": "approved"})
        total_fraud = db['cpa_traffic'].count_documents({"status": "fraud"})
        
        total_leads = total_hold + total_approved + total_fraud
        conversion = round((total_approved / total_leads * 100), 1) if total_leads > 0 else 0

        # Делаем агрегацию (ТОП-5) прямо средствами MongoDB
        pipeline = [
            {"$match": {"status": "approved"}},
            {"$group": {"_id": "$agent_id", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 5}
        ]
        top_agents = list(db['cpa_traffic'].aggregate(pipeline))

        report_text = (
            f"📈 **ОБЩАЯ АНАЛИТИКА CPA-СЕТИ**\n\n"
            f"👥 **Уникальных заявок:** {total_leads}\n"
            f"✅ **Одобрено (Живые):** {total_approved}\n"
            f"🚫 **Отбраковано Скайнетом:** {total_fraud}\n"
            f"⏳ **В карантине (48ч):** {total_hold}\n\n"
            f"🔥 **Средняя конверсия:** {conversion}%\n\n"
            f"🏆 **ТОП-5 АГЕНТОВ (По живому трафику):**\n"
        )

        if not top_agents:
            report_text += "_Пока нет одобренного трафика._"
        else:
            for idx, agent in enumerate(top_agents, 1):
                agent_id = agent['_id']
                count = agent['count']
                
                # Ищем количество брака конкретно у этого агента
                fraud_by_agent = db['cpa_traffic'].count_documents({"agent_id": agent_id, "status": "fraud"})
                
                # Пытаемся достать имя агента (если есть) или выводим ID
                user_info = db['users'].find_one({"_id": agent_id})
                agent_name = f"ID `{agent_id}`"
                if user_info and "first_name" in user_info:
                    agent_name = f"[{escape_md(user_info.get('first_name', 'Агент'))}](tg://user?id={agent_id})"
                
                report_text += f"{idx}. {agent_name} — **{count}** живых | 🗑 Мусор: {fraud_by_agent}\n"

        bot.send_message(message.chat.id, report_text, parse_mode="Markdown")

    @bot.message_handler(commands=['unban'])
    def global_unban_user(message):
        try:
            staff_member = bot.get_chat_member(STAFF_GROUP_ID, message.from_user.id)
            if staff_member.status not in ['administrator', 'creator']:
                bot.send_message(message.chat.id, "❌ Отказано. Вы не можете отдавать приказы Скайнету.")
                return
        except Exception: return 
        args = message.text.split(maxsplit=2)
        if len(args) < 2:
            bot.send_message(message.chat.id, "❌ Формат: `/unban [ID] [НЕОБЯЗАТЕЛЬНО: ТЕГ]`\nПример: `/unban 123456 𝐑𝐄𝐀𝐋/𝐕𝐈𝐏♕`", parse_mode="Markdown")
            return
        try: target_id = int(args[1])
        except ValueError:
            bot.send_message(message.chat.id, "❌ Ошибка: ID должен состоять только из цифр!")
            return
        bot.send_message(message.chat.id, f"🔄 Запускаю протокол амнистии для `{target_id}`...")
        unbanned_count = unban_user_everywhere(target_id)
        tag_info = ""
        if len(args) == 3:
            new_tag = args[2]
            if new_tag.lower() == "none":
                users_collection.update_one({"_id": target_id}, {"$unset": {"custom_tag": ""}})
                tag_info = "\n🔖 Глобальный тег очищен."
            else:
                users_collection.update_one({"_id": target_id}, {"$set": {"custom_tag": new_tag}}, upsert=True)
                tag_info = f"\n🔖 Присвоен новый тег: `{new_tag}`"
        bot.send_message(
            message.chat.id, 
            f"✅ **Амнистия завершена!**\nЮзер `{target_id}` вычеркнут из Черного Списка в {unbanned_count} чатах.{tag_info}\n\n⚠️ *Передайте ему, что он может заново вступать в группы по ссылкам.*",
            parse_mode="Markdown"
        )

    @bot.message_handler(commands=['admin'])
    def promote_to_admin_global(message):
        try:
            staff_member = bot.get_chat_member(STAFF_GROUP_ID, message.from_user.id)
            if staff_member.status not in ['administrator', 'creator']:
                bot.send_message(message.chat.id, "❌ Отказано. Только руководство может раздавать погоны.")
                return
        except Exception: return 
        args = message.text.split(maxsplit=2)
        if len(args) < 3:
            bot.send_message(message.chat.id, "❌ Формат: `/admin [ID] [Должность]`\nПример: `/admin 123456789 прЫнц`", parse_mode="Markdown")
            return
        try: target_id = int(args[1])
        except ValueError:
            bot.send_message(message.chat.id, "❌ Ошибка: ID должен состоять только из цифр!")
            return
        custom_title = args[2]
        if len(custom_title) > 16:
            bot.send_message(message.chat.id, "❌ Ошибка: Телеграм не позволяет делать тег админа длиннее 16 символов. Сократите название.")
            return
        bot.send_message(message.chat.id, f"🔄 Запускаю протокол «Коронация» для `{target_id}`.\nНазначаю права и должность «{custom_title}» по всей сети...", parse_mode="Markdown")
        all_chats = []
        all_chats.extend(chat_ids_parni.values())
        all_chats.extend(chat_ids_mk.values())
        all_chats.extend(chat_ids_ns.values())
        all_chats.extend(chat_ids_rainbow.values())
        all_chats.extend(chat_ids_gayznak.values())
        unique_chats = set(all_chats)
        success_count = 0
        error_count = 0
        for cid in unique_chats:
            try:
                bot.promote_chat_member(
                    chat_id=cid, user_id=target_id, can_manage_chat=True, can_change_info=False,
                    can_delete_messages=True, can_restrict_members=True, can_invite_users=True,
                    can_pin_messages=False, can_manage_video_chats=True, is_anonymous=True, can_promote_members=False
                )
                bot.set_chat_administrator_custom_title(chat_id=cid, user_id=target_id, custom_title=custom_title)
                success_count += 1
            except Exception: error_count += 1
            time.sleep(1)
        bot.send_message(
            message.chat.id, 
            f"✅ **Коронация завершена!** 👑\n\nПользователь `{target_id}` назначен модератором в **{success_count}** чатах.\n🔖 Выдана должность: `{custom_title}`\n\n⚠️ *Ошибок/Пропусков: {error_count} (юзера нет в чате или у бота не хватает прав).*\n\n**Важно:** Пусть новый админ добавится во все нужные чаты, если он еще не там, чтобы права применились корректно.",
            parse_mode="Markdown"
        )

    @bot.message_handler(commands=['unadmin'])
    def demote_admin_global(message):
        try:
            staff_member = bot.get_chat_member(STAFF_GROUP_ID, message.from_user.id)
            if staff_member.status not in ['administrator', 'creator']:
                bot.send_message(message.chat.id, "❌ Отказано. Только руководство может срывать погоны.")
                return
        except Exception: return 
        args = message.text.split()
        if len(args) < 2:
            bot.send_message(message.chat.id, "❌ Формат: `/unadmin [ID]`\nПример: `/unadmin 123456789`", parse_mode="Markdown")
            return
        try: target_id = int(args[1])
        except ValueError:
            bot.send_message(message.chat.id, "❌ Ошибка: ID должен состоять только из цифр!")
            return
        bot.send_message(message.chat.id, f"🔄 Запускаю протокол «Разжалование» для `{target_id}`...\nСнимаю права по всей сети...", parse_mode="Markdown")
        all_chats = []
        all_chats.extend(chat_ids_parni.values())
        all_chats.extend(chat_ids_mk.values())
        all_chats.extend(chat_ids_ns.values())
        all_chats.extend(chat_ids_rainbow.values())
        all_chats.extend(chat_ids_gayznak.values())
        unique_chats = set(all_chats)
        success_count = 0
        error_count = 0
        for cid in unique_chats:
            try:
                bot.promote_chat_member(
                    chat_id=cid, user_id=target_id, can_manage_chat=False, can_change_info=False,
                    can_delete_messages=False, can_restrict_members=False, can_invite_users=False,
                    can_pin_messages=False, can_manage_video_chats=False, is_anonymous=False, can_promote_members=False
                )
                success_count += 1
            except Exception: error_count += 1
            time.sleep(1)
        bot.send_message(
            message.chat.id, 
            f"✅ **Разжалование завершено!** 📉\n\nПользователь `{target_id}` лишен прав модератора в **{success_count}** чатах.\n\n⚠️ *Ошибок/Пропусков: {error_count} (юзер уже не админ или его нет в чате).* \n\n**Важно:** Кастомный тег должности удаляется автоматически при снятии прав.",
            parse_mode="Markdown"
        )