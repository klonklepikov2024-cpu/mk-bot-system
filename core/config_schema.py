"""
core/config_schema.py — реестр настроек, которые меняются из веб-панели (вкладка «🎛 Управление»).

Каждая настройка: где лежит в базе (settings._id = doc, поле = field), тип, значение по умолчанию
(= как было в коде), подсказка. Добавить новую ручку = добавить строку сюда и читать её через cfg().
owner=True — видит и меняет только владелец (деньги, рефералка).
bots — какие боты читают настройку (для подписи в панели).
"""

G_MONEY = "💰 Цены и курсы"
G_VIP = "👑 VIP-воронка"
G_CPA = "💼 CPA-агенты"
G_POSTER = "📝 Мини-приложение публикации"
G_MOD = "🛡 Модерация и уборка"
G_AI = "🤖 ИИ"
G_LINKS = "🔗 Ссылки"
G_STEALTH = "🥷 Автопилот"
G_SEC = "🗂 Секретарь"
G_BEYOND = "🏳️‍🌈 BEYOND"

SCHEMA = [
    # --- деньги ---
    {"key": "rub_per_star", "group": G_MONEY, "label": "Курс: рублей за 1 ⭐️", "type": "float", "default": 2.0, "min": 0.5, "max": 10,
     "help": "Единый курс сети: оплата с рублёвого баланса, крипто-счета, цены на рынке.", "bots": "Скайнет, Секретарь, BEYOND, МП", "owner": True},
    {"key": "points_per_star", "group": G_MONEY, "label": "Курс: очков за 1 ⭐️", "type": "int", "default": 5, "min": 1, "max": 100,
     "help": "Оплата очками: VIP, штрафы, индульгенция, BEYOND.", "bots": "Скайнет, Секретарь, BEYOND", "owner": True},
    {"key": "city_pass_price", "group": G_MONEY, "label": "Пропуск в чужой город, ⭐️", "type": "int", "default": 250, "min": 1, "max": 100000,
     "bots": "Скайнет", "owner": True},
    {"key": "fine_tag_free", "group": G_MONEY, "label": "Штраф, после которого тег «Свободен», ⭐️", "type": "int", "default": 650, "min": 1, "max": 100000,
     "help": "Если штраф оплачен ровно на эту сумму, человек получает тег «Свободен».", "bots": "Скайнет", "owner": True},
    {"key": "fine_tag_sponsor", "group": G_MONEY, "label": "Штраф-взнос спонсора, ⭐️", "type": "int", "default": 750, "min": 1, "max": 100000,
     "help": "Оплата этой суммы даёт тег «Спонсор_Одобрен» (иммунитет к фильтру коммерции).", "bots": "Скайнет", "owner": True},
    {"key": "ref_tiers", "group": G_MONEY, "label": "Рефералка: пороги и проценты", "type": "text", "default": "10:10, 30:13, 50:15, 100:17, *:20",
     "help": "Формат «до N приглашённых : процент». Звёздочка — все, кто больше. Процент считается от цены VIP.", "bots": "Скайнет", "owner": True},

    # --- VIP ---
    {"key": "vip_remind_days", "group": G_VIP, "label": "Напомнить застрявшему через, дней", "type": "int", "default": 7, "min": 1, "max": 60,
     "help": "Если человек начал вступление в VIP и остановился (не прислал кружок или не оплатил).", "bots": "Скайнет"},
    {"key": "vip_ban_days", "group": G_VIP, "label": "Бан после напоминания через, дней", "type": "int", "default": 3, "min": 1, "max": 60,
     "help": "Работает, только если включён тумблер «VIP-Снайпер». Тех, чей кружок на проверке, не трогает.", "bots": "Скайнет"},
    {"key": "cheap_stars_url", "group": G_VIP, "label": "Ссылка «купить звёзды дешевле»", "type": "text",
     "default": "https://t.me/Avrrorkastarbot?start=7924963993", "help": "Отправляется вместе со счётом на VIP. Пусто — не отправлять.", "bots": "Скайнет", "owner": True},

    # --- CPA ---
    {"key": "cpa_hold_days", "group": G_CPA, "label": "Холд лида, дней", "type": "int", "default": 14, "min": 1, "max": 90,
     "help": "Сколько приведённый человек должен продержаться в чате, чтобы агент получил кейс.", "bots": "Скайнет"},
    {"key": "cpa_welcome_points", "group": G_CPA, "label": "Стартовый набор новичку: очки", "type": "int", "default": 50, "min": 0, "max": 10000, "bots": "Скайнет", "owner": True},
    {"key": "cpa_welcome_shields", "group": G_CPA, "label": "Стартовый набор новичку: щиты", "type": "int", "default": 1, "min": 0, "max": 100, "bots": "Скайнет", "owner": True},
    {"key": "cpa_contest_min", "group": G_CPA, "label": "Конкурс месяца: минимум лидов", "type": "int", "default": 50, "min": 1, "max": 10000, "bots": "Скайнет"},
    {"key": "cpa_prize_4", "group": G_CPA, "label": "Конкурс: 4 место, очки", "type": "int", "default": 3000, "min": 0, "max": 1000000, "bots": "Скайнет", "owner": True},
    {"key": "cpa_prize_5", "group": G_CPA, "label": "Конкурс: 5 место, очки", "type": "int", "default": 1500, "min": 0, "max": 1000000, "bots": "Скайнет", "owner": True},

    # --- мини-приложение ---
    {"key": "bump_hours", "doc": "moderation_limits", "group": G_POSTER, "label": "Поднимать анкету не чаще, часов", "type": "int", "default": 12, "min": 1, "max": 168, "bots": "Скайнет"},
    {"key": "poster_cooldown_min", "group": G_POSTER, "label": "Пауза между публикациями, минут", "type": "int", "default": 2, "min": 1, "max": 1440, "bots": "Скайнет"},
    {"key": "poster_max_templates", "group": G_POSTER, "label": "Шаблонов у одного человека", "type": "int", "default": 5, "min": 1, "max": 30, "bots": "Скайнет"},
    {"key": "poster_max_drafts", "group": G_POSTER, "label": "Черновиков у одного человека", "type": "int", "default": 10, "min": 1, "max": 50, "bots": "Скайнет"},

    # --- модерация ---
    {"key": "cleanup_minutes", "doc": "moderation_limits", "group": G_MOD, "label": "Удалять прожарки бота через, минут", "type": "int", "default": 10, "min": 1, "max": 1440, "bots": "Скайнет"},
    {"key": "repost_strikes", "group": G_MOD, "label": "Страйков за баяны и копипаст до мута", "type": "int", "default": 3, "min": 1, "max": 20, "bots": "Скайнет"},
    {"key": "photo_similarity", "group": G_MOD, "label": "Анти-баян: чувствительность к похожим фото", "type": "int", "default": 8, "min": 0, "max": 20,
     "help": "0 — только точные копии, 8 — обрезанные и пережатые копии, больше — ловит и просто похожие.", "bots": "Скайнет"},
    {"key": "spy_delete_delay", "group": G_MOD, "label": "Шпион: удалять спам ботов-мусорщиков через, сек", "type": "int", "default": 12, "min": 0, "max": 3600, "bots": "Шпион"},
    {"key": "spy_trash_bots", "group": G_MOD, "label": "Шпион: ID ботов-мусорщиков (через запятую)", "type": "text", "default": "7195399721",
     "help": "Сообщения этих ботов Шпион удаляет из чатов. Сейчас: @CPBlockerBot.", "bots": "Шпион"},

    # --- ИИ ---
    {"key": "groq_model", "group": G_AI, "label": "Модель Groq", "type": "text", "default": "openai/gpt-oss-120b",
     "help": "Если Groq снова отключит модель, впишите новую из console.groq.com/docs/models. Пусто — по умолчанию.", "bots": "Скайнет, Шпион"},

    # --- ссылки ---
    {"key": "support_bot", "group": G_LINKS, "label": "Бот поддержки (без @)", "type": "text", "default": "FAQMKBOT", "bots": "Скайнет",
     "help": "Куда ведут кнопки «Поддержка» / «Пройти верификацию» и автоответы Скайнета в чатах."},
    {"key": "vip_bot", "group": G_LINKS, "label": "Бот VIP-вступления (без @)", "type": "text", "default": "Elitepost_bot", "bots": "Скайнет",
     "help": "Кнопка «Купить VIP-статус» в предупреждениях Скайнета."},

    # --- Секретарь ---
    {"key": "support_price", "group": G_SEC, "label": "Доступ к поддержке, ⭐️", "type": "int", "default": 50, "min": 1, "max": 100000, "bots": "Секретарь", "owner": True},
    {"key": "support_group_price", "group": G_SEC, "label": "Цена вопроса в платной группе поддержки (текст)", "type": "text",
     "default": "от 60 до 150 звёзд", "help": "Подставляется в сообщение «Сначала задайте вопрос в платной группе и оплатите …». Пишите как в тексте: «от 60 до 150 звёзд».",
     "bots": "Секретарь", "owner": True},
    {"key": "spam_fine", "group": G_SEC, "label": "Штраф за спам кнопками, ⭐️", "type": "int", "default": 111, "min": 1, "max": 100000,
     "help": "Выставляется после 3 ошибок подряд в меню Секретаря. Не должен совпадать с ценой поддержки.", "bots": "Секретарь", "owner": True},
    {"key": "indulgence_price", "group": G_SEC, "label": "Индульгенция, ⭐️", "type": "int", "default": 2000, "min": 1, "max": 100000,
     "help": "Снятие бана без вопросов + иммунитет. Доступа в VIP и BEYOND не даёт.", "bots": "Секретарь, Скайнет", "owner": True},
    {"key": "shop_price_50", "group": G_SEC, "label": "Магазин: 50 очков, ⭐️", "type": "int", "default": 50, "min": 1, "max": 100000, "bots": "Секретарь", "owner": True},
    {"key": "shop_price_300", "group": G_SEC, "label": "Магазин: 300 очков, ⭐️", "type": "int", "default": 200, "min": 1, "max": 100000, "bots": "Секретарь", "owner": True},
    {"key": "shop_price_1000", "group": G_SEC, "label": "Магазин: 1000 очков + щит, ⭐️", "type": "int", "default": 500, "min": 1, "max": 100000, "bots": "Секретарь", "owner": True},

    # --- BEYOND ---
    {"key": "beyond_reject_days", "group": G_BEYOND, "label": "Повторная заявка после отказа через, дней", "type": "int", "default": 3, "min": 1, "max": 365, "bots": "BEYOND"},
    {"key": "beyond_remind_days", "group": G_BEYOND, "label": "Напомнить застрявшему через, дней", "type": "int", "default": 7, "min": 1, "max": 60, "bots": "BEYOND"},
    {"key": "beyond_ban_days", "group": G_BEYOND, "label": "Бан после напоминания через, дней", "type": "int", "default": 3, "min": 1, "max": 60, "bots": "BEYOND"},
    {"key": "beyond_invite_days", "group": G_BEYOND, "label": "Ссылка-приглашение живёт, дней", "type": "int", "default": 7, "min": 1, "max": 30, "bots": "BEYOND"},

    # --- автопилот (стелс) ---
    {"key": "stealth_enemy_ref", "group": G_STEALTH, "label": "Стелс: какую реф-ссылку подменять", "type": "text", "default": "ref_EQHH7XHV", "bots": "Скайнет", "owner": True},
    {"key": "stealth_boss_ref", "group": G_STEALTH, "label": "Стелс: на какую подменять", "type": "text", "default": "ref_2BBPF35H", "bots": "Скайнет", "owner": True},
    {"key": "stealth_exempt_ids", "group": G_STEALTH, "label": "Стелс: кому показывать оригинал (ID через запятую)", "type": "text", "default": "7235010425", "bots": "Скайнет", "owner": True},
]

BY_KEY = {e["key"]: e for e in SCHEMA}
GROUP_ORDER = [G_MONEY, G_SEC, G_VIP, G_BEYOND, G_CPA, G_POSTER, G_MOD, G_AI, G_LINKS, G_STEALTH]
