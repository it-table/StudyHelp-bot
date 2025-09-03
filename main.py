import os
from flask import Flask, request, jsonify, render_template, send_from_directory
import psycopg2
from datetime import datetime, time
import re

app = Flask(__name__)

# Serve static files
@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory('static', filename)

# Функция для получения подключения к PostgreSQL
def get_db_connection():
    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        raise Exception("DATABASE_URL environment variable is not set")
    
    return psycopg2.connect(database_url)

# Маршрут для главной страницы
@app.route('/')
def home():
    return render_template('index.html')

# Новый маршрут для создания бронирования (соответствует фронтенду)
@app.route('/api/book', methods=['POST'])
def create_booking():
    try:
        data = request.get_json()
        print("Received data:", data)
        
        # Проверяем структуру данных от фронтенда
        if 'user' not in data or 'booking' not in data:
            return jsonify({'success': False, 'error': 'Неверный формат данных'}), 400
        
        user_data = data['user']
        booking_data = data['booking']
        
        # Валидация обязательных полей
        required_fields = ['date', 'time', 'subject', 'service']
        for field in required_fields:
            if field not in booking_data or not str(booking_data[field]).strip():
                return jsonify({'success': False, 'error': f'Поле {field} обязательно'}), 400
        
        # Валидация формата времени
        if not re.match(r'^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$', booking_data['time']):
            return jsonify({'success': False, 'error': 'Неверный формат времени'}), 400
        
        # Подключение к БД и вставка данных
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("""
            INSERT INTO bookings (date, time, name, service, comment, user_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id, created_at
        """, (
            booking_data['date'],
            booking_data['time'],
            booking_data['subject'],  # subject -> name в БД
            booking_data['service'],
            booking_data.get('comment', ''),
            str(user_data['id'])  # user_id из Telegram
        ))
        
        result = cur.fetchone()
        conn.commit()
        
        cur.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'message': 'Бронирование успешно создано',
            'booking_id': result[0]
        }), 201
        
    except Exception as e:
        print(f"Error creating booking: {e}")
        return jsonify({'success': False, 'error': 'Ошибка при создании бронирования'}), 500

# Новый маршрут для получения бронирований (соответствует фронтенду)
@app.route('/api/user-bookings')
def get_user_bookings():
    try:
        user_id = request.args.get('user_id')
        print(f"Fetching bookings for user: {user_id}")
        
        if not user_id:
            return jsonify({'error': 'User ID is required'}), 400
        
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT id, date, time, name as subject, service, comment, created_at
            FROM bookings 
            WHERE user_id = %s 
            ORDER BY created_at DESC
        """, (user_id,))
        
        bookings = []
        for row in cur.fetchall():
            booking_date = datetime.strptime(row[1], '%Y-%m-%d').date() if isinstance(row[1], str) else row[1]
            is_past = booking_date < datetime.now().date()
            
            bookings.append({
                'id': row[0],
                'date': row[1],
                'time': row[2],
                'subject': row[3],  # name -> subject для фронтенда
                'service': row[4],
                'comment': row[5],
                'created_at': row[6].isoformat() if row[6] else None,
                'can_modify': not is_past
            })
        
        cur.close()
        conn.close()
        
        return jsonify(bookings)
        
    except Exception as e:
        print(f"Error fetching bookings: {e}")
        return jsonify({'error': 'Ошибка при получении бронирований'}), 500

# Новый маршрут для валидации времени
@app.route('/api/validate-time', methods=['POST'])
def validate_time():
    try:
        data = request.get_json()
        time_str = data.get('time', '')
        
        # Проверка формата времени
        if not re.match(r'^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$', time_str):
            return jsonify({'valid': False, 'error': 'Неверный формат времени. Используйте ЧЧ:MM'})
        
        # Проверка рабочего времени (9:00 - 21:00)
        hours, minutes = map(int, time_str.split(':'))
        if hours < 9 or hours >= 21:
            return jsonify({'valid': False, 'error': 'Время работы: с 9:00 до 21:00'})
        
        return jsonify({'valid': True})
        
    except Exception as e:
        return jsonify({'valid': False, 'error': 'Ошибка валидации времени'})

# Новый маршрут для обновления бронирования
@app.route('/api/update-booking', methods=['POST'])
def update_booking():
    try:
        data = request.get_json()
        print("Update booking data:", data)
        
        required_fields = ['booking_id', 'user_id', 'updates']
        for field in required_fields:
            if field not in data:
                return jsonify({'status': 'error', 'message': f'Отсутствует поле {field}'}), 400
        
        updates = data['updates']
        
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Проверяем, что бронирование принадлежит пользователю
        cur.execute("SELECT user_id FROM bookings WHERE id = %s", (data['booking_id'],))
        booking = cur.fetchone()
        
        if not booking:
            return jsonify({'status': 'error', 'message': 'Бронирование не найдено'}), 404
        
        if str(booking[0]) != str(data['user_id']):
            return jsonify({'status': 'error', 'message': 'Нет прав для редактирования'}), 403
        
        # Обновляем данные
        cur.execute("""
            UPDATE bookings 
            SET date = %s, time = %s, name = %s, service = %s, comment = %s
            WHERE id = %s
        """, (
            updates['date'],
            updates['time'],
            updates['subject'],
            updates['service'],
            updates.get('comment', ''),
            data['booking_id']
        ))
        
        conn.commit()
        cur.close()
        conn.close()
        
        return jsonify({'status': 'success', 'message': 'Бронирование обновлено'})
        
    except Exception as e:
        print(f"Error updating booking: {e}")
        return jsonify({'status': 'error', 'message': 'Ошибка при обновлении'}), 500

# Новый маршрут для отмены бронирования
@app.route('/api/cancel-booking', methods=['POST'])
def cancel_booking():
    try:
        data = request.get_json()
        print("Cancel booking data:", data)
        
        if 'booking_id' not in data or 'user_id' not in data:
            return jsonify({'status': 'error', 'message': 'Неверные данные'}), 400
        
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Проверяем принадлежность бронирования
        cur.execute("SELECT user_id FROM bookings WHERE id = %s", (data['booking_id'],))
        booking = cur.fetchone()
        
        if not booking:
            return jsonify({'status': 'error', 'message': 'Бронирование не найдено'}), 404
        
        if str(booking[0]) != str(data['user_id']):
            return jsonify({'status': 'error', 'message': 'Нет прав для отмены'}), 403
        
        cur.execute("DELETE FROM bookings WHERE id = %s", (data['booking_id'],))
        conn.commit()
        
        cur.close()
        conn.close()
        
        return jsonify({'status': 'success', 'message': 'Бронирование отменено'})
        
    except Exception as e:
        print(f"Error canceling booking: {e}")
        return jsonify({'status': 'error', 'message': 'Ошибка при отмене'}), 500

# Health check endpoint
@app.route('/health')
def health_check():
    try:
        conn = get_db_connection()
        conn.close()
        return jsonify({'status': 'healthy', 'database': 'connected'})
    except Exception as e:
        return jsonify({'status': 'unhealthy', 'database': 'disconnected', 'error': str(e)}), 500

# Обработка ошибок
@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Ресурс не найден'}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({'error': 'Внутренняя ошибка сервера'}), 500

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
