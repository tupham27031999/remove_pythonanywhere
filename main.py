# main.py – FastAPI server (full replacement of Flask app)

import os
import sqlite3
import datetime
import json
import asyncio
from typing import Dict

from fastapi import FastAPI, Request, Form, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.status import HTTP_401_UNAUTHORIZED
from fastapi.middleware.gzip import GZipMiddleware
import asyncio
import base64

# ----------------------------------------------------------------------
# FastAPI app configuration
# ----------------------------------------------------------------------
# Dùng đường dẫn tuyệt đối để tránh lỗi khi chạy qua WSGI
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(title="Remote Control Server (FastAPI)", debug=True)
app.add_middleware(SessionMiddleware, secret_key=os.urandom(24))
os.makedirs(os.path.join(BASE_DIR, "static"), exist_ok=True)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
app.add_middleware(GZipMiddleware, minimum_size=500)
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# ----------------------------------------------------------------------
# Database utilities (same schema as original Flask app)
# ----------------------------------------------------------------------
DATABASE = os.path.join(BASE_DIR, "database.db")
SCREEN_PATH = os.path.join(BASE_DIR, "static", "screen.jpg")

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    os.makedirs(os.path.join(BASE_DIR, "static"), exist_ok=True)
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS session_info (
                id INTEGER PRIMARY KEY,
                password TEXT NOT NULL,
                last_seen TIMESTAMP NOT NULL
            );
            CREATE TABLE IF NOT EXISTS commands (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                data TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS file_content (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL,
                content TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                error_message TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.commit()

init_db()

# ----------------------------------------------------------------------
# Helper for client authentication (header X-Client-Password)
# ----------------------------------------------------------------------
def verify_client(request: Request) -> bool:
    pwd = request.headers.get("X-Client-Password")
    if not pwd:
        return False
    with get_db() as conn:
        row = conn.execute(
            "SELECT password FROM session_info ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row and row["password"] == pwd:
            conn.execute(
                "UPDATE session_info SET last_seen = ? WHERE password = ?",
                (datetime.datetime.now(), pwd),
            )
            conn.commit()
            return True
    return False

# ----------------------------------------------------------------------
# Session based login (web UI) – uses Starlette session middleware
# ----------------------------------------------------------------------
def auth_required(func):
    async def wrapper(request: Request, *args, **kwargs):
        if not request.session.get("logged_in"):
            raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="Unauthenticated")
        return await func(request, *args, **kwargs)
    return wrapper

@app.get("/login", response_class=HTMLResponse)
async def get_login(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})

@app.post("/login", response_class=HTMLResponse)
async def post_login(request: Request, password: str = Form(...)):
    with get_db() as conn:
        row = conn.execute(
            "SELECT password FROM session_info ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row and row["password"] == password:
            request.session["logged_in"] = True
            return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "error": "Sai mật mã xác thực!"})

@app.get("/logout")
async def logout(request: Request):
    request.session.pop("logged_in", None)
    return RedirectResponse(url="/login", status_code=302)

@app.get("/", response_class=HTMLResponse)
@auth_required
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# ----------------------------------------------------------------------
# API used by the home‑PC client (register, poll, screen upload)
# ----------------------------------------------------------------------
@app.post("/api/client/register")
async def client_register(request: Request):
    data = await request.json()
    password = data.get("password")
    if not password:
        return JSONResponse({"status": "error", "message": "Missing password"}, status_code=400)
    now = datetime.datetime.now()
    with get_db() as conn:
        conn.execute("DELETE FROM session_info")
        conn.execute("DELETE FROM commands")
        conn.execute("DELETE FROM file_content")
        conn.execute(
            "INSERT INTO session_info (id, password, last_seen) VALUES (1, ?, ?)",
            (password, now),
        )
        conn.commit()
    # reset in‑memory screen cache
    global latest_screen_bytes, latest_screen_mimetype, screen_version, screen_lock
    if screen_lock is None:
        import threading
        screen_lock = threading.Lock()
    with screen_lock:
        latest_screen_bytes = None
        screen_version = 0
    # delete stale file on disk
    if os.path.exists(SCREEN_PATH):
        try:
            os.remove(SCREEN_PATH)
        except Exception:
            pass
    return JSONResponse({"status": "success", "message": "Client registered successfully"})

@app.get("/api/client/poll")
async def client_poll(request: Request):
    if not verify_client(request):
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, type, data FROM commands WHERE status = 'pending' ORDER BY id ASC"
        ).fetchall()
        commands = [{"id": r["id"], "type": r["type"], "data": r["data"]} for r in rows]
        conn.execute("DELETE FROM commands WHERE status = 'pending'")
        conn.commit()
    return JSONResponse({"status": "success", "commands": commands})

@app.post("/api/client/screen")
async def client_upload_screen(request: Request, file: UploadFile = File(...)):
    if not verify_client(request):
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    global latest_screen_bytes, latest_screen_mimetype, screen_version, screen_lock
    content = await file.read()
    mimetype = file.content_type or "image/webp"
    if screen_lock is None:
        import threading
        screen_lock = threading.Lock()
    with screen_lock:
        latest_screen_bytes = content
        latest_screen_mimetype = mimetype
        screen_version += 1
    return JSONResponse({"status": "success", "message": "Screen updated", "version": screen_version})

# ----------------------------------------------------------------------
# API used by the web UI (post commands, file requests, etc.)
# ----------------------------------------------------------------------
@app.post("/api/command")
@auth_required
async def post_command(request: Request):
    data = await request.json()
    if not data or "type" not in data or "data" not in data:
        return JSONResponse({"status": "error", "message": "Invalid command data"}, status_code=400)
    cmd_type = data["type"]
    cmd_data = data["data"]
    if isinstance(cmd_data, dict):
        cmd_data = json.dumps(cmd_data)
    with get_db() as conn:
        conn.execute(
            "INSERT INTO commands (type, data, status) VALUES (?, ?, 'pending')",
            (cmd_type, cmd_data),
        )
        conn.commit()
    return JSONResponse({"status": "success", "message": "Command queued"})

@app.post("/api/request_file")
@auth_required
async def request_file(request: Request):
    data = await request.json()
    file_path = data.get("file_path")
    if not file_path:
        return JSONResponse({"status": "error", "message": "Thiếu đường dẫn file"}, status_code=400)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO file_content (file_path, status) VALUES (?, 'pending')",
            (file_path,),
        )
        transfer_id = cursor.lastrowid
        cmd_payload = json.dumps({"file_path": file_path, "transfer_id": transfer_id})
        cursor.execute(
            "INSERT INTO commands (type, data, status) VALUES ('copy_file_content', ?, 'pending')",
            (cmd_payload,),
        )
        conn.commit()
    return JSONResponse({"status": "success", "message": "Đã yêu cầu đọc file", "transfer_id": transfer_id})

@app.post("/api/save_file")
@auth_required
async def save_file(request: Request):
    data = await request.json()
    file_path = data.get("file_path")
    content = data.get("content")
    if not file_path or content is None:
        return JSONResponse({"status": "error", "message": "Thiếu thông tin file hoặc nội dung"}, status_code=400)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO file_content (file_path, content, status) VALUES (?, ?, 'pending')",
            (file_path, content),
        )
        transfer_id = cursor.lastrowid
        cmd_payload = json.dumps({"file_path": file_path, "content": content, "transfer_id": transfer_id})
        cursor.execute(
            "INSERT INTO commands (type, data, status) VALUES ('write_file_content', ?, 'pending')",
            (cmd_payload,),
        )
        conn.commit()
    return JSONResponse({"status": "success", "message": "Đã gửi yêu cầu lưu file", "transfer_id": transfer_id})

@app.get("/api/file_status/{transfer_id}")
@auth_required
async def file_status(transfer_id: int):
    with get_db() as conn:
        row = conn.execute(
            "SELECT file_path, content, status, error_message FROM file_content WHERE id = ?",
            (transfer_id,),
        ).fetchone()
        if not row:
            return JSONResponse({"status": "error", "message": "Không tìm thấy phiên yêu cầu file"}, status_code=404)
    return JSONResponse({
        "status": "success",
        "data": {
            "file_path": row["file_path"],
            "content": row["content"],
            "status": row["status"],
            "error_message": row["error_message"],
        },
    })

@app.post("/api/client/file_content")
async def client_upload_file_content(request: Request):
    if not verify_client(request):
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    data = await request.json()
    transfer_id = data.get("transfer_id")
    status = data.get("status")
    content = data.get("content")
    error_message = data.get("error_message")
    if transfer_id is None or status is None:
        return JSONResponse({"status": "error", "message": "Dữ liệu không hợp lệ"}, status_code=400)
    with get_db() as conn:
        if content is not None:
            conn.execute(
                """
                UPDATE file_content SET status = ?, content = ?, error_message = ?,
                updated_at = CURRENT_TIMESTAMP WHERE id = ?
                """,
                (status, content, error_message, transfer_id),
            )
        else:
            conn.execute(
                """
                UPDATE file_content SET status = ?, error_message = ?,
                updated_at = CURRENT_TIMESTAMP WHERE id = ?
                """,
                (status, error_message, transfer_id),
            )
        conn.commit()
    return JSONResponse({"status": "success", "message": "Cập nhật nội dung file thành công"})

@app.get("/api/screen")
@auth_required
async def get_screen():
    global latest_screen_bytes, latest_screen_mimetype, screen_version, screen_lock
    if screen_lock is None:
        import threading
        screen_lock = threading.Lock()
    with screen_lock:
        data = latest_screen_bytes
        mimetype = latest_screen_mimetype
        ver = screen_version
    if data:
        headers = {
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "X-Screen-Version": str(ver),
        }
        return StreamingResponse(iter([data]), media_type=mimetype, headers=headers)
    elif os.path.exists(SCREEN_PATH):
        return FileResponse(
            SCREEN_PATH,
            media_type="image/jpeg",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )
    else:
        raise HTTPException(status_code=404, detail="No screen frame available yet")

@app.get("/api/status")
@auth_required
async def get_status():
    with get_db() as conn:
        row = conn.execute(
            "SELECT last_seen FROM session_info ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row:
            last_seen_str = row["last_seen"]
            try:
                last_seen = datetime.datetime.strptime(last_seen_str.split('.')[0], "%Y-%m-%d %H:%M:%S")
            except Exception:
                try:
                    last_seen = datetime.datetime.strptime(last_seen_str, "%Y-%m-%d %H:%M:%S")
                except Exception:
                    return JSONResponse({"status": "unknown"})
            diff = (datetime.datetime.now() - last_seen).total_seconds()
            if diff < 15:
                return JSONResponse({"status": "online", "last_seen_seconds_ago": int(diff)})
            else:
                return JSONResponse({"status": "offline", "last_seen_seconds_ago": int(diff)})
        return JSONResponse({"status": "not_registered"})

@app.get("/api/ping")
async def ping():
    return JSONResponse({"server_time": datetime.datetime.now().timestamp()})

# ----------------------------------------------------------------------
# WebSocket – push pending commands to the client in real‑time
# ----------------------------------------------------------------------
connected_clients: set[WebSocket] = set()

@app.websocket("/ws/commands")
async def ws_commands(ws: WebSocket):
    await ws.accept()
    client_pwd = ws.headers.get("X-Client-Password")
    if not client_pwd:
        await ws.close(code=1008)
        return
    # Verify password against DB
    with get_db() as conn:
        row = conn.execute(
            "SELECT password FROM session_info ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row or row["password"] != client_pwd:
            await ws.close(code=1008)
            return
    connected_clients.add(ws)
    try:
        while True:
            await asyncio.sleep(0.5)
            with get_db() as conn:
                rows = conn.execute(
                    "SELECT id, type, data FROM commands WHERE status = 'pending' ORDER BY id ASC"
                ).fetchall()
                if not rows:
                    continue
                commands = [{"id": r["id"], "type": r["type"], "data": r["data"]} for r in rows]
                conn.execute("DELETE FROM commands WHERE status = 'pending'")
                conn.commit()
            await ws.send_json({"type": "commands", "payload": commands})
    except WebSocketDisconnect:
        connected_clients.discard(ws)
    except Exception as e:
        print(f"WebSocket error: {e}")
        connected_clients.discard(ws)
        await ws.close()

# ----------------------------------------------------------------------
# WebSocket – stream screen updates in real‑time
# ----------------------------------------------------------------------
@app.websocket("/ws/screen")
async def ws_screen(ws: WebSocket):
    await ws.accept()
    last_version = -1
    try:
        while True:
            await asyncio.sleep(0.1)
            async with screen_lock:
                version = screen_version
                data = latest_screen_bytes
                mimetype = latest_screen_mimetype
            if version != last_version and data is not None:
                payload = {
                    "type": "screen",
                    "version": version,
                    "mimetype": mimetype,
                    "timestamp": int(datetime.datetime.utcnow().timestamp() * 1000),
                    "data": base64.b64encode(data).decode("utf-8")
                }
                await ws.send_json(payload)
                last_version = version
    except WebSocketDisconnect:
        pass

# ----------------------------------------------------------------------
# Run with uvicorn (development mode)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=True)
