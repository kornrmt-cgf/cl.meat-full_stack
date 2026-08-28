# CL.MEAT Environment Setup

> Last updated: TASK 02.0 — Python 3.12 + Django 5.2 LTS

---

## Requirements

| Component | Version | Notes |
|-----------|---------|-------|
| Python | 3.12.x | Required by Django 5.2 |
| Django | 5.2 LTS | `>=5.2,<5.3` |
| Pillow | 10-12 | Image processing |
| freezegun | >=1.2.0 | Time freezing for tests |

---

## Quick Start

### Option A: Miniconda (recommended for macOS)

```bash
# Create conda environment
conda create -n clmeat python=3.12 -y
conda activate clmeat

# Install dependencies
cd task_manager
pip install -r requirements.txt

# Run checks
DJANGO_SECRET_KEY=test-key python manage.py check
DJANGO_SECRET_KEY=test-key python manage.py test
```

### Option B: Python venv

```bash
# Create virtual environment
cd task_manager
python3.12 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run checks
DJANGO_SECRET_KEY=test-key python manage.py check
DJANGO_SECRET_KEY=test-key python manage.py test
```

---

## Environment Variables

Create `.env` file in `task_manager/` directory:

```bash
# Required
DJANGO_SECRET_KEY=your-secret-key-here

# Optional (defaults shown)
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
DB_ENGINE=django.db.backends.sqlite3
DB_NAME=db.sqlite3
```

See `.env.example` for full template.

---

## Development Commands

```bash
# Check system
python --version          # Should show Python 3.12.x
python -c "import django; print(django.VERSION)"  # Should show (5, 2, ...)

# Run checks
DJANGO_SECRET_KEY=test-key python manage.py check

# Run tests
DJANGO_SECRET_KEY=test-key python manage.py test

# Run specific test suite
DJANGO_SECRET_KEY=test-key python manage.py test inventory
DJANGO_SECRET_KEY=test-key python manage.py test tasks
DJANGO_SECRET_KEY=test-key python manage.py test planning.rotation_tests

# Check migrations
DJANGO_SECRET_KEY=test-key python manage.py makemigrations --check
DJANGO_SECRET_KEY=test-key python manage.py migrate --plan
```

---

## Notes

- SQLite is used for development only. Production uses PostgreSQL.
- `DJANGO_SECRET_KEY` is required — app will not start without it.
- `DJANGO_DEBUG` defaults to `False` for production safety.
- All 315 tests should pass with Python 3.12 + Django 5.2.
