# CL.MEAT Migration Boundaries

> Quick reference — TASK 01

---

## Phase 1: Model Consolidation (NEXT)

```
SOURCE                          → TARGET                        ACTION
─────────────────────────────────────────────────────────────────────────
stock_meat.Category             → inventory.Category             COPY DATA
stock_meat.Supply_meat          → inventory.Supplier             COPY DATA
stock_meat.meat_parts           → inventory.Product (partial)    MERGE
stock_meat.Product_info         → inventory.Product + Batch      SPLIT + CONVERT
stock_meat.Product_list         → inventory.Package              CONVERT (g→kg)
stock_meat.FreezeRotation       → operations.RotationEvent       MAP ACTIONS
stock_meat.RotationSchedule     → planning.RotationPlan          MAP FIELDS
stock_meat.Product_list fields  → planning.ThawQueueEntry        EXTRACT
stock_meat.PriceChangeHistory   → inventory.PriceChangeHistory   COPY DATA
```

## Phase 2: WorkerTask → Task Migration

```
CURRENT                         → TARGET                        ACTION
─────────────────────────────────────────────────────────────────────────
operations.WorkerTask           → tasks.Task                    MERGE
  (package FK)                    (add package FK to Task)
  (rotation_plan FK)              (add rotation_plan FK or JSON)
  (task_type)                     (Task.category='ROTATION')
  (scheduled_at)                  (Task.start_at)
  (status)                        (Task.status mapped)
  (completed_by)                  (TaskAssignment)

operations.TaskEvent            → tasks.TaskActivity            MERGE
operations.RotationEvent        → (KEEP — different purpose)    KEEP
```

## Phase 3: Business Apps

```
SOURCE                          → TARGET                        ACTION
─────────────────────────────────────────────────────────────────────────
stock_meat.sold_items           → sales app (NEW)               REWRITE
stock_meat.loyverse             → integrations app (NEW)        REWRITE
stock_meat.Transaction          → finance app (NEW)             REWRITE
stock_meat.ElectricityBill      → utility app (NEW)             REWRITE
stock_meat.ProductProcessing    → processing app (NEW)          REWRITE
stock_meat.views                → task_manager views            REWRITE
stock_meat.freeze_queue         → planning views + state machine REWRITE
stock_meat.dashboard            → dashboard app                  REWRITE
```

## What Never Moves

```
ITEM                            REASON
─────────────────────────────────────────────────────────────────────────
stock_meat.db.sqlite3           Production data — manual migration only
stock_meat.tests                Must be rewritten for new models
stock_meat.templates            Must be replaced by task_manager templates
stock_meat.static               Must be migrated to task_manager static
stock_meat.management/commands  Must be adapted or rewritten
```

## State Mapping (Product_list → Package)

```
Product_list.storage_status  →  Package.current_state
──────────────────────────────────────────────────────
'frozen'                     →  'FROZEN' (if thaw_queue_position=0)
                               'THAW_QUEUED' (if thaw_queue_position>0)
'thawing'                    →  'THAWING'
'display'                    →  'ON_DISPLAY'
'depleted'                   →  'COMPLETED'
(default/new)                →  'PACKED'
```

## Weight Conversion

```
Product_list.weight (grams)  →  Package.weight (kg)
──────────────────────────────────────────────────────
weight_grams / 1000.0        =  weight_kg
Example: 270g → 0.270kg
```
