from flask import Flask, render_template, request, jsonify, send_from_directory
import requests
from datetime import datetime, timedelta
import os
import psycopg2
from flask_cors import CORS
from threading import Lock
from urllib.parse import urlparse
import sqlite3  # Добавлен импорт для SQLite

app = Flask(__name__, template_folder='.')
CORS(app)

# --- Config ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "your_bot_token")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "your_admin_chat_id")
DATABASE_URL = os.getenv("DATABASE_URL")  # если пусто → используем SQLite

# Блокировка для избежания конфликтов
booking_lock = Lock()

# --- DB connection ---
def get_db_connection():
    if DATABASE_URL:  # PostgreSQL
        # Heroku-style fix
        database_url = DATABASE_URL
        if database_url.startswith('postgres://'):
            database_url = database_url.replace('postgres://', 'postgresql://', 1)
        parsed_url = urlparse(database_url)
        conn = psycopg2.connect(
            database=parsed_url.path[1:],
            user=parsed_url.username,
            password=parsed_url.password,
            host=parsed_url.hostname,
            port=parsed_url.port
        )
        return conn
    else:  # SQLite
        conn = sqlite3.connect("bookings.db")
        conn.row_factory = sqlite3.Row
        return conn

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()

    # Универсальное создание таблицы
    if DATABASE_URL:  # PostgreSQL
        cur.execute("""
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
        """)
    else:  # SQLite
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
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
        """)

    # Создание индексов
    cur.execute("CREATE INDEX IF NOT EXISTS idx_user_id ON bookings (user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_date_time ON bookings (date, time)")

    conn.commit()
    conn.close()

init_db()

# --- Helpers ---
def send_telegram_message(chat_id, text):
    """Отправка сообщения в Telegram"""
    if not BOT_TOKEN or not chat_id:
        print("Cannot send message: missing BOT_TOKEN or chat_id")
        return None

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        response = requests.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        }, timeout=10)
        return response.json()
    except Exception as e:
        print("Telegram error:", e)
        return None

def is_time_occupied(date, time, exclude_booking_id=None):
    """Проверяем, занято ли время"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        if DATABASE_URL:  # PostgreSQL
            if exclude_booking_id:
                cur.execute("SELECT id FROM bookings WHERE date = %s AND time = %s AND id != %s",
                           (date, time, exclude_booking_id))
            else:
                cur.execute("SELECT id FROM bookings WHERE date = %s AND time = %s", (date, time))
        else:  # SQLite
            if exclude_booking_id:
                cur.execute("SELECT id FROM bookings WHERE date=? AND time=? AND id!=?",
                           (date, time, exclude_booking_id))
            else:
                cur.execute("SELECT id FROM bookings WHERE date=? AND time=?", (date, time))

        exists = cur.fetchone()
        conn.close()
        return bool(exists)
    except Exception as e:
        print(f"Database error in is_time_occupied: {e}")
        return False

def format_date(date_str):
    """Форматирование даты"""
    try:
        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        return date_obj.strftime('%d.%m.%Y')
    except:
        return date_str

# --- API Routes ---
@app.route('/health')
def health():
    return 'ok', 200

# ... (остальной код без изменений) ...

@app.route('/api/available-times', methods=['GET'])
def get_available_times():
    """Теперь просто валидирует дату, время вводится свободно"""
    try:
        date_str = request.args.get('date')
        if not date_str:
            return jsonify({'error': 'Date parameter is required'}), 400

        # Валидация даты
        try:
            selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            today = datetime.now().date()

            if selected_date < today:
                return jsonify({'valid': False, 'message': 'Нельзя выбрать прошедшую дату'})

            if selected_date > today + timedelta(days=30):
                return jsonify({'valid': False, 'message': 'Запись доступна только на 30 дней вперед'})

        except ValueError:
            return jsonify({'error': 'Invalid date format'}), 400

        # Возвращаем успешную валидацию даты
        return jsonify({'valid': True, 'message': 'Date is valid'})

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route("/api/validate-time", methods=["POST"])
def validate_time():
    """Валидация времени"""
    try:
        data = request.json
        time_str = data.get('time')
        date_str = data.get('date')

        if not time_str:
            return jsonify({'valid': False, 'error': 'Введите время'})

        # Проверка формата времени
        try:
            datetime.strptime(time_str, '%H:%M')
        except ValueError:
            return jsonify({'valid': False, 'error': 'Неверный формат времени. Используйте ЧЧ:MM'})

        # Для сегодняшней даты проверяем чтобы время не было в прошлом
        if date_str:
            try:
                selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                today = datetime.now().date()

                if selected_date == today:
                    current_time = datetime.now().time()
                    selected_time = datetime.strptime(time_str, '%H:%M').time()

                    if selected_time <= current_time:
                        return jsonify({'valid': False, 'error': 'Нельзя выбрать прошедшее время'})
            except:
                pass

        return jsonify({'valid': True, 'message': 'Time is valid'})

    except Exception as e:
        return jsonify({'valid': False, 'error': str(e)})

@app.route("/api/book", methods=["POST"])
def book_service():
    """Создание бронирования со свободным вводом времени"""
    try:
        data = request.json
        user_data = data.get('user', {})
        booking_data = data.get('booking', {})

        subject = booking_data.get("subject")
        service = booking_data.get("service")
        date = booking_data.get("date")
        time = booking_data.get("time")
        comment = booking_data.get("comment", "")
        user_id = user_data.get("id")

        if not all([subject, service, date, time, user_id]):
            return jsonify({"error": "Заполните все обязательные поля"}), 400

        # Validate date
        try:
            selected_date = datetime.strptime(date, "%Y-%m-%d").date()
            today = datetime.now().date()
            if selected_date < today:
                return jsonify({"error": "Нельзя выбрать прошедшую дату"}), 400
            if selected_date > today + timedelta(days=30):
                return jsonify({"error": "Запись доступна только на 30 дней вперед"}), 400
        except ValueError:
            return jsonify({"error": "Неверный формат даты"}), 400

        # Validate time format
        try:
            datetime.strptime(time, "%H:%M")
        except ValueError:
            return jsonify({"error": "Неверный формат времени. Используйте ЧЧ:MM"}), 400

        # Для сегодняшней даты проверяем чтобы время не было в прошлом
        selected_date = datetime.strptime(date, "%Y-%m-%d").date()
        today = datetime.now().date()
        if selected_date == today:
            current_time = datetime.now().time()
            selected_time = datetime.strptime(time, "%H:%M").time()
            if selected_time <= current_time:
                return jsonify({"error": "Нельзя выбрать прошедшее время"}), 400

        # Save to database (БЕЗ проверки занятости!)
        conn = get_db_connection()
        cur = conn.cursor()

        if DATABASE_URL:
            cur.execute("""
                INSERT INTO bookings (user_id, first_name, last_name, username, subject, service, date, time, comment)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                user_id,
                user_data.get('first_name', ''),
                user_data.get('last_name', ''),
                user_data.get('username', ''),
                subject,
                service,
                date,
                time,
                comment
            ))
        else:
            cur.execute("""
                INSERT INTO bookings (user_id, first_name, last_name, username, subject, service, date, time, comment)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                user_id,
                user_data.get('first_name', ''),
                user_data.get('last_name', ''),
                user_data.get('username', ''),
                subject,
                service,
                date,
                time,
                comment
            ))

        conn.commit()
        conn.close()

        # Send notifications
        user_msg = f"""✅ Вы успешно записались на услугу!
📚 Предмет: {subject}
📅 Дата: {format_date(date)}
⏰ Время: {time}
📋 Услуга: {service}
💬 Комментарий: {comment or 'нет'}"""

        admin_msg = f"""🎉 Новая запись!
👤 Пользователь: {user_data.get('first_name', '')} {user_data.get('last_name', '')}
📞 Username: @{user_data.get('username', 'нет')}
📚 Предмет: {subject}
📅 Дата: {format_date(date)}
⏰ Время: {time}
📋 Услуга: {service}
📝 Комментарий: {comment or 'нет'}"""

        send_telegram_message(user_id, user_msg)
        if ADMIN_CHAT_ID:
            send_telegram_message(ADMIN_CHAT_ID, admin_msg)

        return jsonify({"success": True, "message": "Запись успешно создана"})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# Serve static files
@app.route("/")
def index():
    return send_from_directory(".", "index.html")

@app.route('/<path:path>')
def serve_static(path):
    return send_from_directory('.', path)

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
