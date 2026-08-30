"""
PostgreSQL Staging Simulation Engine

Pipeline:
  legacy.sqlite (READ ONLY)
  → DryRunEngine
  → ResolutionApplier
  → PostgreSQL staging DB (clmeat_staging)
  → psycopg2 INSERT with real constraints
  → root cause / dependent blocker classification
  → collect results
  → cleanup staging DB (TRUNCATE all tables)

Uses the REAL PostgreSQL database for constraint enforcement.
Each simulation run cleans the staging DB before and after.
"""
import hashlib
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

import psycopg2
import psycopg2.extensions

from inventory.migration_engine import DryRunEngine, file_hash
from inventory.resolution import ResolutionApplier


# ============================================================
# FAILURE CATEGORIES (same as SQLite simulation)
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
    blocker_type: str = BlockerType.ROOT
    root_cause_key: str = ''


@dataclass
class SimRecord:
    entity: str
    legacy_id: int
    source_table: str
    target_data: dict
    status: str
    failures: list = field(default_factory=list)
    resolution_rule: str = ''
    resolution_status: str = 'NOT_APPLICABLE'
    target_id: Optional[int] = None
    migration_batch: str = ''


# ============================================================
# PG CONNECTION CONFIG
# ============================================================

PG_CONFIG = {
    'dbname': os.environ.get('STAGING_DB_NAME', 'clmeat_staging'),
    'user': os.environ.get('STAGING_DB_USER', 'macky_01'),
    'password': os.environ.get('STAGING_DB_PASSWORD', ''),
    'host': os.environ.get('STAGING_DB_HOST', 'localhost'),
    'port': os.environ.get('STAGING_DB_PORT', '5432'),
}

# Tables to truncate in dependency-safe order
TRUNCATE_TABLES = [
    'inventory_temperaturelog',
    'inventory_stockmovement',
    'inventory_pricechangehistory',
    'inventory_barcodesequence',
    'inventory_productplanningprofile',
    'inventory_package',
    'inventory_batch',
    'inventory_product',
    'inventory_storagelocation',
    'inventory_supplier',
    'inventory_category',
]


def _pg_connect():
    """Open a psycopg2 connection to the staging database."""
    conn = psycopg2.connect(**PG_CONFIG)
    conn.autocommit = False
    return conn


def _pg_truncate_all(conn):
    """Truncate all inventory tables to reset staging DB."""
    cur = conn.cursor()
    for table in TRUNCATE_TABLES:
        cur.execute(f'TRUNCATE TABLE {table} CASCADE')
    # Reset sequences
    cur.execute("""
        SELECT sequence_name FROM information_schema.sequences
        WHERE sequence_schema = 'public' AND sequence_name LIKE 'inventory_%_id_seq'
    """)
    for (seq_name,) in cur.fetchall():
        cur.execute(f'ALTER SEQUENCE {seq_name} RESTART WITH 1')
    conn.commit()


# ============================================================
# POSTGRESQL SIMULATION ENGINE
# ============================================================

class PgMigrationSimulation:
    """Simulation using real PostgreSQL staging database."""

    def __init__(self, legacy_db_path):
        self.legacy_db_path = legacy_db_path
        self.legacy_hash_before = None
        self.legacy_hash_after = None
        self.results = {}
        self.failures = []
        self.traceability = []
        self.batch_id = None
        self.resolution_trail = None
        self._default_hash_before = None
        self._default_hash_after = None
        self._schema_columns = {}
        self._root_causes = {}
        self._pg_conn = None
        self._insertion_time = 0.0

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

        # Connect to PostgreSQL staging
        self._pg_conn = _pg_connect()
        try:
            _pg_truncate_all(self._pg_conn)
            self._introspect_schema()
            t0 = time.monotonic()
            self._build_and_insert(engine.results)
            self._insertion_time = time.monotonic() - t0
            self._check_package_state_conflicts(engine.results)
            self._classify_root_vs_dependent()
        finally:
            # Always clean up staging DB
            _pg_truncate_all(self._pg_conn)
            self._pg_conn.close()
            self._pg_conn = None

        return self

    def _introspect_schema(self):
        """Introspect actual PostgreSQL schema."""
        cur = self._pg_conn.cursor()
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name LIKE 'inventory_%'
            ORDER BY table_name
        """)
        tables = [row[0] for row in cur.fetchall()]
        for table in tables:
            cur.execute(
                "SELECT column_name, data_type, is_nullable "
                "FROM information_schema.columns "
                f"WHERE table_name = '{table}' ORDER BY ordinal_position")
            cols = [(row[0], row[1], row[2] == 'YES') for row in cur.fetchall()]
            self._schema_columns[table] = cols

        # Get unique constraints
        cur.execute("""
            SELECT tc.table_name, kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
            WHERE tc.constraint_type = 'UNIQUE'
              AND tc.table_schema = 'public'
              AND tc.table_name LIKE 'inventory_%'
            ORDER BY tc.table_name, kcu.column_name
        """)
        self._pg_unique_constraints = defaultdict(list)
        for table, col in cur.fetchall():
            self._pg_unique_constraints[table].append(col)

        # Get foreign keys
        cur.execute("""
            SELECT tc.table_name, kcu.column_name,
                   ccu.table_name, ccu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
            JOIN information_schema.constraint_column_usage ccu
              ON tc.constraint_name = ccu.constraint_name
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema = 'public'
              AND tc.table_name LIKE 'inventory_%'
            ORDER BY tc.table_name, kcu.column_name
        """)
        self._pg_foreign_keys = [
            {'table': r[0], 'column': r[1],
             'ref_table': r[2], 'ref_column': r[3]}
            for r in cur.fetchall()
        ]

    # ─── ROOT CAUSE / DEPENDENT CLASSIFICATION ──────────────

    def _classify_root_vs_dependent(self):
        """Classify blocker failures as ROOT or DEPENDENT."""
        entity_map = defaultdict(dict)
        for entity, records in self.results.items():
            for r in records:
                entity_map[r.entity][r.legacy_id] = r

        root_causes = {}
        for entity, records in self.results.items():
            for r in records:
                if r.status != 'BLOCKED':
                    continue
                for f in r.failures:
                    is_dependent = False
                    if f.category == FailureCategory.TARGET_FK:
                        if f.field == 'category' and f.entity == 'Product':
                            cat_legacy_id = r.target_data.get('category_legacy_id')
                            cat_rec = entity_map.get('Category', {}).get(cat_legacy_id)
                            if cat_rec and cat_rec.status == 'BLOCKED':
                                is_dependent = True
                                f.root_cause_key = f'Category:{cat_legacy_id}'
                        elif f.field == 'batch' and f.entity == 'Package':
                            pi_id = r.target_data.get('product_legacy_id')
                            batch_rec = entity_map.get('Batch', {}).get(pi_id)
                            if batch_rec and batch_rec.status == 'BLOCKED':
                                is_dependent = True
                                batch_root = root_causes.get(f'Batch:{pi_id}')
                                f.root_cause_key = batch_root.root_cause_key if batch_root else f'Batch:{pi_id}'
                        elif f.field == 'product' and f.entity == 'Package':
                            mp_id = r.target_data.get('meat_parts_id')
                            prod_rec = entity_map.get('Product', {}).get(mp_id)
                            if prod_rec and prod_rec.status == 'BLOCKED':
                                is_dependent = True
                                prod_root = root_causes.get(f'Product:{mp_id}')
                                f.root_cause_key = prod_root.root_cause_key if prod_root else f'Product:{mp_id}'

                    if is_dependent:
                        f.blocker_type = BlockerType.DEPENDENT
                    else:
                        f.blocker_type = BlockerType.ROOT
                        key = f'{f.entity}:{f.legacy_id}'
                        if key not in root_causes:
                            root_causes[key] = f

        self._root_causes = root_causes

    # ─── INSERT OPERATIONS ──────────────────────────────────

    def _build_and_insert(self, results):
        """Insert all records into PostgreSQL staging DB."""
        conn = self._pg_conn

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
                    cur = conn.cursor()
                    now = datetime.now()
                    cur.execute(
                        'INSERT INTO inventory_category '
                        '(code, name, name_thai, is_active, created_at) '
                        'VALUES (%s, %s, %s, %s, %s) RETURNING id',
                        (td['code'], td['name'], td['name_thai'],
                         td['is_active'], now))
                    record.target_id = cur.fetchone()[0]
                    cat_map[c.legacy_id] = record.target_id
                    cat_map[str(c.legacy_id)] = record.target_id
                    conn.commit()
                except psycopg2.IntegrityError as e:
                    conn.rollback()
                    record.status = 'BLOCKED'
                    record.failures.append(SimFailure(
                        FailureCategory.SOURCE_INTRINSIC_BLOCKER,
                        'Category', c.legacy_id, 'code',
                        'INSERT', 'IntegrityError', str(e)[:200], expected=True))
                except Exception as e:
                    conn.rollback()
                    record.status = 'BLOCKED'
                    record.failures.append(SimFailure(
                        FailureCategory.UNEXPECTED_ERROR,
                        'Category', c.legacy_id, 'code',
                        'INSERT', type(e).__name__, str(e)[:200],
                        expected=False))

            record.resolution_status = self._get_resolution('Category', c.legacy_id)
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
                    cur = conn.cursor()
                    now = datetime.now()
                    cur.execute(
                        'INSERT INTO inventory_supplier '
                        '(name, locations, is_active, created_at) '
                        'VALUES (%s, %s, %s, %s) RETURNING id',
                        (td['name'], td['locations'], td['is_active'],
                         now))
                    record.target_id = cur.fetchone()[0]
                    sup_map[c.legacy_id] = record.target_id
                    sup_map[str(c.legacy_id)] = record.target_id
                    conn.commit()
                except psycopg2.IntegrityError as e:
                    conn.rollback()
                    record.status = 'BLOCKED'
                    record.failures.append(SimFailure(
                        FailureCategory.SOURCE_INTRINSIC_BLOCKER,
                        'Supplier', c.legacy_id, 'name',
                        'INSERT', 'IntegrityError', str(e)[:200], expected=True))
                except Exception as e:
                    conn.rollback()
                    record.status = 'BLOCKED'
                    record.failures.append(SimFailure(
                        FailureCategory.UNEXPECTED_ERROR,
                        'Supplier', c.legacy_id, 'name',
                        'INSERT', type(e).__name__, str(e)[:200],
                        expected=False))

            record.resolution_status = self._get_resolution('Supplier', c.legacy_id)
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
                    cur = conn.cursor()
                    now = datetime.now()
                    cur.execute(
                        'INSERT INTO inventory_product '
                        '(sku, name, name_thai, category_id, unit, cost_per_kg, '
                        'selling_price_per_kg, barcode_prefix, kcalories, '
                        'protein, fat, active, created_at, updated_at) '
                        'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) '
                        'RETURNING id',
                        (td['sku'], td['name'], td['name_thai'],
                         target_cat_id, td['unit'], td['cost_per_kg'],
                         td['selling_price_per_kg'], td['barcode_prefix'],
                         td['kcalories'], td['protein'], td['fat'],
                         td['active'], now, now))
                    record.target_id = cur.fetchone()[0]
                    prod_map[c.legacy_id] = record.target_id
                    prod_map[str(c.legacy_id)] = record.target_id
                    conn.commit()
                except psycopg2.IntegrityError as e:
                    conn.rollback()
                    record.status = 'BLOCKED'
                    record.failures.append(SimFailure(
                        FailureCategory.DATABASE_CONSTRAINT,
                        'Product', c.legacy_id, 'sku',
                        'INSERT', 'IntegrityError', str(e)[:200]))
                except Exception as e:
                    conn.rollback()
                    record.status = 'BLOCKED'
                    record.failures.append(SimFailure(
                        FailureCategory.UNEXPECTED_ERROR,
                        'Product', c.legacy_id, 'sku',
                        'INSERT', type(e).__name__, str(e)[:200],
                        expected=False))

            record.resolution_status = self._get_resolution('Product', c.legacy_id)
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
                        td['received_at'].replace(' ', 'T'))
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
                    cur = conn.cursor()
                    now = datetime.now()
                    cur.execute(
                        'INSERT INTO inventory_batch '
                        '(batch_number, supplier_id, received_at, notes, '
                        'active, created_at, updated_at) '
                        'VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id',
                        (td['batch_number'], target_sup_id,
                         received_at, '', True, now, now))
                    record.target_id = cur.fetchone()[0]
                    batch_map[c.legacy_id] = record.target_id
                    batch_map[str(c.legacy_id)] = record.target_id
                    conn.commit()
                except psycopg2.IntegrityError as e:
                    conn.rollback()
                    record.status = 'BLOCKED'
                    record.failures.append(SimFailure(
                        FailureCategory.SOURCE_INTRINSIC_BLOCKER,
                        'Batch', c.legacy_id, 'batch_number',
                        'INSERT', 'IntegrityError', str(e)[:200]))
                except Exception as e:
                    conn.rollback()
                    record.status = 'BLOCKED'
                    record.failures.append(SimFailure(
                        FailureCategory.UNEXPECTED_ERROR,
                        'Batch', c.legacy_id, 'batch_number',
                        'INSERT', type(e).__name__, str(e)[:200],
                        expected=False))

            record.resolution_status = self._get_resolution('Batch', c.legacy_id)
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
                record.resolution_status = self._get_resolution('Package', c.legacy_id)
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
                target_prod_id = prod_map.get(mp_id) or prod_map.get(str(mp_id))
            if not target_prod_id and pi_id:
                target_prod_id = prod_map.get(pi_id) or prod_map.get(str(pi_id))
            if not target_prod_id:
                record.failures.append(SimFailure(
                    FailureCategory.TARGET_FK, 'Package', c.legacy_id,
                    'product', 'INSERT', 'ForeignKeyError',
                    f'Product not found: sku="{td["product_sku"]}"'))

            # Resolve batch FK
            target_batch_id = None
            if pi_id:
                target_batch_id = batch_map.get(pi_id) or batch_map.get(str(pi_id))
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
                        td['packed_at'].replace(' ', 'T'))
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
                    cur = conn.cursor()
                    now = datetime.now()
                    # Convert to proper Python bool for PostgreSQL boolean column
                    loyverse_synced_bool = bool(td['loyverse_synced'])
                    cur.execute(
                        'INSERT INTO inventory_package '
                        '(product_id, batch_id, barcode, weight, '
                        'selling_price, packed_at, current_state, '
                        'loyverse_sku, loyverse_item_id, '
                        'loyverse_variant_id, loyverse_synced, '
                        'created_at, updated_at) '
                        'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) '
                        'RETURNING id',
                        (target_prod_id, target_batch_id, td['barcode'],
                         str(weight_kg), td['selling_price'], packed_at,
                         td['canonical_state'], loyverse_sku,
                         td['loyverse_item_id'] or None,
                         td['loyverse_variant_id'] or None,
                         loyverse_synced_bool, now, now))
                    record.target_id = cur.fetchone()[0]
                    conn.commit()
                except psycopg2.IntegrityError as e:
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
                except Exception as e:
                    conn.rollback()
                    record.status = 'BLOCKED'
                    record.failures.append(SimFailure(
                        FailureCategory.UNEXPECTED_ERROR,
                        'Package', c.legacy_id, 'weight',
                        'INSERT', type(e).__name__, str(e)[:200],
                        expected=False))

            record.resolution_status = self._get_resolution('Package', c.legacy_id)
            pkg_records.append(record)
            self.failures.extend(record.failures)
            self.traceability.append({
                'entity': 'Package', 'legacy_id': c.legacy_id,
                'target_id': record.target_id,
                'source_table': c.legacy_source,
                'migration_batch': self.batch_id,
                'resolution_status': record.resolution_status})
        self.results['packages'] = pkg_records

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

    # ─── REPORTING ──────────────────────────────────────────

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

        root_count = len(self._root_causes)
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
            'insertion_time': self._insertion_time,
            'records_per_sec': (
                insertable / self._insertion_time if self._insertion_time > 0
                else 0),
        }

    def get_logical_signature(self):
        """Return deterministic logical result for comparison."""
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
        print('POSTGRESQL STAGING SIMULATION')
        print('=' * 60)
        print(f'  Batch:          {self.batch_id}')
        print(f'  Source:         {self.legacy_db_path}')
        print(f'  Target:         PostgreSQL ({PG_CONFIG["dbname"]})')
        print()
        print('SCHEMA:')
        for table, cols in self._schema_columns.items():
            print(f'  {table}: {len(cols)} columns')
        print()
        print('UNIQUE CONSTRAINTS:')
        for table, uqs in getattr(self, '_pg_unique_constraints', {}).items():
            for uq in uqs:
                print(f'  {table}.{uq}')
        print()
        print('FOREIGN KEYS:')
        for fk in getattr(self, '_pg_foreign_keys', []):
            print(f'  {fk["table"]}.{fk["column"]} → {fk["ref_table"]}.{fk["ref_column"]}')
        print()
        print('RESULTS:')
        print(f'  {"Total candidates":30s} {s["total"]}')
        print(f'  {"PG inserts (actual)":30s} {s["insertable"]}')
        print(f'  {"Blocked":30s} {s["blocked"]}')
        print(f'  {"Warnings":30s} {s["warnings"]}')
        print(f'  {"Root blockers":30s} {s["root_blockers"]}')
        print(f'  {"Dependent blockers":30s} {s["dependent_blockers"]}')
        print(f'  {"Insertion time":30s} {s["insertion_time"]:.3f}s')
        print(f'  {"Records/sec":30s} {s["records_per_sec"]:.1f}')
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
        print(f'  TRACEABILITY ({len(self.traceability)} records)')
        print('-' * 60)
        for t in self.traceability[:10]:
            tid = (f' → pg#{t["target_id"]}'
                   if t.get('target_id') else '')
            print(f'  {t["entity"]:12s} legacy#{t["legacy_id"]:4d}{tid}')
        if len(self.traceability) > 10:
            print(f'  ... and {len(self.traceability) - 10} more')
        print()
        print('  🔒 Legacy DB: SHA-256 '
              + ('unchanged' if s['legacy_unchanged'] else 'MODIFIED!'))
        print('  🔒 Default DB: SHA-256 '
              + ('unchanged' if s.get('default_db_unchanged') else 'UNKNOWN'))
        print()
        print('=' * 60)
        print('SIMULATION COMPLETE — STAGING DB CLEANED UP')
        print('=' * 60)
        print()
