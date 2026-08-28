"""
Legacy Migration Dry-Run Engine

STRICTLY READ-ONLY.  Never writes to any database.

Transforms legacy data into migration candidates, validates them,
and generates deterministic reports.
"""
import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path


# ============================================================
# VALIDATION SEVERITY
# ============================================================

class Severity:
    INFO = 'INFO'
    WARNING = 'WARNING'
    ERROR = 'ERROR'


# ============================================================
# CANDIDATE STATUS
# ============================================================

class Status:
    VALID = 'VALID'
    WARNING = 'WARNING'
    INVALID = 'INVALID'
    SKIPPED = 'SKIPPED'


# ============================================================
# MIGRATION BATCH
# ============================================================

def make_batch_id():
    """Deterministic batch ID based on execution time."""
    return f"MIGRATION-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


# ============================================================
# LEGACY DATABASE ACCESS
# ============================================================

class LegacyDB:
    """Read-only access to the legacy SQLite database."""

    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.conn = None

    def open(self):
        if not self.db_path.exists():
            raise FileNotFoundError(f"Legacy database not found: {self.db_path}")
        uri = f"file:{self.db_path}?mode=ro"
        self.conn = sqlite3.connect(uri, uri=True)
        self.conn.row_factory = sqlite3.Row

    def close(self):
        if self.conn:
            self.conn.close()

    def table_count(self, table):
        cur = self.conn.cursor()
        try:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            return cur.fetchone()[0]
        except sqlite3.OperationalError:
            return 0

    def fetch_all(self, table):
        cur = self.conn.cursor()
        try:
            cur.execute(f"SELECT * FROM {table}")
            return [dict(row) for row in cur.fetchall()]
        except sqlite3.OperationalError:
            return []

    def source_counts(self):
        tables = [
            'stock_meat_category', 'stock_meat_supply_meat',
            'stock_meat_meat_parts', 'stock_meat_product_info',
            'stock_meat_product_list', 'stock_meat_freezerotation',
            'stock_meat_rotationschedule', 'stock_meat_workertask',
        ]
        return {t: self.table_count(t) for t in tables}


# ============================================================
# VALIDATION ISSUE
# ============================================================

class Issue:
    def __init__(self, severity, source, legacy_id, message, field=None):
        self.severity = severity
        self.source = source
        self.legacy_id = legacy_id
        self.message = message
        self.field = field

    def to_dict(self):
        d = {
            'severity': self.severity,
            'source': self.source,
            'legacy_id': self.legacy_id,
            'message': self.message,
        }
        if self.field:
            d['field'] = self.field
        return d


# ============================================================
# MIGRATION CANDIDATE
# ============================================================

class Candidate:
    def __init__(self, target_model, legacy_source, legacy_id, data, status=Status.VALID):
        self.target_model = target_model
        self.legacy_source = legacy_source
        self.legacy_id = legacy_id
        self.data = data
        self.status = status
        self.issues = []

    def add_issue(self, severity, message, field=None):
        self.issues.append(Issue(severity, self.legacy_source, self.legacy_id, message, field))
        if severity == Severity.ERROR and self.status not in (Status.INVALID, Status.SKIPPED):
            self.status = Status.INVALID
        elif severity == Severity.WARNING and self.status == Status.VALID:
            self.status = Status.WARNING

    def to_dict(self):
        return {
            'target_model': self.target_model,
            'legacy_source': self.legacy_source,
            'legacy_id': self.legacy_id,
            'status': self.status,
            'data': {k: str(v) if isinstance(v, (Decimal, datetime)) else v
                     for k, v in self.data.items()},
            'issues': [i.to_dict() for i in self.issues],
        }


# ============================================================
# MAPPING: CATEGORY
# ============================================================

def map_categories(rows, batch_id):
    candidates = []
    seen_codes = {}
    for row in rows:
        name = (row.get('name_type') or '').strip()
        legacy_id = row['ids']

        if not name:
            c = Candidate('Category', 'stock_meat_category', legacy_id, {'name': ''}, Status.SKIPPED)
            c.add_issue(Severity.ERROR, 'Category name is empty', 'name_type')
            candidates.append(c)
            continue

        if name.lower() in ('test', 'test ', ''):
            c = Candidate('Category', 'stock_meat_category', legacy_id, {'name': name}, Status.WARNING)
            c.add_issue(Severity.WARNING, f'Category name "{name}" appears to be test data', 'name_type')
            candidates.append(c)
            continue

        code = name[:20].upper().replace(' ', '_')
        if code in seen_codes:
            c = Candidate('Category', 'stock_meat_category', legacy_id, {'name': name, 'code': code}, Status.WARNING)
            c.add_issue(Severity.WARNING, f'Duplicate category code "{code}" (same as legacy #{seen_codes[code]})', 'code')
            candidates.append(c)
            continue

        seen_codes[code] = legacy_id
        data = {
            'code': code,
            'name': name,
            'name_thai': name,
            'is_active': True,
        }
        c = Candidate('Category', 'stock_meat_category', legacy_id, data)
        candidates.append(c)

    return candidates


# ============================================================
# MAPPING: SUPPLIER
# ============================================================

def map_suppliers(rows, batch_id):
    candidates = []
    seen_names = {}
    for row in rows:
        name = (row.get('name_place') or '').strip()
        locations = (row.get('locations') or '').strip()
        legacy_id = row['ids']

        if not name:
            c = Candidate('Supplier', 'stock_meat_supply_meat', legacy_id, {'name': ''}, Status.SKIPPED)
            c.add_issue(Severity.ERROR, 'Supplier name is empty', 'name_place')
            candidates.append(c)
            continue

        if name in seen_names:
            c = Candidate('Supplier', 'stock_meat_supply_meat', legacy_id, {'name': name}, Status.WARNING)
            c.add_issue(Severity.WARNING, f'Duplicate supplier name (same as legacy #{seen_names[name]})', 'name')
            candidates.append(c)
            continue

        seen_names[name] = legacy_id
        data = {
            'name': name,
            'locations': locations,
            'is_active': True,
        }
        c = Candidate('Supplier', 'stock_meat_supply_meat', legacy_id, data)
        candidates.append(c)

    return candidates


# ============================================================
# MAPPING: PRODUCT (from meat_parts)
# ============================================================

def _generate_sku(prefix_barcode):
    if prefix_barcode:
        return f"MP-{prefix_barcode}"
    return None


def map_products(rows, category_candidates, batch_id):
    candidates = []
    seen_skus = {}
    category_map = {}  # legacy category id → candidate
    for cc in category_candidates:
        if cc.status != Status.INVALID and 'code' in cc.data:
            category_map[cc.legacy_id] = cc
            category_map[str(cc.legacy_id)] = cc  # handle string keys from SQLite

    for row in rows:
        name = (row.get('name') or '').strip()
        legacy_id = row['id']
        category_id = row.get('category_id')
        prefix_barcode = (row.get('prefix_barcode') or '').strip()
        kcalories = row.get('kcalories') or 0
        protein = row.get('protent') or 0  # legacy typo
        fat = row.get('fat') or 0

        if not name:
            c = Candidate('Product', 'stock_meat_meat_parts', legacy_id, {'name': ''}, Status.SKIPPED)
            c.add_issue(Severity.ERROR, 'Product name is empty', 'name')
            candidates.append(c)
            continue

        if category_id is None or category_id not in category_map:
            c = Candidate('Product', 'stock_meat_meat_parts', legacy_id, {'name': name}, Status.SKIPPED)
            cat_status = 'missing' if category_id is None else f'invalid (id={category_id})'
            c.add_issue(Severity.ERROR, f'Category reference {cat_status}', 'category_id')
            candidates.append(c)
            continue

        sku = _generate_sku(prefix_barcode)
        if not sku:
            sku = f"MP-{legacy_id:04d}"

        if sku in seen_skus:
            c = Candidate('Product', 'stock_meat_meat_parts', legacy_id, {'name': name, 'sku': sku}, Status.WARNING)
            c.add_issue(Severity.WARNING, f'Duplicate SKU "{sku}" (same as legacy #{seen_skus[sku]})', 'sku')
            candidates.append(c)
            continue

        seen_skus[sku] = legacy_id
        data = {
            'sku': sku,
            'name': name,
            'name_thai': name,
            'category_code': category_map[category_id].data['code'],
            'category_legacy_id': category_id,
            'barcode_prefix': prefix_barcode,
            'kcalories': Decimal(str(kcalories)),
            'protein': Decimal(str(protein)),
            'fat': Decimal(str(fat)),
            'unit': 'KG',
            'active': True,
        }
        c = Candidate('Product', 'stock_meat_meat_parts', legacy_id, data)
        candidates.append(c)

    return candidates


# ============================================================
# MAPPING: BATCH (from Product_info)
# ============================================================

def map_batches(rows, product_candidates, supplier_candidates, batch_id):
    candidates = []
    product_map = {}  # meat_parts id → product candidate
    supplier_map = {}  # supply_meat ids → supplier candidate
    seen_batch_numbers = {}

    for pc in product_candidates:
        if pc.status not in (Status.INVALID, Status.SKIPPED) and 'sku' in pc.data:
            product_map[pc.legacy_id] = pc
            product_map[str(pc.legacy_id)] = pc
    for sc in supplier_candidates:
        if sc.status not in (Status.INVALID, Status.SKIPPED) and 'name' in sc.data:
            supplier_map[sc.legacy_id] = sc
            supplier_map[str(sc.legacy_id)] = sc

    for row in rows:
        legacy_id = row['id']
        meat_parts_id = row.get('name_id')
        supplier_id = row.get('import_from_id')
        lot_number = row.get('lot_number') or 1
        cost = row.get('cost')
        selling_price_per_kg = row.get('selling_price_per_kg') or 0
        weight = row.get('weight') or 0
        created_at = row.get('created_at') or ''

        # Resolve product
        if meat_parts_id is None or meat_parts_id not in product_map:
            c = Candidate('Batch', 'stock_meat_product_info', legacy_id, {}, Status.SKIPPED)
            ref = 'missing' if meat_parts_id is None else f'invalid (id={meat_parts_id})'
            c.add_issue(Severity.ERROR, f'Product reference {ref}', 'name_id')
            candidates.append(c)
            continue

        product_c = product_map[meat_parts_id]

        # Resolve supplier
        supplier_name = None
        supplier_legacy_id = None
        if supplier_id and supplier_id in supplier_map:
            supplier_name = supplier_map[supplier_id].data['name']
            supplier_legacy_id = supplier_id
        elif supplier_id:
            pass  # supplier not found — will issue warning

        # Generate batch number
        date_str = created_at[:10].replace('-', '') if created_at else '00000000'
        try:
            supplier_int = int(supplier_id) if supplier_id else 0
        except (TypeError, ValueError):
            supplier_int = 0
        try:
            lot_int = int(lot_number) if lot_number else 1
        except (TypeError, ValueError):
            lot_int = 1
        batch_number = f"B-{date_str}-{supplier_int:02d}-{lot_int:02d}"

        # Check duplicate batch number
        if batch_number in seen_batch_numbers:
            pass  # Intentional — same supplier+lot+date = same batch

        # Report weight=0.0
        weight_issue = None
        if weight == 0.0:
            weight_issue = 'Product_info.weight = 0.0 (not used for Package weight)'

        data = {
            'batch_number': batch_number,
            'product_sku': product_c.data['sku'],
            'product_legacy_id': meat_parts_id,
            'supplier_name': supplier_name or '(unknown)',
            'supplier_legacy_id': supplier_legacy_id,
            'lot_number': lot_int,
            'cost_per_kg': Decimal(str(cost)) if cost else Decimal('0'),
            'selling_price_per_kg': Decimal(str(selling_price_per_kg)),
            'received_at': created_at or None,
        }

        c = Candidate('Batch', 'stock_meat_product_info', legacy_id, data)

        if not supplier_id:
            c.add_issue(Severity.WARNING, 'No supplier reference', 'import_from_id')
        elif supplier_id not in supplier_map:
            c.add_issue(Severity.WARNING, f'Supplier reference invalid (id={supplier_id})', 'import_from_id')

        if weight_issue:
            c.add_issue(Severity.INFO, weight_issue, 'weight')

        if lot_int <= 0:
            c.add_issue(Severity.WARNING, f'Invalid lot_number: {lot_number}', 'lot_number')

        candidates.append(c)
        seen_batch_numbers[batch_number] = legacy_id

    return candidates


# ============================================================
# STATE MAPPING
# ============================================================

STORAGE_STATUS_MAP = {
    'frozen': None,    # Depends on thaw_queue_position
    'thawing': 'THAWING',
    'display': 'ON_DISPLAY',
    'depleted': 'COMPLETED',
}


def _map_storage_status(row):
    storage_status = (row.get('storage_status') or '').strip().lower()
    try:
        thaw_queue = int(row.get('thaw_queue_position') or 0)
    except (TypeError, ValueError):
        thaw_queue = 0

    if storage_status == 'frozen':
        if thaw_queue > 0:
            return 'THAW_QUEUED', f'frozen + thaw_queue_position={thaw_queue}'
        return 'FROZEN', 'frozen'

    if storage_status in STORAGE_STATUS_MAP and STORAGE_STATUS_MAP[storage_status]:
        return STORAGE_STATUS_MAP[storage_status], storage_status

    return None, storage_status


# ============================================================
# MAPPING: PACKAGE (from Product_list)
# ============================================================

def map_packages(rows, product_candidates, batch_candidates, batch_id):
    candidates = []
    product_map = {}  # meat_parts id → product candidate
    seen_barcodes = {}
    seen_loyverse_skus = {}

    for pc in product_candidates:
        if pc.status not in (Status.INVALID, Status.SKIPPED) and 'sku' in pc.data:
            product_map[pc.legacy_id] = pc
            product_map[str(pc.legacy_id)] = pc

    # Build batch lookup: (product_legacy_id, lot?) — simplified
    # In practice, we need to resolve product_info → product + batch
    # For now, group batch candidates by product_legacy_id
    batch_by_product = {}
    for bc in batch_candidates:
        if bc.status != Status.INVALID:
            prod_id = bc.data.get('product_legacy_id')
            if prod_id not in batch_by_product:
                batch_by_product[prod_id] = bc

    for row in rows:
        legacy_id = row['id']
        product_info_id = row.get('product_id')
        barcode = (row.get('barcode') or '').strip()
        weight_grams = row.get('weight') or 0
        selling_price = row.get('selling_price') or 0
        storage_status_raw = (row.get('storage_status') or '').strip()
        loyverse_sku = row.get('loyverse_sku')
        loyverse_item_id = row.get('loyverse_item_id')
        loyverse_variant_id = row.get('loyverse_variant_id')
        loyverse_synced = row.get('loyverse_synced') or False
        try:
            thaw_queue_position = int(row.get('thaw_queue_position') or 0)
        except (TypeError, ValueError):
            thaw_queue_position = 0
        mfg = row.get('mfg') or ''

        # Validate barcode
        if not barcode:
            c = Candidate('Package', 'stock_meat_product_list', legacy_id, {}, Status.SKIPPED)
            c.add_issue(Severity.ERROR, 'Empty barcode', 'barcode')
            candidates.append(c)
            continue

        if barcode in seen_barcodes:
            c = Candidate('Package', 'stock_meat_product_list', legacy_id, {'barcode': barcode}, Status.SKIPPED)
            c.add_issue(Severity.ERROR, f'Duplicate barcode (same as legacy #{seen_barcodes[barcode]})', 'barcode')
            candidates.append(c)
            continue

        # Resolve product
        product_candidate = None
        if product_info_id and product_info_id in product_map:
            product_candidate = product_map[product_info_id]

        if not product_candidate:
            c = Candidate('Package', 'stock_meat_product_list', legacy_id, {'barcode': barcode}, Status.SKIPPED)
            ref = 'missing' if product_info_id is None else f'invalid (id={product_info_id})'
            c.add_issue(Severity.ERROR, f'Product reference {ref}', 'product_id')
            candidates.append(c)
            continue

        # Weight conversion
        weight_kg = None
        try:
            weight_kg = Decimal(str(weight_grams)) / Decimal('1000')
        except (InvalidOperation, TypeError, ValueError):
            pass

        if weight_kg is None or weight_kg <= 0:
            c = Candidate('Package', 'stock_meat_product_list', legacy_id, {'barcode': barcode}, Status.SKIPPED)
            c.add_issue(Severity.ERROR, f'Invalid weight: {weight_grams}g', 'weight')
            candidates.append(c)
            continue

        if weight_kg > Decimal('100'):
            pass  # unusual but not necessarily wrong

        # Price conversion
        try:
            price = Decimal(str(selling_price))
        except (InvalidOperation, TypeError, ValueError):
            price = Decimal('0')

        # State mapping
        canonical_state, state_note = _map_storage_status(row)

        # Detect conflicting states
        state_issues = []
        if storage_status_raw == 'depleted' and thaw_queue_position > 0:
            state_issues.append(Severity.WARNING)
            state_note = f'depleted but thaw_queue_position={thaw_queue_position} (conflicting)'

        if canonical_state is None:
            c = Candidate('Package', 'stock_meat_product_list', legacy_id, {'barcode': barcode}, Status.SKIPPED)
            c.add_issue(Severity.ERROR, f'Unknown storage_status: "{storage_status_raw}"', 'storage_status')
            candidates.append(c)
            continue

        # Batch resolution
        batch_number = None
        if product_info_id:
            # Find the batch candidate for this product_info
            for bc in batch_candidates:
                if bc.legacy_id == product_info_id and bc.status != Status.INVALID:
                    batch_number = bc.data.get('batch_number')
                    break

        loyverse_sku_str = str(loyverse_sku) if loyverse_sku else None
        if loyverse_sku_str and loyverse_sku_str in seen_loyverse_skus:
            pass  # report as warning

        data = {
            'barcode': barcode,
            'product_sku': product_candidate.data['sku'],
            'product_legacy_id': product_info_id,
            'batch_number': batch_number or f'B-UNKNOWN-{product_info_id}',
            'weight_kg': weight_kg,
            'selling_price': price,
            'canonical_state': canonical_state,
            'state_note': state_note,
            'loyverse_sku': loyverse_sku_str,
            'loyverse_item_id': loyverse_item_id,
            'loyverse_variant_id': loyverse_variant_id,
            'loyverse_synced': loyverse_synced,
            'packed_at': mfg or None,
            'thaw_queue_position': thaw_queue_position,
        }

        c = Candidate('Package', 'stock_meat_product_list', legacy_id, data)

        # State conflict warning
        if storage_status_raw == 'depleted' and thaw_queue_position > 0:
            c.add_issue(Severity.WARNING,
                       f'storage_status=depleted but thaw_queue_position={thaw_queue_position} (inconsistent)',
                       'storage_status')

        if price < 0:
            c.add_issue(Severity.WARNING, f'Negative selling_price: {selling_price}', 'selling_price')

        # Loyverse duplicate
        if loyverse_sku_str and loyverse_sku_str in seen_loyverse_skus:
            c.add_issue(Severity.WARNING,
                       f'Duplicate loyverse_sku (same as legacy #{seen_loyverse_skus[loyverse_sku_str]})',
                       'loyverse_sku')

        candidates.append(c)
        seen_barcodes[barcode] = legacy_id
        if loyverse_sku_str:
            seen_loyverse_skus[loyverse_sku_str] = legacy_id

    return candidates


# ============================================================
# DRY-RUN ENGINE
# ============================================================

class DryRunEngine:
    """Main dry-run engine — read-only by design."""

    def __init__(self, legacy_db_path):
        self.legacy_db_path = legacy_db_path
        self.batch_id = make_batch_id()
        self.db = LegacyDB(legacy_db_path)
        self.results = {}

    def run(self):
        self.db.open()
        try:
            source_counts = self.db.source_counts()
            self.results['source_counts'] = source_counts
            self.results['batch_id'] = self.batch_id
            self.results['source'] = str(self.legacy_db_path)

            # Extract
            categories_raw = self.db.fetch_all('stock_meat_category')
            suppliers_raw = self.db.fetch_all('stock_meat_supply_meat')
            products_raw = self.db.fetch_all('stock_meat_meat_parts')
            batches_raw = self.db.fetch_all('stock_meat_product_info')
            packages_raw = self.db.fetch_all('stock_meat_product_list')

            # Map
            cat_candidates = map_categories(categories_raw, self.batch_id)
            sup_candidates = map_suppliers(suppliers_raw, self.batch_id)
            prod_candidates = map_products(products_raw, cat_candidates, self.batch_id)
            batch_candidates = map_batches(batches_raw, prod_candidates, sup_candidates, self.batch_id)
            pkg_candidates = map_packages(packages_raw, prod_candidates, batch_candidates, self.batch_id)

            self.results['categories'] = cat_candidates
            self.results['suppliers'] = sup_candidates
            self.results['products'] = prod_candidates
            self.results['batches'] = batch_candidates
            self.results['packages'] = pkg_candidates

            # Summary
            all_candidates = cat_candidates + sup_candidates + prod_candidates + batch_candidates + pkg_candidates
            summary = {
                'total': len(all_candidates),
                'valid': sum(1 for c in all_candidates if c.status == Status.VALID),
                'warning': sum(1 for c in all_candidates if c.status == Status.WARNING),
                'invalid': sum(1 for c in all_candidates if c.status == Status.INVALID),
                'skipped': sum(1 for c in all_candidates if c.status == Status.SKIPPED),
            }
            self.results['summary'] = summary

            # Collect issues
            all_issues = []
            for c in all_candidates:
                all_issues.extend(c.issues)
            self.results['issues'] = all_issues

        finally:
            self.db.close()

    def print_report(self):
        s = self.results.get('summary', {})
        sc = self.results.get('source_counts', {})

        print()
        print("=" * 60)
        print("LEGACY MIGRATION DRY-RUN REPORT")
        print("=" * 60)
        print(f"  Migration Batch:  {self.results.get('batch_id', '?')}")
        print(f"  Source Database:  {self.results.get('source', '?')}")
        print()
        print("SOURCE RECORD COUNTS:")
        for table, count in sc.items():
            label = table.replace('stock_meat_', '')
            print(f"  {label:25s} {count}")
        print()
        print("CANDIDATE SUMMARY:")
        print(f"  {'Total':25s} {s.get('total', 0)}")
        print(f"  {'Valid':25s} {s.get('valid', 0)}")
        print(f"  {'Warning':25s} {s.get('warning', 0)}")
        print(f"  {'Invalid':25s} {s.get('invalid', 0)}")
        print(f"  {'Skipped':25s} {s.get('skipped', 0)}")
        print()

        for model_name in ['categories', 'suppliers', 'products', 'batches', 'packages']:
            candidates = self.results.get(model_name, [])
            print("-" * 60)
            print(f"  {model_name.upper()} ({len(candidates)} candidates)")
            print("-" * 60)
            for c in candidates:
                icon = {'VALID': '✅', 'WARNING': '⚠️', 'INVALID': '❌', 'SKIPPED': '⏭️'}.get(c.status, '?')
                label = c.data.get('name') or c.data.get('sku') or c.data.get('barcode') or c.data.get('batch_number') or '?'
                print(f"  {icon} #{c.legacy_id:4d} {label}")
                for issue in c.issues:
                    sev_icon = {'ERROR': '❌', 'WARNING': '⚠️', 'INFO': 'ℹ️'}.get(issue.severity, '?')
                    print(f"      {sev_icon} [{issue.severity}] {issue.message}")
            print()

        # Issue summary
        issues = self.results.get('issues', [])
        errors = [i for i in issues if i.severity == Severity.ERROR]
        warnings = [i for i in issues if i.severity == Severity.WARNING]

        if errors or warnings:
            print("=" * 60)
            print("ISSUES REQUIRING ATTENTION")
            print("=" * 60)
            for i in errors:
                print(f"  ❌ [{i.source} #{i.legacy_id}] {i.message}")
            for i in warnings:
                print(f"  ⚠️  [{i.source} #{i.legacy_id}] {i.message}")
            print()

        print("=" * 60)
        print("DRY-RUN COMPLETE — NO DATA WAS WRITTEN")
        print("=" * 60)
        print()

    def export_json(self, path):
        output = {
            'migration_batch': self.results.get('batch_id'),
            'source': self.results.get('source'),
            'source_counts': self.results.get('source_counts'),
            'summary': self.results.get('summary'),
            'categories': [c.to_dict() for c in self.results.get('categories', [])],
            'suppliers': [c.to_dict() for c in self.results.get('suppliers', [])],
            'products': [c.to_dict() for c in self.results.get('products', [])],
            'batches': [c.to_dict() for c in self.results.get('batches', [])],
            'packages': [c.to_dict() for c in self.results.get('packages', [])],
            'issues': [i.to_dict() for i in self.results.get('issues', [])],
        }
        Path(path).write_text(json.dumps(output, indent=2, default=str))
