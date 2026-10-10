"""
web_auth.py — защита веб-панели «Глаз Саурона».

  * Логин + пароль (пароли — только ХЕШИ).
  * 2FA: одноразовый код приходит админу в Telegram вместе с IP и браузером.
  * Несколько админов: у каждого свой логин, пароль и свой Telegram.
  * Второй админ задаёт себе пароль сам: пишет боту /webpass -> получает одноразовую ссылку.
  * Лимит попыток по IP (5 ошибок -> бан 15 минут).
  * Тревога владельцу в Telegram при подозрительных попытках.
  * Охранник на все /glaz/*: без сессии не пройти, даже если в роуте забыли проверку.
  * Проверка подписи CryptoBot и секрета Telegram-вебхука.
"""
import os
import time
import hmac
import hashlib
import secrets
from datetime import timedelta

from flask import request, session, redirect, url_for, render_template, jsonify
from werkzeug.security import check_password_hash, generate_password_hash

from database import db

# ====== НАСТРОЙКИ ======
# Дополнительные админы: {telegram_id: "логин"}. Пароль они задают сами через /webpass.
ALLOWED_ADMINS = {
    7235010425: "MKprinc",
}

MAX_FAILS = 5
BLOCK_SECONDS = 15 * 60
CODE_TTL = 5 * 60
CODE_MAX_TRIES = 3
SESSION_HOURS = 12
SETPASS_TTL = 10 * 60
MIN_PASSWORD_LEN = 12
ALERT_THROTTLE = 5 * 60

PUBLIC_EXACT = {"/glaz/login", "/glaz/2fa", "/glaz/api/cryptobot_webhook"}
PUBLIC_PREFIX = ("/glaz/setpass/",)

# фиктивный хеш: проверяем его, когда логин неизвестен, чтобы время ответа не выдавало существование логина
_DUMMY_HASH = generate_password_hash(secrets.token_hex(16))


def client_ip():
    # Первый адрес в X-Forwarded-For присылает сам клиент и может подставить любой,
    # обходя лимит попыток. Доверяем адресу, который дописал прокси хостинга
    # (TRUSTED_PROXY_HOPS-й с конца, по умолчанию последний).
    xff = [p.strip() for p in request.headers.get("X-Forwarded-For", "").split(",") if p.strip()]
    try:
        hops = max(1, int(os.getenv("TRUSTED_PROXY_HOPS", "1")))
    except ValueError:
        hops = 1
    if len(xff) >= hops:
        return xff[-hops]
    return request.remote_addr or "?"


def _eq(a, b):
    return hmac.compare_digest(str(a).encode(), str(b).encode())


# ---------- ЛИМИТ ПОПЫТОК ----------
def _is_blocked(ip):
    rec = db["login_attempts"].find_one({"_id": ip})
    return bool(rec and rec.get("blocked_until", 0) > time.time())


def _register_fail(ip):
    """Возвращает True, если именно эта ошибка вызвала блокировку."""
    rec = db["login_attempts"].find_one_and_update(
        {"_id": ip}, {"$inc": {"fails": 1}, "$set": {"last": time.time()}},
        upsert=True, return_document=True,
    )
    if rec["fails"] >= MAX_FAILS:
        db["login_attempts"].update_one(
            {"_id": ip}, {"$set": {"blocked_until": time.time() + BLOCK_SECONDS, "fails": 0}}
        )
        return True
    return False


def _clear_fails(ip):
    db["login_attempts"].delete_one({"_id": ip})


# ---------- ВЛАДЕЛЕЦ ----------
OWNER_ONLY_PATHS = (
    "/glaz/api/live_finance", "/glaz/api/root/finance", "/glaz/api/analytics/revenue",
    "/glaz/api/diag/revenue_month",
    "/glaz/api/get_prices", "/glaz/api/save_prices",   # тарифы VIP/BEYOND/рекламы — только владелец
)


def is_owner():
    """Вошёл именно владелец (логин WEB_USER), а не дополнительный админ."""
    login = session.get("login") or ""
    owner = os.getenv("WEB_USER", "")
    return bool(login and owner) and _eq(login, owner)


# ---------- ПОДПИСИ ВНЕШНИХ ВЕБХУКОВ ----------
def verify_cryptobot_signature(raw_body: bytes, header_sig: str) -> bool:
    token = os.getenv("CRYPTO_TOKEN", "")
    if not token or not header_sig:
        return False
    key = hashlib.sha256(token.encode()).digest()
    expected = hmac.new(key, raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header_sig)


def verify_telegram_webhook_secret() -> bool:
    expected = os.getenv("TG_WEBHOOK_SECRET", "")
    got = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    return bool(expected) and hmac.compare_digest(expected, got)


# ---------- ПОДКЛЮЧЕНИЕ ----------
def init_web_auth(app, bot, add_radar_log, owner_id):
    secret = os.getenv("FLASK_SECRET_KEY")
    if not secret or len(secret) < 32:
        raise RuntimeError("Задай FLASK_SECRET_KEY (>=32 символов) в переменных окружения!")
    app.secret_key = secret

    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=SESSION_HOURS),
    )

    owner_login = os.getenv("WEB_USER", "")
    owner_hash = os.getenv("WEB_PASS_HASH", "")
    if not owner_login or not owner_hash:
        raise RuntimeError("Задай WEB_USER и WEB_PASS_HASH в переменных окружения!")

    base_url = (os.getenv("APP_URL") or "https://elite-poster-bot.onrender.com").rstrip("/")

    # ---- вспомогательные ----
    def find_admin(username):
        """-> {'login','tg_id','hash'} или None"""
        if _eq(username, owner_login):
            return {"login": owner_login, "tg_id": owner_id, "hash": owner_hash}
        for tg_id, login in ALLOWED_ADMINS.items():
            if _eq(username, login):
                rec = db["web_admins"].find_one({"_id": tg_id})
                if rec and rec.get("hash"):
                    return {"login": login, "tg_id": tg_id, "hash": rec["hash"]}
        return None

    def alert_owner(kind, text):
        """Тревога владельцу в Telegram, не чаще раза в 5 минут на один вид события."""
        key = f"{kind}"
        rec = db["alert_throttle"].find_one({"_id": key})
        if rec and time.time() - rec.get("ts", 0) < ALERT_THROTTLE:
            return
        db["alert_throttle"].replace_one({"_id": key}, {"_id": key, "ts": time.time()}, upsert=True)
        try:
            bot.send_message(owner_id, text, parse_mode="HTML")
        except Exception as e:
            print(f"alert_owner error: {e}")

    # ---- Бот: /webpass — одноразовая ссылка для установки пароля ----
    @bot.message_handler(commands=["webpass"])
    def cmd_webpass(message):
        if message.chat.type != "private":
            return
        tg_id = message.from_user.id
        login = ALLOWED_ADMINS.get(tg_id)
        if not login:
            return  # чужим не отвечаем
        token = secrets.token_urlsafe(32)
        db["setpass_tokens"].replace_one(
            {"_id": hashlib.sha256(token.encode()).hexdigest()},
            {"_id": hashlib.sha256(token.encode()).hexdigest(), "tg_id": tg_id, "exp": time.time() + SETPASS_TTL},
            upsert=True,
        )
        bot.send_message(
            tg_id,
            f"🔑 Твой логин: <code>{login}</code>\n\n"
            f"Ссылка для создания пароля (действует 10 минут, один раз):\n"
            f"{base_url}/glaz/setpass/{token}\n\n"
            f"Пароль вводится на сайте, в чат его писать не надо.",
            parse_mode="HTML", disable_web_page_preview=True,
        )

    # ---- Охранник ----
    @app.before_request
    def guard_glaz():
        p = request.path
        if p.startswith("/glaz") and p not in PUBLIC_EXACT and not p.startswith(PUBLIC_PREFIX):
            if not session.get("logged_in"):
                if p.startswith("/glaz/api"):
                    return jsonify({"error": "Unauthorized"}), 401
                return redirect(url_for("login"))
            # Доходы видит только владелец: остальным админам эти разделы не отдаются вовсе
            if p.startswith(OWNER_ONLY_PATHS) and not is_owner():
                return jsonify({"error": "Access Denied"}), 403

    @app.context_processor
    def inject_owner_flag():
        return {"is_owner": is_owner()}

    @app.after_request
    def sec_headers(resp):
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        if request.path.startswith("/glaz"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    # ---- Установка пароля по одноразовой ссылке ----
    def _get_token_rec(token):
        rec = db["setpass_tokens"].find_one({"_id": hashlib.sha256(token.encode()).hexdigest()})
        if not rec or rec["exp"] < time.time():
            return None
        return rec

    @app.route("/glaz/setpass/<token>", methods=["GET", "POST"])
    def setpass(token):
        rec = _get_token_rec(token)
        if not rec:
            return render_template("setpass.html", expired=True, token=token), 410
        error = None
        if request.method == "POST":
            p1 = request.form.get("password", "")
            p2 = request.form.get("password2", "")
            if len(p1) < MIN_PASSWORD_LEN:
                error = f"Пароль слишком короткий (минимум {MIN_PASSWORD_LEN} символов)."
            elif p1 != p2:
                error = "Пароли не совпадают."
            elif p1.isdigit():
                error = "Пароль из одних цифр слабый. Добавь буквы."
            else:
                tg_id = rec["tg_id"]
                db["web_admins"].replace_one(
                    {"_id": tg_id},
                    {"_id": tg_id, "login": ALLOWED_ADMINS[tg_id], "hash": generate_password_hash(p1),
                     "updated": time.time()},
                    upsert=True,
                )
                db["setpass_tokens"].delete_one({"_id": hashlib.sha256(token.encode()).hexdigest()})
                add_radar_log(f"🔑 Админ {ALLOWED_ADMINS[tg_id]} задал пароль для веб-панели. IP {client_ip()}")
                alert_owner(f"setpass:{tg_id}", f"🔑 Админ <b>{ALLOWED_ADMINS[tg_id]}</b> задал/сменил пароль для веб-панели. Если это не он, напиши ему.")
                try:
                    bot.send_message(tg_id, "✅ Пароль сохранён. Теперь заходи в панель: логин + пароль, затем код придёт сюда.")
                except Exception:
                    pass
                return render_template("setpass.html", done=True, login=ALLOWED_ADMINS[tg_id], token=token)
        return render_template("setpass.html", error=error, token=token, login=ALLOWED_ADMINS.get(rec["tg_id"]))

    # ---- Шаг 1: логин + пароль ----
    @app.route("/glaz/login", methods=["GET", "POST"])
    def login():
        error = None
        ip = client_ip()
        if request.method == "POST":
            if _is_blocked(ip):
                return render_template("login.html", error="Слишком много попыток. Подожди 15 минут."), 429

            u = request.form.get("username", "")
            pw = request.form.get("password", "")
            admin = find_admin(u)
            pass_ok = check_password_hash(admin["hash"] if admin else _DUMMY_HASH, pw)

            if admin and pass_ok:
                code = f"{secrets.randbelow(1_000_000):06d}"
                db["web_login_codes"].replace_one(
                    {"_id": str(admin["tg_id"])},
                    {"_id": str(admin["tg_id"]), "code_hash": hashlib.sha256(code.encode()).hexdigest(),
                     "exp": time.time() + CODE_TTL, "tries": 0, "ip": ip},
                    upsert=True,
                )
                ua = request.headers.get("User-Agent", "?")[:120]
                try:
                    bot.send_message(
                        admin["tg_id"],
                        f"🔐 <b>Вход в веб-панель</b> ({admin['login']})\nКод: <code>{code}</code>\n\n"
                        f"IP: <code>{ip}</code>\nБраузер: {ua}\n\n"
                        f"Это не ты? Никому не говори код и срочно смени пароль.",
                        parse_mode="HTML",
                    )
                except Exception as e:
                    add_radar_log(f"⚠️ Не удалось отправить 2FA-код ({admin['login']}): {e}")
                    return render_template("login.html", error="Не могу отправить код в Telegram. Нажми Старт у бота."), 500

                session.clear()
                session["pre_auth_until"] = time.time() + CODE_TTL
                session["pre_auth_tg"] = admin["tg_id"]
                session["pre_auth_login"] = admin["login"]
                add_radar_log(f"🔑 Пароль верный ({admin['login']}), ждём 2FA-код. IP {ip}")
                return redirect(url_for("two_factor"))

            blocked_now = _register_fail(ip)
            add_radar_log(f"⚠️ Неудачная попытка входа в веб! IP {ip}, логин: {u[:30]}")
            if admin:
                # логин настоящий, а пароль мимо — это уже подозрительно
                alert_owner(f"badpass:{admin['login']}",
                            f"⚠️ <b>Неверный пароль</b> для логина <code>{admin['login']}</code>\nIP: <code>{ip}</code>")
            if blocked_now:
                alert_owner(f"blocked:{ip}", f"🚫 <b>IP заблокирован</b> на 15 минут после {MAX_FAILS} неудачных попыток входа.\nIP: <code>{ip}</code>")
            error = "ОТКАЗАНО: Неверный маркер доступа!"
        return render_template("login.html", error=error)

    # ---- Шаг 2: код из Telegram ----
    @app.route("/glaz/2fa", methods=["GET", "POST"])
    def two_factor():
        ip = client_ip()
        tg_id = session.get("pre_auth_tg")
        if not tg_id or session.get("pre_auth_until", 0) < time.time():
            return redirect(url_for("login"))
        if _is_blocked(ip):
            return render_template("2fa.html", error="Слишком много попыток."), 429

        error = None
        if request.method == "POST":
            entered = request.form.get("code", "").strip()
            rec = db["web_login_codes"].find_one({"_id": str(tg_id)})
            if not rec or rec["exp"] < time.time() or rec["tries"] >= CODE_MAX_TRIES:
                session.clear()
                return redirect(url_for("login"))

            db["web_login_codes"].update_one({"_id": str(tg_id)}, {"$inc": {"tries": 1}})
            if hmac.compare_digest(rec["code_hash"], hashlib.sha256(entered.encode()).hexdigest()):
                login_name = session.get("pre_auth_login", "?")
                db["web_login_codes"].delete_one({"_id": str(tg_id)})
                _clear_fails(ip)
                session.clear()
                session.permanent = True
                session["logged_in"] = True
                session["login"] = login_name
                session["login_ip"] = ip
                add_radar_log(f"🔐 Успешный вход в веб: {login_name} (2FA пройдена). IP {ip}")
                if tg_id != owner_id:
                    alert_owner(f"login:{tg_id}:{ip}", f"👁 В панель вошёл <b>{login_name}</b>\nIP: <code>{ip}</code>")
                return redirect(url_for("admin_panel"))

            blocked_now = _register_fail(ip)
            add_radar_log(f"⚠️ Неверный 2FA-код! IP {ip}")
            alert_owner(f"badcode:{tg_id}", f"🚨 <b>Пароль подошёл, но 2FA-код неверный!</b>\nЛогин: <code>{session.get('pre_auth_login')}</code>\nIP: <code>{ip}</code>\nПохоже, пароль утёк. Смени его.")
            error = "Неверный код"
        return render_template("2fa.html", error=error)

    @app.route("/glaz/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))
