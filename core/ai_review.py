"""
core/ai_review.py — ручное решение, когда ИИ-проверка недоступна.

Раньше в админку приходила одна кнопка «ЗАБАНИТЬ ВЕЗДЕ», которая банила с причиной
«Радар Твинков (Клон забаненной анкеты)» — даже за оранжевую зону, где положен мут.
Теперь дело сохраняется в базе (настоящая причина, улика, что просил Шпион), а у админа
три кнопки: забанить, замутить, «не нарушение». Двойное нажатие не сработает.
"""
import time

from bson import ObjectId
from telebot import types

from database import db

ZONE_REASONS = {
    "black": ("ban", "Черная зона: Несовершеннолетний (<18)"),
    "orange": ("mute", "Оранжевая зона: Возраст 18-21"),
    "yellow": ("mute", "Желтая зона: Коммерция"),
}


def create_review(uid, action, reason, trigger_text="", origin_chat="", duration=0, user_link=None, source="Скайнет"):
    """-> разметка с кнопками для сообщения в админку."""
    doc = {"uid": int(uid), "action": action, "reason": reason, "trigger_text": str(trigger_text or "")[:1500],
           "origin_chat": origin_chat or "", "duration": int(duration or 0), "user_link": user_link,
           "source": source, "status": "pending", "ts": time.time()}
    rid = str(db['ai_reviews'].insert_one(doc).inserted_id)
    mk = types.InlineKeyboardMarkup(row_width=2)
    ban_btn = types.InlineKeyboardButton("🔨 Забанить", callback_data=f"aire_b_{rid}")
    mute_btn = types.InlineKeyboardButton("🔇 Замутить", callback_data=f"aire_m_{rid}")
    # Первой идёт кнопка того наказания, которое положено по зоне / просил Шпион
    mk.add(*([ban_btn, mute_btn] if action == "ban" else [mute_btn, ban_btn]))
    mk.add(types.InlineKeyboardButton("✅ Не нарушение", callback_data=f"aire_x_{rid}"))
    return mk


def claim_review(rid, decision, admin):
    """Атомарно забирает дело. None — уже решено другим админом или не найдено."""
    try:
        oid = ObjectId(rid)
    except Exception:
        return None
    return db['ai_reviews'].find_one_and_update(
        {"_id": oid, "status": "pending"},
        {"$set": {"status": decision, "decided_by": admin, "decided_at": time.time()}})
