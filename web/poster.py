"""
web/poster.py — серверная часть мини-приложения публикации (Elite Poster).

Все запросы подписаны Telegram (initData). Анкета, разосланная в несколько чатов, — это одна
«публикация» (pub_id): по ней считаются отклики, её поднимают, правят и снимают целиком.

Коллекции:
  posts          — по документу на каждый чат (как раньше) + pub_id, text, media, header_idx,
                   text_msg_id, text_is_caption, replies, status
  post_replies   — {pub_id, chat_id, uid, ts}: события откликов (для графика по дням)
  poster_drafts  — черновики и шаблоны {uid, kind: draft|template, name, text, network, city, ts}
"""
import html as _html
import io
import random
import re
import threading
import time
import uuid
from datetime import datetime

from bson import ObjectId
from flask import jsonify, render_template, request
from pymongo.errors import DuplicateKeyError
from telebot import types

from database import db, users_collection, banned_collection

NETS = {"mk": "Мужской Клуб", "parni": "ПАРНИ 18+", "ns": "НС", "rainbow": "Радуга", "gayznak": "Гей Знакомства"}
NET_BY_NAME = {v: k for k, v in NETS.items()}
MAX_TEXT = 800
from core.cfg import cfg  # лимиты — в панели «🎛 Управление»

VIP_TOP = ('<tg-emoji emoji-id="5467688183229610037">👑</tg-emoji>'
           '<tg-emoji emoji-id="5467466378233543299">👑</tg-emoji>'
           '<tg-emoji emoji-id="5467630896955815565">👑</tg-emoji>\n\n')
VIP_BOTTOM = ('\n\n<tg-emoji emoji-id="5949582599012750373">✅</tg-emoji> <b>Анкета проверена администрацией сети</b>\n\n'
              '<tg-emoji emoji-id="6215039782955783886">🌟</tg-emoji> <b>Привилегированный участник</b> '
              '<tg-emoji emoji-id="6215039782955783886">🌟</tg-emoji>')
HEADERS = [
    "💎 VIP-РЕЗИДЕНТ на связи: {u} 💎",
    "👑 {u} заходит с козырей! Элитная анкета: 👑",
    "🏆 Эксклюзивный доступ: сообщение от {u}",
    "🎩 {u} знает себе цену. Читать внимательно:",
    "🔥 {u} забирает всё внимание чата на себя! 🔥",
    "⚡️ {u} не любит ждать. Читай и действуй:",
    "💥 Меньше слов, больше дела. Анкета от {u}:",
    "🚀 {u} нажал на газ! Кто составит компанию?",
    "🧿 У вас одно непрочитанное VIP-сообщение от {u}",
    "🤫 Только для своих: {u} ищет компанию.",
    "🎯 Внимание на экран! {u} в активном поиске:",
    "🚨 Срочный перехват эфира от {u}! 🚨",
    "✨ {u} бросает вызов одиночеству!",
    "⚡️ Молния! {u} только что опубликовал анкету:",
    "🌟 Привилегированный участник {u} на радаре:",
]


def bump_hours():
    lim = db['settings'].find_one({"_id": "moderation_limits"}) or {}
    try:
        return max(1, int(lim.get("bump_hours", 12)))
    except (TypeError, ValueError):
        return 12


def build_full_text(uid, name, text, header_idx):
    safe_name = _html.escape(str(name or "VIP"))
    user_html = f'<a href="tg://user?id={uid}">{safe_name}</a>'
    header = HEADERS[header_idx % len(HEADERS)].format(u=user_html)
    safe_text = _html.escape(str(text))
    return f"{VIP_TOP}{header}\n\n{safe_text}{VIP_BOTTOM}"


def respond_markup():
    return types.InlineKeyboardMarkup().add(types.InlineKeyboardButton(text="Откликнуться ♥", callback_data="respond"))


def register_poster_routes(app, bot):
    from config import TOKEN as BOT_TOKEN, VIP_CHAT_ID
    from core.guards import check_webapp_init_data

    # ---------- общие помощники ----------
    def webapp_user():
        body = request.get_json(silent=True) or {}
        init = (request.headers.get("X-Telegram-Init-Data") or request.form.get("init_data")
                or body.get("init_data") or "")
        u = check_webapp_init_data(init, BOT_TOKEN)
        return (int(u["id"]), u) if u else (None, None)

    def is_vip_now(uid):
        if banned_collection.find_one({"_id": uid}):
            return False
        doc = users_collection.find_one({"_id": uid}) or {}
        if doc.get("is_vip"):
            return True
        try:
            m = bot.get_chat_member(VIP_CHAT_ID, uid)
            return m.status in ("member", "administrator", "creator") or (m.status == "restricted" and getattr(m, "is_member", False))
        except Exception:
            return False

    def deny(msg, code):
        return jsonify({"success": False, "status": "error", "message": msg}), code

    def need_vip():
        uid, u = webapp_user()
        if not uid:
            return None, None, deny("Откройте приложение кнопкой из бота (подпись Telegram не прошла).", 401)
        if not is_vip_now(uid):
            return None, None, deny("Раздел доступен только VIP-участникам.", 403)
        return uid, u, None

    def target_chats(network, city):
        infra = db['settings'].find_one({"_id": "infrastructure"}) or {}
        nets = infra.get("networks", {})
        keys = list(NETS) if network == "Все сети" else [NET_BY_NAME.get(network)]
        out = {}
        for nk in keys:
            for item in nets.get(nk, []) or []:
                cid = item.get("id")
                if not cid:
                    continue
                clean = re.sub(r'\s*\d+$', '', str(item.get("name", ""))).strip()
                if clean == city or str(item.get("name")) == city:
                    out[int(cid)] = nk
        return out

    def send_to_chat(chat_id, media_items, full_text):
        """-> (ids, text_msg_id, text_is_caption)"""
        markup = respond_markup()
        if len(media_items) > 1:
            group = [types.InputMediaPhoto(m['id']) if m['type'] == 'photo' else types.InputMediaVideo(m['id']) for m in media_items]
            sent = bot.send_media_group(chat_id, group)
            t = bot.send_message(chat_id, full_text, parse_mode="HTML", reply_markup=markup)
            return [m.message_id for m in sent] + [t.message_id], t.message_id, False
        if len(media_items) == 1:
            m = media_items[0]
            send = bot.send_photo if m['type'] == 'photo' else bot.send_video
            s = send(chat_id, m['id'], caption=full_text, parse_mode="HTML", reply_markup=markup)
            return [s.message_id], s.message_id, True
        s = bot.send_message(chat_id, full_text, parse_mode="HTML", reply_markup=markup)
        return [s.message_id], s.message_id, False

    def pub_docs(uid, pub_id):
        if ObjectId.is_valid(pub_id):
            q = {"user_id": uid, "$or": [{"pub_id": pub_id}, {"_id": ObjectId(pub_id)}]}
        else:
            q = {"user_id": uid, "pub_id": pub_id}
        return list(db['posts'].find(q))

    # ---------- страница ----------
    @app.route('/mini_app_post')
    def mini_app_post():
        return render_template('create_post.html')

    @app.route('/api/get_cities_matrix', methods=['GET'])
    def api_get_cities_matrix():
        infra = db['settings'].find_one({"_id": "infrastructure"}) or {}
        networks = infra.get("networks", {})
        city_data = {}
        for net_key in NETS:
            city_data[net_key] = []
            for item in networks.get(net_key, []) or []:
                clean_name = re.sub(r'\s*\d+$', '', item.get("name", "")).strip()
                if clean_name and clean_name not in city_data[net_key]:
                    city_data[net_key].append(clean_name)
        return jsonify(city_data)

    @app.route('/api/poster/me', methods=['GET'])
    def api_poster_me():
        uid, u, err = need_vip()
        if err: return err
        return jsonify({"success": True, "name": u.get("first_name", "VIP"), "bump_hours": bump_hours()})

    # ---------- предпросмотр ----------
    @app.route('/api/poster/preview', methods=['POST'])
    def api_poster_preview():
        uid, u, err = need_vip()
        if err: return err
        data = request.get_json(silent=True) or {}
        text = str(data.get("text") or "").strip()[:MAX_TEXT]
        network, city = data.get("network"), data.get("city")
        idx = random.randrange(len(HEADERS))
        chats = target_chats(network, city) if network and city else {}
        nets = sorted({NETS[nk] for nk in chats.values()})
        return jsonify({"success": True, "header_idx": idx,
                        "html": build_full_text(uid, u.get("first_name", "VIP"), text, idx),
                        "chats": len(chats), "networks": nets})

    # ---------- публикация ----------
    @app.route('/api/submit_mini_app', methods=['POST'])
    def submit_mini_app():
        uid, u, err = need_vip()
        if err: return err
        text = (request.form.get('text') or '').strip()
        network = request.form.get('network')
        city = request.form.get('city')
        try:
            header_idx = int(request.form.get('header_idx', random.randrange(len(HEADERS))))
        except ValueError:
            header_idx = 0
        if not text or len(text) > MAX_TEXT:
            return deny(f"Текст пустой или длиннее {MAX_TEXT} символов.", 400)
        targets = target_chats(network, city)
        if not targets:
            return deny("Город не найден в выбранной сети.", 400)
        try:
            db['mini_app_rate'].insert_one({"_id": f"{uid}_{int(time.time() // (cfg('poster_cooldown_min') * 60))}"})
        except DuplicateKeyError:
            return deny(f"Подождите {cfg('poster_cooldown_min')} мин. перед следующей публикацией.", 429)

        name = u.get("first_name", "VIP")
        users_collection.update_one({"_id": uid}, {"$set": {"first_name": name}}, upsert=True)
        files = []
        for f in request.files.getlist('media')[:10]:
            fn = (f.filename or "").lower()
            files.append({"bytes": f.read(), "is_video": fn.endswith(('.mp4', '.mov', '.avi', '.mkv', '.webm'))})
        draft_id = request.form.get('draft_id')

        def work():
            media = []
            for f in files:
                try:
                    bio = io.BytesIO(f['bytes'])
                    if f['is_video']:
                        bio.name = "video.mp4"
                        msg = bot.send_video(uid, bio)
                        media.append({"type": "video", "id": msg.video.file_id})
                    else:
                        bio.name = "photo.jpg"
                        msg = bot.send_photo(uid, bio)
                        media.append({"type": "photo", "id": msg.photo[-1].file_id})
                    try: bot.delete_message(uid, msg.message_id)  # служебная загрузка — не мусорим в личке
                    except Exception: pass
                except Exception as e:
                    print(f"poster upload: {e}")
            full = build_full_text(uid, name, text, header_idx)
            pub_id = uuid.uuid4().hex[:12]
            ok = 0
            for chat_id, nk in targets.items():
                try:
                    ids, text_id, is_cap = send_to_chat(chat_id, media, full)
                    db['posts'].insert_one({
                        "user_id": uid, "pub_id": pub_id, "message_ids": ids, "chat_id": chat_id,
                        "time": datetime.now(), "city": city, "network": NETS[nk], "pub_network": network,
                        "text": text, "media": media, "header_idx": header_idx,
                        "text_msg_id": text_id, "text_is_caption": is_cap, "replies": 0, "status": "live",
                        "bumped_at": time.time(), "bump_count": 0,
                    })
                    ok += 1
                except Exception as e:
                    print(f"poster send {chat_id}: {e}")
                time.sleep(0.3)
            if draft_id and ObjectId.is_valid(draft_id):
                db['poster_drafts'].delete_one({"_id": ObjectId(draft_id), "uid": uid, "kind": "draft"})
            try: bot.send_message(uid, f"✅ Анкета опубликована в городе {city}: {ok} чатов.")
            except Exception: pass

        threading.Thread(target=work, daemon=True).start()
        return jsonify({"success": True, "status": "ok", "chats": len(targets)})

    # ---------- мои анкеты ----------
    @app.route('/api/get_user_posts', methods=['GET'])
    def api_get_user_posts():
        uid, _ = webapp_user()
        if not uid:
            return deny("Нет подписи Telegram", 401)
        now = time.time()
        cooldown = bump_hours() * 3600
        groups = {}
        for p in db['posts'].find({"user_id": uid}).sort("time", -1):
            key = p.get("pub_id") or str(p["_id"])
            g = groups.setdefault(key, {"id": key, "city": p.get("city"), "network": p.get("pub_network") or p.get("network"),
                                        "text": p.get("text", ""), "time": p.get("time"), "chats": [], "replies": 0,
                                        "bumped_at": p.get("bumped_at"), "bump_count": p.get("bump_count", 0),
                                        # старые анкеты тоже можно поднять (копированием) и править
                                        "can_edit": bool(p.get("text_msg_id") or p.get("message_ids")), "has_text": True})
            g["replies"] += int(p.get("replies", 0))
            g["chats"].append({"name": f"{p.get('network')} · {p.get('city')}", "replies": int(p.get("replies", 0)),
                               "status": p.get("status", "live")})
        out = []
        for g in groups.values():
            t = g.pop("time")
            ts = t.timestamp() if hasattr(t, "timestamp") else now
            g["age_hours"] = int((now - ts) // 3600)
            last = g.pop("bumped_at")
            last = ts if last is None else last
            left = int(cooldown - (now - last))
            g["can_bump"] = g["has_text"] and left <= 0
            g["bump_in_hours"] = max(0, -(-left // 3600)) if left > 0 else 0
            out.append(g)
        week = list(db['post_replies'].find({"owner": uid, "ts": {"$gt": now - 7 * 86400}}, {"_id": 0, "ts": 1}))
        return jsonify({"success": True, "posts": out, "replies_week": len(week),
                        "chats_total": sum(len(g["chats"]) for g in out),
                        "can_bump_count": sum(1 for g in out if g["can_bump"])})

    @app.route('/api/poster/stats', methods=['GET'])
    def api_poster_stats():
        uid, _ = webapp_user()
        if not uid:
            return deny("Нет подписи Telegram", 401)
        pub_id = request.args.get("id", "")
        now = time.time()
        days = []
        for i in range(6, -1, -1):
            start = now - (i + 1) * 86400
            n = db['post_replies'].count_documents({"owner": uid, "pub_id": pub_id, "ts": {"$gt": start, "$lte": start + 86400}})
            days.append({"label": datetime.fromtimestamp(start + 86400).strftime("%d.%m"), "n": n})
        return jsonify({"success": True, "days": days})

    @app.route('/api/poster/bump', methods=['POST'])
    def api_poster_bump():
        uid, u, err = need_vip()
        if err: return err
        pub_id = str((request.get_json(silent=True) or {}).get("id", ""))
        docs = pub_docs(uid, pub_id)
        if not docs:
            return deny("Анкета не найдена.", 404)
        cooldown = bump_hours() * 3600
        def _last(d):
            if d.get("bumped_at") is not None:
                return d["bumped_at"]
            return d["time"].timestamp() if hasattr(d.get("time"), "timestamp") else 0
        last = max(_last(d) for d in docs)
        if time.time() - last < cooldown:
            return deny(f"Поднимать можно раз в {bump_hours()} ч.", 429)
        # Атомарно занимаем поднятие (двойной тап / два устройства)
        claim = db['posts'].update_many({"_id": {"$in": [d["_id"] for d in docs]},
                                         "$or": [{"bumped_at": {"$lte": last}}, {"bumped_at": {"$exists": False}}]},
                                        {"$set": {"bumped_at": time.time()}})
        if claim.modified_count == 0:
            return deny("Уже поднимается.", 409)
        name = u.get("first_name", "VIP")

        def legacy_bump(d):
            """Анкета до обновления: ни текста, ни медиа в базе нет. Telegram умеет копировать
            сообщения бота — копируем пост как есть (альбом + текст) и удаляем старый."""
            chat, ids = d["chat_id"], [m for m in d.get("message_ids", []) if m]
            if not ids:
                raise ValueError("нет сообщений")
            new_ids = []
            if len(ids) > 1:
                new_ids += [m.message_id for m in bot.copy_messages(chat, chat, ids[:-1])]
            last = bot.copy_message(chat, chat, ids[-1], reply_markup=respond_markup())
            new_ids.append(last.message_id)
            for mid in ids:
                try: bot.delete_message(chat, mid)
                except Exception: pass
            db['posts'].update_one({"_id": d["_id"]}, {"$set": {"message_ids": new_ids, "text_msg_id": last.message_id, "status": "live"},
                                                       "$inc": {"bump_count": 1}})

        def work():
            for d in docs:
                if not d.get("text") or "media" not in d:
                    try:
                        legacy_bump(d)
                    except Exception as e:
                        es = str(e).lower()
                        db['posts'].update_one({"_id": d["_id"]}, {"$set": {"status": "gone" if "not found" in es else "error"}})
                        print(f"legacy bump {d['chat_id']}: {e}")
                    time.sleep(0.3)
                    continue
                for mid in d.get("message_ids", []):
                    try: bot.delete_message(d["chat_id"], mid)
                    except Exception: pass
                try:
                    full = build_full_text(uid, name, d["text"], d.get("header_idx", 0))
                    ids, text_id, is_cap = send_to_chat(d["chat_id"], d.get("media", []), full)
                    db['posts'].update_one({"_id": d["_id"]}, {"$set": {"message_ids": ids, "text_msg_id": text_id,
                                                                        "text_is_caption": is_cap, "status": "live"},
                                                               "$inc": {"bump_count": 1}})
                except Exception as e:
                    db['posts'].update_one({"_id": d["_id"]}, {"$set": {"status": "error"}})
                    print(f"bump {d['chat_id']}: {e}")
                time.sleep(0.3)

        threading.Thread(target=work, daemon=True).start()
        return jsonify({"success": True})

    @app.route('/api/poster/edit', methods=['POST'])
    def api_poster_edit():
        uid, u, err = need_vip()
        if err: return err
        data = request.get_json(silent=True) or {}
        text = str(data.get("text") or "").strip()
        if not text or len(text) > MAX_TEXT:
            return deny(f"Текст пустой или длиннее {MAX_TEXT} символов.", 400)
        docs = pub_docs(uid, str(data.get("id", "")))
        if not docs:
            return deny("Анкета не найдена.", 404)
        name = u.get("first_name", "VIP")
        done, gone = 0, 0
        for d in docs:
            # у старых анкет текст — последнее сообщение публикации (подпись к фото или отдельный текст)
            mid = d.get("text_msg_id") or ([m for m in d.get("message_ids", []) if m] or [None])[-1]
            if not mid:
                continue
            idx = d.get("header_idx", random.randrange(len(HEADERS)))
            full = build_full_text(uid, name, text, idx)
            try:
                is_cap = d.get("text_is_caption")
                if is_cap is None:
                    try:
                        bot.edit_message_text(full, d["chat_id"], mid, parse_mode="HTML", reply_markup=respond_markup())
                        is_cap = False
                    except Exception as e1:
                        if "no text" not in str(e1).lower() and "text is empty" not in str(e1).lower():
                            raise
                        bot.edit_message_caption(full, d["chat_id"], mid, parse_mode="HTML", reply_markup=respond_markup())
                        is_cap = True
                elif is_cap:
                    bot.edit_message_caption(full, d["chat_id"], mid, parse_mode="HTML", reply_markup=respond_markup())
                else:
                    bot.edit_message_text(full, d["chat_id"], mid, parse_mode="HTML", reply_markup=respond_markup())
                db['posts'].update_one({"_id": d["_id"]}, {"$set": {"text": text, "status": "live", "text_msg_id": mid,
                                                                    "text_is_caption": is_cap, "header_idx": idx}})
                done += 1
            except Exception as e:
                es = str(e).lower()
                if "not found" in es or "can't be edited" in es:
                    db['posts'].update_one({"_id": d["_id"]}, {"$set": {"status": "gone"}})
                    gone += 1
                elif "not modified" in es:
                    done += 1
        return jsonify({"success": True, "edited": done, "gone": gone})

    @app.route('/api/delete_post', methods=['POST'])
    def api_delete_post():
        uid, _ = webapp_user()
        if not uid:
            return deny("Нет подписи Telegram", 401)
        data = request.get_json(silent=True) or {}
        docs = pub_docs(uid, str(data.get("post_id") or data.get("id") or ""))
        if not docs:
            return jsonify({"success": False, "message": "Анкета не найдена"})
        for d in docs:
            for mid in d.get("message_ids", [d.get("message_id")]):
                if mid:
                    try: bot.delete_message(d["chat_id"], mid)
                    except Exception: pass
        db['posts'].delete_many({"_id": {"$in": [d["_id"] for d in docs]}})
        return jsonify({"success": True})

    @app.route('/api/delete_all_posts', methods=['POST'])
    def api_delete_all_posts():
        uid, _ = webapp_user()
        if not uid:
            return deny("Нет подписи Telegram", 401)
        posts = list(db['posts'].find({"user_id": uid}))
        for p in posts:
            for mid in p.get("message_ids", [p.get("message_id")]):
                if mid:
                    try: bot.delete_message(p["chat_id"], mid)
                    except Exception: pass
        db['posts'].delete_many({"user_id": uid})
        return jsonify({"success": True, "count": len({p.get('pub_id') or str(p['_id']) for p in posts})})

    # ---------- черновики и шаблоны ----------
    @app.route('/api/poster/drafts', methods=['GET'])
    def api_poster_drafts():
        uid, _ = webapp_user()
        if not uid:
            return deny("Нет подписи Telegram", 401)
        rows = []
        for d in db['poster_drafts'].find({"uid": uid}).sort("ts", -1):
            rows.append({"id": str(d["_id"]), "kind": d.get("kind"), "name": d.get("name", ""), "text": d.get("text", ""),
                         "network": d.get("network"), "city": d.get("city"), "ts": d.get("ts", 0)})
        return jsonify({"success": True, "items": rows, "max_templates": cfg("poster_max_templates")})

    @app.route('/api/poster/drafts/save', methods=['POST'])
    def api_poster_drafts_save():
        uid, _ = webapp_user()
        if not uid:
            return deny("Нет подписи Telegram", 401)
        d = request.get_json(silent=True) or {}
        kind = "template" if d.get("kind") == "template" else "draft"
        text = str(d.get("text") or "").strip()[:MAX_TEXT]
        if not text:
            return deny("Пустой текст сохранять не нужно.", 400)
        doc = {"uid": uid, "kind": kind, "text": text, "ts": time.time(),
               "name": str(d.get("name") or "").strip()[:30] or ("Шаблон" if kind == "template" else ""),
               "network": d.get("network") or None, "city": d.get("city") or None}
        did = d.get("id")
        if did and ObjectId.is_valid(did):
            db['poster_drafts'].update_one({"_id": ObjectId(did), "uid": uid}, {"$set": doc})
            return jsonify({"success": True, "id": did})
        limit = cfg("poster_max_templates") if kind == "template" else cfg("poster_max_drafts")
        if db['poster_drafts'].count_documents({"uid": uid, "kind": kind}) >= limit:
            if kind == "template":
                return deny(f"Шаблонов не больше {limit}. Удалите лишний.", 400)
            old = db['poster_drafts'].find_one({"uid": uid, "kind": "draft"}, sort=[("ts", 1)])
            if old: db['poster_drafts'].delete_one({"_id": old["_id"]})
        res = db['poster_drafts'].insert_one(doc)
        return jsonify({"success": True, "id": str(res.inserted_id)})

    @app.route('/api/poster/drafts/delete', methods=['POST'])
    def api_poster_drafts_delete():
        uid, _ = webapp_user()
        if not uid:
            return deny("Нет подписи Telegram", 401)
        did = str((request.get_json(silent=True) or {}).get("id", ""))
        if ObjectId.is_valid(did):
            db['poster_drafts'].delete_one({"_id": ObjectId(did), "uid": uid})
        return jsonify({"success": True})
