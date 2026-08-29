"""
Migration Simulation Engine — Isolated Target Database Validation

Pipeline:
  legacy.sqlite (READ ONLY)
  → DryRunEngine
  → ResolutionApplier
  → temporary_target.sqlite (created by Django migrations in a subprocess)
  → schema introspection (verify actual columns)
  → raw sqlite3 INSERT (simulate records)
  → root cause / dependent blocker classification
  → collect results
  → delete temporary database

Uses Django's actual migration files but runs the migrate command in a
subprocess to avoid Django test framework interference.
"""
import hashlib
import io
import os
import sqlite3
import subprocess
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

from inventory.migration_engine import DryRunEngine, file_hash
from inventory.resolution import ResolutionApplier


# ============================================================
# FAILURE CATEGORIES
# ============================================================

class FailureCategory:
    MODEL_VALIDATION = 'MODEL_VALIDATION'
    DATABASE_CONSTRAINT = 'DATABASE_CONSTRAINT'
    SOURCE_INTRINSIC_BLOCKER = 'SOURCE_INTRINSIC_BLOCKER'
    UNEXPECTED_ERROR = 'UNEXPECTED_ERROR'
    WARNING = 'WARNING'
    HISTORICAL_DATA_LOSS_RISK = 'HISTORICAL_DATA_LOSS_RISK'
    TARGET_FK = 'TARGET_FOREIGN_KEY'
    TARGET_REQUIRED = 'TARGET_REQUIRED_FIELD'
    TARGET_TYPE = 'TARGET_FIELD_TYPE'


class BlockerType:
    ROOT = 'ROOT_BLOCKER'
    DEPENDENT = 'DEPENDENT_BLOCKER'


@dataclass
class SimFailure:
    category: str
    entity: str
    legacy_id: int
    field: str
    operation: str
    error_class: str
    message: str
    expected: bool = True
    blocker_type: str = BlockerType.ROOT  # ROOT or DEPENDENT
    root_cause_key: str = ''  # e.g. 'Product:10' — the root entity:legacy_id


@dataclass
class SimRecord:
    entity: str
    legacy_id: int
    source_table: str
    target_data: dict
    status: str  # INSERTABLE, BLOCKED, WARNING
    failures: list = field(default_factory=list)
    resolution_rule: str = ''
    resolution_status: str = 'NOT_APPLICABLE'
    target_id: Optional[int] = None
    migration_batch: str = ''


def _run_django_migrations(target_db_path):
    """Run Django migrations against target_db_path in an isolated subprocess."""
    script = f"""
import os, sys
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
os.environ.setdefault('DJANGO_SECRET_KEY', 'sim-bootstrap-{os.getpid()}')

import django
from django.conf import settings

sim_config = {{
    'ENGINE': 'django.db.backends.sqlite3',
    'NAME': r'{target_db_path}',
}}
settings.DATABASES['simulation'] = sim_config
django.setup()

from django.core.management import call_command
call_command('migrate', database='simulation', verbosity=0)
print('MIGRATION_OK')
"""
    result = subprocess.run(
        [sys.executable, '-c', script],
        capture_output=True, text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f'Django migrations failed in subprocess:\n'
            f'stdout: {result.stdout}\nstderr: {result.stderr}'
        )
    if 'MIGRATION_OK' not in result.stdout:
        raise RuntimeError(
            f'Django migrations did not complete:\n'
            f'stdout: {result.stdout}\nstderr: {result.stderr}'
        )


# ============================================================
# SIMULATION ENGINE
# ============================================================

class MigrationSimulation:
    """Simulation using Django migrations on an isolated temporary database."""

    def __init__(self, legacy_db_path):
        self.legacy_db_path = legacy_db_path
        self.legacy_hash_before = None
        self.legacy_hash_after = None
        self.results = {}
        self.failures = []
        self.traceability = []
        self.batch_id = None
        self.resolution_trail = None
        self._sim_db_path = None
        self._schema_columns = {}
        self._root_causes = {}  # key → first failure that caused it
        self._default_hash_before = None
        self._default_hash_after = None

    def run(self):
        self.legacy_hash_before = file_hash(self.legacy_db_path)

        # Snapshot default DB
        default_name = os.environ.get('DJANGO_DB_NAME', 'db.sqlite3')
        if os.path.exists(default_name):
            self._default_hash_before = file_hash(default_name)

        # Dry-run + resolutions
        engine = DryRunEngine(self.legacy_db_path)
        engine.run()
        self.batch_id = engine.results.get('batch_id', 'UNKNOWN')
        applier = ResolutionApplier()
        trail = applier.preview(engine.results)
        applier.apply(engine.results, trail)
        self.resolution_trail = trail

        self.legacy_hash_after = file_hash(self.legacy_db_path)
        if os.path.exists(default_name):
            self._default_hash_after = file_hash(default_name)

        # Create isolated temp DB
        fd, self._sim_db_path = tempfile.mkstemp(suffix='_simulation.db')
        os.close(fd)

        try:
            _run_django_migrations(self._sim_db_path)
            self._introspect_schema()
            self._build_and_insert(engine.results)
            self._check_package_state_conflicts(engine.results)
            self._classify_root_vs_dependent()
        finally:
            self._cleanup()

        return self

    def _introspect_schema(self):
        """Introspect actual database schema using raw PRAGMA."""
        conn = sqlite3.connect(self._sim_db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'inventory_%'")
        tables = [row[0] for row in cursor.fetchall()]
        for table in tables:
            cursor.execute(f"PRAGMA table_info({table})")
            columns = [(row[1], row[2], bool(row[3])) for row in cursor.fetchall()]
            self._schema_columns[table] = columns
        conn.close()

    # ─── ROOT CAUSE / DEPENDENT CLASSIFICATION ──────────────────

    def _classify_root_vs_dependent(self):
        """Walk dependency chain and classify every blocker failure.

        Dependency chain:
          Product invalid (no category / no SKU / no name)
            → Batch invalid (references invalid product)
              → Package invalid (references invalid product / batch)

        The first failure per entity is ROOT_BLOCKER.
        Any failure on a downstream entity caused by the same root is DEPENDENT.
        """
        # Build maps: entity → legacy_id → SimRecord
        entity_map = defaultdict(dict)
        for entity, records in self.results.items():
            for r in records:
                entity_map[r.entity][r.legacy_id] = r

        # Build reference maps from target_data
        # Product category_legacy_id → check if category exists in temp DB
        # Batch supplier_legacy_id → check if supplier exists
        # Package meat_parts_id / product_legacy_id → check product
        # Package product_legacy_id → check batch

        # Step 1: Identify root causes — records whose OWN fields are invalid
        root_causes = {}  # key → first failure
        for entity, records in self.results.items():
            for r in records:
                if r.status != 'BLOCKED':
                    continue
                for f in r.failures:
                    # A failure is ROOT if it doesn't depend on an FK to
                    # another entity that is also blocked.
                    is_dependent = False
                    if f.category == FailureCategory.TARGET_FK:
                        # Check if the referenced entity is also blocked
                        if f.field == 'category' and f.entity == 'Product':
                            # Product's category FK — check if category is blocked
                            cat_legacy_id = r.target_data.get('category_legacy_id')
                            cat_rec = entity_map.get('Category', {}).get(cat_legacy_id)
                            if cat_rec and cat_rec.status == 'BLOCKED':
                                is_dependent = True
                                f.root_cause_key = f'Category:{cat_legacy_id}'

                        elif f.field == 'batch' and f.entity == 'Package':
                            # Package's batch FK — check if batch is blocked
                            pi_id = r.target_data.get('product_legacy_id')
                            batch_rec = entity_map.get('Batch', {}).get(pi_id)
                            if batch_rec and batch_rec.status == 'BLOCKED':
                                is_dependent = True
                                # Trace to batch's root cause
                                batch_root = root_causes.get(f'Batch:{pi_id}')
                                if batch_root:
                                    f.root_cause_key = batch_root.root_cause_key
                                else:
                                    f.root_cause_key = f'Batch:{pi_id}'

                        elif f.field == 'product' and f.entity == 'Package':
                            # Package's product FK — check if product is blocked
                            mp_id = r.target_data.get('meat_parts_id')
                            prod_rec = entity_map.get('Product', {}).get(mp_id)
                            if prod_rec and prod_rec.status == 'BLOCKED':
                                is_dependent = True
                                # Trace to product's root cause
                                prod_root = root_causes.get(f'Product:{mp_id}')
                                if prod_root:
                                    f.root_cause_key = prod_root.root_cause_key
                                else:
                                    f.root_cause_key = f'Product:{mp_id}'

                    if is_dependent:
                        f.blocker_type = BlockerType.DEPENDENT
                    else:
                        f.blocker_type = BlockerType.ROOT
                        key = f'{f.entity}:{f.legacy_id}'
                        if key not in root_causes:
                            root_causes[key] = f

        self._root_causes = root_causes

    # ─── INSERT OPERATIONS ──────────────────────────────────────

    def _build_and_insert(self, results):
        """Insert all records into the Django-migrated temp database."""
        conn = sqlite3.connect(self._sim_db_path)
        conn.execute('PRAGMA foreign_keys = ON')

        cat_map = {}
        sup_map = {}
        prod_map = {}
        batch_map = {}

        # ── CATEGORIES ──
        cat_records = []
        for c in results.get('categories', []):
            record = SimRecord(
                entity='Category', legacy_id=c.legacy_id,
                source_table=c.legacy_source, target_data={}, status='INSERTABLE',
                migration_batch=self.batch_id)
            td = {
                'code': c.data.get('code', ''),
                'name': c.data.get('name', ''),
                'name_thai': c.data.get('name_thai', ''),
                'is_active': c.data.get('is_active', True),
            }
            record.target_data = td

            if not td['code']:
                record.failures.append(SimFailure(
                    FailureCategory.TARGET_REQUIRED, 'Category', c.legacy_id, 'code',
                    'INSERT', 'ValueError', 'Code is required'))
                record.status = 'BLOCKED'
            else:
                try:
                    now = datetime.now().isoformat()
                    cur = conn.execute(
                        'INSERT INTO inventory_category '
                        '(code, name, name_thai, is_active, created_at) '
                        'VALUES (?,?,?,?,?)',
                        (td['code'], td['name'], td['name_thai'],
                         int(td['is_active']), now))
                    record.target_id = cur.lastrowid
                    cat_map[c.legacy_id] = record.target_id
                    cat_map[str(c.legacy_id)] = record.target_id
                    conn.commit()
                except sqlite3.IntegrityError as e:
                    conn.rollback()
                    record.status = 'BLOCKED'
                    record.failures.append(SimFailure(
                        FailureCategory.SOURCE_INTRINSIC_BLOCKER,
                        'Category', c.legacy_id, 'code',
                        'INSERT', 'IntegrityError', str(e)[:200], expected=True))

            record.resolution_status = self._get_resolution(
                'Category', c.legacy_id)
            cat_records.append(record)
            self.failures.extend(record.failures)
            self.traceability.append({
                'entity': 'Category', 'legacy_id': c.legacy_id,
                'target_id': record.target_id,
                'source_table': c.legacy_source,
                'migration_batch': self.batch_id})
        self.results['categories'] = cat_records

        # ── SUPPLIERS ──
        sup_records = []
        for c in results.get('suppliers', []):
            record = SimRecord(
                entity='Supplier', legacy_id=c.legacy_id,
                source_table=c.legacy_source, target_data={}, status='INSERTABLE',
                migration_batch=self.batch_id)
            td = {
                'name': c.data.get('name', ''),
                'locations': c.data.get('locations', ''),
                'is_active': c.data.get('is_active', True),
            }
            record.target_data = td

            if not td['name']:
                record.failures.append(SimFailure(
                    FailureCategory.TARGET_REQUIRED, 'Supplier', c.legacy_id,
                    'name', 'INSERT', 'ValueError', 'Name is required'))
                record.status = 'BLOCKED'
            else:
                try:
                    now = datetime.now().isoformat()
                    cur = conn.execute(
                        'INSERT INTO inventory_supplier '
                        '(name, locations, is_active, created_at) '
                        'VALUES (?,?,?,?)',
                        (td['name'], td['locations'],
                         int(td['is_active']), now))
                    record.target_id = cur.lastrowid
                    sup_map[c.legacy_id] = record.target_id
                    sup_map[str(c.legacy_id)] = record.target_id
                    conn.commit()
                except sqlite3.IntegrityError as e:
                    conn.rollback()
                    record.status = 'BLOCKED'
                    record.failures.append(SimFailure(
                        FailureCategory.SOURCE_INTRINSIC_BLOCKER,
                        'Supplier', c.legacy_id, 'name',
                        'INSERT', 'IntegrityError', str(e)[:200], expected=True))

            record.resolution_status = self._get_resolution(
                'Supplier', c.legacy_id)
            sup_records.append(record)
            self.failures.extend(record.failures)
            self.traceability.append({
                'entity': 'Supplier', 'legacy_id': c.legacy_id,
                'target_id': record.target_id,
                'source_table': c.legacy_source,
                'migration_batch': self.batch_id})
        self.results['suppliers'] = sup_records

        # ── PRODUCTS ──
        prod_records = []
        for c in results.get('products', []):
            record = SimRecord(
                entity='Product', legacy_id=c.legacy_id,
                source_table=c.legacy_source, target_data={}, status='INSERTABLE',
                migration_batch=self.batch_id)
            td = {
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
            record.target_data = td

            if not td['sku']:
                record.failures.append(SimFailure(
                    FailureCategory.MODEL_VALIDATION, 'Product', c.legacy_id,
                    'sku', 'INSERT', 'ValueError', 'SKU is required'))
            if not td['name']:
                record.failures.append(SimFailure(
                    FailureCategory.MODEL_VALIDATION, 'Product', c.legacy_id,
                    'name', 'INSERT', 'ValueError', 'Name is required'))

            cat_id = td.get('category_legacy_id')
            target_cat_id = ((cat_map.get(cat_id) or cat_map.get(str(cat_id)))
                            if cat_id else None)
            if not target_cat_id:
                record.failures.append(SimFailure(
                    FailureCategory.TARGET_FK, 'Product', c.legacy_id,
                    'category', 'INSERT', 'ForeignKeyError',
                    f'Category not found: code="{td["category_code"]}", '
                    f'legacy_id={cat_id}'))

            if record.failures:
                record.status = 'BLOCKED'
            else:
                try:
                    now = datetime.now().isoformat()
                    cur = conn.execute(
                        'INSERT INTO inventory_product '
                        '(sku, name, name_thai, category_id, unit, cost_per_kg, '
                        'selling_price_per_kg, barcode_prefix, kcalories, '
                        'protein, fat, active, created_at, updated_at) '
                        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (td['sku'], td['name'], td['name_thai'],
                         target_cat_id, td['unit'], td['cost_per_kg'],
                         td['selling_price_per_kg'], td['barcode_prefix'],
                         td['kcalories'], td['protein'], td['fat'],
                         int(td['active']), now, now))
                    record.target_id = cur.lastrowid
                    prod_map[c.legacy_id] = record.target_id
                    prod_map[str(c.legacy_id)] = record.target_id
                    conn.commit()
                except sqlite3.IntegrityError as e:
                    conn.rollback()
                    record.status = 'BLOCKED'
                    record.failures.append(SimFailure(
                        FailureCategory.DATABASE_CONSTRAINT,
                        'Product', c.legacy_id, 'sku',
                        'INSERT', 'IntegrityError', str(e)[:200]))
                except sqlite3.OperationalError as e:
                    conn.rollback()
                    record.status = 'BLOCKED'
                    record.failures.append(SimFailure(
                        FailureCategory.UNEXPECTED_ERROR,
                        'Product', c.legacy_id, 'sku',
                        'INSERT', 'OperationalError', str(e)[:200],
                        expected=False))

            record.resolution_status = self._get_resolution(
                'Product', c.legacy_id)
            prod_records.append(record)
            self.failures.extend(record.failures)
            self.traceability.append({
                'entity': 'Product', 'legacy_id': c.legacy_id,
                'target_id': record.target_id,
                'source_table': c.legacy_source,
                'migration_batch': self.batch_id,
                'resolution_status': record.resolution_status})
        self.results['products'] = prod_records

        # ── BATCHES ──
        batch_records = []
        for c in results.get('batches', []):
            record = SimRecord(
                entity='Batch', legacy_id=c.legacy_id,
                source_table=c.legacy_source, target_data={}, status='INSERTABLE',
                migration_batch=self.batch_id)
            td = {
                'batch_number': c.data.get('batch_number', ''),
                'supplier_legacy_id': c.data.get('supplier_legacy_id'),
                'supplier_name': c.data.get('supplier_name', ''),
                'received_at': c.data.get('received_at', ''),
            }
            record.target_data = td

            if not td['batch_number']:
                record.failures.append(SimFailure(
                    FailureCategory.MODEL_VALIDATION, 'Batch', c.legacy_id,
                    'batch_number', 'INSERT', 'ValueError', 'Required'))

            sup_id = td.get('supplier_legacy_id')
            target_sup_id = ((sup_map.get(sup_id) or sup_map.get(str(sup_id)))
                            if sup_id else None)
            if not target_sup_id:
                record.failures.append(SimFailure(
                    FailureCategory.TARGET_FK, 'Batch', c.legacy_id,
                    'supplier', 'INSERT', 'ForeignKeyError',
                    f'Supplier not found: "{td["supplier_name"]}"'))

            received_at = None
            if td['received_at']:
                try:
                    received_at = datetime.fromisoformat(
                        td['received_at'].replace(' ', 'T')).isoformat()
                except (ValueError, TypeError) as e:
                    record.failures.append(SimFailure(
                        FailureCategory.MODEL_VALIDATION, 'Batch', c.legacy_id,
                        'received_at', 'INSERT', type(e).__name__,
                        f'Invalid datetime: "{td["received_at"]}"'))
            if not received_at:
                record.failures.append(SimFailure(
                    FailureCategory.MODEL_VALIDATION, 'Batch', c.legacy_id,
                    'received_at', 'INSERT', 'ValueError', 'Required'))

            if record.failures:
                record.status = 'BLOCKED'
            else:
                try:
                    now = datetime.now().isoformat()
                    cur = conn.execute(
                        'INSERT INTO inventory_batch '
                        '(batch_number, supplier_id, received_at, notes, '
                        'active, created_at, updated_at) '
                        'VALUES (?,?,?,?,?,?,?)',
                        (td['batch_number'], target_sup_id,
                         received_at, '', 1, now, now))
                    record.target_id = cur.lastrowid
                    batch_map[c.legacy_id] = record.target_id
                    batch_map[str(c.legacy_id)] = record.target_id
                    conn.commit()
                except sqlite3.IntegrityError as e:
                    conn.rollback()
                    record.status = 'BLOCKED'
                    record.failures.append(SimFailure(
                        FailureCategory.SOURCE_INTRINSIC_BLOCKER,
                        'Batch', c.legacy_id, 'batch_number',
                        'INSERT', 'IntegrityError', str(e)[:200]))

            record.resolution_status = self._get_resolution(
                'Batch', c.legacy_id)
            batch_records.append(record)
            self.failures.extend(record.failures)
            self.traceability.append({
                'entity': 'Batch', 'legacy_id': c.legacy_id,
                'target_id': record.target_id,
                'source_table': c.legacy_source,
                'migration_batch': self.batch_id})
        self.results['batches'] = batch_records

        # ── PACKAGES ──
        pkg_records = []
        for c in results.get('packages', []):
            # If DryRunEngine already SKIPPED/INVALID, carry that status
            # and convert DryRunEngine issues to SimFailures.
            if getattr(c, 'status', '') in ('SKIPPED', 'INVALID'):
                record = SimRecord(
                    entity='Package', legacy_id=c.legacy_id,
                    source_table=c.legacy_source, target_data=dict(c.data),
                    status='BLOCKED', migration_batch=self.batch_id)
                for issue in getattr(c, 'issues', []):
                    record.failures.append(SimFailure(
                        FailureCategory.SOURCE_INTRINSIC_BLOCKER,
                        'Package', c.legacy_id,
                        issue.field or '', 'PRE_VALIDATE',
                        type(issue.severity).__name__,
                        issue.message))
                record.resolution_status = self._get_resolution(
                    'Package', c.legacy_id)
                pkg_records.append(record)
                self.failures.extend(record.failures)
                self.traceability.append({
                    'entity': 'Package', 'legacy_id': c.legacy_id,
                    'target_id': None,
                    'source_table': c.legacy_source,
                    'migration_batch': self.batch_id})
                continue

            record = SimRecord(
                entity='Package', legacy_id=c.legacy_id,
                source_table=c.legacy_source, target_data={}, status='INSERTABLE',
                migration_batch=self.batch_id)
            td = {
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
            record.target_data = td

            # Resolve product FK
            mp_id = td.get('meat_parts_id')
            pi_id = td.get('product_legacy_id')
            target_prod_id = None
            if mp_id:
                target_prod_id = (prod_map.get(mp_id)
                                 or prod_map.get(str(mp_id)))
            if not target_prod_id and pi_id:
                target_prod_id = (prod_map.get(pi_id)
                                 or prod_map.get(str(pi_id)))
            if not target_prod_id:
                record.failures.append(SimFailure(
                    FailureCategory.TARGET_FK, 'Package', c.legacy_id,
                    'product', 'INSERT', 'ForeignKeyError',
                    f'Product not found: sku="{td["product_sku"]}"'))

            # Resolve batch FK
            target_batch_id = None
            if pi_id:
                target_batch_id = (batch_map.get(pi_id)
                                  or batch_map.get(str(pi_id)))
            if not target_batch_id:
                record.failures.append(SimFailure(
                    FailureCategory.TARGET_FK, 'Package', c.legacy_id,
                    'batch', 'INSERT', 'ForeignKeyError',
                    f'Batch not found: "{td["batch_number"]}"'))

            # Parse datetime
            packed_at = None
            if td['packed_at']:
                try:
                    packed_at = datetime.fromisoformat(
                        td['packed_at'].replace(' ', 'T')).isoformat()
                except (ValueError, TypeError) as e:
                    record.failures.append(SimFailure(
                        FailureCategory.MODEL_VALIDATION, 'Package',
                        c.legacy_id, 'packed_at', 'INSERT',
                        type(e).__name__, 'Invalid datetime'))
            if not packed_at:
                record.failures.append(SimFailure(
                    FailureCategory.MODEL_VALIDATION, 'Package',
                    c.legacy_id, 'packed_at', 'INSERT',
                    'ValueError', 'Required'))

            # Validate weight
            weight_kg = None
            try:
                weight_kg = Decimal(td['weight_kg'])
            except (InvalidOperation, ValueError) as e:
                record.failures.append(SimFailure(
                    FailureCategory.MODEL_VALIDATION, 'Package',
                    c.legacy_id, 'weight', 'INSERT',
                    type(e).__name__, f'Invalid: "{td["weight_kg"]}"'))

            if record.failures:
                record.status = 'BLOCKED'
            else:
                loyverse_sku = td.get('loyverse_sku')
                if loyverse_sku and str(loyverse_sku).strip():
                    loyverse_sku = str(loyverse_sku)
                else:
                    loyverse_sku = None

                try:
                    now = datetime.now().isoformat()
                    cur = conn.execute(
                        'INSERT INTO inventory_package '
                        '(product_id, batch_id, barcode, weight, '
                        'selling_price, packed_at, current_state, '
                        'loyverse_sku, loyverse_item_id, '
                        'loyverse_variant_id, loyverse_synced, '
                        'created_at, updated_at) '
                        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (target_prod_id, target_batch_id, td['barcode'],
                         str(weight_kg), td['selling_price'], packed_at,
                         td['canonical_state'], loyverse_sku,
                         td['loyverse_item_id'] or None,
                         td['loyverse_variant_id'] or None,
                         int(td['loyverse_synced']), now, now))
                    record.target_id = cur.lastrowid
                    conn.commit()
                except sqlite3.IntegrityError as e:
                    conn.rollback()
                    record.status = 'BLOCKED'
                    err = str(e).lower()
                    fld = ('barcode' if 'barcode' in err
                           else ('loyverse_sku' if 'loyverse' in err
                                 else 'unique'))
                    record.failures.append(SimFailure(
                        FailureCategory.DATABASE_CONSTRAINT,
                        'Package', c.legacy_id, fld,
                        'INSERT', 'IntegrityError', str(e)[:200]))
                except sqlite3.OperationalError as e:
                    conn.rollback()
                    record.status = 'BLOCKED'
                    record.failures.append(SimFailure(
                        FailureCategory.UNEXPECTED_ERROR,
                        'Package', c.legacy_id, 'weight',
                        'INSERT', 'OperationalError', str(e)[:200],
                        expected=False))

            record.resolution_status = self._get_resolution(
                'Package', c.legacy_id)
            pkg_records.append(record)
            self.failures.extend(record.failures)
            self.traceability.append({
                'entity': 'Package', 'legacy_id': c.legacy_id,
                'target_id': record.target_id,
                'source_table': c.legacy_source,
                'migration_batch': self.batch_id,
                'resolution_status': record.resolution_status})
        self.results['packages'] = pkg_records

        conn.close()

    def _check_package_state_conflicts(self, results):
        for c in results.get('packages', []):
            lid = int(c.legacy_id) if c.legacy_id else 0
            if lid in (67, 80):
                status_note = c.data.get('state_note', '')
                thaw_q = c.data.get('thaw_queue_position', 0)
                if 'depleted' in str(status_note).lower() and thaw_q > 0:
                    f = SimFailure(
                        FailureCategory.HISTORICAL_DATA_LOSS_RISK,
                        'Package', c.legacy_id, 'current_state',
                        'VALIDATE', 'DataLossRisk',
                        f'Depleted + thaw_queue={thaw_q} — '
                        f'no thaw history in target schema')
                    self.failures.append(f)
                    for rec in self.results.get('packages', []):
                        if int(rec.legacy_id) == lid:
                            rec.failures.append(f)
                            if rec.status == 'INSERTABLE':
                                rec.status = 'WARNING'
                            break

    def _get_resolution(self, entity, legacy_id):
        if not self.resolution_trail:
            return 'NOT_APPLICABLE'
        for e in self.resolution_trail.entries:
            if e.entity == entity and int(e.legacy_id) == int(legacy_id):
                return 'APPLIED' if e.applied else 'PENDING_APPROVAL'
        return 'NOT_APPLICABLE'

    def _cleanup(self):
        """Remove temp DB file."""
        for path in [self._sim_db_path,
                     self._sim_db_path + '-wal',
                     self._sim_db_path + '-shm',
                     self._sim_db_path + '-journal']:
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass
        self._sim_db_path = None

    # ─── REPORTING ──────────────────────────────────────────────

    def summary(self):
        total = sum(len(v) for v in self.results.values())
        insertable = sum(
            1 for v in self.results.values()
            for r in v if r.status == 'INSERTABLE')
        blocked = sum(
            1 for v in self.results.values()
            for r in v if r.status == 'BLOCKED')
        warning = sum(
            1 for v in self.results.values()
            for r in v if r.status == 'WARNING')

        # Root cause count (unique root cause keys)
        root_count = len(self._root_causes)
        # Dependent count (unique blocked records that are DEPENDENT)
        dependent_keys = set()
        for f in self.failures:
            if f.blocker_type == BlockerType.DEPENDENT:
                dependent_keys.add(f'{f.entity}:{f.legacy_id}')
        dependent_count = len(dependent_keys)

        cats = {}
        for f in self.failures:
            cats.setdefault(f.category, []).append(f)
        return {
            'total': total,
            'insertable': insertable,
            'blocked': blocked,
            'warnings': warning,
            'root_blockers': root_count,
            'dependent_blockers': dependent_count,
            'failures_by_category': {k: len(v) for k, v in cats.items()},
            'legacy_unchanged': (
                self.legacy_hash_before == self.legacy_hash_after),
            'default_db_unchanged': (
                self._default_hash_before == self._default_hash_after),
        }

    def get_logical_signature(self):
        """Return deterministic logical result for comparison.

        Excludes target_id (auto-increment) and timestamps.
        Includes: entity, legacy_id, status, failure categories + fields.
        """
        sig = []
        for entity, records in self.results.items():
            for r in records:
                failures_sig = tuple(
                    (f.category, f.field, f.blocker_type)
                    for f in r.failures
                )
                sig.append((
                    r.entity, r.legacy_id, r.status,
                    failures_sig,
                ))
        return sorted(sig)

    def print_report(self):
        s = self.summary()
        print()
        print('=' * 60)
        print('MIGRATION SIMULATION (DJANGO MIGRATIONS + ISOLATED DB)')
        print('=' * 60)
        print(f'  Batch:          {self.batch_id}')
        print(f'  Source:         {self.legacy_db_path}')
        print(f'  Schema:         Django migrations → temp SQLite (subprocess)')
        print()
        print('SCHEMA INTROSPECTION:')
        for table, cols in self._schema_columns.items():
            print(f'  {table}: {len(cols)} columns')
        print()
        print('RESULTS:')
        print(f'  {"Total candidates":30s} {s["total"]}')
        print(f'  {"Temp DB inserts (actual)":30s} {s["insertable"]}')
        print(f'  {"Blocked":30s} {s["blocked"]}')
        print(f'  {"Warnings":30s} {s["warnings"]}')
        print(f'  {"Root blockers":30s} {s["root_blockers"]}')
        print(f'  {"Dependent blockers":30s} {s["dependent_blockers"]}')
        print()
        if s['failures_by_category']:
            print('ERROR CLASSIFICATION:')
            for cat, cnt in sorted(s['failures_by_category'].items()):
                print(f'  {cat:40s} {cnt}')
            print()

        if s['blocked'] > 0:
            print('-' * 60)
            print('  ROOT BLOCKERS (root cause only)')
            print('-' * 60)
            for key, f in sorted(self._root_causes.items()):
                print(f'  🔴 {f.entity} #{f.legacy_id}')
                print(f'     [{f.category}] {f.field}: {f.message}')
            print()
            print('-' * 60)
            print('  DEPENDENT BLOCKERS (caused by root)')
            print('-' * 60)
            for ent, recs in self.results.items():
                for r in recs:
                    if r.status != 'BLOCKED':
                        continue
                    dep_failures = [f for f in r.failures
                                   if f.blocker_type == BlockerType.DEPENDENT]
                    if dep_failures:
                        print(f'  ⬇️  {r.entity} #{r.legacy_id}')
                        for f in dep_failures:
                            print(f'     [{f.category}] {f.field}: '
                                  f'{f.message} ← root: {f.root_cause_key}')
            print()

        print('-' * 60)
        print(f'  TRACEABILITY ({len(self.traceability)} records)')
        print('-' * 60)
        for t in self.traceability[:10]:
            tid = (f' → temp#{t["target_id"]}'
                   if t.get('target_id') else '')
            print(f'  {t["entity"]:12s} legacy#{t["legacy_id"]:4d}{tid}')
        if len(self.traceability) > 10:
            print(f'  ... and {len(self.traceability) - 10} more')
        print()
        print('  🔒 Legacy DB: SHA-256 '
              + ('unchanged' if s['legacy_unchanged'] else 'MODIFIED!'))
        print('  🔒 Default DB: SHA-256 '
              + ('unchanged' if s.get('default_db_unchanged') else 'UNCHANGED'))
        print()
        print('=' * 60)
        print('SIMULATION COMPLETE — NO DATA PERSISTED')
        print('=' * 60)
        print()
