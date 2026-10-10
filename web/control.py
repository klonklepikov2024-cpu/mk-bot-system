"""Вкладка «🎛 Управление»: настройки ботов без правки кода и деплоя (реестр — core/config_schema.py)."""
from flask import jsonify, request, session

from database import db
from core.config_schema import SCHEMA, BY_KEY, GROUP_ORDER
from core.cfg import coerce, invalidate


def register_control_routes(app, add_radar_log):
    from web_auth import is_owner

    def visible(e):
        return is_owner() or not e.get("owner")

    @app.route('/glaz/api/config')
    def api_config():
        if not session.get('logged_in'): return jsonify({"error": "Unauthorized"}), 401
        docs = {}
        groups = {g: [] for g in GROUP_ORDER}
        for e in SCHEMA:
            if not visible(e):
                continue
            doc = e.get("doc", "config")
            if doc not in docs:
                docs[doc] = db['settings'].find_one({"_id": doc}) or {}
            val = docs[doc].get(e["key"])
            groups.setdefault(e["group"], []).append({
                "key": e["key"], "label": e["label"], "type": e["type"], "help": e.get("help", ""),
                "default": e["default"], "value": e["default"] if val is None or val == "" else val,
                "changed": val is not None and val != "" and val != e["default"],
                "min": e.get("min"), "max": e.get("max"), "bots": e.get("bots", ""),
            })
        return jsonify([{"group": g, "items": items} for g, items in groups.items() if items])

    @app.route('/glaz/api/config/save', methods=['POST'])
    def api_config_save():
        if not session.get('logged_in'): return jsonify({"success": False, "error": "Unauthorized"}), 401
        data = (request.get_json(silent=True) or {}).get("values", {})
        updates, errors, log = {}, {}, []
        for key, raw in data.items():
            e = BY_KEY.get(key)
            if not e or not visible(e):
                errors[key] = "нет доступа"
                continue
            try:
                if raw is None or (isinstance(raw, str) and raw.strip() == ""):
                    v = None  # сброс к значению по умолчанию
                else:
                    v = coerce(e, raw)
            except (TypeError, ValueError) as ex:
                errors[key] = str(ex) or "неверное значение"
                continue
            updates.setdefault(e.get("doc", "config"), {})[key] = v
            log.append(f"{e['label']} → {e['default'] if v is None else v}")
        if errors:
            return jsonify({"success": False, "errors": errors})
        for doc, vals in updates.items():
            sets = {k: v for k, v in vals.items() if v is not None}
            unsets = {k: "" for k, v in vals.items() if v is None}
            upd = {}
            if sets: upd["$set"] = sets
            if unsets: upd["$unset"] = unsets
            if upd:
                db['settings'].update_one({"_id": doc}, upd, upsert=True)
        invalidate()
        for line in log:
            add_radar_log(f"🎛 Настройка: {line}")
        return jsonify({"success": True, "saved": len(log)})
