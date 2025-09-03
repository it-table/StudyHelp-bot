from flask import Flask, render_template, request, jsonify, url_for
import requests
from datetime import datetime, timedelta
import os
import psycopg2
from flask_cors import CORS
from threading import Lock
from urllib.parse import urlparse

app = Flask(__name__)
CORS(app)

# Блокировка для избежания конфликтов
booking_lock = Lock()

# --- DB CONNECTION ---
def get_db_connection():
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://")
        parsed_url = urlparse(database_url)
        conn = psycopg2.connect(
            database=parsed_url.path[1:],
            user=parsed_url.username,
            password=parsed_url.password,
            host=parsed_url.hostname,
            port=parsed_url.port,
        )
        return conn
    else:
        raise RuntimeError("DATABASE_URL not set")


# --- DB INIT ---
# --- DB INIT ---
def init_db():
    # Просто проверяем соединение с базой
    try:
        conn = get_db_connection()
        conn.close()
        print("Database connection successful")
    except Exception as e:
        print(f"Database connection error: {e}")
        raise

# Уберите вызов init_db() если он вызывает проблемы
# init_db()


# --- CONFIG ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")


# --- ROUTES ---
@app.route("/")
def web():
    return render_template("index.html")


@app.route("/webhook", methods=["POST"])
def webhook():
    """Обработчик апдейтов от Telegram"""
    update = request.get_json(force=True)

    if not update:
        return jsonify({"ok": False, "error": "No update"}), 400

    try:
        if "message" in update:
            chat_id = update["message"]["chat"]["id"]
            text = update["message"].get("text", "")

            # стандартный старт
            if text == "/start":
                send_telegram_message(chat_id, "Привет! Я бот StudyHelp 🚀")

            # данные из WebApp
            if "web_app_data" in update["message"]:
                data = update["message"]["web_app_data"]["data"]
                send_telegram_message(chat_id, f"📩 Данные из WebApp: {data}")

        return jsonify({"ok": True})
    except Exception as e:
        print(f"Webhook error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/available-times", methods=["GET"])
def get_available_times():
    try:
        date_str = request.args.get("date")
        if not date_str:
            return jsonify({"error": "Date parameter is required"}), 400

        selected_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        today = datetime.now().date()

        if selected_date < today:
            return jsonify({"times": []})

        if selected_date > today + timedelta(days=30):
            return jsonify({"times": [], "message": "Запись доступна только на 30 дней вперед"})

        occupied_times = get_occupied_times(date_str)

        all_times = []
        start_time = 9
        end_time = 18
        current_time = datetime.now()
        is_today = selected_date == today

        for hour in range(start_time, end_time + 1):
            time_str = f"{hour:02d}:00"
            if is_today:
                time_obj = datetime.strptime(time_str, "%H:%M").time()
                if current_time.time() > time_obj:
                    continue
            if time_str in occupied_times:
                continue
            all_times.append(time_str)

        return jsonify({"times": all_times})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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


@app.route("/api/validate-booking", methods=["POST"])
def validate_booking():
    try:
        data = request.json
        booking_data = data.get("booking")

        if not booking_data.get("subject"):
            return jsonify({"valid": False, "error": "Укажите предмет помощи"})
        if not booking_data.get("date"):
            return jsonify({"valid": False, "error": "Выберите дату"})
        if not booking_data.get("time"):
            return jsonify({"valid": False, "error": "Выберите время"})

        selected_date = datetime.strptime(booking_data["date"], "%Y-%m-%d").date()
        today = datetime.now().date()
        if selected_date < today:
            return jsonify({"valid": False, "error": "Нельзя выбрать прошедшую дату"})
        if selected_date > today + timedelta(days=30):
            return jsonify({"valid": False, "error": "Запись доступна только на 30 дней вперед"})

        datetime.strptime(booking_data["time"], "%H:%M")

        if selected_date == today:
            current_time = datetime.now().time()
            selected_time = datetime.strptime(booking_data["time"], "%H:%M").time()
            if selected_time <= current_time:
                return jsonify({"valid": False, "error": "Нельзя выбрать прошедшее время"})

        if is_time_occupied(booking_data["date"], booking_data["time"]):
            return jsonify({"valid": False, "error": "Это время уже занято"})

        return jsonify({"valid": True})
    except Exception as e:
        return jsonify({"valid": False, "error": str(e)})


def is_time_occupied(date, time, exclude_booking_id=None):
    try:
        conn = get_db_connection()
        c = conn.cursor()
        if exclude_booking_id:
            c.execute(
                "SELECT COUNT(*) FROM bookings WHERE date = %s AND time = %s AND id != %s",
                (date, time, exclude_booking_id),
            )
        else:
            c.execute("SELECT COUNT(*) FROM bookings WHERE date = %s AND time = %s", (date, time))
        count = c.fetchone()[0]
        conn.close()
        return count > 0
    except Exception as e:
        print(f"Database error: {e}")
        return False


@app.route("/api/book", methods=["POST"])
def book_service():
    try:
        data = request.json
        user_data = data.get("user")
        booking_data = data.get("booking")

        with booking_lock:
            if is_time_occupied(booking_data["date"], booking_data["time"]):
                return jsonify({"status": "error", "message": "Это время уже занято"}), 400

            save_booking_to_db(user_data, booking_data)

        send_telegram_message(
            user_data["id"],
            f"✅ Запись создана!\n📚 {booking_data['subject']}\n📅 {booking_data['date']} ⏰ {booking_data['time']}",
        )

        send_telegram_message(
            ADMIN_CHAT_ID,
            f"🎉 Новая запись!\n👤 {user_data.get('first_name','')} {user_data.get('last_name','')}\n📚 {booking_data['subject']} ⏰ {booking_data['time']}",
        )

        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


def save_booking_to_db(user_data, booking_data):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO bookings (user_id, first_name, last_name, username, subject, service, date, time, comment)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            user_data.get("id"),
            user_data.get("first_name", ""),
            user_data.get("last_name", ""),
            user_data.get("username", ""),
            booking_data["subject"],
            booking_data.get("service", "Консультация"),
            booking_data["date"],
            booking_data["time"],
            booking_data.get("comment", ""),
        ),
    )
    conn.commit()
    conn.close()


@app.route("/api/user-bookings", methods=["GET"])
def get_user_bookings():
    try:
        user_id = request.args.get("user_id")
        if not user_id:
            return jsonify({"error": "User ID required"}), 400

        conn = get_db_connection()
        c = conn.cursor()
        c.execute(
            """
            SELECT id, subject, service, date, time, comment, created_at
            FROM bookings WHERE user_id = %s
            ORDER BY date DESC, time DESC
            """,
            (user_id,),
        )
        rows = c.fetchall()
        conn.close()

        bookings = []
        today = datetime.now().date()
        for row in rows:
            try:
                booking_date = datetime.strptime(row[3], "%Y-%m-%d").date()
                can_modify = booking_date >= today
            except:
                can_modify = False
            bookings.append(
                {
                    "id": row[0],
                    "subject": row[1],
                    "service": row[2],
                    "date": row[3],
                    "time": row[4],
                    "comment": row[5],
                    "created_at": str(row[6]),
                    "can_modify": can_modify,
                }
            )

        return jsonify({"bookings": bookings})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/update-booking", methods=["POST"])
def update_booking():
    return jsonify({"status": "error", "message": "Not implemented"}), 501


@app.route("/api/cancel-booking", methods=["POST"])
def cancel_booking():
    return jsonify({"status": "error", "message": "Not implemented"}), 501


# --- HELPERS ---
def send_telegram_message(chat_id, text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Telegram send error: {e}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
