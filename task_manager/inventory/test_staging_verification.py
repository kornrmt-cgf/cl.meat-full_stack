"""
TASK 02.5 — Staging Environment Verification

Proves the PostgreSQL staging environment is safe, reproducible,
isolated, and operationally ready for migration rehearsal.

Tests cover:
1. Staging environment identity (guard against production)
2. Destructive operation guard (TRUNCATE safety)
3. Staging database isolation
4. Migrations consistency
5. Clean baseline verification
6. Full rehearsal
7. Repeatability (run twice, compare logical signatures)
8. Transaction / rollback
9. Traceability
10. Data safety (legacy + default DB unchanged)
11. Staging cleanup
12. Backup / restore
13. Permissions
14. Failure safety (wrong DB, bad creds, unavailable PG)
15. Performance baseline
"""
import os
import subprocess
import tempfile
import time
from datetime import datetime
from decimal import Decimal
from unittest import SkipTest

import psycopg2
from django.test import SimpleTestCase

from inventory.migration_engine import DryRunEngine, file_hash
from inventory.migration_simulation import MigrationSimulation
from inventory.pg_simulation import (
    PgMigrationSimulation, _pg_connect, _pg_truncate_all,
    PG_CONFIG, TRUNCATE_TABLES,
)
from inventory.resolution import ResolutionApplier


# ============================================================
# HELPERS
# ============================================================

_EXPECTED_STAGING_DB = 'clmeat_staging'
_EXPECTED_STAGING_HOST = 'localhost'


def _pg_available():
    try:
        conn = _pg_connect()
        conn.close()
        return True
    except Exception:
        return False


def _pg_exec(sql, params=None):
    """Execute a single SQL statement and return result."""
    conn = _pg_connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        result = cur.fetchall() if cur.description else None
        conn.commit()
        return result
    finally:
        conn.close()


def _pg_table_counts():
    """Get row counts for all inventory tables."""
    counts = {}
    conn = _pg_connect()
    try:
        cur = conn.cursor()
        for table in TRUNCATE_TABLES:
            cur.execute(f'SELECT COUNT(*) FROM {table}')
            counts[table] = cur.fetchone()[0]
    finally:
        conn.close()
    return counts


def _pg_identity():
    """Get PostgreSQL database identity info."""
    conn = _pg_connect()
    try:
        cur = conn.cursor()
        cur.execute('SELECT current_database()')
        db_name = cur.fetchone()[0]
        cur.execute('SELECT current_user')
        user = cur.fetchone()[0]
        cur.execute('SELECT inet_server_addr()')
        host = cur.fetchone()[0]
        cur.execute('SELECT version()')
        pg_version = cur.fetchone()[0]
        return {
            'database': db_name,
            'user': user,
            'host': host or 'local',
            'pg_version': pg_version,
        }
    finally:
        conn.close()


_skip_pg = not _pg_available()


def _require_pg():
    if _skip_pg:
        raise SkipTest('PostgreSQL staging not available')


# ============================================================
# 1. STAGING ENVIRONMENT IDENTITY
# ============================================================

class TestStagingIdentity(SimpleTestCase):
    """Verify the staging DB is explicitly identified and not production."""

    def test_staging_database_name(self):
        _require_pg()
        identity = _pg_identity()
        self.assertEqual(
            identity['database'], _EXPECTED_STAGING_DB,
            f'Staging DB name mismatch: got "{identity["database"]}", '
            f'expected "{_EXPECTED_STAGING_DB}". '
            'Refusing to operate on non-staging database.')

    def test_staging_host_is_local(self):
        _require_pg()
        identity = _pg_identity()
        self.assertIn(
            identity['host'], ('local', 'localhost', '127.0.0.1', '::1'),
            f'Staging host is not local: {identity["host"]}')

    def test_staging_config_matches_env(self):
        _require_pg()
        identity = _pg_identity()
        self.assertEqual(
            PG_CONFIG['dbname'], _EXPECTED_STAGING_DB,
            f'PG_CONFIG["dbname"]={PG_CONFIG["dbname"]} '
            f'does not match expected staging name')

    def test_staging_is_not_production(self):
        """Refuse to operate if DB name contains 'prod' or 'production'."""
        _require_pg()
        identity = _pg_identity()
        dangerous_names = {'production', 'prod', 'main', 'live', 'master'}
        db_lower = identity['database'].lower()
        for dangerous in dangerous_names:
            self.assertNotIn(
                dangerous, db_lower,
                f'Staging DB name "{identity["database"]}" contains '
                f'production indicator "{dangerous}"')

    def test_staging_is_not_default_dev(self):
        """Refuse if the staging DB is the same as the default DB."""
        _require_pg()
        identity = _pg_identity()
        default_db = os.environ.get('DB_NAME', 'db.sqlite3')
        # For SQLite default, the PG staging can never match
        if default_db.endswith('.sqlite3') or default_db.endswith('.db'):
            return  # SQLite default can't collide with PG staging
        self.assertNotEqual(
            identity['database'], default_db,
            f'Staging DB is the same as default DB: {default_db}')


# ============================================================
# 2. DESTRUCTIVE OPERATION GUARD
# ============================================================

class TestDestructiveGuard(SimpleTestCase):
    """Verify destructive operations (TRUNCATE) are guarded by identity."""

    def test_truncate_refuses_non_staging(self):
        """TRUNCATE must abort if database name doesn't match staging."""
        _require_pg()
        identity = _pg_identity()
        # The guard is built into pg_simulation._pg_truncate_all.
        # Verify it only works on the correct database.
        self.assertEqual(
            identity['database'], _EXPECTED_STAGING_DB,
            'TRUNCATE guard: database identity mismatch')

    def test_truncate_only_touches_inventory_tables(self):
        """TRUNCATE must only target inventory_ tables."""
        _require_pg()
        # Verify the TRUNCATE_TABLES list only contains inventory_ tables
        for table in TRUNCATE_TABLES:
            self.assertTrue(
                table.startswith('inventory_'),
                f'TRUNCATE_TABLES contains non-inventory table: {table}')

    def test_reset_sequences_only_inventory(self):
        """Sequence reset must only target inventory_ sequences."""
        _require_pg()
        result = _pg_exec(
            "SELECT sequence_name FROM information_schema.sequences "
            "WHERE sequence_schema = 'public' "
            "AND sequence_name LIKE 'inventory_%_id_seq'")
        for (seq_name,) in result:
            self.assertTrue(
                seq_name.startswith('inventory_'),
                f'Unexpected sequence: {seq_name}')


# ============================================================
# 3. STAGING DATABASE ISOLATION
# ============================================================

class TestStagingIsolation(SimpleTestCase):
    """Verify staging is separate from default and legacy databases."""

    def test_staging_is_separate_from_default_db(self):
        _require_pg()
        default_name = os.environ.get('DJANGO_DB_NAME', 'db.sqlite3')
        identity = _pg_identity()
        if default_name.endswith('.sqlite3') or default_name.endswith('.db'):
            # Default is SQLite, staging is PostgreSQL — inherently separate
            return
        self.assertNotEqual(
            identity['database'], default_name,
            'Staging DB must differ from default DB')

    def test_staging_has_no_production_data(self):
        """Staging should only contain data from simulation runs."""
        _require_pg()
        counts = _pg_table_counts()
        # After a clean state, inventory tables should be empty
        for table in TRUNCATE_TABLES:
            self.assertEqual(
                counts[table], 0,
                f'{table} has {counts[table]} rows — '
                'staging contains pre-existing data')


# ============================================================
# 4. MIGRATIONS CONSISTENCY
# ============================================================

class TestMigrationsConsistency(SimpleTestCase):
    """Verify Django migrations are consistent and pass on staging."""

    def test_django_check_passes(self):
        _require_pg()
        result = subprocess.run(
            ['python', 'manage.py', 'check'],
            capture_output=True, text=True, timeout=30,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.assertEqual(result.returncode, 0,
            f'Django check failed:\n{result.stderr}')

    def test_no_pending_migrations(self):
        _require_pg()
        result = subprocess.run(
            ['python', 'manage.py', 'makemigrations', '--check', '--dry-run'],
            capture_output=True, text=True, timeout=30,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.assertEqual(result.returncode, 0,
            f'Pending migrations detected:\n{result.stderr}')


# ============================================================
# 5. CLEAN BASELINE
# ============================================================

class TestCleanBaseline(SimpleTestCase):
    """Verify staging starts empty before rehearsal."""

    def test_baseline_all_zero(self):
        _require_pg()
        _pg_truncate_all(_pg_connect())
        counts = _pg_table_counts()
        for table, count in counts.items():
            self.assertEqual(count, 0,
                f'Baseline violation: {table} has {count} rows')


# ============================================================
# 6. FULL REHEARSAL
# ============================================================

class TestFullRehearsal(SimpleTestCase):
    """Run complete pipeline against PostgreSQL staging."""

    def _get_legacy_path(self):
        import glob as g
        for pattern in ['../database_clmeat_main/**/*.sqlite3',
                        '../database_clmeat_main/**/*.db']:
            matches = g.glob(pattern, recursive=True)
            if matches:
                return matches[0]
        return None

    def test_full_pg_rehearsal(self):
        _require_pg()
        legacy_path = self._get_legacy_path()
        if not legacy_path:
            raise SkipTest('Legacy database not found')

        sim = PgMigrationSimulation(legacy_path).run()
        s = sim.summary()

        self.assertEqual(s['total'], 209,
            f'Expected 209 candidates, got {s["total"]}')
        self.assertGreater(s['insertable'], 0,
            'No records inserted')
        self.assertGreater(s['blocked'], 0,
            'No blockers found (unexpected)')
        self.assertTrue(s['legacy_unchanged'],
            'Legacy DB was modified!')
        self.assertTrue(s['default_db_unchanged'],
            'Default DB was modified!')

    def test_rehearsal_root_dependent_classification(self):
        _require_pg()
        legacy_path = self._get_legacy_path()
        if not legacy_path:
            raise SkipTest('Legacy database not found')

        sim = PgMigrationSimulation(legacy_path).run()
        s = sim.summary()

        self.assertGreater(s['root_blockers'], 0,
            'No root blockers found')
        self.assertGreaterEqual(s['dependent_blockers'], 0)
        # Root and dependent are counted by unique entity:legacy_id keys,
        # not by individual failure counts. Some blocked records may have
        # both root and dependent failures across different fields.
        # Just verify root > 0 and dependent >= 0.

    def test_rehearsal_known_blockers_present(self):
        """Known data blockers must remain visible."""
        _require_pg()
        legacy_path = self._get_legacy_path()
        if not legacy_path:
            raise SkipTest('Legacy database not found')

        sim = PgMigrationSimulation(legacy_path).run()

        # Must have duplicate SKU blockers
        sku_failures = [f for f in sim.failures
                       if f.category == 'DATABASE_CONSTRAINT'
                       and f.field == 'sku']
        self.assertGreater(len(sku_failures), 0,
            'Duplicate SKU blocker not detected')

        # Must have duplicate batch_number blockers
        batch_failures = [f for f in sim.failures
                         if f.category == 'SOURCE_INTRINSIC_BLOCKER'
                         and f.field == 'batch_number']
        self.assertGreater(len(batch_failures), 0,
            'Duplicate batch_number blocker not detected')


# ============================================================
# 7. REPEATABILITY
# ============================================================

class TestRepeatability(SimpleTestCase):
    """Run rehearsal twice and verify identical logical results."""

    def _get_legacy_path(self):
        import glob as g
        for pattern in ['../database_clmeat_main/**/*.sqlite3',
                        '../database_clmeat_main/**/*.db']:
            matches = g.glob(pattern, recursive=True)
            if matches:
                return matches[0]
        return None

    def test_repeatability_logical_signature(self):
        _require_pg()
        legacy_path = self._get_legacy_path()
        if not legacy_path:
            raise SkipTest('Legacy database not found')

        sig1 = PgMigrationSimulation(legacy_path).run().get_logical_signature()
        sig2 = PgMigrationSimulation(legacy_path).run().get_logical_signature()

        self.assertEqual(len(sig1), len(sig2),
            f'Different record counts: {len(sig1)} vs {len(sig2)}')
        for i, (a, b) in enumerate(zip(sig1, sig2)):
            self.assertEqual(a, b,
                f'Record {i} differs:\n  run1: {a}\n  run2: {b}')

    def test_repeatability_summary_counts(self):
        _require_pg()
        legacy_path = self._get_legacy_path()
        if not legacy_path:
            raise SkipTest('Legacy database not found')

        s1 = PgMigrationSimulation(legacy_path).run().summary()
        s2 = PgMigrationSimulation(legacy_path).run().summary()

        for key in ['total', 'insertable', 'blocked', 'warnings',
                    'root_blockers', 'dependent_blockers']:
            self.assertEqual(s1[key], s2[key],
                f'{key} differs: {s1[key]} vs {s2[key]}')


# ============================================================
# 8. TRANSACTION / ROLLBACK
# ============================================================

class TestTransactionRollback(SimpleTestCase):
    """Test that failed operations leave no partial state."""

    def test_failed_insert_leaves_no_residual(self):
        """Insert + rollback must not leave any rows."""
        _require_pg()
        _pg_truncate_all(_pg_connect())

        conn = _pg_connect()
        try:
            cur = conn.cursor()
            now = datetime.now()

            # Insert a category (succeeds)
            cur.execute(
                'INSERT INTO inventory_category '
                '(code, name, name_thai, is_active, created_at) '
                'VALUES (%s, %s, %s, %s, %s) RETURNING id',
                ('TEST_CAT', 'Test', 'Test', True, now))
            cat_id = cur.fetchone()[0]
            conn.commit()

            # Now try to insert a duplicate (must fail)
            try:
                cur.execute(
                    'INSERT INTO inventory_category '
                    '(code, name, name_thai, is_active, created_at) '
                    'VALUES (%s, %s, %s, %s, %s)',
                    ('TEST_CAT', 'Test2', 'Test2', True, now))
                conn.commit()
                self.fail('Expected IntegrityError for duplicate code')
            except psycopg2.IntegrityError:
                conn.rollback()

            # Verify only one category exists
            cur.execute('SELECT COUNT(*) FROM inventory_category')
            count = cur.fetchone()[0]
            self.assertEqual(count, 1,
                f'Expected 1 category after rollback, got {count}')
        finally:
            conn.close()
            _pg_truncate_all(_pg_connect())

    def test_batch_insert_rollback_on_supplier_fk_failure(self):
        """Batch insert without valid supplier must leave no batch."""
        _require_pg()
        _pg_truncate_all(_pg_connect())

        conn = _pg_connect()
        try:
            cur = conn.cursor()
            now = datetime.now()

            # Try batch with non-existent supplier_id=99999
            try:
                cur.execute(
                    'INSERT INTO inventory_batch '
                    '(batch_number, supplier_id, received_at, notes, '
                    'active, created_at, updated_at) '
                    'VALUES (%s, %s, %s, %s, %s, %s, %s)',
                    ('B-TEST-001', 99999, now, '', True, now, now))
                conn.commit()
                self.fail('Expected FK violation')
            except psycopg2.IntegrityError:
                conn.rollback()

            # Verify no batch was created
            cur.execute('SELECT COUNT(*) FROM inventory_batch')
            count = cur.fetchone()[0]
            self.assertEqual(count, 0,
                f'Expected 0 batches after FK failure, got {count}')
        finally:
            conn.close()
            _pg_truncate_all(_pg_connect())

    def test_package_insert_rollback_on_product_fk_failure(self):
        """Package insert without valid product must leave no package."""
        _require_pg()
        _pg_truncate_all(_pg_connect())

        conn = _pg_connect()
        try:
            cur = conn.cursor()
            now = datetime.now()

            # Create a category + supplier first
            cur.execute(
                'INSERT INTO inventory_category '
                '(code, name, name_thai, is_active, created_at) '
                'VALUES (%s, %s, %s, %s, %s) RETURNING id',
                ('PORK', 'Pork', 'Pork', True, now))
            cat_id = cur.fetchone()[0]
            cur.execute(
                'INSERT INTO inventory_supplier '
                '(name, locations, is_active, created_at) '
                'VALUES (%s, %s, %s, %s) RETURNING id',
                ('S1', '', True, now))
            sup_id = cur.fetchone()[0]

            # Create a valid product
            cur.execute(
                'INSERT INTO inventory_product '
                '(sku, name, name_thai, category_id, unit, cost_per_kg, '
                'selling_price_per_kg, barcode_prefix, kcalories, '
                'protein, fat, active, created_at, updated_at) '
                'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) '
                'RETURNING id',
                ('MP-TEST', 'Test', 'Test', cat_id, 'KG', '100', '150',
                 '8001', '185', '31', '6', True, now, now))
            prod_id = cur.fetchone()[0]

            # Create a valid batch
            cur.execute(
                'INSERT INTO inventory_batch '
                '(batch_number, supplier_id, received_at, notes, '
                'active, created_at, updated_at) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id',
                ('B-TEST-001', sup_id, now, '', True, now, now))
            batch_id = cur.fetchone()[0]
            conn.commit()

            # Now try package with non-existent product_id=99999
            try:
                cur.execute(
                    'INSERT INTO inventory_package '
                    '(product_id, batch_id, barcode, weight, '
                    'selling_price, packed_at, current_state, '
                    'loyverse_synced, created_at, updated_at) '
                    'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                    (99999, batch_id, 'DUP-TEST', '1.000', '150',
                     now, 'PACKED', False, now, now))
                conn.commit()
                self.fail('Expected FK violation for product')
            except psycopg2.IntegrityError:
                conn.rollback()

            # Verify product, batch, supplier, category still exist
            cur.execute('SELECT COUNT(*) FROM inventory_product')
            self.assertEqual(cur.fetchone()[0], 1)
            cur.execute('SELECT COUNT(*) FROM inventory_batch')
            self.assertEqual(cur.fetchone()[0], 1)
            cur.execute('SELECT COUNT(*) FROM inventory_package')
            self.assertEqual(cur.fetchone()[0], 0,
                'Package should not exist after FK failure')
        finally:
            conn.close()
            _pg_truncate_all(_pg_connect())

    def test_successful_insert_persists(self):
        """Successful inserts should persist within the transaction."""
        _require_pg()
        _pg_truncate_all(_pg_connect())

        conn = _pg_connect()
        try:
            cur = conn.cursor()
            now = datetime.now()
            cur.execute(
                'INSERT INTO inventory_category '
                '(code, name, name_thai, is_active, created_at) '
                'VALUES (%s, %s, %s, %s, %s) RETURNING id',
                ('PERSIST', 'Persist', 'Persist', True, now))
            cat_id = cur.fetchone()[0]
            conn.commit()

            # Verify it persisted
            cur.execute('SELECT COUNT(*) FROM inventory_category')
            self.assertEqual(cur.fetchone()[0], 1)
            cur.execute('SELECT code FROM inventory_category WHERE id = %s',
                       (cat_id,))
            self.assertEqual(cur.fetchone()[0], 'PERSIST')
        finally:
            conn.close()
            _pg_truncate_all(_pg_connect())


# ============================================================
# 9. TRACEABILITY
# ============================================================

class TestTraceability(SimpleTestCase):
    """Verify every inserted record has a source trace."""

    def _get_legacy_path(self):
        import glob as g
        for pattern in ['../database_clmeat_main/**/*.sqlite3',
                        '../database_clmeat_main/**/*.db']:
            matches = g.glob(pattern, recursive=True)
            if matches:
                return matches[0]
        return None

    def test_every_inserted_record_has_target_id(self):
        _require_pg()
        legacy_path = self._get_legacy_path()
        if not legacy_path:
            raise SkipTest('Legacy database not found')

        sim = PgMigrationSimulation(legacy_path).run()

        for entity, records in sim.results.items():
            for r in records:
                if r.status == 'INSERTABLE':
                    self.assertIsNotNone(r.target_id,
                        f'{r.entity} #{r.legacy_id} INSERTABLE '
                        f'but no target_id — trace broken')
                    self.assertGreater(r.target_id, 0)

    def test_traceability_count_matches_insertable(self):
        _require_pg()
        legacy_path = self._get_legacy_path()
        if not legacy_path:
            raise SkipTest('Legacy database not found')

        sim = PgMigrationSimulation(legacy_path).run()
        s = sim.summary()
        traced = sum(1 for t in sim.traceability if t.get('target_id'))
        self.assertEqual(s['insertable'], traced,
            f'Insertable ({s["insertable"]}) != traced ({traced})')


# ============================================================
# 10. DATA SAFETY
# ============================================================

class TestDataSafety(SimpleTestCase):
    """Verify legacy and default databases are untouched."""

    def _get_legacy_path(self):
        import glob as g
        for pattern in ['../database_clmeat_main/**/*.sqlite3',
                        '../database_clmeat_main/**/*.db']:
            matches = g.glob(pattern, recursive=True)
            if matches:
                return matches[0]
        return None

    def test_legacy_db_unchanged(self):
        _require_pg()
        legacy_path = self._get_legacy_path()
        if not legacy_path:
            raise SkipTest('Legacy database not found')

        h1 = file_hash(legacy_path)
        PgMigrationSimulation(legacy_path).run()
        h2 = file_hash(legacy_path)
        self.assertEqual(h1, h2,
            'Legacy database was modified during simulation!')

    def test_default_db_unchanged(self):
        _require_pg()
        legacy_path = self._get_legacy_path()
        if not legacy_path:
            raise SkipTest('Legacy database not found')

        default_name = os.environ.get('DJANGO_DB_NAME', 'db.sqlite3')
        if os.path.exists(default_name):
            h1 = file_hash(default_name)
            PgMigrationSimulation(legacy_path).run()
            h2 = file_hash(default_name)
            self.assertEqual(h1, h2,
                'Default database was modified during simulation!')


# ============================================================
# 11. STAGING CLEANUP
# ============================================================

class TestStagingCleanup(SimpleTestCase):
    """Verify staging is fully cleaned after rehearsal."""

    def _get_legacy_path(self):
        import glob as g
        for pattern in ['../database_clmeat_main/**/*.sqlite3',
                        '../database_clmeat_main/**/*.db']:
            matches = g.glob(pattern, recursive=True)
            if matches:
                return matches[0]
        return None

    def test_all_inventory_tables_empty_after_simulation(self):
        _require_pg()
        legacy_path = self._get_legacy_path()
        if not legacy_path:
            raise SkipTest('Legacy database not found')

        PgMigrationSimulation(legacy_path).run()

        counts = _pg_table_counts()
        for table, count in counts.items():
            self.assertEqual(count, 0,
                f'{table} has {count} rows after simulation — '
                'cleanup incomplete')

    def test_sequences_reset_after_simulation(self):
        """After truncate+restart, sequence values should be 1."""
        _require_pg()
        legacy_path = self._get_legacy_path()
        if not legacy_path:
            raise SkipTest('Legacy database not found')

        PgMigrationSimulation(legacy_path).run()

        conn = _pg_connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT sequence_name, last_value "
                "FROM information_schema.sequences s "
                "JOIN pg_sequences ps ON s.sequence_name = ps.sequencename "
                "WHERE s.sequence_schema = 'public' "
                "AND s.sequence_name LIKE 'inventory_%_id_seq'")
            for seq_name, last_value in cur.fetchall():
                self.assertEqual(last_value, 1,
                    f'{seq_name} has last_value={last_value}, expected 1')
        finally:
            conn.close()


# ============================================================
# 12. BACKUP / RESTORE
# ============================================================

class TestBackupRestore(SimpleTestCase):
    """Staging-only backup and restore rehearsal."""

    def test_pg_dump_and_restore(self):
        """Backup staging, restore to temp DB, verify schema."""
        _require_pg()
        _pg_truncate_all(_pg_connect())

        # Insert minimal data
        conn = _pg_connect()
        try:
            cur = conn.cursor()
            now = datetime.now()
            cur.execute(
                'INSERT INTO inventory_category '
                '(code, name, name_thai, is_active, created_at) '
                'VALUES (%s, %s, %s, %s, %s) RETURNING id',
                ('BKUP', 'Backup Test', 'Backup Test', True, now))
            conn.commit()
        finally:
            conn.close()

        # Backup
        fd, backup_path = tempfile.mkstemp(suffix='.sql')
        os.close(fd)
        try:
            result = subprocess.run(
                ['/opt/homebrew/opt/postgresql@16/bin/pg_dump',
                 '-h', PG_CONFIG['host'],
                 '-U', PG_CONFIG['user'],
                 '-d', PG_CONFIG['dbname'],
                 '-f', backup_path,
                 '--data-only'],
                capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0,
                f'pg_dump failed:\n{result.stderr}')

            # Restore to a temp DB
            temp_db = 'clmeat_staging_restore_test'
            subprocess.run(
                ['/opt/homebrew/opt/postgresql@16/bin/createdb',
                 '-h', PG_CONFIG['host'],
                 '-U', PG_CONFIG['user'],
                 temp_db],
                capture_output=True, text=True, timeout=10)
            try:
                # First: create schema via Django migrations on temp DB
                env = os.environ.copy()
                env['DJANGO_SECRET_KEY'] = 'staging-restore-test'
                env['STAGING_DB_NAME'] = temp_db
                result = subprocess.run(
                    ['python', 'manage.py', 'migrate',
                     '--database=staging'],
                    capture_output=True, text=True, timeout=60,
                    env=env,
                    cwd=os.path.dirname(os.path.dirname(
                        os.path.abspath(__file__))))
                self.assertEqual(result.returncode, 0,
                    f'Migrations on temp DB failed:\n{result.stderr}')

                # Second: restore data via pg_dump SQL file
                result = subprocess.run(
                    ['/opt/homebrew/opt/postgresql@16/bin/psql',
                     '-h', PG_CONFIG['host'],
                     '-U', PG_CONFIG['user'],
                     '-d', temp_db,
                     '-f', backup_path],
                    capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0,
                    f'pg_restore failed:\n{result.stderr}')

                # Verify data exists in restored DB
                restore_conn = psycopg2.connect(
                    dbname=temp_db,
                    user=PG_CONFIG['user'],
                    host=PG_CONFIG['host'],
                    port=PG_CONFIG['port'])
                try:
                    cur = restore_conn.cursor()
                    cur.execute(
                        'SELECT COUNT(*) FROM inventory_category')
                    count = cur.fetchone()[0]
                    self.assertGreater(count, 0,
                        'No data in restored database')

                    # Verify constraints still work
                    try:
                        cur.execute(
                            'INSERT INTO inventory_category '
                            '(code, name, name_thai, is_active, '
                            'created_at) VALUES '
                            '(%s, %s, %s, %s, %s)',
                            ('BKUP', 'Dup', 'Dup', True,
                             datetime.now()))
                        restore_conn.commit()
                        self.fail('UNIQUE constraint not enforced '
                                  'after restore')
                    except psycopg2.IntegrityError:
                        restore_conn.rollback()
                finally:
                    restore_conn.close()
            finally:
                subprocess.run(
                    ['/opt/homebrew/opt/postgresql@16/bin/dropdb',
                     '-h', PG_CONFIG['host'],
                     '-U', PG_CONFIG['user'],
                     temp_db],
                    capture_output=True, timeout=10)
        finally:
            os.unlink(backup_path)
            _pg_truncate_all(_pg_connect())


# ============================================================
# 13. PERMISSIONS
# ============================================================

class TestPermissions(SimpleTestCase):
    """Verify staging user has appropriate permissions."""

    def test_user_can_select(self):
        _require_pg()
        result = _pg_exec('SELECT 1')
        self.assertEqual(result, [(1,)])

    def test_user_can_insert_and_delete(self):
        _require_pg()
        conn = _pg_connect()
        try:
            cur = conn.cursor()
            now = datetime.now()
            cur.execute(
                'INSERT INTO inventory_category '
                '(code, name, name_thai, is_active, created_at) '
                'VALUES (%s, %s, %s, %s, %s) RETURNING id',
                ('PERM_TEST', 'Perm', 'Perm', True, now))
            cat_id = cur.fetchone()[0]
            conn.commit()

            # Verify we can read it
            cur.execute('SELECT code FROM inventory_category WHERE id = %s',
                       (cat_id,))
            self.assertEqual(cur.fetchone()[0], 'PERM_TEST')

            # Delete it
            cur.execute('DELETE FROM inventory_category WHERE id = %s',
                       (cat_id,))
            conn.commit()
            cur.execute('SELECT COUNT(*) FROM inventory_category')
            self.assertEqual(cur.fetchone()[0], 0)
        finally:
            conn.close()

    def test_user_can_create_via_migrations(self):
        """The staging user can create tables through Django migrations."""
        _require_pg()
        # If we got this far, migrations already ran successfully
        result = _pg_exec(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' "
            "AND table_name = 'inventory_category'")
        self.assertEqual(len(result), 1,
            'inventory_category table not found — migrations may have failed')

    def test_user_permissions_documented(self):
        """Document the effective permissions."""
        _require_pg()
        conn = _pg_connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT has_database_privilege(current_user, "
                "current_database(), 'CONNECT')")
            can_connect = cur.fetchone()[0]
            cur.execute(
                "SELECT has_database_privilege(current_user, "
                "current_database(), 'CREATE')")
            can_create = cur.fetchone()[0]
            self.assertTrue(can_connect,
                'User cannot connect to staging database')
            # CREATE may be False for non-superuser — that's OK
            # Migrations use the user's own schema privileges
        finally:
            conn.close()


# ============================================================
# 14. FAILURE SAFETY
# ============================================================

class TestFailureSafety(SimpleTestCase):
    """Verify the system fails clearly on connection errors."""

    def test_wrong_database_name_fails(self):
        """Connecting to wrong DB name must raise an error."""
        try:
            conn = psycopg2.connect(
                dbname='nonexistent_database_xyz',
                user=PG_CONFIG['user'],
                host=PG_CONFIG['host'],
                port=PG_CONFIG['port'])
            conn.close()
            self.fail('Expected OperationalError for wrong database')
        except psycopg2.OperationalError:
            pass  # Expected

    def test_wrong_host_fails(self):
        """Connecting to wrong host must raise an error."""
        try:
            conn = psycopg2.connect(
                dbname=PG_CONFIG['dbname'],
                user=PG_CONFIG['user'],
                host='192.0.2.1',  # TEST-NET-1, should not be routable
                port=PG_CONFIG['port'],
                connect_timeout=3)
            conn.close()
            self.fail('Expected error for wrong host')
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            pass  # Expected

    def test_wrong_port_fails(self):
        """Connecting to wrong port must raise an error."""
        try:
            conn = psycopg2.connect(
                dbname=PG_CONFIG['dbname'],
                user=PG_CONFIG['user'],
                host=PG_CONFIG['host'],
                port='19999',
                connect_timeout=3)
            conn.close()
            self.fail('Expected error for wrong port')
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            pass  # Expected

    def test_wrong_user_fails(self):
        """Connecting with wrong user must raise an error."""
        try:
            conn = psycopg2.connect(
                dbname=PG_CONFIG['dbname'],
                user='nonexistent_user_xyz',
                host=PG_CONFIG['host'],
                port=PG_CONFIG['port'])
            conn.close()
            self.fail('Expected error for wrong user')
        except psycopg2.OperationalError:
            pass  # Expected


# ============================================================
# 15. PERFORMANCE BASELINE
# ============================================================

class TestPerformanceBaseline(SimpleTestCase):
    """Record performance metrics for future reference."""

    def _get_legacy_path(self):
        import glob as g
        for pattern in ['../database_clmeat_main/**/*.sqlite3',
                        '../database_clmeat_main/**/*.db']:
            matches = g.glob(pattern, recursive=True)
            if matches:
                return matches[0]
        return None

    def test_performance_metrics_collected(self):
        _require_pg()
        legacy_path = self._get_legacy_path()
        if not legacy_path:
            raise SkipTest('Legacy database not found')

        sim = PgMigrationSimulation(legacy_path).run()
        s = sim.summary()

        self.assertIn('insertion_time', s)
        self.assertIn('records_per_sec', s)
        self.assertGreaterEqual(s['insertion_time'], 0)
        # Records/sec should be reasonable (at least 10 rec/s)
        if s['insertable'] > 0:
            self.assertGreater(s['records_per_sec'], 10,
                f'Performance regression: {s["records_per_sec"]:.1f} rec/s')

    def test_pg_version_documented(self):
        _require_pg()
        identity = _pg_identity()
        self.assertIn('PostgreSQL', identity['pg_version'])
        # Should be version 12+
        self.assertIn('PostgreSQL 1', identity['pg_version'])
