document.addEventListener('DOMContentLoaded', () => {
    const remoteScreen = document.getElementById('remote-screen');
    const screenWrapper = document.getElementById('screen-wrapper');
    const connectionBadge = document.getElementById('connection-badge');
    const refreshSelect = document.getElementById('refresh-interval');
    const btnManualRefresh = document.getElementById('btn-manual-refresh');
    const btnSendText = document.getElementById('btn-send-text');
    const textToType = document.getElementById('text-to-type');
    const virtualCursor = document.getElementById('mouse-cursor');

    let refreshTimer = null;
    let isRefreshing = false;
    let tempFastRefreshTimer = null;
    let tempFastRefreshCounter = 0;

    // ================= 1. HÀM CẬP NHẬT MÀN HÌNH =================
    
    function refreshScreen() {
        if (isRefreshing) return;
        isRefreshing = true;

        // Thêm timestamp để ép trình duyệt không cache ảnh
        const timestamp = new Date().getTime();
        const img = new Image();
        
        img.onload = function() {
            remoteScreen.src = this.src;
            isRefreshing = false;
        };
        
        img.onerror = function() {
            isRefreshing = false;
        };
        
        img.src = `/api/screen?t=${timestamp}`;
    }

    // Thiết lập chu kỳ refresh tự động
    function setupAutoRefresh() {
        if (refreshTimer) {
            clearInterval(refreshTimer);
            refreshTimer = null;
        }

        const interval = refreshSelect.value;
        if (interval !== 'manual') {
            refreshTimer = setInterval(refreshScreen, parseInt(interval));
        }
    }

    refreshSelect.addEventListener('change', setupAutoRefresh);
    btnManualRefresh.addEventListener('click', refreshScreen);

    // Tự động kích hoạt refresh nhanh dồn dập khi có tương tác
    function triggerFastRefresh() {
        if (tempFastRefreshTimer) {
            clearInterval(tempFastRefreshTimer);
        }
        tempFastRefreshCounter = 0;
        // Thực hiện refresh nhanh mỗi 300ms trong vòng 8 lần (~2.4 giây)
        // để bắt kịp phản hồi của máy tính ở nhà sau lệnh click/phím
        tempFastRefreshTimer = setInterval(() => {
            refreshScreen();
            tempFastRefreshCounter++;
            if (tempFastRefreshCounter >= 8) {
                clearInterval(tempFastRefreshTimer);
                tempFastRefreshTimer = null;
            }
        }, 300);
    }

    setupAutoRefresh(); // Kích hoạt ngay khi load trang

    // ================= 2. KIỂM TRA TRẠNG THÁI CLIENT =================
    
    function checkClientStatus() {
        fetch('/api/status')
            .then(res => res.json())
            .then(data => {
                if (data.status === 'online') {
                    connectionBadge.textContent = 'ONLINE';
                    connectionBadge.className = 'badge badge-online pulse';
                } else if (data.status === 'offline') {
                    connectionBadge.textContent = 'OFFLINE';
                    connectionBadge.className = 'badge badge-offline';
                } else {
                    connectionBadge.textContent = 'CHƯA KẾT NỐI';
                    connectionBadge.className = 'badge badge-warning';
                }
            })
            .catch(() => {
                connectionBadge.textContent = 'LỖI SERVER';
                connectionBadge.className = 'badge badge-offline';
            });
    }

    setInterval(checkClientStatus, 5000); // Check mỗi 5 giây
    checkClientStatus(); // Check ngay lập tức khi load trang

    // ================= 3. GỬI LỆNH LÊN SERVER =================
    
    function sendCommand(type, data) {
        // Tạm thời hiển thị cursor ảo đỏ tại điểm click (nếu là lệnh click)
        if (type === 'click') {
            const xPercent = data.x * 100;
            const yPercent = data.y * 100;
            virtualCursor.style.left = `${xPercent}%`;
            virtualCursor.style.top = `${yPercent}%`;
            virtualCursor.style.display = 'block';
            setTimeout(() => {
                virtualCursor.style.display = 'none';
            }, 500);
        }

        fetch('/api/command', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ type, data })
        })
        .then(res => res.json())
        .then(resData => {
            if (resData.status === 'success') {
                // Kích hoạt chuỗi refresh nhanh dồn dập ngay sau khi gửi lệnh thành công
                triggerFastRefresh();
            }
        })
        .catch(err => console.error('Lỗi gửi lệnh:', err));

    }

    // ================= 4. BẮT SỰ KIỆN TƯƠNG TÁC CHUỘT TRÊN MÀN HÌNH =================
    
    let isMouseDown = false;
    let dragStartX = 0;
    let dragStartY = 0;

    // Click chuột phải (Chặn menu chuột phải mặc định và gửi command)
    remoteScreen.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        
        const rect = remoteScreen.getBoundingClientRect();
        const x = (e.clientX - rect.left) / rect.width;
        const y = (e.clientY - rect.top) / rect.height;
        
        // Gửi lệnh click chuột phải
        sendCommand('click', { x, y, button: 'right' });
    });

    // Mousedown (để bắt đầu kéo thả hoặc click thường)
    remoteScreen.addEventListener('mousedown', (e) => {
        // Chỉ bắt click chuột trái (button = 0)
        if (e.button !== 0) return;
        
        isMouseDown = true;
        const rect = remoteScreen.getBoundingClientRect();
        dragStartX = (e.clientX - rect.left) / rect.width;
        dragStartY = (e.clientY - rect.top) / rect.height;
    });

    // Mouseup (để phân biệt Click, Double Click, Drag)
    remoteScreen.addEventListener('mouseup', (e) => {
        if (e.button !== 0) return; // Chỉ chuột trái
        isMouseDown = false;
        
        const rect = remoteScreen.getBoundingClientRect();
        const dragEndX = (e.clientX - rect.left) / rect.width;
        const dragEndY = (e.clientY - rect.top) / rect.height;
        
        // Tính khoảng cách kéo thả
        const dist = Math.sqrt(Math.pow(dragEndX - dragStartX, 2) + Math.pow(dragEndY - dragStartY, 2));
        
        // Nếu giữ phím Shift khi nhả chuột, hoặc kéo chuột 1 khoảng đủ xa (> 1% màn hình), thì coi là KÉO THẢ (Drag & Drop)
        if (e.shiftKey || dist > 0.01) {
            sendCommand('drag', { 
                start_x: dragStartX, 
                start_y: dragStartY, 
                end_x: dragEndX, 
                end_y: dragEndY 
            });
        } else {
            // Click thường (hoặc Double Click sẽ được xử lý riêng bởi dbclick event)
            // Nếu người dùng giữ Ctrl khi click trái, coi như click phải
            const buttonType = e.ctrlKey ? 'right' : 'left';
            sendCommand('click', { x: dragEndX, y: dragEndY, button: buttonType });
        }
    });

    // Nhấp đúp chuột (Double click)
    remoteScreen.addEventListener('dblclick', (e) => {
        if (e.button !== 0) return;
        
        const rect = remoteScreen.getBoundingClientRect();
        const x = (e.clientX - rect.left) / rect.width;
        const y = (e.clientY - rect.top) / rect.height;
        
        sendCommand('double_click', { x, y });
    });

    // ================= 5. CÁC PHÍM TẮT & GÕ VĂN BẢN =================
    
    // Nút phím nhanh
    document.querySelectorAll('.btn-control').forEach(btn => {
        btn.addEventListener('click', () => {
            const keyCombo = btn.getAttribute('data-key');
            sendCommand('hotkey', { keys: keyCombo.split('+') });
        });
    });

    // Gõ text
    btnSendText.addEventListener('click', () => {
        const text = textToType.value;
        if (!text) return;
        sendCommand('type_text', { text: text });
        textToType.value = ''; // Reset input
    });

    textToType.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            btnSendText.click();
        }
    });

});
