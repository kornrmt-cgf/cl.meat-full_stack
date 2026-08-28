# CL.MEAT Architecture Decision Lock

> TASK 01.5 — Resolved before data migration begins

---

## 1. Database Terminology

| Term | Definition | Current Instance |
|------|-----------|-----------------|
| **Canonical Application** | The single Django project that owns all production models | `task_manager` |
| **Canonical Domain Model** | The authoritative model definition for each business entity | `inventory.Product`, `inventory.Package`, `planning.RotationPlan`, `tasks.Task` |
| **Canonical Database Schema** | The Django migrations defining the production tables | `task_manager/*/migrations/` |
| **Development Database** | SQLite file used for local development and testing | `task_manager/db.sqlite3` |
| **Test Database** | In-memory or temporary database created by `manage.py test` | `:memory:` (Django default) |
| **Production Database** | The database serving real users in production | **PostgreSQL** (target, not yet deployed) |
| **Legacy Source Database** | The database containing data to be migrated from | `database_clmeat_main/db.sqlite3` |
| **Migration Source** | The legacy database + transformation scripts | `database_clmeat_main/` extraction scripts |

### IMPORTANT: Production Database Architecture

```
CURRENT (Development):
  task_manager/db.sqlite3  ←  SQLite, dev/test only
  
TARGET (Production):
  task_manager → PostgreSQL
  ├── Canonical tables (inventory, planning, operations, tasks, accounts)
  ├── Business integration tables (future: sales, finance, loyverse)
  └── Managed by Django migrations
  
LEGACY (Read-only after migration):
  database_clmeat_main/db.sqlite3  ←  Original operational data
  ├── Read by migration scripts
  ├── Never written to again
  └── Retained for audit trail
```

**SQLite is NOT the production database.**  
**PostgreSQL is the intended production target.**  
**The canonical application is `task_manager`.**

---

## 2. Product / Batch / Package Semantics

### 2A. Legacy Models — What Each Row Represents

#### `stock_meat.Category`

| Aspect | Value |
|--------|-------|
| One row represents | A product type category (e.g., "หมู", "ไก่") |
| Is it a master product? | No — it's a category classifier |
| Is it mutable? | Rarely — category names are stable |
| Contains historical state? | No |
| Primary key | `ids` (AutoField) |

#### `stock_meat.Supply_meat`

| Aspect | Value |
|--------|-------|
| One row represents | A supplier / source location |
| Is it a master product? | No — it's a supplier entity |
| Is it mutable? | Rarely |
| Contains historical state? | No |
| Primary key | `ids` (AutoField) |

#### `stock_meat.meat_parts`

| Aspect | Value |
|--------|-------|
| One row represents | A specific meat cut (e.g., "สะโพกหมู", "คอหมู") |
| Is it a master product? | YES — this is the product definition |
| Is it mutable? | Rarely — meat part names are stable |
| Contains historical state? | No |
| Primary key | `id` (AutoField) |
| Key fields | `name`, `prefix_barcode`, `kcalories`, `protent`, `fat` |

#### `stock_meat.Product_info`

| Aspect | Value |
|--------|-------|
| One row represents | **A specific lot/batch of a meat part from a supplier** |
| Is it a master product? | NO — it's a batch-level record with pricing |
| Is it mutable? | YES — `weight`, `cost`, `selling_price_per_kg` can change |
| Contains historical state? | Partially — `weight` changes as packages are sold |
| Primary key | `id` (AutoField) |
| Key fields | `name` (FK→meat_parts), `type_product` (FK→Category), `import_from` (FK→Supply_meat), `lot_number`, `weight` (grams), `cost`, `selling_price_per_kg`, `max_display_count` |

**CRITICAL INSIGHT:** `Product_info` conflates three concepts:
1. **Product definition** (via FK to `meat_parts`)
2. **Batch/lot** (via `lot_number` + `import_from`)
3. **Pricing** (via `cost`, `selling_price_per_kg`)

It is NOT simply "Product" — it's closer to "Product + Batch + Pricing" in one record.

#### `stock_meat.Product_list`

| Aspect | Value |
|--------|-------|
| One row represents | **One physical package of meat** — a sellable unit |
| Is it a master product? | NO — it's a physical item |
| Is it mutable? | YES — `storage_status`, `thaw_*`, `freeze_*`, `loyverse_*` fields change |
| Contains historical state? | YES — `storage_status` is current state; `FreezeRotation` is history |
| Primary key | `id` (AutoField) |
| Key fields | `product` (FK→Product_info), `barcode`, `weight` (grams), `selling_price`, `mfg` (auto_now_add), `storage_status`, all thaw/freeze/loyverse fields |

**CRITICAL INSIGHT:** `Product_list` is the equivalent of `Package` but it also embeds:
- Lifecycle state (`storage_status`)
- Thaw scheduling fields (`thaw_started_at`, `thaw_duration_hours`, etc.)
- Freeze scheduling fields (`freeze_started_at`, `freeze_end_at`, etc.)
- Loyverse integration fields (`loyverse_sku`, `loyverse_item_id`, etc.)
- Display scheduling fields (`entered_display_at`, `display_max_days`)

This is a "fat model" that mixes physical package identity with lifecycle state and integration state.

#### `inventory.Product` (Canonical)

| Aspect | Value |
|--------|-------|
| One row represents | **A product definition** — what the product IS |
| Is it a master product? | YES |
| Is it mutable? | Pricing can change; identity is stable |
| Contains historical state? | No |
| Key fields | `sku`, `name`, `name_thai`, `category` (FK), `supplier` (FK), `cost_per_kg`, `selling_price_per_kg`, `barcode_prefix`, nutrition |

#### `inventory.Batch` (Canonical)

| Aspect | Value |
|--------|-------|
| One row represents | **A receiving event** — products that arrived together |
| Is it a master product? | No — it's a grouping entity |
| Is it mutable? | Rarely |
| Contains historical state? | `received_at` is historical |
| Key fields | `batch_number`, `supplier` (FK), `received_at` |

#### `inventory.Package` (Canonical)

| Aspect | Value |
|--------|-------|
| One row represents | **One physical sellable unit** — with lifecycle tracking |
| Is it a master product? | No — it's a physical item |
| Is it mutable? | YES — `current_state`, `storage_location`, `selling_price` change |
| Contains historical state? | `current_state` is current; `StockMovement` + `RotationEvent` are history |
| Key fields | `product` (FK), `batch` (FK), `barcode`, `weight` (kg), `selling_price`, `packed_at`, `current_state`, `storage_location` (FK), loyalty fields |

---

### 2B. Field-Level Semantic Mapping

#### Product_info → Product + Batch

| Legacy Field | Target | Meaning | Transformation | Risk |
|-------------|--------|---------|---------------|------|
| `Product_info.name` (FK→meat_parts) | `Product.name` | Product name | `meat_parts.name` → `Product.name` | LOW |
| `Product_info.type_product` (FK→Category) | `Product.category` (FK→Category) | Category | Map Category.id → Category.id | LOW |
| `Product_info.import_from` (FK→Supply_meat) | `Product.supplier` (FK→Supplier) | Supplier | Map Supply_meat.id → Supplier.id | LOW |
| `Product_info.lot_number` | `Batch.batch_number` | Lot identifier | Generate: `B-{date}-{lot}` | MEDIUM |
| `Product_info.weight` (grams) | **NOT MIGRATED** | Total lot weight | Legacy uses total weight; canonical uses per-package weight | ⚠️ WARNING |
| `Product_info.cost` | `Product.cost_per_kg` | Unit cost | `cost / (weight/1000)` if total, or direct if per-kg | HIGH |
| `Product_info.selling_price_per_kg` | `Product.selling_price_per_kg` | Unit price | Direct copy (Float→Decimal) | LOW |
| `Product_info.max_display_count` | **NOT MIGRATED** | Display limit | Business rule, not model field | ✅ SAFE |
| `Product_info.created_at` | `Product.created_at` | Creation time | Direct copy | LOW |
| `meat_parts.name` | `Product.name` | Product name | Direct copy | LOW |
| `meat_parts.name` | `Product.name_thai` | Thai name | Direct copy | LOW |
| `meat_parts.prefix_barcode` | `Product.barcode_prefix` | Barcode prefix | Direct copy | LOW |
| `meat_parts.kcalories` | `Product.kcalories` | Nutrition | Float→Decimal | LOW |
| `meat_parts.protent` | `Product.protein` | Nutrition | Float→Decimal, fix typo | LOW |
| `meat_parts.fat` | `Product.fat` | Nutrition | Float→Decimal | LOW |

**Key decision:** One `Product_info` row creates ONE `Product` (from `meat_parts`) and ONE `Batch` (from `lot_number` + `import_from`). Multiple `Product_info` rows with the same `meat_parts` but different `lot_number` create different `Batch` records but the same `Product`.

**Problem:** `Product_info.weight` is the total weight of the lot, not per-package. The canonical `Product` model doesn't store total weight — that's per-package. **Do not migrate `weight` to Product.**

#### Product_list → Package

| Legacy Field | Target | Meaning | Transformation | Risk |
|-------------|--------|---------|---------------|------|
| `Product_list.product` (FK→Product_info) | `Package.product` (FK→Product) + `Package.batch` (FK→Batch) | Product + batch | Resolve Product_info → Product + Batch | HIGH |
| `Product_list.barcode` | `Package.barcode` | Unique barcode | Direct copy (already unique) | LOW |
| `Product_list.weight` (grams) | `Package.weight` (kg) | Package weight | **÷ 1000, Float→Decimal** | MEDIUM |
| `Product_list.selling_price` (Float) | `Package.selling_price` (Decimal) | Package price | Float→Decimal | LOW |
| `Product_list.mfg` (auto_now_add) | `Package.packed_at` | When packaged | Direct copy | LOW |
| `Product_list.storage_status` | `Package.current_state` | Lifecycle state | **Map values** (see below) | HIGH |
| `Product_list.loyverse_sku` | `Package.loyverse_sku` | Loyverse SKU | Direct copy | LOW |
| `Product_list.loyverse_item_id` | `Package.loyverse_item_id` | Loyverse item ID | Direct copy | LOW |
| `Product_list.loyverse_variant_id` | `Package.loyverse_variant_id` | Loyverse variant ID | Direct copy | LOW |
| `Product_list.loyverse_synced` | `Package.loyverse_synced` | Sync status | Direct copy | LOW |
| `Product_list.loyverse_synced_at` | `Package.loyverse_synced_at` | Sync time | Direct copy | LOW |
| `Product_list.activated` | **NOT MIGRATED** | Activation flag | Unused in canonical | ✅ SAFE |
| `Product_list.thaw_started_at` | **→ RotationPlan** | Thaw start time | Extract to RotationPlan | MEDIUM |
| `Product_list.thaw_duration_hours` | **→ RotationPlan.thaw_duration** | Thaw duration | hours → Duration (timedelta) | MEDIUM |
| `Product_list.thaw_queue_position` | **→ ThawQueueEntry.queue_position** | Queue position | Extract to ThawQueueEntry | MEDIUM |
| `Product_list.thaw_scheduled_at` | **→ RotationPlan.planned_thaw_queue_at** | Queue time | Extract to RotationPlan | MEDIUM |
| `Product_list.thaw_target_ready_at` | **→ RotationPlan.target_ready_at** | Target ready | Extract to RotationPlan | MEDIUM |
| `Product_list.freeze_started_at` | **→ RotationPlan.planned_freeze_start_at** | Freeze start | Extract to RotationPlan | MEDIUM |
| `Product_list.freeze_duration_minutes` | **→ RotationPlan.freeze_duration** | Freeze duration | minutes → Duration | MEDIUM |
| `Product_list.freeze_end_at` | **→ RotationPlan.planned_freeze_end_at** | Freeze end | Extract to RotationPlan | MEDIUM |
| `Product_list.freeze_target_temp` | **→ FreezeProfile.target_temperature** | Freezer temp | Extract to FreezeProfile | LOW |
| `Product_list.rotate_priority` | **NOT MIGRATED** | Priority | Will use RotationEngine priority | ✅ SAFE |
| `Product_list.last_alert_at` | **NOT MIGRATED** | Alert tracking | Will be recalculated | ✅ SAFE |
| `Product_list.display_max_days` | **NOT MIGRATED** | Display limit | Business rule, not in canonical Package | ✅ SAFE |
| `Product_list.entered_display_at` | **NOT MIGRATED** | Display start | Track via StockMovement | ✅ SAFE |

**Storage Status Mapping:**

| `Product_list.storage_status` | `Package.current_state` | Condition |
|------------------------------|------------------------|-----------|
| `'frozen'` | `FROZEN` | `thaw_queue_position == 0` |
| `'frozen'` | `THAW_QUEUED` | `thaw_queue_position > 0` |
| `'thawing'` | `THAWING` | — |
| `'display'` | `ON_DISPLAY` | — |
| `'depleted'` | `COMPLETED` | — |
| (new package) | `PACKED` | Default for new records |

---

## 3. Data Identity & Traceability

### Identity Preservation Strategy

Every migrated record must be traceable back to its legacy source.

| Canonical Model | Legacy Source | Traceability Field | Purpose |
|----------------|--------------|-------------------|---------|
| `Product` | `meat_parts.id` | `legacy_meat_parts_id` (new field) | Link to source product definition |
| `Product` | `Product_info.id` | `legacy_product_info_ids` (JSON) | List of all Product_info IDs that mapped to this Product |
| `Batch` | `Product_info.id` | `legacy_product_info_id` (new field) | Link to source batch/lot record |
| `Package` | `Product_list.id` | `legacy_product_list_id` (new field) | Link to source package record |
| `RotationPlan` | `RotationSchedule.id` | `legacy_rotation_schedule_id` (exists) | Already defined in model |
| `Category` | `Category.ids` | `legacy_category_id` (new field) | Link to source category |
| `Supplier` | `Supply_meat.ids` | `legacy_supplier_id` (new field) | Link to source supplier |

### Migration Audit Fields

Each canonical model should add (during migration, not now):

| Field | Type | Purpose |
|-------|------|---------|
| `migrated_from` | `CharField(max_length=50)` | Source system identifier (e.g., `'database_clmeat_main'`) |
| `migrated_at` | `DateTimeField(null=True)` | When the record was migrated |
| `migration_batch` | `CharField(max_length=100, null=True)` | Migration batch identifier for grouping |

**Decision:** Add legacy traceability fields during Phase 1 migration. Do NOT add them to the canonical model definitions now — they are migration artifacts, not permanent schema.

---

## 4. Event Ownership Matrix

| Event Type | Owner | Purpose | Creates Task? | Creates StockMovement? | Changes Package State? | Audit Requirement |
|-----------|-------|---------|--------------|----------------------|----------------------|-------------------|
| **TaskActivity** | `tasks.TaskActivity` | Human task history (created, assigned, started, completed, problem) | No (IS the task history) | No | No | Every status change on Task |
| **RotationEvent** | `operations.RotationEvent` | Package lifecycle state transitions + manual overrides | No | No | No (records the transition, doesn't cause it) | Every state transition on Package |
| **StockMovement** | `inventory.StockMovement` | Physical location/quantity movement of packages | No | No (IS the movement record) | No (records the movement) | Every physical movement |
| **Package state transition** | `common.state_machine` | Lifecycle state change via `transition_package()` | No (but auto-completes WorkerTask) | Optionally (services create StockMovement) | **YES** (this IS the state change) | Creates RotationEvent |
| **TaskEvent** | `operations.TaskEvent` | Legacy operational task event log | No (IS the event log for WorkerTask) | No | No | Every action on WorkerTask |
| **FreezeRotation** | `stock_meat.FreezeRotation` (LEGACY) | Legacy audit trail for rotation actions | No | No | No (legacy records state changes inline) | Legacy audit trail |

### Key Distinction: RotationEvent vs StockMovement

- **RotationEvent** = "Package X changed from FROZEN to THAWING at 14:00, actor: Somchai"
- **StockMovement** = "Package X moved from FREEZER-A3 to THAW_AREA at 14:00, actor: Somchai"

A state transition (RotationEvent) can occur without physical movement (e.g., READY_FOR_THAW → THAW_QUEUED is a queue status change, not a physical move). A physical movement (StockMovement) can occur without state change (e.g., moving a FROZEN package from one freezer shelf to another).

### What triggers what:

```
Worker completes task (TaskActivity created)
    → state_machine.transition_package() called
        → Package.current_state updated
        → RotationEvent created (audit)
        → WorkerTask auto-completed (TaskEvent created)
        → Optionally: StockMovement created (if physical move)
```

---

## 5. AUTO / CUSTOM / Safety Architecture

### Definitions

| Mode | Definition | Who Controls | Override Allowed? |
|------|-----------|-------------|-------------------|
| **AUTO** | System calculates duration from Profile + weight + configuration | System (automatic) | Admin can switch to CUSTOM |
| **CUSTOM** | Administrator explicitly specifies the duration | Human administrator | Yes — this IS the override |
| **SAFETY CONSTRAINT** | Minimum/maximum limits that should not be casually overridden | System configuration | Only with documented reason + admin role |

### What each mode means operationally

**AUTO mode:**
```
FreezeProfile provides: minimum_duration, default_duration, buffer_duration
ThawProfile provides: minimum_duration, default_duration, buffer_duration, weight_threshold_kg, weight_scale_factor

System calculates:
  freeze_duration = f(weight, profile) + buffer
  thaw_duration = f(weight, profile) + buffer
  
Timeline is backward-scheduled from target_ready_at.
```

**CUSTOM mode:**
```
Administrator provides:
  freeze_override = timedelta(hours=X)
  thaw_override = timedelta(hours=Y)
  override_reason = "Heavy package needs longer"
  overridden_by = "Somchai"

System uses override values instead of AUTO calculation.
Audit trail records: who, what, when, why.
```

**SAFETY CONSTRAINT (future):**
```
System enforces:
  freeze_duration >= profile.minimum_duration (even in CUSTOM mode)
  thaw_duration >= profile.minimum_duration (even in CUSTOM mode)
  target_ready_at >= now + minimum_lead_time
  
These are configuration-level constraints, not food-safety rules.
The system does NOT enforce food-safety rules — it enforces operational constraints.
```

### Existing Formulas — Operational Status

| Formula | Source | Status | Evidence |
|---------|--------|--------|----------|
| Freeze: `base × mass^0.67` | `stock_meat.schedule` (Newton's law) | **CURRENT OPERATIONAL LOGIC** | Used in production; not formally validated as food-safety rule |
| Freeze: profile weight brackets | `planning.services` | **CURRENT OPERATIONAL LOGIC** | New implementation; not formally validated |
| Thaw: `base × mass^0.67` | `stock_meat.schedule` (Newton's law) | **CURRENT OPERATIONAL LOGIC** | Used in production; not formally validated |
| Thaw: weight interpolation | `planning.services` | **CURRENT OPERATIONAL LOGIC** | New implementation; not formally validated |
| Buffer: 120 minutes | `stock_meat.schedule` DEFAULT_BUFFER_MINUTES | **CURRENT OPERATIONAL LOGIC** | Hardcoded in legacy; configurable in canonical |

**⚠️ WARNING:** None of these formulas have formal food-safety validation evidence. They are operational heuristics that have been used in production. The system should document them as "operational logic" and allow configuration — not treat them as safety rules.

### Configuration Hierarchy

```
FreezeProfile (system-wide defaults)
    ↓
ProductPlanningProfile (per-product overrides)
    ↓
RotationPlan.freeze_override / thaw_override (per-package CUSTOM)
    ↓
Minimum duration constraints (SAFETY CONSTRAINT — future)
```

---

## 6. RotationPlan Versioning

### Current Limitation

```
Package → OneToOne → RotationPlan
```

One package can have exactly ONE plan. If the plan is cancelled, the package cannot get a new plan without deleting the old record.

### Desired Future Architecture

```
Package
    ↓ ForeignKey (multiple plans)
    ├── RotationPlan (status=CANCELLED, superseded_at=now)    ← historical
    ├── RotationPlan (status=CANCELLED, superseded_at=now)    ← historical  
    └── RotationPlan (status=IN_PROGRESS, is_active=True)     ← current active
```

### Plan Lifecycle State Diagram

```
                    ┌─────────┐
                    │  DRAFT  │ ← Plan being configured
                    └────┬────┘
                         │ confirm
                         ▼
                    ┌──────────┐
                    │ PLANNED  │ ← Timeline calculated, tasks generated
                    └────┬─────┘
                         │ tasks start executing
                         ▼
                    ┌──────────┐
                    │IN_PROGRESS│ ← At least one task completed
                    └──┬───┬───┘
                       │   │
            ┌──────────┘   └──────────┐
            ▼                         ▼
     ┌────────────┐            ┌───────────┐
     │ COMPLETED  │            │ CANCELLED │
     │ (terminal) │            │ (terminal)│
     └────────────┘            └───────────┘
            │                         │
            │                    ┌────┴────┐
            │                    ▼         │
            │             ┌───────────┐    │
            │             │ AT_RISK   │    │
            │             │(deadline  │    │
            │             │ approaching)   │
            │             └───────────┘    │
            │                              │
            │                         ┌────┘
            │                         ▼
            │                  ┌──────────┐
            │                  │ OVERDUE  │
            │                  │(past     │
            │                  │ target)  │
            │                  └──────────┘
            ▼
     ┌────────────┐
     │  (future)  │
     │ SUPERSEDED │ ← Replaced by a newer plan
     └────────────┘
```

### Re-Planning Rule

When a package needs a new plan after cancellation:
1. Old plan status → `CANCELLED` (or `SUPERSEDED` if replaced)
2. New plan created with `is_active=True`
3. Old plan's `is_active` set to `False`
4. Audit trail records both plans

**Decision for now:** Keep OneToOne. Change to ForeignKey in Phase 2 after data migration is stable. Document the limitation.

---

## 7. Human Task Boundary

### Canonical: `tasks.Task`

All human work goes through `tasks.Task`. No exceptions.

### WorkerTask → Task Field Mapping

| WorkerTask Field | Task Field | Missing Data | Proposed Solution |
|-----------------|------------|-------------|-------------------|
| `package` (FK→Package) | **NEW: `Task.package`** (FK→Package, nullable) | Task has no package field | Add nullable FK to Task model |
| `rotation_plan` (FK→RotationPlan) | **NEW: `Task.rotation_plan`** (FK→RotationPlan, nullable) | Task has no rotation_plan field | Add nullable FK to Task model |
| `task_type` (e.g., FREEZE_START) | `Task.category = 'ROTATION'` + `Task.title` (descriptive) | Task has no task_type field | Map task_type to title + use category |
| `scheduled_at` | `Task.start_at` | — | Direct mapping |
| `status` (PENDING→COMPLETED) | `Task.status` (scheduled→completed) | Different status values | Status mapping table |
| `completed_at` | `Task.completed_at` | — | Direct mapping |
| `completed_by` (FK→User) | `TaskAssignment.completed_at` + `TaskAssignment.assigned_to` | WorkerTask has single completion; Task has assignment model | Create TaskAssignment on migration |
| `notes` | `Task.notes` | — | Direct mapping |
| `created_at` | `Task.created_at` | — | Direct mapping |
| `updated_at` | `Task.updated_at` | — | Direct mapping |

### Status Mapping Table

| WorkerTask Status | Task Status | Notes |
|------------------|-------------|-------|
| `PENDING` | `scheduled` | — |
| `IN_PROGRESS` | `in_progress` | — |
| `COMPLETED` | `completed` | — |
| `SKIPPED` | `cancelled` | Closest equivalent |
| `OVERDUE` | `scheduled` (with is_overdue check) | Task calculates overdue from deadline |
| `CANCELLED` | `cancelled` | — |

### Task Type → Title Mapping

| WorkerTask task_type | Task.title | Task.category |
|---------------------|------------|---------------|
| `FREEZE_START` | "เริ่มแช่แข็ง {package.display_name}" | `ROTATION` |
| `FREEZE_CHECK` | "ตรวจสอบแช่แข็ง {package.display_name}" | `ROTATION` |
| `MOVE_TO_THAW_QUEUE` | "เข้าคิวละลาย {package.display_name}" | `ROTATION` |
| `THAW_START` | "เริ่มละลาย {package.display_name}" | `ROTATION` |
| `THAW_CHECK` | "ตรวจสอบละลาย {package.display_name}" | `ROTATION` |
| `THAW_COMPLETE` | "ละลายเสร็จ {package.display_name}" | `ROTATION` |
| `MOVE_TO_DISPLAY` | "นำออกวางขาย {package.display_name}" | `ROTATION` |
| `REFREEZE` | "กลับแช่แข็ง {package.display_name}" | `ROTATION` |
| `PROCESS` | "แปรรูป {package.display_name}" | `ROTATION` |
| `DISCARD` | "ทิ้งสินค้า {package.display_name}" | `ROTATION` |

### Decision: When to merge

**NOT NOW.** Merge WorkerTask → Task in Phase 2, after:
1. Phase 1 data migration is complete and verified
2. All legacy views have been rewritten to use canonical models
3. The state_machine._auto_complete_worker_tasks() coupling is refactored

---

## 8. Legacy Business Domain Boundaries

| Domain | Current Source | Action | Timeline | Notes |
|--------|---------------|--------|----------|-------|
| **Pricing** | `inventory.services.calculate_package_price()` | **KEEP** | Now | 3 modes: price_per_kg, cost_margin, discount |
| **Sales** | `stock_meat.sold_items` | **REWRITE** | Phase 3 | New `sales` app with canonical Package FK |
| **Loyverse** | `stock_meat.loyverse` + `stock_meat.loyverse_export` | **REWRITE** | Phase 3 | New `integrations` app; adapter for Package model |
| **Finance** | `stock_meat.Transaction` + `stock_meat.ExpenseCategory` | **REWRITE** | Phase 3 | New `finance` app |
| **Electricity** | `stock_meat.ElectricityBill` + `stock_meat.DailyElectricity` | **REWRITE** | Phase 3 | New `utility` app |
| **Barcode** | `inventory.services.generate_barcode()` | **KEEP** | Now | Atomic, race-safe; legacy `get_next_pack_number` is superseded |
| **Label Printing** | `inventory.label_service` + `stock_meat.niimbot` | **ADAPT** | Phase 2 | Canonical service exists; legacy NIIMBOT code to be ported |
| **Product Processing** | `stock_meat.ProductProcessing` + `stock_meat.ProcessType` | **REWRITE** | Phase 3 | New `processing` app |
| **Freeze/Thaw Calculation** | `planning.services` (canonical) + `stock_meat.schedule` (legacy) | **KEEP canonical** | Now | Profile-based is primary; Newton's law available as reference |
| **Rotation Decision** | `planning.rotation.RotationEngine` (canonical) | **KEEP** | Now | Superior to legacy `auto_rotation_check` |
| **State Machine** | `common.state_machine` (canonical) | **KEEP** | Now | Single entry point; legacy `freeze_queue.py` inline changes are superseded |
| **Dashboard** | `stock_meat.dashboard` + `stock_meat.freeze_queue` | **REWRITE** | Phase 3 | Unified dashboard using canonical models |
| **Views (all)** | `stock_meat.views` (2000+ lines) | **REWRITE** | Phase 3 | Must be rewritten as Django views/templates |

---

## 9. Django Version Decision

### Current State

| Project | Django Version | Status |
|---------|---------------|--------|
| `task_manager` | Django 4.2.30 | ✅ Active, canonical |
| `database_clmeat_main` | Django 4.2.30 | ✅ Same version, legacy |
| `project_management_clmeat` | Django 5.x (requirement) | ⚠️ Not installed; code already ported |

### Decision

**Target: Stay on Django 4.2.x (LTS) for now.**

| Factor | Assessment |
|--------|-----------|
| Django 4.2 LTS support | Until April 2026 (extended) — still within support |
| Stability | Proven; all 315 tests pass |
| Compatibility | All existing code works on 4.2 |
| project_management_clmeat code | Already ported to task_manager; no Django 5 features used |
| Risk of upgrading now | Medium — could introduce subtle breakage during consolidation |
| Risk of staying on 4.2 | Low — LTS is supported; upgrade can happen after consolidation |

**When to upgrade:** After Phase 3 (all legacy code rewritten). Choose Django 5.x LTS at that time.

**Do NOT upgrade now.** Focus on consolidation, not framework version.

---

## 10. Production Migration Strategy

### The Pipeline

```
Legacy Operational Database (database_clmeat_main/db.sqlite3)
    │
    ├── 1. READ-ONLY EXTRACTION
    │   ├── Export to CSV/JSON (read-only)
    │   ├── Never modify legacy database
    │   └── Record extraction timestamp
    │
    ├── 2. VALIDATION / TRANSFORMATION
    │   ├── Validate data integrity
    │   ├── Transform fields (weight g→kg, status mapping, etc.)
    │   ├── Resolve duplicates
    │   ├── Handle missing data
    │   └── Record transformation log
    │
    ├── 3. STAGING DATABASE
    │   ├── Load into staging PostgreSQL
    │   ├── Run Django migrations on staging
    │   ├── Import transformed data
    │   └── Verify row counts match
    │
    ├── 4. VALIDATION
    │   ├── Run all 315+ tests against staging
    │   ├── Verify referential integrity
    │   ├── Verify business rules
    │   ├── Compare old vs new query results
    │   └── Record validation report
    │
    ├── 5. HUMAN REVIEW
    │   ├── Present migration report to operator
    │   ├── Show sample records (old vs new)
    │   ├── Show any data quality issues
    │   ├── Operator approves or rejects
    │   └── Record approval
    │
    └── 6. PRODUCTION MIGRATION
        ├── Backup production database
        ├── Run migration scripts
        ├── Verify production data
        ├── Switch application to new database
        └── Monitor for issues
```

### NEVER

```
Legacy Database
    → direct destructive merge
    → production

This is prohibited under all circumstances.
```

### Migration Script Requirements

Every migration script must:
1. Be idempotent (safe to re-run)
2. Have rollback capability
3. Log every record migrated
4. Report any records skipped or transformed
5. Be reviewed by human before production execution
6. Have a backup taken before execution

---

## 11. Architecture Acceptance Criteria

### ✅ RESOLVED

| # | Question | Answer |
|---|----------|--------|
| 1 | What is the canonical application? | **`task_manager`** — the single Django project |
| 2 | What is the canonical inventory model? | **`inventory.Product`** (definition), **`inventory.Batch`** (receiving), **`inventory.Package`** (physical unit) |
| 3 | What is the canonical human task model? | **`tasks.Task`** — WorkerTask will merge into it in Phase 2 |
| 4 | What is the canonical package lifecycle? | **12-state machine** via `common.state_machine.transition_package()` — single entry point |
| 5 | What is the ownership of each event/audit model? | TaskActivity=human tasks, RotationEvent=package state, StockMovement=physical location, TaskEvent=legacy operational |
| 6 | What is the identity/traceability strategy? | Legacy ID fields added during migration; `migrated_from`, `migrated_at`, `migration_batch` audit fields |
| 7 | How will re-planning work? | Change OneToOne → ForeignKey in Phase 2; old plans marked CANCELLED/SUPERSEDED; new plan created |
| 8 | How will AUTO/CUSTOM configuration work? | FreezeProfile/ThawProfile for AUTO; per-plan overrides for CUSTOM; minimum duration constraints for SAFETY (future) |
| 9 | What is legacy and what is canonical? | Canonical: task_manager apps. Legacy: database_clmeat_main stock_meat. Reference: project_management_clmeat |
| 10 | What is the safe production migration strategy? | 6-step pipeline: extract → transform → staging → validate → human review → production |

### ⚠️ OPEN ITEMS (not blocking TASK 02)

| # | Question | Status | Resolution |
|---|----------|--------|-----------|
| 11 | Exact Production PostgreSQL version? | UNRESOLVED | Choose when deploying; not blocking data migration |
| 12 | StorageLocation seed data? | UNRESOLVED | Create during Phase 1 — need operator input on actual freezer/thaw/display locations |
| 13 | Product SKUs for legacy products? | UNRESOLVED | Generate during migration — use `meat_parts.prefix_barcode` or create new scheme |
| 14 | Batch number format? | UNRESOLVED | Propose: `B-{YYYYMMDD}-{sequence}` — operator to confirm |

---

## 12. Summary of Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Canonical application | `task_manager` | Already has all canonical models |
| Production database | PostgreSQL | Not SQLite; SQLite is dev-only |
| Product_info decomposition | Product (from meat_parts) + Batch (from lot_number+import_from) | Preserves product identity and batch traceability |
| Product_list → Package | Direct with field extraction | Lifecycle fields → RotationPlan; state mapping applied |
| Weight unit | Kilograms (kg) | Canonical uses kg; legacy uses grams — ÷1000 conversion |
| WorkerTask fate | Merge into tasks.Task (Phase 2) | Not now; after Phase 1 is stable |
| RotationPlan cardinality | OneToOne (now) → ForeignKey (Phase 2) | Keep simple for migration; upgrade later |
| Django version | Stay on 4.2.x LTS | Stability over novelty during consolidation |
| Legacy code fate | Keep, don't delete | Reference for migration; rewrite views in Phase 3 |
| Food safety claims | None — operational logic only | No formal validation evidence for formulas |
