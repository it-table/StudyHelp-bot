import os
from flask import Flask, request, jsonify, render_template
import psycopg2
from datetime import datetime

app = Flask(__name__)

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

# Маршрут для создания бронирования
@app.route('/api/bookings', methods=['POST'])
def create_booking():
    try:
        data = request.get_json()
        
        # Валидация обязательных полей
        required_fields = ['date', 'time', 'name', 'service', 'user_id']
        for field in required_fields:
            if field not in data or not data[field].strip():
                return jsonify({'error': f'Поле {field} обязательно'}), 400
        
        # Подключение к БД и вставка данных
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("""
            INSERT INTO bookings (date, time, name, service, comment, user_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id, created_at
        """, (
            data['date'].strip(),
            data['time'].strip(),
            data['name'].strip(),
            data['service'].strip(),
            data.get('comment', '').strip(),
            data['user_id'].strip()
        ))
        
        result = cur.fetchone()
        conn.commit()
        
        cur.close()
        conn.close()
        
        return jsonify({
            'message': 'Бронирование успешно создано',
            'booking_id': result[0],
            'created_at': result[1].isoformat() if result[1] else None
        }), 201
        
    except Exception as e:
        print(f"Error creating booking: {e}")
        return jsonify({'error': 'Ошибка при создании бронирования'}), 500

# Маршрут для получения бронирований пользователя
@app.route('/api/bookings/<user_id>', methods=['GET'])
def get_user_bookings(user_id):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("""
            SELECT id, date, time, name, service, comment, created_at
            FROM bookings 
            WHERE user_id = %s 
            ORDER BY created_at DESC
        """, (user_id,))
        
        bookings = []
        for row in cur.fetchall():
            bookings.append({
                'id': row[0],
                'date': row[1],
                'time': row[2],
                'name': row[3],
                'service': row[4],
                'comment': row[5],
                'created_at': row[6].isoformat() if row[6] else None
            })
        
        cur.close()
        conn.close()
        
        return jsonify({'bookings': bookings})
        
    except Exception as e:
        print(f"Error fetching bookings: {e}")
        return jsonify({'error': 'Ошибка при получении бронирований'}), 500

# Маршрут для удаления бронирования
@app.route('/api/bookings/<int:booking_id>', methods=['DELETE'])
def delete_booking(booking_id):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("DELETE FROM bookings WHERE id = %s", (booking_id,))
        
        if cur.rowcount == 0:
            cur.close()
            conn.close()
            return jsonify({'error': 'Бронирование не найдено'}), 404
        
        conn.commit()
        cur.close()
        conn.close()
        
        return jsonify({'message': 'Бронирование успешно удалено'})
        
    except Exception as e:
        print(f"Error deleting booking: {e}")
        return jsonify({'error': 'Ошибка при удалении бронирования'}), 500

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
    # Получаем порт из переменных окружения (для Railway)
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
