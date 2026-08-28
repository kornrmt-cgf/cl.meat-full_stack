// ============================================================
// SHARED DARK MODE - ใช้ร่วมทุกหน้า
// ============================================================
(function() {
    // โหลดโหมดจาก localStorage
    var saved = localStorage.getItem('clmeat_dark_mode');
    if (saved === 'true') {
        document.documentElement.classList.add('dark-mode');
        document.body.classList.add('dark-mode');
    }

    // ฟังก์ชัน toggle
    window.toggleDarkMode = function() {
        var isDark = document.documentElement.classList.toggle('dark-mode');
        document.body.classList.toggle('dark-mode');
        localStorage.setItem('clmeat_dark_mode', isDark ? 'true' : 'false');

        // อัปเดต icon ปุ่ม
        var btns = document.querySelectorAll('.theme-btn');
        btns.forEach(function(btn) {
            btn.textContent = isDark ? '☀️' : '🌙';
        });
    };

    // อัปเดต icon ปุ่มเมื่อโหลดหน้า
    document.addEventListener('DOMContentLoaded', function() {
        var isDark = document.documentElement.classList.contains('dark-mode');
        var btns = document.querySelectorAll('.theme-btn');
        btns.forEach(function(btn) {
            btn.textContent = isDark ? '☀️' : '🌙';
        });
    });
})();
