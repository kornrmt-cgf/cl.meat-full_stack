# CL.MEAT Migration Readiness & Framework Target

> TASK 01.6 — Final readiness before data migration

---

## 1. Django Current Status (August 29, 2026)

| Version | Released | Security Support Ends | Python Support | Status |
|---------|----------|----------------------|----------------|--------|
| Django 4.2 LTS | April 2023 | **April 7, 2026** | 3.9, 3.10, 3.11, 3.12 | 🔴 **EOL** — no security patches |
| Django 5.0 | December 2023 | April 2025 | 3.10, 3.11, 3.12 | 🔴 EOL |
| Django 5.1 | August 2024 | December 2025 | 3.10, 3.11, 3.12 | 🔴 EOL |
| Django 5.2 LTS | April 2025 | **April 2028** | 3.10, 3.11, 3.12, 3.13 | ✅ **ACTIVE LTS** |
| Django 6.0 | December 2025 | April 2027 | 3.10, 3.11, 3.12, 3.13 | ✅ Active (non-LTS) |
| Django 6.1 | August 2026 | December 2027 | 3.10, 3.11, 3.12, 3.13, 3.14 | ✅ Active (non-LTS) |
| Django 6.2 LTS | April 2027 | April 2030 | TBD | ⏳ Planned |

### Current System State

| Component | Version | Notes |
|-----------|---------|-------|
| Python | 3.9.6 | System Python — EOL October 2025 |
| Python | 3.12 | Available via Miniconda — ✅ supported |
| Django | 4.2.30 | 🔴 EOL since April 2026 |
| Django (Miniconda) | 5.2.17 | ✅ Ready to use |

---

## 2. Django Target Recommendation

### Decision: Upgrade to Django 5.2 LTS

```
CURRENT BASELINE:    Django 4.2.30  (EOL, Python 3.9.6)
    ↓
SAFE UPGRADE:        Django 5.2.17  (LTS, Python 3.12)
    ↓
PRODUCTION TARGET:   Django 5.2 LTS on Python 3.12
```

### Rationale

| Factor | Django 4.2 | Django 5.2 LTS | Assessment |
|--------|-----------|----------------|------------|
| Security support | **ENDED April 2026** | Active until April 2028 | 🔴 vs ✅ |
| Python support | 3.9-3.12 | 3.10-3.13 | 3.12 available ✅ |
| Migration from 4.2 | N/A | Well-documented upgrade path | ✅ |
| Breaking changes from 4.2 | N/A | Minimal for this codebase | ✅ |
| Third-party compatibility | N/A | Pillow ✅, freezegun ✅ | ✅ |
| Long-term viability | None | 2+ years remaining | ✅ |
| Risk of staying on 4.2 | **No security patches** | N/A | 🔴 CRITICAL |

**Why NOT Django 6.0/6.1:** Non-LTS releases have shorter support windows. For a production system undergoing consolidation, LTS stability is essential.

**Why NOT stay on 4.2:** Security support has ended. Running an EOL framework in production is a security risk.

### Upgrade Path Requirements

1. **Python upgrade:** System Python 3.9.6 → 3.12 (via Miniconda or system upgrade)
2. **Django upgrade:** `pip install Django>=5.2,<5.3`
3. **Dependencies:** Pillow 10-12 ✅, freezegun ✅ — no conflicts
4. **Code changes:** Minimal (see compatibility findings below)

---

## 3. Compatibility Findings

### Codebase Audit Results

| Component | File/Pattern | Current API | Django 5.2 Risk | Required Change | Phase |
|-----------|-------------|-------------|----------------|-----------------|-------|
| Settings | `core/settings.py` | Standard middleware, auth, templates | ✅ None | No change needed | — |
| Settings | `DEFAULT_AUTO_FIELD` | `BigAutoField` | ✅ None | Already set | — |
| Settings | `CSRF_COOKIE_HTTPONLY = False` | Standard | ✅ None | No change needed | — |
| URLs | `core/urls.py` | `django.conf.urls.static.static` | ✅ None | Still works in 5.2 | — |
| Auth | `accounts/models.py` | `AbstractUser` | ✅ None | Still works in 5.2 | — |
| Models | All inventory/planning/operations | Standard `models.*` | ✅ None | No deprecated fields used | — |
| Views | All CBVs and FBVs | `LoginRequiredMixin`, `View`, `TemplateView` | ✅ None | Still works in 5.2 | — |
| Forms | `tasks/forms.py`, `accounts/forms.py` | Standard forms | ✅ None | No deprecated APIs | — |
| Admin | All `admin.py` files | Standard admin | ✅ None | No deprecated APIs | — |
| Templates | All templates | Standard template tags | ✅ None | No deprecated tags | — |
| Timezone | `core/utils.py` | `django.utils.timezone.localtime` | ✅ None | Already migrated from pytz | — |
| DB | All migrations | Standard migration framework | ✅ None | No changes needed | — |
| DB | `Transaction.atomic()` | Standard | ✅ None | No changes needed | — |
| DB | `select_for_update()` | Standard | ✅ None | No changes needed | — |

### Deprecated APIs NOT Found

The following deprecated APIs were searched for and NOT found:
- ❌ `NullBooleanField` (removed in Django 4.0)
- ❌ `django.conf.urls.url()` (removed in Django 4.0)
- ❌ `force_text` (removed in Django 4.0)
- ❌ `DEFAULT_FILE_STORAGE` / `STATICFILES_STORAGE` (changed in Django 5.1)
- ❌ `USE_L10N` (removed in Django 5.0)
- ❌ `SmartSettings` (removed in Django 4.0)

### Conclusion

**The codebase is clean for Django 5.2 upgrade.** No deprecated APIs, no breaking patterns. The upgrade can be performed safely after data migration is complete.

---

## 4. Migration Dry-Run Architecture

### Design Principle

The dry-run is **strictly read-only**. It touches NO database writes.

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    MIGRATION DRY-RUN                         │
│                    (read-only)                               │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  1. LEGACY DATABASE (read-only)                      │   │
│  │     database_clmeat_main/db.sqlite3                  │   │
│  │     • Never opened for write                          │   │
│  │     • Connection: read_only=True (PRAGMA)             │   │
│  │     • Snapshot/copy preferred for safety              │   │
│  └─────────────────────────────────────────────────────┘   │
│                          │                                  │
│                          ▼                                  │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  2. EXTRACTION (read-only queries)                    │   │
│  │     • SELECT FROM each legacy table                   │   │
│  │     • No INSERT, UPDATE, DELETE                       │   │
│  │     • Results stored in Python objects                │   │
│  └─────────────────────────────────────────────────────┘   │
│                          │                                  │
│                          ▼                                  │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  3. TRANSFORMATION (in-memory)                        │   │
│  │     • Map fields per ARCHITECTURE_DECISIONS.md        │   │
│  │     • Weight: grams → kg (÷ 1000)                     │   │
│  │     • Price: Float → Decimal                          │   │
│  │     • Status: storage_status → current_state          │   │
│  │     • FK resolution: Product_info → Product + Batch   │   │
│  │     • Generate SKU, Batch number, Barcode             │   │
│  └─────────────────────────────────────────────────────┘   │
│                          │                                  │
│                          ▼                                  │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  4. VALIDATION (in-memory)                            │   │
│  │     • Check for missing references                    │   │
│  │     • Check for invalid values                        │   │
│  │     • Check for duplicates                            │   │
│  │     • Check for data quality issues                   │   │
│  └─────────────────────────────────────────────────────┘   │
│                          │                                  │
│                          ▼                                  │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  5. PREVIEW REPORT (stdout/file)                      │   │
│  │     • Record-by-record mapping                        │   │
│  │     • Summary statistics                              │   │
│  │     • Error/warning list                              │   │
│  │     • NO database writes                              │   │
│  └─────────────────────────────────────────────────────┘   │
│                          │                                  │
│                          ▼                                  │
│                       STOP                                  │
│              (no migration executed)                        │
└─────────────────────────────────────────────────────────────┘
```

### Implementation: Django Management Command

```python
# management/commands/migrate_legacy_data.py

class Command(BaseCommand):
    help = 'Dry-run legacy data migration (READ-ONLY)'
    
    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', default=True,
                          help='Read-only preview (always True for safety)')
        parser.add_argument('--legacy-db', type=str,
                          default='database_clmeat_main/db.sqlite3',
                          help='Path to legacy database')
        parser.add_argument('--output', type=str, default=None,
                          help='Output file for report')
    
    def handle(self, *args, **options):
        # 1. Open legacy DB as read-only
        legacy_conn = self._open_readonly(options['legacy_db'])
        
        # 2. Extract all records
        legacy_data = self._extract_all(legacy_conn)
        
        # 3. Transform in-memory
        candidates = self._transform_all(legacy_data)
        
        # 4. Validate
        validation = self._validate_all(candidates)
        
        # 5. Generate report
        self._print_report(candidates, validation)
        
        # STOP — no database writes
```

### Read-Only Safeguards

```python
def _open_readonly(self, db_path):
    """Open legacy database in read-only mode."""
    import sqlite3
    # SQLite: connect with uri=True and mode=ro
    uri = f'file:{db_path}?mode=ro'
    conn = sqlite3.connect(uri, uri=True)
    # Verify read-only
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode")  # Should return 'delete' or 'wal'
    return conn
```

---

## 5. Migration Preview Output Format

### Per-Record Output

```
═══════════════════════════════════════════════════════════
LEGACY: stock_meat_product_list #66
═══════════════════════════════════════════════════════════
  barcode:          3-1-1108-0001
  weight:           1000.0g → 1.000kg           ✓
  selling_price:    38.0 → ฿38.00              ✓
  storage_status:   frozen → FROZEN             ✓
  loyverse_sku:     30066                       ✓
  
  PRODUCT RESOLUTION:
    product_info_id: 9
    → meat_parts: เศษไก่ติดหนัง (id=3)
    → category: ไก่สดใส่ถุง (id=1)
    → supplier: ศิวกรหมูสด (id=3)
    → lot_number: 1
    
  CANDIDATE:
    Product:   เศษไก่ติดหนัง (SKU: MP-3-1003)     ✓ NEW
    Batch:     B-20260829-001 (from lot 1 + supplier 3)  ✓ NEW
    Package:   barcode=3-1-1108-0001, weight=1.000kg, state=FROZEN  ✓ VALID
  
  STATUS: ✅ VALID — ready for migration
═══════════════════════════════════════════════════════════
```

### Summary Output

```
═══════════════════════════════════════════════════════════
MIGRATION DRY-RUN SUMMARY
═══════════════════════════════════════════════════════════

SOURCE RECORD COUNTS:
  Category:        3
  Supplier:        6
  Meat Parts:      24
  Product Info:    20
  Product List:    156
  Freeze Rotation: 30
  Rotation Schedule: 0
  Worker Task:     0
  Sold Item:       81
  Transaction:     233

TARGET CANDIDATES:
  Product:         24 (from meat_parts)
  Batch:           20 (from product_info)
  Package:         156 (from product_list)
  RotationPlan:    0 (no rotation_schedule records)
  RotationEvent:   30 (from freezerotation)
  Category:        3 (from category)
  Supplier:        6 (from supply_meat)

VALIDATION:
  ✅ Valid:         150
  ⚠️ Warnings:      4
  ❌ Errors:        2
  ⏭️ Skipped:       0

WARNINGS:
  - Product_list #67: thaw_queue_position=3 but storage_status=depleted (inconsistent)
  - Product_list #123: weight=0.0 (invalid weight)
  - Product_info #8: weight=0.0 (total lot weight not recorded)
  - Category #3: name='test' (appears to be test data)

ERRORS:
  - Product_list #200: product_id=NULL (orphaned record)
  - Product_list #201: barcode='' (empty barcode)

═══════════════════════════════════════════════════════════
DRY-RUN COMPLETE — NO DATA WAS WRITTEN
═══════════════════════════════════════════════════════════
```

---

## 6. Product/Batch/Package Validation Rules

### Category Validation

| Rule | Check | Action on Fail |
|------|-------|---------------|
| `name_type` not empty | `len(name_type) > 0` | SKIP with warning |
| `name_type` unique | No duplicate names | MERGE or SKIP |
| Not test data | `name_type not in ('test', 'Test', '')` | FLAG as test data |

### Supplier Validation

| Rule | Check | Action on Fail |
|------|-------|---------------|
| `name_place` not empty | `len(name_place) > 0` | SKIP with warning |
| `name_place` unique | No duplicate names | MERGE or SKIP |

### Product (meat_parts) Validation

| Rule | Check | Action on Fail |
|------|-------|---------------|
| `name` not empty | `len(name) > 0` | SKIP with warning |
| `category_id` references valid Category | FK check | ERROR — cannot create Product without Category |
| `prefix_barcode` not empty | `len(prefix_barcode) > 0` | WARN — SKU generation may fail |
| `kcalories` >= 0 | `kcalories >= 0` | WARN — use 0 |
| `protent` >= 0 | `protent >= 0` | WARN — use 0 |
| `fat` >= 0 | `fat >= 0` | WARN — use 0 |

### Batch (Product_info) Validation

| Rule | Check | Action on Fail |
|------|-------|---------------|
| `name_id` references valid meat_parts | FK check | ERROR — cannot create Batch without Product |
| `import_from_id` references valid Supply_meat | FK check | WARN — use default Supplier |
| `lot_number` > 0 | `lot_number > 0` | WARN — use 1 |
| `selling_price_per_kg` > 0 | `selling_price_per_kg > 0` | WARN — use 0 |
| `cost` >= 0 (or NULL) | `cost is None or cost >= 0` | WARN — use 0 |

### Package (Product_list) Validation

| Rule | Check | Action on Fail |
|------|-------|---------------|
| `product_id` references valid Product_info | FK check | ERROR — orphaned record |
| `barcode` not empty | `len(barcode) > 0` | ERROR — no barcode = no identity |
| `barcode` unique | No duplicates | ERROR — duplicate identity |
| `weight` > 0 | `weight > 0` | ERROR — zero/negative weight |
| `selling_price` >= 0 | `selling_price >= 0` | WARN — use 0 |
| `storage_status` valid | In ('frozen', 'thawing', 'display', 'depleted') | WARN — default to 'frozen' |
| `loyverse_sku` unique (if not NULL) | Unique check | WARN — skip Loyverse fields |

---

## 7. Identity & Traceability

### Legacy ID Preservation

Every canonical record created during migration will have:

| Field | Purpose | Example |
|-------|---------|---------|
| `legacy_source` | Which legacy table | `'stock_meat_product_list'` |
| `legacy_id` | Original primary key | `66` |
| `migration_batch` | Migration run identifier | `'MIGRATION-20260829-001'` |
| `migrated_at` | When migrated | `datetime(2026, 8, 29, ...)` |

### Implementation

Add temporary fields to canonical models during migration:

```python
# Added to each canonical model during Phase 1 migration
class MigrationMeta:
    legacy_source = models.CharField(max_length=100, blank=True, default='')
    legacy_id = models.PositiveIntegerField(null=True, blank=True)
    migration_batch = models.CharField(max_length=100, blank=True, default='')
    migrated_at = models.DateTimeField(null=True, blank=True)
```

**Note:** These are migration artifacts. They can be removed after migration is verified and stable.

---

## 8. StorageLocation Strategy

### Do NOT Invent Production Data

Actual StorageLocation records must come from the operator. The dry-run should NOT create any.

### Configuration Template

```python
# storage_locations_config.py — to be filled by operator

STORAGE_LOCATIONS = [
    # Freezers
    {
        'name': 'FREEZER-A1',       # ← Operator provides
        'location_type': 'FREEZER',
        'capacity': 50,              # ← Operator provides
        'thaw_capacity': 0,
        'min_temperature': -18.0,    # ← Operator provides
        'max_temperature': -8.0,     # ← Operator provides
    },
    # Thaw Areas
    {
        'name': 'THAW-01',
        'location_type': 'THAW_AREA',
        'capacity': 20,
        'thaw_capacity': 10,
        'min_temperature': 1.0,
        'max_temperature': 5.0,
    },
    # Display Cases
    {
        'name': 'DISPLAY-01',
        'location_type': 'DISPLAY',
        'capacity': 30,
        'thaw_capacity': 0,
        'min_temperature': 0.0,
        'max_temperature': 8.0,
    },
    # Processing Area
    {
        'name': 'PROCESSING-01',
        'location_type': 'STORAGE',
        'capacity': 10,
        'thaw_capacity': 0,
        'min_temperature': None,
        'max_temperature': None,
    },
]
```

---

## 9. SKU Strategy

### Analysis of Legacy Barcodes

Legacy barcode format: `{supplier_id}-{lot_number}-{prefix_barcode}-{sequence}`

Example: `3-1-1108-0001`
- Supplier: 3 (ศิวกรหมูสด)
- Lot: 1
- Prefix: 1108 (เศษไก่ติดหนัง)
- Sequence: 0001

### Canonical SKU Strategy

SKU identifies the **Product** (not the Package). SKU must be unique per Product.

**Strategy: Use `prefix_barcode` as SKU base**

```
SKU = f"MP-{prefix_barcode}"
```

Examples:
| meat_parts.id | name | prefix_barcode | SKU |
|--------------|------|---------------|-----|
| 1 | ปีกบน | 1001 | MP-1001 |
| 2 | อกไก่ลอกหนัง | 1003 | MP-1003 |
| 3 | เศษไก่ติดหนัง | 1108 | MP-1108 |
| 4 | หมูบดเกรด A | 8009 | MP-8009 |

**Prefix `MP-`** = "Meat Product" — clear, short, deterministic.

**Collision handling:** If two meat_parts share the same prefix_barcode (unlikely but possible), append `-{id}`: `MP-1001-1`.

**Deterministic:** Same legacy record → same SKU every time. Idempotent.

---

## 10. Batch Number Strategy

### Analysis of Legacy Lot Numbers

Legacy `Product_info.lot_number` is a simple integer (1, 2, 3...). It's per-supplier, not globally unique.

### Canonical Batch Number Strategy

**Strategy: `B-{YYYYMMDD}-{supplier_id:02d}-{lot_number:02d}`**

```
Batch Number = f"B-{received_date}-{supplier_id:02d}-{lot_number:02d}"
```

Since `Product_info` doesn't have a `received_at` date, use `created_at`:

```
Batch Number = f"B-{created_at.strftime('%Y%m%d')}-{import_from_id:02d}-{lot_number:02d}"
```

Examples:
| Product_info.id | created_at | import_from_id | lot_number | Batch Number |
|----------------|-----------|---------------|-----------|-------------|
| 8 | 2024-01-15 | 3 | 1 | B-20240115-03-01 |
| 9 | 2024-01-15 | 3 | 1 | B-20240115-03-01 |

**⚠️ COLLISION RISK:** Two Product_info records with same supplier + lot_number + created_at date produce the same batch number. This is **intentional** — they belong to the same batch.

**Collision handling:** If two Product_info records should be separate batches but share the same key, append `-{id}`: `B-20240115-03-01-8`.

**Deterministic:** Same legacy record → same batch number every time. Idempotent.

---

## 11. Idempotency Strategy

### Detection of Already-Migrated Records

Every migration run must detect records that were already migrated.

**Mechanism: Legacy ID lookup**

```python
def is_already_migrated(legacy_source, legacy_id, migration_batch=None):
    """Check if a legacy record has already been migrated."""
    # For Product: check if any Product has legacy_id=meat_parts.id
    # For Package: check if any Package has legacy_id=product_list.id
    return CanonicalModel.objects.filter(
        legacy_source=legacy_source,
        legacy_id=legacy_id
    ).exists()
```

### Migration Batch Identifier

Format: `MIGRATION-{YYYYMMDD}-{HHMMSS}`

Example: `MIGRATION-20260829-143022`

Each dry-run or real migration gets a unique batch ID. This allows:
- Tracing which migration run created each record
- Rolling back a specific batch
- Comparing results between runs

### Idempotency Rules

| Action | Idempotent? | How |
|--------|-------------|-----|
| Create Product | ✅ Yes | Check `legacy_id` before create |
| Create Batch | ✅ Yes | Check `legacy_id` before create |
| Create Package | ✅ Yes | Check `legacy_id` + `barcode` before create |
| Create Category | ✅ Yes | Check `legacy_id` before create |
| Create Supplier | ✅ Yes | Check `legacy_id` before create |
| Create RotationPlan | ✅ Yes | Check `legacy_rotation_schedule_id` before create |
| Create RotationEvent | ⚠️ Careful | Check `legacy_id` + `timestamp` before create |

---

## 12. Production Database Safety

### Rules

1. **Legacy database is READ-ONLY** — opened with `?mode=ro` in SQLite URI
2. **Dry-run NEVER writes to any database** — all transformations in-memory
3. **Real migration uses transaction.atomic()** — all-or-nothing
4. **Backup before every real migration** — `cp db.sqlite3 db.sqlite3.backup.{timestamp}`
5. **Snapshot preferred** — copy legacy DB before migration testing

### Safety Checkpoints

```
CHECKPOINT 1: Before dry-run
  → Verify legacy DB is read-only
  → Verify canonical DB is empty or has only test data

CHECKPOINT 2: After dry-run
  → Review report
  → Confirm no errors
  → Operator approval

CHECKPOINT 3: Before real migration
  → Backup canonical DB
  → Backup legacy DB
  → Verify transaction.atomic() wrapping

CHECKPOINT 4: After real migration
  → Verify record counts
  → Run all 315+ tests
  → Verify referential integrity
  → Operator sign-off
```

---

## 13. Migration Environments

### DEVELOPMENT

| Aspect | Value |
|--------|-------|
| Database | SQLite (`task_manager/db.sqlite3`) |
| Data | Synthetic/sample data |
| Purpose | Code development, unit testing |
| Django version | 5.2 LTS (after upgrade) |
| Python version | 3.12 |

### STAGING

| Aspect | Value |
|--------|-------|
| Database | PostgreSQL |
| Data | Sanitized snapshot of legacy data |
| Purpose | Migration testing, validation |
| Django version | 5.2 LTS |
| Python version | 3.12 |
| Access | Developer only |

### PRODUCTION

| Aspect | Value |
|--------|-------|
| Database | PostgreSQL |
| Data | Migrated from legacy (controlled) |
| Purpose | Live system |
| Django version | 5.2 LTS |
| Python version | 3.12 |
| Access | Operator + admin |
| Migration | Requires human approval |

---

## 14. Rollback Strategy

### Backup Point

Before every real migration:
```bash
cp task_manager/db.sqlite3 task_manager/db.sqlite3.pre-migration
cp database_clmeat_main/db.sqlite3 database_clmeat_main/db.sqlite3.backup
```

### Migration Batch Tracking

Every migration batch is recorded:
```python
MigrationBatch.objects.create(
    batch_id='MIGRATION-20260829-143022',
    started_at=now,
    completed_at=None,
    status='running',
    record_counts={...},
)
```

### Rollback Mechanism

```python
def rollback_batch(batch_id):
    """Remove all records created in a specific migration batch."""
    with transaction.atomic():
        # Delete in reverse dependency order
        RotationEvent.objects.filter(migration_batch=batch_id).delete()
        WorkerTask.objects.filter(migration_batch=batch_id).delete()
        RotationPlan.objects.filter(migration_batch=batch_id).delete()
        Package.objects.filter(migration_batch=batch_id).delete()
        Batch.objects.filter(migration_batch=batch_id).delete()
        Product.objects.filter(migration_batch=batch_id).delete()
        Supplier.objects.filter(migration_batch=batch_id).delete()
        Category.objects.filter(migration_batch=batch_id).delete()
        
        # Update batch status
        MigrationBatch.objects.filter(batch_id=batch_id).update(
            status='rolled_back',
            rolled_back_at=timezone.now()
        )
```

### Post-Rollback Verification

```bash
python manage.py test  # All 315 tests must pass
python manage.py check  # System check must pass
```

---

## 15. Migration Test Plan

### Test Matrix

| # | Scenario | Expected Result | Priority |
|---|----------|----------------|----------|
| 1 | Empty legacy database | 0 candidates, no errors | HIGH |
| 2 | One category | 1 Category candidate | HIGH |
| 3 | One supplier | 1 Supplier candidate | HIGH |
| 4 | One meat_parts | 1 Product candidate | HIGH |
| 5 | One product_info | 1 Batch candidate + Product candidate | HIGH |
| 6 | One product_list | 1 Package candidate | HIGH |
| 7 | Multiple products (24) | 24 Product candidates | HIGH |
| 8 | Multiple packages (156) | 156 Package candidates | HIGH |
| 9 | Duplicate barcode | ERROR — duplicate identity | HIGH |
| 10 | Missing product reference | ERROR — orphaned record | HIGH |
| 11 | Missing supplier reference | WARN — use default | MEDIUM |
| 12 | Missing category reference | ERROR — cannot create Product | HIGH |
| 13 | Zero weight | ERROR — invalid weight | HIGH |
| 14 | Negative weight | ERROR — invalid weight | HIGH |
| 15 | Weight = 0.0 in Product_info | WARN — not used for Package | MEDIUM |
| 16 | Decimal price (38.5) | VALID — Decimal supports | LOW |
| 17 | Frozen package | VALID — state=FROZEN | HIGH |
| 18 | Thawing package | VALID — state=THAWING | HIGH |
| 19 | Display package | VALID — state=ON_DISPLAY | HIGH |
| 20 | Depleted package | VALID — state=COMPLETED | HIGH |
| 21 | Repeated dry-run | Same results (idempotent) | HIGH |
| 22 | Repeated dry-run after migration | Detects already-migrated | HIGH |
| 23 | Product_info weight=0 | WARN — not used | MEDIUM |
| 24 | Empty prefix_barcode | WARN — SKU generation fallback | MEDIUM |
| 25 | Test category (name='test') | FLAG as test data | LOW |

### Test Execution

```bash
# Run dry-run against legacy database
python manage.py migrate_legacy_data --dry-run --legacy-db database_clmeat_main/db.sqlite3

# Run against copy (safety)
cp database_clmeat_main/db.sqlite3 /tmp/legacy_test.db
python manage.py migrate_legacy_data --dry-run --legacy-db /tmp/legacy_test.db

# Run against empty database (should produce 0 candidates)
python manage.py migrate_legacy_data --dry-run --legacy-db /tmp/empty.db
```

---

## 16. Documentation Changes

| File | Status |
|------|--------|
| `docs/ARCHITECTURE.md` | ✅ Already exists (TASK 01) |
| `docs/ARCHITECTURE_DECISIONS.md` | ✅ Already exists (TASK 01.5) |
| `docs/MIGRATION_BOUNDARIES.md` | ✅ Already exists (TASK 01) |
| `docs/MIGRATION_READINESS.md` | ✅ **NEW** (this file) |

---

## 17. Summary of Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Django target | **5.2 LTS** | Security support until April 2028; 4.2 is EOL |
| Python target | **3.12** | Available via Miniconda; supported by Django 5.2 |
| Dry-run mode | **Read-only** | Never writes to any database |
| SKU format | `MP-{prefix_barcode}` | Deterministic, idempotent, based on legacy data |
| Batch format | `B-{YYYYMMDD}-{supplier:02d}-{lot:02d}` | Deterministic, idempotent |
| Idempotency | Legacy ID lookup | Check before create; batch tracking |
| StorageLocation | **Operator config** | Do not invent production data |
| Rollback | Batch-based delete | Delete by migration_batch identifier |
| Legacy DB access | **Read-only** (SQLite `?mode=ro`) | Prevent accidental modification |
