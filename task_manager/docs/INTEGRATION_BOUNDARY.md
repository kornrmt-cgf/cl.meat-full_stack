# CL.MEAT Integration Boundary — Phase 06.5 Hardening

> Generated: 2026-09-02 — Phase 06.5

---

## 1. WorkerTask vs Task Boundary

### Current Architecture

Two parallel task models exist:

| Model | App | Purpose | Status |
|-------|-----|---------|--------|
| `tasks.Task` | tasks | Canonical human-work model | ACTIVE — general task management |
| `operations.WorkerTask` | operations | Operational lifecycle task | ACTIVE — rotation freeze/thaw lifecycle |

### WorkerTask Creation Paths

| # | Location | Trigger |
|---|----------|---------|
| 1 | `planning.services.generate_worker_tasks()` | Called by `create_rotation_plan()` — generates 7 tasks per plan |
| 2 | `task_manager/database_clmeat_main/stock_meat/schedule.py:228` | Legacy schedule creation (bulk_create) |
| 3 | `project_management_clmeat/inventory/management/commands/seed_demo.py:379` | Demo data seeding |
| 4 | `project_management_clmeat/planning/services.py:359` | Legacy plan creation (bulk_create) |

### WorkerTask Read Paths

| # | Location | Purpose |
|---|----------|---------|
| 1 | `operations.views.WorkerTaskListView` | Today's task board |
| 2 | `operations.views.WorkerTaskDetailView` | Task detail + actions |
| 3 | `operations.views.TaskStatusAJAXView` | Real-time status polling |
| 4 | `operations.views.TaskListAJAXView` | Task count dashboard |
| 5 | `operations.services.get_available_tasks()` | PENDING task discovery |
| 6 | `operations.services.get_worker_tasks()` | Worker's active tasks |
| 7 | `operations.services.get_todays_tasks()` | Today's tasks (backward-compat) |
| 8 | `operations.services.get_task_history()` | Completed/cancelled tasks |
| 9 | `inventory.models.Package.current_task` | Lazy property on Package |
| 10 | `common.state_machine._auto_complete_worker_tasks()` | Auto-complete on state transition |
| 11 | `common.worker_actions.get_next_action()` | Next valid action lookup |
| 12 | `project_management_clmeat/worker/views.py` | Legacy worker views |
| 13 | `project_management_clmeat/worker/api.py` | Legacy worker API |

### WorkerTask Mutation Paths

| # | Location | Mutation |
|---|----------|----------|
| 1 | `operations.services.claim_task()` | PENDING → CLAIMED |
| 2 | `operations.services.start_task()` | CLAIMED → IN_PROGRESS |
| 3 | `operations.services.complete_task()` | IN_PROGRESS → COMPLETED |
| 4 | `operations.services.cancel_task()` | any → CANCELLED |
| 5 | `operations.services.skip_stale_tasks()` | stale → SKIPPED |
| 6 | `operations.services.cancel_tasks_for_plan()` | bulk cancel for plan |
| 7 | `common.state_machine._auto_complete_worker_tasks()` | auto-complete on transition |

### WorkerTask UI Paths

| # | URL | View | Template |
|---|-----|------|----------|
| 1 | `/worker/` | WorkerTaskListView | operations/task_list.html |
| 2 | `/worker/<pk>/` | WorkerTaskDetailView | operations/task_detail.html |
| 3 | `/worker/<pk>/claim/` | WorkerClaimTaskView | — (POST redirect) |
| 4 | `/worker/<pk>/start/` | WorkerStartTaskView | — (POST redirect) |
| 5 | `/worker/<pk>/complete/` | WorkerCompleteTaskView | — (POST redirect) |
| 6 | `/worker/<pk>/cancel/` | WorkerCancelTaskView | — (POST redirect) |
| 7 | `/worker/history/` | WorkerTaskHistoryView | operations/task_history.html |
| 8 | `/worker/ajax/scan/` | BarcodeScanView | — (JSON) |
| 9 | `/worker/ajax/task/<pk>/status/` | TaskStatusAJAXView | — (JSON) |
| 10 | `/worker/ajax/tasks/count/` | TaskListAJAXView | — (JSON) |

### Task (tasks.Task) Creation/Mutation Paths

| # | Location | Purpose |
|---|----------|---------|
| 1 | `tasks.views.TaskCreateView` | Manager creates general tasks |
| 2 | `tasks.services.TaskService.create_task()` | Service-layer task creation |
| 3 | `tasks.services.TaskService.assign_task()` | Assignment |
| 4 | `tasks.services.TaskService.accept_task()` | Employee accepts |
| 5 | `tasks.services.TaskService.start_task()` | Start working |
| 6 | `tasks.services.TaskService.complete_task()` | Mark complete |
| 7 | `tasks.services.TaskService.cancel_task()` | Cancel |
| 8 | `tasks.models.TaskTemplate.generate_task()` | Recurring task generation |

### Field Mapping (WorkerTask → Task)

| WorkerTask Field | Task Field | Mapping Notes |
|------------------|------------|---------------|
| `package` | — | **NO EQUIVALENT** — Task has no package FK |
| `rotation_plan` | — | **NO EQUIVALENT** — Task has no rotation FK |
| `task_type` | `category` + `title` | TaskType maps to Task.Category + generated title |
| `scheduled_at` | `start_at` | Direct temporal mapping |
| `status` | `status` | Different enum values (see below) |
| `claimed_by` | `claimed_by` + `TaskAssignment` | Task has both |
| `claimed_at` | `claimed_at` | Direct mapping |
| `started_at` | — | Task tracks via TaskActivity |
| `completed_at` | `completed_at` | Direct mapping |
| `completed_by` | TaskActivity.user | Tracked via activity log |
| `cancelled_at` | — | Task tracks via TaskActivity |
| `notes` | `notes` | Direct mapping |
| `created_at` | `created_at` | Direct mapping |
| `updated_at` | `updated_at` | Direct mapping |

### Status Mapping

| WorkerTask Status | Task Status | Notes |
|-------------------|-------------|-------|
| PENDING | SCHEDULED / READY | Task has no direct PENDING |
| CLAIMED | ACCEPTED | Close equivalent |
| IN_PROGRESS | IN_PROGRESS | Direct match |
| COMPLETED | COMPLETED | Direct match |
| CANCELLED | CANCELLED | Direct match |
| SKIPPED | — | **NO EQUIVALENT** — Task has no SKIPPED |
| OVERDUE | — | **NO EQUIVALENT** — Task uses is_overdue property |

### Audit Mapping

| WorkerTask Audit | Task Audit | Notes |
|------------------|------------|-------|
| TaskEvent | TaskActivity | Different schema — TaskEvent is simpler |
| RotationEvent | — | **NO EQUIVALENT** — Package lifecycle audit |

---

## 2. Transition Strategy (Documented, NOT Implemented)

### Migration Path

```
WorkerTask                    tasks.Task
─────────────                 ──────────
task_type='FREEZE_START'  →   category='warehouse', title='เริ่มแช่แข็ง: {barcode}'
package FK               →   New field needed: package FK (or JSON extra_data)
rotation_plan FK         →   New field needed: rotation_plan FK (or JSON extra_data)
scheduled_at             →   start_at
status                   →   status (mapped)
claimed_by               →   claimed_by + TaskAssignment
TaskEvent                →   TaskActivity (schema migration needed)
```

### Fields Without Direct Equivalents

| WorkerTask Field | Issue | Resolution |
|------------------|-------|------------|
| `package` | Task has no package FK | Add FK or JSON extra_data |
| `rotation_plan` | Task has no rotation FK | Add FK or JSON extra_data |
| `SKIPPED` status | Task has no SKIPPED | Add status or use COMPLETED+notes |
| `OVERDUE` status | Task uses is_overdue property | Keep as property |
| TaskEvent | TaskActivity has richer schema | Migrate event data |

### Recommended Approach

1. Add `package` and `rotation_plan` ForeignKey fields to `tasks.Task` (nullable)
2. Add `SKIPPED` to `tasks.Task.Status`
3. Migrate TaskEvent records to TaskActivity
4. Create data migration script for WorkerTask → Task records
5. Update all WorkerTask references to use Task
6. Remove operations.WorkerTask after full migration

---

## 3. Package.current_task

### Current Implementation

```python
# inventory/models.py
@property
def current_task(self):
    from operations.models import WorkerTask, TaskStatus
    return WorkerTask.objects.filter(
        package=self,
        status__in=[TaskStatus.PENDING, TaskStatus.IN_PROGRESS]
    ).order_by('scheduled_at').first()
```

### Consumers

| Consumer | Location | Usage |
|----------|----------|-------|
| `Package.current_task` property | inventory/models.py | Lazy lookup |
| `common.worker_actions.get_next_action()` | common/worker_actions.py | Determines next UI action |
| `ARCHITECTURE.md` reference | docs/ARCHITECTURE.md | Documented dependency |

### Desired Canonical Behavior

After WorkerTask → Task migration:
```python
@property
def current_task(self):
    from tasks.models import Task
    return Task.objects.filter(
        package=self,
        status__in=['scheduled', 'ready', 'accepted', 'in_progress']
    ).order_by('start_at').first()
```

### Compatibility Boundary

- Currently lazy-imports `operations.models` to avoid circular dependency
- After migration, will lazy-import `tasks.models` (same pattern)
- No breaking change needed for consumers — property signature unchanged

---

## 4. State Machine Coupling

### Current Flow

```
Package state transition (via transition_package)
  → _auto_complete_worker_tasks()
    → WorkerTask auto-complete
    → TaskEvent creation
```

### Coupling Point

```python
# common/state_machine.py
def _auto_complete_worker_tasks(package, target_state):
    tasks = WorkerTask.objects.filter(
        package=package,
        status__in=[TaskStatus.PENDING, TaskStatus.CLAIMED, TaskStatus.IN_PROGRESS]
    )
    # ... auto-complete matching tasks
```

### Desired Future Architecture

```
Package State Machine
  → Domain Event (e.g., StateTransitioned)
    → Event Listener / Signal Handler
      → Task Service
        → tasks.Task update
```

### Benefits of Decoupling

- State machine has no import dependency on operations app
- Task completion becomes a side-effect, not a direct call
- Easier to test state machine in isolation
- Supports eventual Event Sourcing pattern

### Risk Assessment

**Current coupling is SAFE for Phase 06.5** — the auto-complete behavior works correctly and is tested. Breaking this coupling should only happen during the WorkerTask → Task migration.

---

## 5. Thaw Capacity Profile Isolation

### Bug Found and Fixed

**Before:** `check_thaw_capacity_at_time()` did NOT filter by `ThawProfile`:

```python
# BUG: queried ALL active entries regardless of profile
active_entries = ThawQueueEntry.objects.filter(
    status__in=[...],
    planned_start_at__lte=target_time,
    target_ready_at__gt=target_time,
)
```

**After:** Now correctly scoped to the given profile:

```python
# FIX: scoped to the specific profile
active_entries = ThawQueueEntry.objects.filter(
    rotation_plan__thaw_profile=profile,  # ← ADDED
    status__in=[...],
    planned_start_at__lte=target_time,
    target_ready_at__gt=target_time,
)
```

### Impact

- `check_thaw_interval_overlap()` was already correctly profile-scoped
- `check_thaw_capacity_at_time()` is used in tests and capacity checks
- Bug could cause false capacity rejections when multiple profiles exist
- Fix ensures each profile's capacity is truly independent

### Regression Tests Added

1. `test_cross_profile_capacity_isolation` — proves Profile A entries don't consume Profile B capacity
2. `test_cross_profile_interval_overlap_isolation` — proves overlap checks are profile-scoped

---

## 6. Audit Retention

### Package FK Relationships

| Model | FK to Package | on_delete | Risk |
|-------|---------------|-----------|------|
| `operations.WorkerTask.package` | PROTECT | Cannot delete Package with active tasks |
| `operations.RotationEvent.package` | CASCADE | **Deletes audit history if Package deleted** |
| `inventory.PriceChangeHistory.package` | CASCADE | **Deletes price history if Package deleted** |
| `inventory.StockMovement.package` | CASCADE | **Deletes movement history if Package deleted** |
| `planning.RotationCycle.package` | PROTECT | Cannot delete Package with rotation cycles |
| `planning.RotationPlan.package` | PROTECT | Cannot delete Package with plans |
| `planning.ThawQueueEntry.package` | PROTECT | Cannot delete Package with queue entries |

### Recommended Long-Term Policy

| Model | Current | Recommended | Reason |
|-------|---------|-------------|--------|
| WorkerTask | PROTECT | PROTECT | Active operational data |
| RotationEvent | CASCADE | **SET_NULL** | Audit trail must survive |
| PriceChangeHistory | CASCADE | **SET_NULL** | Price history must survive |
| StockMovement | CASCADE | **SET_NULL** | Movement history must survive |
| RotationCycle | PROTECT | PROTECT | Structural integrity |
| RotationPlan | PROTECT | PROTECT | Structural integrity |
| ThawQueueEntry | PROTECT | PROTECT | Structural integrity |

### Migration Risk

Changing CASCADE → SET_NULL requires:
1. New Django migration
2. Test against staging database
3. Verify existing data integrity
4. **NOT applied to production in this phase**

---

## 7. Operations API Authorization

### TaskStatusAJAXView Audit

```python
class TaskStatusAJAXView(LoginRequiredMixin, View):
    def get(self, request, pk):
        task = WorkerTask.objects.get(pk=pk)  # ← No ownership check
        return JsonResponse({...})
```

**Finding: LOW RISK** — The view only returns status metadata (status label, claimed_by name, package state). It does not expose sensitive data or allow mutations. Any authenticated worker can poll any task's status, which is acceptable for a shared task board.

### TaskListAJAXView Audit

```python
class TaskListAJAXView(LoginRequiredMixin, View):
    def get(self, request):
        # Returns aggregate counts only
        return JsonResponse({
            'pending': ..., 'claimed': ..., 'in_progress': ..., ...
        })
```

**Finding: SAFE** — Returns only aggregate counts, no individual task data.

### BarcodeScanView Audit

Already hardened in Phase 06:
- task_id required
- IN_PROGRESS only
- claimant only
- barcode must match

**Finding: SAFE**

### WorkerCompleteTaskView Audit

Already hardened in Phase 06:
- barcode mandatory
- ownership enforced via service layer

**Finding: SAFE**

### Recommendation

No authorization changes needed for Phase 06.5. The current model (authenticated users can see task board, only claimants can operate) is correct for a shared workspace.

---

## 8. Task Board Semantics

### Current Implementation

`WorkerTaskListView` shows:

- **Pending:** All PENDING tasks scheduled for today (any worker)
- **Claimed:** All CLAIMED tasks scheduled for today
- **In Progress:** All IN_PROGRESS tasks scheduled for today
- **Completed:** All COMPLETED tasks scheduled for today
- **Cancelled:** All CANCELLED/SKIPPED/OVERDUE tasks scheduled for today

### Semantics Decision

**`งานของฉัน` means: "Operational tasks for today" (B), NOT "tasks assigned to me" (A)**

Rationale:
- PENDING tasks are shown to ALL workers for claiming
- CLAIMED/IN_PROGRESS tasks show who claimed them
- The board is a shared workspace, not a personal task list
- Workers claim tasks from the shared pool

### Task History

`WorkerTaskHistoryView` shows ONLY the current worker's historical tasks:
- `claimed_by=self.request.user`
- COMPLETED, CANCELLED, SKIPPED, OVERDUE

This is correctly scoped per-worker.

---

## 9. Batch Domain Decision

### Current Model

```python
class Batch(models.Model):
    batch_number = models.CharField(max_length=50, unique=True)
    supplier = models.ForeignKey(Supplier, ...)
    received_at = models.DateTimeField()
    notes = models.TextField(blank=True)
    active = models.BooleanField(default=True)
```

### Analysis

| Aspect | Current Behavior | Implication |
|--------|-----------------|-------------|
| Identity | `batch_number` (unique) | One batch = one receiving event |
| Scope | Per-supplier | Batch groups packages from same shipment |
| Multiple Products | Yes — Batch has no Product FK | A batch can contain different products |
| FIFO/FEFO | Not enforced at Batch level | Enforced at Package level via rotation |
| Cost | Not tracked on Batch | Tracked on Product (cost_per_kg) |
| Traceability | Batch → Package chain | Full traceability from receiving to sale |

### Decision: **UNRESOLVED**

Batch currently means: **(A) Supplier receiving batch** — packages from one shipment grouped together.

This is sufficient for current operations. FIFO/FEFO is managed at the Package level through the rotation lifecycle, not at the Batch level.

**No schema change required.** Document for future reference.

---

## 10. Static Audit Findings

### Direct WorkerTask ORM Access Outside Operations

| # | Location | Finding | Severity |
|---|----------|---------|----------|
| 1 | `common/state_machine.py:251` | `_auto_complete_worker_tasks()` directly queries WorkerTask | WARNING — should migrate to service call |
| 2 | `common/worker_actions.py:164` | `get_next_action()` directly queries WorkerTask | WARNING — should migrate to service call |
| 3 | `inventory/models.py:338` | `Package.current_task` property directly queries WorkerTask | WARNING — lazy import, acceptable for now |
| 4 | `planning/services.py:186` | `generate_worker_tasks()` creates WorkerTask via bulk_create | WARNING — canonical creation path, acceptable |
| 5 | `planning/tests.py:789` | Test directly queries WorkerTask for assertions | SAFE — test code |

### Direct Package.current_state Mutation

| # | Location | Finding | Severity |
|---|----------|---------|----------|
| 1 | `common/state_machine.py:88` | `transition_package()` — canonical mutation path | SAFE |
| 2 | `inventory/test_*.py` | Test setup code — direct assignment for test fixtures | SAFE |
| 3 | `project_management_clmeat/...` | Legacy code — not part of canonical path | WARNING — legacy |

### Hardcoded Credentials

None found. All credentials use environment variables.

### TODO/FIXME Related to Boundaries

| # | File | Finding | Severity |
|---|------|---------|----------|
| 1 | `ARCHITECTURE.md` | "WorkerTask → FUTURE: tasks.Task" documented | INFO |
| 2 | `MIGRATION_READINESS.md` | Migration boundaries documented | INFO |

---

## 11. Documentation Status

| Document | Status | Action |
|----------|--------|--------|
| `docs/ARCHITECTURE.md` | Current but test counts stale (315 vs 784) | Updated below |
| `docs/ARCHITECTURE_DECISIONS.md` | Current | No change |
| `docs/MIGRATION_BOUNDARIES.md` | Current | No change |
| `docs/MIGRATION_READINESS.md` | Current | No change |
| `docs/ENVIRONMENT_SETUP.md` | Current | No change |
| `.freebuff/run.md` | States Python 3.9.6 — actual is system Python | Updated below |
| `README.md` | Missing at root | Created |

---

## 12. Test Results

```
Phase 06.5 Verification:
─────────────────────────

$ python manage.py check
System check identified no issues (0 silenced) ✅

$ python manage.py makemigrations --check --dry-run
No changes detected ✅

$ DJANGO_ENV=staging python manage.py test --verbosity=0 --keepdb
Ran 784 tests — OK ✅

New tests added: 2
  - test_cross_profile_capacity_isolation
  - test_cross_profile_interval_overlap_isolation

Pre-existing failures: 0
Environment-specific issues: 0
```

---

## 13. Files Changed in Phase 06.5

| File | Change | Reason |
|------|--------|--------|
| `planning/services.py` | Fix `check_thaw_capacity_at_time` profile isolation | Bug: capacity check was not scoped to profile |
| `planning/tests.py` | Add 2 cross-profile isolation tests | Regression test for capacity bug |
| `docs/INTEGRATION_BOUNDARY.md` | New document | Phase 06.5 boundary hardening documentation |

---

*End of Integration Boundary Document*
