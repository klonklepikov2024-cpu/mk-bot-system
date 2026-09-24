import random
import datetime
import time
from zoneinfo import ZoneInfo

# 🔥 ПРАВИЛЬНЫЕ ИМПОРТЫ ПОД ТВОЙ TELEBOT 🔥
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.mongodb import MongoDBJobStore
from database.mongo import client, db, paid_collection 

from config import APP_URL
WEBAPP_URL = f"{APP_URL.rstrip('/')}/webapp"

tz = ZoneInfo("Europe/Moscow")

jobstores = {
    'default': MongoDBJobStore(client=client, database='elite_bot_db', collection='apscheduler_jobs')
}

scheduler = BackgroundScheduler(jobstores=jobstores, timezone=tz)

# Копия настроек семян для планировщика
CROPS = {
    "radish": {"name": "🧅 Редис", "grow_time": 4*3600, "water_req": False},
    "mizuna": {"name": "🥬 Мизуна", "grow_time": 6*3600, "water_req": False},
    "tomato": {"name": "🍅 Помидоры", "grow_time": 12*3600, "water_req": False},
    "sunflower": {"name": "🌻 Подсолнух", "grow_time": 24*3600, "water_req": True},
    "watermelon": {"name": "🍉 Арбуз", "grow_time": 48*3600, "water_req": True},
    "chestnut": {"name": "🌳 К. Каштан", "grow_time": 7*24*3600, "water_req": True},
    "rhododendron": {"name": "🌸 Рододендрон", "grow_time": 3*24*3600, "water_req": True}
}

# ================= 1. БАЗОВЫЕ ФУНКЦИИ И РОЗЫГРЫШИ =================

def schedule_message_deletion(chat_id, message_id, delay_seconds, bot_instance):
    run_date = datetime.datetime.now(tz) + datetime.timedelta(seconds=delay_seconds)
    scheduler.add_job(
        delete_task_executor, 
        'date', 
        run_date=run_date, 
        args=[chat_id, message_id],
        id=f"del_{chat_id}_{message_id}",
        replace_existing=True
    )

def delete_task_executor(chat_id, message_id):
    from core.bot import bot
    try: bot.delete_message(chat_id, message_id)
    except: pass

def check_giveaways_task():
    now = datetime.datetime.now()
    
    # 1. ЗАВЕРШЕНИЕ РОЗЫГРЫШЕЙ (Те, чье время вышло)
    ended_gws = db['giveaways'].find({"status": "active", "end_date": {"$lte": now}})
    
    for gw in ended_gws:
        gw_id = gw["_id"]
        last_ticket = gw.get("last_ticket_num", 0)
        
        if last_ticket == 0:
            db['giveaways'].update_one({"_id": gw_id}, {"$set": {"status": "completed", "winner": "Нет участников"}})
            continue
            
        winners_count = gw.get("winners_count", 1)
        # Защита: не можем выдать больше призов, чем всего куплено билетов
        winners_count = min(winners_count, last_ticket)
        
        # Выбираем уникальные номера билетов
        winning_numbers = random.sample(range(1, last_ticket + 1), winners_count)
        tickets = list(db['tickets_history'].find({"giveaway_id": gw_id}))
        winners_info = [] 
        
        for win_num in winning_numbers:
            for t in tickets:
                r_start, r_end = map(int, t['range'].split('-'))
                if r_start <= win_num <= r_end:
                    winners_info.append({"uid": t['uid'], "name": t['name'], "ticket": win_num})
                    break
                    
        winners_names_str = ", ".join([f"{w['name']} (№{w['ticket']})" for w in winners_info])
        
        db['giveaways'].update_one({"_id": gw_id}, {
            "$set": {
                "status": "completed", 
                "winner_name": winners_names_str, 
                "winners_data": winners_info 
            }
        })
        
        from core.bot import bot
        from config import STAFF_GROUP_ID, PRIZES_THREAD_ID
        
        # Уведомляем каждого счастливчика в личку
        for w in winners_info:
            try:
                bot.send_message(w['uid'], f"🏆 **ВЫ СОРВАЛИ КУШ В РОЗЫГРЫШЕ!** 🏆\n\nВаш билет №{w['ticket']} оказался победным! Скоро с вами свяжутся администраторы для выдачи приза: **{gw['title']}**.", parse_mode="Markdown")
            except: pass
            
        # Отчет админам
        admin_report = f"🎉 **РОЗЫГРЫШ ЗАВЕРШЕН!**\n\n🎁 Приз: **{gw['title']}**\n🏆 Победители:\n"
        for i, w in enumerate(winners_info, 1):
            admin_report += f"{i}. {w['name']} (`{w['uid']}`) — Билет №{w['ticket']}\n"
            
        try:
            bot.send_message(STAFF_GROUP_ID, admin_report, message_thread_id=PRIZES_THREAD_ID, parse_mode="Markdown")
        except: pass

    # 2. ПРОГРЕВ ГОРЯЩИХ РОЗЫГРЫШЕЙ (Те, кому осталось < 1 часа)
    almost_ended = db['giveaways'].find({
        "status": "active", 
        "teased": {"$ne": True}, # Ищем те, о которых мы еще не трубили
        "end_date": {"$lte": now + datetime.timedelta(hours=1)}
    })
    
    for gw in almost_ended:
        # Ставим флажок, что мы уже прорекламировали этот розыгрыш
        db['giveaways'].update_one({"_id": gw["_id"]}, {"$set": {"teased": True}})
        
        time_left_mins = int((gw['end_date'] - now).total_seconds() / 60)
        text = f"🔥 <b>ГОРИТ РОЗЫГРЫШ: {gw['title']}!</b>\n\n⏳ Осталось всего <b>{time_left_mins} минут</b>!\n🎟 Куплено билетов: {gw.get('total_tickets', 0)}. Шансы на победу АНОМАЛЬНО ВЫСОКИЕ!\n\nЗалетай, пока время не вышло!"
        
        broadcast_teaser(text, "🎫 Забрать билет", "giveaways")

def tick_blue_safe():
    """Каждую минуту добавляем 5 очков в Сейф Данных"""
    db['safes_state'].update_one({"_id": "safe_blue"}, {"$inc": {"balance": 5}})

# ================= 2. ПЕРСОНАЛЬНЫЕ УВЕДОМЛЕНИЯ В ЛС =================

def personal_farm_notifications():
    """Проверяет грядки, пишет в ЛС и генерирует нападение Вредителей!"""
    from core.bot import bot
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
    from config import APP_URL
    WEBAPP_URL = f"{APP_URL.rstrip('/')}/webapp"
    
    now = int(time.time())
    growing_plots = db['farm_plots'].find({"status": "growing"})
    
    # === ЗООПАРК СКАЙНЕТА ===
    possible_pests = [
        {"id": "goat", "emoji": "🐐", "name": "Соседская Коза", "targets": ["money_tree", "chestnut", "cactus", "amanita"]},
        {"id": "crow", "emoji": "🐦", "name": "Наглая Ворона", "targets": ["sunflower", "watermelon"]},
        {"id": "caterpillar", "emoji": "🐛", "name": "Троянская Гусеница", "targets": ["radish", "mizuna", "tomato", "parsley"]},
        {"id": "locust", "emoji": "🚁", "name": "Дрон-Саранча", "targets": ["sunflower", "rhododendron"]},
        {"id": "mole", "emoji": "⛏", "name": "Крипто-Крот", "targets": ["money_tree", "watermelon", "tomato"]},
        {"id": "miner", "emoji": "🪲", "name": "Жук-Майнер", "targets": ["ALL"]}
    ]
    
    for plot in growing_plots:
        seed = plot.get("seed_type")
        if seed not in CROPS: continue
        crop = CROPS[seed]
        uid = plot["uid"]
        planted_at = plot.get("planted_at", now)
        last_watered = plot.get("last_watered", now)
        
        markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🚜 На ферму", web_app=WebAppInfo(url=f"{WEBAPP_URL}?tab=farm")))

        # --- 1. ПРОВЕРКА НА ВРЕДИТЕЛЯ (ЕСЛИ УЖЕ ЗАРАЖЕНО) ---
        if plot.get("pest"):
            spawned_at = plot["pest"].get("spawned_at", now)
            if now - spawned_at > 43200: # 12 часов на то, чтобы убить гада
                db['farm_plots'].update_one({"_id": plot["_id"]}, {"$set": {"status": "withered"}, "$unset": {"pest": ""}})
                try: bot.send_message(uid, f"🥀 <b>УРОЖАЙ УНИЧТОЖЕН!</b>\n{plot['pest']['name']} {plot['pest']['emoji']} сожрал(а) ваш {crop['name']}.", parse_mode="HTML", reply_markup=markup)
                except: pass
            continue # Если заражено, воду не проверяем и не растем
            
        # --- 2. ГЕНЕРАЦИЯ НОВОГО НАПАДЕНИЯ (Шанс 2% каждые 15 мин) ---
        if random.randint(1, 100) <= 2: 
            valid_pests = [p for p in possible_pests if seed in p["targets"] or "ALL" in p["targets"]]
            if valid_pests:
                pest = random.choice(valid_pests)
                
                # Проверка АВТО-ЩИТА!
                user_db = paid_collection.find_one({"uid": uid}) or {}
                if user_db.get("immunity", 0) > 0:
                    paid_collection.update_one({"uid": uid}, {"$inc": {"immunity": -1}})
                    try: bot.send_message(uid, f"🛡 <b>ЗАЩИТА ФЕРМЫ!</b>\n{pest['name']} {pest['emoji']} попытался сожрать ваш {crop['name']}, но ваш <b>Щит Иммунитета</b> ударил его током!\n<i>(Списан 1 щит, урожай спасен)</i>", parse_mode="HTML", reply_markup=markup)
                    except: pass
                else:
                    # Заражаем грядку!
                    db['farm_plots'].update_one({"_id": plot["_id"]}, {"$set": {"pest": {"id": pest["id"], "name": pest["name"], "emoji": pest["emoji"], "spawned_at": now}}})
                    try: bot.send_message(uid, f"🚨 <b>ТРЕВОГА НА ФЕРМЕ!</b>\nНа ваш {crop['name']} напал(а) <b>{pest['name']} {pest['emoji']}</b>!\n\nУ вас есть <b>12 часов</b>, чтобы зайти в Кабинет и прогнать вредителя, иначе он сожрет урожай!", parse_mode="HTML", reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("👞 ПРОГНАТЬ!", web_app=WebAppInfo(url=f"{WEBAPP_URL}?tab=farm"))))
                    except: pass
                continue # Прерываем цикл, так как напали

        # --- 3. ОБЫЧНЫЕ ПРОВЕРКИ ВОДЫ И СОЗРЕВАНИЯ ---
        if crop["water_req"] and (now - last_watered > 86400):
            db['farm_plots'].update_one({"_id": plot["_id"]}, {"$set": {"status": "withered"}})
            try: bot.send_message(uid, f"🥀 <b>ПЛОХИЕ НОВОСТИ!</b>\nВаш {crop['name']} засох без воды.", parse_mode="HTML", reply_markup=markup)
            except: pass
            continue
            
        if crop["water_req"] and (now - last_watered > 72000) and not plot.get("water_warning"):
            db['farm_plots'].update_one({"_id": plot["_id"]}, {"$set": {"water_warning": True}})
            try: bot.send_message(uid, f"⚠️ <b>ТРЕВОГА НА УЧАСТКЕ!</b>\nВаш {crop['name']} скоро засохнет! (Осталось <4 часов)", parse_mode="HTML", reply_markup=markup)
            except: pass
            continue
            
        if now >= planted_at + crop["grow_time"]:
            db['farm_plots'].update_one({"_id": plot["_id"]}, {"$set": {"status": "ready"}})
            try: bot.send_message(uid, f"✅ <b>УРОЖАЙ ГОТОВ!</b>\nВаш {crop['name']} полностью созрел!", parse_mode="HTML", reply_markup=markup)
            except: pass

def daily_bonus_reminder():
    """Вечернее пуш-уведомление для тех, кто забыл забрать Ежедневный Бонус"""
    from core.bot import bot
    now = datetime.datetime.now()
    
    # Ищем тех, у кого УЖЕ есть стрик (им есть что терять)
    forgetful_users = paid_collection.find({"bonus_streak": {"$gt": 0}})
    
    markup = InlineKeyboardMarkup().add(
        InlineKeyboardButton("🎁 Забрать бонус", web_app=WebAppInfo(url=f"{WEBAPP_URL}?tab=profile"))
    )
    
    count = 0
    for user in forgetful_users:
        last_bonus = user.get("last_bonus_date")
        if not last_bonus:
            continue
            
        time_diff = (now - last_bonus).total_seconds()
        
        # Если прошло >20 часов, но <48 часов
        # Значит, бонус либо уже доступен, либо будет доступен с минуты на минуту, а стрик еще жив!
        if 72000 <= time_diff <= 172800:
            uid = user.get("uid")
            streak = user.get("bonus_streak", 1)
            try:
                bot.send_message(
                    uid,
                    f"⚠️ **Ваш стрик ({streak} дн.) скоро сгорит!**\n\n"
                    f"Кажется, вы забыли забрать свой Ежедневный Бонус. Зайдите в Игровой Кабинет, чтобы не потерять прогресс и получить Осколок рулетки на 7-й день!",
                    parse_mode="Markdown",
                    reply_markup=markup
                )
                count += 1
                time.sleep(0.05) # Защита от Flood Wait
            except:
                pass
                
    if count > 0:
        from config import STAFF_GROUP_ID
        try: bot.send_message(STAFF_GROUP_ID, f"📢 **Умные Push-уведомления:** Отправлено {count} напоминаний о бонусе.")
        except: pass

# ================= 3. РЕКЛАМНАЯ ВОРОНКА ПО ЧАТАМ =================

def broadcast_teaser(text, button_text, tab_name):
    """Безопасная рассылка по группам (Исправленная версия с диплинком)"""
    from core.bot import bot
    from config import chat_ids_mk, chat_ids_parni, chat_ids_ns, chat_ids_gayznak, chat_ids_rainbow, STAFF_GROUP_ID
    
    # 1. Получаем юзернейм бота, чтобы сделать ссылку на него
    bot_username = bot.get_me().username
    deep_link = f"https://t.me/{bot_username}?start=app_{tab_name}"
    
    # 2. Делаем ОБЫЧНУЮ url-кнопку (Они разрешены в группах!)
    keyboard = InlineKeyboardMarkup().add(InlineKeyboardButton(text=button_text, url=deep_link))
    
    # 3. Собираем чаты
    all_target_chats = []
    all_target_chats.extend(chat_ids_mk.values())
    all_target_chats.extend(chat_ids_parni.values())
    all_target_chats.extend(chat_ids_ns.values())
    all_target_chats.extend(chat_ids_gayznak.values())
    all_target_chats.extend(chat_ids_rainbow.values())
    
    unique_chats = set(all_target_chats)
    
    if not unique_chats:
        return

    success_count = 0
    first_error = None
    
    for chat_id in unique_chats:
        try:
            bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard, parse_mode='HTML')
            success_count += 1
            import time
            time.sleep(0.05) # Пауза от бана Telegram
        except Exception as e:
            if not first_error:
                first_error = str(e)
            
    # Отчет админам
    report_msg = f"📢 <b>Зазывала:</b> Реклама отправлена в {success_count} из {len(unique_chats)} чатов."
    if success_count == 0 and first_error:
        report_msg += f"\n\n🚨 <b>ПРИЧИНА ОШИБКИ:</b> <code>{first_error}</code>"
        
    try:
        bot.send_message(STAFF_GROUP_ID, report_msg, parse_mode='HTML')
    except: pass

def smart_funnel_teaser():
    """Умная рекламная карусель с системой Анти-Попугай"""
    # 🔥 ПРЕДОХРАНИТЕЛЬ: СВЕРКА С БАЗОЙ ДАННЫХ 🔥
    now = int(time.time())
    timer_data = db['settings'].find_one({"_id": "teaser_timer"})
    last_time = timer_data.get("last_time", 0) if timer_data else 0
    
    # 21600 секунд = 6 часов. Если прошло меньше — молча выходим
    if now - last_time < 21600:
        return

    teasers = [
        {
            "text": "🎰 <b>УДАЧА ЛЮБИТ СМЕЛЫХ!</b>\n\nДавно не крутили Гача-Рулетку? А ведь там можно выбить <b>Осколки джекпота</b>, <b>Щиты иммунитета</b> или реальные <b>Рубли</b>!\n\n<i>Стоимость прокрута: всего 50 💎</i>",
            "btn": "🎰 Крутить барабан",
            "tab": "profile"
        },
        {
            "text": "⚒ <b>НАКОВАЛЬНЯ ЖДЕТ!</b>\n\nЗнали ли вы, что из <b>50 Осколков</b> можно выковать Золотой Билет (VIP)? А за 3000 очков и 2 щита — получить статус <b>BEYOND</b>!\n\n<i>Проверьте свой инвентарь.</i>",
            "btn": "🎒 Открыть Рюкзак",
            "tab": "inventory"
        },
        {
            "text": "💱 <b>ОБМЕННИК КЭШБЭКА</b>\n\nНакопили рубли, но не хватает до минималки на вывод? \nОбменяйте их на Очки Бдительности с выгодой до <b>30%</b> и играйте по-крупному!",
            "btn": "💼 В Финансы",
            "tab": "finance"
        },
        {
            "text": "⚖️ <b>ТЕНЕВАЯ ЭКОНОМИКА И ГОРЯЩИЕ СКИДКИ!</b>\n\nИщете дешевый VIP или Ордер на Арест? Загляните на Черный Рынок — там игроки сбрасывают цены на артефакты!\nА если лот висит долго, Скайнет автоматически делает на него <b>УЦЕНКУ до -50%</b>!\n\n<i>Покупайте с огромной выгодой или продавайте свои излишки.</i>",
            "btn": "🛒 На Черный Рынок",
            "tab": "market"
        },
        {
            "text": "🎁 <b>ЕЖЕДНЕВНЫЕ ЗАДАНИЯ И КЕЙСЫ!</b>\n\nСкайнет щедро награждает за активность! Общайтесь в чатах, крутите рулетку и открывайте <b>Деревянные, Серебряные и Золотые Сундуки</b> каждый день!\n\n<i>Прогресс сбрасывается в полночь. Успейте забрать награды!</i>",
            "btn": "📋 Открыть Кейсы",
            "tab": "tasks"
        },
        {
            "text": "💼 <b>СТАНЬ АГЕНТОМ СКАЙНЕТА!</b>\n\nПриглашайте друзей в наши чаты по своей ссылке и получайте <b>+15 Очков</b> за каждого! А за каждые 10 человек — бонусный куш <b>+50 Очков</b> сверху!\n\n<i>Генератор персональных ссылок теперь встроен прямо в Игровой Кабинет.</i>",
            "btn": "🔗 Моя партнерская ссылка",
            "tab": "finance"
        },
        {
            "text": "🎁 <b>РЕАЛЬНЫЕ ПРИЗЫ УЖЕ ЖДУТ!</b>\n\nВ Игровом Кабинете запущены новые Розыгрыши! Покупай билеты за Очки Бдительности и выигрывай ценные призы, сертификаты и VIP-доступ.\n\n<i>Чем больше билетов, тем выше шанс сорвать куш! Победитель определяется честным рандомом.</i>",
            "btn": "🎟 Закупиться билетами",
            "tab": "giveaways"
        },
        {
            "text": "🚜 <b>ПОРА СОБИРАТЬ УРОЖАЙ!</b>\n\nТвоя Кибер-Ферма простаивает! Посади помидоры, вырасти подсолнух или рискни с мухомором. Собирай Очки, Осколки рулетки и Ключи от банковских Сейфов!\n\n<i>Не забудь вовремя полить грядки, иначе всё засохнет!</i>",
            "btn": "🌱 На Кибер-Участок",
            "tab": "farm"
        },
        {
            "text": "🗄 <b>ВЗЛОМ СИСТЕМЫ!</b>\n\nВ Финансовом Сейфе лежат реальные рубли, а в Сейфе Данных — горы Очков! Добудь Ключ на ферме, угадай PIN-код и забери весь банк, пока это не сделал кто-то другой!\n\n<i>Лимит взломов ограничен. Расти Конские Каштаны, чтобы получить больше попыток!</i>",
            "btn": "🔐 Взломать Сейф",
            "tab": "farm"
        }
    ]
    
    # Ситуативные тизеры
    blue_safe = db['safes_state'].find_one({"_id": "safe_blue"})
    if blue_safe and blue_safe.get("balance", 0) > 4000:
        teasers.append({
            "text": f"🚨 <b>СЕЙФ ДАННЫХ ПУХНЕТ!</b> 🚨\n\nТам скопилось уже <b>{blue_safe['balance']} 💎</b>!\nДобудьте Синий Ключ на ферме и подберите пин-код из 3 цифр, чтобы забрать всё!",
            "btn": "🗄 Взломать Сейф",
            "tab": "farm"
        })
        
    fund = db['casino_bank'].find_one({"_id": "premium_fund"})
    if fund and fund.get('balance', 0) > 800: 
        teasers.append({
            "text": f"🏆 <b>ФОНД TELEGRAM PREMIUM РАСТЕТ!</b>\n\nСобрано уже <b>{fund['balance']} ⭐️</b>!\nКто заберет главный куш в рулетке? Сделай прокрут первым!",
            "btn": "🎰 Испытать удачу",
            "tab": "profile"
        })

    # 🔥 АНТИ-ПОПУГАЙ: Исключаем то, что отправляли в прошлый раз 🔥
    last_btn = timer_data.get("last_btn", "") if timer_data else ""
    available_teasers = [t for t in teasers if t["btn"] != last_btn]
    
    # Страховка: если список почему-то опустел, берем исходный
    if not available_teasers:
        available_teasers = teasers
        
    selected = random.choice(available_teasers)
    
    # Записываем новое время сброса И ЗАПОМИНАЕМ ВЫБРАННЫЙ ПОСТ в базу
    db['settings'].update_one(
        {"_id": "teaser_timer"}, 
        {"$set": {
            "last_time": now,
            "last_btn": selected["btn"]
        }}, 
        upsert=True
    )

    broadcast_teaser(selected["text"], selected["btn"], selected["tab"])

import os
import json
import requests

def holiday_contest_scout():
    """Разведчик Скайнета: Ищет крупные праздники за 10 дней и предлагает лонгрид-конкурс"""
    now = datetime.datetime.now(tz)
    target_date = now + datetime.timedelta(days=10) # Ищем за 10 дней до события
    month_day = target_date.strftime("%m-%d")

    # Календарь КРУПНЫХ тематических праздников для мужского комьюнити
    major_holidays = {
        "12-31": "Новый Год",
        "02-14": "День всех влюбленных (Конкурс для парочек и тех, кто в активном поиске)",
        "02-23": "23 Февраля (Суровый мужской праздник)",
        "04-01": "1 Апреля (День смеха и нелепых ситуаций)",
        "05-01": "Майские праздники (Открытие шашлычного сезона и отдых на природе)",
        "06-01": "Первый день Лета (Пляжный сезон, шорты и пресс)",
        "10-31": "Хэллоуин (Время темных и мистических образов)",
        "11-11": "Всемирный день холостяка (Показываем себя во всей красе)",
        "11-19": "Международный мужской день"
    }

    if month_day not in major_holidays:
        return

    holiday_name = major_holidays[month_day]
    
    # 1. Проверяем, нет ли уже запущенного конкурса
    active = db['active_contest'].find_one({"_id": "current_event", "status": "running"})
    if active: 
        return # Не перебиваем текущий, пусть юзеры спокойно доиграют
        
    # 2. Проверяем, не предлагали ли мы это уже (защита от спама)
    year = now.year
    proposed_id = f"scout_{month_day}_{year}"
    if db['settings'].find_one({"_id": proposed_id}): 
        return 
        
    db['settings'].insert_one({"_id": proposed_id, "status": "proposed"})

    # 3. Генерируем конкурс через Gemini (Мега-Промпт по твоему ТЗ)
    gemini_key = os.getenv("GEMINI_API_KEY")
    if not gemini_key: return

    system_prompt = """Ты креативный директор мужского Telegram-сообщества. Твоя задача — придумать МАСШТАБНЫЙ тематический фотоконкурс.
    Верни СТРОГО валидный JSON без маркдауна (без ```json).
    Формат ответа:
    {
      "contest_id": "уникальный_id",
      "title": "Яркое название с эмодзи",
      "description": "Короткое описание",
      "announcement_text": "ОГРОМНЫЙ текст поста-анонса и правил (используй HTML теги <b> и <i>). ОБЯЗАТЕЛЬНО СКОПИРУЙ ЭТУ СТРУКТУРУ ТЕКСТА:
      
      [Завлекающее вступление в брутальном мужском стиле]
      
      <b>⚡️ Суть конкурса</b>
      [Что нужно сфоткать в тематике праздника]
      
      <b>🚀 Как участвовать?</b>
      1. Сделай снимок.
      2. Перейди в личку бота и отправь команду /contest.
      3. [Еще один пункт по теме]
      
      <b>🎭 Номинации — выбери свою категорию!</b>
      [Придумай 4-5 крутых названий номинаций по теме конкурса и распиши, за что они даются]
      
      <b>⏰ Важные даты</b>
      [Укажи, что дедлайн приема работ — ровно через 10 дней от сегодня]
      
      <b>💡 Советы для успеха</b>
      [Дай 3 полезных совета по свету, фону и композиции]
      
      <b>📜 ПРАВИЛА КОНКУРСА</b>
      - Кто участвует: Мужчины 18+
      - 1 аккаунт = максимум 3 фото
      - Никаких ИИ, стоков и чужих фото из интернета
      - Запрет на реалистичную кровь, наготу, политику
      - Накрутка = мгновенная дисквалификация",
      "prizes": {
         "1": {"text": "5000 💎 + VIP + 1500 ₽"},
         "2": {"text": "3000 💎 + 3 Ордера на Арест"},
         "3": {"text": "1000 💎 + 2 Щита Иммунитета"}
      }
    }"""
    
    models_queue = ["gemini-3.7-flash", "gemini-3.6-flash"]
    ai_data = None
    
    for model_name in models_queue:
        url = f"[https://generativelanguage.googleapis.com/v1beta/models/](https://generativelanguage.googleapis.com/v1beta/models/){model_name}:generateContent?key={gemini_key}"
        for attempt in range(2):
            try:
                payload = {
                    "systemInstruction": {"parts": [{"text": system_prompt}]},
                    "contents": [{"parts": [{"text": f"Придумай крутой конкурс на тему: {holiday_name}"}]}],
                    "generationConfig": {"temperature": 0.8, "responseMimeType": "application/json"}
                }
                res = requests.post(url, headers={"Content-Type": "application/json"}, json=payload, timeout=30)
                if res.status_code == 200:
                    raw_text = res.json()["candidates"][0]["content"]["parts"][0]["text"]
                    clean_text = raw_text.strip()
                    if clean_text.startswith("```json"): clean_text = clean_text[7:]
                    if clean_text.startswith("```"): clean_text = clean_text[3:]
                    if clean_text.endswith("```"): clean_text = clean_text[:-3]
                    ai_data = json.loads(clean_text.strip())
                    break
            except: time.sleep(2)
        if ai_data: break

    if not ai_data: return

    # 4. Сохраняем в базу как ЧЕРНОВИК СКАЙНЕТА
    ai_data['status'] = 'scout_draft'
    db['active_contest'].update_one({"_id": "current_event"}, {"$set": ai_data}, upsert=True)

    # 5. Отправляем в админ-чат красивое уведомление с кнопками
    from core.bot import bot
    from config import STAFF_GROUP_ID, CONTESTS_THREAD_ID
    
    prize_block = f"\n\n🎁 <b>ПРИЗОВОЙ ФОНД:</b>\n🥇 1 место: {ai_data.get('prizes', {}).get('1', {}).get('text', '')}\n🥈 2 место: {ai_data.get('prizes', {}).get('2', {}).get('text', '')}\n🥉 3 место: {ai_data.get('prizes', {}).get('3', {}).get('text', '')}"
    
    full_text = f"🤖 <b>РАЗВЕДКА СКАЙНЕТА</b> 🤖\n\nСэр, через 10 дней наступит <b>{holiday_name}</b>!\nЯ проверил: активных конкурсов сейчас нет. Я подготовил для вас масштабный проект:\n\n🏷 <b>{ai_data.get('title')}</b>\n\n{ai_data.get('announcement_text')}{prize_block}\n\n👇 <i>Запустить этот конкурс в работу?</i>"
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✅ Одобрить и ЗАПУСТИТЬ", callback_data="scout_deploy_contest"))
    markup.add(InlineKeyboardButton("❌ Отклонить идею", callback_data="scout_reject_contest"))
    
    try:
        bot.send_message(STAFF_GROUP_ID, full_text, parse_mode="HTML", reply_markup=markup, message_thread_id=CONTESTS_THREAD_ID)
    except Exception as e:
        pass

# ================= ЗАПУСК ПЛАНИРОВЩИКА =================

def start_scheduler():
    if not scheduler.running:
        # 1. Ежеминутные технические задачи
        scheduler.add_job(check_giveaways_task, 'interval', minutes=1, id='gw_checker', replace_existing=True)
        scheduler.add_job(tick_blue_safe, 'interval', minutes=1, id='tick_blue', replace_existing=True)
        
        # 2. Уведомления в ЛС (Проверяем грядки каждые 15 минут)
        scheduler.add_job(personal_farm_notifications, 'interval', minutes=15, id='farm_dm', replace_existing=True)
        
        # 3. 🔥 ВЕЧЕРНИЙ ПУШ О БОНУСАХ (Ровно в 20:00 по Москве) 🔥
        scheduler.add_job(daily_bonus_reminder, 'cron', hour=20, minute=0, id='bonus_reminder', replace_existing=True)
             
        # 4. Умная воронка-карусель в чаты (Проверяет базу каждую минуту)
        scheduler.add_job(smart_funnel_teaser, 'interval', minutes=1, id='smart_funnel', replace_existing=True)

        # Умная воронка-карусель в чаты
        scheduler.add_job(smart_funnel_teaser, 'interval', minutes=1, id='smart_funnel', replace_existing=True)
        
        # 🔥 НОВОЕ: Разведчик конкурсов (Каждое утро в 10:00) 🔥
        scheduler.add_job(holiday_contest_scout, 'cron', hour=10, minute=0, id='holiday_scout', replace_existing=True)
        
        scheduler.start()
        print("⏰ APScheduler запущен (Воронка + ЛС Ферма + Розыгрыши + Вечерний Пуш)!")