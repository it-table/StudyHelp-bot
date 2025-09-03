import os
import sqlite3
import psycopg2
from flask import Flask, request, jsonify, send_from_directory
from datetime import datetime, timedelta
import requests

app = Flask(__name__)

# --- Config ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "your_bot_token")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "your_admin_chat_id")
DATABASE_URL = os.getenv("DATABASE_URL")  # Railway/Supabase передают в переменных окружения


# --- DB connection ---
def get_db_connection():
    database_url = DATABASE_URL

    if not database_url:
        # fallback на SQLite (локально без PostgreSQL)
        conn = sqlite3.connect("bookings.db")
        conn.row_factory = sqlite3.Row
        return conn

    # Fix для старых URL (Heroku-style)
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)

    # Гарантируем SSL (Supabase требует sslmode=require)
    if "sslmode=" not in database_url:
        sep = "&" if "?" in database_url else "?"
        database_url = f"{database_url}{sep}sslmode=require"

    return psycopg2.connect(database_url)


def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    if not DATABASE_URL:  # SQLite
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                time TEXT NOT NULL,
                name TEXT NOT NULL,
                service TEXT NOT NULL,
                comment TEXT,
                user_id TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    else:  # PostgreSQL (Supabase)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id SERIAL PRIMARY KEY,
                date TEXT NOT NULL,
                time TEXT NOT NULL,
                name TEXT NOT NULL,
                service TEXT NOT NULL,
                comment TEXT,
                user_id TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    conn.commit()
    conn.close()


init_db()


# --- Helpers ---
def send_telegram_message(chat_id, text):
    """Отправка сообщения в Telegram"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": chat_id, "text": text})
        return r.json()
    except Exception as e:
        print("Telegram error:", e)
        return None


def is_time_occupied(date, time, exclude_booking_id=None):
    """Проверяем, занято ли время"""
    conn = get_db_connection()
    cur = conn.cursor()
    if exclude_booking_id:
        cur.execute("SELECT id FROM bookings WHERE date=%s AND time=%s AND id!=%s",
                    (date, time, exclude_booking_id))
    else:
        cur.execute("SELECT id FROM bookings WHERE date=%s AND time=%s", (date, time))
    exists = cur.fetchone()
    conn.close()
    return bool(exists)


# --- API ---
@app.route("/api/bookings", methods=["POST"])
def create_booking():
    data = request.json
    date, time, name, service, comment, user_id = (
        data.get("date"), data.get("time"),
        data.get("name"), data.get("service"),
        data.get("comment", ""), data.get("user_id")
    )

    if not all([date, time, name, service, user_id]):
        return jsonify({"error": "Missing fields"}), 400

    # validate date
    selected_date = datetime.strptime(date, "%Y-%m-%d").date()
    today, max_date = datetime.today().date(), datetime.today().date() + timedelta(days=30)
    if selected_date < today or selected_date > max_date:
        return jsonify({"error": "Invalid date"}), 400

    if is_time_occupied(date, time):
        return jsonify({"error": "Time slot already booked"}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO bookings (date,time,name,service,comment,user_id) VALUES (%s,%s,%s,%s,%s,%s)",
        (date, time, name, service, comment, user_id)
    )
    conn.commit()
    booking_id = None
    if not DATABASE_URL:  # SQLite
        booking_id = cur.lastrowid
    conn.close()

    msg = f"Новая запись:\nДата: {date}\nВремя: {time}\nИмя: {name}\nУслуга: {service}\nКомментарий: {comment}"
    send_telegram_message(ADMIN_CHAT_ID, msg)

    return jsonify({"success": True, "booking_id": booking_id})


@app.route("/api/bookings/<user_id>", methods=["GET"])
def get_bookings(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id,date,time,name,service,comment FROM bookings WHERE user_id=%s ORDER BY date,time", (user_id,))
    rows = cur.fetchall()
    conn.close()

    # Обрабатываем по типу результата (SQLite vs PostgreSQL)
    if DATABASE_URL:
        keys = ["id", "date", "time", "name", "service", "comment"]
        result = [dict(zip(keys, row)) for row in rows]
    else:
        result = [dict(r) for r in rows]

    return jsonify(result)


@app.route("/api/update-booking", methods=["POST"])
def update_booking():
    data = request.json
    booking_id = data.get("id")
    if not booking_id:
        return jsonify({"error": "Booking ID required"}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT date,time,name,service,comment FROM bookings WHERE id=%s", (booking_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Booking not found"}), 404

    if DATABASE_URL:
        current_date, current_time, current_name, current_service, current_comment = row
    else:
        current_date, current_time, current_name, current_service, current_comment = row

    new_date = data.get("date", current_date)
    new_time = data.get("time", current_time)
    new_name = data.get("name", current_name)
    new_service = data.get("service", current_service)
    new_comment = data.get("comment", current_comment)

    if is_time_occupied(new_date, new_time, exclude_booking_id=booking_id):
        conn.close()
        return jsonify({"error": "Time slot already booked"}), 400

    cur.execute("UPDATE bookings SET date=%s,time=%s,name=%s,service=%s,comment=%s WHERE id=%s",
                (new_date, new_time, new_name, new_service, new_comment, booking_id))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/cancel-booking", methods=["POST"])
def cancel_booking():
    data = request.json
    booking_id = data.get("id")
    if not booking_id:
        return jsonify({"error": "Booking ID required"}), 400
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM bookings WHERE id=%s", (booking_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


# Serve static files
@app.route("/")
def index():
    return send_from_directory(".", "index.html")

@app.route("/<path:path>")
def serve_static(path):
    return send_from_directory(".", path)


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
