/**
 * Fresh Meat Rotation Planner — Base JavaScript
 * Theme persistence, table sorting, modal, toast, API helpers
 */

/* ===================================================================
   Theme Management
   =================================================================== */
var ThemeManager = {
    COOKIE_NAME: 'fmp_theme',
    DARK: 'dark',
    LIGHT: 'light',

    getPreferred() {
        const cookie = this.getCookie(this.COOKIE_NAME);
        if (cookie) return cookie;
        return window.matchMedia('(prefers-color-scheme: dark)').matches ? this.DARK : this.LIGHT;
    },

    apply(theme) {
        document.documentElement.setAttribute('data-theme', theme);
        this.setCookie(this.COOKIE_NAME, theme, 365);
        this.updateIcon(theme);
    },

    toggle() {
        const current = document.documentElement.getAttribute('data-theme') || this.LIGHT;
        const next = current === this.DARK ? this.LIGHT : this.DARK;
        this.apply(next);
    },

    updateIcon(theme) {
        const btn = document.querySelector('.theme-toggle');
        if (btn) btn.textContent = theme === this.DARK ? '☀️' : '🌙';
    },

    getCookie(name) {
        const match = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
        return match ? match[2] : null;
    },

    setCookie(name, value, days) {
        const expires = new Date(Date.now() + days * 864e5).toUTCString();
        document.cookie = `${name}=${value}; expires=${expires}; path=/; SameSite=Lax`;
    },

    init() {
        this.apply(this.getPreferred());
    }
};

/* ===================================================================
   CSRF Token
   =================================================================== */
function getCsrfToken() {
    const name = 'csrftoken';
    const cookies = document.cookie.split(';');
    for (const cookie of cookies) {
        const trimmed = cookie.trim();
        if (trimmed.startsWith(name + '=')) {
            return decodeURIComponent(trimmed.substring(name.length + 1));
        }
    }
    return null;
}

/* ===================================================================
   API Helper
   =================================================================== */
async function apiCall(url, method = 'GET', data = null) {
    const options = {
        method,
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCsrfToken(),
        },
    };
    if (data) options.body = JSON.stringify(data);

    const response = await fetch(url, options);
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'เกิดข้อผิดพลาด');
    return result;
}

/* ===================================================================
   Toast Notifications
   =================================================================== */
function showToast(message, type = 'info') {
    const existing = document.querySelectorAll('.toast');
    existing.forEach(t => t.remove());

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 3500);
}

/* ===================================================================
   Modal
   =================================================================== */
function showModal(title, body, footer = '') {
    const overlay = document.getElementById('modal-overlay');
    if (!overlay) return;
    document.getElementById('modal-title').textContent = title;
    document.getElementById('modal-body').innerHTML = body;
    document.getElementById('modal-footer').innerHTML = footer;
    overlay.style.display = 'flex';
    document.body.style.overflow = 'hidden';
}

function closeModal() {
    const overlay = document.getElementById('modal-overlay');
    if (!overlay) return;
    overlay.style.display = 'none';
    document.body.style.overflow = '';
}

function confirmAction(message, onConfirm) {
    const body = `<p style="margin:0;color:var(--text-primary);">${message}</p>`;
    const footer = `
        <button class="btn btn-outline" onclick="closeModal()">ยกเลิก</button>
        <button class="btn btn-primary" onclick="handleConfirm()">ยืนยัน</button>
    `;
    window._confirmCallback = onConfirm;
    showModal('ยืนยันการดำเนินการ', body, footer);
}

function handleConfirm() {
    if (window._confirmCallback) window._confirmCallback();
    closeModal();
}

/* ===================================================================
   Table Sorting
   =================================================================== */
function initTableSort() {
    document.querySelectorAll('.data-table thead th[data-sortable]').forEach(th => {
        th.addEventListener('click', function () {
            const table = this.closest('table');
            const tbody = table.querySelector('tbody');
            if (!tbody) return;
            const rows = Array.from(tbody.querySelectorAll('tr'));
            const colIdx = Array.from(this.parentNode.children).indexOf(this);
            const isAsc = this.classList.contains('sort-asc');

            // Clear sort indicators from siblings
            this.parentNode.querySelectorAll('th').forEach(h => h.classList.remove('sort-asc', 'sort-desc'));

            // Determine sort type
            const isNumeric = this.dataset.type === 'number';

            rows.sort((a, b) => {
                let aVal = (a.children[colIdx]?.textContent || '').trim();
                let bVal = (b.children[colIdx]?.textContent || '').trim();
                if (isNumeric) {
                    aVal = parseFloat(aVal.replace(/[^0-9.\-]/g, '')) || 0;
                    bVal = parseFloat(bVal.replace(/[^0-9.\-]/g, '')) || 0;
                    return isAsc ? bVal - aVal : aVal - bVal;
                }
                return isAsc ? bVal.localeCompare(aVal, 'th') : aVal.localeCompare(bVal, 'th');
            });

            this.classList.add(isAsc ? 'sort-desc' : 'sort-asc');
            rows.forEach(row => tbody.appendChild(row));
        });
    });
}

/* ===================================================================
   User Dropdown
   =================================================================== */
function initUserDropdown() {
    const toggle = document.querySelector('.navbar-user-toggle');
    const dropdown = document.querySelector('.navbar-dropdown');
    if (!toggle || !dropdown) return;

    toggle.addEventListener('click', (e) => {
        e.stopPropagation();
        dropdown.classList.toggle('show');
    });

    document.addEventListener('click', () => dropdown.classList.remove('show'));
    dropdown.addEventListener('click', (e) => e.stopPropagation());
}

/* ===================================================================
   Mobile Navbar
   =================================================================== */
function initMobileNav() {
    const hamburger = document.querySelector('.navbar-hamburger');
    const mobile = document.querySelector('.navbar-mobile');
    if (!hamburger || !mobile) return;

    hamburger.addEventListener('click', () => {
        mobile.classList.toggle('show');
        hamburger.textContent = mobile.classList.contains('show') ? '✕' : '☰';
    });

    // Close on nav click
    mobile.querySelectorAll('.nav-link').forEach(link => {
        link.addEventListener('click', () => {
            mobile.classList.remove('show');
            hamburger.textContent = '☰';
        });
    });
}

/* ===================================================================
   Date Formatting
   =================================================================== */
function formatThaiDate(dateString) {
    const d = new Date(dateString);
    const pad = n => String(n).padStart(2, '0');
    return `${pad(d.getDate())}/${pad(d.getMonth()+1)}/${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/* ===================================================================
   Auto-dismiss alerts
   =================================================================== */
function initAlertDismiss() {
    document.querySelectorAll('.alert .alert-close').forEach(btn => {
        btn.addEventListener('click', function () {
            const alert = this.closest('.alert');
            if (alert) {
                alert.style.opacity = '0';
                alert.style.transform = 'translateY(-10px)';
                setTimeout(() => alert.remove(), 200);
            }
        });
    });
}

/* ===================================================================
   Init
   =================================================================== */
document.addEventListener('DOMContentLoaded', () => {
    ThemeManager.init();
    initTableSort();
    initUserDropdown();
    initMobileNav();
    initAlertDismiss();

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeModal();
    });

    const overlay = document.getElementById('modal-overlay');
    if (overlay) {
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) closeModal();
        });
    }
});
