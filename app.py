import os
import sys
import telebot
import requests
from telebot import types
from flask import Flask, request, render_template, session, redirect, url_for, jsonify
from datetime import datetime
from core.settings import SkynetSettings
import pymongo
from pymongo import MongoClient
import pytz
import random
from web_auth import init_web_auth, verify_telegram_webhook_secret
import uuid
import json
import re
import time
import difflib
import threading
import logging
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from core.guards import is_staff, is_staff_admin, deny_callback, acquire_lease, safe_delete
from core.cfg import cfg as _cfg  # настройки из панели «🎛 Управление»
from core.diag import log_error

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("skynet")  # раньше logger не был объявлен: демоны CPA падали на первой же записи в лог


# ====================== НОВЫЕ МОДУЛИ ======================
from config import (
    TOKEN, MONGO_URI, ADMIN_CHAT_IDS, OWNER_ID, VIP_PRICE_STARS,
    STAFF_GROUP_ID, JOURNAL_CHAT_ID, SUPPORT_GROUP_ID,
    MAIN_CHANNEL_ID, MAIN_CHANNEL_LINK, NETWORK_LINKS,
    VIP_CHAT_ID, BEYOND_CHAT_ID, NON_CITIES,
    chat_ids_mk, chat_ids_parni, chat_ids_ns, 
    chat_ids_rainbow, chat_ids_gayznak, PARNI_CHATS,
    all_cities, GROQ_API_KEYS  # <--- ДОБАВИЛИ СЮДА!
)

from database import (
    users_collection, pending_refs_collection, banned_collection,
    posts_collection, archive_collection, temp_posts, proxy_sessions,
    withdrawals_collection, update_user_stats, get_user_stats,
    set_pending_ref, get_pending_ref, delete_pending_ref, db
)

from utils import (
    escape_md, clean_user_text, format_time, get_user_name,
    net_key_to_name, get_referral_bonus
)

from handlers.admin import register_admin_handlers
from handlers.vip import register_vip_handlers, send_vip_welcome
from handlers.posts import register_post_handlers
from handlers.proxy import register_proxy_handlers
from handlers.skynet import register_skynet_handlers

# ==================== ИНИЦИАЛИЗАЦИЯ ====================
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# 🔐 Секреты теперь берутся из переменных окружения (не из кода!)
WEB_USER = os.getenv("WEB_USER")
WEB_PASS = None  # пароль больше не хранится в коде, проверка идёт по хешу в web_auth.py

# === 👑 ROOT PIN ===
ROOT_PIN = os.getenv("ROOT_PIN")
if not ROOT_PIN or len(ROOT_PIN) < 8:
    raise RuntimeError("Задай ROOT_PIN (минимум 8 символов) в переменных окружения!")

# Глобальные переменные
ns_city_substitution = {}
responded = {}
pending_verification_users = {}
active_vip_requests = set()
safe_from_autoban = set()

# 📡 ЖИВОЙ РАДАР (Теперь использует общую базу данных MongoDB)
def add_radar_log(text):
    # Тумблер «Живой Радар»: события безопасности (входы в панель и т.п.) пишутся всегда
    try:
        if not SkynetSettings.get().get("radar_logging", True) and not str(text).startswith(("🔐", "🔑", "⚠️", "🚫", "🚨")):
            return
    except Exception:
        pass
    # Кто из админов нажал кнопку в веб-панели (раньше в логе было не понять — вы или MKprinc)
    try:
        from flask import has_request_context
        if has_request_context() and request.path.startswith("/glaz") and session.get("login"):
            from core.diag import log_admin
            log_admin(session.get("login"), text)
            text = f"[{session.get('login')}] {text}"
    except Exception:
        pass
    now = datetime.now(pytz.timezone('Asia/Yekaterinburg')).strftime("%H:%M:%S")
    # Пишем напрямую в матрицу, чтобы все процессы сервера это видели
    db['radar_logs'].insert_one({
        "text": f"[{now}] {text}",
        "ts": time.time()
    })

# 🔐 Включаем защиту веб-панели: логин + 2FA в Telegram + охранник на /glaz/*
init_web_auth(app, bot, add_radar_log, OWNER_ID)

def is_banned_in_network(user_id):
    from config import get_network_data
    chat_ids_mk, chat_ids_parni, chat_ids_ns, chat_ids_rainbow, chat_ids_gayznak, PARNI_CHATS, all_cities, MAIN_CHANNEL_LINK = get_network_data()

    # 🔥 ИММУНИТЕТ ДЛЯ ВТОРОГО ШАНСА 🔥
    # Если юзер оплатил штраф, но еще не прошел верификацию - бот с ним общается!
    user_data = users_collection.find_one({"_id": user_id}) or {}
    if user_data.get("fine_paid_pending_vip"):
        return False

    # 1. СНАЧАЛА ПРОВЕРЯЕМ БАЗУ СКАЙНЕТА...
    if banned_collection.find_one({"_id": user_id}):
        return True

    # 2. ДВОЙНАЯ ПРОВЕРКА ПО ФИЗИЧЕСКИМ ЯКОРЯМ
    anchor_chats = [
        VIP_CHAT_ID,
        chat_ids_mk.get("БЕЗ ПРЕДРАССУДКОВ"), 
        chat_ids_mk.get("Москва"),            
        chat_ids_mk.get("Екатеринбург"),      
        chat_ids_parni.get("Екатеринбург")    
    ]
    anchor_chats = [cid for cid in anchor_chats if cid]
    for chat_id in anchor_chats:
        try:
            member = bot.get_chat_member(chat_id, user_id)
            if member.status == "kicked": return True
        except: pass 
    return False

def is_indulgence(user_data):
    """Купленная Индульгенция: снятие бана + иммунитет от автоматики, без доступа в премиум-чаты."""
    return bool(user_data.get("indulgence") or user_data.get("custom_tag") == "Индульгенция")


def migrate_indulgence_flags():
    """Разовая миграция. Раньше Индульгенция ставила is_vip и is_queer, и человек проходил в VIP-чат
    и в BEYOND. Оставляем флаги только тем, кто реально состоит в этих чатах."""
    if db['settings'].find_one({"_id": "migration_indulgence_v1"}):
        return
    if not acquire_lease("migration_indulgence", 3600):
        return
    fixed = 0
    for u in users_collection.find({"custom_tag": "Индульгенция"}):
        uid = u["_id"]
        upd = {"indulgence": True}
        for flag, chat in (("is_vip", VIP_CHAT_ID), ("is_queer", BEYOND_CHAT_ID)):
            if not u.get(flag):
                continue
            try:
                m = bot.get_chat_member(chat, uid)
                inside = m.status in ("member", "administrator", "creator") or (m.status == "restricted" and getattr(m, "is_member", False))
            except Exception:
                inside = True  # не смогли проверить — не трогаем
            if not inside:
                upd[flag] = False
                fixed += 1
        users_collection.update_one({"_id": uid}, {"$set": upd})
        time.sleep(0.1)
    db['settings'].update_one({"_id": "migration_indulgence_v1"}, {"$set": {"done": time.time(), "fixed_flags": fixed}}, upsert=True)
    add_radar_log(f"📜 Миграция Индульгенции: снято лишних VIP/QUEER-флагов: {fixed}")


def get_main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(types.KeyboardButton("Создать новое объявление"))
    markup.add(types.KeyboardButton("Удалить объявление"), types.KeyboardButton("Удалить все объявления"))
    markup.add(types.KeyboardButton("👑 Вступить в VIP-чат"), types.KeyboardButton("👤 Партнерская программа"))
    # 👇 НОВАЯ КНОПКА САППОРТА 👇
    markup.add(types.KeyboardButton("💬 Написать в Поддержку"))
    return markup

def safe_set_tag(chat_id, user_id, tag):
    """Безопасная выдача тегов без прав админа (обновление Telegram от Марта 2026)"""
    try:
        # Пробуем нативный метод (если библиотека обновлена)
        bot.set_chat_member_tag(chat_id, user_id, tag)
    except AttributeError:
        # Если библиотека старая, бьем напрямую в API Телеграма
        url = f"https://api.telegram.org/bot{TOKEN}/setChatMemberTag"
        response = requests.post(url, json={"chat_id": chat_id, "user_id": user_id, "tag": tag}, timeout=5)
        
        # 🔥 ИСПРАВЛЕНИЕ: Вызываем жесткую ошибку, если ТГ отказал (например, лимиты), 
        # чтобы Скайнет не записал фейковый успех в свою базу!
        if not response.json().get('ok'):
            raise Exception(f"Telegram API Error: {response.text}")

def is_real_vip(user_id: int) -> bool:
    """Надёжная проверка VIP-статуса по живому состоянию в чате"""
    # 👇 НОВАЯ ЗАЩИТА ИНДУЛЬГЕНЦИИ (Верим базе на слово) 👇
    # Индульгенция — снятие бана и иммунитет, а не VIP: VIP-публикации ей не положены
    # 👆 ================================================ 👆
    
    try:
        member = bot.get_chat_member(VIP_CHAT_ID, user_id)
        
        if member.status in ['member', 'administrator', 'creator']:
            return True
            
        if member.status == 'restricted':
            return getattr(member, 'is_member', False)
            
        return False
        
    except telebot.apihelper.ApiTelegramException as e:
        error = str(e).lower()
        if any(phrase in error for phrase in ["user not found", "chat not found", "not a member", "forbidden", "user is not a member"]):
            # Зачищаем "призраков" в базе
            users_collection.update_one(
                {"_id": user_id}, 
                {"$set": {"is_vip": False}}
            )
            return False
        # Для других ошибок API (например, таймаут) — логируем
        print(f"[is_real_vip] Telegram API error for {user_id}: {e}")
        
    except Exception as e:
        print(f"[is_real_vip] Unexpected error for {user_id}: {e}")
    
    # Fallback — только если Telegram полностью недоступен
    user_data = users_collection.find_one({"_id": user_id}) or {}
    return bool(user_data.get("is_vip", False))

# ==================== МОДУЛЬ 2: УМНАЯ ТАМОЖНЯ + СТАТИСТИКА ====================
@bot.chat_join_request_handler()
def handle_join_requests(message: telebot.types.ChatJoinRequest):
    # 👇 НОВЫЕ ДВЕ СТРОЧКИ 👇
    from config import get_network_data
    chat_ids_mk, chat_ids_parni, chat_ids_ns, chat_ids_rainbow, chat_ids_gayznak, PARNI_CHATS, all_cities, MAIN_CHANNEL_LINK = get_network_data()

    user_id = message.from_user.id
    chat_id = message.chat.id

    # 🔐 Платные клубы обслуживают свои боты. Раньше Скайнет одобрял сюда любую заявку
    # от VIP/QUEER, и VIP попадал в BEYOND в обход анкеты BEYOND-бота.
    if chat_id == BEYOND_CHAT_ID:
        return
    if chat_id == VIP_CHAT_ID:
        try:
            u = users_collection.find_one({"_id": user_id}) or {}
            if u.get("is_vip") and not banned_collection.find_one({"_id": user_id}):
                bot.approve_chat_join_request(chat_id, user_id)
        except Exception as e:
            print(f"Таможня VIP: {e}")
        return

    # 👇 1. СИСТЕМА УЧЕТА CPA ТРАФИКА (ПЕРЕХВАТ ССЫЛКИ + АНТИФРОД) 👇
    agent_id = None
    if message.invite_link and message.invite_link.name and message.invite_link.name.startswith("cpa_"):
        try:
            agent_id = int(message.invite_link.name.split("_")[1])
        except (IndexError, ValueError):
            agent_id = None
    if agent_id and agent_id != user_id:
        # Уже знакомый сети человек (писал в чатах / привязан к городу / в ЧС) — не новый лид
        known = users_collection.find_one({"_id": user_id, "$or": [
            {"main_city": {"$exists": True}}, {"first_seen": {"$exists": True}}]})
        is_new_lead = False
        if not known and not banned_collection.find_one({"_id": user_id}):
            # Атомарно: две заявки подряд (в два чата) больше не создают два лида
            res = db['cpa_traffic'].update_one(
                {"new_user_id": user_id},
                {"$setOnInsert": {"new_user_id": user_id, "agent_id": agent_id, "status": "hold",
                                  "join_time": time.time(), "chat_id": chat_id}},
                upsert=True
            )
            is_new_lead = res.upserted_id is not None

        if is_new_lead:

            # 1. Радуем Агента быстрым дофамином
            try:
                bot.send_message(
                    agent_id, 
                    f"👀 **У вас новый реферал!**\nПользователь подал заявку по вашей ссылке.\n\n⏳ _Скайнет поместил его в холд на {_cfg('cpa_hold_days')} дн. Если он не сбежит и не получит бан за спам, вы получите_ 💼 **Кейс Агента** _(и +1 лид в зачет Конкурса Месяца)!_",
                    parse_mode="Markdown"
                )
            except: pass
            
            # 2. 🔥 ВЫДАЕМ WELCOME-BOX НОВИЧКУ 🔥
            try:
                # Начисляем стартовый капитал (размер — в панели «Управление»)
                from core.cfg import cfg
                _wb_pts, _wb_sh = cfg("cpa_welcome_points"), cfg("cpa_welcome_shields")
                db['paid_users'].update_one(
                    {"uid": user_id}, 
                    {"$inc": {"bounty_points": _wb_pts, "immunity": _wb_sh}}, 
                    upsert=True
                )
                from core.janitor import log_points
                log_points(user_id, "bounty_points", _wb_pts, reason="cpa_welcome_box")
                log_points(user_id, "immunity", _wb_sh, reason="cpa_welcome_box")
                
                # Достаем имя агента для красивого приветствия
                agent_info = db['users'].find_one({"_id": agent_id}) or {}
                agent_name = agent_info.get("first_name", f"Агентом ID {agent_id}")
                
                from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
                # ВАЖНО: Замени FAQMKBOT на реальный юзернейм твоего бота-Секретаря, если он другой
                markup = InlineKeyboardMarkup().add(
                    InlineKeyboardButton("🎮 Открыть Игровой Кабинет", url=f"https://t.me/{cfg('support_bot')}?start=app_profile")
                )
                
                welcome_text = (
                    f"🎁 **СТАРТОВЫЙ НАБОР ВЫЖИВАНИЯ!**\n\n"
                    f"Вы были завербованы в сеть {agent_name}!\n"
                    f"Вам передан секретный кейс новичка:\n"
                    f"💎 **{_wb_pts} Очков Бдительности**\n"
                    f"🛡 **{_wb_sh} Щит Иммунитета** (спасет от мута или вредителя на ферме)\n\n"
                    f"Запускайте Игровой Кабинет, чтобы бесплатно испытать удачу в Гача-Рулетке! Добро пожаловать в Империю. 😎"
                )
                bot.send_message(user_id, welcome_text, parse_mode="Markdown", reply_markup=markup)
            except Exception as e: 
                pass # Если у юзера закрыта личка, просто игнорим

        else:
            # ❌ ЮЗЕР УЖЕ ЕСТЬ В СЕТИ (Или зашел по ссылке другого агента ранее)
            # Просто тихо плюсуем счетчик "Дубликаты" (один раз на пару агент+юзер)
            try:
                db['cpa_duplicates_seen'].insert_one({"_id": f"{agent_id}_{user_id}"})
                db['paid_users'].update_one({"uid": agent_id}, {"$inc": {"cpa_duplicates": 1}}, upsert=True)
            except DuplicateKeyError:
                pass
    # 👆 ============================================================= 👆
    
    # 1. ФИКСИРУЕМ ЗАЯВКУ В СТАТИСТИКЕ (Строго 1 раз за весь период!)
    req_id = f"{user_id}_{chat_id}"
    
    # Ищем, стучался ли он уже в этот чат в текущем периоде
    if not db['period_joins'].find_one({"_id": req_id}):
        db['period_joins'].insert_one({"_id": req_id}) # Запоминаем его стук
        
        # Плюсуем счетчик заявок!
        db['network_stats'].update_one(
            {"_id": "current_period"}, 
            {"$inc": {"total": 1, f"chats.{chat_id}.total": 1}}, 
            upsert=True
        )
    
    try:
        # --- ФАЗА -1: Проверка Глобального Черного Списка ---
        if is_banned_in_network(user_id):
            bot.decline_chat_join_request(chat_id, user_id)
            try:
                bot.send_message(
                    user_id, 
                    "🚫 **Доступ заблокирован за грубые нарушения.**\n\n"
                    "Ваш аккаунт находится в глобальном черном списке сети. "
                    "Разблокировка возможна только после оплаты официального штрафа.\n\n"
                    "📍 Обратитесь в поддержку для уточнения суммы и получения ссылки на оплату: @MK_MensClubSUPPORT",
                    parse_mode="Markdown"
                )
            except: pass
            return

        # --- 🕊️ ЛОКАЛЬНАЯ АМНИСТИЯ ПАРНИ (Размут ТОЛЬКО в 18+) ---
        if chat_id in PARNI_CHATS and SkynetSettings.get().get("parni_autounmute", True):
            user_data = users_collection.find_one({"_id": user_id}) or {}
            last_reason = user_data.get("last_mute_reason", "")
            
            # Проверяем, был ли мут именно за параметры
            if any(word in last_reason for word in ["1 Мая", "параметр"]):
                # РАЗМУЧИВАЕМ ТОЛЬКО В ЭТОЙ СЕТИ
                count = unmute_in_parni_only(user_id)
                
                # Очищаем причину, чтобы не срабатывало повторно
                users_collection.update_one({"_id": user_id}, {"$unset": {"last_mute_reason": ""}})
                
                # Сообщаем админам о частичной свободе
                try: 
                    bot.send_message(STAFF_GROUP_ID, f"🕊️ **ЛОКАЛЬНАЯ АМНИСТИЯ:** Юзер `{user_id}` размучен ТОЛЬКО в сети ПАРНИ 18+ ({count} чатов). В остальных сетях ограничения сохраняются до верификации.")
                except: pass

        # --- ФАЗА 1: Режим БОГА (VIP и BEYOND) ---
        user_data = users_collection.find_one({"_id": user_id}) or {}
        # Индульгенция: таможня пропускает во все города (но не в VIP/BEYOND — они отсечены выше)
        is_privileged = user_data.get("is_vip", False) or user_data.get("is_queer", False) or is_indulgence(user_data)
        
        if not is_privileged:
            for priv_chat in [VIP_CHAT_ID, BEYOND_CHAT_ID]:
                try:
                    member = bot.get_chat_member(priv_chat, user_id)
                    is_physically_there = getattr(member, 'is_member', False) if member.status == 'restricted' else True
                    if member.status in ['member', 'administrator', 'creator'] or (member.status == 'restricted' and is_physically_there):
                        is_privileged = True
                        break 
                except: pass
        
        if is_privileged:
            bot.approve_chat_join_request(chat_id, user_id)
            db['network_stats'].update_one(
                {"_id": "current_period"}, 
                {"$inc": {"approved": 1, "vip_tickets": 1, f"chats.{chat_id}.approved": 1}}, 
                upsert=True
            )
            return

        # --- ФАЗА 0: Санитарный контроль (БИО) [ТОЛЬКО ДЛЯ ОБЫЧНЫХ ЮЗЕРОВ] ---
        settings = SkynetSettings.get()
        if settings.get("bio_hardcheck", True):
            try:
                user_info = bot.get_chat(user_id)
                bio = user_info.bio.lower() if user_info.bio else ""
            except Exception:
                bio = ""  # раньше ошибка API тут обрывала таможню, и заявка висела вечно
            
            allowed_links = ["anonquebot", "secretmessagebot", "askbot", "contactme", "voprosy"]
            has_bad_link = ("t.me/" in bio or "http" in bio) and not any(allowed in bio for allowed in allowed_links)
            
            safe_bio = re.sub(r'(?<!\d)[1-9]\d/1\d{2}/\d{2,3}(?:/\d{1,2}(?:[.,*xхX]\d{1,2})?)?(?!\d)', '', bio)
            safe_bio = re.sub(r'\b(1[0-7])\s*(см|cm)\b', '', safe_bio)
            
            minor_bio_patterns = [
                r'\b(мне|я)\s*(1[0-7])\b',                   
                r'\b(мне|я)\s*18\s*-\s*[1-9]\b',             
                r'\b(1[0-7]|18\s*-\s*[1-9])\s*(лет|годик)\b',
                r'\b(1[0-7])\s*[/\\-]\s*1\d{2}\b',           
                r'\b(200[9]|201[0-9])\s*(г|год|года|г\.р)\b',
                r'\bочень молод(ой|енький)\b'
            ]
            has_bad_age = any(re.search(p, safe_bio) for p in minor_bio_patterns)
            
            if has_bad_age:
                bot.decline_chat_join_request(chat_id, user_id)
                bot.send_message(STAFF_GROUP_ID, f"🚨 **СКАЙНЕТ: ТАМОЖНЯ**\nОтклонена заявка от малолетки (`{user_id}`).\nЗапускаю глобальный БАН...")
                ban_user_everywhere(user_id, reason="Возраст <18 (или 'очень молодой') в БИО", admin_name="Скайнет 🛂")
                return

            if has_bad_link:
                bot.approve_chat_join_request(chat_id, user_id) 
                db['network_stats'].update_one({"_id": "current_period"}, {"$inc": {"approved": 1, f"chats.{chat_id}.approved": 1}}, upsert=True)
                bot.send_message(STAFF_GROUP_ID, f"⚠️ **СКАЙНЕТ: ТАМОЖНЯ**\nПустил спамера со ссылкой в БИО (`{user_id}`) для массовки.\nЗатыкаю рот глобальным МУТОМ 🤐...")
                mute_user_everywhere(user_id, reason="Рекламная ссылка в БИО", admin_name="Скайнет 🛂")
                return

        # --- ФАЗА 2: Биг-чаты (Открытые котлы) ---
        big_chats = [
            chat_ids_mk.get("БЕЗ ПРЕДРАССУДКОВ"),
            chat_ids_mk.get("Секс Туризм"),
            chat_ids_mk.get("Галерея"),
            chat_ids_mk.get("Мужской Чат"),
            chat_ids_mk.get("Фетиши"),  # <--- ВОТ ЗДЕСЬ НУЖНА ЗАПЯТАЯ!
            chat_ids_mk.get("Аренда Жилья")
        ]
        if chat_id in big_chats:
            bot.approve_chat_join_request(chat_id, user_id)
            db['network_stats'].update_one({"_id": "current_period"}, {"$inc": {"approved": 1, f"chats.{chat_id}.approved": 1}}, upsert=True)
            return

        # --- ФАЗА 3: Гео-контроль (Единый город + Метод первой двери + Монетизация) ---
        possible_cities = []
        for city_name, networks in all_cities.items():
            for net, groups in networks.items():
                if any(g['chat_id'] == chat_id for g in groups):
                    possible_cities.append(city_name)
        
        if possible_cities:
            user_data = users_collection.find_one({"_id": user_id}) or {}
            main_city = user_data.get("main_city")
            purchased_cities = user_data.get("purchased_cities", [])

            # Берем первый город для системных сообщений/пейвола (по умолчанию)
            primary_target_city = possible_cities[0]

            if main_city:
                # 📍 СЦЕНАРИЙ А: Юзер уже привязан к городу
                # Пускаем, если его родной город ИЛИ купленный город есть в списке допустимых для этого чата
                if main_city in possible_cities or any(c in purchased_cities for c in possible_cities):
                    bot.approve_chat_join_request(chat_id, user_id)
                    db['network_stats'].update_one({"_id": "current_period"}, {"$inc": {"approved": 1, f"chats.{chat_id}.approved": 1}}, upsert=True)
                    return
                else:
                    # Чужой город! Отклоняем моментально + Кидаем Оффер
                    bot.decline_chat_join_request(chat_id, user_id)
                    
                    markup = types.InlineKeyboardMarkup(row_width=1)
                    markup.add(
                        types.InlineKeyboardButton(f"🎟 Купить пропуск в г. {primary_target_city} ({_cfg('city_pass_price')}⭐️)", callback_data=f"buy_city_{primary_target_city}"),
                        types.InlineKeyboardButton("👑 Купить VIP (Все города)", callback_data="start_verification")
                    )
                    try:
                        bot.send_message(
                            user_id, 
                            f"❌ Ваша заявка в чат **{primary_target_city}** отклонена.\n\n"
                            f"По правилам сети за вами закреплен город: **{main_city}**.\n\n"
                            f"Вы можете приобрести разовый пропуск, либо стать VIP-участником для неограниченного доступа везде.",
                            reply_markup=markup,
                            parse_mode="Markdown"
                        )
                    except: pass
                    return
            else:
                # 📍 СЦЕНАРИЙ Б: Метод «Первой двери» для новичков
                users_collection.update_one({"_id": user_id}, {"$set": {"main_city": primary_target_city}}, upsert=True)
                
                bot.approve_chat_join_request(chat_id, user_id)
                db['network_stats'].update_one({"_id": "current_period"}, {"$inc": {"approved": 1, f"chats.{chat_id}.approved": 1}}, upsert=True)
                return

    except Exception as e:
        print(f"Ошибка Таможни: {e}")

@bot.message_handler(commands=['start'])
def start(message):
    try:
        if message.chat.type != "private":
            bot.send_message(message.chat.id, "Пожалуйста, используйте ЛС для работы с ботом.")
            return

        # --- СТАТИСТИКА: ЗАПИСЫВАЕМ НОВОГО ЮЗЕРА В БАЗУ ---
        users_collection.update_one({"_id": message.from_user.id}, {"$set": {"active": True}}, upsert=True)
        # -------------------------------------------------

        # --- ЖЕСТКИЙ ФЕЙСКОНТРОЛЬ ---
        if is_banned_in_network(message.from_user.id):
            try:
                from config import VIP_PRICE_STARS
                prices = db['settings'].find_one({"_id": "skynet_pricing"})
                current_vip_price = prices.get("vip_price", VIP_PRICE_STARS) if prices else VIP_PRICE_STARS
            except Exception:
                current_vip_price = 250
                
            ban_doc = banned_collection.find_one({"_id": message.from_user.id}) or {}
            reason_text = ban_doc.get("reason", "Нарушение правил сети")
            reason_upper = reason_text.upper()
            
            # 🔥 ЕДИНЫЙ СЛОВАРЬ ПРЕМИУМ-ГРЕХОВ (Как в BEYOND) 🔥
            premium_bot_issues = [
                "БОТ", "VIP", "ВИП", "БТБ", "БВБ", "ТРАНСБОТ", "V БЛОК", 
                "ТЯНУЛ ВРЕМЯ", "T БЛОК", "НЕ ОПЛАТИЛ"
            ]
            heavy_violations = ["КРАСНАЯ ЗОНА", "НАРКОТ", "ЦП", "МЕФ", "СОЛИ", "<18", "НЕСОВЕРШЕННОЛЕТ", "СПОНСОР", "ЭССКОРТ", "ЖЕЛТАЯ ЗОНА"]
            
            is_premium_issue = any(k in reason_upper for k in premium_bot_issues)
            is_heavy_violation = any(k in reason_upper for k in heavy_violations)
            
            markup = types.InlineKeyboardMarkup(row_width=1)
            
            if is_premium_issue and not is_heavy_violation:
                markup.add(types.InlineKeyboardButton(f"💸 Оплатить штраф ({current_vip_price}⭐️)", callback_data=f"sec_chance_buy_{current_vip_price}"))
                text = (
                    "🚫 **Доступ закрыт.**\n"
                    "Вы заблокированы в сети за блокировку одного из наших премиум-ботов или затягивание времени.\n"
                    f"📌 **Причина:** _{reason_text}_\n\n"
                    "Вы можете воспользоваться правом на **«Второй шанс»** — оплатить штраф, после чего процесс получения VIP-статуса начнется заново."
                )
            else:
                markup.add(types.InlineKeyboardButton("🆘 Служба Поддержки", url=f"https://t.me/{_cfg('support_bot')}"))
                text = (
                    "🚫 **ДОСТУП ЗАПРЕЩЕН**\n"
                    "Вы находитесь в глобальном черном списке нашей сети.\n"
                    f"📌 **Причина блокировки:** _{reason_text}_\n\n"
                    "Апелляция и снятие ограничений (в том числе по просроченной верификации) возможны только через Службу Поддержки."
                )

            bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="Markdown")
            return # Выбрасываем из функции, меню не покажется!
        # ----------------------------

        # 1. Ловим реферальную ссылку (t.me/bot?start=ref_12345)
        start_params = message.text.split()
        is_referral = False
        if len(start_params) > 1 and start_params[1].startswith('ref_') and SkynetSettings.get().get("referral_system", True):
            ref_id = int(start_params[1].replace('ref_', ''))
            
            # Проверяем, не переходил ли он уже по ссылке ранее (защита от накрутки)
            existing_ref = pending_refs_collection.find_one({"_id": message.from_user.id})
            
            if ref_id != message.from_user.id and not existing_ref:
                # ПИШЕМ КЛИК В БАЗУ!
                set_pending_ref(message.from_user.id, ref_id)
                update_user_stats(ref_id, clicks_add=1)
                is_referral = True
                try: 
                    bot.send_message(ref_id, "🔔 По вашей ссылке перешел новый человек! Ждем его оплату.") 
                except: 
                    pass
            elif existing_ref:
                # Человек уже кликал ссылку, просто запускаем приветствие без спама
                is_referral = True 

        # 2. Выдаем меню
        bot.send_message(
            message.chat.id,
            f"Привет, {escape_md(message.from_user.first_name)}! Я ElitePoster. 👋\n\n"
            "Здесь ты можешь опубликовать объявление в наших сетях-партнерах, "
            "а также подать заявку в закрытый VIP-клуб.\n\n"
            "Выберите действие в меню ниже:",
            reply_markup=get_main_keyboard(),
            parse_mode="Markdown"
        )

        # 3. Если пришел от друга — сразу запускаем приветствие VIP
        if is_referral:
            send_vip_welcome(bot, message.chat.id, message.from_user.first_name)

    except Exception as e:
        for admin_id in ADMIN_CHAT_IDS:
            try: bot.send_message(admin_id, f"Ошибка в /start: {e}")
            except: pass

def ban_user_everywhere(target_id, reason="Без причины", admin_name="Система", user_link=None, trigger_text=None, origin_chat=None, force=False):
    # 👇 НОВЫЕ ДВЕ СТРОЧКИ 👇
    from config import get_network_data
    chat_ids_mk, chat_ids_parni, chat_ids_ns, chat_ids_rainbow, chat_ids_gayznak, PARNI_CHATS, all_cities, MAIN_CHANNEL_LINK = get_network_data()

    # 👇 ТОТАЛЬНАЯ ЗАЩИТА ЭЛИТЫ (ПРЕДОХРАНИТЕЛЬ ЯДРА) 👇
    if not force:
        user_data = users_collection.find_one({"_id": target_id}) or {}
        
        # --- УЗКАЯ ЗАЩИТА СПОНСОРОВ (ТОЛЬКО ОТ МП) ---
        is_sponsor = user_data.get("custom_tag") == "Спонсор_Одобрен"
        if is_sponsor:
            # Слова-маркеры в причине или самом тексте, которые прощаются Спонсорам
            safe_sponsor_triggers = ["мп", "спонсор", "содержан", "вознагражден", "коммерц", "деньги", "плачу", "щедр"]
            reason_and_text = (str(reason) + " " + str(trigger_text)).lower()
            
            # Если в причине бана или тексте юзера есть слова о коммерции — СПАСАЕМ!
            if any(word in reason_and_text for word in safe_sponsor_triggers):
                try: bot.send_message(STAFF_GROUP_ID, f"💎 **СПОНСОРСКИЙ ИММУНИТЕТ:** Юзер `{target_id}` написал про МП, но он официальный Спонсор! Бан отменен.", parse_mode="Markdown")
                except: pass
                add_radar_log(f"💎 ИММУНИТЕТ МП: {target_id} спасен от бана")
                return 0
        # ---------------------------------------------
        
        # ЗАЩИТА VIP И QUEER (ОТ ВСЕГО) + ИНДУЛЬГЕНЦИЯ (от всего, кроме тяжёлых статей)
        heavy = any(k in str(reason).upper() for k in ["КРАСНАЯ ЗОНА", "ЧЕРНАЯ ЗОНА", "ЧЁРНАЯ ЗОНА", "НАРКОТ", "<18", "НЕСОВЕРШЕННОЛЕТ", "ЦП"])
        if user_data.get("is_vip", False) or user_data.get("is_queer", False) or (is_indulgence(user_data) and not heavy):
            status_name = "🏳️‍🌈 BEYOND" if user_data.get("is_queer") else ("👑 VIP" if user_data.get("is_vip") else "📜 Индульгенция")
            who_tried = admin_name if admin_name else "Система"
            
            alert_text = (
                f"🛡 **СКАЙНЕТ: БЛОКИРОВКА БАНА!** 🛡\n\n"
                f"Попытка забанить пользователя `{target_id}`.\n"
                f"**Инициатор:** {who_tried}\n"
                f"**Причина:** {reason}\n\n"
                f"❌ Действие отменено, так как это действующий {status_name}!\n\n"
                f"👉 *Если вы действительно хотите уничтожить этого клиента, сначала снимите с него VIP-статус в Веб-Панели, а затем баньте.*"
            )
            try: bot.send_message(STAFF_GROUP_ID, alert_text, parse_mode="Markdown")
            except: pass
            
            add_radar_log(f"🛡 ЗАЩИТА ОТ БАНА: {target_id} спасен от {who_tried}")
            return 0 # Прерываем функцию, 0 чатов забанено
    # 👆 ================================================ 👆

    # === СБРАСЫВАЕМ VIP-СТАТУС И ТЕГИ ПРИ БАНЕ ===
    users_collection.update_one({"_id": target_id}, {"$set": {"is_vip": False}, "$unset": {"custom_tag": ""}})
    # ============================================

    banned_collection.update_one({"_id": target_id}, {"$set": {"reason": reason}}, upsert=True)
   
    chats_to_ban = {VIP_CHAT_ID: "VIP Клуб"}
    for city, cid in chat_ids_parni.items(): chats_to_ban[cid] = f"ПАРНИ 18+ | {city}"
    for city, cid in chat_ids_mk.items(): chats_to_ban[cid] = f"МК | {city}"
    for city, cid in chat_ids_ns.items(): chats_to_ban[cid] = f"НС | {city}"
    for city, cid in chat_ids_rainbow.items(): chats_to_ban[cid] = f"Радуга | {city}"
    for city, cid in chat_ids_gayznak.items(): chats_to_ban[cid] = f"Гей Знакомства | {city}"
                
    banned_in = []
    for cid, name in chats_to_ban.items():
        try:
            # === СТИРАЕМ ЮЗЕРА ИЗ РЕАЛЬНОСТИ (Удаляем все его сообщения) ===
            bot.ban_chat_member(cid, target_id, revoke_messages=True)
            
            # 🔥 Выжигаем все его реакции (до 10 000 шт) по новому API Телеграма
            try: bot.delete_all_message_reactions(chat_id=cid, user_id=target_id)
            except: pass
            # ===============================================================
            banned_in.append(f"🔸 {name}")
        except: pass
            
    who_is_it = user_link if user_link else f"[{target_id}](tg://user?id={target_id})"
    
    # БАЗОВЫЙ ТЕКСТ ОТЧЕТА
    base_text = (
        f"🚫 **#BAN**\n"
        f"• **Кто наказал:** {admin_name}\n"
        f"• **Кому:** {who_is_it} (ID: `{target_id}`)\n"
        f"• **Причина:** {reason}\n"
    )
    # === ДОБАВЛЯЕМ ИНФОРМАЦИЮ О ЧАТЕ ===
    if origin_chat:
        base_text += f"• **Где попался:** {origin_chat}\n"
    
    if trigger_text and trigger_text != "Без текста (медиа)":
        base_text += f"• **Улика:** _{escape_md(trigger_text[:300])}_\n"
        # Запоминаем для Радара (если текст длиннее 30 символов)
        if len(trigger_text) > 30:
            clean_for_radar = re.sub(r'\s+', '', trigger_text.lower())
            db['blacklisted_texts'].insert_one({
                "uid": target_id,
                "clean_text": clean_for_radar,
                "timestamp": time.time()
            })
        
    # ДВА РАЗНЫХ СООБЩЕНИЯ ДЛЯ РАЗНЫХ ЧАТОВ
    journal_text = base_text + f"• **Группы:** ({len(banned_in)} шт.)\n" + "\n".join(banned_in)
    staff_text = base_text + f"• **Забанен в группах:** {len(banned_in)} шт."
    
    # Отправляем простыню в журнал
    try: bot.send_message(JOURNAL_CHAT_ID, journal_text, parse_mode="Markdown")
    except: pass
    
    # Отправляем короткую сводку в рабочий чат
    try: bot.send_message(STAFF_GROUP_ID, staff_text, parse_mode="Markdown")
    except: pass
    
    # === ПИШЕМ ИСТОРИЮ В АРХИВ ДЛЯ СЕКРЕТАРЯ ===
    now_str = datetime.now(pytz.timezone('Asia/Yekaterinburg')).strftime("%d.%m.%Y %H:%M")
    archive_collection.update_one(
        {"target": str(target_id)}, 
        {"$push": {"history": {
            "date": now_str,
            "action": "Глобальный БАН (Скайнет)",  # раньше бан записывался в досье как «МУТ»
            "reason": reason,
            # 🔥 И СЮДА ДОБАВЛЯЕМ УЛИКУ 🔥
            "evidence_summary": trigger_text if trigger_text and trigger_text != "Без текста (медиа)" else "Отсутствует"
        }}},
        upsert=True
    )
    # ===========================================

    # 📡 ОТПРАВЛЯЕМ СИГНАЛ В WEB-РАДАР (раньше стоял после return и не выполнялся)
    try: add_radar_log(f"💥 БАН ({admin_name}): {target_id} | {reason}")
    except Exception: pass

    return len(banned_in)

# ==================== 📩 СИСТЕМА ТИКЕТОВ (САППОРТ) ====================
@bot.message_handler(func=lambda message: message.text == "💬 Написать в Поддержку" and message.chat.type == "private")
def support_request_handler(message):
    user_id = message.from_user.id
    user_data = users_collection.find_one({"_id": user_id}) or {}
    
    is_vip = user_data.get("is_vip", False)
    is_queer = user_data.get("is_queer", False)

    # 🔥 АКТИВНАЯ ПРОВЕРКА ДЛЯ ТЕХ, КТО КУПИЛ В ДРУГОМ БОТЕ (BEYOND) 🔥
    if not is_vip or not is_queer:
        try:
            m_vip = bot.get_chat_member(VIP_CHAT_ID, user_id)
            if m_vip.status in ['member', 'administrator', 'creator'] or (m_vip.status == 'restricted' and getattr(m_vip, 'is_member', False)):
                is_vip = True
                users_collection.update_one({"_id": user_id}, {"$set": {"is_vip": True}}, upsert=True)
        except: pass

        try:
            m_beyond = bot.get_chat_member(BEYOND_CHAT_ID, user_id)
            if m_beyond.status in ['member', 'administrator', 'creator'] or (m_beyond.status == 'restricted' and getattr(m_beyond, 'is_member', False)):
                is_queer = True
                users_collection.update_one({"_id": user_id}, {"$set": {"is_queer": True}}, upsert=True)
        except: pass

        # Если после проверки оказалось, что он элита, но в нашей базе этого не было — АВТОМАТИЧЕСКИ АМНИСТИРУЕМ!
        if (is_vip or is_queer) and (not user_data.get("is_vip") and not user_data.get("is_queer")):
            bot.send_message(message.chat.id, "🔄 Вижу, что вы приобрели премиум-доступ! Синхронизирую базы данных и снимаю все ограничения (это займет пару секунд)...")
            unban_user_everywhere(user_id)
            unmute_user_everywhere(user_id)
            bot.send_message(message.chat.id, "✅ Все муты и баны успешно сняты! Можете возвращаться к общению в группах.")

    # Если юзер не VIP и не QUEER — отказываем и отправляем в FAQ
    if not is_vip and not is_queer:
        bot.send_message(
            message.chat.id, 
            "⚠️ **Служба поддержки недоступна**\n\n"
            "Поддержка в этом боте доступна только участникам закрытых VIP и BEYOND чатов.\n\n"
            "По общим вопросам, проблемам с верификацией или разблокировке обращайтесь в нашего сервисного бота: @FAQMKBOT",
            parse_mode="Markdown"
        )
        return # ⛔️ Важно! Прерываем выполнение, чтобы капкан не включился

    # 👇 НОВЫЙ ЖУЧОК СКАЙНЕТА 👇
    users_collection.update_one({"_id": user_id}, {"$set": {"intent_support": True}}, upsert=True)
    # 👆 ==================== 👆

    # 1. Просим юзера написать проблему (он прошел проверку)
    msg = bot.send_message(
        message.chat.id, 
        "📝 **VIP Служба поддержки**\n\nНапишите ваш вопрос, жалобу или предложение *одним сообщением* ниже. Мы ответим вам прямо в этом чате.", 
        parse_mode="Markdown"
    )
    # 2. Включаем "капкан": ловим только следующее сообщение от этого юзера!
    bot.register_next_step_handler(msg, process_support_msg)

def process_support_msg(message):
    # 3. Защита от дурака: если юзер передумал и нажал другую кнопку меню
    if message.text in ["💬 Написать в Поддержку", "/start", "Создать новое объявление", "👑 Вступить в VIP-чат"]:
        bot.send_message(message.chat.id, "Отмена отправки сообщения в поддержку. Выберите действие в меню 👇", reply_markup=get_main_keyboard())
        return
        
    text = message.text or message.caption or "[Медиафайл / Без текста]"
    
    # 4. Сохраняем вопрос юзера в новую базу данных (Ящик Входящих)
    db['support_tickets'].insert_one({
        "uid": message.from_user.id,
        "name": message.from_user.first_name,
        "username": message.from_user.username,
        "text": text,
        "timestamp": time.time(),
        "is_read": False,    # Метка для вебки: прочитано админом или нет
        "is_answered": False # Метка для вебки: ответили или нет
    })
    
    # 5. Сигналим на твой Радар
    add_radar_log(f"📩 НОВЫЙ ТИКЕТ: Вопрос от {message.from_user.id}")
    
    # 6. Успокаиваем юзера
    bot.send_message(message.chat.id, "✅ Ваше сообщение успешно доставлено Администрации! Ожидайте ответа, мы напишем вам сюда.")
# ======================================================================

def background_corpse_removal(dead_uid):
    """Медленно и аккуратно выносит труп из всех чатов сети, чтобы не словить Flood Wait"""
    # 👇 НОВЫЕ ДВЕ СТРОЧКИ 👇
    from config import get_network_data
    chat_ids_mk, chat_ids_parni, chat_ids_ns, chat_ids_rainbow, chat_ids_gayznak, PARNI_CHATS, all_cities, MAIN_CHANNEL_LINK = get_network_data()

    # Собираем все чаты Империи в один список
    all_empire_chats = []
    if isinstance(PARNI_CHATS, list): all_empire_chats.extend(PARNI_CHATS)
    if isinstance(chat_ids_mk, dict): all_empire_chats.extend(chat_ids_mk.values())
    if isinstance(chat_ids_parni, dict): all_empire_chats.extend(chat_ids_parni.values())
    all_empire_chats.extend([VIP_CHAT_ID, BEYOND_CHAT_ID])
    
    # Убираем дубликаты
    all_empire_chats = list(set(all_empire_chats))
    
    for chat in all_empire_chats:
        try:
            # ban + revoke_messages=True: Вышвыриваем и сжигаем ВСЕ его сообщения в этом чате! 🔥
            bot.ban_chat_member(chat, dead_uid, revoke_messages=True)
            # unban: Сразу снимаем системный блок, чтобы не засорять черный список группы
            bot.unban_chat_member(chat, dead_uid)
        except: pass
        time.sleep(0.3)  # Пауза, чтобы Телеграм не счел это спамом

def unmute_user_everywhere(target_id):
    # 👇 НОВЫЕ ДВЕ СТРОЧКИ 👇
    from config import get_network_data
    chat_ids_mk, chat_ids_parni, chat_ids_ns, chat_ids_rainbow, chat_ids_gayznak, PARNI_CHATS, all_cities, MAIN_CHANNEL_LINK = get_network_data()
    """Снимает мут с пользователя абсолютно во всех чатах сети"""
    # 1. Собираем базовые чаты (для всех)
    all_chats = []
    all_chats.extend(chat_ids_parni.values())
    all_chats.extend(chat_ids_mk.values())
    all_chats.extend(chat_ids_ns.values())
    all_chats.extend(chat_ids_rainbow.values())
    all_chats.extend(chat_ids_gayznak.values())
    
    # --- УМНЫЙ РАЗМУТ (ТОЛЬКО ДЛЯ СВОИХ) ---
    user_data = users_collection.find_one({"_id": target_id}) or {}
    
    # Добавляем VIP-чат только если юзер реально VIP
    if user_data.get("is_vip", False):
        all_chats.append(VIP_CHAT_ID)
        
    # Добавляем BEYOND-чат только если юзер реально QUEER
    if user_data.get("is_queer", False):
        all_chats.append(BEYOND_CHAT_ID)
    # ---------------------------------------
    
    users_collection.update_one({"_id": target_id}, {"$unset": {"net_mute_until": "", "net_mute_reason": ""}})
    unmuted_count = 0
    for cid in all_chats:
        try:
            bot.restrict_chat_member(
                cid, target_id,
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True
            )
            unmuted_count += 1
        except:
            pass
        time.sleep(0.1) # ⏳ СПАСАЕМ ОТ ЛИМИТОВ ТЕЛЕГРАМА
            
    # === ПИШЕМ ИСТОРИЮ В АРХИВ ДЛЯ СЕКРЕТАРЯ ===
    now_str = datetime.now(pytz.timezone('Asia/Yekaterinburg')).strftime("%d.%m.%Y %H:%M")
    archive_collection.update_one(
        {"target": str(target_id)}, 
        {"$push": {"history": {"date": now_str, "action": "Глобальный РАЗМУТ (Скайнет)", "reason": "Амнистия / Снятие ограничений"}}},
        upsert=True
    )
    # ===========================================
            
    return unmuted_count

def unmute_in_parni_only(target_id):
    # 👇 НОВЫЕ ДВЕ СТРОЧКИ 👇
    from config import get_network_data
    chat_ids_mk, chat_ids_parni, chat_ids_ns, chat_ids_rainbow, chat_ids_gayznak, PARNI_CHATS, all_cities, MAIN_CHANNEL_LINK = get_network_data()

    """Снимает мут ТОЛЬКО в чатах сети ПАРНИ 18+"""
    success_count = 0
    for cid in PARNI_CHATS:
        try:
            bot.restrict_chat_member(
                cid, target_id,
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True
            )
            success_count += 1
        except:
            pass
    return success_count

def unban_user_everywhere(target_id):
    # 👇 НОВЫЕ ДВЕ СТРОЧКИ 👇
    from config import get_network_data
    chat_ids_mk, chat_ids_parni, chat_ids_ns, chat_ids_rainbow, chat_ids_gayznak, PARNI_CHATS, all_cities, MAIN_CHANNEL_LINK = get_network_data()

    success_count = 0
    error_count = 0
    """Снимает глобальный бан с пользователя во всех чатах сети"""
    # 1. Удаляем метку бана из памяти Скайнета
    banned_collection.delete_one({"_id": target_id})
    
    # 2. Собираем все чаты
    all_chats = [VIP_CHAT_ID, BEYOND_CHAT_ID]
    all_chats.extend(chat_ids_parni.values())
    all_chats.extend(chat_ids_mk.values())
    all_chats.extend(chat_ids_ns.values())
    all_chats.extend(chat_ids_rainbow.values())
    all_chats.extend(chat_ids_gayznak.values())
    
    unbanned_count = 0
    for cid in all_chats:
        try:
            # only_if_banned=True - важно, чтобы бот просто вычеркнул из ЧС, а не кикнул случайно
            bot.unban_chat_member(cid, target_id, only_if_banned=True)
            unbanned_count += 1
        except:
            pass
        time.sleep(0.1) # ⏳ СПАСАЕМ ОТ ЛИМИТОВ ТЕЛЕГРАМА
            
    # === ПИШЕМ ИСТОРИЮ В АРХИВ ДЛЯ СЕКРЕТАРЯ ===
    now_str = datetime.now(pytz.timezone('Asia/Yekaterinburg')).strftime("%d.%m.%Y %H:%M")
    archive_collection.update_one(
        {"target": str(target_id)}, 
        {"$push": {"history": {"date": now_str, "action": "Глобальный РАЗБАН (Скайнет)", "reason": "Амнистия / Снятие ограничений"}}},
        upsert=True
    )
    # ===========================================
            
    return unbanned_count

# --- ФУНКЦИЯ: ГЛОБАЛЬНЫЙ МУТ (ДЛЯ РЕКЛАМЩИКОВ И НАРУШИТЕЛЕЙ) ---
def mute_user_everywhere(target_id, reason="Без причины", admin_name="Система", user_link=None, trigger_text=None, mute_time=0, origin_chat=None, ignore_shield=False):
    # 👇 НОВЫЕ ДВЕ СТРОЧКИ 👇
    from config import get_network_data
    chat_ids_mk, chat_ids_parni, chat_ids_ns, chat_ids_rainbow, chat_ids_gayznak, PARNI_CHATS, all_cities, MAIN_CHANNEL_LINK = get_network_data()

    # 👇 МАГИЯ ЩИТА ИММУНИТЕТА (ЗАЩИТА ОТ АВТО-МУТОВ) 👇
    user_data = users_collection.find_one({"_id": target_id}) or {}
    
    # --- УЗКАЯ ЗАЩИТА СПОНСОРОВ (ТОЛЬКО ОТ МП) ---
    is_sponsor = user_data.get("custom_tag") == "Спонсор_Одобрен"
    if is_sponsor:
        safe_sponsor_triggers = ["мп", "спонсор", "содержан", "вознагражден", "коммерц", "деньги", "плачу", "щедр"]
        reason_and_text = (str(reason) + " " + str(trigger_text)).lower()
        
        if any(word in reason_and_text for word in safe_sponsor_triggers):
            try: bot.send_message(STAFF_GROUP_ID, f"💎 **СПОНСОРСКИЙ ИММУНИТЕТ:** Юзер `{target_id}` написал про МП, но он официальный Спонсор! Мут отменен.", parse_mode="Markdown")
            except: pass
            add_radar_log(f"💎 ИММУНИТЕТ МП: {target_id} спасен от мута")
            return 0
    # ---------------------------------------------
    
    # Достаем данные юзера из базы для проверки одноразовых щитов
    # Атомарно: щит списывается, только если он есть. Раньше два мута подряд
    # тратили один щит дважды и уводили баланс щитов в минус.
    shield_used = None
    if not ignore_shield:
        shield_used = db['paid_users'].find_one_and_update(
            {"uid": target_id, "immunity": {"$gt": 0}},
            {"$inc": {"immunity": -1}}
        )

    if shield_used:
        try:
            from core.janitor import log_points
            log_points(target_id, "immunity", -1, reason="mute_shield")
        except Exception: pass

        # 2. Пишем в веб-радар
        add_radar_log(f"🛡 ЩИТ СРАБОТАЛ: {target_id} спасен от мута ({reason})")
        
        # 3. Пишем юзеру в ЛС, что он чудом спасся
        try:
            bot.send_message(
                target_id, 
                f"⛔️ **Скайнет зафиксировал нарушение!**\nПричина: {reason}\n\n"
                f"Но ваш **🛡 Щит Иммунитета поглотил удар!** Вы избежали глобального мута.\n"
                f"_Щит разрушен. Будьте осторожны в следующий раз!_",
                parse_mode="Markdown"
            )
        except: pass
        
        # 4. Уведомляем админов
        try:
            bot.send_message(STAFF_GROUP_ID, f"🛡 **БРОНЯ ПРОБИТА:** Скайнет пытался выдать мут `{target_id}` за ({reason}), но **Щит Иммунитета** поглотил удар!", parse_mode="Markdown")
        except: pass
        
        # 5. ПРЕРЫВАЕМ ФУНКЦИЮ! Мут не выдается (возвращаем 0).
        return 0
    # 👆 ======================================================== 👆

    # --- СОХРАНЯЕМ ПРИЧИНУ ДЛЯ АМНИСТИИ В ПАРНЯХ ---
    # net_mute_until: общий для всех ботов флаг сетевого мута (0 = бессрочно). Раньше Секретарь видел
    # только муты, заказанные через skynet_tasks, а муты самого Скайнета (зоны, 1 Мая, копипаст) — нет.
    users_collection.update_one({"_id": target_id}, {"$set": {"last_mute_reason": reason,
                                                              "net_mute_until": int(mute_time or 0),
                                                              "net_mute_reason": reason}}, upsert=True)
    chats_to_mute = {}
    for city, cid in chat_ids_parni.items(): chats_to_mute[cid] = f"ПАРНИ 18+ | {city}"
    for city, cid in chat_ids_mk.items(): chats_to_mute[cid] = f"МК | {city}"
    for city, cid in chat_ids_ns.items(): chats_to_mute[cid] = f"НС | {city}"
    for city, cid in chat_ids_rainbow.items(): chats_to_mute[cid] = f"Радуга | {city}"
    for city, cid in chat_ids_gayznak.items(): chats_to_mute[cid] = f"Гей Знакомства | {city}"
                
    muted_in = []
    for cid, name in chats_to_mute.items():
        try:
            bot.restrict_chat_member(cid, target_id, until_date=mute_time, can_send_messages=False)
            muted_in.append(f"🔸 {name}")
        except: pass
            
    who_is_it = user_link if user_link else f"[{target_id}](tg://user?id={target_id})"
    
    # БАЗОВЫЙ ТЕКСТ ОТЧЕТА
    base_text = (
        f"🤐 **#MUTE (Глобальный)**\n"
        f"• **Кто наказал:** {admin_name}\n"
        f"• **Кому:** {who_is_it} (ID: `{target_id}`)\n"
        f"• **Причина:** {reason}\n"
    )
    # === ДОБАВЛЯЕМ ИНФОРМАЦИЮ О ЧАТЕ ===
    if origin_chat:
        base_text += f"• **Где попался:** {origin_chat}\n"
        
    if trigger_text and trigger_text != "Без текста (медиа)":
        base_text += f"• **Улика:** _{escape_md(trigger_text[:300])}_\n"
        # Запоминаем для Радара (если текст длиннее 30 символов)
        if len(trigger_text) > 30:
            clean_for_radar = re.sub(r'\s+', '', trigger_text.lower())
            db['blacklisted_texts'].insert_one({
                "uid": target_id,
                "clean_text": clean_for_radar,
                "timestamp": time.time()
            })
        
    # ДВА РАЗНЫХ СООБЩЕНИЯ ДЛЯ РАЗНЫХ ЧАТОВ
    journal_text = base_text + f"• **Группы:** ({len(muted_in)} шт.)\n" + "\n".join(muted_in)
    staff_text = base_text + f"• **Замучен в группах:** {len(muted_in)} шт."
    
    # Отправляем простыню в журнал
    try: bot.send_message(JOURNAL_CHAT_ID, journal_text, parse_mode="Markdown")
    except: pass
    
    # Отправляем короткую сводку в рабочий чат
    try: bot.send_message(STAFF_GROUP_ID, staff_text, parse_mode="Markdown")
    except: pass
    
    # === ПИШЕМ ИСТОРИЮ В АРХИВ ДЛЯ СЕКРЕТАРЯ ===
    now_str = datetime.now(pytz.timezone('Asia/Yekaterinburg')).strftime("%d.%m.%Y %H:%M")
    archive_collection.update_one(
        {"target": str(target_id)}, 
        {"$push": {"history": {
            "date": now_str, 
            "action": "Глобальный МУТ (Скайнет)", 
            "reason": reason,
            # 🔥 ВОТ ОНА! СОХРАНЯЕМ УЛИКУ В БАЗУ ДЛЯ ИИ 🔥
            "evidence_summary": trigger_text if trigger_text and trigger_text != "Без текста (медиа)" else "Отсутствует"
        }}},
        upsert=True
    )
    # ===========================================
    
    # 📡 ОТПРАВЛЯЕМ СИГНАЛ В WEB-РАДАР
    add_radar_log(f"🤐 МУТ ({admin_name}): {target_id} | {reason}")
    
    return len(muted_in)

def is_subscribed(user_id):
    try:
        member = bot.get_chat_member(MAIN_CHANNEL_ID, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        print(f"Ошибка при проверке подписки для {user_id}: {e}")
        return False

# === АКТИВАЦИЯ ВНЕШНИХ ХЭНДЛЕРОВ ===
register_admin_handlers(
    bot, 
    ban_user_everywhere, 
    mute_user_everywhere, 
    unban_user_everywhere, 
    unmute_user_everywhere, 
    unmute_in_parni_only
)

register_vip_handlers(
    bot,
    pending_verification_users,
    active_vip_requests,
    safe_from_autoban,
    ban_user_everywhere,
    unmute_user_everywhere,
    unban_user_everywhere
)

register_post_handlers(bot, is_banned_in_network, get_main_keyboard, is_real_vip)
register_proxy_handlers(bot, ban_user_everywhere)
ENTRY_HOOKS = {}  # заполняется ниже, когда объявлен catch_illegal_entry
register_skynet_handlers(bot, ban_user_everywhere, mute_user_everywhere, safe_set_tag, add_radar_log, is_subscribed, entry_hooks=ENTRY_HOOKS)

from web.support import register_support_routes
from web.finance import register_finance_routes
from web.routes import register_main_routes
from web.ads import register_ads_routes

# --- МГНОВЕННЫЙ РАДАР БЛОКИРОВОК (Ловит ТОЛЬКО беглецов из VIP-воронки) ---
@bot.my_chat_member_handler()
def catch_bot_block(message):
    # 🎛️ ПРОВЕРЯЕМ ТУМБЛЕР С САЙТА
    settings = SkynetSettings.get()
    if not settings.get("autoban_on_block", True):
        return  # Если выключен — игнорируем блокировки бота, никого не баним!

    if message.chat.type == "private":
        new_status = message.new_chat_member.status
        if new_status == "kicked": 
            # В приватных чатах chat.id — это 100% ID самого пользователя
            user_id = message.chat.id
            
            # =================================================================
            # 🛡️ ФАЗА 1: ЖЕЛЕЗОБЕТОННАЯ ЗАЩИТА СВОИХ (VIP & BEYOND ИММУНИТЕТ)
            # =================================================================
            
            # А) Сначала проверяем по нашей базе данных (самый надежный способ)
            user_data = users_collection.find_one({"_id": user_id}) or {}
            if user_data.get("is_vip", False) or user_data.get("is_queer", False) or is_indulgence(user_data):
                return  # Своих не трогаем, пусть блокируют бота сколько влезет
                
            # Б) На всякий случай проверяем живое присутствие в VIP-чате
            try:
                m_vip = bot.get_chat_member(VIP_CHAT_ID, user_id)
                if m_vip.status in ["member", "administrator", "creator"]:
                    return
                if m_vip.status == 'restricted' and getattr(m_vip, 'is_member', False):
                    return
            except: pass
            
            # В) И живое присутствие в чате BEYOND (QUEER)
            try:
                m_beyond = bot.get_chat_member(BEYOND_CHAT_ID, user_id)
                if m_beyond.status in ["member", "administrator", "creator"]:
                    return
                if m_beyond.status == 'restricted' and getattr(m_beyond, 'is_member', False):
                    return
            except: pass
            
            # =================================================================

            # 2. ПРОВЕРКА: ИММУНИТЕТ (Официальный отказ)
            if user_id in safe_from_autoban:
                try: safe_from_autoban.remove(user_id)
                except: pass
                return
                
            # 3. ПРОВЕРКА НА БЕГЛЕЦА: Он был в воронке отбора?
            is_in_funnel = db['vip_funnel'].find_one({"_id": user_id})
            is_pending = pending_verification_users.get(user_id, False)
            
            if is_in_funnel or is_pending:
                # Ага! Нажал кнопку верификации и сбежал в блок! ЛИКВИДИРОВАТЬ!
                try:
                    bot.send_message(STAFF_GROUP_ID, f"⚡️ **РАДАР СРАБОТАЛ** ⚡️\nТрус `{user_id}` попытался сбежать с верификации и заблокировал бота! Запускаю ликвидацию...")
                except: pass
                
                ban_user_everywhere(user_id, reason="Сбежал с верификации и заблокировал бота", admin_name="Auto-Radar ⚡️")
                
                # Зачищаем следы в воронке
                db['vip_funnel'].delete_one({"_id": user_id})
                pending_verification_users[user_id] = False
            else:
                # Это обычный обыватель. Просто заблокировал бота. Пусть живет мирно 🕊
                pass

@bot.message_handler(commands=['удали'])
def test_kill_cookie(message):
    # Раньше любой участник группы мог удалить чужое сообщение руками бота
    if not is_staff_admin(bot, message.from_user.id):
        return
    if message.reply_to_message:
        target_id = message.reply_to_message.message_id
        try:
            bot.delete_message(message.chat.id, target_id)
            bot.reply_to(message, f"✅ Скайнет смог! Я удалил ID {target_id} напрямую!")
        except Exception as e:
            bot.reply_to(message, f"❌ Скайнет смотрит в упор, но Телеграм не дает: {e}")

# ==================== ПЕРЕХВАТЧИК "МЕРТВЫХ ДУШ" (Защита от старых заявок + Амнистия + Теги) ====================
# ВАЖНО: в telebot срабатывает только ПЕРВЫЙ подходящий хэндлер. Фейс-контроль входа в
# handlers/skynet.py регистрировался раньше, поэтому этот перехватчик не выполнялся никогда:
# забаненных, которых админ одобрил вручную, никто не выкидывал, теги не восстанавливались.
# Теперь фейс-контроль вызывает эту функцию сам (через ENTRY_HOOKS).
def catch_illegal_entry(message):
    # 👇 НОВЫЕ ДВЕ СТРОЧКИ 👇
    from config import get_network_data
    chat_ids_mk, chat_ids_parni, chat_ids_ns, chat_ids_rainbow, chat_ids_gayznak, PARNI_CHATS, all_cities, MAIN_CHANNEL_LINK = get_network_data()

    for new_user in message.new_chat_members:
        user_id = new_user.id
        chat_id = message.chat.id
        
        # 1. Проверяем, есть ли он в черном списке Скайнета
        banned_info = banned_collection.find_one({"_id": user_id})
        
        if banned_info:
            # 2. Мгновенно баним обратно
            try:
                bot.ban_chat_member(chat_id, user_id, revoke_messages=True)
            except:
                pass
            
            # 3. Отчет в Staff-чат
            user_link = f"[{escape_md(new_user.first_name)}](tg://user?id={user_id})"
            chat_title = escape_md(message.chat.title) if message.chat.title else str(chat_id)
            
            report = (
                f"🚨 **ПЕРЕХВАТ ПРОНИКНОВЕНИЯ!**\n"
                f"Кто-то одобрил старую заявку забаненного юзера {user_link} (`{user_id}`).\n"
                f"📍 **Где:** {chat_title}\n"
                f"🤖 Скайнет мгновенно вернул его в бан! 🛡"
            )
            try: bot.send_message(STAFF_GROUP_ID, report, parse_mode="Markdown")
            except: pass
            continue # Если юзер в бане, дальше не идем (раньше return пропускал остальных вошедших)

        # 👇 НОВЫЙ БЛОК: АВТО-ВОССТАНОВЛЕНИЕ ТЕГОВ 👇
        user_data = users_collection.find_one({"_id": user_id}) or {}
        saved_tag = user_data.get("custom_tag")
        
        if saved_tag:
            try:
                # Надеваем бейджик обратно при входе!
                safe_set_tag(chat_id, user_id, saved_tag)
            except Exception as e:
                print(f"Не удалось восстановить тег '{saved_tag}' для {user_id}: {e}")
        # 👆 ========================================== 👆

        # --- 🕊️ ЛОКАЛЬНАЯ АМНИСТИЯ ПАРНИ (Для тех, кто вошел сам или одобрен вручную) ---
        if chat_id in PARNI_CHATS and SkynetSettings.get().get("parni_autounmute", True):
            last_reason = user_data.get("last_mute_reason", "")
            
            # Если мут был за параметры (1 Мая)
            if any(word in last_reason for word in ["1 Мая", "параметр"]):
                # РАЗМУЧИВАЕМ ТОЛЬКО В СЕТИ ПАРНИ
                count = unmute_in_parni_only(user_id)
                
                # Очищаем причину, чтобы не срабатывало повторно
                users_collection.update_one({"_id": user_id}, {"$unset": {"last_mute_reason": ""}})
                
                # Отчет админам
                try: 
                    bot.send_message(STAFF_GROUP_ID, f"🕊️ **АМНИСТИЯ (Вход):** Юзер `{user_id}` вошел в сеть ПАРНИ 18+ и был автоматически размучен ({count} чатов).")
                except: pass
# ===========================================================================================

ENTRY_HOOKS["entry"] = catch_illegal_entry

@bot.callback_query_handler(func=lambda call: call.data.startswith("radar_ban_"))
def radar_confirm_ban(call):
    # Кнопку можно подделать (callback_data) — без проверки любой мог забанить кого угодно
    if not is_staff_admin(bot, call.from_user.id):
        return deny_callback(bot, call)
    try:
        target_id = int(call.data.split("_")[2])
    except (IndexError, ValueError):
        return
    admin_info = get_user_name(call.from_user)
    
    # Меняем текст сообщения, чтобы другие админы не нажали повторно
    try: bot.edit_message_text(call.message.text + f"\n\n✅ **ЗАБАНЕН АДМИНОМ:** {admin_info}", call.message.chat.id, call.message.message_id)
    except: pass
    
    # Казним!
    bot.send_message(call.message.chat.id, "🚀 Запускаю массовый бан твинка...")
    count = ban_user_everywhere(target_id, reason="Радар Твинков (Клон забаненной анкеты)", admin_name=admin_info)
    bot.send_message(call.message.chat.id, f"✅ Твинк уничтожен в {count} чатах.")

@bot.callback_query_handler(func=lambda call: call.data.startswith("aire_"))
def ai_review_decision(call):
    """Решение админа по делу, которое ИИ не смог проверить (core/ai_review.py)."""
    if not is_staff_admin(bot, call.from_user.id):
        return deny_callback(bot, call)
    try:
        _, code, rid = call.data.split("_", 2)
    except ValueError:
        return
    decision = {"b": "ban", "m": "mute", "x": "dismissed"}.get(code)
    if not decision:
        return
    from core.ai_review import claim_review
    admin_info = get_user_name(call.from_user)
    case = claim_review(rid, decision, admin_info)
    if not case:
        try: bot.answer_callback_query(call.id, "Это дело уже решено.", show_alert=True)
        except Exception: pass
        try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception: pass
        return
    try: bot.answer_callback_query(call.id)
    except Exception: pass
    uid, reason = int(case["uid"]), case.get("reason") or "Решение администратора"
    verdict = {"ban": f"🔨 ЗАБАНЕН: {admin_info}", "mute": f"🔇 ЗАМУЧЕН: {admin_info}", "dismissed": f"✅ НЕ НАРУШЕНИЕ: {admin_info}"}[decision]
    # Без parse_mode: раньше в тексте оставались сырые звёздочки **
    try: bot.edit_message_text(f"{call.message.text}\n\n{verdict}", call.message.chat.id, call.message.message_id)
    except Exception: pass
    try:
        if decision == "ban":
            ban_user_everywhere(uid, reason=reason, admin_name=admin_info, user_link=case.get("user_link"),
                                trigger_text=case.get("trigger_text"), origin_chat=escape_md(case.get("origin_chat", "")))
        elif decision == "mute":
            dur = int(case.get("duration") or 0)
            mute_user_everywhere(uid, reason=reason, admin_name=admin_info, user_link=case.get("user_link"),
                                 trigger_text=case.get("trigger_text"), mute_time=int(time.time() + dur) if dur else 0,
                                 origin_chat=escape_md(case.get("origin_chat", "")))
        add_radar_log(f"🤖→👤 Ручное решение ({reason}) для {uid}: {verdict}")
    except Exception as e:
        log_error("Ручное решение по ИИ", e, uid)
        try: bot.send_message(call.message.chat.id, f"⚠️ Не удалось выполнить: {e}")
        except Exception: pass

# ==================== VIP СНАЙПЕР (Фоновая задача) ====================
def vip_funnel_sniper():
    while True:
        try:
            settings = SkynetSettings.get()
            # Замок: при нескольких воркерах gunicorn снайпер работал в каждом и дублировал баны
            if settings.get("vip_sniper", True) and acquire_lease("vip_funnel_sniper", 13 * 3600):
                now = time.time()

                for doc in db['vip_funnel'].find():
                    user_id = doc['_id']

                    # Ход за админами (кружок на проверке) — кандидата не трогаем.
                    # Раньше тех, кого админы не успели проверить, через 10 дней банили «за затягивание».
                    if doc.get("stage") in ("admin_review", "deciding"):
                        continue
                    
                    # 👇 ЗАЩИТА ОТ ДРУЖЕСТВЕННОГО ОГНЯ (ДЛЯ ДЕЙСТВУЮЩИХ ВИПОВ) 👇
                    user_data = users_collection.find_one({"_id": user_id}) or {}
                    if user_data.get("is_vip", False) or user_data.get("is_queer", False):
                        # Юзер УЖЕ вип! Он случайно нажал кнопку верификации.
                        # Просто тихо удаляем его из воронки и не баним.
                        db['vip_funnel'].delete_one({"_id": user_id})
                        continue
                    # 👆 ========================================================= 👆

                    timestamp = doc.get('timestamp', now)
                    reminded = doc.get('reminded', False)
                    
                    # 1. Напоминание через неделю
                    if not reminded and (now - timestamp > _cfg("vip_remind_days") * 86400):
                        reminder_text = (
                            "⚠️ **Системное уведомление!**\n\n"
                            "Вы начали процесс вступления в VIP, но остановились. "
                            "Если вы не завершите верификацию или оплату, ваша заявка будет аннулирована.\n\n"
                            "Ждем ваших действий! ⏱"
                        )
                        try:
                            bot.send_message(user_id, reminder_text, parse_mode="Markdown")
                        except:
                            pass
                        
                        db['vip_funnel'].update_one(
                            {"_id": user_id}, 
                            {"$set": {"reminded": True, "timestamp": now}}
                        )
                    
                    # 2. Бан через 3 дня после напоминания
                    elif reminded and (now - timestamp > _cfg("vip_ban_days") * 86400):
                        ban_user_everywhere(user_id, reason="Не оплатил ВИП, тянул время", admin_name="Скайнет ⏱")
                        db['vip_funnel'].delete_one({"_id": user_id})
                        
        except Exception as e:
            print(f"Ошибка Снайпера: {e}")
            
        time.sleep(43200)

# 👇 УМНАЯ ПРОВЕРКА КОНТЕКСТА ЧЕРЕЗ ИИ 👇
def ai_context_checker(text, zone="black"):
    """True — нарушение, False — оправдан, None — ИИ недоступен (см. core/ai.py)."""
    from core.ai import ai_verdict
    return ai_verdict(text, zone)
# 👆 ===================================== 👆

# ==================== СЛУШАТЕЛЬ СЕКРЕТАРЯ (РАЗБАН ПО КНОПКЕ) ====================
SKYNET_TASK_ACTIONS = ["full_unban", "fine_unban", "auto_heal", "global_unmute", "global_ban", "global_mute"]

def _execute_skynet_task(task):
    if task['action'] in ["full_unban", "fine_unban", "auto_heal"]:
        target_uid = int(task['uid'])
        
        # 1. Снимаем бан и мут везде
        unbanned = unban_user_everywhere(target_uid)
        unmuted = unmute_user_everywhere(target_uid)

        # 2. ВЫДАЕМ ИММУНИТЕТ ИЛИ ПРОСТО ЧИСТИМ ТЕГ
        if task['action'] == "full_unban":
            # 👇 БРОНЯ ОТ СТИРАНИЯ ТЕГОВ 👇
            u_info = users_collection.find_one({"_id": target_uid}) or {} 
            if not u_info.get("custom_tag"):
                users_collection.update_one(
                    {"_id": target_uid}, 
                    {"$set": {"is_verified": True, "custom_tag": "Верифицирован МК"}, "$unset": {"shame_tag": ""}},
                    upsert=True
                )
            else:
                # Если тег уже есть (Элита) - просто подтверждаем вериф без стирания статуса
                users_collection.update_one({"_id": target_uid}, {"$set": {"is_verified": True}, "$unset": {"shame_tag": ""}}, upsert=True)
        
        elif task['action'] == "fine_unban":
            amount = task.get('amount') or task.get('price')
            if not amount:
                last_pay = db['fine_payments'].find_one({"uid": target_uid}, sort=[("timestamp", -1)])
                amount = last_pay.get('amount', 0) if last_pay else 0
            
            if int(amount) == _cfg("fine_tag_free"):
                users_collection.update_one({"_id": target_uid}, {"$set": {"custom_tag": "Свободен"}, "$unset": {"shame_tag": ""}}, upsert=True)
                add_radar_log(f"🎖️ Юзер {target_uid} оплатил {amount}⭐️ и получил тег 'Свободен'")
            elif int(amount) == _cfg("fine_tag_sponsor"):
                users_collection.update_one({"_id": target_uid}, {"$set": {"custom_tag": "Спонсор_Одобрен"}, "$unset": {"shame_tag": ""}}, upsert=True)
                add_radar_log(f"💎 Юзер {target_uid} оплатил {amount}⭐️ и получил тег 'Спонсор_Одобрен'")
            else:
                users_collection.update_one({"_id": target_uid}, {"$unset": {"shame_tag": "", "custom_tag": ""}})
                add_radar_log(f"🧹 Юзер {target_uid} оплатил обычный штраф ({amount}⭐️), теги сброшены")
        
        # 3. ОТЧЕТ В ФЛУДИЛКУ АДМИНАМ!
        if task['action'] == "fine_unban":
            report_text = f"💸 **Скайнет (Автоматика):**\nЮзер `{target_uid}` оплатил штраф!\nОграничения сняты. Глобально разбанен ({unbanned} чатов) и размучен ({unmuted} чатов)."
        elif task['action'] == "auto_heal":
            report_text = f"🛡 **Скайнет (Авто-Исцеление):**\nVIP-юзер `{target_uid}` открыл поддержку. Скайнет профилактически снял с него все возможные ограничения!\nГлобально разбанен ({unbanned} чатов) и размучен ({unmuted} чатов)."
        else:
            report_text = f"✅ **Скайнет (Автоматика):**\nЮзер `{target_uid}` прошел верификацию!\nГлобально разбанен ({unbanned} чатов) и размучен ({unmuted} чатов)."
        
        try: bot.send_message(STAFF_GROUP_ID, report_text, parse_mode="Markdown")
        except: pass
        

    elif task['action'] == "global_unmute":
        unmute_user_everywhere(int(task['uid']))
    
    # 👇 ИСПОЛНЕНИЕ ПРИКАЗОВ ОТ ШПИОНА (С ДВОЙНОЙ ПРОВЕРКОЙ ИИ) 👇
    elif task['action'] in ["global_ban", "global_mute"]:
        trigger_text = task.get('trigger_text', '')
        reason = task.get('reason', 'Шпионаж')
        
        # 🔥 СУДЬЯ СКАЙНЕТ ПРОВЕРЯЕТ УЛИКИ АНДРЮШЕНЬКИ 🔥
        is_guilty = True
        if trigger_text and not task.get('skip_ai'):  # skip_ai: решение уже принял человек в панели
            reason_lower = reason.lower() # Приводим к нижнему регистру для надежности!
            if "черная зона" in reason_lower:
                is_guilty = ai_context_checker(trigger_text, zone="black")
            elif "оранжевая зона" in reason_lower:
                is_guilty = ai_context_checker(trigger_text, zone="orange")
            elif "желтая зона" in reason_lower:
                is_guilty = ai_context_checker(trigger_text, zone="yellow")

        if is_guilty is None:
            # ИИ недоступен: раньше в этом случае наказывали вслепую. Теперь решает человек.
            from core.diag import log_error
            log_error("Приказ Шпиона", f"ИИ недоступен, {task['action']} для {task['uid']} не исполнен", task.get('uid'))
            try:
                from core.ai_review import create_review
                mk = create_review(int(task['uid']), "ban" if task['action'] == 'global_ban' else "mute", reason,
                                   trigger_text=trigger_text, origin_chat=task.get('origin_chat', ''),
                                   duration=int(task.get('duration') or 0), source="Шпион")
                bot.send_message(STAFF_GROUP_ID, f"🤖 ИИ-проверка недоступна. Шпион просит {'бан' if task['action'] == 'global_ban' else 'мут'} для {task['uid']}\nПричина: {reason}\nУлика: {str(trigger_text)[:300]}\nРешите вручную.", reply_markup=mk)
            except Exception as e:
                log_error("Приказ Шпиона: алерт", e, task.get('uid'))
        elif is_guilty:
            # 👇 БЕРЕМ ИМЯ ИЗ ПРИКАЗА (иначе дефолт шпиона) 👇
            task_admin_name = task.get('admin_name', "Андрюшенька (Спецагент Шпион) 🕵️‍♂️")
            
            # ИИ подтвердил вину -> Наказываем!
            if task['action'] == "global_ban":
                ban_user_everywhere(
                    target_id=int(task['uid']), 
                    reason=reason, 
                    admin_name=task_admin_name, 
                    trigger_text=trigger_text, 
                    origin_chat=escape_md(task.get('origin_chat', ''))
                )
            else:
                _dur = int(task.get('duration') or 0)
                mute_user_everywhere(
                    target_id=int(task['uid']),
                    reason=reason,
                    admin_name=task_admin_name,
                    trigger_text=trigger_text,
                    mute_time=int(time.time() + _dur) if _dur else 0,
                    origin_chat=escape_md(task.get('origin_chat', '')),
                    ignore_shield=bool(task.get('ignore_shield'))
                )
        else:
            # 🛡 ИИ ОПРАВДАЛ ЮЗЕРА! Ордер аннулирован.
            print(f"🛡 СКАЙНЕТ ОТМЕНИЛ АРЕСТ! Андрюшенька ошибся. Улика: {trigger_text}")


def skynet_listener():
    """Исполняет приказы других ботов из skynet_tasks.
    Каждую задачу атомарно «забирает» один процесс (status=processing), поэтому при нескольких
    воркерах приказ больше не исполняется дважды. Ошибка в одной задаче больше не валит весь
    цикл и не повторяет ту же задачу (с отчётом в STAFF) каждые 3 секунды."""
    while True:
        try:
            now = time.time()
            # Подбираем задачи, зависшие в обработке (процесс упал посреди исполнения)
            db['skynet_tasks'].update_many(
                {"status": "processing", "claimed_at": {"$lt": now - 600}},
                {"$set": {"status": "pending"}, "$inc": {"attempts": 1}}
            )
            while True:
                task = db['skynet_tasks'].find_one_and_update(
                    {"status": {"$nin": ["done", "processing", "error"]},
                     "action": {"$in": SKYNET_TASK_ACTIONS},
                     "attempts": {"$not": {"$gte": 3}}},
                    {"$set": {"status": "processing", "claimed_at": time.time()}},
                    sort=[("_id", 1)],
                    return_document=ReturnDocument.AFTER
                )
                if not task:
                    break
                try:
                    _execute_skynet_task(task)
                    db['skynet_tasks'].update_one({"_id": task['_id']}, {"$set": {"status": "done", "done_at": time.time()}})
                except Exception as e:
                    logger.error(f"Ошибка задачи Скайнета {task.get('_id')}: {e}")
                    from core.diag import log_error
                    log_error("Приказ Скайнету", f"{task.get('action')}: {e}", task.get('uid'))
                    db['skynet_tasks'].update_one({"_id": task['_id']}, {"$set": {"status": "error", "error": str(e)[:300]}})
                    try: bot.send_message(STAFF_GROUP_ID, f"⚠️ Скайнет не смог выполнить приказ {task.get('action')} для {task.get('uid')}: {str(e)[:200]}")
                    except Exception: pass
        except Exception as e:
            print(f"Ошибка слушателя: {e}")
            
        time.sleep(3) # Проверяем приказы каждые 3 секунды

# === ЗАПУСКАЕМ ФОНОВЫЕ ПРОЦЕССЫ ЗДЕСЬ (КОГДА ПИТОН УЖЕ ЗНАЕТ ВСЕ ФУНКЦИИ) ===
threading.Thread(target=vip_funnel_sniper, daemon=True).start()
threading.Thread(target=skynet_listener, daemon=True).start()
# ============================================================================

# ==================== ДЕМОН CPA-СЕТИ (БУХГАЛТЕР 2.0) ====================
def cpa_tracker_daemon():
    while True:
        try:
            if not acquire_lease("cpa_tracker", 7 * 3600):
                time.sleep(21600)
                continue
            now = time.time()
            # 🔥 НОВЫЙ ХОЛД: 14 дней (1 209 600 секунд) 🔥
            HOLD_TIME = _cfg("cpa_hold_days") * 86400
            
            pending_traffic = list(db['cpa_traffic'].find({"status": "hold", "join_time": {"$lt": now - HOLD_TIME}}))
            
            for record in pending_traffic:
                # Атомарно забираем запись: при нескольких воркерах агент получал кейс дважды
                if not db['cpa_traffic'].find_one_and_update({"_id": record['_id'], "status": "hold"},
                                                             {"$set": {"status": "checking"}}):
                    continue
                if True:
                    new_user_id = record['new_user_id']
                    agent_id = record['agent_id']
                    target_chat_id = record.get('chat_id')
                    
                    is_banned = banned_collection.find_one({"_id": new_user_id})
                    
                    is_physically_present = False
                    check_failed = False
                    if target_chat_id and not is_banned:
                        try:
                            member = bot.get_chat_member(target_chat_id, new_user_id)
                            # Мут (restricted) тоже считается присутствием!
                            if member.status in ['member', 'administrator', 'creator'] or (member.status == 'restricted' and getattr(member, 'is_member', False)):
                                is_physically_present = True
                        except Exception as e:
                            # «not found» = человека в чате нет. Прочие ошибки (таймаут, лимиты) —
                            # не списываем лид в брак, а проверим в следующий проход.
                            if "not found" not in str(e).lower():
                                check_failed = True

                    if check_failed:
                        db['cpa_traffic'].update_one({"_id": record['_id']}, {"$set": {"status": "hold"}})
                        continue

                    if is_banned:
                        db['cpa_traffic'].update_one({"_id": record['_id']}, {"$set": {"status": "fraud_banned"}})
                    elif not is_physically_present:
                        db['cpa_traffic'].update_one({"_id": record['_id']}, {"$set": {"status": "fraud_left"}})
                    else:
                        # 🔥 ТРАФИК ОДОБРЕН! Присваиваем ему метку ТЕКУЩЕГО МЕСЯЦА для конкурса 🔥
                        import datetime
                        current_month = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5))).strftime("%Y-%m")
                        db['cpa_traffic'].update_one({"_id": record['_id']}, {"$set": {"status": "approved", "approved_month": current_month}})
                        
                        paid_collection = db['paid_users']
                        paid_collection.update_one({"uid": agent_id}, {"$inc": {"agent_cases": 1, "cpa_refs": 1}}, upsert=True)
                        
                        agent_data = paid_collection.find_one({"uid": agent_id})
                        total_refs = agent_data.get("cpa_refs", 0)
                        
                        msg_text = f"💼 **CPA-Сеть:** Ваш реферал успешно выжил {_cfg('cpa_hold_days')} дней в группе!\n\nВам начислен **1 Кейс Агента**! Зайдите в Игровой Кабинет (вкладка Финансы), чтобы забрать приз!\n_Всего приведено: {total_refs} чел._"
                        
                        if total_refs > 0 and total_refs % 10 == 0:
                            paid_collection.update_one({"uid": agent_id}, {"$inc": {"agent_cases": 1}})
                            msg_text += f"\n\n🎊 **ЮБИЛЕЙ!** Вы привели {total_refs} человек! Ловите еще **+1 Кейс Агента** сверху! 🎰"
                            
                        try: bot.send_message(agent_id, msg_text, parse_mode="Markdown")
                        except: pass
                        
        except Exception as e:
            logger.error(f"Ошибка CPA Tracker: {e}")
            from core.diag import log_error; log_error("CPA", e)
        
        time.sleep(21600) # Спит 6 часов

# ==================== АВТО-КОНКУРС АГЕНТОВ (КАЖДОЕ 1 ЧИСЛО) ====================
def _claim_contest_month(month_str):
    try:
        db['settings'].insert_one({"_id": f"cpa_contest_resolved_{month_str}", "done": False, "started": time.time()})
        return True
    except DuplicateKeyError:
        return False

def cpa_monthly_daemon():
    while True:
        try:
            import datetime
            # Берем время по Екб (+5)
            now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5)))
            
            # Если сегодня 1-е число месяца
            if now.day == 1:
                # Находим строку ПРОШЛОГО месяца (например, "2026-09" если сейчас октябрь)
                prev_month_date = now.replace(day=1) - datetime.timedelta(days=1)
                prev_month_str = prev_month_date.strftime("%Y-%m")
                
                # 🔥 ПРЕДОХРАНИТЕЛЬ: Конкурс официально стартует с Октября (2026-10) 🔥
                # Если прошлый месяц был сентябрь 2026 или раньше — игнорируем!
                if prev_month_str < "2026-10":
                    # Ставим заглушку в базу, чтобы бот даже не пытался ничего считать
                    db['settings'].update_one({"_id": f"cpa_contest_resolved_{prev_month_str}"}, {"$set": {"done": True, "skipped": "before_launch"}}, upsert=True)
                
                # Проверяем, не выдавали ли мы уже призы за этот месяц.
                # Флаг ставится ДО раздачи и атомарно (insert с уникальным _id): два воркера
                # или рестарт посреди раздачи больше не выдадут призы дважды.
                elif _claim_contest_month(prev_month_str):
                    logger.info(f"🏆 СКАЙНЕТ НАЧИНАЕТ ПОДВЕДЕНИЕ ИТОГОВ КОНКУРСА АГЕНТОВ ЗА {prev_month_str}...")
                    
                    # 1. Выгружаем ТОП агентов с лидами >= 50 за ПРОШЛЫЙ месяц
                    pipeline = [
                        {"$match": {"status": "approved", "approved_month": prev_month_str}},
                        {"$group": {"_id": "$agent_id", "count": {"$sum": 1}}},
                        {"$match": {"count": {"$gte": _cfg("cpa_contest_min")}}}, # 🔥 ЖЕСТКИЙ ПОРОГ В 50 ЛИДОВ 🔥
                        {"$sort": {"count": -1}},
                        {"$limit": 5}
                    ]
                    top_agents = list(db['cpa_traffic'].aggregate(pipeline))
                    
                    if top_agents:
                        from config import STAFF_GROUP_ID, PRIZES_THREAD_ID  # раньше PRIZES_THREAD_ID не существовал → ImportError 1-го числа
                        from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
                        
                        report_to_admins = f"🏆 **ИТОГИ CPA-КОНКУРСА ({prev_month_str})** 🏆\n\n_Порог в {_cfg('cpa_contest_min')} лидов прошли {len(top_agents)} агентов!_\n\n"
                        
                        for idx, agent in enumerate(top_agents):
                            place = idx + 1
                            agent_id = agent["_id"]
                            leads = agent["count"]
                            
                            u_info = db['users'].find_one({"_id": agent_id}) or {}
                            agent_name = u_info.get("first_name", f"ID {agent_id}")
                            username_str = f"@{u_info.get('username')}" if u_info.get('username') else f"ID {agent_id}"
                            
                            if place == 1:
                                prize_str = "Сертификат 2000₽"
                                db['premium_claims'].insert_one({"uid": agent_id, "username": username_str, "details": "CPA 1 МЕСТО (Серт. 2000₽)", "timestamp": time.time(), "status": "pending"})
                            elif place == 2:
                                prize_str = "Сертификат 1500₽"
                                db['premium_claims'].insert_one({"uid": agent_id, "username": username_str, "details": "CPA 2 МЕСТО (Серт. 1500₽)", "timestamp": time.time(), "status": "pending"})
                            elif place == 3:
                                prize_str = "Сертификат 1000₽"
                                db['premium_claims'].insert_one({"uid": agent_id, "username": username_str, "details": "CPA 3 МЕСТО (Серт. 1000₽)", "timestamp": time.time(), "status": "pending"})
                            elif place == 4:
                                _p = _cfg("cpa_prize_4"); prize_str = f"{_p} Очков 💎"
                                db['paid_users'].update_one({"uid": agent_id}, {"$inc": {"bounty_points": _p}}, upsert=True)
                                from core.janitor import log_points; log_points(agent_id, "bounty_points", _p, reason="cpa_contest")
                            elif place == 5:
                                _p = _cfg("cpa_prize_5"); prize_str = f"{_p} Очков 💎"
                                db['paid_users'].update_one({"uid": agent_id}, {"$inc": {"bounty_points": _p}}, upsert=True)
                                from core.janitor import log_points; log_points(agent_id, "bounty_points", _p, reason="cpa_contest")
                                
                            report_to_admins += f"**{place} МЕСТО:** {agent_name} ({leads} лидов)\n🎁 Приз: {prize_str}\n\n"
                            
                            # Пишем агенту в ЛС
                            try:
                                bot.send_message(
                                    agent_id, 
                                    f"🏆 **ПОЗДРАВЛЯЕМ! ВЫ В ТОП-5 АГЕНТОВ СЕТИ!** 🏆\n\n"
                                    f"За прошлый месяц вы привели **{leads}** активных участников и заняли **{place} место**!\n\n"
                                    f"Ваш приз: **{prize_str}**.\n"
                                    f"_(Если вы выиграли Сертификат, заявка отправлена администрации. Если Очки — они уже на балансе!)_",
                                    parse_mode="Markdown"
                                )
                            except: pass

                        markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ Обработать призы в ЦУП", url="https://elite-poster-bot.onrender.com/glaz"))
                        try: bot.send_message(STAFF_GROUP_ID, report_to_admins, parse_mode="Markdown", reply_markup=markup, message_thread_id=PRIZES_THREAD_ID)
                        except: pass
                    else:
                        try: bot.send_message(STAFF_GROUP_ID, f"📉 Итоги CPA ({prev_month_str}): Ни один агент не смог преодолеть порог в {_cfg('cpa_contest_min')} лидов.")
                        except: pass

                    # Записываем флаг, что месяц обработан
                    db['settings'].update_one({"_id": f"cpa_contest_resolved_{prev_month_str}"}, {"$set": {"done": True}}, upsert=True)
                    
        except Exception as e:
            logger.error(f"Ошибка CPA Конкурса: {e}")
            from core.diag import log_error; log_error("CPA-конкурс", e)
            
        time.sleep(3600) # Проверяем дату каждый час

# Запускаем демонов CPA
threading.Thread(target=cpa_tracker_daemon, daemon=True).start()
threading.Thread(target=cpa_monthly_daemon, daemon=True).start()

# 🤖 ФОНОВЫЙ ДЕМОН АВТОПИЛОТА
def autopilot_daemon():
    while True:
        try:
            # Ищем шаблоны, у которых включен автопилот (> 0 часов)
            templates = list(db['templates'].find({"autopilot_interval": {"$gt": 0}}))
            current_time = time.time()
            
            for t in templates:
                interval_sec = t['autopilot_interval'] * 3600 # переводим часы в секунды
                
                # Если прошло нужное время с последнего запуска
                if current_time - t.get('last_run', 0) >= interval_sec:
                    # 1. Атомарно «забираем» запуск: обновится только если last_run не менялся.
                    # Раньше при нескольких воркерах рассылка уходила по 2–4 раза.
                    claimed = db['templates'].update_one(
                        {"id": t['id'], "last_run": t.get('last_run', 0)} if 'last_run' in t else {"id": t['id'], "last_run": {"$exists": False}},
                        {"$set": {"last_run": current_time}}
                    )
                    if claimed.modified_count == 0:
                        continue
                    
                    # 2. Собираем клавиатуру (если есть кнопки)
                    markup = None
                    if t.get("buttons"):
                        markup = types.InlineKeyboardMarkup(row_width=1)
                        for btn in t["buttons"]:
                            kwargs = {"text": btn["text"], "url": btn["url"]}
                            if btn.get("style") and btn["style"] != "default": kwargs["style"] = btn["style"]
                            if btn.get("emoji_id"): kwargs["icon_custom_emoji_id"] = btn["emoji_id"]
                            markup.add(types.InlineKeyboardButton(**kwargs))

                    # 3. Выбираем цель
                    tgt = t['target']
                    txt = t['text']
                    cursor = users_collection.find({}) if tgt == 'all' else users_collection.find({"is_vip": True}) if tgt == 'vip' else users_collection.find({"is_queer": True}) if tgt == 'queer' else None
                    
                    if cursor:
                        count = 0
                        add_radar_log(f"🤖 АВТОПИЛОТ: Запуск по расписанию '{t['name']}'")
                        
                        # 👇 🥷 СТЕЛС-МОДУЛЬ 2.0 (ПЕРСОНАЛЬНАЯ ИЛЛЮЗИЯ) 🥷 👇
                        # Ссылки и исключения настраиваются в панели: «Управление» → «Автопилот»
                        enemy_ref = _cfg("stealth_enemy_ref")
                        boss_ref = _cfg("stealth_boss_ref")
                        admin_ids = [int(x) for x in re.findall(r"-?\d+", str(_cfg("stealth_exempt_ids")))]

                        for u in cursor:
                            uid = u['_id']
                            
                            # Если это админ - показываем ему ЕГО ссылку, если обычный юзер - ТВОЮ
                            current_ref = enemy_ref if uid in admin_ids else boss_ref
                            
                            # Собираем индивидуальный текст на лету
                            final_txt = txt.replace(enemy_ref, current_ref) if enemy_ref in txt else txt
                            
                            # Собираем индивидуальные кнопки на лету
                            final_markup = None
                            if t.get("buttons"):
                                final_markup = types.InlineKeyboardMarkup(row_width=1)
                                for btn in t["buttons"]:
                                    btn_url = btn.get("url")
                                    # Подменяем ссылку в кнопке, если она там есть
                                    if btn_url and enemy_ref in btn_url:
                                        btn_url = btn_url.replace(enemy_ref, current_ref)
                                        
                                    kwargs = {"text": btn["text"], "url": btn_url}
                                    if btn.get("style") and btn["style"] != "default": kwargs["style"] = btn["style"]
                                    if btn.get("emoji_id"): kwargs["icon_custom_emoji_id"] = btn["emoji_id"]
                                    final_markup.add(types.InlineKeyboardButton(**kwargs))
                        # 👆 🥷 КОНЕЦ СТЕЛС-МОДУЛЯ 🥷 👆

                            try:
                                # Отправляем ИНДИВИДУАЛЬНУЮ сборку
                                bot.send_message(uid, final_txt, parse_mode="HTML", disable_web_page_preview=True, reply_markup=final_markup)
                                count += 1
                                time.sleep(0.05)  # ~20 сообщений/сек, иначе Telegram режет флуд-лимитом
                            except Exception as e:
                                if "too many requests" in str(e).lower():
                                    time.sleep(5)
                                # Спасаем текст, если слетел Markdown
                                if "parse entities" in str(e).lower():
                                    try: bot.send_message(uid, final_txt, disable_web_page_preview=True, reply_markup=final_markup)
                                    except: pass
                        try:
                            # Уведомляем админов, что автопилот отработал
                            bot.send_message(STAFF_GROUP_ID, f"🤖 **Автопилот Скайнета сработал!**\nШаблон: `{t['name']}`\n✅ Доставлено: {count} чел.")
                        except: pass

        except Exception as e:
            print(f"Ошибка Автопилота: {e}")
        
        # Демон спит 60 секунд, потом снова проверяет базу
        time.sleep(60)

# Запускаем Демона при старте сервера
threading.Thread(target=autopilot_daemon, daemon=True).start()
# =======================================

# === ДАТЧИК ПУЛЬСА СКАЙНЕТА ===
def heartbeat_skynet():
    from database import db
    import time
    while True:
        try:
            db['settings'].update_one({"_id": "bot_status"}, {"$set": {"skynet_last_seen": time.time()}}, upsert=True)
        except: pass
        time.sleep(60)

import threading
threading.Thread(target=heartbeat_skynet, daemon=True).start()

# 🧹 Уборщик: удаляет сообщения бота по расписанию и раз в сутки чистит служебные коллекции
from core.janitor import janitor_loop
threading.Thread(target=janitor_loop, args=(bot,), daemon=True).start()
threading.Thread(target=migrate_indulgence_flags, daemon=True).start()
# ==============================

# ==================== WEBHOOK ====================
@app.route('/webhook', methods=['POST'])
def webhook():
    if not verify_telegram_webhook_secret():
        return 'forbidden', 403
    update = telebot.types.Update.de_json(request.stream.read().decode('utf-8'))
    bot.process_new_updates([update])
    return 'ok', 200

# Активация внешних WEB-роутов
register_support_routes(app, bot, add_radar_log)
register_finance_routes(app, bot, add_radar_log, OWNER_ID, ROOT_PIN)
register_main_routes(
    app, bot, add_radar_log, ban_user_everywhere, mute_user_everywhere,
    unban_user_everywhere, unmute_user_everywhere, background_corpse_removal,
    WEB_USER, WEB_PASS, OWNER_ID, ADMIN_CHAT_IDS, ROOT_PIN, STAFF_GROUP_ID
)
register_ads_routes(app, bot, add_radar_log)
from web.diag import register_diag_routes
register_diag_routes(app, bot)
from web.poster import register_poster_routes
register_poster_routes(app, bot)
from web.control import register_control_routes
from web.home import register_home_routes
register_control_routes(app, add_radar_log)
register_home_routes(app, add_radar_log)

if __name__ == '__main__':
    print("Бот запущен — мягкая версия с приветствием и удалением сообщений (кроме сети ПАРНИ)")
    app.run(host='0.0.0.0', port=5000)