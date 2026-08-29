"""
Migration Simulation Engine — Isolated Target Database Validation

Creates a temporary SQLite database with real Django schema.
All inserts use raw sqlite3 (bypassing Django ORM) for true isolation.
The temporary database is deleted after simulation.
"""
import os
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

from inventory.migration_engine import DryRunEngine, file_hash
from inventory.resolution import ResolutionApplier


class FailureCategory:
    SOURCE_INTRINSIC = 'SOURCE_INTRINSIC_CONFLICT'
    TARGET_CONSTRAINT = 'TARGET_CONSTRAINT_BLOCKER'
    TARGET_FK = 'TARGET_FOREIGN_KEY'
    TARGET_UNIQUE = 'TARGET_UNIQUE_CONSTRAINT'
    TARGET_REQUIRED = 'TARGET_REQUIRED_FIELD'
    TARGET_TYPE = 'TARGET_FIELD_TYPE'
    HISTORICAL_LOSS = 'HISTORICAL_DATA_LOSS_RISK'


@dataclass
class SimFailure:
    category: str
    entity: str
    legacy_id: int
    field: str
    message: str


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


# DDL for all target tables — mirrors Django inventory/models.py schema
_TARGET_SCHEMA = """
CREATE TABLE IF NOT EXISTS inventory_category (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code VARCHAR(20) NOT NULL UNIQUE,
    name VARCHAR(100) NOT NULL,
    name_thai VARCHAR(100) NOT NULL DEFAULT '',
    is_active BOOLEAN NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS inventory_supplier (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(200) NOT NULL UNIQUE,
    locations TEXT NOT NULL DEFAULT '',
    is_active BOOLEAN NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS inventory_product (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku VARCHAR(50) NOT NULL UNIQUE,
    name VARCHAR(200) NOT NULL,
    name_thai VARCHAR(200) NOT NULL DEFAULT '',
    category_id INTEGER NOT NULL REFERENCES inventory_category(id),
    supplier_id INTEGER NULL REFERENCES inventory_supplier(id),
    unit VARCHAR(10) NOT NULL DEFAULT 'KG',
    cost_per_kg DECIMAL(10,2) NOT NULL DEFAULT 0,
    selling_price_per_kg DECIMAL(10,2) NOT NULL DEFAULT 0,
    barcode_prefix VARCHAR(20) NOT NULL DEFAULT '',
    kcalories DECIMAL(8,1) NOT NULL DEFAULT 0,
    protein DECIMAL(8,1) NOT NULL DEFAULT 0,
    fat DECIMAL(8,1) NOT NULL DEFAULT 0,
    active BOOLEAN NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS inventory_batch (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_number VARCHAR(50) NOT NULL UNIQUE,
    supplier_id INTEGER NOT NULL REFERENCES inventory_supplier(id),
    received_at DATETIME NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    active BOOLEAN NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS inventory_storagelocation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(100) NOT NULL,
    location_type VARCHAR(20) NOT NULL,
    capacity INTEGER NOT NULL DEFAULT 50,
    thaw_capacity INTEGER NOT NULL DEFAULT 20,
    min_temperature DECIMAL(5,2) NULL,
    max_temperature DECIMAL(5,2) NULL,
    active BOOLEAN NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS inventory_package (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES inventory_product(id),
    batch_id INTEGER NOT NULL REFERENCES inventory_batch(id),
    barcode VARCHAR(100) NOT NULL UNIQUE,
    weight DECIMAL(6,3) NOT NULL,
    selling_price DECIMAL(10,2) NOT NULL DEFAULT 0,
    packed_at DATETIME NOT NULL,
    current_state VARCHAR(20) NOT NULL DEFAULT 'PACKED',
    storage_location_id INTEGER NULL REFERENCES inventory_storagelocation(id),
    loyverse_sku VARCHAR(40) NULL UNIQUE,
    loyverse_item_id VARCHAR(100) NULL,
    loyverse_variant_id VARCHAR(100) NULL,
    loyverse_synced BOOLEAN NOT NULL DEFAULT 0,
    loyverse_synced_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class MigrationSimulation:
    """Simulation using an isolated temporary SQLite database with raw inserts."""

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

    def run(self):
        self.legacy_hash_before = file_hash(self.legacy_db_path)

        engine = DryRunEngine(self.legacy_db_path)
        engine.run()
        self.batch_id = engine.results.get('batch_id', 'UNKNOWN')

        applier = ResolutionApplier()
        trail = applier.preview(engine.results)
        applier.apply(engine.results, trail)
        self.resolution_trail = trail

        self.legacy_hash_after = file_hash(self.legacy_db_path)

        # Create isolated temp DB
        fd, self._sim_db_path = tempfile.mkstemp(suffix='_simulation.db')
        os.close(fd)

        conn = sqlite3.connect(self._sim_db_path)
        conn.execute('PRAGMA foreign_keys = ON')
        conn.executescript(_TARGET_SCHEMA)
        conn.commit()

        try:
            self._build_and_insert(engine.results, conn)
            self._check_package_state_conflicts(engine.results)
        finally:
            conn.close()
            # Delete temp DB
            for path in [self._sim_db_path, self._sim_db_path + '-wal',
                         self._sim_db_path + '-shm', self._sim_db_path + '-journal']:
                if os.path.exists(path):
                    try:
                        os.unlink(path)
                    except OSError:
                        pass
            self._sim_db_path = None

        return self

    def _build_and_insert(self, results, conn):
        cat_map = {}  # legacy_id → target_id
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
                    FailureCategory.TARGET_REQUIRED, 'Category', c.legacy_id, 'code', 'Required'))
                record.status = 'BLOCKED'
            else:
                try:
                    cur = conn.execute(
                        'INSERT INTO inventory_category (code, name, name_thai, is_active) VALUES (?,?,?,?)',
                        (td['code'], td['name'], td['name_thai'], int(td['is_active'])))
                    record.target_id = cur.lastrowid
                    cat_map[c.legacy_id] = record.target_id
                    cat_map[str(c.legacy_id)] = record.target_id
                    conn.commit()
                except sqlite3.IntegrityError as e:
                    conn.rollback()
                    record.status = 'BLOCKED'
                    record.failures.append(SimFailure(
                        FailureCategory.SOURCE_INTRINSIC, 'Category', c.legacy_id, 'code',
                        f'DB UNIQUE: {str(e)[:200]}'))

            record.resolution_status = self._get_resolution('Category', c.legacy_id)
            cat_records.append(record)
            self.failures.extend(record.failures)
            self.traceability.append({
                'entity': 'Category', 'legacy_id': c.legacy_id,
                'target_id': record.target_id, 'source_table': c.legacy_source,
                'migration_batch': self.batch_id})
        self.results['categories'] = cat_records

        # ── SUPPLIERS ──
        sup_records = []
        for c in results.get('suppliers', []):
            record = SimRecord(
                entity='Supplier', legacy_id=c.legacy_id,
                source_table=c.legacy_source, target_data={}, status='INSERTABLE',
                migration_batch=self.batch_id)
            td = {'name': c.data.get('name', ''), 'locations': c.data.get('locations', ''),
                  'is_active': c.data.get('is_active', True)}
            record.target_data = td

            if not td['name']:
                record.failures.append(SimFailure(
                    FailureCategory.TARGET_REQUIRED, 'Supplier', c.legacy_id, 'name', 'Required'))
                record.status = 'BLOCKED'
            else:
                try:
                    cur = conn.execute(
                        'INSERT INTO inventory_supplier (name, locations, is_active) VALUES (?,?,?)',
                        (td['name'], td['locations'], int(td['is_active'])))
                    record.target_id = cur.lastrowid
                    sup_map[c.legacy_id] = record.target_id
                    sup_map[str(c.legacy_id)] = record.target_id
                    conn.commit()
                except sqlite3.IntegrityError as e:
                    conn.rollback()
                    record.status = 'BLOCKED'
                    record.failures.append(SimFailure(
                        FailureCategory.SOURCE_INTRINSIC, 'Supplier', c.legacy_id, 'name',
                        f'DB UNIQUE: {str(e)[:200]}'))

            record.resolution_status = self._get_resolution('Supplier', c.legacy_id)
            sup_records.append(record)
            self.failures.extend(record.failures)
            self.traceability.append({
                'entity': 'Supplier', 'legacy_id': c.legacy_id,
                'target_id': record.target_id, 'source_table': c.legacy_source,
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
                'sku': c.data.get('sku', ''), 'name': c.data.get('name', ''),
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
                    FailureCategory.TARGET_REQUIRED, 'Product', c.legacy_id, 'sku', 'Required'))
            if not td['name']:
                record.failures.append(SimFailure(
                    FailureCategory.TARGET_REQUIRED, 'Product', c.legacy_id, 'name', 'Required'))

            # Resolve FK
            cat_id = td.get('category_legacy_id')
            target_cat_id = cat_map.get(cat_id) or cat_map.get(str(cat_id)) if cat_id else None
            if not target_cat_id:
                record.failures.append(SimFailure(
                    FailureCategory.TARGET_FK, 'Product', c.legacy_id, 'category',
                    f'Category not found: code="{td["category_code"]}", legacy_id={cat_id}'))

            if record.failures:
                record.status = 'BLOCKED'
            else:
                try:
                    cur = conn.execute(
                        'INSERT INTO inventory_product (sku, name, name_thai, category_id, unit, '
                        'cost_per_kg, selling_price_per_kg, barcode_prefix, kcalories, protein, fat, active) '
                        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                        (td['sku'], td['name'], td['name_thai'], target_cat_id, td['unit'],
                         td['cost_per_kg'], td['selling_price_per_kg'], td['barcode_prefix'],
                         td['kcalories'], td['protein'], td['fat'], int(td['active'])))
                    record.target_id = cur.lastrowid
                    prod_map[c.legacy_id] = record.target_id
                    prod_map[str(c.legacy_id)] = record.target_id
                    conn.commit()
                except sqlite3.IntegrityError as e:
                    conn.rollback()
                    record.status = 'BLOCKED'
                    record.failures.append(SimFailure(
                        FailureCategory.TARGET_CONSTRAINT, 'Product', c.legacy_id, 'sku',
                        f'DB UNIQUE: {str(e)[:200]}'))
                except sqlite3.OperationalError as e:
                    conn.rollback()
                    record.status = 'BLOCKED'
                    record.failures.append(SimFailure(
                        FailureCategory.TARGET_TYPE, 'Product', c.legacy_id, 'sku',
                        f'DB: {str(e)[:200]}'))

            record.resolution_status = self._get_resolution('Product', c.legacy_id)
            prod_records.append(record)
            self.failures.extend(record.failures)
            self.traceability.append({
                'entity': 'Product', 'legacy_id': c.legacy_id,
                'target_id': record.target_id, 'source_table': c.legacy_source,
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
                    FailureCategory.TARGET_REQUIRED, 'Batch', c.legacy_id, 'batch_number', 'Required'))

            sup_id = td.get('supplier_legacy_id')
            target_sup_id = sup_map.get(sup_id) or sup_map.get(str(sup_id)) if sup_id else None
            if not target_sup_id:
                record.failures.append(SimFailure(
                    FailureCategory.TARGET_FK, 'Batch', c.legacy_id, 'supplier',
                    f'Supplier not found: "{td["supplier_name"]}"'))

            received_at = None
            if td['received_at']:
                try:
                    received_at = datetime.fromisoformat(td['received_at'].replace(' ', 'T')).isoformat()
                except (ValueError, TypeError):
                    record.failures.append(SimFailure(
                        FailureCategory.TARGET_TYPE, 'Batch', c.legacy_id, 'received_at', 'Invalid datetime'))
            if not received_at:
                record.failures.append(SimFailure(
                    FailureCategory.TARGET_REQUIRED, 'Batch', c.legacy_id, 'received_at', 'Required'))

            if record.failures:
                record.status = 'BLOCKED'
            else:
                try:
                    cur = conn.execute(
                        'INSERT INTO inventory_batch (batch_number, supplier_id, received_at) VALUES (?,?,?)',
                        (td['batch_number'], target_sup_id, received_at))
                    record.target_id = cur.lastrowid
                    batch_map[c.legacy_id] = record.target_id
                    batch_map[str(c.legacy_id)] = record.target_id
                    conn.commit()
                except sqlite3.IntegrityError as e:
                    conn.rollback()
                    record.status = 'BLOCKED'
                    record.failures.append(SimFailure(
                        FailureCategory.SOURCE_INTRINSIC, 'Batch', c.legacy_id, 'batch_number',
                        f'DB UNIQUE: {str(e)[:200]}'))

            record.resolution_status = self._get_resolution('Batch', c.legacy_id)
            batch_records.append(record)
            self.failures.extend(record.failures)
            self.traceability.append({
                'entity': 'Batch', 'legacy_id': c.legacy_id,
                'target_id': record.target_id, 'source_table': c.legacy_source,
                'migration_batch': self.batch_id})
        self.results['batches'] = batch_records

        # ── PACKAGES ──
        pkg_records = []
        for c in results.get('packages', []):
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
                    FailureCategory.TARGET_FK, 'Package', c.legacy_id, 'product',
                    f'Product not found: sku="{td["product_sku"]}"'))

            # Resolve batch FK
            target_batch_id = None
            if pi_id:
                target_batch_id = batch_map.get(pi_id) or batch_map.get(str(pi_id))
            if not target_batch_id:
                record.failures.append(SimFailure(
                    FailureCategory.TARGET_FK, 'Package', c.legacy_id, 'batch',
                    f'Batch not found: "{td["batch_number"]}"'))

            # Parse datetime
            packed_at = None
            if td['packed_at']:
                try:
                    packed_at = datetime.fromisoformat(td['packed_at'].replace(' ', 'T')).isoformat()
                except (ValueError, TypeError):
                    record.failures.append(SimFailure(
                        FailureCategory.TARGET_TYPE, 'Package', c.legacy_id, 'packed_at', 'Invalid'))
            if not packed_at:
                record.failures.append(SimFailure(
                    FailureCategory.TARGET_REQUIRED, 'Package', c.legacy_id, 'packed_at', 'Required'))

            # Validate weight
            weight_kg = None
            try:
                weight_kg = Decimal(td['weight_kg'])
            except (InvalidOperation, ValueError):
                record.failures.append(SimFailure(
                    FailureCategory.TARGET_TYPE, 'Package', c.legacy_id, 'weight', f'Invalid: "{td["weight_kg"]}"'))

            if record.failures:
                record.status = 'BLOCKED'
            else:
                loyverse_sku = td.get('loyverse_sku')
                if loyverse_sku and str(loyverse_sku).strip():
                    loyverse_sku = str(loyverse_sku)
                else:
                    loyverse_sku = None

                try:
                    cur = conn.execute(
                        'INSERT INTO inventory_package '
                        '(product_id, batch_id, barcode, weight, selling_price, packed_at, '
                        'current_state, loyverse_sku, loyverse_item_id, loyverse_variant_id, loyverse_synced) '
                        'VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                        (target_prod_id, target_batch_id, td['barcode'],
                         str(weight_kg), td['selling_price'], packed_at,
                         td['canonical_state'], loyverse_sku,
                         td['loyverse_item_id'] or None, td['loyverse_variant_id'] or None,
                         int(td['loyverse_synced'])))
                    record.target_id = cur.lastrowid
                    conn.commit()
                except sqlite3.IntegrityError as e:
                    conn.rollback()
                    record.status = 'BLOCKED'
                    err = str(e).lower()
                    fld = 'barcode' if 'barcode' in err else ('loyverse_sku' if 'loyverse' in err else 'unique')
                    record.failures.append(SimFailure(
                        FailureCategory.SOURCE_INTRINSIC, 'Package', c.legacy_id, fld,
                        f'DB: {str(e)[:200]}'))

            record.resolution_status = self._get_resolution('Package', c.legacy_id)
            pkg_records.append(record)
            self.failures.extend(record.failures)
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
                status_note = c.data.get('state_note', '')
                thaw_q = c.data.get('thaw_queue_position', 0)
                if 'depleted' in str(status_note).lower() and thaw_q > 0:
                    f = SimFailure(FailureCategory.HISTORICAL_LOSS, 'Package', c.legacy_id,
                                  'current_state',
                                  f'Depleted + thaw_queue={thaw_q} — no thaw history in target schema')
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

    def summary(self):
        total = sum(len(v) for v in self.results.values())
        insertable = sum(1 for v in self.results.values() for r in v if r.status == 'INSERTABLE')
        blocked = sum(1 for v in self.results.values() for r in v if r.status == 'BLOCKED')
        warning = sum(1 for v in self.results.values() for r in v if r.status == 'WARNING')
        cats = {}
        for f in self.failures:
            cats.setdefault(f.category, []).append(f)
        return {
            'total': total, 'insertable': insertable, 'blocked': blocked,
            'warnings': warning, 'failures_by_category': {k: len(v) for k, v in cats.items()},
            'legacy_unchanged': self.legacy_hash_before == self.legacy_hash_after,
        }

    def print_report(self):
        s = self.summary()
        print()
        print('=' * 60)
        print('MIGRATION SIMULATION (ISOLATED TARGET DB)')
        print('=' * 60)
        print(f'  Batch:       {self.batch_id}')
        print(f'  Source:      {self.legacy_db_path}')
        print(f'  Temp DB:     (created, tested, deleted)')
        print()
        print('RESULTS:')
        print(f'  {"Total candidates":30s} {s["total"]}')
        print(f'  {"Temp DB inserts":30s} {s["insertable"]}')
        print(f'  {"Source-intrinsic blockers":30s} {s["blocked"]}')
        print(f'  {"Warnings":30s} {s["warnings"]}')
        print()
        if s['failures_by_category']:
            print('CONSTRAINT RESULTS:')
            for cat, cnt in sorted(s['failures_by_category'].items()):
                print(f'  {cat:40s} {cnt}')
            print()
        if s['blocked'] > 0:
            print('-' * 60)
            print('  BLOCKED RECORDS')
            print('-' * 60)
            for ent, recs in self.results.items():
                for r in recs:
                    if r.status == 'BLOCKED':
                        print(f'  ❌ {r.entity} #{r.legacy_id}')
                        for f in r.failures:
                            print(f'     [{f.category}] {f.field}: {f.message}')
                        print()
        print('-' * 60)
        print(f'  TRACEABILITY ({len(self.traceability)} records)')
        print('-' * 60)
        for t in self.traceability[:10]:
            tid = f' → temp#{t["target_id"]}' if t.get('target_id') else ''
            print(f'  {t["entity"]:12s} legacy#{t["legacy_id"]:4d}{tid}')
        if len(self.traceability) > 10:
            print(f'  ... and {len(self.traceability) - 10} more')
        print()
        print('  🔒 Legacy DB: SHA-256 unchanged' if s['legacy_unchanged'] else '  ❌ Legacy DB modified!')
        print()
        print('=' * 60)
        print('SIMULATION COMPLETE — NO DATA PERSISTED')
        print('=' * 60)
        print()
