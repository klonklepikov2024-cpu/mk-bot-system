"""
core/diag.py — прозрачность для веб-панели (вкладка «🩺 Диагностика»).
  log_error  — «что сломалось»: ошибки задач, ответы Groq, спорные/упавшие оплаты.
  log_admin  — журнал действий админов веб-панели (кто, что, когда).
"""
import time
from database import db


def log_error(kind, text, uid=None):
    try:
        db['skynet_errors'].insert_one({"ts": time.time(), "kind": kind, "text": str(text)[:500], "uid": uid})
    except Exception:
        pass


def log_admin(login, text):
    try:
        db['admin_audit'].insert_one({"ts": time.time(), "login": login, "text": str(text)[:500]})
    except Exception:
        pass
