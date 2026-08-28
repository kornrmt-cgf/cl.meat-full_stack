"""
Django settings for Freebuff Desktop - Workforce Task & Scheduling System

.production-ready สำหรับ Milestone 1
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# === SECURITY ===
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    raise ValueError("DJANGO_SECRET_KEY environment variable is required.")
DEBUG = os.environ.get("DJANGO_DEBUG", "False").lower() in ("true", "1", "yes")
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

# === APPLICATIONS ===
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Apps ของโปรเจค
    "accounts",
    "tasks",
    "scheduling",
    "reports",
    "dashboard",
    "notifications",
    # Inventory & Stock Management
    "common",
    "inventory",
    "planning",
    "operations",
]

# === MIDDLEWARE ===
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "core.urls"

# === TEMPLATES ===
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "accounts.context_processors.theme_context",
            ],
        },
    },
]

WSGI_APPLICATION = "core.wsgi.application"

# === DATABASE ===
# ใช้ SQLite สำหรับ development, PostgreSQL สำหรับ production
DATABASES = {
    "default": {
        "ENGINE": os.environ.get(
            "DB_ENGINE", "django.db.backends.sqlite3"
        ),
        "NAME": os.environ.get("DB_NAME", str(BASE_DIR / "db.sqlite3")),
        "USER": os.environ.get("DB_USER", ""),
        "PASSWORD": os.environ.get("DB_PASSWORD", ""),
        "HOST": os.environ.get("DB_HOST", ""),
        "PORT": os.environ.get("DB_PORT", ""),
    }
}

# === AUTH ===
AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "tasks:today"
LOGOUT_REDIRECT_URL = "accounts:login"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# === INTERNATIONALIZATION ===
LANGUAGE_CODE = "th"
TIME_ZONE = "Asia/Bangkok"
USE_I18N = True
USE_TZ = True  # เก็บ UTC ภายใน, แสดงเวลาไทย

# === STATIC FILES ===
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# === MEDIA FILES ===
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# === DEFAULT PRIMARY KEY ===
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# === SESSION ===
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_AGE = 86400 * 7  # 7 วัน

# === CSRF ===
CSRF_COOKIE_HTTPONLY = False  # HTMX ต้องเข้าถึง CSRF token

# === MESSAGES ===
from django.contrib.messages import constants as messages_constants

MESSAGE_TAGS = {
    messages_constants.DEBUG: "debug",
    messages_constants.INFO: "info",
    messages_constants.SUCCESS: "success",
    messages_constants.WARNING: "warning",
    messages_constants.ERROR: "error",
}
