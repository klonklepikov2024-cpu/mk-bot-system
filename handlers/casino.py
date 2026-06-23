import random
import datetime
from config import chat_ids_mk, chat_ids_parni, chat_ids_ns, chat_ids_gayznak
from zoneinfo import ZoneInfo # <--- ДОБАВИТЬ ЭТУ СТРОКУ
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

from core.bot import bot
from core.scheduler import scheduler, schedule_message_deletion
from config import STAFF_GROUP_ID
from database.mongo import paid_collection, db
from utils.logger import logger

# ================= АНТИ-СПАМ СМАЙЛИКАМИ В ЧАТАХ =================
@bot.message_handler(content_types=['dice'], func=lambda message: message.chat.type in ['group', 'supergroup'])
def handle_manual_dice(message):
    try:
        # 1. Молча удаляем брошенный вручную смайлик
        bot.delete_message(message.chat.id, message.message_id)
        
        # 2. Выдаем предупреждение
        warning = bot.send_message(
            message.chat.id, 
            f"⚠️ @{message.from_user.username}, рулетка с реальными призами работает **только через команду** `/казино` (или `/spin`)!\nПростые смайлики здесь бессильны 😅",
            parse_mode="Markdown"
        )
        
        # 3. НАДЕЖНОЕ Самоуничтожение предупреждения через 15 секунд (Без threading!)
        schedule_message_deletion(message.chat.id, warning.message_id, 15, bot)
    except Exception as e:
        # Если у бота нет прав удалять сообщения, логируем это, а не просто глушим
        logger.warning(f"Не удалось обработать ручной дайс в чате {message.chat.id}: {e}")

# ================= ОБНОВЛЕННАЯ ТАБЛИЦА ПРИЗОВ =================
@bot.message_handler(commands=['prizes', 'призы', 'куш'])
def show_casino_prizes(message):
    text = (
        "🎰 **ТАБЛИЦА ПРИЗОВ РУЛЕТКИ** 🎰\n\n"
        "Стоимость игры: **50 очков** (`/spin`)\n\n"
        "💎 **СУПЕР-ПРИЗ:** Telegram Premium на 1 месяц!\n"
        "🏆 **ДЖЕКПОТ (7️⃣7️⃣7️⃣):** Золотой Билет (VIP-доступ) + 💰 300 очков!\n\n"
        "🌟 **РЕДКОСТЬ:** Личный Кастомный Тег (Статус) в чатах!\n"
        "🔥 **ЭПИЧЕСКИЙ:** 🛡 Щит Иммунитета *(Авто-защита от бана)*\n"
        "🚓 **АРТЕФАКТ:** Ордер на Арест *(Отправь любого в мут!)*\n\n"
        "🕊 **АМНИСТИЯ:** Снятие 1 штрафа (или +100 очков).\n"
        "💸 **КУШ:** Кэшбэк от 100 до 250 очков!\n"
        "📢 **СКИДКИ:** Промокоды до -50% на услуги.\n\n"
        "💀 **ОПАСНОСТЬ:** Сектор «Налоговая» (Сжигает 30% ваших очков).\n\n"
        "🥉 **ОБЫЧНЫЙ (Неудача):** 🧩 **1-2 Осколка Джекпота** *(50 шт = 100% Супер-Приз)*"
    )
    try:
        bot.reply_to(message, text, parse_mode="Markdown")
    except Exception as e:
        logger.warning(f"Не удалось отправить таблицу призов: {e}")

# ================= КАЗИНО (РУЛЕТКА) =================
@bot.message_handler(commands=['spin', 'казино', 'рулетка'])
def handle_casino_spin(message):
    uid = message.from_user.id
    user_data = paid_collection.find_one({"uid": uid}) or {}
    points = user_data.get("bounty_points", 0)

    SPIN_PRICE = 50 # Стоимость одной прокрутки

    if points < SPIN_PRICE:
        bot_info = bot.get_me()
        bot_username = bot_info.username
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("💳 Купить очки (В ЛС бота)", url=f"https://t.me/{bot_username}?start=shop"))
        
        try:
            msg = bot.reply_to(
                message, 
                f"❌ **Недостаточно Очков Бдительности!**\n\n"
                f"Стоимость прокрутки: `{SPIN_PRICE}` очков.\n"
                f"Ваш баланс: `{points}` очков.\n\n"
                f"💡 _Нажмите кнопку ниже, чтобы мгновенно перейти в магазин._", 
                parse_mode="Markdown",
                reply_markup=markup
            )
            # НАДЕЖНОЕ удаление сообщения бота и команды юзера через 180 секунд
            schedule_message_deletion(message.chat.id, msg.message_id, 180, bot)
            schedule_message_deletion(message.chat.id, message.message_id, 180, bot)
        except Exception as e:
            logger.warning(f"Ошибка при обработке нехватки баланса для спина: {e}")
        return

    # 1. Списываем очки за прокрут
    paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": -SPIN_PRICE}}, upsert=True)

    # 2. Кидаем анимированные слоты в чат
    try:
        sent_dice = bot.send_dice(message.chat.id, emoji='🎰')
        val = sent_dice.dice.value # Телеграм сам генерирует честный рандом (1-64)

        # 3. НАДЕЖНЫЙ ЗАПУСК ФОНОВОЙ ЗАДАЧИ
        tz = ZoneInfo("Europe/Moscow")
        run_time = datetime.datetime.now(tz) + datetime.timedelta(seconds=2.2)
        scheduler.add_job(
            process_spin_result, 
            'date', 
            run_date=run_time, 
            args=[message.chat.id, message.from_user.username, sent_dice.message_id, val, uid] # Передаем ID вместо объектов
        )
    except Exception as e:
        logger.error(f"Не удалось запустить рулетку для {uid}: {e}")
        # Возвращаем деньги, если бросок сломался
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": SPIN_PRICE}})

def process_spin_result(chat_id, username, dice_msg_id, val, uid): # Изменили аргументы
    user_data = paid_collection.find_one({"uid": uid}) or {}
    points = user_data.get("bounty_points", 0)
    pm_msg = None
    pm_markup = None

    bank_data = db['casino_bank'].find_one({"_id": "premium_fund"}) or {"balance": 0}
    premium_cost_stars = 1500 

    if val == 63:
        if bank_data.get("balance", 0) >= premium_cost_stars:
            db['casino_bank'].update_one({"_id": "premium_fund"}, {"$inc": {"balance": -premium_cost_stars}})
            msg = f"🏆 **ГЛАВНЫЙ СУПЕР-ПРИЗ!!!** 🏆\n\nНевероятно! Барабан остановился на счастливой звезде!\n🎁 **Приз:** Telegram Premium на 3 месяца!\n\n_🎁 Инструкция отправлена в ЛС!_"
            pm_msg = f"💎 **ВЫ ВЫИГРАЛИ TELEGRAM PREMIUM (3 мес.)!** 💎\n\nНажмите кнопку ниже, чтобы забрать ваш приз напрямую у администрации!"
            pm_markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🎁 Забрать Premium", callback_data="claim_premium"))
            
            try: bot.send_message(STAFF_GROUP_ID, f"🚨 **ВНИМАНИЕ! СОРВАН СУПЕР-ПРИЗ!** 🚨\n\nПользователь `{uid}` (@{username}) выбил **TELEGRAM PREMIUM**! Фонд казино списан.") # Использовали username
            except Exception as e: logger.error(f"Не удалось уведомить админов о премиуме: {e}")
        else:
            val = 999 

    # 1.5 РЕЗЕРВНЫЙ СУПЕР-ПРИЗ (Если выпал Премиум, но в кассе пусто)
    if val == 999:
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": 500, "cashback_balance": 500}})
        msg = f"🎰 **МИНИ-ДЖЕКПОТ!** 🎰\n\nВы были в миллиметре от Премиума, но срываете отличный куш!\n🎁 **Приз:** +500 Очков Бдительности и 500 Рублей кэшбэка!"
        pm_msg = f"💎 **Ваш выигрыш:** +500 Очков и 500 Руб. Главный приз (Premium) пока копится в фонде казино, попробуйте позже!"

    # 2. 🎰 ЛЕГЕНДАРНЫЙ ДЖЕКПОТ (7️⃣7️⃣7️⃣ — val: 64)
    elif val == 64:
        code = f"JACKPOT-{random.randint(1000, 9999)}"
        db['promocodes'].insert_one({"_id": code, "type": "percent", "value": 100, "target": "vip", "usage_limit": 1, "used_count": 0, "is_active": True, "owner_uid": uid})
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": 300}})
        msg = f"🚨 **ДЖЕКПОТ!!! 7️⃣7️⃣7️⃣** 🚨\n\n🎟 **Приз:** Золотой Билет (VIP) + 💰 300 очков!\n_🎁 Промокод отправлен в ЛС!_"
        
        pm_msg = (
            f"🎉 **ВАШ ДЖЕКПОТ!**\n"
            f"🎫 VIP-доступ (100% скидка): `{code}`\n"
            f"💰 Начислено: 300 очков.\n\n"
            f"📖 **Как активировать VIP:**\n"
            f"1. Перейдите в @Elitepost_bot\n"
            f"2. Раздел: «Вступить в VIP чат» -> «Готов пройти верификацию»\n"
            f"3. Отправьте видео-кружок и дождитесь одобрения.\n"
            f"4. При выставлении счета нажмите **«🎫 У меня есть промокод»** и введите этот код!\n\n"
            f"_(Счет будет пересчитан до 1⭐️ по правилам Telegram). Если VIP уже есть, код можно подарить!_"
        )
        pm_markup = InlineKeyboardMarkup().add(InlineKeyboardButton("♻️ Сдать билет (+150 Очков)", callback_data=f"trade_promo_{code}_points_150"))

    # 3. 🌟 ЛИЧНЫЙ СТАТУС (Кастомный тег — val: 7, 21, 35)
    elif val in [7, 21, 35]:
        msg = f"🌟 **СУПЕР-РЕДКИЙ ДРОП!** 🌟\n\nВы выиграли право установить **Личный Кастомный Тег** рядом с вашим ником во всех чатах!\n\n_🎁 Заберите приз в ЛС!_"
        
        pm_msg = (
            f"👑 **Ваш выигрыш из рулетки!**\n\n"
            f"Вы получили купон на создание **Личного Статуса (Тега)**!\n\n"
            f"📖 **Что это дает:**\n"
            f"1️⃣ Рядом с вашим именем во всех чатах будет красоваться ваш личный статус (например: `БРАТВА`, `Красавчик`, `БОСС`).\n"
            f"2️⃣ **Скрытый бонус:** Система признает вас элитным участником. Вы получаете иммунитет от авто-мутов за неправильные анкеты!\n\n"
            f"Нажмите кнопку ниже, чтобы заказать свой статус!"
        )
        pm_markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✍️ Заказать свой тег", callback_data="claim_custom_tag"))

    # 4. 🔥 ЭПИЧЕСКИЙ (Щит Иммунитета — val: 1, 22, 43)
    elif val in [1, 22, 43]:
        paid_collection.update_one({"uid": uid}, {"$inc": {"immunity": 1, "bounty_points": 50}})
        msg = f"🔥 **ЭПИЧЕСКИЙ ВЫИГРЫШ!** 🔥\n\n🛡 **Ваш приз:** Щит Иммунитета + 💰 50 очков!\n_Он дает право на бесплатное обращение в поддержку!_"
        
        pm_msg = (
            f"🛡 **Ваш выигрыш! Вы получили 1 Щит Иммунитета и 50 очков!**\n\n"
            f"📖 **Как это работает:**\n"
            f"1️⃣ **Бесплатный билет в поддержку:** Если вас забанили или нужна верификация, просто нажмите кнопку «🆘 Разблокировка», и Щит оплатит обращение вместо 50⭐️!\n"
            f"2️⃣ **Защита от страйков:** Если вы случайно нарушите правила в боте (например, ложный донос или капкан), Щит поглотит наказание.\n\n"
            f"Щит активируется автоматически, когда это необходимо! 😎"
        )
        pm_markup = InlineKeyboardMarkup().add(InlineKeyboardButton("♻️ Разобрать щит (+5 Осколков)", callback_data="trade_shield_shards_5"))

    # 5. 💀 НАЛОГОВАЯ / СКАМ (Сектор Риска — val: 10, 20, 40, 50)
    elif val in [10, 20, 40, 50]:
        lost_points = int(points * 0.3)
        if lost_points < 10: lost_points = 10
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": -lost_points}})
        msg = f"💀 **НАЛОГОВАЯ ПРОВЕРКА!** 💀\n\nВы попали на сектор риска! Налоговая инспекция списывает 30% ваших сбережений.\n_Потеряно: {lost_points} очков._"
        pm_msg = None

    # 6. 🚓 ОРДЕР НА АРЕСТ (Социальный артефакт — val: 5, 17, 29)
    elif val in [5, 17, 29]:
        code = f"ARREST-{random.randint(100, 999)}"
        db['promocodes'].insert_one({"_id": code, "type": "artifact", "value": 0, "target": "mute", "usage_limit": 1, "used_count": 0, "is_active": True, "owner_uid": uid})
        msg = f"🚓 **СОЦИАЛЬНЫЙ АРТЕФАКТ!**\n\nВы нашли **Ордер на Арест**! Теперь у вас есть власть над другими.\n_🎁 Инструкция в ЛС!_"
        
        pm_msg = (
            f"🚓 **Ваш артефакт: Ордер на Арест**\n\n"
            f"Код: `{code}`\n\n"
            f"📜 **Что это дает:**\n"
            f"Вы можете легально отправить ЛЮБОГО пользователя нашей сети чатов в мут (лишить права писать) на 1 час!\n\n"
            f"⚠️ *Ограничения: Запрещено применять на администраторов. Ордер сгорает после одного использования.*"
        )
        pm_markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🚓 Использовать Ордер", callback_data=f"use_arrest_{code}"))

    # 7. 🕊 АМНИСТИЯ ИЛИ ДОНАТ (val: 13, 26, 39, 52)
    elif val in [13, 26, 39, 52]:
        current_strikes = user_data.get("strikes", 0)
        if current_strikes > 0:
            paid_collection.update_one({"uid": uid}, {"$inc": {"strikes": -1}})
            msg = f"🕊 **АМНИСТИЯ!**\n\nСписан 1 штрафной страйк!\n_Текущие страйки: {current_strikes - 1}/3_"
        else:
            paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": 100}})
            msg = f"🕊 **БЕЛЫЙ БИЛЕТ!**\n\nУ вас нет страйков! Мы дарим вам 💰 **100 очков**!"
        pm_msg = msg

    # 8. 💸 КЭШБЭК / УМНОЖИТЕЛЬ (val: 15, 30, 45, 60)
    elif val in [15, 30, 45, 60]:
        win_points = random.choice([100, 150, 250])
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": win_points}})
        msg = f"💸 **КРУПНЫЙ КУШ!**\n\nВы выиграли **{win_points} очков**!"
        pm_msg = f"💸 Ваш выигрыш: +{win_points} очков."

    # 9. 📢 СКИДКИ (Любое кратное 7, кроме уже занятых)
    elif val % 7 == 0:
        promos = [
            {"target": "fine", "value": 50, "prefix": "FINE50", "name": "50% на оплату Штрафа"},
            {"target": "ads", "value": 30, "prefix": "ADS30", "name": "30% на покупку Рекламы"},
            {"target": "vip", "value": 40, "prefix": "VIP40", "name": "40% на покупку VIP"},
            {"target": "all", "value": 15, "prefix": "ALL15", "name": "15% на Любую услугу"}
        ]
        drop = random.choice(promos)
        code = f"{drop['prefix']}-{random.randint(1000, 9999)}"
        db['promocodes'].insert_one({"_id": code, "type": "percent", "value": drop["value"], "target": drop["target"], "usage_limit": 1, "used_count": 0, "is_active": True, "owner_uid": uid})
        
        if drop['target'] == 'vip': instruction = "📖 **Как применить:** Перейдите в @Elitepost\\_bot -> «Вступить в VIP чат». После одобрения кружка, при выставлении счета нажмите **«🎫 У меня есть промокод»**."
        elif drop['target'] == 'ads': instruction = "📖 **Как применить:** В боте публикации рекламы @PostGoldBot\\_bot начните создавать объявление. Выберите сеть, город и отправьте текст. Бот выдаст меню тарифов — нажмите в нём **«🎫 У меня есть промокод»**."
        elif drop['target'] == 'fine': instruction = "📖 **Как применить:** При обращении в Службу Поддержки за разбаном, администратор выставит вам счет на оплату штрафа. Нажмите кнопку **«🎫 У меня есть промокод»** под счетом."
        else: instruction = "📖 **Как применить:** Это универсальный код! Нажмите **«🎫 У меня есть промокод»** при оплате любого штрафа через администратора в боте @FAQMKBOT, рекламы @PostGoldBot\\_bot или VIP-статуса @Elitepost\\_bot ."

        msg = f"✨ **РЕДКИЙ ДРОП!**\n\n🎁 **Ваш приз:** Скидка {drop['name']}!\n_🎁 Промокод отправлен вам в ЛС!_"
        pm_msg = f"✨ **Ваш выигрыш из рулетки!**\n\n🎫 {drop['name']}\nВаш код: `{code}`\n\n{instruction}"
        pm_markup = InlineKeyboardMarkup().add(InlineKeyboardButton("♻️ Обменять на 2 Осколка", callback_data=f"trade_promo_{code}_shards_2"))

    # 9.5 🌟 ЗАМАСКИРОВАННЫЙ КЭШБЭК (Реальные рубли на баланс — val: 11, 33)
    elif val in [11, 33]:
        win_rub = random.choices([100, 250, 500], weights=[75, 20, 5], k=1)[0]
        
        paid_collection.update_one({"uid": uid}, {"$inc": {"cashback_balance": win_rub}})
        msg = f"✨ **РЕДКИЙ ДРОП: ДЕНЕЖНЫЙ КУПОН!** ✨\n\nВы выиграли **{win_rub} руб.** на внутренний счет!\n\n_🎁 Баланс обновлен, подробности в ЛС._"
        pm_msg = f"🎁 **Вы выбили денежный купон на {win_rub} руб.!**\n\nСредства зачислены на ваш баланс. Вы можете копить их для вывода на карту/телефон или оплачивать ими внутренние штрафы и рекламу!"

    # 10. 🥉 УТЕШИТЕЛЬНЫЙ ДРОП (Все остальные числа — Осколки)
    else:
        shards_won = random.choice([1, 1, 1, 2])
        paid_collection.update_one({"uid": uid}, {"$inc": {"jackpot_shards": shards_won}})
        msg = f"🧩 *Барабан остановился...*\n\nВы нашли: **+{shards_won} Осколок Джекпота**!\n_Соберите 50 штук в кабинете для супер-приза._"

    try:
        # Отвечаем на кружок рулетки, используя ID чата и ID сообщения
        bot.send_message(chat_id, msg, reply_to_message_id=dice_msg_id, parse_mode="Markdown")
    except Exception as e:
        logger.warning(f"Не удалось ответить на рулетку в чате: {e}")
    
    if pm_msg:
        try:
            if pm_markup:
                bot.send_message(uid, pm_msg, parse_mode="Markdown", reply_markup=pm_markup)
            else:
                bot.send_message(uid, pm_msg, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Не удалось отправить приз в ЛС юзеру {uid}: {e}")
            try:
                bot.send_message(chat_id, f"⚠️ @{username}, я не смог отправить вам приз в ЛС. Напишите мне в личные сообщения /start!", parse_mode="Markdown") # Использовали username и chat_id
            except: pass

# ================= ЕЖЕДНЕВНЫЙ БОНУС =================
@bot.message_handler(commands=['bonus', 'бонус'])
def handle_daily_bonus(message):
    uid = message.from_user.id
    user_data = paid_collection.find_one({"uid": uid}) or {}

    last_bonus = user_data.get("last_bonus_date")
    now = datetime.datetime.now()

    # Проверка: прошло ли 24 часа?
    if last_bonus:
        time_diff = (now - last_bonus).total_seconds()
        if time_diff < 86400: # 86400 секунд = 24 часа
            hours_left = int((86400 - time_diff) // 3600)
            minutes_left = int(((86400 - time_diff) % 3600) // 60)
            try: bot.reply_to(message, f"⏳ Вы уже забирали бонус сегодня!\nВозвращайтесь через **{hours_left} ч. {minutes_left} мин.**")
            except: pass
            return

    # Выдача награды: от 15 до 50 очков
    bonus_points = random.randint(15, 50)
    bonus_shards = 1 if random.randint(1, 100) <= 5 else 0 # 5% шанс на 1 осколок

    update_data = {
        "$inc": {"bounty_points": bonus_points, "jackpot_shards": bonus_shards},
        "$set": {"last_bonus_date": now}
    }
    paid_collection.update_one({"uid": uid}, update_data, upsert=True)

    reply_text = f"🎁 **Ежедневный бонус получен!**\n\nВам начислено: **+{bonus_points} очков** 💰"
    if bonus_shards > 0:
        reply_text += f"\n🧩 А также: **+{bonus_shards} Осколок рулетки!**"
    reply_text += "\n\n_Возвращайтесь завтра за новой наградой!_"

    try: bot.reply_to(message, reply_text, parse_mode="Markdown")
    except: pass

# ================= АНГЕЛ-ХРАНИТЕЛЬ =================
@bot.message_handler(commands=['angel', 'ангел'])
def handle_guardian_angel(message):
    uid = message.from_user.id
    args = message.text.split()
    
    if len(args) != 2:
        try: bot.reply_to(message, "👼 **Ангел-Хранитель**\n\nПозволяет снять блокировку с любого пользователя (друга) за счет вашего Щита Иммунитета!\n\n**Использование:** `/angel [ID_пользователя]`\n_Пример:_ `/angel 123456789`\n\n_Свой ID можно узнать у бота @getmyid_bot_", parse_mode="Markdown")
        except: pass
        return
        
    target_id_str = args[1]
    if not target_id_str.isdigit():
        try: bot.reply_to(message, "❌ ID пользователя должен состоять только из цифр!")
        except: pass
        return
        
    target_uid = int(target_id_str)
    
    user_data = paid_collection.find_one({"uid": uid}) or {}
    if user_data.get("immunity", 0) < 1:
        try: bot.reply_to(message, "❌ У вас нет активных **🛡 Щитов Иммунитета** для призыва Ангела!\nКупите щит в магазине или выиграйте в рулетку.")
        except: pass
        return
        
    # Списываем щит у спасателя
    paid_collection.update_one({"uid": uid}, {"$inc": {"immunity": -1}})
    
    # Разбаниваем друга (обнуляем страйки и статус)
    paid_collection.update_one({"uid": target_uid}, {"$set": {"strikes": 0, "status": 0}, "$unset": {"topic_type": ""}})
    
    # Запись в досье Скайнета
    now_str = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
    db['archive'].update_one(
        {"target": str(target_uid)}, 
        {"$push": {"history": {"date": now_str, "action": "👼 Ангел-Хранитель", "reason": f"Спасен другом (ID {uid})"}}}, 
        upsert=True
    )
    
    # Приказ Скайнету на физический разбан во всех чатах
    db['skynet_tasks'].insert_one({"uid": target_uid, "action": "full_unban", "timestamp": datetime.datetime.now()})
    
    try: bot.reply_to(message, f"👼 **Магия сработала!**\n\nВы пожертвовали своим Щитом, чтобы спасти пользователя `{target_uid}`. Приказ на снятие всех ограничений передан Скайнету!", parse_mode="Markdown")
    except: pass
    
    try: bot.send_message(target_uid, "👼 **ЧУДО! АНГЕЛ-ХРАНИТЕЛЬ СНИЗОШЕЛ С НЕБЕС!**\n\nКто-то из ваших друзей пожертвовал своим Щитом Иммунитета, чтобы спасти вас!\nВсе ваши блокировки и страйки аннулированы. Ограничения сняты. Приятного общения!", parse_mode="Markdown")
    except: pass

# ================= 🎁 ВНЕЗАПНЫЕ АИРДРОПЫ В ЧАТАХ =================

def trigger_random_airdrop(is_manual=False):
    """Фоновая задача: Скайнет сбрасывает контейнер в случайный чат"""
    try:
        import time
        now = time.time()
        
        # 🔥 ГЛОБАЛЬНЫЙ ПРЕДОХРАНИТЕЛЬ: ЗАЩИТА ОТ РАЗДВОЕНИЯ ПОТОКОВ 🔥
        if not is_manual:
            timer_data = db['settings'].find_one({"_id": "airdrop_timer"})
            
            # Жесткий кулдаун: минимум 2.5 часа (9000 секунд) между автоматическими сбросами
            # Если прошло меньше 2.5 часов — молча отменяем сброс!
            if timer_data and (now - timer_data.get("last_time", 0) < 9000):
                return 
                
        # Синхронизируем время последнего сброса для всех потоков сервера
        db['settings'].update_one({"_id": "airdrop_timer"}, {"$set": {"last_time": now}}, upsert=True)

        # 1. Собираем все чаты Империи в один список
        all_chats = []
        all_chats.extend(chat_ids_mk.values())
        all_chats.extend(chat_ids_parni.values())
        all_chats.extend(chat_ids_ns.values())
        all_chats.extend(chat_ids_gayznak.values())
        
        if not all_chats: return
        
        # 2. Выбираем случайный чат
        target_chat_id = random.choice(all_chats)
        
        # 3. Генерируем уникальный ID и очки (от 10 до 100)
        drop_id = f"drop_{random.randint(10000, 99999)}"
        points_inside = random.randint(10, 100)
        
        # 🔥 УДАЛЯЕМ ПРОТУХШИЕ КОНТЕЙНЕРЫ (Старше 24 часов) 🔥
        import datetime
        yesterday = datetime.datetime.now() - datetime.timedelta(days=1)
        db['active_airdrops'].delete_many({"created_at": {"$lt": yesterday}})

        # 4. Сохраняем инфу о контейнере в базу (Теперь на 5 человек!)
        db['active_airdrops'].insert_one({
            "_id": drop_id,
            "points": points_inside,
            "claimed_count": 0, # Сколько человек уже забрали
            "claimed_by": [],   # Список ID, чтобы не брали дважды
            "max_claims": 5,    # Лимит победителей
            "created_at": datetime.datetime.now()
        })
        
        # 5. Кидаем в чат!
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🎁 Забрать припасы (0/5)", callback_data=f"claim_drop_{drop_id}"))
        
        bot.send_message(
            target_chat_id, 
            "🚨 **ВНИМАНИЕ! СКАЙНЕТ СБРОСИЛ КОНТЕЙНЕР!** 🚨\n\nВнутри ценные Очки Бдительности! Припасов хватит только на **первых 5 человек**!", 
            reply_markup=markup, 
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Ошибка при сбросе Аирдропа: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('claim_drop_'))
def handle_claim_airdrop(call):
    drop_id = call.data.replace("claim_drop_", "")
    uid = call.from_user.id
    
    # Ищем контейнер в базе
    drop_data = db['active_airdrops'].find_one({"_id": drop_id})
    
    if not drop_data:
        try: bot.answer_callback_query(call.id, "❌ Этот контейнер уже заржавел и исчез!", show_alert=True)
        except: pass
        try: bot.delete_message(call.message.chat.id, call.message.message_id)
        except: pass
        return
        
    # Проверяем, не брал ли юзер уже свою долю
    if uid in drop_data.get("claimed_by", []):
        try: bot.answer_callback_query(call.id, "❌ Жадность фраера погубит! Вы уже взяли свою долю.", show_alert=True)
        except: pass
        return
        
    # Проверяем, остались ли места
    if drop_data.get("claimed_count", 0) >= drop_data.get("max_claims", 5):
        try: bot.answer_callback_query(call.id, "❌ Контейнер пуст! Вы не успели.", show_alert=True)
        except: pass
        return
        
    # БЛОКИРУЕМ ДОЛЮ (Атомарный запрос, защищающий от миллисекундных гонок)
    result = db['active_airdrops'].update_one(
        {"_id": drop_id, "claimed_count": {"$lt": 5}, "claimed_by": {"$ne": uid}}, 
        {"$inc": {"claimed_count": 1}, "$push": {"claimed_by": uid}}
    )
    
    if result.modified_count == 0:
        try: bot.answer_callback_query(call.id, "❌ Не успели! Кто-то забрал последнюю долю прямо перед вами.", show_alert=True)
        except: pass
        return

    # 🔥 КОТ-ВОРИШКА В КОНТЕЙНЕРЕ 🔥
    points = drop_data["points"]
    user_data = paid_collection.find_one({"uid": uid}) or {}
    current_points = user_data.get("bounty_points", 0)
    
    chance = random.randint(1, 100)
    
    # 20% шанс, что вместо припасов выскочит кот (работает только если у юзера есть что красть)
    if chance <= 20 and current_points > 20:
        # Кот крадет от 10% до 20% текущего баланса лудомана!
        steal_percent = random.uniform(0.10, 0.20)
        stolen = int(current_points * steal_percent)
        if stolen < 10: stolen = 10
        
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": -stolen}})
        
        alert_msg = f"🐈‍⬛ МЯУ! ВЫ СХВАТИЛИ ДИКОГО КОТА!\n\nВместо припасов из ящика выпрыгнул кот! Он расцарапал вам руки и украл {stolen} ваших очков, пока убегал! 🩸"
        try: bot.answer_callback_query(call.id, alert_msg, show_alert=True)
        except: pass
    else:
        # ВЫДАЕМ ОБЫЧНУЮ НАГРАДУ
        paid_collection.update_one({"uid": uid}, {"$inc": {"bounty_points": points}}, upsert=True)
        try: bot.answer_callback_query(call.id, f"🎉 Вы урвали {points} очков!", show_alert=True)
        except: pass
    
    # МЕНЯЕМ СООБЩЕНИЕ В ЧАТЕ
    new_count = drop_data.get("claimed_count", 0) + 1
    
    try:
        if new_count >= 5:
            # Если забрали последнее - убираем кнопку и пишем итог
            bot.edit_message_text(
                f"📦 **Контейнер полностью разграблен (5/5)!**\n\nКто-то из счастливчиков забрал свои **{points} очков** 💰, а кто-то наткнулся на дикого кота-воришку 🐈‍⬛!\n\n_Ждите следующих сбросов от Скайнета..._",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                parse_mode="Markdown"
            )
        else:
            # Если места еще есть - просто обновляем счетчик на кнопке
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton(f"🎁 Забрать припасы ({new_count}/5)", callback_data=f"claim_drop_{drop_id}"))
            bot.edit_message_reply_markup(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=markup
            )
    except Exception as e:
        logger.debug(f"Игнор ошибки обновления кнопки аирдропа: {e}")

# 🔥 ЗАПУСКАЕМ ТАЙМЕР АИРДРОПОВ (Например, каждые 3 часа)
scheduler.add_job(trigger_random_airdrop, 'interval', hours=3)

# ================= КНОПКИ РУЛЕТКИ ИЗ КАБИНЕТА =================
@bot.callback_query_handler(func=lambda call: call.data == 'show_prizes_btn')
def btn_show_prizes(call):
    try: bot.answer_callback_query(call.id)
    except: pass
    
    # Подменяем автора на того, кто нажал кнопку
    call.message.from_user = call.from_user
    # Вызываем твою готовую функцию!
    show_casino_prizes(call.message)

@bot.callback_query_handler(func=lambda call: call.data == 'play_roulette_btn')
def btn_play_roulette(call):
    try: bot.answer_callback_query(call.id)
    except: pass
    
    # Подменяем автора на того, кто нажал кнопку, чтобы очки списались с него, а не с бота
    call.message.from_user = call.from_user
    # Запускаем оригинальную рулетку
    handle_casino_spin(call.message)