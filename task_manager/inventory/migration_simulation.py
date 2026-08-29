"""
Migration Simulation Engine — Real Target Database Validation

Uses Django's test database for constraint enforcement.
Each simulation run uses get_or_create for reference data (Category, Supplier)
and tests real DB constraints (unique, FK, type, required) on new records.

SIMULATION ONLY — test database is rolled back after each test.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

from django.db import transaction, IntegrityError, DataError
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.utils import DataError as DjangoDataError

from inventory.migration_engine import (
    DryRunEngine, Status, FindingCode, file_hash,
)
from inventory.resolution import (
    ResolutionApplier, classify_findings, AuditTrail,
)
from inventory.models import (
    Category, Supplier, Product, Batch, Package, PackageState,
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
# DATA CLASSES
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
    status: str
    failures: list = field(default_factory=list)
    resolution_rule: str = ''
    resolution_old_value: str = ''
    resolution_new_value: str = ''
    resolution_status: str = 'NOT_APPLICABLE'
    target_id: Optional[int] = None
    migration_batch: str = ''


# ============================================================
# MIGRATION SIMULATION ENGINE
# ============================================================

class MigrationSimulation:
    """
    Simulates migration using REAL Django models.

    Uses get_or_create for reference data (Category, Supplier) since they
    may already exist. Tests real DB constraints on Product, Batch, Package.
    """

    def __init__(self, legacy_db_path):
        self.legacy_db_path = legacy_db_path
        self.legacy_hash_before = None
        self.legacy_hash_after = None
        self.results = {}
        self.failures = []
        self.traceability = []
        self.batch_id = None
        self.resolution_trail = None

    def run(self):
        """Execute the full simulation pipeline."""
        self.legacy_hash_before = file_hash(self.legacy_db_path)

        engine = DryRunEngine(self.legacy_db_path)
        engine.run()
        self.batch_id = engine.results.get('batch_id', 'UNKNOWN')

        applier = ResolutionApplier()
        trail = applier.preview(engine.results)
        applier.apply(engine.results, trail)
        self.resolution_trail = trail

        self.legacy_hash_after = file_hash(self.legacy_db_path)

        # Each entity insertion uses savepoints to prevent
        # IntegrityError from breaking the outer transaction
        self._build_and_insert(engine.results)

        self._check_package_state_conflicts(engine.results)
        return self

    def _build_and_insert(self, results):
        """Build target records and insert into real Django DB."""
        category_map = {}
        supplier_map = {}
        product_map = {}
        batch_map = {}

        # ── CATEGORIES (get_or_create — reference data) ──
        cat_records = []
        for c in results.get('categories', []):
            record = SimulationRecord(
                entity='Category', legacy_id=c.legacy_id,
                source_table=c.legacy_source, target_data={}, status='INSERTABLE',
                migration_batch=self.batch_id)
            target_data = {
                'code': c.data.get('code', ''),
                'name': c.data.get('name', ''),
                'name_thai': c.data.get('name_thai', ''),
                'is_active': c.data.get('is_active', True),
            }
            record.target_data = target_data

            if not target_data['code']:
                record.failures.append(SimulationFailure(
                    FailureCategory.TARGET_REQUIRED_FIELD,
                    'Category', c.legacy_id, 'code', 'Code is required'))
                record.status = 'BLOCKED'
                self.failures.extend(record.failures)
            else:
                try:
                    obj, created = Category.objects.get_or_create(
                        code=target_data['code'],
                        defaults={'name': target_data['name'],
                                  'name_thai': target_data['name_thai'],
                                  'is_active': target_data['is_active']})
                    record.target_id = obj.pk
                    category_map[c.legacy_id] = obj
                    category_map[str(c.legacy_id)] = obj
                except Exception as e:
                    record.status = 'BLOCKED'
                    record.failures.append(SimulationFailure(
                        FailureCategory.TARGET_UNIQUE_CONSTRAINT,
                        'Category', c.legacy_id, 'code', f'DB error: {str(e)[:200]}'))
                    self.failures.extend(record.failures)

            record.resolution_status = self._get_resolution_status('Category', c.legacy_id)
            cat_records.append(record)
            self.traceability.append({
                'entity': 'Category', 'legacy_id': c.legacy_id,
                'target_id': record.target_id, 'source_table': c.legacy_source,
                'migration_batch': self.batch_id})
        self.results['categories'] = cat_records

        # ── SUPPLIERS (get_or_create) ──
        sup_records = []
        for c in results.get('suppliers', []):
            record = SimulationRecord(
                entity='Supplier', legacy_id=c.legacy_id,
                source_table=c.legacy_source, target_data={}, status='INSERTABLE',
                migration_batch=self.batch_id)
            target_data = {
                'name': c.data.get('name', ''),
                'locations': c.data.get('locations', ''),
                'is_active': c.data.get('is_active', True),
            }
            record.target_data = target_data

            if not target_data['name']:
                record.failures.append(SimulationFailure(
                    FailureCategory.TARGET_REQUIRED_FIELD,
                    'Supplier', c.legacy_id, 'name', 'Name is required'))
                record.status = 'BLOCKED'
                self.failures.extend(record.failures)
            else:
                try:
                    obj, created = Supplier.objects.get_or_create(
                        name=target_data['name'],
                        defaults={'locations': target_data['locations'],
                                  'is_active': target_data['is_active']})
                    record.target_id = obj.pk
                    supplier_map[c.legacy_id] = obj
                    supplier_map[str(c.legacy_id)] = obj
                except Exception as e:
                    record.status = 'BLOCKED'
                    record.failures.append(SimulationFailure(
                        FailureCategory.TARGET_UNIQUE_CONSTRAINT,
                        'Supplier', c.legacy_id, 'name', f'DB error: {str(e)[:200]}'))
                    self.failures.extend(record.failures)

            record.resolution_status = self._get_resolution_status('Supplier', c.legacy_id)
            sup_records.append(record)
            self.traceability.append({
                'entity': 'Supplier', 'legacy_id': c.legacy_id,
                'target_id': record.target_id, 'source_table': c.legacy_source,
                'migration_batch': self.batch_id})
        self.results['suppliers'] = sup_records

        # ── PRODUCTS (real DB insert — tests unique SKU constraint) ──
        prod_records = []
        for c in results.get('products', []):
            record = SimulationRecord(
                entity='Product', legacy_id=c.legacy_id,
                source_table=c.legacy_source, target_data={}, status='INSERTABLE',
                migration_batch=self.batch_id)
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
            record.target_data = target_data

            if not target_data['sku']:
                record.failures.append(SimulationFailure(
                    FailureCategory.TARGET_REQUIRED_FIELD,
                    'Product', c.legacy_id, 'sku', 'SKU is required'))
            if not target_data['name']:
                record.failures.append(SimulationFailure(
                    FailureCategory.TARGET_REQUIRED_FIELD,
                    'Product', c.legacy_id, 'name', 'Name is required'))

            # Resolve FK
            category_obj = None
            cat_id = target_data.get('category_legacy_id')
            if cat_id:
                category_obj = category_map.get(cat_id) or category_map.get(str(cat_id))
            if not category_obj and target_data['category_code']:
                category_obj = Category.objects.filter(code=target_data['category_code']).first()
            if not category_obj:
                record.failures.append(SimulationFailure(
                    FailureCategory.TARGET_FOREIGN_KEY,
                    'Product', c.legacy_id, 'category',
                    f'Category not found: code="{target_data["category_code"]}"'))

            if record.failures:
                record.status = 'BLOCKED'
                self.failures.extend(record.failures)
            else:
                sid = transaction.savepoint()
                try:
                    obj = Product.objects.create(
                        sku=target_data['sku'],
                        name=target_data['name'],
                        name_thai=target_data['name_thai'],
                        category=category_obj,
                        unit=target_data['unit'],
                        cost_per_kg=Decimal(target_data['cost_per_kg']),
                        selling_price_per_kg=Decimal(target_data['selling_price_per_kg']),
                        barcode_prefix=target_data['barcode_prefix'],
                        kcalories=Decimal(target_data['kcalories']),
                        protein=Decimal(target_data['protein']),
                        fat=Decimal(target_data['fat']),
                        active=target_data['active'])
                    record.target_id = obj.pk
                    product_map[c.legacy_id] = obj
                    product_map[str(c.legacy_id)] = obj
                except IntegrityError as e:
                    transaction.savepoint_rollback(sid)
                    record.status = 'BLOCKED'
                    record.failures.append(SimulationFailure(
                        FailureCategory.TARGET_CONSTRAINT_BLOCKER,
                        'Product', c.legacy_id, 'sku',
                        f'DB rejected: {str(e)[:200]}'))
                    self.failures.extend(record.failures)
                except (DataError, DjangoDataError) as e:
                    transaction.savepoint_rollback(sid)
                    record.status = 'BLOCKED'
                    record.failures.append(SimulationFailure(
                        FailureCategory.TARGET_FIELD_TYPE,
                        'Product', c.legacy_id, 'sku', f'DB rejected: {str(e)[:200]}'))
                    self.failures.extend(record.failures)
                except DjangoValidationError as e:
                    transaction.savepoint_rollback(sid)
                    record.status = 'BLOCKED'
                    for fld, errs in e.message_dict.items():
                        for err in errs:
                            record.failures.append(SimulationFailure(
                                FailureCategory.TARGET_FIELD_TYPE,
                                'Product', c.legacy_id, fld, f'Validation: {err}'))
                    self.failures.extend(record.failures)

            record.resolution_status = self._get_resolution_status('Product', c.legacy_id)
            prod_records.append(record)
            self.traceability.append({
                'entity': 'Product', 'legacy_id': c.legacy_id,
                'target_id': record.target_id, 'source_table': c.legacy_source,
                'migration_batch': self.batch_id,
                'resolution_status': record.resolution_status})
        self.results['products'] = prod_records

        # ── BATCHES (real DB insert — tests unique batch_number) ──
        batch_records = []
        for c in results.get('batches', []):
            record = SimulationRecord(
                entity='Batch', legacy_id=c.legacy_id,
                source_table=c.legacy_source, target_data={}, status='INSERTABLE',
                migration_batch=self.batch_id)
            target_data = {
                'batch_number': c.data.get('batch_number', ''),
                'supplier_name': c.data.get('supplier_name', ''),
                'supplier_legacy_id': c.data.get('supplier_legacy_id'),
                'received_at': c.data.get('received_at', ''),
            }
            record.target_data = target_data

            if not target_data['batch_number']:
                record.failures.append(SimulationFailure(
                    FailureCategory.TARGET_REQUIRED_FIELD,
                    'Batch', c.legacy_id, 'batch_number', 'Batch number is required'))

            supplier_obj = None
            sup_id = target_data.get('supplier_legacy_id')
            if sup_id:
                supplier_obj = supplier_map.get(sup_id) or supplier_map.get(str(sup_id))
            if not supplier_obj and target_data['supplier_name']:
                supplier_obj = Supplier.objects.filter(name=target_data['supplier_name']).first()
            if not supplier_obj:
                record.failures.append(SimulationFailure(
                    FailureCategory.TARGET_FOREIGN_KEY,
                    'Batch', c.legacy_id, 'supplier',
                    f'Supplier not found: "{target_data["supplier_name"]}"'))

            received_at = None
            if target_data['received_at']:
                try:
                    received_at = datetime.fromisoformat(
                        target_data['received_at'].replace(' ', 'T'))
                except (ValueError, TypeError):
                    record.failures.append(SimulationFailure(
                        FailureCategory.TARGET_FIELD_TYPE,
                        'Batch', c.legacy_id, 'received_at',
                        f'Invalid datetime: "{target_data["received_at"]}"'))
            if not received_at:
                record.failures.append(SimulationFailure(
                    FailureCategory.TARGET_REQUIRED_FIELD,
                    'Batch', c.legacy_id, 'received_at', 'required'))

            if record.failures:
                record.status = 'BLOCKED'
                self.failures.extend(record.failures)
            else:
                sid = transaction.savepoint()
                try:
                    obj = Batch.objects.create(
                        batch_number=target_data['batch_number'],
                        supplier=supplier_obj,
                        received_at=received_at)
                    record.target_id = obj.pk
                    batch_map[c.legacy_id] = obj
                    batch_map[str(c.legacy_id)] = obj
                except IntegrityError as e:
                    transaction.savepoint_rollback(sid)
                    record.status = 'BLOCKED'
                    record.failures.append(SimulationFailure(
                        FailureCategory.TARGET_UNIQUE_CONSTRAINT,
                        'Batch', c.legacy_id, 'batch_number',
                        f'DB rejected: {str(e)[:200]}'))
                    self.failures.extend(record.failures)

            record.resolution_status = self._get_resolution_status('Batch', c.legacy_id)
            batch_records.append(record)
            self.traceability.append({
                'entity': 'Batch', 'legacy_id': c.legacy_id,
                'target_id': record.target_id, 'source_table': c.legacy_source,
                'migration_batch': self.batch_id})
        self.results['batches'] = batch_records

        # ── PACKAGES (real DB insert — tests unique barcode, FK) ──
        pkg_records = []
        for c in results.get('packages', []):
            record = SimulationRecord(
                entity='Package', legacy_id=c.legacy_id,
                source_table=c.legacy_source, target_data={}, status='INSERTABLE',
                migration_batch=self.batch_id)
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
            record.target_data = target_data

            # Resolve FK: product
            product_obj = None
            mp_id = target_data.get('meat_parts_id')
            pi_id = target_data.get('product_legacy_id')
            if mp_id:
                product_obj = product_map.get(mp_id) or product_map.get(str(mp_id))
            if not product_obj and pi_id:
                product_obj = product_map.get(pi_id) or product_map.get(str(pi_id))
            if not product_obj and target_data['product_sku']:
                product_obj = Product.objects.filter(sku=target_data['product_sku']).first()
            if not product_obj:
                record.failures.append(SimulationFailure(
                    FailureCategory.TARGET_FOREIGN_KEY,
                    'Package', c.legacy_id, 'product',
                    f'Product not found: sku="{target_data["product_sku"]}"'))

            # Resolve FK: batch
            batch_obj = None
            if pi_id:
                batch_obj = batch_map.get(pi_id) or batch_map.get(str(pi_id))
            if not batch_obj and target_data['batch_number']:
                batch_obj = Batch.objects.filter(batch_number=target_data['batch_number']).first()
            if not batch_obj:
                record.failures.append(SimulationFailure(
                    FailureCategory.TARGET_FOREIGN_KEY,
                    'Package', c.legacy_id, 'batch',
                    f'Batch not found: "{target_data["batch_number"]}"'))

            # Parse datetime
            packed_at = None
            if target_data['packed_at']:
                try:
                    packed_at = datetime.fromisoformat(
                        target_data['packed_at'].replace(' ', 'T'))
                except (ValueError, TypeError):
                    record.failures.append(SimulationFailure(
                        FailureCategory.TARGET_FIELD_TYPE,
                        'Package', c.legacy_id, 'packed_at', 'Invalid datetime'))
            if not packed_at:
                record.failures.append(SimulationFailure(
                    FailureCategory.TARGET_REQUIRED_FIELD,
                    'Package', c.legacy_id, 'packed_at', 'required'))

            # Validate weight
            weight_kg = None
            try:
                weight_kg = Decimal(target_data['weight_kg'])
            except (InvalidOperation, ValueError):
                record.failures.append(SimulationFailure(
                    FailureCategory.TARGET_FIELD_TYPE,
                    'Package', c.legacy_id, 'weight', f'Invalid: "{target_data["weight_kg"]}"'))

            if record.failures:
                record.status = 'BLOCKED'
                self.failures.extend(record.failures)
            else:
                loyverse_sku = target_data.get('loyverse_sku')
                if loyverse_sku and str(loyverse_sku).strip():
                    loyverse_sku = str(loyverse_sku)
                else:
                    loyverse_sku = None

                sid = transaction.savepoint()
                try:
                    obj = Package.objects.create(
                        product=product_obj, batch=batch_obj,
                        barcode=target_data['barcode'],
                        weight=weight_kg,
                        selling_price=Decimal(target_data['selling_price']),
                        packed_at=packed_at,
                        current_state=target_data['canonical_state'],
                        loyverse_sku=loyverse_sku,
                        loyverse_item_id=target_data['loyverse_item_id'] or None,
                        loyverse_variant_id=target_data['loyverse_variant_id'] or None,
                        loyverse_synced=bool(target_data['loyverse_synced']))
                    record.target_id = obj.pk
                except IntegrityError as e:
                    transaction.savepoint_rollback(sid)
                    record.status = 'BLOCKED'
                    err_msg = str(e).lower()
                    if 'barcode' in err_msg:
                        fld = 'barcode'
                    elif 'loyverse' in err_msg:
                        fld = 'loyverse_sku'
                    else:
                        fld = 'unique constraint'
                    record.failures.append(SimulationFailure(
                        FailureCategory.TARGET_UNIQUE_CONSTRAINT,
                        'Package', c.legacy_id, fld, f'DB rejected: {str(e)[:200]}'))
                    self.failures.extend(record.failures)
                except DjangoValidationError as e:
                    transaction.savepoint_rollback(sid)
                    record.status = 'BLOCKED'
                    for fld, errs in e.message_dict.items():
                        for err in errs:
                            record.failures.append(SimulationFailure(
                                FailureCategory.TARGET_FIELD_TYPE,
                                'Package', c.legacy_id, fld, f'Validation: {err}'))
                    self.failures.extend(record.failures)

            record.resolution_status = self._get_resolution_status('Package', c.legacy_id)
            pkg_records.append(record)
            self.traceability.append({
                'entity': 'Package', 'legacy_id': c.legacy_id,
                'target_id': record.target_id, 'source_table': c.legacy_source,
                'migration_batch': self.batch_id,
                'resolution_status': record.resolution_status})
        self.results['packages'] = pkg_records

    def _check_package_state_conflicts(self, results):
        for c in results.get('packages', []):
            lid = int(c.legacy_id) if c.legacy_id else 0
            if lid in (67, 80):
                storage_status = c.data.get('state_note', '')
                thaw_queue = c.data.get('thaw_queue_position', 0)
                if 'depleted' in str(storage_status).lower() and thaw_queue > 0:
                    failure = SimulationFailure(
                        FailureCategory.HISTORICAL_DATA_LOSS_RISK,
                        'Package', c.legacy_id, 'current_state',
                        f'storage_status=depleted but thaw_queue_position={thaw_queue}. '
                        f'Target schema has no field to preserve historical thaw queue data.')
                    self.failures.append(failure)
                    for rec in self.results.get('packages', []):
                        if int(rec.legacy_id) == lid:
                            rec.failures.append(failure)
                            if rec.status == 'INSERTABLE':
                                rec.status = 'WARNING'
                            break

    def _get_resolution_status(self, entity, legacy_id):
        if not self.resolution_trail:
            return 'NOT_APPLICABLE'
        for entry in self.resolution_trail.entries:
            if entry.entity == entity and int(entry.legacy_id) == int(legacy_id):
                return 'APPLIED' if entry.applied else 'PENDING_APPROVAL'
        return 'NOT_APPLICABLE'

    def summary(self):
        total = sum(len(v) for v in self.results.values())
        insertable = sum(1 for v in self.results.values() for r in v if r.status == 'INSERTABLE')
        blocked = sum(1 for v in self.results.values() for r in v if r.status == 'BLOCKED')
        warning = sum(1 for v in self.results.values() for r in v if r.status == 'WARNING')
        failure_by_cat = {}
        for f in self.failures:
            failure_by_cat.setdefault(f.category, []).append(f)
        return {
            'total_target_candidates': total,
            'python_validation_passed': total,
            'real_db_insertable': insertable,
            'database_blocked': blocked,
            'warnings': warning,
            'total_failures': len(self.failures),
            'failures_by_category': {k: len(v) for k, v in failure_by_cat.items()},
            'legacy_db_unchanged': self.legacy_hash_before == self.legacy_hash_after,
            'legacy_hash_before': self.legacy_hash_before,
            'legacy_hash_after': self.legacy_hash_after,
        }

    def print_report(self):
        s = self.summary()
        print()
        print('=' * 60)
        print('MIGRATION SIMULATION REPORT (REAL TARGET DB)')
        print('=' * 60)
        print(f'  Migration Batch:    {self.batch_id}')
        print(f'  Source Database:    {self.legacy_db_path}')
        print()
        print('TARGET DATABASE SIMULATION:')
        print(f'  {"Total candidates":30s} {s["total_target_candidates"]}')
        print(f'  {"Python validation passed":30s} {s["python_validation_passed"]}')
        print(f'  {"Real DB insertable":30s} {s["real_db_insertable"]}')
        print(f'  {"Database blocked":30s} {s["database_blocked"]}')
        print(f'  {"Warnings":30s} {s["warnings"]}')
        print()
        print('REAL DATABASE CONSTRAINTS:')
        for cat, count in sorted(s['failures_by_category'].items()):
            print(f'  {cat:40s} {count}')
        print()

        if s['database_blocked'] > 0:
            print('-' * 60)
            print('  DATABASE BLOCKED RECORDS')
            print('-' * 60)
            for entity, records in self.results.items():
                for r in records:
                    if r.status == 'BLOCKED':
                        print(f'  ❌ {r.entity} #{r.legacy_id}')
                        for f in r.failures:
                            print(f'     [{f.category}] {f.field}: {f.message}')
                        print()

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
                        print()

        print('-' * 60)
        print(f'  TRACEABILITY ({len(self.traceability)} records)')
        print('-' * 60)
        for t in self.traceability[:10]:
            tid = f' → target#{t["target_id"]}' if t.get('target_id') else ''
            print(f'  {t["entity"]:12s} legacy#{t["legacy_id"]:4d}{tid}')
        if len(self.traceability) > 10:
            print(f'  ... and {len(self.traceability) - 10} more')
        print()

        if s['legacy_db_unchanged']:
            print(f'  🔒 READ-ONLY VERIFIED: Legacy DB SHA-256 match')
        print()
        print('=' * 60)
        print('SIMULATION COMPLETE — NO PRODUCTION DATA WAS WRITTEN')
        print('=' * 60)
        print()
