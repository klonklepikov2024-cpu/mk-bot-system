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
    """Каждую минуту добавляем 60 очков в Сейф Данных"""
    db['safes_state'].update_one({"_id": "safe_blue"}, {"$inc": {"balance": 60}})

# ================= 2. ПЕРСОНАЛЬНЫЕ УВЕДОМЛЕНИЯ В ЛС =================

def personal_farm_notifications():
    """Проверяет грядки и пишет в ЛС, если созрело или засыхает"""
    from core.bot import bot
    now = int(time.time())
    
    growing_plots = db['farm_plots'].find({"status": "growing"})
    
    for plot in growing_plots:
        seed = plot.get("seed_type")
        if seed not in CROPS: continue
        crop = CROPS[seed]
        
        uid = plot["uid"]
        planted_at = plot.get("planted_at", now)
        last_watered = plot.get("last_watered", now)
        
        markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🚜 На ферму", web_app=WebAppInfo(url=f"{WEBAPP_URL}?tab=farm")))

        # 1. Проверка на смерть (засохло) - 24 часа без воды (86400 сек)
        if crop["water_req"] and (now - last_watered > 86400):
            db['farm_plots'].update_one({"_id": plot["_id"]}, {"$set": {"status": "withered"}})
            try: bot.send_message(uid, f"🥀 <b>ПЛОХИЕ НОВОСТИ!</b>\nВаш {crop['name']} засох без воды.\n\nВам придется очистить грядку и посадить новое семя.", parse_mode="HTML", reply_markup=markup)
            except: pass
            continue
            
        # 2. Предупреждение о жажде (осталось < 4 часов до смерти)
        if crop["water_req"] and (now - last_watered > 72000) and not plot.get("water_warning"):
            db['farm_plots'].update_one({"_id": plot["_id"]}, {"$set": {"water_warning": True}})
            try: bot.send_message(uid, f"⚠️ <b>ТРЕВОГА НА УЧАСТКЕ!</b>\nВаш {crop['name']} скоро засохнет!\n\nУ вас осталось меньше 4 часов, чтобы зайти и полить его.", parse_mode="HTML", reply_markup=markup)
            except: pass
            continue
            
        # 3. Созревание урожая
        if now >= planted_at + crop["grow_time"]:
            db['farm_plots'].update_one({"_id": plot["_id"]}, {"$set": {"status": "ready"}})
            try: bot.send_message(uid, f"✅ <b>УРОЖАЙ ГОТОВ!</b>\nВаш {crop['name']} полностью созрел.\n\nСкорее заходите собрать урожай (и, возможно, найти ключи от сейфа)!", parse_mode="HTML", reply_markup=markup)
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
            "text": "⚖️ <b>ТЕНЕВАЯ ЭКОНОМИКА</b>\n\nУ вас есть ненужные ордера на арест или купоны? Продайте их другим игрокам на Черном Рынке за реальный кэшбэк!\n\n<i>Комиссия Скайнета всего 10%.</i>",
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
        
        scheduler.start()
        print("⏰ APScheduler запущен (Воронка + ЛС Ферма + Розыгрыши + Вечерний Пуш)!")