import os
import re # <--- ДОБАВИТЬ ЭТО
from pymongo import MongoClient

# ==================== СЕКРЕТЫ И НАСТРОЙКИ СЕРВЕРА ====================
BOT_TOKEN = os.getenv('BOT_TOKEN')
TOKEN = BOT_TOKEN  # 🔥 Универсальный алиас! Теперь будут работать ВСЕ боты
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

if not BOT_TOKEN or not MONGO_URI:
    raise ValueError("❌ КРИТИЧЕСКАЯ ОШИБКА: BOT_TOKEN или MONGO_URI не найдены в переменных окружения!")

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

# Базовые списки, если база пустая (ФОЛЛБЕК)
FALLBACK_MK = {"Екатеринбург": -1002210043742, "Челябинск": -1002238514762, "БЕЗ ПРЕДРАССУДКОВ": -1001219669239, "RAINBOW MAN": -1003496028436, "Пермь": -1002205127231, "Ижевск": -1001604781452, "Казань": -1002228881675, "Оренбург": -1002255568202, "Уфа": -1002196469365, "Новосибирск": -1002235645677, "Красноярск": -1002248474008, "Барнаул": -1002234471215, "Омск": -1002151258573, "Саратов": -1002426762134, "Воронеж": -1002207503508, "Самара": -1001852671383, "Волгоград": -1002167762598, "Нижний Новгород": -1001631628911, "Калининград": -1002217056197, "Иркутск": -1002685095003, "Кемерово": -1002147522863, "Москва": -1002208434096, "Санкт-Петербург": -1002485776859, "Общая группа Юга": -1001814693664, "Тюмень": -1002210623988, "ХМАО": -1002210623988, "ЯМАЛ": -1002210623988, "Казахстан": -1003091556050, "Мужской Чат": -1002169723426, "Фетиши": -1002197215824, "Аренда Жилья": -1001238252865, "Секс Туризм": -1002236337328, "Галерея": -1002217967528, "Тестовая группа 🛠️": -1002426733876}
FALLBACK_PARNI = {"Екатеринбург": -1002413948841, "Тюмень": -1002255622479, "Омск": -1002274367832, "Челябинск": -1002406302365, "Пермь": -1002280860973, "Курган": -1002469285352, "ХМАО": -1002287709568, "Уфа": -1002448909000, "Новосибирск": -1002261777025, "ЯМАЛ": -1002371438340, "Оренбург": -1003888335997, "Москва": -1003856528145, "Санкт-Петербург": -1003519420984, "Красноярск": -1003347456711}
FALLBACK_NS = {"Новосибирск": -1001824149334, "Челябинск": -1002233108474, "Пермь": -1001753881279, "Уфа": -1001823390636, "ЯМАЛ": -1002145851794, "Москва": -1001938448310, "ХМАО": -1001442597049, "Екатеринбург": -1002169473861, "Тюмень": -1002170955867, "Санкт-Петербург": -1002335014334, "Тюмень 2": -1001427433513, "Челябинск 2": -1002193127380}
FALLBACK_RAINBOW = {"Екатеринбург": -1002419653224}
FALLBACK_GAYZNAK = {"Красноярск": -1002335149925, "Екатеринбург": -1002571605722, "Пермь": -1002599206099, "Тюмень": -1002553431228, "Новосибирск": -1002627786446, "Самара": -1002301984331, "Казань": -1002277433049, "Воронеж": -1002428155161, "Кемерово": -1002418700136, "Иркутск": -1002454522264, "Челябинск": -1003366643944, "Орёл": -1003323558103, "Саратов": -1003638608363, "Архангельск": -1003120218775, "Ярославль": -1003332193158, "Тверь": -1003369813272, "Великий Новгород": -1003429766543, "Владимир": -1003276544901, "Мурманск": -1003302580641, "Рязань": -1003460247519, "Смоленск": -1003423811230, "Тамбов": -1003225139634, "Липецк": -1003487872172, "Тула": -1003482077625, "Брянск": -1003372917376, "Волгоград": -1002476113714, "Москва": -1002255869134}

# ==================== УМНАЯ СИНХРОНИЗАЦИЯ С ЦУП ====================
client_db = MongoClient(MONGO_URI)
db = client_db['elite_bot_db']

def create_and_push_default():
    def convert_dict_to_list(chat_dict):
        return [{"name": name, "id": str(chat_id)} for name, chat_id in chat_dict.items()]
        
    default_data = {
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
    db['settings'].update_one({"_id": "infrastructure"}, {"$set": default_data}, upsert=True)
    return default_data

# 🔥 1. Создаем "контейнеры" один раз, чтобы другие файлы могли их импортировать
chat_ids_mk = {}
chat_ids_parni = {}
chat_ids_ns = {}
chat_ids_rainbow = {}
chat_ids_gayznak = {}
all_cities = {}
PARNI_CHATS = []
MAIN_CHANNEL_LINK = "https://t.me/clubofrm"

NETWORK_LINKS = (
    "📍 **Ссылки для возврата в чаты:**\n"
    "• [МК (Мужской Клуб)](https://t.me/clubofrm/44)\n"
    "• [ПАРНИ 18+](https://t.me/znakparni/116)\n"
    "• [ГЕЙ чаты (Инфо)](https://t.me/gaychatcities_info/4)\n"
    "• [НС (Урал)](https://t.me/uralns/118)"
)

NON_CITIES = [
    "БЕЗ ПРЕДРАССУДКОВ", "RAINBOW MAN", "Мужской Чат", "Фетиши", 
    "Аренда Жилья", "Секс Туризм", "Галерея", "Тестовая группа 🛠️"
]

# 🔥 2. Функция умного обновления (меняет "внутренности" словарей)
def refresh_matrix():
    global MAIN_CHANNEL_LINK
    infra = db['settings'].find_one({"_id": "infrastructure"})
    
    if not infra or not infra.get("networks") or len(infra["networks"].get("mk", [])) == 0:
        print("⚙️ Матрица городов пуста. Запускаю авто-заполнение ЦУПа...")
        infra = create_and_push_default()

    networks = infra.get("networks", {})
    MAIN_CHANNEL_LINK = infra.get("global_links", {}).get("main_channel", "https://t.me/clubofrm")
    
    def list_to_dict(chat_list):
        return {item["name"]: int(item["id"]) for item in chat_list}

    # ИСПОЛЬЗУЕМ clear() и update(), чтобы файлы (casino.py и т.д.) увидели новые данные!
    chat_ids_mk.clear(); chat_ids_mk.update(list_to_dict(networks.get("mk", [])))
    chat_ids_parni.clear(); chat_ids_parni.update(list_to_dict(networks.get("parni", [])))
    chat_ids_ns.clear(); chat_ids_ns.update(list_to_dict(networks.get("ns", [])))
    chat_ids_rainbow.clear(); chat_ids_rainbow.update(list_to_dict(networks.get("rainbow", [])))
    chat_ids_gayznak.clear(); chat_ids_gayznak.update(list_to_dict(networks.get("gayznak", [])))

    PARNI_CHATS.clear(); PARNI_CHATS.extend(list(chat_ids_parni.values()))

    all_cities.clear()
    def insert_to_all(city, net_key, real_name, chat_id):
        if city in NON_CITIES: return
        clean_city = re.sub(r'\s*\d+$', '', city).strip()
        if clean_city not in all_cities:
            all_cities[clean_city] = {}
        if net_key not in all_cities[clean_city]:
            all_cities[clean_city][net_key] = []
        all_cities[clean_city][net_key].append({"name": real_name, "chat_id": chat_id})

    for city, chat_id in chat_ids_mk.items(): insert_to_all(city, "mk", city, chat_id)
    for city, chat_id in chat_ids_parni.items(): insert_to_all(city, "parni", city, chat_id)
    for city, chat_id in chat_ids_ns.items(): insert_to_all(city, "ns", city, chat_id)
    for city, chat_id in chat_ids_rainbow.items(): insert_to_all(city, "rainbow", city, chat_id)
    for city, chat_id in chat_ids_gayznak.items(): insert_to_all(city, "gayznak", city, chat_id)

# 🔥 3. Загружаем данные при старте бота
refresh_matrix()
print("✅ Скайнет-Секретарь успешно загрузил Матрицу Инфраструктуры!")

# 🔥 4. Запускаем ДЕМОНА: он будет обновлять города в фоне каждые 60 секунд!
import threading
import time
def matrix_updater_daemon():
    while True:
        time.sleep(60)
        try: refresh_matrix()
        except: pass

threading.Thread(target=matrix_updater_daemon, daemon=True).start()