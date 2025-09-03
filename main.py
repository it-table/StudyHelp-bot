from flask import Flask, render_template, request, jsonify
import requests
from datetime import datetime, timedelta
import os
import psycopg2
from flask_cors import CORS
from threading import Lock
from urllib.parse import urlparse
import json

app = Flask(__name__, template_folder='.')
CORS(app)

# Блокировка для избежания конфликтов
booking_lock = Lock()

# Инициализация базы данных
def get_db_connection():
    database_url = os.environ.get('DATABASE_URL')
    
    if database_url:
        if database_url.startswith('postgres://'):
            database_url = database_url.replace('postgres://', 'postgresql://')
        
        parsed_url = urlparse(database_url)
        conn = psycopg2.connect(
            database=parsed_url.path[1:],
            user=parsed_url.username,
            password=parsed_url.password,
            host=parsed_url.hostname,
            port=parsed_url.port
        )
        return conn
    else:
        import sqlite3
        return sqlite3.connect('bookings.db')

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS bookings (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            first_name TEXT,
            last_name TEXT,
            username TEXT,
            subject TEXT NOT NULL,
            service TEXT NOT NULL,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            comment TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    c.execute('''
        CREATE INDEX IF NOT EXISTS idx_user_id ON bookings (user_id);
        CREATE INDEX IF NOT EXISTS idx_date_time ON bookings (date, time);
        CREATE INDEX IF NOT EXISTS idx_created_at ON bookings (created_at);
    ''')
    
    conn.commit()
    conn.close()

init_db()

# Конфигурация бота
BOT_TOKEN = os.environ.get('BOT_TOKEN', 'YOUR_BOT_TOKEN')
ADMIN_CHAT_ID = os.environ.get('ADMIN_CHAT_ID', 'ADMIN_CHAT_ID')

@app.route("/")
def web():
    return render_template('index.html')

# ------------------ WEBHOOK ------------------ #
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        update = request.get_json()
        print("Получен апдейт:", json.dumps(update, ensure_ascii=False, indent=2))

        if "message" in update:
            message = update["message"]
            chat_id = message["chat"]["id"]

            # Если это web_app_data
            if "web_app_data" in message:
                try:
                    data = json.loads(message["web_app_data"]["data"])
                    user_data = {
                        "id": message["from"]["id"],
                        "first_name": message["from"].get("first_name", ""),
                        "last_name": message["from"].get("last_name", ""),
                        "username": message["from"].get("username", "")
                    }
                    booking_data = data.get("booking", {})

                    # Сохраняем в базу
                    save_booking_to_db(user_data, booking_data)

                    send_telegram_message(
                        chat_id,
                        f"✅ Запись успешно создана!\n"
                        f"📚 {booking_data.get('subject')}\n"
                        f"📅 {format_date(booking_data.get('date'))}\n"
                        f"⏰ {booking_data.get('time')}\n"
                        f"💬 {booking_data.get('comment','нет')}"
                    )
                except Exception as e:
                    print("Ошибка при обработке web_app_data:", e)
                    send_telegram_message(chat_id, "❌ Ошибка при сохранении записи.")
        
        return jsonify({"ok": True})
    except Exception as e:
        print("Ошибка в webhook:", e)
        return jsonify({"ok": False}), 500
# ------------------ END WEBHOOK ------------------ #

# ---------- Остальной API (без изменений) ---------- #
@app.route("/api/available-times", methods=['GET'])
def get_available_times():
    try:
        date_str = request.args.get('date')
        if not date_str:
            return jsonify({'error': 'Date parameter is required'}), 400
        
        selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        today = datetime.now().date()
        
        if selected_date < today:
            return jsonify({'times': []})
        
        if selected_date > today + timedelta(days=30):
            return jsonify({'times': [], 'message': 'Запись доступна только на 30 дней вперед'})
        
        occupied_times = get_occupied_times(date_str)
        
        all_times = []
        start_time = 9
        end_time = 18
        
        current_time = datetime.now()
        is_today = selected_date == today
        
        for hour in range(start_time, end_time + 1):
            time_str = f"{hour:02d}:00"
            if is_today:
                time_obj = datetime.strptime(time_str, '%H:%M').time()
                if current_time.time() > time_obj:
                    continue
            if time_str in occupied_times:
                continue
            all_times.append(time_str)
        
        return jsonify({'times': all_times})
        
    except ValueError:
        return jsonify({'error': 'Invalid date format'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def get_occupied_times(date_str):
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT time FROM bookings WHERE date = %s", (date_str,))
        occupied_times = [row[0] for row in c.fetchall()]
        conn.close()
        return occupied_times
    except Exception as e:
        print(f"Database error: {e}")
        return []

def save_booking_to_db(user_data, booking_data):
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute(
            "INSERT INTO bookings (user_id, first_name, last_name, username, subject, service, date, time, comment) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                user_data.get('id'),
                user_data.get('first_name', ''),
                user_data.get('last_name', ''),
                user_data.get('username', ''),
                booking_data['subject'],
                booking_data.get('service', 'Консультация'),
                booking_data['date'],
                booking_data['time'],
                booking_data.get('comment', '')
            )
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Database error: {e}")
        raise e

def format_date(date_str):
    try:
        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        return date_obj.strftime('%d.%m.%Y')
    except:
        return date_str

def send_telegram_message(chat_id, text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'}
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"Error sending Telegram message: {e}")
        return False

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
