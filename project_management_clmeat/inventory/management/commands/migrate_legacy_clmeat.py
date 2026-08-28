"""
Management command: migrate_legacy_clmeat

Performs dry-run or actual migration of legacy CL.MEAT data.

Usage:
    python manage.py migrate_legacy_clmeat --dry-run
    python manage.py migrate_legacy_clmeat --execute
    python manage.py migrate_legacy_clmeat --dry-run --legacy-db path/to/db.sqlite3

The legacy database is expected at: database_clmeat_main/db.sqlite3
"""
import os
import sqlite3
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import transaction

from inventory.models import (
    Product, Batch, Package, PackageState,
    BarcodeSequence, PriceChangeHistory
)


class Command(BaseCommand):
    help = 'Migrate data from legacy CL.MEAT database (dry-run or execute)'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            default=True,
            help='Show what would be migrated without making changes (default)'
        )
        parser.add_argument(
            '--execute',
            action='store_true',
            help='Actually perform the migration'
        )
        parser.add_argument(
            '--legacy-db',
            type=str,
            default=None,
            help='Path to legacy SQLite database (default: database_clmeat_main/db.sqlite3)'
        )
    
    def handle(self, *args, **options):
        dry_run = not options['execute']
        
        # Find legacy database
        legacy_db = options.get('legacy_db')
        if not legacy_db:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))
            )))
            legacy_db = os.path.join(project_root, 'database_clmeat_main', 'db.sqlite3')
        
        if not os.path.exists(legacy_db):
            self.stderr.write(self.style.ERROR(
                f'Legacy database not found: {legacy_db}'
            ))
            return
        
        self.stdout.write(self.style.WARNING(
            f'Legacy database: {legacy_db}'
        ))
        
        if dry_run:
            self.stdout.write(self.style.WARNING(
                '=== DRY RUN MODE — No changes will be made ==='
            ))
        else:
            self.stdout.write(self.style.ERROR(
                '=== EXECUTE MODE — Changes WILL be written to database ==='
            ))
        
        # Connect to legacy DB
        conn = sqlite3.connect(legacy_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # ============================================================
        # SCAN LEGACY DATA
        # ============================================================
        
        report = {
            'categories': [],
            'suppliers': [],
            'meat_parts': [],
            'product_info': [],
            'product_list': [],
            'rotation_schedules': [],
            'worker_tasks': [],
            'conflicts': [],
            'warnings': [],
        }
        
        # Categories
        try:
            cursor.execute('SELECT ids, name_type FROM stock_meat_category')
            for row in cursor.fetchall():
                report['categories'].append({
                    'id': row['ids'],
                    'name': row['name_type'],
                })
        except Exception as e:
            report['warnings'].append(f'Could not read categories: {e}')
        
        # Suppliers
        try:
            cursor.execute('SELECT ids, name_place, locations FROM stock_meat_supply_meat')
            for row in cursor.fetchall():
                report['suppliers'].append({
                    'id': row['ids'],
                    'name': row['name_place'],
                    'locations': row['locations'],
                })
        except Exception as e:
            report['warnings'].append(f'Could not read suppliers: {e}')
        
        # Meat parts (product definitions)
        try:
            cursor.execute(
                'SELECT id, name, prefix_barcode, kcalories, protent, fat '
                'FROM stock_meat_meat_parts'
            )
            for row in cursor.fetchall():
                report['meat_parts'].append({
                    'id': row['id'],
                    'name': row['name'],
                    'prefix_barcode': row['prefix_barcode'] or '',
                    'kcalories': row['kcalories'] or 0,
                    'protein': row['protent'] or 0,
                    'fat': row['fat'] or 0,
                })
        except Exception as e:
            report['warnings'].append(f'Could not read meat_parts: {e}')
        
        # Product_info (batch stock records)
        try:
            cursor.execute(
                'SELECT id, type_product_id, import_from_id, name_id, '
                'lot_number, weight, cost, selling_price_per_kg, max_display_count '
                'FROM stock_meat_product_info'
            )
            for row in cursor.fetchall():
                report['product_info'].append({
                    'id': row['id'],
                    'category_id': row['type_product_id'],
                    'supplier_id': row['import_from_id'],
                    'meat_part_id': row['name_id'],
                    'lot_number': row['lot_number'],
                    'weight_g': row['weight'],
                    'cost': row['cost'] or 0,
                    'selling_price_per_kg': row['selling_price_per_kg'] or 0,
                    'max_display_count': row['max_display_count'] or 5,
                })
        except Exception as e:
            report['warnings'].append(f'Could not read product_info: {e}')
        
        # Product_list (physical packages)
        try:
            cursor.execute(
                'SELECT id, product_id, barcode, weight, selling_price, '
                'storage_status, mfg, activated '
                'FROM stock_meat_product_list'
            )
            for row in cursor.fetchall():
                report['product_list'].append({
                    'id': row['id'],
                    'product_info_id': row['product_id'],
                    'barcode': row['barcode'] or '',
                    'weight_g': row['weight'],
                    'selling_price': row['selling_price'] or 0,
                    'storage_status': row['storage_status'] or 'frozen',
                    'mfg': row['mfg'],
                    'activated': row['activated'],
                })
        except Exception as e:
            report['warnings'].append(f'Could not read product_list: {e}')
        
        # Rotation schedules
        try:
            cursor.execute(
                'SELECT id, product_list_id, status, target_ready_at, '
                'thaw_start_at, freeze_end_at, freeze_start_at '
                'FROM stock_meat_rotationschedule'
            )
            for row in cursor.fetchall():
                report['rotation_schedules'].append({
                    'id': row['id'],
                    'product_list_id': row['product_list_id'],
                    'status': row['status'],
                    'target_ready_at': row['target_ready_at'],
                })
        except Exception as e:
            report['warnings'].append(f'Could not read rotation_schedules: {e}')
        
        conn.close()
        
        # ============================================================
        # ANALYZE AND REPORT
        # ============================================================
        
        self.stdout.write('\n' + '=' * 60)
        self.stdout.write(self.style.SUCCESS('LEGACY DATA ANALYSIS'))
        self.stdout.write('=' * 60)
        
        # Categories
        self.stdout.write(f'\n📂 Categories: {len(report["categories"])}')
        for cat in report['categories']:
            self.stdout.write(f'   [{cat["id"]}] {cat["name"]}')
        
        # Suppliers
        self.stdout.write(f'\n🏭 Suppliers: {len(report["suppliers"])}')
        for sup in report['suppliers']:
            self.stdout.write(f'   [{sup["id"]}] {sup["name"]} ({sup["locations"]})')
        
        # Meat parts
        self.stdout.write(f'\n🥩 Meat Parts (→ Products): {len(report["meat_parts"])}')
        for mp in report['meat_parts']:
            cat_name = next(
                (c['name'] for c in report['categories'] if c['id'] == mp.get('category_id')),
                '?'
            ) if 'category_id' in mp else '?'
            self.stdout.write(
                f'   [{mp["id"]}] {mp["name"]} '
                f'| prefix={mp["prefix_barcode"]} '
                f'| kcal={mp["kcalories"]} '
                f'| protein={mp["protein"]} '
                f'| fat={mp["fat"]}'
            )
        
        # Product_info (batches)
        self.stdout.write(f'\n📦 Product Info (→ Batches): {len(report["product_info"])}')
        for pi in report['product_info']:
            weight_kg = pi['weight_g'] / 1000 if pi['weight_g'] else 0
            self.stdout.write(
                f'   [{pi["id"]}] Lot {pi["lot_number"]} '
                f'| {weight_kg:.2f} kg '
                f'| ฿{pi["cost"]}/kg cost '
                f'| ฿{pi["selling_price_per_kg"]}/kg sell'
            )
        
        # Product_list (packages)
        active_count = sum(1 for p in report['product_list'] if p['activated'])
        self.stdout.write(f'\n📋 Product List (→ Packages): {len(report["product_list"])} total, {active_count} active')
        for pl in report['product_list'][:10]:  # Show first 10
            weight_kg = pl['weight_g'] / 1000 if pl['weight_g'] else 0
            status_emoji = {
                'frozen': '❄️',
                'thawing': '🔄',
                'display': '🛒',
                'depleted': '📦',
            }.get(pl['storage_status'], '❓')
            self.stdout.write(
                f'   [{pl["id"]}] {pl["barcode"]} '
                f'| {weight_kg:.3f} kg '
                f'| ฿{pl["selling_price"]} '
                f'| {status_emoji} {pl["storage_status"]}'
            )
        if len(report['product_list']) > 10:
            self.stdout.write(f'   ... and {len(report["product_list"]) - 10} more')
        
        # Rotation schedules
        self.stdout.write(f'\n📅 Rotation Schedules: {len(report["rotation_schedules"])}')
        
        # ============================================================
        # CONFLICT DETECTION
        # ============================================================
        
        self.stdout.write('\n' + '=' * 60)
        self.stdout.write(self.style.WARNING('CONFLICT DETECTION'))
        self.stdout.write('=' * 60)
        
        # Barcode collisions
        existing_barcodes = set(
            Package.objects.exclude(barcode='').values_list('barcode', flat=True)
        )
        legacy_barcodes = [pl['barcode'] for pl in report['product_list'] if pl['barcode']]
        collisions = existing_barcodes.intersection(set(legacy_barcodes))
        
        if collisions:
            self.stdout.write(self.style.ERROR(
                f'\n🔴 BARCODE COLLISIONS: {len(collisions)} barcodes already exist in current DB'
            ))
            for bc in list(collisions)[:5]:
                self.stdout.write(f'   - {bc}')
        else:
            self.stdout.write(self.style.SUCCESS(
                f'\n✅ No barcode collisions detected ({len(legacy_barcodes)} legacy barcodes)'
            ))
        
        # Product name matches
        existing_products = {
            p.name.lower(): p for p in Product.objects.all()
        }
        matched = 0
        unmatched = []
        for mp in report['meat_parts']:
            if mp['name'].lower() in existing_products:
                matched += 1
            else:
                unmatched.append(mp['name'])
        
        self.stdout.write(f'\n📊 Product Matching:')
        self.stdout.write(f'   Matched: {matched}/{len(report["meat_parts"])}')
        if unmatched:
            self.stdout.write(self.style.WARNING(
                f'   Unmatched (will create new): {", ".join(unmatched)}'
            ))
        
        # ============================================================
        # MIGRATION PLAN
        # ============================================================
        
        self.stdout.write('\n' + '=' * 60)
        self.stdout.write(self.style.SUCCESS('MIGRATION PLAN'))
        self.stdout.write('=' * 60)
        
        products_to_create = len(unmatched)
        batches_to_create = len(report['product_info'])
        packages_to_create = len(report['product_list'])
        
        self.stdout.write(f'\n   Products to create: {products_to_create}')
        self.stdout.write(f'   Products to match: {matched}')
        self.stdout.write(f'   Batches to create: {batches_to_create}')
        self.stdout.write(f'   Packages to create: {packages_to_create}')
        
        if dry_run:
            self.stdout.write(self.style.WARNING(
                '\n=== DRY RUN COMPLETE — No changes made ==='
            ))
            self.stdout.write('Run with --execute to perform actual migration.')
        else:
            self.stdout.write(self.style.ERROR(
                '\n=== EXECUTE MODE ==='
            ))
            # Actual migration would go here
            self.stdout.write('Migration not yet implemented in execute mode.')
            self.stdout.write('Please review the dry-run output and implement migration logic.')
        
        # ============================================================
        # WARNINGS
        # ============================================================
        
        if report['warnings']:
            self.stdout.write('\n' + '=' * 60)
            self.stdout.write(self.style.WARNING('WARNINGS'))
            self.stdout.write('=' * 60)
            for w in report['warnings']:
                self.stdout.write(f'   ⚠️ {w}')
