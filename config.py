import os
from database import db
from pymongo import MongoClient

# ==================== СЕКРЕТЫ И НАСТРОЙКИ СЕРВЕРА ====================
TOKEN = os.getenv('BOT_TOKEN') # 🔥 ИСПРАВЛЕНО: переименовали BOT_TOKEN обратно в TOKEN
MONGO_URI = os.getenv('MONGO_URI')
CRYPTO_TOKEN = os.getenv("CRYPTO_TOKEN")
APP_URL = os.getenv("APP_URL")
PORT = int(os.environ.get('PORT', 5000))

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_API_KEY_2 = os.environ.get("GROQ_API_KEY_2")
GROQ_API_KEY_3 = os.environ.get("GROQ_API_KEY_3")
GROQ_API_KEYS = [key for key in [GROQ_API_KEY, GROQ_API_KEY_2, GROQ_API_KEY_3] if key]

HF_TOKEN = os.getenv('HF_TOKEN')
OPENROUTER_KEY = os.getenv('OPENROUTER_KEY')

if not TOKEN or not MONGO_URI: # 🔥 ИСПРАВЛЕНО ЗДЕСЬ ТОЖЕ
    raise ValueError("❌ КРИТИЧЕСКАЯ ОШИБКА: TOKEN или MONGO_URI не найдены в переменных окружения!")

ADMIN_CHAT_IDS = [479938867, 7235010425]
OWNER_ID = 479938867
VIP_PRICE_STARS = 250

# ==================== НАСТРОЙКИ СЛУЖЕБНЫХ ЧАТОВ ====================
STAFF_GROUP_ID = -1002196190507
JOURNAL_CHAT_ID = -1002158861390
SUPPORT_GROUP_ID = -1002287143588
MAIN_CHANNEL_ID = -1002246737442
MAIN_CHANNEL_USERNAME = "@clubofrm"
VIP_CHAT_ID = -1002446486648
BEYOND_CHAT_ID = -1002873115881
VERIFICATION_LINK = "http://t.me/vip_znakbot"

# Вставь вот сюда
NON_CITIES = ["БЕЗ ПРЕДРАССУДКОВ", "RAINBOW MAN", "Мужской Чат", "Фетиши", "Аренда Жилья", "Секс Туризм", "Галерея", "Тестовая группа 🛠️"]

# Тема (топик) в STAFF-группе для итогов CPA-конкурса. None = общий чат группы.
PRIZES_THREAD_ID = int(os.getenv("PRIZES_THREAD_ID")) if os.getenv("PRIZES_THREAD_ID", "").lstrip("-").isdigit() else None

# Резервная копия матрицы чатов. Раньше эти словари нигде не были объявлены, и при пустой
# базе бот падал с NameError на старте. Заполните, если хотите аварийную копию в коде.
FALLBACK_PARNI = {}
FALLBACK_MK = {}
FALLBACK_NS = {}
FALLBACK_RAINBOW = {}
FALLBACK_GAYZNAK = {}

# Дальше идет функция
def get_network_data():
    from database import db
    infra = db['settings'].find_one({"_id": "infrastructure"})
    
    # 🔥 ПАРАШЮТ БЕЗОПАСНОСТИ: Если база вдруг пустая или удалилась
    has_fallback = any([FALLBACK_PARNI, FALLBACK_MK, FALLBACK_NS, FALLBACK_RAINBOW, FALLBACK_GAYZNAK])
    if (not infra or not infra.get("networks") or len(infra["networks"].get("mk", [])) == 0) and not has_fallback:
        print("⚠️ Матрица городов в базе пуста, а резервной копии в config.py нет. Заполните её в ЦУП.")
        infra = infra or {}
    elif not infra or not infra.get("networks") or len(infra["networks"].get("mk", [])) == 0:
        print("⚙️ Матрица городов пуста. Восстанавливаю резервную копию...")
        def convert_dict_to_list(chat_dict):
            return [{"name": name, "id": str(chat_id)} for name, chat_id in chat_dict.items()]
            
        infra = {
            "cities": "Екатеринбург, Челябинск, Пермь, Ижевск, Казань, Оренбург, Уфа, Новосибирск, Красноярск, Барнаул, Омск, Саратов, Воронеж, Самара, Волгоград, Нижний Новгород, Калининград, Иркутск, Кемерово, Москва, Санкт-Петербург, Тюмень, ХМАО, ЯМАЛ, Орёл, Архангельск, Ярославль, Тверь, Великий Новгород, Владимир, Мурманск, Рязань, Смоленск, Тамбов, Липецк, Тула, Брянск",
            "global_links": {"main_channel": "https://t.me/clubofrm", "faq": "https://t.me/FAQMKBOT"},
            "networks": {
                "parni": convert_dict_to_list(FALLBACK_PARNI),
                "mk": convert_dict_to_list(FALLBACK_MK),
                "ns": convert_dict_to_list(FALLBACK_NS),
                "rainbow": convert_dict_to_list(FALLBACK_RAINBOW),
                "gayznak": convert_dict_to_list(FALLBACK_GAYZNAK)
            },
            "competitors": db['settings'].find_one({"_id": "spy_settings"}).get("chats", []) if db['settings'].find_one({"_id": "spy_settings"}) else []
        }
        # Сохраняем восстановленную базу обратно в MongoDB
        db['settings'].update_one({"_id": "infrastructure"}, {"$set": infra}, upsert=True)
    # 👆 ======================================================== 👆

    networks = infra.get("networks", {})
    
    c_mk = {c["name"]: int(c["id"]) for c in networks.get("mk", []) if c.get("id")}
    c_parni = {c["name"]: int(c["id"]) for c in networks.get("parni", []) if c.get("id")}
    c_ns = {c["name"]: int(c["id"]) for c in networks.get("ns", []) if c.get("id")}
    c_rainbow = {c["name"]: int(c["id"]) for c in networks.get("rainbow", []) if c.get("id")}
    c_gay = {c["name"]: int(c["id"]) for c in networks.get("gayznak", []) if c.get("id")}
    
    p_chats = list(c_parni.values())
    m_link = infra.get("global_links", {}).get("main_channel", "https://t.me/clubofrm")
    
    a_cities = {}
    NON_CITIES = ["БЕЗ ПРЕДРАССУДКОВ", "RAINBOW MAN", "Мужской Чат", "Фетиши", "Аренда Жилья", "Секс Туризм", "Галерея", "Тестовая группа 🛠️"]
    
    def insert_to_all(city, net_key, real_name, chat_id):
        if city in NON_CITIES: return
        clean_city = city.replace(" 2", "")
        if clean_city not in a_cities: a_cities[clean_city] = {}
        if net_key not in a_cities[clean_city]: a_cities[clean_city][net_key] = []
        a_cities[clean_city][net_key].append({"name": real_name, "chat_id": chat_id})

    for city, cid in c_mk.items(): insert_to_all(city, "mk", city, cid)
    for city, cid in c_parni.items(): insert_to_all(city, "parni", city, cid)
    for city, cid in c_ns.items(): insert_to_all(city, "ns", city, cid)
    for city, cid in c_rainbow.items(): insert_to_all(city, "rainbow", city, cid)
    for city, cid in c_gay.items(): insert_to_all(city, "gayznak", city, cid)
    
    return c_mk, c_parni, c_ns, c_rainbow, c_gay, p_chats, a_cities, m_link

# Вызываем один раз при старте, чтобы не сломать старые импорты в других файлах
chat_ids_mk, chat_ids_parni, chat_ids_ns, chat_ids_rainbow, chat_ids_gayznak, PARNI_CHATS, all_cities, MAIN_CHANNEL_LINK = get_network_data()

NETWORK_LINKS = (
    "📍 **Ссылки для возврата в чаты:**\n"
    "• [МК (Мужской Клуб)](https://t.me/clubofrm/44)\n"
    "• [ПАРНИ 18+](https://t.me/znakparni/116)\n"
    "• [ГЕЙ чаты (Инфо)](https://t.me/gaychatcities_info/4)\n"
    "• [НС (Урал)](https://t.me/uralns/118)"
)