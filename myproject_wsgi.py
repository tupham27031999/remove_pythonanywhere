import sys
import os

# Đường dẫn tới thư mục chứa mã nguồn FastAPI (thay USERNAME bằng tên người dùng trên PythonAnywhere)
BASE_DIR = '/home/USERNAME/code_pythonanywhere'
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

# Đảm bảo biến môi trường PYTHONPATH bao gồm thư mục này (tùy chọn)
os.environ.setdefault('PYTHONPATH', BASE_DIR)

# Import FastAPI app từ file main.py
from main import app as application

# Nếu cần chạy bằng gunicorn, không cần thêm gì thêm; `application` là đối tượng ASGI.
# (PythonAnywhere sẽ sử dụng `gunicorn -k uvicorn.workers.UvicornWorker myproject_wsgi:application`)
