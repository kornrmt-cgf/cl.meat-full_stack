# Fresh Meat Rotation Planner

A comprehensive system for managing fresh-meat packages through a controlled rotation lifecycle.

## Overview

The system manages fresh-meat packages through the following lifecycle:

```
Purchase → Package → Freeze → Frozen Storage → Thaw Queue → Thaw Schedule → Thawing → Ready for Sale → Display → Refreeze / Process / Discard
```

## Features

- **Target-Driven Planning**: Enter target ready dates, system calculates required thaw/freeze start times
- **Weight-Dependent Calculations**: Duration calculations based on package weight
- **Thaw Queue Management**: Real queue with position tracking
- **Worker Tasks**: Automated task generation for operational workflow
- **Monthly Planning**: Plan across date ranges with conflict detection
- **Audit Trail**: Complete history of all state transitions

## Installation

1. Clone the repository
2. Create virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Copy `.env.example` to `.env` and configure
5. Run migrations:
   ```bash
   python manage.py migrate
   ```
6. Seed demo data:
   ```bash
   python manage.py seed_demo
   ```
7. Create superuser:
   ```bash
   python manage.py createsuperuser
   ```
8. Run development server:
   ```bash
   python manage.py runserver
   ```

## Project Structure

```
fresh_meat/
├── config/          # Django project settings
├── common/          # Shared utilities (state machine, time service)
├── inventory/       # Product, Batch, Package management
├── planning/        # Rotation plans, profiles, queue
├── operations/      # Worker tasks, events
├── dashboard/       # Overview dashboard
├── templates/       # HTML templates
└── static/          # CSS, JavaScript
```

## Usage

### Dashboard
- View overview of packages, plans, and tasks
- See today's tasks and overdue items

### Inventory
- Manage products, batches, and packages
- Track package states and locations

### Planning
- Create rotation plans with target ready times
- Manage thaw queue
- Monthly planning view

### Operations
- Today's tasks dashboard
- Complete worker tasks
- View task history

## Testing

Run tests:
```bash
python manage.py test
```

Run specific app tests:
```bash
python manage.py test inventory
python manage.py test planning
python manage.py test operations
```

## Configuration

Configuration is managed via `.env` file:

- `DJANGO_SECRET_KEY`: Secret key for Django
- `DJANGO_DEBUG`: Debug mode (True/False)
- `DJANGO_ALLOWED_HOSTS`: Allowed hosts
- `DJANGO_TIMEZONE`: Timezone (default: Asia/Bangkok)

## License

This project is for internal use.
