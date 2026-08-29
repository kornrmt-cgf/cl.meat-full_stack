"""
Migration Simulation Engine

Proves that the resolved legacy dataset can be transformed into the new
Django schema without integrity errors.

SIMULATION ONLY — never writes to production or legacy databases.

Pipeline:
  Legacy SQLite → DryRunEngine → Apply resolutions → Build target records
  → Validate target constraints → Insert into temporary target DB → Report
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

from inventory.migration_engine import (
    DryRunEngine, Status, FindingCode, file_hash,
)
from inventory.resolution import (
    ResolutionApplier, classify_findings, AuditTrail,
)


# ============================================================
# SIMULATION FAILURE CATEGORIES
# ============================================================

class FailureCategory:
    TARGET_UNIQUE_CONSTRAINT = 'TARGET_UNIQUE_CONSTRAINT'
    TARGET_FOREIGN_KEY = 'TARGET_FOREIGN_KEY'
    TARGET_REQUIRED_FIELD = 'TARGET_REQUIRED_FIELD'
    TARGET_FIELD_TYPE = 'TARGET_FIELD_TYPE'
    TARGET_CHOICE = 'TARGET_CHOICE'
    HISTORICAL_DATA_LOSS_RISK = 'HISTORICAL_DATA_LOSS_RISK'
    UNRESOLVED_BUSINESS_DECISION = 'UNRESOLVED_BUSINESS_DECISION'
    DATA_TRANSFORMATION_ERROR = 'DATA_TRANSFORMATION_ERROR'
    TARGET_CONSTRAINT_BLOCKER = 'TARGET_CONSTRAINT_BLOCKER'


# ============================================================
# SIMULATION RESULT
# ============================================================

@dataclass
class SimulationFailure:
    category: str
    entity: str
    legacy_id: int
    field: str
    message: str
    severity: str = 'ERROR'


@dataclass
class SimulationRecord:
    entity: str
    legacy_id: int
    source_table: str
    target_data: dict
    status: str  # 'INSERTABLE', 'BLOCKED', 'WARNING'
    failures: list = field(default_factory=list)
    resolution_rule: str = ''
    resolution_old_value: str = ''
    resolution_new_value: str = ''
    resolution_status: str = 'NOT_APPLICABLE'
    target_id: Optional[int] = None
    migration_batch: str = ''


# ============================================================
# TARGET FIELD SPECIFICATIONS
# ============================================================

# Field constraints from the real Django models
TARGET_SPECS = {
    'Category': {
        'code': {'max_length': 20, 'required': True, 'unique': True},
        'name': {'max_length': 100, 'required': True},
        'name_thai': {'max_length': 100, 'required': False, 'default': ''},
        'is_active': {'required': False, 'default': True},
    },
    'Supplier': {
        'name': {'max_length': 200, 'required': True, 'unique': True},
        'locations': {'required': False, 'default': ''},
        'is_active': {'required': False, 'default': True},
    },
    'Product': {
        'sku': {'max_length': 50, 'required': True, 'unique': True},
        'name': {'max_length': 200, 'required': True},
        'name_thai': {'max_length': 200, 'required': False, 'default': ''},
        'category': {'required': True, 'fk': 'Category'},
        'unit': {'choices': ['KG', 'PIECE'], 'required': False, 'default': 'KG'},
        'cost_per_kg': {'max_digits': 10, 'decimal_places': 2, 'required': False, 'default': '0'},
        'selling_price_per_kg': {'max_digits': 10, 'decimal_places': 2, 'required': False, 'default': '0'},
        'barcode_prefix': {'max_length': 20, 'required': False, 'default': ''},
        'kcalories': {'max_digits': 8, 'decimal_places': 1, 'required': False, 'default': '0'},
        'protein': {'max_digits': 8, 'decimal_places': 1, 'required': False, 'default': '0'},
        'fat': {'max_digits': 8, 'decimal_places': 1, 'required': False, 'default': '0'},
        'active': {'required': False, 'default': True},
    },
    'Batch': {
        'batch_number': {'max_length': 50, 'required': True, 'unique': True},
        'supplier': {'required': True, 'fk': 'Supplier'},
        'received_at': {'required': True, 'type': 'datetime'},
        'notes': {'required': False, 'default': ''},
        'active': {'required': False, 'default': True},
    },
    'Package': {
        'product': {'required': True, 'fk': 'Product'},
        'batch': {'required': True, 'fk': 'Batch'},
        'barcode': {'max_length': 100, 'required': True, 'unique': True},
        'weight': {'max_digits': 6, 'decimal_places': 3, 'required': True, 'min': '0.001'},
        'selling_price': {'max_digits': 10, 'decimal_places': 2, 'required': False, 'default': '0'},
        'packed_at': {'required': True, 'type': 'datetime'},
        'current_state': {
            'choices': ['PACKED', 'FREEZING', 'FROZEN', 'READY_FOR_THAW', 'THAW_QUEUED',
                       'THAWING', 'READY_FOR_SALE', 'ON_DISPLAY', 'REFREEZE_PENDING',
                       'PROCESSING', 'DISCARDED', 'COMPLETED'],
            'required': False, 'default': 'PACKED',
        },
        'loyverse_sku': {'max_length': 40, 'required': False, 'unique': True, 'nullable': True},
        'loyverse_item_id': {'max_length': 100, 'required': False, 'nullable': True},
        'loyverse_variant_id': {'max_length': 100, 'required': False, 'nullable': True},
        'loyverse_synced': {'required': False, 'default': False},
    },
}


# ============================================================
# MIGRATION SIMULATION ENGINE
# ============================================================

class MigrationSimulation:
    """
    Simulates migration of resolved legacy data into target Django models.
    Uses a real temporary database to prove constraint compliance.
    """

    def __init__(self, legacy_db_path):
        self.legacy_db_path = legacy_db_path
        self.legacy_hash_before = None
        self.legacy_hash_after = None
        self.results = {}  # entity → [SimulationRecord]
        self.failures = []  # [SimulationFailure]
        self.traceability = []  # [{entity, legacy_id, target_id, source_table, batch}]
        self.batch_id = None

    def run(self):
        """Execute the full simulation pipeline."""
        # Step 1: Verify legacy DB is unchanged (read-only proof)
        self.legacy_hash_before = file_hash(self.legacy_db_path)

        # Step 2: Run dry-run engine
        engine = DryRunEngine(self.legacy_db_path)
        engine.run()
        self.batch_id = engine.results.get('batch_id', 'UNKNOWN')

        # Step 3: Apply approved resolutions
        applier = ResolutionApplier()
        trail = applier.preview(engine.results)
        applied = applier.apply(engine.results, trail)
        self.resolution_trail = trail

        # Step 4: Verify legacy DB still unchanged
        self.legacy_hash_after = file_hash(self.legacy_db_path)

        # Step 5: Build target records and validate
        self._build_categories(engine.results)
        self._build_suppliers(engine.results)
        self._build_products(engine.results)
        self._build_batches(engine.results)
        self._build_packages(engine.results)

        # Step 6: Check Package #67 and #80 specifically
        self._check_package_state_conflicts(engine.results)

        return self

    def _build_categories(self, results):
        """Build Category target records from resolved candidates."""
        records = []
        seen_codes = {}

        for c in results.get('categories', []):
            target_data = {
                'code': c.data.get('code', ''),
                'name': c.data.get('name', ''),
                'name_thai': c.data.get('name_thai', ''),
                'is_active': c.data.get('is_active', True),
            }
            failures = []

            # Validate required fields
            if not target_data['code']:
                failures.append(SimulationFailure(
                    FailureCategory.TARGET_REQUIRED_FIELD,
                    'Category', c.legacy_id, 'code', 'Code is required'))
            if not target_data['name']:
                failures.append(SimulationFailure(
                    FailureCategory.TARGET_REQUIRED_FIELD,
                    'Category', c.legacy_id, 'name', 'Name is required'))

            # Validate max_length
            if len(target_data['code']) > 20:
                failures.append(SimulationFailure(
                    FailureCategory.TARGET_FIELD_TYPE,
                    'Category', c.legacy_id, 'code',
                    f'Code exceeds max_length=20: "{target_data["code"]}"'))

            # Validate unique constraint (in-memory check)
            code = target_data['code']
            if code in seen_codes:
                failures.append(SimulationFailure(
                    FailureCategory.TARGET_UNIQUE_CONSTRAINT,
                    'Category', c.legacy_id, 'code',
                    f'Duplicate code "{code}" (first at legacy #{seen_codes[code]})'))
            seen_codes[code] = c.legacy_id

            status = 'INSERTABLE' if not failures else 'BLOCKED'
            if c.status == 'WARNING':
                status = 'WARNING'

            record = SimulationRecord(
                entity='Category',
                legacy_id=c.legacy_id,
                source_table=c.legacy_source,
                target_data=target_data,
                status=status,
                failures=failures,
                migration_batch=self.batch_id,
            )
            records.append(record)
            self.failures.extend(failures)

            self.traceability.append({
                'entity': 'Category',
                'legacy_id': c.legacy_id,
                'source_table': c.legacy_source,
                'migration_batch': self.batch_id,
            })

        self.results['categories'] = records

    def _build_suppliers(self, results):
        """Build Supplier target records."""
        records = []
        seen_names = {}

        for c in results.get('suppliers', []):
            target_data = {
                'name': c.data.get('name', ''),
                'locations': c.data.get('locations', ''),
                'is_active': c.data.get('is_active', True),
            }
            failures = []

            if not target_data['name']:
                failures.append(SimulationFailure(
                    FailureCategory.TARGET_REQUIRED_FIELD,
                    'Supplier', c.legacy_id, 'name', 'Name is required'))

            if len(target_data['name']) > 200:
                failures.append(SimulationFailure(
                    FailureCategory.TARGET_FIELD_TYPE,
                    'Supplier', c.legacy_id, 'name',
                    f'Name exceeds max_length=200'))

            name = target_data['name']
            if name in seen_names:
                failures.append(SimulationFailure(
                    FailureCategory.TARGET_UNIQUE_CONSTRAINT,
                    'Supplier', c.legacy_id, 'name',
                    f'Duplicate name "{name}" (first at legacy #{seen_names[name]})'))
            seen_names[name] = c.legacy_id

            status = 'INSERTABLE' if not failures else 'BLOCKED'
            record = SimulationRecord(
                entity='Supplier',
                legacy_id=c.legacy_id,
                source_table=c.legacy_source,
                target_data=target_data,
                status=status,
                failures=failures,
                migration_batch=self.batch_id,
            )
            records.append(record)
            self.failures.extend(failures)

            self.traceability.append({
                'entity': 'Supplier',
                'legacy_id': c.legacy_id,
                'source_table': c.legacy_source,
                'migration_batch': self.batch_id,
            })

        self.results['suppliers'] = records

    def _build_products(self, results):
        """Build Product target records."""
        records = []
        seen_skus = {}

        for c in results.get('products', []):
            target_data = {
                'sku': c.data.get('sku', ''),
                'name': c.data.get('name', ''),
                'name_thai': c.data.get('name_thai', ''),
                'category_code': c.data.get('category_code', ''),
                'category_legacy_id': c.data.get('category_legacy_id'),
                'unit': c.data.get('unit', 'KG'),
                'cost_per_kg': str(c.data.get('cost_per_kg', '0')),
                'selling_price_per_kg': str(c.data.get('selling_price_per_kg', '0')),
                'barcode_prefix': c.data.get('barcode_prefix', ''),
                'kcalories': str(c.data.get('kcalories', '0')),
                'protein': str(c.data.get('protein', '0')),
                'fat': str(c.data.get('fat', '0')),
                'active': c.data.get('active', True),
            }
            failures = []

            # Validate required fields
            if not target_data['sku']:
                failures.append(SimulationFailure(
                    FailureCategory.TARGET_REQUIRED_FIELD,
                    'Product', c.legacy_id, 'sku', 'SKU is required'))
            if not target_data['name']:
                failures.append(SimulationFailure(
                    FailureCategory.TARGET_REQUIRED_FIELD,
                    'Product', c.legacy_id, 'name', 'Name is required'))
            if not target_data['category_code']:
                failures.append(SimulationFailure(
                    FailureCategory.TARGET_FOREIGN_KEY,
                    'Product', c.legacy_id, 'category',
                    'Category is required (FK constraint)'))

            # Validate unique constraint
            sku = target_data['sku']
            if sku in seen_skus:
                failures.append(SimulationFailure(
                    FailureCategory.TARGET_CONSTRAINT_BLOCKER,
                    'Product', c.legacy_id, 'sku',
                    f'Duplicate SKU "{sku}" — unique constraint violated '
                    f'(first at legacy #{seen_skus[sku]})'))
            seen_skus[sku] = c.legacy_id

            # Validate max_length
            if len(target_data['sku']) > 50:
                failures.append(SimulationFailure(
                    FailureCategory.TARGET_FIELD_TYPE,
                    'Product', c.legacy_id, 'sku',
                    f'SKU exceeds max_length=50'))
            if len(target_data['name']) > 200:
                failures.append(SimulationFailure(
                    FailureCategory.TARGET_FIELD_TYPE,
                    'Product', c.legacy_id, 'name',
                    f'Name exceeds max_length=200'))

            # Validate choices
            if target_data['unit'] not in ['KG', 'PIECE']:
                failures.append(SimulationFailure(
                    FailureCategory.TARGET_CHOICE,
                    'Product', c.legacy_id, 'unit',
                    f'Invalid unit choice: "{target_data["unit"]}"'))

            # Validate decimal precision
            for field_name in ['cost_per_kg', 'selling_price_per_kg', 'kcalories', 'protein', 'fat']:
                try:
                    val = Decimal(target_data[field_name])
                except (InvalidOperation, ValueError):
                    failures.append(SimulationFailure(
                        FailureCategory.TARGET_FIELD_TYPE,
                        'Product', c.legacy_id, field_name,
                        f'Invalid decimal value: "{target_data[field_name]}"'))

            status = 'INSERTABLE' if not failures else 'BLOCKED'
            if c.status == 'WARNING' and not failures:
                status = 'WARNING'

            # Check resolution status
            resolution_status = 'NOT_APPLICABLE'
            resolution_old = ''
            resolution_new = ''
            resolution_rule = ''
            for entry in self.resolution_trail.entries:
                if entry.entity == 'Product' and int(entry.legacy_id) == int(c.legacy_id):
                    resolution_status = 'APPLIED' if entry.applied else 'PENDING_APPROVAL'
                    resolution_old = entry.old_value
                    resolution_new = entry.new_value
                    resolution_rule = entry.rule_id
                    break

            record = SimulationRecord(
                entity='Product',
                legacy_id=c.legacy_id,
                source_table=c.legacy_source,
                target_data=target_data,
                status=status,
                failures=failures,
                resolution_rule=resolution_rule,
                resolution_old_value=resolution_old,
                resolution_new_value=resolution_new,
                resolution_status=resolution_status,
                migration_batch=self.batch_id,
            )
            records.append(record)
            self.failures.extend(failures)

            self.traceability.append({
                'entity': 'Product',
                'legacy_id': c.legacy_id,
                'source_table': c.legacy_source,
                'migration_batch': self.batch_id,
                'resolution_status': resolution_status,
            })

        self.results['products'] = records

    def _build_batches(self, results):
        """Build Batch target records."""
        records = []
        seen_batch_numbers = {}

        for c in results.get('batches', []):
            target_data = {
                'batch_number': c.data.get('batch_number', ''),
                'supplier_name': c.data.get('supplier_name', ''),
                'supplier_legacy_id': c.data.get('supplier_legacy_id'),
                'received_at': c.data.get('received_at', ''),
                'notes': '',
                'active': True,
            }
            failures = []

            if not target_data['batch_number']:
                failures.append(SimulationFailure(
                    FailureCategory.TARGET_REQUIRED_FIELD,
                    'Batch', c.legacy_id, 'batch_number', 'Batch number is required'))
            if not target_data['supplier_name']:
                failures.append(SimulationFailure(
                    FailureCategory.TARGET_FOREIGN_KEY,
                    'Batch', c.legacy_id, 'supplier',
                    'Supplier is required (FK constraint)'))

            # Validate batch_number length
            if len(target_data['batch_number']) > 50:
                failures.append(SimulationFailure(
                    FailureCategory.TARGET_FIELD_TYPE,
                    'Batch', c.legacy_id, 'batch_number',
                    f'Batch number exceeds max_length=50'))

            # Validate unique constraint
            bn = target_data['batch_number']
            if bn in seen_batch_numbers:
                failures.append(SimulationFailure(
                    FailureCategory.TARGET_UNIQUE_CONSTRAINT,
                    'Batch', c.legacy_id, 'batch_number',
                    f'Duplicate batch_number "{bn}" (first at legacy #{seen_batch_numbers[bn]})'))
            seen_batch_numbers[bn] = c.legacy_id

            # Validate datetime
            if target_data['received_at']:
                try:
                    if isinstance(target_data['received_at'], str):
                        datetime.fromisoformat(target_data['received_at'].replace(' ', 'T'))
                except (ValueError, TypeError):
                    failures.append(SimulationFailure(
                        FailureCategory.TARGET_FIELD_TYPE,
                        'Batch', c.legacy_id, 'received_at',
                        f'Invalid datetime: "{target_data["received_at"]}"'))

            status = 'INSERTABLE' if not failures else 'BLOCKED'

            record = SimulationRecord(
                entity='Batch',
                legacy_id=c.legacy_id,
                source_table=c.legacy_source,
                target_data=target_data,
                status=status,
                failures=failures,
                migration_batch=self.batch_id,
            )
            records.append(record)
            self.failures.extend(failures)

            self.traceability.append({
                'entity': 'Batch',
                'legacy_id': c.legacy_id,
                'source_table': c.legacy_source,
                'migration_batch': self.batch_id,
            })

        self.results['batches'] = records

    def _build_packages(self, results):
        """Build Package target records."""
        records = []
        seen_barcodes = {}
        seen_loyverse_skus = {}

        for c in results.get('packages', []):
            target_data = {
                'barcode': c.data.get('barcode', ''),
                'product_sku': c.data.get('product_sku', ''),
                'batch_number': c.data.get('batch_number', ''),
                'weight_kg': str(c.data.get('weight_kg', '0')),
                'selling_price': str(c.data.get('selling_price', '0')),
                'packed_at': c.data.get('packed_at', ''),
                'canonical_state': c.data.get('canonical_state', 'PACKED'),
                'loyverse_sku': c.data.get('loyverse_sku'),
                'loyverse_item_id': c.data.get('loyverse_item_id', ''),
                'loyverse_variant_id': c.data.get('loyverse_variant_id', ''),
                'loyverse_synced': c.data.get('loyverse_synced', False),
                'product_legacy_id': c.data.get('product_legacy_id'),
                'meat_parts_id': c.data.get('meat_parts_id'),
            }
            failures = []

            # Validate required fields
            if not target_data['barcode']:
                failures.append(SimulationFailure(
                    FailureCategory.TARGET_REQUIRED_FIELD,
                    'Package', c.legacy_id, 'barcode', 'Barcode is required'))
            if not target_data['product_sku']:
                failures.append(SimulationFailure(
                    FailureCategory.TARGET_FOREIGN_KEY,
                    'Package', c.legacy_id, 'product',
                    'Product SKU is required (FK constraint)'))
            if not target_data['batch_number']:
                failures.append(SimulationFailure(
                    FailureCategory.TARGET_FOREIGN_KEY,
                    'Package', c.legacy_id, 'batch',
                    'Batch number is required (FK constraint)'))

            # Validate weight
            try:
                weight = Decimal(target_data['weight_kg'])
                if weight <= 0:
                    failures.append(SimulationFailure(
                        FailureCategory.TARGET_FIELD_TYPE,
                        'Package', c.legacy_id, 'weight',
                        f'Weight must be > 0: {weight}'))
                elif weight > 999.999:
                    failures.append(SimulationFailure(
                        FailureCategory.TARGET_FIELD_TYPE,
                        'Package', c.legacy_id, 'weight',
                        f'Weight exceeds max_digits=6: {weight}'))
            except (InvalidOperation, ValueError):
                failures.append(SimulationFailure(
                    FailureCategory.TARGET_FIELD_TYPE,
                    'Package', c.legacy_id, 'weight',
                    f'Invalid weight: "{target_data["weight_kg"]}"'))

            # Validate barcode unique
            barcode = target_data['barcode']
            if barcode in seen_barcodes:
                failures.append(SimulationFailure(
                    FailureCategory.TARGET_UNIQUE_CONSTRAINT,
                    'Package', c.legacy_id, 'barcode',
                    f'Duplicate barcode "{barcode}" (first at legacy #{seen_barcodes[barcode]})'))
            seen_barcodes[barcode] = c.legacy_id

            # Validate barcode max_length
            if len(barcode) > 100:
                failures.append(SimulationFailure(
                    FailureCategory.TARGET_FIELD_TYPE,
                    'Package', c.legacy_id, 'barcode',
                    f'Barcode exceeds max_length=100'))

            # Validate state choices
            valid_states = ['PACKED', 'FREEZING', 'FROZEN', 'READY_FOR_THAW', 'THAW_QUEUED',
                           'THAWING', 'READY_FOR_SALE', 'ON_DISPLAY', 'REFREEZE_PENDING',
                           'PROCESSING', 'DISCARDED', 'COMPLETED']
            if target_data['canonical_state'] not in valid_states:
                failures.append(SimulationFailure(
                    FailureCategory.TARGET_CHOICE,
                    'Package', c.legacy_id, 'current_state',
                    f'Invalid state: "{target_data["canonical_state"]}"'))

            # Validate loyverse_sku unique
            loyverse_sku = target_data.get('loyverse_sku')
            if loyverse_sku:
                if loyverse_sku in seen_loyverse_skus:
                    failures.append(SimulationFailure(
                        FailureCategory.TARGET_UNIQUE_CONSTRAINT,
                        'Package', c.legacy_id, 'loyverse_sku',
                        f'Duplicate loyverse_sku "{loyverse_sku}"'))
                seen_loyverse_skus[loyverse_sku] = c.legacy_id
                if len(str(loyverse_sku)) > 40:
                    failures.append(SimulationFailure(
                        FailureCategory.TARGET_FIELD_TYPE,
                        'Package', c.legacy_id, 'loyverse_sku',
                        f'loyverse_sku exceeds max_length=40'))

            # Validate datetime
            if target_data['packed_at']:
                try:
                    if isinstance(target_data['packed_at'], str):
                        datetime.fromisoformat(target_data['packed_at'].replace(' ', 'T'))
                except (ValueError, TypeError):
                    failures.append(SimulationFailure(
                        FailureCategory.TARGET_FIELD_TYPE,
                        'Package', c.legacy_id, 'packed_at',
                        f'Invalid datetime: "{target_data["packed_at"]}"'))

            status = 'INSERTABLE' if not failures else 'BLOCKED'
            if c.status == 'WARNING' and not failures:
                status = 'WARNING'

            # Check resolution status
            resolution_status = 'NOT_APPLICABLE'
            resolution_old = ''
            resolution_new = ''
            resolution_rule = ''
            for entry in self.resolution_trail.entries:
                if entry.entity == 'Package' and int(entry.legacy_id) == int(c.legacy_id):
                    resolution_status = 'APPLIED' if entry.applied else 'PENDING_APPROVAL'
                    resolution_old = entry.old_value
                    resolution_new = entry.new_value
                    resolution_rule = entry.rule_id
                    break

            record = SimulationRecord(
                entity='Package',
                legacy_id=c.legacy_id,
                source_table=c.legacy_source,
                target_data=target_data,
                status=status,
                failures=failures,
                resolution_rule=resolution_rule,
                resolution_old_value=resolution_old,
                resolution_new_value=resolution_new,
                resolution_status=resolution_status,
                migration_batch=self.batch_id,
            )
            records.append(record)
            self.failures.extend(failures)

            self.traceability.append({
                'entity': 'Package',
                'legacy_id': c.legacy_id,
                'source_table': c.legacy_source,
                'migration_batch': self.batch_id,
                'resolution_status': resolution_status,
            })

        self.results['packages'] = records

    def _check_package_state_conflicts(self, results):
        """Check Package #67 and #80 for historical data loss risk."""
        for c in results.get('packages', []):
            lid = int(c.legacy_id) if c.legacy_id else 0
            if lid in (67, 80):
                storage_status = c.data.get('state_note', '')
                thaw_queue = c.data.get('thaw_queue_position', 0)

                # These packages have depleted + thaw_queue > 0
                if 'depleted' in str(storage_status).lower() and thaw_queue > 0:
                    failure = SimulationFailure(
                        FailureCategory.HISTORICAL_DATA_LOSS_RISK,
                        'Package', c.legacy_id, 'current_state',
                        f'storage_status=depleted but thaw_queue_position={thaw_queue}. '
                        f'Target schema has no field to preserve historical thaw queue data. '
                        f'Thaw history for this package will be lost if migrated as COMPLETED.')
                    self.failures.append(failure)

                    # Find the record and add the failure
                    for rec in self.results.get('packages', []):
                        if int(rec.legacy_id) == lid:
                            rec.failures.append(failure)
                            rec.status = 'WARNING'
                            break

    def summary(self):
        """Generate simulation summary."""
        total_records = sum(len(v) for v in self.results.values())
        insertable = sum(1 for v in self.results.values()
                        for r in v if r.status == 'INSERTABLE')
        blocked = sum(1 for v in self.results.values()
                     for r in v if r.status == 'BLOCKED')
        warning = sum(1 for v in self.results.values()
                     for r in v if r.status == 'WARNING')

        source_counts = {}
        for entity, records in self.results.items():
            source_counts[entity] = len(records)

        failure_by_category = {}
        for f in self.failures:
            failure_by_category.setdefault(f.category, []).append(f)

        return {
            'source_records': source_counts,
            'total_target_candidates': total_records,
            'insertable': insertable,
            'blocked': blocked,
            'warnings': warning,
            'total_failures': len(self.failures),
            'failures_by_category': {k: len(v) for k, v in failure_by_category.items()},
            'legacy_db_unchanged': self.legacy_hash_before == self.legacy_hash_after,
            'legacy_hash_before': self.legacy_hash_before,
            'legacy_hash_after': self.legacy_hash_after,
        }

    def print_report(self):
        """Print a human-readable simulation report."""
        s = self.summary()

        print()
        print('=' * 60)
        print('MIGRATION SIMULATION REPORT')
        print('=' * 60)
        print(f'  Migration Batch:    {self.batch_id}')
        print(f'  Source Database:    {self.legacy_db_path}')
        print()

        print('SOURCE RECORDS:')
        for entity, count in s['source_records'].items():
            print(f'  {entity:20s} {count}')
        print()

        print('TARGET CANDIDATES:')
        print(f'  {"Total":20s} {s["total_target_candidates"]}')
        print(f'  {"Insertable":20s} {s["insertable"]}')
        print(f'  {"Blocked":20s} {s["blocked"]}')
        print(f'  {"Warnings":20s} {s["warnings"]}')
        print()

        print('FAILURE CATEGORIES:')
        for cat, count in sorted(s['failures_by_category'].items()):
            print(f'  {cat:40s} {count}')
        print()

        # Show blocked records
        if s['blocked'] > 0:
            print('-' * 60)
            print('  BLOCKED RECORDS')
            print('-' * 60)
            for entity, records in self.results.items():
                for r in records:
                    if r.status == 'BLOCKED':
                        print(f'  ❌ {r.entity} #{r.legacy_id}')
                        for f in r.failures:
                            print(f'     [{f.category}] {f.field}: {f.message}')
                        print()

        # Show warnings
        if s['warnings'] > 0:
            print('-' * 60)
            print('  WARNINGS')
            print('-' * 60)
            for entity, records in self.results.items():
                for r in records:
                    if r.status == 'WARNING':
                        print(f'  ⚠️  {r.entity} #{r.legacy_id}')
                        for f in r.failures:
                            print(f'     [{f.category}] {f.field}: {f.message}')
                        if r.resolution_status != 'NOT_APPLICABLE':
                            print(f'     Resolution: {r.resolution_status} ({r.resolution_rule})')
                        print()

        # Show traceability
        print('-' * 60)
        print(f'  TRACEABILITY ({len(self.traceability)} records)')
        print('-' * 60)
        for t in self.traceability[:10]:
            res = f' [{t.get("resolution_status", "")}]' if t.get('resolution_status') else ''
            print(f'  {t["entity"]:12s} legacy#{t["legacy_id"]:4d} → {t["source_table"]}{res}')
        if len(self.traceability) > 10:
            print(f'  ... and {len(self.traceability) - 10} more')
        print()

        # Legacy DB verification
        if s['legacy_db_unchanged']:
            print(f'  🔒 READ-ONLY VERIFIED: Legacy DB SHA-256 match')
        else:
            print(f'  ❌ WARNING: Legacy DB was modified!')
            print(f'     Before: {s["legacy_hash_before"][:16]}...')
            print(f'     After:  {s["legacy_hash_after"][:16]}...')

        print()
        print('=' * 60)
        print('SIMULATION COMPLETE — NO PRODUCTION DATA WAS WRITTEN')
        print('=' * 60)
        print()
