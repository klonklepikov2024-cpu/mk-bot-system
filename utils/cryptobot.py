import requests
from config import CRYPTO_TOKEN
from utils.logger import logger

def get_crypto_pay_url(custom_payload, amount_stars, description, asset=None):
    amount_rub = int(amount_stars * 1.8)
    
    if not CRYPTO_TOKEN:
        logger.error("Токен CRYPTO_TOKEN не найден!")
        return None

    url = "https://pay.crypt.bot/api/createInvoice"
    headers = {
        "Crypto-Pay-API-Token": CRYPTO_TOKEN,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    payload = {
        "currency_type": "fiat",
        "fiat": "RUB",
        "amount": str(amount_rub), 
        "payload": custom_payload,
        "description": description
    }
    
    if asset: payload["asset"] = asset
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        res = response.json()
        if res.get("ok"): 
            return res["result"]["mini_app_invoice_url"]
    except Exception as e: 
        logger.error(f"Ошибка связи с CryptoBot: {e}")
        
    return None