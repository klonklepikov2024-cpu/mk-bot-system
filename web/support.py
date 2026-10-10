from flask import request, jsonify, session
from database import db
from telebot import types  # <--- Добавили библиотеку для кнопок

def register_support_routes(app, bot, add_radar_log):
    
    @app.route('/glaz/api/tickets', methods=['GET'])
    def api_get_tickets():
        if not session.get('logged_in'): return jsonify({"error": "Unauthorized"}), 401
        
        pipeline = [
            {"$match": {"is_closed": {"$ne": True}}},
            {"$sort": {"timestamp": -1}}, 
            {"$group": {
                "_id": "$uid",
                "uid": {"$first": "$uid"},
                "name": {"$first": "$name"},
                "username": {"$first": "$username"},
                "text": {"$first": "$text"},
                "timestamp": {"$first": "$timestamp"},
                "is_answered": {"$first": "$is_answered"}
            }},
            {"$sort": {"timestamp": -1}} 
        ]
        tickets = list(db['support_tickets'].aggregate(pipeline))
        return jsonify(tickets)

    @app.route('/glaz/api/tickets/history', methods=['GET'])
    def api_get_ticket_history():
        if not session.get('logged_in'): return jsonify({"error": "Unauthorized"}), 401
        uid = request.args.get('uid')
        if not uid: return jsonify([])
        
        history = list(db['support_tickets'].find({"uid": int(uid)}).sort("timestamp", 1))
        for h in history: h.pop('_id', None) 
        return jsonify(history)

    @app.route('/glaz/api/tickets/reply', methods=['POST'])
    def api_reply_ticket():
        if not session.get('logged_in'): return jsonify({"success": False}), 401
        data = request.json
        uid = data.get('uid')
        text = data.get('text')
        
        # Получаем кнопки с веб-панели (если их нет, будет пустой список)
        buttons_raw = data.get('buttons', [])
        
        # Собираем клавиатуру с кнопками
        markup = None
        if buttons_raw:
            markup = types.InlineKeyboardMarkup(row_width=1)
            for btn in buttons_raw:
                markup.add(types.InlineKeyboardButton(text=btn["text"], url=btn["url"]))
        
        try:
            bot.send_message(
                int(uid), 
                f"👨‍💻 <b>Ответ Службы Поддержки:</b>\n\n{text}", 
                parse_mode="HTML",   # <--- ГЛАВНОЕ: Перевели на HTML!
                reply_markup=markup  # <--- Прикрепили кнопки
            )
            db['support_tickets'].update_many(
                {"uid": int(uid), "is_answered": False},
                {"$set": {"is_answered": True, "reply_text": text}}
            )
            add_radar_log(f"✅ Отправлен ответ юзеру {uid}")
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

    @app.route('/glaz/api/tickets/close', methods=['POST'])
    def api_close_ticket():
        if not session.get('logged_in'): return jsonify({"success": False}), 401
        data = request.json
        uid = data.get('uid')
        
        db['support_tickets'].update_many(
            {"uid": int(uid)},
            {"$set": {"is_closed": True}}
        )
        
        try:
            bot.send_message(
                int(uid), 
                "✅ **Ваш запрос в Поддержку успешно решен и закрыт.**\n\nЕсли у вас возникнут новые вопросы — просто нажмите кнопку **«💬 Написать в Поддержку»** в меню бота, и мы снова будем на связи!", 
                parse_mode="Markdown"
            )
        except: pass
        
        add_radar_log(f"🧹 Все тикеты юзера {uid} закрыты и перемещены в архив.")
        return jsonify({"success": True})