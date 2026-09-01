# CL.MEAT TaskManager

ระบบจัดการงาน ตารางเวลา และสินค้าคงคลังสำหรับร้านเนื้อ — Workforce Task, Scheduling & Inventory System

## Architecture Overview

```
tasks/            — Core task management (Task, TaskAssignment, TaskActivity)
operations/       — Worker operational tasks (WorkerTask, TaskEvent, RotationEvent)
                   — Worker UI views, barcode scanning, task board
inventory/        — Product, Batch, Package, Stock, StockMovement
planning/         — RotationPlan, RotationCycle, ThawQueue, ThawProfile, CapacityLock
scheduling/       — Week schedule, TaskTemplate, conflict detection
common/           — State machine, worker actions
reports/          — Reporting system
notifications/    — Notification system
dashboard/        — Manager dashboard
accounts/         — User model (userid login), EmployeeProfile, Role, Team
```

## ฟีเจอร์หลัก

### Inventory (Phase 03)
- Product / Batch / Package / Stock / StockMovement
- Stock calculation: one authoritative rule — SUM(weight) of active packages
- Barcode generation with concurrency-safe sequence
- Decimal pricing (no floating-point)
- Stock movement audit trail (RECEIVE, PACK, MOVE, SELL, DISCARD)

### Freeze / Thaw / Rotation (Phase 04)
- RotationCycle: multi-cycle package lifecycle tracking
- Freeze lifecycle: start → check → complete
- Thaw queue: per-profile capacity, interval-overlap, deterministic ordering
- CapacityLock: serialized admission via PostgreSQL row locking
- Refreeze support with full history preservation

### Task Manager Integration (Phase 05)
- WorkerTask linked to RotationPlan lifecycle
- Atomic claim: only one worker per task (SELECT FOR UPDATE)
- Task ownership: only real Django User instances (isinstance check)
- Fail-closed dispatch: unknown task types rejected
- Idempotent completion, stale task detection

### Worker Operations (Phase 06)
- Thai-language task board (งานของฉัน)
- Claim / Start / Complete / Cancel workflow
- Mandatory barcode confirmation for completion
- Cancel restricted to claimant for CLAIMED/IN_PROGRESS tasks
- AJAX polling with 30s interval
- Alpine.js confirmation dialogs

### Core Task Workflow
- สร้าง/แก้ไข/ลบงาน
- มอบหมายงานให้พนักงาน
- สถานะงาน: กำหนดไว้ → รับงาน → กำลังทำ → เสร็จ/มีปัญหา/ข้อผิดพลาด
- รายงานปัญหาและข้อผิดพลาด
- ประวัติการเปลี่ยนสถานะ

### Scheduling & Queue
- ตารางงานรายสัปดาห์
- แม่แบบงาน (TaskTemplate) พร้อม recurrence
- Drag-and-drop reordering
- Conflict detection
- Reschedule endpoint

### Dashboard & Reporting
- Manager dashboard พร้อมสถิติ
- รายงานสถานะ/พนักงาน/ประสิทธิภาพ
- ภาระงานพนักงาน (Workload)
- ภาพรวมทีม

### Task Marketplace
- โหมดงาน: มอบหมายเฉพาะคน หรือ เปิดให้แย่งงาน
- ค่าตอบแทน (reward) สำหรับงานแต่ละงาน
- แย่งงานแบบ atomic (ป้องกัน race condition)

## การติดตั้ง

### 1. Clone repository
```bash
git clone https://github.com/kornrmt-cgf/cl.meat-task_manager.git
cd cl.meat-task_manager
```

### 2. สร้าง Virtual Environment
```bash
python3 -m venv venv
source venv/bin/activate  # macOS/Linux
# venv\Scripts\activate   # Windows
```

### 3. ติดตั้ง dependencies
```bash
pip install -r requirements.txt
```

### 4. Environment Variables (ถ้ามี)
```bash
cp .env.example .env
# แก้ไขค่าใน .env
```

### 5. Run migrations
```bash
python manage.py migrate
```

### 6. สร้าง Superuser
```bash
python manage.py createsuperuser
```

### 7. รัน server
```bash
python manage.py runserver
```

เปิด http://127.0.0.1:8000/

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `DJANGO_SECRET_KEY` | Django secret key | dev-only insecure key |
| `DJANGO_DEBUG` | Debug mode | `True` |
| `DJANGO_ALLOWED_HOSTS` | Allowed hosts | `localhost,127.0.0.1` |
| `DB_ENGINE` | Database engine | `django.db.backends.sqlite3` |
| `DB_NAME` | Database name | `db.sqlite3` |

## รัน Tests

```bash
python manage.py test
```

## Project Structure

```
cl.meat-task_manager/
├── accounts/           # User model, login, register, profile
│   ├── models.py       # User (userid login), EmployeeProfile, Role, Team
│   ├── views.py        # Login, Register, Profile, Theme toggle
│   ├── forms.py        # LoginForm, RegisterForm, ProfileForm
│   └── urls.py
├── tasks/              # Core task management
│   ├── models.py       # Task, TaskAssignment, TaskActivity, TaskReport, TaskTemplate
│   ├── views.py        # Task CRUD, Today/Tomorrow views, Complete/Problem/Error
│   ├── forms.py        # TaskCreateForm (work_mode, reward, assigned_to)
│   ├── services.py     # TaskService (create, claim, complete, report)
│   └── tests.py
├── scheduling/         # Schedule management
│   ├── views.py        # Week view, Manager schedule, Template CRUD
│   ├── services.py     # SchedulingService (validate, reschedule, conflicts)
│   └── forms.py        # TaskTemplateForm
├── notifications/      # Notification system
│   ├── models.py       # Notification model
│   ├── views.py        # Notification list, popup, mark-as-read
│   ├── services.py     # NotificationService (create, notify, preferences)
│   └── urls.py
├── dashboard/          # Manager dashboard
├── reports/            # Reporting system
├── templates/          # HTML templates (all pages)
│   ├── base.html       # Main layout + CSS + JavaScript
│   ├── accounts/       # Login, Register, Profile
│   ├── tasks/          # Today, Tomorrow, Task form, Task detail
│   ├── scheduling/     # Week, Manager schedule, Templates
│   ├── notifications/  # List, Popup partial
│   └── dashboard/      # Manager dashboard
├── core/
│   └── settings.py     # Django settings
├── manage.py
├── requirements.txt
└── README.md
```

## User Roles

### Manager (admin)
- สร้าง/แก้ไข/ลบงาน
- เลือกโหมดงาน: มอบหมาย หรือ เปิดให้แย่ง
- ดู Dashboard พร้อมสถิติ
- ดูรายงานปัญหา/ข้อผิดพลาด
- จัดตารางงาน

### Employee
- ดูงานวันนี้/พรุ่งนี้/สัปดาห์
- รับงาน / เริ่มทำงาน / เสร็จงาน
- รายงานปัญหา/ข้อผิดพลาด
- แย่งงานเปิด (Task Marketplace)
- ดูแจ้งเตือน

## Authentication

เข้าสู่ระบบด้วย **userid** (ไม่ใช้ email) + password

สร้างบัญชีทดสอบ:
```bash
python manage.py seed_data          # สร้างข้อมูลตัวอย่าง
python manage.py createsuperuser     # สร้าง admin
```

## Tech Stack

- **Backend:** Django 5.2 LTS + PostgreSQL (staging) / SQLite (development)
- **Frontend:** Tailwind CSS (CDN) + Alpine.js + HTMX
- **Timezone:** Asia/Bangkok (UTC+7)

## Testing

```bash
# Django checks
python manage.py check
python manage.py makemigrations --check --dry-run

# Full test suite (SQLite)
python manage.py test

# PostgreSQL concurrency tests (requires staging DB)
DJANGO_ENV=staging python manage.py test
```
