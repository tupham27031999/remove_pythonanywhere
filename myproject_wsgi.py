import sys
import os

# Đường dẫn tới thư mục chứa mã nguồn FastAPI
BASE_DIR = '/home/tupham/remove_pythonanywhere'
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

os.environ.setdefault('PYTHONPATH', BASE_DIR)

# FastAPI là ASGI app, PythonAnywhere chạy WSGI server
# → Dùng a2wsgi để bridge ASGI → WSGI
from main import app as fastapi_app
from a2wsgi import ASGIMiddleware

# PythonAnywhere tìm biến tên `application`
application = ASGIMiddleware(fastapi_app)