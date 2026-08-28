# CL.MEAT Unified Architecture & Domain Ownership

> Generated: 2026-08-29 — TASK 01

---

## 1. Application Ownership Matrix

| Domain | Current Source | Canonical Owner | Legacy Source | Migration Required | Risk |
|--------|---------------|-----------------|---------------|-------------------|------|
| Human Task Management | `tasks.Task` | **`tasks.Task`** | — | No | ✅ SAFE |
| Task Assignment | `tasks.TaskAssignment` | **`tasks.TaskAssignment`** | — | No | ✅ SAFE |
| Task Activity/Audit | `tasks.TaskActivity` | **`tasks.TaskActivity`** | `operations.TaskEvent` | Yes (later) | ⚠️ WARNING |
| Package Lifecycle | `inventory.Package` | **`inventory.Package`** | `stock_meat.Product_list` | Yes (data) | ⚠️ WARNING |
| Package State Machine | `common.state_machine` | **`common.state_machine`** | `stock_meat.freeze_queue` (inline) | Yes (views) | ⚠️ WARNING |
| Product Definition | `inventory.Product` | **`inventory.Product`** | `stock_meat.Category` + `meat_parts` + `Product_info` | Yes (data) | ⚠️ WARNING |
| Batch / Receiving | `inventory.Batch` | **`inventory.Batch`** | `stock_meat.Product_info.lot_number` | Yes (data) | ⚠️ WARNING |
| Supplier | `inventory.Supplier` | **`inventory.Supplier`** | `stock_meat.Supply_meat` | Yes (data) | ✅ SAFE |
| Category | `inventory.Category` | **`inventory.Category`** | `stock_meat.Category` | Yes (data) | ✅ SAFE |
| Storage Location | `inventory.StorageLocation` | **`inventory.StorageLocation`** | — (implicit in status) | No | ✅ SAFE |
| Stock Movement | `inventory.StockMovement` | **`inventory.StockMovement`** | `stock_meat.FreezeRotation` | Yes (data) | ⚠️ WARNING |
| Temperature Log | `inventory.TemperatureLog` | **`inventory.TemperatureLog`** | — (not in legacy) | No | ✅ SAFE |
| Pricing (per kg) | `inventory.Product.selling_price_per_kg` | **`inventory.Product`** | `stock_meat.Product_info.selling_price_per_kg` | Yes (data) | ⚠️ WARNING |
| Package Pricing | `inventory.Package.selling_price` | **`inventory.Package`** | `stock_meat.Product_list.selling_price` | Yes (data) | ⚠️ WARNING |
| Price Audit | `inventory.PriceChangeHistory` | **`inventory.PriceChangeHistory`** | `stock_meat.PriceChangeHistory` | Yes (data) | ✅ SAFE |
| Barcode Generation | `inventory.services.generate_barcode` | **`inventory.services`** | `stock_meat.views.get_next_pack_number` | Yes (views) | ⚠️ WARNING |
| Label Printing | `inventory.label_service` | **`inventory.label_service`** | `stock_meat.niimbot` | Yes (views) | ✅ SAFE |
| Freeze Profile | `planning.FreezeProfile` | **`planning.FreezeProfile`** | — (hardcoded in `schedule.py`) | No | ✅ SAFE |
| Thaw Profile | `planning.ThawProfile` | **`planning.ThawProfile`** | — (hardcoded in `schedule.py`) | No | ✅ SAFE |
| Rotation Plan | `planning.RotationPlan` | **`planning.RotationPlan`** | `stock_meat.RotationSchedule` | Yes (data) | ⚠️ WARNING |
| Thaw Queue | `planning.ThawQueueEntry` | **`planning.ThawQueueEntry`** | `stock_meat.Product_list.thaw_queue_position` (field-based) | Yes (data) | ⚠️ WARNING |
| Worker Tasks (operational) | `operations.WorkerTask` | **→ `tasks.Task`** (future) | `stock_meat.WorkerTask` | Yes (structural) | 🔴 CRITICAL |
| Rotation Events (audit) | `operations.RotationEvent` | **`operations.RotationEvent`** | `stock_meat.FreezeRotation` | Yes (data) | ⚠️ WARNING |
| Sales / Loyverse Sync | `stock_meat.sold_items` | **`stock_meat`** (legacy) | — | Later (new app) | ⚠️ WARNING |
| Loyverse Integration | `stock_meat.loyverse` | **`stock_meat`** (legacy) | — | Later (new app) | ⚠️ WARNING |
| Finance / Transactions | `stock_meat.Transaction` | **`stock_meat`** (legacy) | — | Later (new app) | ✅ SAFE |
| Electricity | `stock_meat.ElectricityBill` | **`stock_meat`** (legacy) | — | Later (new app) | ✅ SAFE |
| Processing | `stock_meat.ProductProcessing` | **`stock_meat`** (legacy) | — | Later (new app) | ✅ SAFE |

---

## 2. Model Ownership Matrix

### 2A. Product Domain

| Model | Project | Purpose | Canonical | Replacement | Migration Risk |
|-------|---------|---------|-----------|-------------|----------------|
| `inventory.Category` | task_manager | Product category (PORK, CHICKEN) | **CANONICAL** | Replaces `stock_meat.Category` | LOW |
| `inventory.Supplier` | task_manager | Supplier source | **CANONICAL** | Replaces `stock_meat.Supply_meat` | LOW |
| `inventory.Product` | task_manager | Product definition | **CANONICAL** | Replaces `Category` + `meat_parts` + `Product_info` | MEDIUM |
| `inventory.Batch` | task_manager | Receiving batch | **CANONICAL** | Replaces `Product_info.lot_number` concept | LOW |
| `stock_meat.Category` | database_clmeat | Product type | LEGACY | → `inventory.Category` | LOW |
| `stock_meat.Supply_meat` | database_clmeat | Supplier | LEGACY | → `inventory.Supplier` | LOW |
| `stock_meat.meat_parts` | database_clmeat | Product part definition | LEGACY | → `inventory.Product` | MEDIUM |
| `stock_meat.Product_info` | database_clmeat | Product + pricing + lot | LEGACY | → `inventory.Product` + `Batch` | HIGH |

### 2B. Package Domain

| Model | Project | Purpose | Canonical | Replacement | Migration Risk |
|-------|---------|---------|-----------|-------------|----------------|
| `inventory.Package` | task_manager | Physical sellable unit | **CANONICAL** | Replaces `Product_list` | HIGH |
| `inventory.StockMovement` | task_manager | Movement trace | **CANONICAL** | Replaces `FreezeRotation` | MEDIUM |
| `inventory.TemperatureLog` | task_manager | Temperature history | **CANONICAL** | No legacy equivalent | NONE |
| `inventory.StorageLocation` | task_manager | Physical location | **CANONICAL** | No legacy equivalent | NONE |
| `stock_meat.Product_list` | database_clmeat | Package + lifecycle + Loyverse | LEGACY | → `inventory.Package` | HIGH |

### 2C. Planning Domain

| Model | Project | Purpose | Canonical | Replacement | Migration Risk |
|-------|---------|---------|-----------|-------------|----------------|
| `planning.FreezeProfile` | task_manager | Freeze configuration | **CANONICAL** | No legacy equivalent (hardcoded) | NONE |
| `planning.ThawProfile` | task_manager | Thaw configuration | **CANONICAL** | No legacy equivalent (hardcoded) | NONE |
| `planning.RotationPlan` | task_manager | Rotation scheduling | **CANONICAL** | Replaces `RotationSchedule` | MEDIUM |
| `planning.ThawQueueEntry` | task_manager | Thaw queue management | **CANONICAL** | Replaces `Product_list.thaw_queue_position` | MEDIUM |
| `stock_meat.RotationSchedule` | database_clmeat | Rotation planning | LEGACY | → `planning.RotationPlan` | MEDIUM |
| `stock_meat.FreezeRotation` | database_clmeat | Audit trail (actions) | LEGACY | → `operations.RotationEvent` | LOW |

### 2D. Task Domain

| Model | Project | Purpose | Canonical | Replacement | Migration Risk |
|-------|---------|---------|-----------|-------------|----------------|
| `tasks.Task` | task_manager | Human work assignment | **CANONICAL** | — | NONE |
| `tasks.TaskAssignment` | task_manager | Worker assignment | **CANONICAL** | — | NONE |
| `tasks.TaskActivity` | task_manager | Status change audit | **CANONICAL** | Replaces `TaskEvent` | LOW |
| `tasks.TaskTemplate` | task_manager | Recurring task template | **CANONICAL** | — | NONE |
| `operations.WorkerTask` | task_manager | Operational task (rotation) | **→ FUTURE: tasks.Task** | Merge into Task with category='ROTATION' | HIGH |
| `operations.TaskEvent` | task_manager | Task action log | **→ FUTURE: tasks.TaskActivity** | Merge into TaskActivity | LOW |
| `operations.RotationEvent` | task_manager | State transition audit | **CANONICAL** | Replaces `FreezeRotation` | LOW |
| `stock_meat.WorkerTask` | database_clmeat | Operational task | LEGACY | → `operations.WorkerTask` | LOW |

---

## 3. Business Logic Ownership Matrix

### 3A. Freeze/Thaw Calculation

| Function | Current Source | Recommended Owner | Action | Reason |
|----------|---------------|-------------------|--------|--------|
| `calculate_freeze_duration()` (Newton's law) | `stock_meat.schedule` | `planning.services` | **ADAPT** | Profile-based is primary; Newton available as reference |
| `calculate_thaw_duration()` (Newton's law) | `stock_meat.schedule` | `planning.services` | **ADAPT** | Profile-based with interpolation is primary |
| `calculate_freeze_duration()` (profile-based) | `planning.services` | `planning.services` | **KEEP** | Canonical implementation |
| `calculate_thaw_duration()` (profile-based) | `planning.services` | `planning.services` | **KEEP** | Canonical implementation with weight interpolation |
| `calculate_rotation_schedule()` | `stock_meat.schedule` | `planning.services.create_rotation_plan()` | **KEEP** | Unified implementation exists |

### 3B. Rotation & Queue Management

| Function | Current Source | Recommended Owner | Action | Reason |
|----------|---------------|-------------------|--------|--------|
| `generate_worker_tasks()` (4 tasks) | `stock_meat.schedule` | `planning.services.generate_worker_tasks()` | **KEEP** | Canonical generates 7 tasks |
| `generate_worker_tasks()` (7 tasks) | `planning.services` | `planning.services` | **KEEP** | More comprehensive |
| `add_to_thaw_queue()` | `planning.services` | `planning.services` | **KEEP** | Unified with state machine integration |
| `freeze_dashboard()` | `stock_meat.freeze_queue` | Later (dashboard app) | **REWRITE LATER** | Views must be migrated separately |
| `start_thaw()` | `stock_meat.freeze_queue` | `planning.services` + state machine | **MIGRATE** | Logic must go through state machine |
| `complete_thaw()` | `stock_meat.freeze_queue` | `planning.services` + state machine | **MIGRATE** | Logic must go through state machine |
| `pull_from_display()` | `stock_meat.freeze_queue` | State machine `REFREEZE_PENDING` | **MIGRATE** | Must go through state machine |
| `auto_rotation_check()` | `stock_meat.freeze_queue` | `planning.rotation.RotationEngine` | **MIGRATE** | Engine provides superior decision logic |

### 3C. Pricing

| Function | Current Source | Recommended Owner | Action | Reason |
|----------|---------------|-------------------|--------|--------|
| `calculate_package_price()` (price_per_kg) | `inventory.services` | `inventory.services` | **KEEP** | Canonical implementation |
| `calculate_package_price()` (cost_margin) | `inventory.services` | `inventory.services` | **KEEP** | Supports margin-based pricing |
| `calculate_package_price()` (discount) | `inventory.services` | `inventory.services` | **KEEP** | Supports discount mode |
| `calculate_package_price()` (views) | `stock_meat.views:445` | Legacy reference | **LEGACY ONLY** | Different interface, same concept |

### 3D. Barcode

| Function | Current Source | Recommended Owner | Action | Reason |
|----------|---------------|-------------------|--------|--------|
| `generate_barcode()` (atomic, race-safe) | `inventory.services` | `inventory.services` | **KEEP** | Superior implementation with select_for_update |
| `get_next_pack_number()` | `stock_meat.views:1299` | Legacy reference | **LEGACY ONLY** | Simpler, no concurrency protection |
| `get_source_number()` | `stock_meat.views:1383` | Legacy reference | **LEGACY ONLY** | Part of old barcode format |

### 3E. Sales & Loyverse

| Function | Current Source | Recommended Owner | Action | Reason |
|----------|---------------|-------------------|--------|--------|
| `sync_loyverse_receipts()` | `stock_meat.sold_items` | Later (new `sales` app) | **REWRITE LATER** | Complex integration, needs its own domain |
| `create_loyverse_product()` | `stock_meat.loyverse` | Later (new `loyverse` app) | **REWRITE LATER** | Needs adapter for new Package model |
| `create_loyverse_item()` | `stock_meat.loyverse` | Later (new `loyverse` app) | **REWRITE LATER** | API client needs separation |
| `generate_loyverse_csv()` | `stock_meat.loyverse_export` | Later | **REWRITE LATER** | Export functionality |

### 3F. State Machine

| Function | Current Source | Recommended Owner | Action | Reason |
|----------|---------------|-------------------|--------|--------|
| `transition_package()` | `common.state_machine` | `common.state_machine` | **KEEP** | Single entry point, validated transitions |
| `can_transition()` | `common.state_machine` | `common.state_machine` | **KEEP** | Public API for checks |
| `_validate_transition_requirements()` | `common.state_machine` | `common.state_machine` | **KEEP** | Prerequisite validation |
| `_auto_complete_worker_tasks()` | `common.state_machine` | `common.state_machine` | **ADAPT** | Must be adapted when WorkerTask → Task migration happens |
| `get_next_action()` | `common.worker_actions` | `common.worker_actions` | **KEEP** | Powers barcode-first UI |
| Inline status changes in `freeze_queue.py` | `stock_meat.freeze_queue` | `common.state_machine` | **MIGRATE** | Must go through state machine |

---

## 4. Dependency Graph

```
                    ┌─────────────┐
                    │   accounts  │
                    │   (User,    │
                    │    Team)    │
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
              ▼            ▼            ▼
        ┌──────────┐ ┌──────────┐ ┌───────────┐
        │  tasks   │ │scheduling│ │   common  │
        │  (Task,  │ │          │ │(state_    │
        │Assignment│ │          │ │ machine)  │
        │Activity) │ │          │ │           │
        └────┬─────┘ └──────────┘ └─────┬─────┘
             │                           │
             │     ┌─────────────┐       │
             │     │ notifications│       │
             │     └─────────────┘       │
             │                           │
             │    ┌──────────────────────┤
             │    │                      │
             ▼    ▼                      ▼
        ┌──────────────┐          ┌────────────┐
        │  inventory   │◄────────►│  planning   │
        │ (Product,    │          │ (Freeze,    │
        │  Package,    │          │  Thaw,      │
        │  Batch,      │          │  Rotation,  │
        │  StockMove,  │          │  ThawQueue) │
        │  TempLog)    │          │             │
        └──────┬───────┘          └─────────────┘
               │                        │
               │    ┌───────────────────┘
               │    │
               ▼    ▼
          ┌──────────────┐
          │  operations   │
          │ (WorkerTask,  │
          │  TaskEvent,   │
          │  RotationEvent│
          └──────────────┘
               │
               ▼
     ┌──────────────────┐
     │ database_clmeat  │ (LEGACY)
     │ (stock_meat)     │
     │ - views          │
     │ - freeze_queue   │
     │ - sold_items     │
     │ - loyverse       │
     │ - finance        │
     │ - electricity    │
     └──────────────────┘
```

### Circular Dependencies Identified

| Dependency | Direction | Issue | Resolution |
|------------|-----------|-------|------------|
| `inventory` ↔ `common.state_machine` | Bidirectional | State machine imports `PackageState` from inventory; inventory Package calls `state_machine.can_transition()` | ACCEPTABLE: Circular import avoided via lazy imports inside methods |
| `common.state_machine` → `operations.WorkerTask` | One-way | State machine auto-completes WorkerTasks | MUST BE BROKEN when WorkerTask → Task migration happens |
| `operations.WorkerTask` → `planning.RotationPlan` | One-way | WorkerTask FK to RotationPlan | Acceptable for now; will be removed during Task migration |
| `inventory.Package` → `operations.WorkerTask` | One-way | Package.current_task property | ACCEPTABLE: Read-only property with lazy import |

**Critical Coupling Point:**
```
state_machine._auto_complete_worker_tasks()
    → operations.WorkerTask (auto-complete on transition)
    → operations.TaskEvent (create audit event)
```

This creates a hard dependency between the state machine and the operations app. When WorkerTask is migrated to tasks.Task, this coupling must be refactored.

---

## 5. Target Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     CL.MEAT UNIFIED SYSTEM                  │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │                    HUMAN LAYER                       │   │
│  │                                                     │   │
│  │  User → Task → TaskAssignment → Team               │   │
│  │       → TaskActivity (audit)                        │   │
│  │       → TaskReport (problems)                       │   │
│  │       → TaskTemplate (recurring)                    │   │
│  │       → TaskDependency (blockers)                   │   │
│  │                                                     │   │
│  │  Source of truth for ALL human work                 │   │
│  └─────────────────────────────────────────────────────┘   │
│                          │                                  │
│  ┌───────────────────────┼──────────────────────────────┐   │
│  │                ORCHESTRATION LAYER                    │   │
│  │                                                      │   │
│  │  common.state_machine  ← Single entry for lifecycle  │   │
│  │  common.worker_actions ← Barcode-first UI            │   │
│  │  common.time_service   ← Bangkok timezone            │   │
│  │                                                      │   │
│  │  planning.rotation.RotationEngine ← Decision engine  │   │
│  │  planning.audit.Audit             ← Audit logging    │   │
│  │  planning.services                ← Plan lifecycle   │   │
│  └──────────────────────────────────────────────────────┘   │
│                          │                                  │
│  ┌───────────────────────┼──────────────────────────────┐   │
│  │                DOMAIN LAYER                           │   │
│  │                                                      │   │
│  │  inventory/          │  planning/                     │   │
│  │  ├─ Product          │  ├─ FreezeProfile              │   │
│  │  ├─ Category         │  ├─ ThawProfile                │   │
│  │  ├─ Supplier         │  ├─ RotationPlan               │   │
│  │  ├─ Batch            │  └─ ThawQueueEntry             │   │
│  │  ├─ Package          │                                │   │
│  │  ├─ StorageLocation  │  operations/                   │   │
│  │  ├─ StockMovement    │  ├─ RotationEvent (audit)      │   │
│  │  ├─ TemperatureLog   │  └─ WorkerTask → (merge to Task)│
│  │  ├─ PriceChangeHistory│                               │   │
│  │  └─ BarcodeSequence  │                                │   │
│  └──────────────────────────────────────────────────────┘   │
│                          │                                  │
│  ┌───────────────────────┼──────────────────────────────┐   │
│  │              INTEGRATION LAYER                        │   │
│  │                                                      │   │
│  │  database_clmeat_main/stock_meat (LEGACY)             │   │
│  │  ├─ Loyverse API client  → Future: new loyverse app  │   │
│  │  ├─ SoldItem sync        → Future: new sales app     │   │
│  │  ├─ Transaction/Finance  → Future: new finance app   │   │
│  │  ├─ ElectricityBill      → Future: new utility app   │   │
│  │  ├─ ProductProcessing    → Future: new processing app│   │
│  │  └─ Dashboard views      → Future: dashboard app     │   │
│  └──────────────────────────────────────────────────────┘   │
│                          │                                  │
│  ┌───────────────────────┼──────────────────────────────┐   │
│  │                  DATA LAYER                           │   │
│  │                                                      │   │
│  │  SQLite (dev) → PostgreSQL (production)              │   │
│  │  task_manager = canonical database                    │   │
│  │  database_clmeat_main = legacy database (read-only)  │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

---

## 6. Database Ownership

### Current State

| Database | Project | Type | Contents | Risk |
|----------|---------|------|----------|------|
| `task_manager/db.sqlite3` | task_manager | Dev | 315 tests pass, canonical models | ✅ SAFE |
| `database_clmeat_main/db.sqlite3` | database_clmeat | **PRODUCTION** | Real operational data, sales, Loyverse sync | 🔴 CRITICAL |
| `project_management_clmeat/db.sqlite3` | project_mgmt | Dev | Reference architecture test data | ✅ SAFE |

### Target Strategy

```
CANONICAL DATABASE: task_manager/db.sqlite3
├── inventory.Product, Batch, Package, StockMovement, ...
├── planning.FreezeProfile, ThawProfile, RotationPlan, ...
├── operations.WorkerTask (temporary), RotationEvent
├── tasks.Task, TaskAssignment, TaskActivity, ...
└── accounts.User, Team, Role

LEGACY DATABASE (READ-ONLY): database_clmeat_main/db.sqlite3
├── stock_meat.Product_list → MIGRATE to inventory.Package
├── stock_meat.Product_info → MIGRATE to inventory.Product + Batch
├── stock_meat.RotationSchedule → MIGRATE to planning.RotationPlan
├── stock_meat.FreezeRotation → MIGRATE to operations.RotationEvent
├── stock_meat.SoldItem → FUTURE: new sales app
├── stock_meat.Transaction → FUTURE: new finance app
└── stock_meat.ElectricityBill → FUTURE: new utility app
```

### Migration Strategy (DO NOT EXECUTE YET)

1. **Phase 1 — Reference Data:** Categories, Suppliers, Products, Locations
2. **Phase 2 — Active Inventory:** Product_list → Package (weight conversion: g → kg)
3. **Phase 3 — Rotation History:** RotationSchedule → RotationPlan, FreezeRotation → RotationEvent
4. **Phase 4 — Business Data:** SoldItem, Transaction, Electricity (new apps)
5. **NEVER AUTOMATE:** Production data migration requires manual verification at each step

---

## 7. Package Lifecycle Architecture

### State Machine (12 States)

```
                    ┌─────────────┐
                    │   PACKED    │ ← Initial state after packaging
                    └──────┬──────┘
                           │ START_FREEZE
                           ▼
                    ┌─────────────┐
                    │  FREEZING   │ ← In freezer, not yet frozen
                    └──────┬──────┘
                           │ FREEZE_CHECK (temp verified)
                           ▼
                    ┌─────────────┐
                    │   FROZEN    │ ← Solidly frozen, in storage
                    └──────┬──────┘
                           │ MOVE_TO_THAW_QUEUE (requires RotationPlan)
                           ▼
                    ┌─────────────┐
                    │READY_FOR_   │ ← Queued, waiting for thaw start
                    │   THAW      │
                    └──────┬──────┘
                           │ THAW_START (requires ThawQueueEntry)
                           ▼
                    ┌─────────────┐
                    │THAW_QUEUED  │ ← In thaw area, thawing
                    └──────┬──────┘
                           │ THAW_COMPLETE (temp verified)
                           ▼
                    ┌─────────────┐
                    │  THAWING    │ ← Thaw complete, ready to sell
                    └──────┬──────┘
                           │ MOVE_TO_DISPLAY
                           ▼
                    ┌─────────────┐
                    │READY_FOR_   │ ← On display, selling
                    │   SALE      │
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
              ▼            ▼            ▼
     ┌────────────┐ ┌────────────┐ ┌────────────┐
     │  ON_DISPLAY│ │ PROCESSING │ │ DISCARDED  │
     │  (selling) │ │ (being     │ │ (waste)    │
     └──────┬─────┘ │  processed)│ └─────┬──────┘
            │        └─────┬──────┘       │
            │              │              │
            ▼              ▼              ▼
     ┌────────────┐ ┌────────────┐ ┌────────────┐
     │ REFREEZE_  │ │ COMPLETED  │ │ COMPLETED  │
     │ PENDING    │ │ (terminal) │ │ (terminal) │
     └──────┬─────┘ └────────────┘ └────────────┘
            │
            ▼
     ┌────────────┐
     │  FREEZING  │ ← Back to freeze cycle
     └────────────┘
```

### Transition Rules (Enforced by State Machine)

| From | To | Prerequisites | Task Type Auto-Complete |
|------|----|--------------|------------------------|
| PACKED | FREEZING | None | — |
| FREEZING | FROZEN | None | FREEZE_START |
| FROZEN | READY_FOR_THAW | None | — |
| READY_FOR_THAW | THAW_QUEUED | RotationPlan exists | — |
| THAW_QUEUED | THAWING | RotationPlan + ThawQueueEntry | THAW_START |
| THAWING | READY_FOR_SALE | ThawQueueEntry.status=COMPLETED | THAW_COMPLETE |
| READY_FOR_SALE | ON_DISPLAY | None | MOVE_TO_DISPLAY |
| ON_DISPLAY | REFREEZE_PENDING | None | — |
| ON_DISPLAY | PROCESSING | None | — |
| ON_DISPLAY | DISCARDED | None | — |
| REFREEZE_PENDING | FREEZING | None | REFREEZE |
| PROCESSING | COMPLETED | None | — |
| DISCARDED | COMPLETED | None | — |
| THAW_QUEUED | PACKED | None (cancel) | — |

---

## 8. Rotation Architecture

### Decision Engine (RotationEngine)

```
RotationEngine.get_decisions()
    │
    ├── _packed_need_freeze()
    │   → Packed packages not in freezer → HIGH priority
    │
    ├── _frozen_need_thaw_queue()
    │   → Frozen packages with plan due within 2h → CRITICAL/HIGH
    │
    ├── _thawing_approaching_done()
    │   → Thawing packages due within 1h → HIGH priority
    │
    └── _overdue_plans()
        → Plans past target_ready_at → CRITICAL priority

Sorted by: priority (CRITICAL=1, HIGH=2, MEDIUM=3, LOW=4)
```

### Plan Lifecycle

```
create_rotation_plan()
    ├── Validate: package must be PACKED or FROZEN
    ├── Validate: no existing plan (OneToOne)
    ├── Calculate freeze duration (AUTO or CUSTOM)
    ├── Calculate thaw duration (AUTO or CUSTOM)
    ├── Calculate timeline (backward from target_ready_at)
    ├── Create RotationPlan
    └── Generate 7 WorkerTasks
        ├── FREEZE_START      @ freeze_start_at
        ├── FREEZE_CHECK      @ freeze_start_at + 2h
        ├── MOVE_TO_THAW_QUEUE @ thaw_queue_at
        ├── THAW_START         @ thaw_start_at
        ├── THAW_CHECK         @ thaw_start_at + (thaw_dur / 2)
        ├── THAW_COMPLETE      @ target_ready_at
        └── MOVE_TO_DISPLAY    @ target_ready_at + 15min

cancel_rotation_plan()
    ├── Set status = CANCELLED
    ├── Cancel all pending/in-progress WorkerTasks
    └── Log audit event

⚠️ LIMITATION: OneToOne means package cannot have new plan after cancel
   → Future: change to ForeignKey + is_active flag
```

---

## 9. AUTO / CUSTOM Configuration Architecture

### Where Configuration Lives

```
┌─────────────────────────────────────────────┐
│           CONFIGURATION MODELS               │
│                                             │
│  FreezeProfile:                             │
│  ├─ target_temperature (°C)                 │
│  ├─ minimum_duration (Duration)             │
│  ├─ default_duration (Duration)             │
│  ├─ buffer_duration (Duration)              │
│  └─ active (bool)                           │
│                                             │
│  ThawProfile:                               │
│  ├─ default_duration (Duration)             │
│  ├─ minimum_duration (Duration)             │
│  ├─ buffer_duration (Duration)              │
│  ├─ weight_threshold_kg (Decimal)           │
│  ├─ weight_scale_factor (Decimal)           │
│  ├─ target_temperature (°C)                 │
│  ├─ min_temperature / max_temperature       │
│  ├─ thaw_capacity (int)                     │
│  └─ category (str, blank=all)               │
│                                             │
│  ProductPlanningProfile:                    │
│  ├─ avg_daily_usage_kg (Decimal)            │
│  ├─ safety_stock_days (Decimal)             │
│  ├─ target_coverage_days (Decimal)          │
│  └─ min_order_qty_kg (Decimal)              │
└─────────────────────────────────────────────┘

┌─────────────────────────────────────────────┐
│           ROTATION PLAN (per package)        │
│                                             │
│  AUTO mode:                                 │
│  ├─ freeze_profile → calculate_freeze_dur() │
│  ├─ thaw_profile → calculate_thaw_dur()     │
│  └─ freeze_duration / thaw_duration stored  │
│                                             │
│  CUSTOM mode:                               │
│  ├─ freeze_override (Duration, nullable)    │
│  ├─ thaw_override (Duration, nullable)      │
│  ├─ override_reason (text)                  │
│  ├─ overridden_by (string)                  │
│  └─ overridden_at (datetime)                │
└─────────────────────────────────────────────┘
```

### Duration Calculation Flow

```
FREEZE (AUTO):
  weight ≤ 0.5kg → minimum_duration + buffer
  0.5 < weight ≤ 1.0kg → default_duration + buffer
  weight > 1.0kg → default_duration × 1.2 + buffer

THAW (AUTO):
  weight ≤ threshold → minimum_duration + buffer
  threshold < weight ≤ 2×threshold → interpolated + buffer
  weight > 2×threshold → default_duration × scale_factor + buffer

BACKWARD SCHEDULING:
  thaw_start = target_ready_at - thaw_duration - buffer
  freeze_end = thaw_start - 15min (safety gap)
  freeze_start = freeze_end - freeze_duration
```

---

## 10. Migration Boundaries

### What Moves FIRST (Phase 1 — Data Migration)

| Source | Target | Method | Risk |
|--------|--------|--------|------|
| `Product_info` → `Product` + `Batch` | Data migration script | HIGH |
| `Category` → `Category` | Direct copy | LOW |
| `Supply_meat` → `Supplier` | Direct copy | LOW |
| `Product_list` → `Package` | Weight conversion (g→kg), state mapping | HIGH |
| `RotationSchedule` → `RotationPlan` | Field mapping + duration conversion | MEDIUM |
| `FreezeRotation` → `RotationEvent` | Action type mapping | LOW |

### What Moves LATER (Phase 2 — New Apps)

| Source | Target | Method | Risk |
|--------|--------|--------|------|
| `SoldItem` | New `sales` app | New model + data migration | HIGH |
| `Transaction` | New `finance` app | New model + data migration | MEDIUM |
| `ElectricityBill` | New `utility` app | New model + data migration | LOW |
| `ProductProcessing` | New `processing` app | New model + data migration | LOW |
| `Loyverse API client` | New `integrations` app | Rewrite as service | MEDIUM |

### What STAYS Legacy (Phase 3 — Never Auto-Migrate)

| Item | Reason |
|------|--------|
| `stock_meat.views` (all view functions) | Must be rewritten as Django views/templates in task_manager |
| `stock_meat.dashboard` | Must be replaced by unified dashboard |
| `stock_meat.forms` | Must be rewritten for new models |
| `stock_meat.management.commands` | Must be adapted or rewritten |
| `stock_meat.tests` | Must be rewritten for new models |
| `stock_meat.templates` | Must be replaced by task_manager templates |
| `stock_meat.static` | Must be migrated to task_manager static |

### What Requires ADAPTER

| Item | Adapter Needed |
|------|---------------|
| `WorkerTask` → `tasks.Task` | Task.category='ROTATION', Task.extra_data JSON field |
| `FreezeRotation` → `RotationEvent` | Action type mapping (thaw_start → STATE_TRANSITION) |
| `Product_list` weight (grams) → `Package` weight (kg) | Division by 1000 in migration |
| `Product_list.storage_status` → `Package.current_state` | State mapping table |
| `RotationSchedule` durations (minutes) → `RotationPlan` durations (Duration) | timedelta conversion |

### What Must NEVER Be Auto-Migrated

| Item | Reason |
|------|--------|
| `Product_list.loyverse_*` fields | Loyverse IDs must be manually verified |
| `SoldItem` data | Sales data requires manual audit before migration |
| `Transaction` data | Financial data requires manual verification |
| `stock_meat.db.sqlite3` production data | Must be backed up and migrated with manual oversight |

---

## 11. Legacy Systems Summary

### database_clmeat_main — What Remains

| Component | Status | Action |
|-----------|--------|--------|
| `stock_meat.models` | Legacy reference | Keep for data migration scripts |
| `stock_meat.views` | Active production views | REWRITE as task_manager views |
| `stock_meat.freeze_queue` | Active freeze/thaw UI | REWRITE using state machine |
| `stock_meat.sold_items` | Active Loyverse sync | REWRITE as new `sales` app |
| `stock_meat.loyverse` | Active API client | REWRITE as new `integrations` app |
| `stock_meat.loyverse_export` | CSV export | REWRITE |
| `stock_meat.finance` | Active finance tracking | REWRITE as new `finance` app |
| `stock_meat.electricity` | Active utility tracking | REWRITE as new `utility` app |
| `stock_meat.processing` | Active processing records | REWRITE as new `processing` app |
| `stock_meat.dashboard` | Active dashboard | REWRITE using task_manager dashboard |
| `stock_meat.niimbot` | Label printing | MIGRATE to `inventory.label_service` |
| `stock_meat.schedule` | Calculation functions | MIGRATE to `planning.services` |
| `stock_meat.tests` | 56 tests (some failing) | REWRITE for new models |

### project_management_clmeat — What Remains

| Component | Status | Action |
|-----------|--------|--------|
| `inventory.models` | Reference architecture | Already merged into `task_manager.inventory` |
| `planning.models` | Reference architecture | Already merged into `task_manager.planning` |
| `common.state_machine` | Reference architecture | Already merged into `task_manager.common` |
| All other files | Reference only | Keep for comparison during migration |

---

## 12. Risks

### 🔴 CRITICAL

1. **WorkerTask dual-source-of-truth:** `operations.WorkerTask` and `tasks.Task` serve overlapping purposes. Until merged, every task-related feature must check both systems.

2. **Production database:** `database_clmeat_main/db.sqlite3` contains real operational data. Any migration must be non-destructive with verified backups.

3. **OneToOne RotationPlan:** Cannot re-plan a package after plan cancellation without deleting the record.

### ⚠️ WARNING

4. **State machine ↔ operations coupling:** `_auto_complete_worker_tasks()` in state_machine creates hard dependency on operations.WorkerTask. This must be decoupled during Task migration.

5. **Legacy view functions:** All `stock_meat/views.py` functions directly manipulate `Product_list.storage_status` bypassing any state machine. These must be rewritten.

6. **Django version conflict:** `project_management_clmeat` requires Django 5.x but unified system uses 4.2.30. Ported code must be compatible with 4.2.

7. **pytz removal complete:** All pytz references replaced with zoneinfo. Verify no new imports introduce pytz dependency.

8. **Missing admin.py:** `inventory`, `planning`, `operations` apps have no admin.py — cannot manage via Django admin.

---

## 13. Tests

```
Test Suite Results (TASK 01):
─────────────────────────────
Ran 315 tests in 99.813s — OK

Breakdown:
  tasks:        80 tests ✅
  inventory:    73 tests ✅
  planning:     26 tests (rotation_tests) ✅
  accounts:     ~50 tests ✅
  scheduling:   ~30 tests ✅
  notifications: ~20 tests ✅
  reports:      ~20 tests ✅
  other:        ~16 tests ✅

Django check: System check identified no issues (0 silenced)
```
