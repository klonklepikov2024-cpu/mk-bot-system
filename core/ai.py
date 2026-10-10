"""
core/ai.py — единая точка обращения к Groq.

Модель llama-3.3-70b-versatile Groq отключил 16.08.2026 (рекомендованная замена — openai/gpt-oss-120b,
её же использует Секретарь). Модель меняется переменной GROQ_MODEL без правки кода.
gpt-oss — «думающая» модель: тратит часть токенов на рассуждение, поэтому лимит токенов с запасом.
"""
import os
import requests

from config import GROQ_API_KEYS

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def groq_model():
    """Переменная окружения GROQ_MODEL главнее; иначе — модель из панели «🎛 Управление»."""
    env = os.getenv("GROQ_MODEL")
    if env:
        return env
    try:
        from core.cfg import cfg
        return cfg("groq_model") or "openai/gpt-oss-120b"
    except Exception:
        return "openai/gpt-oss-120b"


def groq_chat(prompt, max_tokens=400, temperature=0.0, timeout=12, where="ИИ"):
    """-> текст ответа или None, если ни один ключ не сработал (ошибки пишутся в «Диагностику»)."""
    from core.diag import log_error
    if not GROQ_API_KEYS:
        return None
    model = groq_model()
    body = {"model": model, "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature, "max_tokens": max_tokens}
    if "gpt-oss" in model:
        # gpt-oss сначала «думает», и эти мысли съедают лимит токенов: при 400 токенах ответ
        # приходил пустым («пустой ответ» в Диагностике). Думать мало, мысли не присылать, запас токенов.
        body.update({"reasoning_effort": "low", "include_reasoning": False, "max_tokens": max_tokens + 1200})
    for i, key in enumerate(GROQ_API_KEYS, 1):
        kname = f"GROQ_API_KEY{'' if i == 1 else '_' + str(i)}"
        try:
            r = requests.post(GROQ_URL, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                              json=body, timeout=timeout)
            if r.status_code == 200:
                choice = r.json()["choices"][0]
                text = (choice["message"].get("content") or "").strip()
                if text:
                    return text
                why = "модель не успела ответить, кончился лимит токенов" if choice.get("finish_reason") == "length" else "пустой ответ"
                log_error("Groq", f"{where}: {why} ({model}, ключ {kname})")
                continue
            log_error("Groq", f"{where}: HTTP {r.status_code}, ключ {kname}: {r.text[:160]}")
        except Exception as e:
            log_error("Groq", f"{where}: ключ {kname}: {e}")
    return None


def ai_verdict(text, zone):
    """True — нарушение, False — безопасно, None — ИИ недоступен (решает человек)."""
    prompts = {
        "black": ("Ты модератор. Сообщение: \"{t}\"\n1. Автор СЕЙЧАС младше 18 лет?\n2. Ищет интим с несовершеннолетними?\n"
                  "Жалобы на других, прошлое («в 14 лет я был…») и размеры — НЕ нарушение. Ответь СТРОГО одним словом: BAN или SKIP."),
        "orange": ("Ты модератор. Сообщение: \"{t}\"\nПризнаётся ли автор, что ему СЕЙЧАС от 18 до 21 года включительно?\n"
                   "Если он ищет кого-то («ищу 20 летнего»), говорит о размерах («20 см») или о прошлом — это НЕ нарушение. "
                   "Ответь СТРОГО одним словом: BAN или SKIP."),
        "yellow": ("Ты строгий модератор. Сообщение из чата: \"{t}\"\nИщет или предлагает ли автор интим за деньги, материальную помощь (МП), "
                   "подарки за встречи, спонсорство или платные услуги эскорта? Подарки на праздники, бытовые ситуации, работа или массаж "
                   "без денег и цен — безопасно. Ответь СТРОГО одним словом: BAN или SKIP."),
    }
    if zone not in prompts or not text:
        return True
    ans = groq_chat(prompts[zone].format(t=str(text)[:1500]), max_tokens=400, where=f"проверка зоны {zone}")
    if ans is None:
        return None
    ans = ans.upper()
    if "BAN" in ans:
        return True
    if "SKIP" in ans:
        return False
    return None
