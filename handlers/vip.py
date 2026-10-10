from telebot import types
import time
from datetime import datetime
import pytz
import tempfile
import threading
import os
import random
import requests # <--- Добавить, если еще нет
from core.cfg import cfg  # курсы и цены из панели «🎛 Управление»

def get_crypto_pay_url(custom_payload, amount_stars, description, asset=None):
    import os
    import requests
    
    amount_rub = int(amount_stars * cfg("rub_per_star"))
    API_TOKEN = os.getenv("CRYPTO_TOKEN")
    
    if not API_TOKEN:
        print("❌ ОШИБКА: Токен CRYPTO_TOKEN не найден!", flush=True)
        return None

    url = "https://pay.crypt.bot/api/createInvoice"
    
    headers = {
        "Crypto-Pay-API-Token": API_TOKEN,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    payload = {
        "currency_type": "fiat",
        "fiat": "RUB",
        "amount": str(amount_rub), 
        "payload": custom_payload,
        "description": description
    }
    
    # 👇 ЕСЛИ ПЕРЕДАН КОНКРЕТНЫЙ АССЕТ — ФОРСИРУЕМ ЕГО 👇
    if asset:
        payload["asset"] = asset
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        res = response.json()
        
        if res.get("ok"): 
            return res["result"]["mini_app_invoice_url"]
    except Exception as e: 
        print(f"❌ Ошибка связи с CryptoBot: {e}", flush=True)
        
    return None

from config import (
    VIP_PRICE_STARS, ADMIN_CHAT_IDS, STAFF_GROUP_ID,
    VIP_CHAT_ID, NETWORK_LINKS, all_cities
)
from database import (
    db, users_collection, archive_collection, withdrawals_collection,
    update_user_stats, get_user_stats, get_pending_ref, delete_pending_ref,
    banned_collection
)
from utils import escape_md, get_user_name, get_referral_bonus, net_key_to_name

def analyze_vip_video_speech(bot, file_id, admin_chat_ids):
    """Фоновая задача для распознавания речи из VIP-кружка через Groq API"""
    groq_key = os.getenv("GROQ_API_KEY")
    if not groq_key: return

    temp_video_path = None
    listening_msgs = []
    
    # 1. Отправляем во все админские чаты плашку "Слушаю..." и запоминаем её ID
    for admin_id in admin_chat_ids:
        try:
            msg = bot.send_message(admin_id, "⏳ *Скайнет слушает VIP-кружок...*", parse_mode="Markdown")
            listening_msgs.append((admin_id, msg.message_id))
        except: pass

    try:
        file_info = bot.get_file(file_id)
        downloaded_file = bot.download_file(file_info.file_path)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as temp_video:
            temp_video.write(downloaded_file)
            temp_video_path = temp_video.name

        url = "https://api.groq.com/openai/v1/audio/transcriptions"
        headers = {"Authorization": f"Bearer {groq_key}"}
        
        with open(temp_video_path, "rb") as audio_file:
            files = {"file": ("video.mp4", audio_file, "video/mp4")}
            data = {"model": "whisper-large-v3", "language": "ru", "response_format": "json"}
            response = requests.post(url, headers=headers, files=files, data=data)

        if response.status_code == 200:
            text = response.json().get("text", "").lower()
            
            # Ищем ключевые слова из фразы: "Привет админам вип-чата, сегодня [дата], на часах [время], хочу стать вип-участником"
            keywords = ["привет", "админ", "вип", "сегодня", "час", "хочу", "стать", "участник"]
            matches = sum(1 for k in keywords if k in text)
            score = int((matches / len(keywords)) * 100)

            if score >= 70:
                verdict = f"✅ **Шаблон подтвержден ({score}%)!** Можете выставлять счет."
            else:
                verdict = f"⚠️ **Совпадение низкое ({score}%). Послушайте вручную.**"

            msg_text = f"🤖 **Нейросеть Скайнета (STT):**\nРаспознанный текст:\n_«{text}»_\n\n{verdict}"
        else:
            msg_text = "⚠️ *Ошибка нейросети.* Проверьте кружок вручную."

        # 2. Заменяем плашку "Слушаю..." на готовый результат ИИ
        for chat_id, msg_id in listening_msgs:
            try: bot.edit_message_text(msg_text, chat_id=chat_id, message_id=msg_id, parse_mode="Markdown")
            except: pass

    except Exception as e:
        print(f"Ошибка при работе STT (VIP): {e}")
    finally:
        if temp_video_path and os.path.exists(temp_video_path):
            try: os.remove(temp_video_path)
            except: pass

# Выносим отдельно, так как эта функция нужна и для команды /start
def send_vip_welcome(bot, chat_id, first_name):
    # 🔥 Достаем динамическую цену из базы Скайнета
    try:
        prices = db['settings'].find_one({"_id": "skynet_pricing"})
        current_vip_price = prices.get("vip_price", VIP_PRICE_STARS) if prices else VIP_PRICE_STARS
    except Exception:
        current_vip_price = VIP_PRICE_STARS

    welcome_text = (
        f"Приветствую, {escape_md(first_name)}! 👋\n\n"
        "Это бот отбора в ВИП-чат, вход после верификации (кружок с лицом) "
        f"и оплаты взноса {current_vip_price}⭐️ единоразово!\n\n"
        "В случае, если вы заблокируете бот и не укажете фразу «Я отказываюсь от продолжения», "
        "вы будете заблокированы во всех сетях-партнерах.\n\n"
        "**ПРЕИМУЩЕСТВА УЧАСТИЯ В ВИП-ЧАТЕ:**\n"
        "1) лояльное отношение администраций сетей-партнеров;\n"
        "2) «золотой билет» - вступление в разные города сети;\n"
        "3) бесплатная публикация объявлений через специальный бот.\n\n"
        "Нажмите кнопку ниже, чтобы начать верификацию:"
    )
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("✅ Готов пройти верификацию", callback_data="start_verification"))
    bot.send_message(chat_id, welcome_text, reply_markup=markup, parse_mode="Markdown")

# ==================== 🔐 СЕРВЕРНЫЕ ЦЕНЫ И ВЫДАЧА VIP ====================
# Раньше цена приходила из callback_data (checkout_pay_vip_250, vip_eco_pts_1250_250,
# sec_chance_buy_250). Кнопку можно подделать кастомным клиентом и купить VIP за 1⭐️ или 1 очко.
# Теперь цена хранится на сервере в vip_offers (создаётся, когда админ одобрил кружок),
# а pre_checkout и обработчики оплаты сверяют сумму с ней.
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
import re as _re

vip_offers = db['vip_offers']
star_payments = db['skynet_star_payments']   # защита от повторной обработки одного платежа
def city_price():
    return cfg("city_pass_price")

_HEAVY_BAN_RE = _re.compile(
    r"КРАСН\w* ЗОН|ЧЕРН\w* ЗОН|ЖЕЛТ\w* ЗОН|НАРКОТ|\bЦП\b|\bМЕФ|\bСОЛИ\b|<18|НЕСОВЕРШЕННОЛЕТ|"
    r"СПОНСОР|ЭС?СКОРТ|КОММЕРЦ|\bМП\b", _re.I)


def get_vip_price():
    try:
        prices = db['settings'].find_one({"_id": "skynet_pricing"}) or {}
        return int(prices.get("vip_price", VIP_PRICE_STARS))
    except Exception:
        return VIP_PRICE_STARS


def open_vip_offer(uid, price):
    vip_offers.update_one(
        {"_id": uid},
        {"$set": {"price": int(price), "base_price": int(price), "promo": None, "created": time.time()}},
        upsert=True,
    )


def get_vip_offer(uid):
    return vip_offers.find_one({"_id": uid})


def get_offer_or_legacy(uid):
    """Оффер заявки. Для кнопок, разосланных ДО обновления (в анкете ещё нет поля stage),
    один раз заводим оффер по полной цене, чтобы уже одобренные люди могли оплатить."""
    offer = get_vip_offer(uid)
    if offer:
        return offer
    funnel = db['vip_funnel'].find_one({"_id": uid})
    if funnel and "stage" not in funnel:
        open_vip_offer(uid, get_vip_price())
        db['vip_funnel'].update_one({"_id": uid}, {"$set": {"stage": "awaiting_payment"}})
        return get_vip_offer(uid)
    return None


def expected_vip_price(uid):
    """Сколько должен заплатить юзер: цена его одобренной заявки (с промокодом) или полная цена."""
    offer = get_vip_offer(uid)
    return int(offer["price"]) if offer and offer.get("price") else get_vip_price()


def is_fine_eligible(reason):
    """Можно ли снять бан штрафом (не тяжёлая статья)."""
    return not _HEAVY_BAN_RE.search((reason or "").upper())


def grant_vip_access(bot, uid, unmute_fn, unban_fn, source):
    """Единая выдача VIP после любой оплаты (звёзды, баланс, очки, крипта)."""
    db['vip_funnel'].delete_one({"_id": uid})
    vip_offers.delete_one({"_id": uid})
    try:
        users_collection.update_one({"_id": uid}, {"$set": {"is_vip": True},
                                                   "$unset": {"shame_tag": "", "fine_paid_pending_vip": ""}}, upsert=True)
        unmute_fn(uid)
        unban_fn(uid)
        archive_collection.update_one({"target": str(uid)}, {"$unset": {"banned_in_support": "", "strikes": ""}})
    except Exception as e:
        print(f"Ошибка при амнистии: {e}")

    try: bot.send_message(STAFF_GROUP_ID, f"🤑 **УСПЕШНАЯ ОПЛАТА VIP** ({source})\nЮзер `{uid}` купил доступ навсегда. Ссылку он получил.", parse_mode="Markdown")
    except Exception: pass

    try:
        invite = bot.create_chat_invite_link(VIP_CHAT_ID, member_limit=1, expire_date=int(time.time()) + 7 * 86400)
        bot.send_message(uid, f"🎉 *Оплата получена! Добро пожаловать в элиту.*\n\n👉 [НАЖМИТЕ СЮДА ДЛЯ ВХОДА В VIP-КЛУБ]({invite.invite_link})", parse_mode="Markdown", disable_web_page_preview=True)
    except Exception as e:
        try: bot.send_message(uid, "Оплата прошла, но возникла ошибка со ссылкой. Напиши админу!")
        except Exception: pass
        for admin_id in ADMIN_CHAT_IDS:
            try: bot.send_message(admin_id, f"🚨 Ошибка создания ссылки VIP для {uid}: {e}")
            except Exception: pass

    # Бонус рефоводу (раньше при оплате криптой не начислялся)
    from core.settings import SkynetSettings
    ref_id = get_pending_ref(uid) if SkynetSettings.get().get("referral_system", True) else None
    if ref_id:
        delete_pending_ref(uid)
        update_user_stats(ref_id, invites_add=1)
        stats = get_user_stats(ref_id)
        _, bonus_stars = get_referral_bonus(stats['invites'])
        update_user_stats(ref_id, balance_add=bonus_stars)
        try: bot.send_message(ref_id, f"🥳 **Твой друг оплатил VIP!**\nТебе начислено: **{bonus_stars} звезд** ⭐️", parse_mode="Markdown")
        except Exception: pass


def build_vip_payment_markup(uid, price):
    """Меню оплаты. В кнопках больше нет цен — только действие."""
    markup = types.InlineKeyboardMarkup(row_width=1)
    offer = get_vip_offer(uid) or {}
    if not offer.get("promo"):
        markup.add(types.InlineKeyboardButton("🎫 У меня есть промокод", callback_data="checkout_promo_vip"))
    markup.add(types.InlineKeyboardButton(f"⭐️ Оплатить {price} Звезд", callback_data="checkout_pay_vip"))

    url_usdt = get_crypto_pay_url(f"vip_{uid}", price, f"Оплата VIP Клуба ({price}⭐️)", asset="USDT")
    url_ton = get_crypto_pay_url(f"vip_{uid}", price, f"Оплата VIP Клуба ({price}⭐️)", asset="TON")
    if url_usdt: markup.add(types.InlineKeyboardButton("🟢 Оплатить через USDT (CryptoBot)", url=url_usdt))
    if url_ton: markup.add(types.InlineKeyboardButton("💎 Оплатить через TON (CryptoBot)", url=url_ton))

    paid_user = db['paid_users'].find_one({"uid": uid}) or {}
    rub_balance = paid_user.get("cashback_balance", 0)
    points_balance = paid_user.get("bounty_points", 0)
    cost_rub = int(price * cfg("rub_per_star"))  # курс ₽ за звезду — в панели
    cost_points = price * cfg("points_per_star")  # курс очков за звезду — в панели

    if rub_balance >= cost_rub:
        markup.add(types.InlineKeyboardButton(f"💳 Списать с баланса ({cost_rub}₽)", callback_data="vip_eco_rub"))
    elif rub_balance > 0:
        markup.add(types.InlineKeyboardButton(f"💳 Баланса не хватает (Твой: {rub_balance}₽)", callback_data="insufficient_funds_vip"))
    if points_balance >= cost_points:
        markup.add(types.InlineKeyboardButton(f"🎰 Оплатить очками ({cost_points} очк.)", callback_data="vip_eco_pts"))
    elif points_balance > 0:
        markup.add(types.InlineKeyboardButton(f"🎰 Очков не хватает (Твои: {points_balance})", callback_data="insufficient_funds_vip"))
    return markup


def register_vip_handlers(bot, pending_verification_users, active_vip_requests, safe_from_autoban, ban_user_everywhere, unmute_user_everywhere, unban_user_everywhere):
    from core.guards import is_staff, is_staff_admin, deny_callback

    def _answer(call, text=None, alert=False):
        try: bot.answer_callback_query(call.id, text, show_alert=alert)
        except Exception: pass

    @bot.message_handler(func=lambda message: message.text == "👑 Вступить в VIP-чат")
    def handle_vip_join_button(message):
        # 👇 НОВЫЙ ЖУЧОК СКАЙНЕТА 👇
        users_collection.update_one({"_id": message.from_user.id}, {"$set": {"intent_vip": True}}, upsert=True)
        # 👆 ==================== 👆
        send_vip_welcome(bot, message.chat.id, message.from_user.first_name)

    @bot.message_handler(func=lambda message: message.text == "👤 Партнерская программа")
    def show_profile(message):
        user_id = message.from_user.id
        stats = get_user_stats(user_id)

        invites = stats['invites']
        balance = stats['balance']
        clicks = stats['clicks']

        current_percent, _ = get_referral_bonus(invites + 1)
        ref_link = f"https://t.me/{bot.get_me().username}?start=ref_{user_id}"

        text = (
            f"📊 **Твой профиль партнера**\n\n"
            f"👣 Переходов по ссылке: **{clicks}**\n"
            f"👥 Успешных оплат: **{invites}**\n"
            f"💰 Твой баланс: **{balance} звезд**\n"
            f"📈 Текущая ставка: **{int(current_percent * 100)}%**\n\n"
            f"🔗 **Твоя персональная ссылка:**\n`{ref_link}`"
        )

        markup = types.InlineKeyboardMarkup()
        if balance > 0:
            markup.add(types.InlineKeyboardButton("💸 Запросить вывод средств", callback_data="request_withdrawal"))

        bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="Markdown")

    @bot.callback_query_handler(func=lambda call: call.data == "request_withdrawal")
    def start_withdrawal(call):
        pending_request = withdrawals_collection.find_one({"user_id": call.from_user.id, "status": "pending"})
        if pending_request:
            bot.answer_callback_query(call.id, "❌ У вас уже есть активная заявка. Дождитесь её обработки!", show_alert=True)
            return

        stats = get_user_stats(call.from_user.id)
        if stats['balance'] <= 0:
            bot.answer_callback_query(call.id, "❌ Ваш баланс пуст", show_alert=True)
            return

        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"💰 Ваш баланс: **{stats['balance']} звезд**.\n\nВведите номер карты и название банка для вывода:",
            parse_mode="Markdown"
        )
        bot.register_next_step_handler(call.message, process_withdrawal_details, stats['balance'])

    def process_withdrawal_details(message, amount):
        if not message.text or message.text == "Назад" or len(message.text) < 10:
            bot.send_message(message.chat.id, "❌ Некорректные данные. Попробуйте еще раз через меню профиля.")
            return

        # Повторная проверка: пока юзер вводил реквизиты, могла появиться другая заявка
        if withdrawals_collection.find_one({"user_id": message.from_user.id, "status": "pending"}):
            bot.send_message(message.chat.id, "❌ У вас уже есть активная заявка. Дождитесь её обработки!")
            return
        amount = min(amount, get_user_stats(message.from_user.id)['balance'])
        if amount <= 0:
            bot.send_message(message.chat.id, "❌ Ваш баланс пуст.")
            return

        user_info = get_user_name(message.from_user)
        details = message.text

        withdrawal_id = f"w_{int(time.time())}_{message.from_user.id}"
        withdrawals_collection.insert_one({
            "_id": withdrawal_id,
            "user_id": message.from_user.id,
            "amount": amount,
            "details": details,
            "status": "pending",
            "currency": "stars_ref",
            "timestamp": time.time()
        })

        bot.send_message(message.chat.id, "✅ Заявка принята! Администратор проверит её в ближайшее время.")

        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("✅ Оплачено", callback_data=f"wd_pay_{withdrawal_id}"),
            types.InlineKeyboardButton("❌ Отклонить", callback_data=f"wd_reject_{withdrawal_id}")
        )

        bot.send_message(
            STAFF_GROUP_ID,
            f"💸 **НОВАЯ ЗАЯВКА НА ВЫВОД**\n\n"
            f"👤 **От:** {user_info}\n"
            f"💰 **Сумма:** {amount} звезд\n"
            f"💳 **Реквизиты:** `{details}`",
            reply_markup=markup,
            parse_mode="Markdown"
        )

    @bot.callback_query_handler(func=lambda call: call.data == "start_verification")
    def ask_for_video(call):
        try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except: pass

        existing = db['vip_funnel'].find_one({"_id": call.from_user.id}) or {}
        if existing.get("stage") in ("admin_review", "deciding"):
            _answer(call, "⏳ Ваш кружок уже на проверке у администрации.", alert=True)
            return

        db['vip_funnel'].update_one(
            {"_id": call.from_user.id},
            {"$set": {"timestamp": time.time(), "reminded": False, "stage": "awaiting_video"}},
            upsert=True
        )

        instruction_text = (
            f"{escape_md(call.from_user.first_name)}, запишите видеосообщение (кружок) с лицом и скажите в нем:\n\n"
            "💬 *«Привет админам вип-чата, сегодня [назовите дату], на часах [назовите время], хочу стать вип-участником»*\n\n"
            "Просто отправьте кружок сюда и ожидайте ответа."
        )
        pending_verification_users[call.from_user.id] = True
        bot.send_message(call.message.chat.id, instruction_text, parse_mode="Markdown")

    @bot.message_handler(content_types=['video_note'])
    def handle_video_note(message):
        if message.chat.type != "private": return
        user_id = message.from_user.id

        # Ожидание кружка теперь хранится и в базе: после рестарта/деплоя кружок не теряется
        funnel = db['vip_funnel'].find_one({"_id": user_id}) or {}
        waiting = (pending_verification_users.get(user_id) or funnel.get("stage") == "awaiting_video"
                   or (funnel and "stage" not in funnel))  # анкеты, начатые до обновления
        if not waiting:
            if funnel.get("stage") in ("admin_review", "deciding"):
                bot.send_message(message.chat.id, "⏳ Ваш кружок уже на проверке, ожидайте ответа.")
            return

        db['vip_funnel'].update_one(
            {"_id": user_id},
            {"$set": {"timestamp": time.time(), "reminded": False, "stage": "admin_review"}},
            upsert=True
        )

        active_vip_requests.add(user_id)
        bot.send_message(message.chat.id, f"⏳ {escape_md(message.from_user.first_name)}, проверяем вашу анкету, подождите...")

        current_vip_price = get_vip_price()

        # 👇 ДОСТАЕМ ДОСЬЕ ИЗ БАЗ СКАЙНЕТА
        user_record = archive_collection.find_one({"target": str(user_id)}) or (archive_collection.find_one({"target": message.from_user.username}) if message.from_user.username else None)
        skynet_ban = banned_collection.find_one({"_id": user_id})

        dossier_text = "🟢 **История чиста.** Отличный кандидат."
        if user_record and "history" in user_record:
            dossier_text = "⚠️ **Досье пользователя (последние 5 записей):**\n"
            for entry in user_record["history"][-5:]:
                reason = entry.get('reason', 'Не указана')
                if not reason: reason = 'Не указана'
                dossier_text += f"• {escape_md(str(entry.get('date', '')))} — {escape_md(str(entry.get('action', '')))} ({escape_md(str(reason))})\n"

        if skynet_ban:
            dossier_text += f"\n🚨 **АКТИВНЫЙ БАН В СЕТИ:** {escape_md(str(skynet_ban.get('reason', 'Не указана')))}"
        # 👆 ================================ 👆

        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton(f"✅ Одобрить (Счет на {current_vip_price}⭐️)", callback_data=f"vip_approve_{user_id}"),
            types.InlineKeyboardButton("🔄 Запросить повторно", callback_data=f"vip_retry_{user_id}"),
            types.InlineKeyboardButton("❌ Отказать (Нарушения)", callback_data=f"vip_reject_{user_id}"),
            types.InlineKeyboardButton("🔨 Забанить везде", callback_data=f"vip_ban_{user_id}")
        )

        for admin_id in ADMIN_CHAT_IDS:
            try:
                # Отправляем инфу с пришитым досье!
                bot.send_message(admin_id, f"🚨 **Заявка в VIP!**\nОт: {get_user_name(message.from_user)}\nID: `{user_id}`\n\n{dossier_text}", parse_mode="Markdown")
                bot.forward_message(admin_id, message.chat.id, message.message_id)
                bot.send_message(admin_id, "Действие:", reply_markup=markup)
            except: pass

        pending_verification_users[user_id] = False

        # 🔥 ЗАПУСКАЕМ НЕЙРОСЕТЬ В ФОНЕ 🔥
        threading.Thread(
            target=analyze_vip_video_speech,
            args=(bot, message.video_note.file_id, ADMIN_CHAT_IDS),
            daemon=True
        ).start()

    @bot.message_handler(func=lambda message: message.text and message.text.strip().lower() in ["я отказываюсь от продолжения", "отказываюсь от продолжения"])
    def handle_refusal(message):
        if message.chat.type != "private": return

        user_id = message.from_user.id
        safe_from_autoban.add(user_id)
        pending_verification_users[user_id] = False
        db['vip_funnel'].delete_one({"_id": user_id})
        vip_offers.delete_one({"_id": user_id})

        bot.send_message(message.chat.id, "✅ Ваша заявка аннулирована. Вы можете безопасно заблокировать бота.")

    @bot.callback_query_handler(func=lambda call: call.data.startswith(("vip_approve_", "vip_retry_", "vip_reject_", "vip_ban_", "vip_forceban_", "vip_cancelban_")))
    def handle_vip_decision(call):
        # 🔐 Без этой проверки подделанной кнопкой vip_forceban_<ID> любой мог забанить кого угодно,
        # даже действующего VIP (force=True обходит защиту).
        if not is_staff(call.from_user.id):
            return deny_callback(bot, call)

        action, user_id_str = call.data.rsplit("_", 1)
        try:
            user_id = int(user_id_str)
        except ValueError:
            return
        admin_info = get_user_name(call.from_user)

        # 🛡️ 1. Обработка отмены (админ одумался)
        if action == "vip_cancelban":
            try: bot.edit_message_text("✅ Фух! Действие отменено. Платный клиент спасен! 🛡", call.message.chat.id, call.message.message_id)
            except: pass
            return

        # 🛡️ 2. ПРЕДОХРАНИТЕЛЬ ДЛЯ РУЧНОГО БАНА
        if action == "vip_ban":
            user_data = users_collection.find_one({"_id": user_id}) or {}
            is_vip = user_data.get("is_vip", False)
            is_queer = user_data.get("is_queer", False)

            if is_vip or is_queer:
                status_name = "🏳️‍🌈 BEYOND" if is_queer else "👑 VIP"
                warn_text = (
                    f"⚠️ **СТОП! СРАБОТАЛА ЗАЩИТА СКАЙНЕТА!** ⚠️\n\n"
                    f"Этот пользователь УЖЕ является премиум-клиентом: **{status_name}**!\n"
                    f"Возможно, он просто перепутал ботов или прислал кружок по ошибке. "
                    f"Блокируя его, вы навсегда выкинете действующего платного клиента из ВСЕХ чатов сети.\n\n"
                    f"**Вы абсолютно уверены?**"
                )
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("🚨 ДА, СНЕСТИ ЕГО (ПЕРМАЧ)", callback_data=f"vip_forceban_{user_id}"))
                markup.add(types.InlineKeyboardButton("Отмена (Сохранить клиента)", callback_data=f"vip_cancelban_{user_id}"))
                bot.send_message(call.message.chat.id, warn_text, reply_markup=markup, parse_mode="Markdown")
                return

        # 3. Проверка на двойные клики (кроме форсированного бана).
        # Раньше это был список в памяти: после каждого деплоя кнопки по старым заявкам
        # отвечали «коллега уже обработал». Теперь заявку атомарно «забирает» база.
        if action in ["vip_approve", "vip_retry", "vip_reject", "vip_ban"]:
            claimed = db['vip_funnel'].find_one_and_update(
                {"_id": user_id, "stage": {"$in": ["admin_review", None]}},
                {"$set": {"stage": "deciding", "decided_by": call.from_user.id}}
            )
            if not claimed:
                bot.answer_callback_query(call.id, "❌ Коллега уже обработал эту заявку!", show_alert=True)
                try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
                except: pass
                return
            active_vip_requests.discard(user_id)
            try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
            except: pass

        # 4. Обработка форсированного бана (убираем кнопки у подтверждения)
        if action == "vip_forceban":
            try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
            except: pass
            active_vip_requests.discard(user_id)

        # 5. Установка статусов для логов
        status_text = ""
        if "approve" in action: status_text = "✅ Одобрена (счет)"
        elif "retry" in action: status_text = "🔄 Запрошен повтор"
        elif "reject" in action: status_text = "❌ Отклонена"
        elif "ban" in action: status_text = "🔨 Забанен"

        notification_text = f"📢 **Статус заявки изменен**\n👤 Юзер: `{user_id}`\n📝 Итог: {status_text}\n👮‍♂️ Модератор: {admin_info}"

        for admin_id in ADMIN_CHAT_IDS:
            if admin_id != call.message.chat.id:
                try: bot.send_message(admin_id, notification_text, parse_mode="Markdown")
                except: pass

        # 6. ИСПОЛНЕНИЕ ПРИГОВОРОВ
        if "approve" in action:
            current_vip_price = get_vip_price()
            # Цена фиксируется на сервере — дальше все кнопки оплаты берут её отсюда
            open_vip_offer(user_id, current_vip_price)
            db['vip_funnel'].update_one({"_id": user_id}, {"$set": {"stage": "awaiting_payment", "timestamp": time.time(), "reminded": False}})

            cheap_stars_text = (
                "💡 **Лайфхак: Как купить звёзды ДЕШЕВЛЕ официального курса?**\n\n"
                "Перед оплатой рекомендуем приобрести звёзды через проверенный сервис. "
                "Это выйдет значительно выгоднее, чем покупать их напрямую через Telegram.\n\n"
                "**Инструкция:**\n"
                f"1️⃣ Перейдите по ссылке: {cfg('cheap_stars_url')}\n"
                "2️⃣ Нажмите кнопку «⭐️ Купить звезды»\n"
                "3️⃣ Выберите пункт «👤 Себе»\n"
                f"4️⃣ Выберите пакет «⭐️ {current_vip_price} звезд»\n"
                "5️⃣ Оплатите удобным способом\n\n"
                "После покупки возвращайтесь сюда и оплачивайте VIP-доступ счетом ниже! 👇"
            )
            if cfg("cheap_stars_url"):
                try: bot.send_message(user_id, cheap_stars_text, parse_mode="Markdown", disable_web_page_preview=True)
                except: pass

            try:
                markup = build_vip_payment_markup(user_id, current_vip_price)
                bot.send_message(
                    user_id,
                    f"💎 **Оформление VIP-доступа**\n\nСтоимость: **{current_vip_price}⭐️** (Доступ навсегда)\n\nВыберите удобный способ оплаты ниже 👇",
                    reply_markup=markup,
                    parse_mode="Markdown"
                )
                bot.send_message(call.message.chat.id, f"✅ Меню оплаты с раздельным выбором монет отправлено пользователю {user_id}.")
            except Exception as e:
                if "bot was blocked" in str(e).lower() or "forbidden" in str(e).lower():
                    if user_id in safe_from_autoban:
                        bot.send_message(call.message.chat.id, f"ℹ️ Юзер {user_id} заблокировал бота, НО он официально отказался. Бан отменен.")
                        safe_from_autoban.discard(user_id)
                    else:
                        bot.send_message(call.message.chat.id, f"🚨 Юзер {user_id} заблокировал бота БЕЗ отказа! Авто-бан активирован.")
                        ban_user_everywhere(user_id, reason="Блокировка бота при верификации", admin_name="Auto-Defender")
                else:
                    bot.send_message(call.message.chat.id, f"❌ Ошибка отправки счета: {e}")

        elif "retry" in action:
            bot.send_message(call.message.chat.id, f"🔄 Запрос на повторный кружок отправлен пользователю {user_id}.")
            retry_text = "⚠️ **Ваш кружок не принят**\n\nК сожалению, видео не соответствует требованиям.\nПожалуйста, **запишите кружок повторно**, четко сказав:\n💬 *«Привет админам вип-чата, сегодня [назовите дату], на часах [назовите время], хочу стать вип-участником»*"
            db['vip_funnel'].update_one({"_id": user_id}, {"$set": {"stage": "awaiting_video", "timestamp": time.time(), "reminded": False}})
            try:
                bot.send_message(user_id, retry_text, parse_mode="Markdown")
                pending_verification_users[user_id] = True
            except: bot.send_message(call.message.chat.id, f"❌ Не удалось отправить уведомление.")

        elif "reject" in action:
            bot.send_message(call.message.chat.id, f"❌ Вы отклонили заявку {user_id}.")
            db['vip_funnel'].delete_one({"_id": user_id})
            vip_offers.delete_one({"_id": user_id})

            # 🔥 Если юзер был на "втором шансе", возвращаем его в ЧС 🔥
            # (раньше в ЧС уходил ЛЮБОЙ отклонённый, даже без штрафа и без бана в чатах)
            u_doc = users_collection.find_one({"_id": user_id}) or {}
            if u_doc.get("fine_paid_pending_vip"):
                users_collection.update_one({"_id": user_id}, {"$unset": {"fine_paid_pending_vip": ""}})
                banned_collection.update_one({"_id": user_id}, {"$set": {"reason": "Повторный отказ после оплаты штрафа"}}, upsert=True)

            reject_text = (
                "❌ **К сожалению, ваша заявка отклонена из-за нарушений.**\n\n"
                "Уточнить причину ограничений можно у операторов в поддержке: @FAQMKBOT"
            )
            try: bot.send_message(user_id, reject_text, parse_mode="Markdown")
            except: pass

        elif "ban" in action:
            bot.send_message(call.message.chat.id, "🔨 Запускаю процесс бана...")
            db['vip_funnel'].delete_one({"_id": user_id})
            vip_offers.delete_one({"_id": user_id})

            # Проверяем, нажал ли админ кнопку подтверждения (forceban)
            is_force = "forceban" in action

            # Передаем команду в ядро
            count = ban_user_everywhere(user_id, reason="Не прошел модерацию кружка (бан админом)", admin_name=admin_info, force=is_force)

            if count > 0:
                bot.send_message(call.message.chat.id, f"✅ Пользователь забанен в {count} чатах.")
            else:
                bot.send_message(call.message.chat.id, "🛡 Бан отменен встроенной защитой Скайнета (пользователь — VIP).")

    @bot.callback_query_handler(func=lambda call: call.data.startswith(("wd_pay_", "wd_reject_")))
    def handle_withdrawal_admin(call):
        if not is_staff_admin(bot, call.from_user.id):
            return deny_callback(bot, call)
        # Раньше split("_") на id вида w_123_456 давал 5 частей и падал: кнопки не работали вообще
        _, action, wd_id = call.data.split("_", 2)
        new_status = "paid" if action == "pay" else "rejected"

        # Атомарно: двойной тап больше не спишет баланс дважды
        wd_request = withdrawals_collection.find_one_and_update(
            {"_id": wd_id, "status": "pending"},
            {"$set": {"status": new_status, "processed_by": call.from_user.id, "processed_at": time.time()}}
        )
        if not wd_request:
            bot.answer_callback_query(call.id, "Заявка уже обработана")
            return

        user_id = wd_request['user_id']
        amount = wd_request['amount']
        admin_info = get_user_name(call.from_user)

        if action == "pay":
            update_user_stats(user_id, balance_add=-amount)
            try: bot.send_message(user_id, f"✅ Ваш запрос на вывод {amount} звезд одобрен! Деньги отправлены на ваши реквизиты.")
            except: pass
            try: bot.edit_message_text(call.message.text + f"\n\n✅ ОПЛАЧЕНО: {call.from_user.first_name}", call.message.chat.id, call.message.message_id)
            except: pass
        else:
            try: bot.send_message(user_id, "❌ Ваш запрос на вывод средств был отклонен администрацией.")
            except: pass
            try: bot.edit_message_text(call.message.text + f"\n\n❌ ОТКЛОНЕНО: {call.from_user.first_name}", call.message.chat.id, call.message.message_id)
            except: pass

    @bot.callback_query_handler(func=lambda call: call.data.startswith('buy_city_'))
    def handle_buy_city(call):
        city_name = call.data.split('_', 2)[2]
        from config import get_network_data
        all_cities_live = get_network_data()[6]
        if city_name not in all_cities_live:
            _answer(call, "❌ Город не найден в сети.", alert=True)
            return
        try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except: pass
        bot.send_invoice(call.message.chat.id, title=f"Пропуск: {city_name} 🏙", description=f"Открывает доступ ко всем чатам нашей сети в городе {city_name} навсегда.", invoice_payload=f"city_access_{city_name}", provider_token="", currency="XTR", prices=[types.LabeledPrice(label=f"Доступ к {city_name}", amount=city_price())])

    @bot.callback_query_handler(func=lambda call: call.data.startswith('sec_chance_buy_'))
    def handle_sec_chance_buy(call):
        _answer(call)
        uid = call.from_user.id
        ban = banned_collection.find_one({"_id": uid})
        if not ban:
            bot.send_message(call.message.chat.id, "✅ Блокировки нет, штраф не требуется. Нажмите /start.")
            return
        if not is_fine_eligible(ban.get("reason")):
            bot.send_message(call.message.chat.id, "⛔️ Эту блокировку нельзя снять штрафом. Обратитесь в поддержку: @FAQMKBOT")
            return
        amount = get_vip_price()  # цена с сервера, а не из кнопки
        bot.send_invoice(
            call.message.chat.id,
            title="Оплата штрафа 💸",
            description="Штраф за нарушение правил / срыв верификации. После оплаты запустится процесс проверки.",
            invoice_payload=f"second_chance_payment_{amount}",
            provider_token="", currency="XTR",
            prices=[types.LabeledPrice(label="Штраф", amount=amount)]
        )

    def process_promo_code(message, call_msg):
        try: bot.edit_message_reply_markup(call_msg.chat.id, call_msg.message_id, reply_markup=None)
        except: pass

        user_id = message.from_user.id
        offer = get_vip_offer(user_id)
        if not offer:
            bot.send_message(message.chat.id, "❌ Заявка на VIP неактивна. Пройдите верификацию заново.")
            return
        if offer.get("promo"):
            bot.send_message(message.chat.id, "❌ Промокод уже применён к этой заявке.")
            return

        promo_text = (message.text or "").strip().upper()
        base_price = int(offer.get("base_price") or offer["price"])

        # Атомарно: лимит использований больше не обходится одновременными вводами
        promo_data = None
        if promo_text:
            promo_data = db['promocodes'].find_one_and_update(
                {"_id": promo_text, "is_active": True, "target": {"$in": ["all", "vip"]},
                 "type": {"$in": ["percent", "fixed"]},
                 "$expr": {"$lt": [{"$ifNull": ["$used_count", 0]}, {"$ifNull": ["$usage_limit", 1]}]}},
                {"$inc": {"used_count": 1}},
                return_document=ReturnDocument.AFTER
            )

        if not promo_data:
            bot.send_message(message.chat.id, "❌ Промокод не найден, исчерпан или недействителен. Цена без изменений.")
            bot.send_message(user_id, f"💎 **Оформление VIP-доступа**\n\nК оплате: **{offer['price']}⭐️**", reply_markup=build_vip_payment_markup(user_id, int(offer['price'])), parse_mode="Markdown")
            return

        discount = promo_data["value"]
        new_amount = base_price
        if promo_data["type"] == "percent": new_amount = int(base_price * (1 - discount / 100))
        elif promo_data["type"] == "fixed": new_amount = base_price - discount
        if new_amount < 1: new_amount = 1

        vip_offers.update_one({"_id": user_id}, {"$set": {"price": new_amount, "promo": promo_text}})
        bot.send_message(message.chat.id, f"✅ **Промокод успешно применен!**\nСкидка составила {base_price - new_amount}⭐️.", parse_mode="Markdown")
        bot.send_message(
            user_id,
            f"💎 **Оформление VIP-доступа (Со скидкой)**\n\nК оплате: **{new_amount}⭐️**\n\nВыберите удобный способ оплаты ниже 👇",
            reply_markup=build_vip_payment_markup(user_id, new_amount),
            parse_mode="Markdown"
        )

    @bot.callback_query_handler(func=lambda call: call.data.startswith('checkout_'))
    def handle_checkout(call):
        # Старые кнопки вида checkout_pay_vip_250 тоже приходят сюда: цифру в конце игнорируем
        parts = call.data.split('_')
        action = parts[1] if len(parts) > 1 else ""
        uid = call.from_user.id
        offer = get_offer_or_legacy(uid)
        if not offer:
            _answer(call, "❌ Счёт устарел или заявка неактивна. Пройдите верификацию заново или напишите в поддержку.", alert=True)
            return
        price = int(offer["price"])

        if action == "pay":
            _answer(call)
            try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
            except: pass
            bot.send_invoice(call.message.chat.id, title="Вход в VIP Клуб 👑", description="Оплата доступа.", invoice_payload="vip_access_payment", provider_token="", currency="XTR", prices=[types.LabeledPrice(label="VIP Доступ", amount=price)])
        elif action == "promo":
            _answer(call)
            msg = bot.send_message(call.message.chat.id, "👇 **Введите ваш промокод ответом на это сообщение:**", parse_mode="Markdown")
            bot.register_next_step_handler(msg, process_promo_code, call_msg=call.message)

# ==================== ОПЛАТА VIP ИЗ ЭКОСИСТЕМЫ РУЛЕТКИ ====================
    @bot.callback_query_handler(func=lambda call: call.data.startswith('vip_eco_rub') or call.data.startswith('vip_eco_pts'))
    def handle_vip_ecosystem_payment(call):
        _answer(call)
        currency_type = 'pts' if call.data.startswith('vip_eco_pts') else 'rub'
        user_id = call.from_user.id

        offer = get_offer_or_legacy(user_id)
        if not offer:
            bot.send_message(call.message.chat.id, "❌ Заявка на VIP неактивна. Пройдите верификацию заново.")
            return
        stars_amount = int(offer["price"])

        if currency_type == 'pts':
            cost = stars_amount * cfg("points_per_star")
            update_field = "bounty_points"
            currency_name = "очков"
            revenue_type = "vip_points"
        else:
            cost = int(stars_amount * cfg("rub_per_star"))
            update_field = "cashback_balance"
            currency_name = "₽"
            revenue_type = "vip_rub_balance"

        # Атомарно забираем заявку: двойной тап больше не спишет дважды
        if not vip_offers.find_one_and_update({"_id": user_id, "paying": {"$ne": True}}, {"$set": {"paying": True}}):
            return

        # Атомарное списание только при достаточном балансе (раньше find + $inc без условия → минус)
        charged = db['paid_users'].find_one_and_update(
            {"uid": user_id, update_field: {"$gte": cost}},
            {"$inc": {update_field: -cost}}
        )
        if charged and currency_type == 'pts':
            from core.janitor import log_points
            log_points(user_id, "bounty_points", -cost, reason="vip_purchase")
        if not charged:
            vip_offers.update_one({"_id": user_id}, {"$unset": {"paying": ""}})
            bot.send_message(call.message.chat.id, f"❌ Ошибка транзакции: Недостаточно {currency_name} на счету!")
            return

        if currency_type == 'rub':
            db['ruble_ledger'].insert_one({"uid": user_id, "amount": -cost, "reason": "Оплата VIP с баланса (Скайнет)", "timestamp": time.time()})

        # Пишем в бухгалтерию (в эквиваленте звезд)
        db['daily_revenue'].insert_one({
            "type": revenue_type,
            "amount": stars_amount,
            "timestamp": time.time(),
            "date": datetime.now().strftime("%d.%m.%Y")
        })

        grant_vip_access(bot, user_id, unmute_user_everywhere, unban_user_everywhere,
                         f"экосистема: {cost}{'₽' if currency_type == 'rub' else ' очков'}")
        try: bot.delete_message(call.message.chat.id, call.message.message_id)
        except: pass

    @bot.callback_query_handler(func=lambda call: call.data == "insufficient_funds_vip")
    def handle_insufficient_funds_vip(call):
        bot.answer_callback_query(call.id, "На вашем счету не хватает средств для оплаты VIP! 😔 Поиграйте еще в рулетку или используйте Telegram-звезды.", show_alert=True)

    @bot.pre_checkout_query_handler(func=lambda query: query.invoice_payload.startswith("vip_access_payment") or query.invoice_payload.startswith("city_access_") or query.invoice_payload.startswith("second_chance_payment_"))
    def checkout_process(pre_checkout_query):
        # Сверяем сумму счёта с серверной ценой (раньше одобрялось всё подряд)
        q = pre_checkout_query
        uid = q.from_user.id
        payload = q.invoice_payload
        ok, err = True, None
        if payload == "vip_access_payment":
            if q.total_amount < expected_vip_price(uid):
                ok, err = False, "Сумма счёта не совпадает с ценой. Нажмите /start и запросите новый счёт."
        elif payload.startswith("second_chance_payment_"):
            ban = banned_collection.find_one({"_id": uid})
            if not ban:
                ok, err = False, "Штраф уже не требуется."
            elif not is_fine_eligible(ban.get("reason")):
                ok, err = False, "Эту блокировку нельзя снять штрафом. Обратитесь в @FAQMKBOT."
            elif q.total_amount < get_vip_price():
                ok, err = False, "Сумма штрафа изменилась. Нажмите /start."
        elif payload.startswith("city_access_"):
            if q.total_amount < city_price():
                ok, err = False, "Неверная сумма."
        bot.answer_pre_checkout_query(q.id, ok=ok, error_message=err)

    @bot.message_handler(content_types=['successful_payment'])
    def successful_payment(message):
        # 👇 НОВЫЕ ДВЕ СТРОЧКИ 👇
        from config import get_network_data
        chat_ids_mk, chat_ids_parni, chat_ids_ns, chat_ids_rainbow, chat_ids_gayznak, PARNI_CHATS, all_cities, MAIN_CHANNEL_LINK = get_network_data()

        new_user_id = message.from_user.id
        sp = message.successful_payment
        payload = sp.invoice_payload
        amount = sp.total_amount

        # Повтор апдейта от Telegram больше не выдаст доступ/ссылки дважды
        try:
            star_payments.insert_one({"_id": sp.telegram_payment_charge_id, "uid": new_user_id,
                                      "payload": payload, "amount": amount, "ts": time.time()})
        except DuplicateKeyError:
            return

        if payload.startswith("second_chance_payment_"):
            db['daily_revenue'].insert_one({"type": "fine", "amount": amount, "timestamp": time.time(), "date": datetime.now().strftime("%d.%m.%Y")})

            # 1. Снимаем бан ТОЛЬКО В БАЗЕ (Физически в чатах он еще в бане!)
            banned_collection.delete_one({"_id": new_user_id})

            # 🔥 ВАЖНО: Даем иммунитет от бота, но не пускаем в чаты! 🔥
            users_collection.update_one({"_id": new_user_id}, {"$set": {"fine_paid_pending_vip": True}}, upsert=True)

            # 2. Запускаем верификацию! (Эмитируем нажатие кнопки)
            db['vip_funnel'].update_one(
                {"_id": new_user_id},
                {"$set": {"timestamp": time.time(), "reminded": False, "stage": "awaiting_video"}},
                upsert=True
            )

            instruction_text = (
                f"✅ **Оплата штрафа получена!**\n\n"
                f"Теперь мы можем начать процесс верификации с чистого листа. Запишите видеосообщение (кружок) с лицом и скажите в нем:\n\n"
                "💬 *«Привет админам вип-чата, сегодня [назовите дату], на часах [назовите время], хочу стать вип-участником»*\n\n"
                "Просто отправьте кружок сюда и ожидайте ответа."
            )
            pending_verification_users[new_user_id] = True
            bot.send_message(new_user_id, instruction_text, parse_mode="Markdown")

            try: bot.send_message(STAFF_GROUP_ID, f"🤑 **ОПЛАЧЕН ВТОРОЙ ШАНС (ШТРАФ)!**\nЮзер `{new_user_id}` оплатил {amount}⭐️ за разбан. Бот запросил у него кружок.", parse_mode="Markdown")
            except: pass
            return
        # 👆 ================================= 👆

        if payload.startswith("city_access_"):
            # 👇 НОВАЯ СТРОЧКА: Пишем доход с пропуска 👇
            db['daily_revenue'].insert_one({"type": "city_access", "amount": amount, "timestamp": time.time(), "date": datetime.now().strftime("%d.%m.%Y")})
            purchased_city = payload.replace("city_access_", "")
            users_collection.update_one({"_id": new_user_id}, {"$addToSet": {"purchased_cities": purchased_city}}, upsert=True)

            links_text = f"🎉 **Оплата успешно получена!**\n\nВы приобрели доступ к городу **{purchased_city}**.\nВот ваши персональные одноразовые ссылки для входа:\n\n"
            links_generated = 0
            for net_key, groups in all_cities.get(purchased_city, {}).items():
                for group in groups:
                    try:
                        invite = bot.create_chat_invite_link(group['chat_id'], member_limit=1)
                        network_name = net_key_to_name(net_key)
                        links_text += f"🔹 **{network_name}**: [Вступить]({invite.invite_link})\n"
                        links_generated += 1
                    except: pass

            if links_generated == 0: links_text += "К сожалению, не удалось сгенерировать ссылки. Обратитесь в поддержку @MK_MensClubSUPPORT."
            else: links_text += "\n⚠️ *Внимание: Ссылки одноразовые! Никому их не передавайте, иначе вы не сможете войти сами.*"

            bot.send_message(new_user_id, links_text, parse_mode="Markdown", disable_web_page_preview=True)
            try: bot.send_message(STAFF_GROUP_ID, f"🤑 **ПРОДАЖА ПРОПУСКА!**\nЮзер `{new_user_id}` купил доступ к городу **{purchased_city}** за {amount}⭐️.", parse_mode="Markdown")
            except: pass
            return

        # (Ветка fine_payment_ удалена: такие счета выставляет только Секретарь, и оплата приходит ему, а не Скайнету.)

        if payload != "vip_access_payment":
            # Неизвестный платёж раньше молча превращался в VIP. Теперь — только сигнал админам.
            from core.diag import log_error; log_error("Оплата звёздами", f"неизвестный payload {payload} на {amount}⭐️", new_user_id)
            try: bot.send_message(STAFF_GROUP_ID, f"⚠️ Неизвестный платёж `{payload}` на {amount}⭐️ от `{new_user_id}`. Проверьте вручную.", parse_mode="Markdown")
            except: pass
            return

        # 👇 Пишем доход с VIP 👇
        db['daily_revenue'].insert_one({"type": "vip", "amount": amount, "timestamp": time.time(), "date": datetime.now().strftime("%d.%m.%Y")})
        grant_vip_access(bot, new_user_id, unmute_user_everywhere, unban_user_everywhere, f"звёзды: {amount}⭐️")
