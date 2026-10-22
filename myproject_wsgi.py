# <--- Đoạn được PythonAnywhere tự động thêm ---
import os
import sys

# Đường dẫn tới virtualenv
path = '/home/tupham/venv-remove-pa/lib/python3.11/site-packages'
if path not in sys.path:
    sys.path.append(path)

# Đường dẫn tới thư mục chứa mã nguồn
project_home = '/home/tupham/remove_pythonanywhere'
if project_home not in sys.path:
    sys.path.append(project_home)

# Thêm thư mục chứa ứng dụng Flask/Django (tùy dự án của bạn)
os.chdir(project_home)

# Import ứng dụng (giả sử Flask và file app.py)
from app import app as application   # <-- thay 'app' và 'application' tùy dự án
# <--- End of auto‑generated section --->