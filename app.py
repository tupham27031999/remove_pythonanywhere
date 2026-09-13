import os
import sys
import sqlite3
import datetime
import threading
from flask import Flask, request, jsonify, render_template, redirect, url_for, session, send_file, Response

# Cấu hình mã hoá utf-8 cho đầu ra console để tránh UnicodeEncodeError trên terminal Windows
if sys.stdout and sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass
if sys.stderr and sys.stderr.encoding != 'utf-8':
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

app = Flask(__name__)
app.secret_key = os.urandom(24) # Sinh ngẫu nhiên secret key cho session mỗi lần khởi động server

DATABASE = os.path.join(app.root_path, 'database.db')
SCREEN_PATH = os.path.join(app.root_path, 'static', 'screen.jpg')

# Lưu trữ ảnh chụp màn hình trực tiếp trong RAM để tối đa hóa tốc độ phục vụ
latest_screen_bytes = None
latest_screen_mimetype = 'image/webp'
screen_lock = threading.Lock()
screen_version = 0

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    # Tạo thư mục static nếu chưa có để chứa ảnh màn hình
    os.makedirs(os.path.join(app.root_path, 'static'), exist_ok=True)
    
    with get_db() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS session_info (
                id INTEGER PRIMARY KEY,
                password TEXT NOT NULL,
                last_seen TIMESTAMP NOT NULL
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS commands (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                data TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS file_content (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL,
                content TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                error_message TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()

# Khởi tạo DB khi chạy ứng dụng
init_db()

# Middleware kiểm tra đăng nhập cho người dùng
def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# Xác thực request từ Client ở nhà
def verify_client_request():
    client_password = request.headers.get('X-Client-Password')
    if not client_password:
        return False
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT password FROM session_info ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        if row and row['password'] == client_password:
            # Cập nhật thời gian hoạt động của client
            conn.execute("UPDATE session_info SET last_seen = ? WHERE password = ?", 
                         (datetime.datetime.now(), client_password))
            conn.commit()
            return True
    return False

# ================= API DÀNH CHO HOME PC CLIENT =================

@app.route('/api/client/register', methods=['POST'])
def client_register():
    data = request.get_json()
    if not data or 'password' not in data:
        return jsonify({"status": "error", "message": "Missing password"}), 400
    
    password = data['password']
    now = datetime.datetime.now()
    
    with get_db() as conn:
        conn.execute("DELETE FROM session_info") # Xoá phiên cũ
        conn.execute("DELETE FROM commands")     # Xoá hàng đợi lệnh cũ
        conn.execute("DELETE FROM file_content")  # Xoá lịch sử lấy file cũ
        conn.execute("INSERT INTO session_info (id, password, last_seen) VALUES (1, ?, ?)", (password, now))
        conn.commit()
        
    # Reset bộ đệm ảnh trong RAM và file cũ
    global latest_screen_bytes, screen_version
    with screen_lock:
        latest_screen_bytes = None
        screen_version = 0

    if os.path.exists(SCREEN_PATH):
        try:
            os.remove(SCREEN_PATH)
        except Exception:
            pass
            
    return jsonify({"status": "success", "message": "Client registered successfully"})

@app.route('/api/client/poll', methods=['GET'])
def client_poll():
    if not verify_client_request():
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
    
    with get_db() as conn:
        cursor = conn.cursor()
        # Lấy tất cả các lệnh đang chờ xử lý
        cursor.execute("SELECT id, type, data FROM commands WHERE status = 'pending' ORDER BY id ASC")
        rows = cursor.fetchall()
        
        commands = []
        if rows:
            for row in rows:
                commands.append({
                    "id": row["id"],
                    "type": row["type"],
                    "data": row["data"]
                })
            # Đánh dấu các lệnh này đã được gửi (done hoặc gửi đi)
            conn.execute("DELETE FROM commands WHERE status = 'pending'")
            conn.commit()
            
    return jsonify({"status": "success", "commands": commands})

@app.route('/api/client/screen', methods=['POST'])
def client_upload_screen():
    global latest_screen_bytes, latest_screen_mimetype, screen_version
    if not verify_client_request():
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
        
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "No file part"}), 400
        
    file = request.files['file']
    if file.filename == '':
        return jsonify({"status": "error", "message": "No selected file"}), 400
        
    if file:
        data = file.read()
        mimetype = file.content_type or 'image/webp'
        with screen_lock:
            latest_screen_bytes = data
            latest_screen_mimetype = mimetype
            screen_version += 1
        return jsonify({"status": "success", "message": "Screen updated", "version": screen_version})

# ================= API DÀNH CHO OFFICE PC (WEB INTERFACE) =================

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        password = request.form.get('password')
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT password FROM session_info ORDER BY id DESC LIMIT 1")
            row = cursor.fetchone()
            if row and row['password'] == password:
                session['logged_in'] = True
                return redirect(url_for('index'))
            else:
                return render_template('login.html', error="Sai mật mã xác thực!")
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    return render_template('index.html')

@app.route('/api/command', methods=['POST'])
@login_required
def post_command():
    data = request.get_json()
    if not data or 'type' not in data or 'data' not in data:
        return jsonify({"status": "error", "message": "Invalid command data"}), 400
        
    cmd_type = data['type']
    cmd_data = data['data'] # JSON string hoặc dict, ta chuyển thành string để lưu SQLite
    if isinstance(cmd_data, dict):
        import json
        cmd_data = json.dumps(cmd_data)
        
    with get_db() as conn:
        conn.execute("INSERT INTO commands (type, data, status) VALUES (?, ?, 'pending')", (cmd_type, cmd_data))
        conn.commit()
        
    return jsonify({"status": "success", "message": "Command queued"})

# ================= API XỬ LÝ LẤY NỘI DUNG FILE TỪ XA =================

@app.route('/api/request_file', methods=['POST'])
@login_required
def request_file():
    data = request.get_json()
    if not data or 'file_path' not in data:
        return jsonify({"status": "error", "message": "Thiếu đường dẫn file"}), 400
        
    file_path = data['file_path']
    
    with get_db() as conn:
        cursor = conn.cursor()
        # Thêm yêu cầu vào bảng file_content
        cursor.execute(
            "INSERT INTO file_content (file_path, status) VALUES (?, 'pending')", 
            (file_path,)
        )
        transfer_id = cursor.lastrowid
        
        # Tạo lệnh gửi xuống cho Client laptop
        import json
        cmd_data = json.dumps({
            "file_path": file_path,
            "transfer_id": transfer_id
        })
        cursor.execute(
            "INSERT INTO commands (type, data, status) VALUES ('copy_file_content', ?, 'pending')", 
            (cmd_data,)
        )
        conn.commit()
        
    return jsonify({
        "status": "success", 
        "message": "Đã yêu cầu đọc file từ laptop", 
        "transfer_id": transfer_id
    })

@app.route('/api/save_file', methods=['POST'])
@login_required
def save_file():
    data = request.get_json()
    if not data or 'file_path' not in data or 'content' not in data:
        return jsonify({"status": "error", "message": "Thiếu thông tin đường dẫn hoặc nội dung file"}), 400
        
    file_path = data['file_path']
    content = data['content']
    
    with get_db() as conn:
        cursor = conn.cursor()
        # Thêm yêu cầu ghi file vào bảng file_content (lưu nội dung mới cần ghi)
        cursor.execute(
            "INSERT INTO file_content (file_path, content, status) VALUES (?, ?, 'pending')", 
            (file_path, content)
        )
        transfer_id = cursor.lastrowid
        
        # Tạo lệnh gửi xuống cho Client laptop
        import json
        cmd_data = json.dumps({
            "file_path": file_path,
            "content": content,
            "transfer_id": transfer_id
        })
        cursor.execute(
            "INSERT INTO commands (type, data, status) VALUES ('write_file_content', ?, 'pending')", 
            (cmd_data,)
        )
        conn.commit()
        
    return jsonify({
        "status": "success", 
        "message": "Đã gửi yêu cầu lưu file xuống laptop", 
        "transfer_id": transfer_id
    })

@app.route('/api/file_status/<int:transfer_id>', methods=['GET'])
@login_required
def file_status(transfer_id):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT file_path, content, status, error_message FROM file_content WHERE id = ?", 
            (transfer_id,)
        )
        row = cursor.fetchone()
        
    if not row:
        return jsonify({"status": "error", "message": "Không tìm thấy phiên yêu cầu file này"}), 404
        
    return jsonify({
        "status": "success",
        "data": {
            "file_path": row["file_path"],
            "content": row["content"],
            "status": row["status"],
            "error_message": row["error_message"]
        }
    })

@app.route('/api/client/file_content', methods=['POST'])
def client_upload_file_content():
    if not verify_client_request():
        return jsonify({"status": "error", "message": "Unauthorized"}), 401
        
    data = request.get_json()
    if not data or 'transfer_id' not in data or 'status' not in data:
        return jsonify({"status": "error", "message": "Dữ liệu không hợp lệ"}), 400
        
    transfer_id = data['transfer_id']
    status = data['status']
    content = data.get('content')
    error_message = data.get('error_message')
    
    with get_db() as conn:
        if content is not None:
            conn.execute(
                "UPDATE file_content SET status = ?, content = ?, error_message = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (status, content, error_message, transfer_id)
            )
        else:
            conn.execute(
                "UPDATE file_content SET status = ?, error_message = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (status, error_message, transfer_id)
            )
        conn.commit()
        
    return jsonify({"status": "success", "message": "Cập nhật nội dung file thành công"})

@app.route('/api/screen')
@login_required
def get_screen():
    global latest_screen_bytes, latest_screen_mimetype, screen_version
    with screen_lock:
        data = latest_screen_bytes
        mimetype = latest_screen_mimetype
        ver = screen_version
        
    if data:
        resp = Response(data, mimetype=mimetype)
        resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
        resp.headers['X-Screen-Version'] = str(ver)
        return resp
    elif os.path.exists(SCREEN_PATH):
        response = send_file(SCREEN_PATH, mimetype='image/jpeg')
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response
    else:
        return "No screen frame available yet", 404

@app.route('/api/status')
@login_required
def get_status():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT last_seen FROM session_info ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        if row:
            last_seen_str = row['last_seen']
            # sqlite3 lưu datetime dưới dạng string 'YYYY-MM-DD HH:MM:SS.ffffff' hoặc tương tự
            try:
                last_seen = datetime.datetime.strptime(last_seen_str.split('.')[0], "%Y-%m-%d %H:%M:%S")
            except Exception:
                try:
                    last_seen = datetime.datetime.strptime(last_seen_str, "%Y-%m-%d %H:%M:%S")
                except Exception:
                    return jsonify({"status": "unknown"})
                    
            diff = (datetime.datetime.now() - last_seen).total_seconds()
            if diff < 15: # Nếu client poll trong vòng 15 giây qua thì là online
                return jsonify({"status": "online", "last_seen_seconds_ago": int(diff)})
            else:
                return jsonify({"status": "offline", "last_seen_seconds_ago": int(diff)})
        return jsonify({"status": "not_registered"})

if __name__ == '__main__':
    # Chạy cục bộ để test
    app.run(host='0.0.0.0', port=5000, debug=True)
