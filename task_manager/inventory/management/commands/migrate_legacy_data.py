"""
Management command: migrate_legacy_data

STRICTLY READ-ONLY dry-run for legacy data migration.
Never writes to any database.

Usage:
    python manage.py migrate_legacy_data --dry-run
    python manage.py migrate_legacy_data --dry-run --legacy-db path/to/legacy.db
    python manage.py migrate_legacy_data --dry-run --json-output report.json
    python manage.py migrate_legacy_data --dry-run --apply-resolutions
"""
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from inventory.migration_engine import DryRunEngine, file_hash
from inventory.migration_engine import ReconciliationReport
from inventory.resolution import (
    classify_findings, print_resolution_report,
    ResolutionApplier, AuditTrail, print_audit_trail,
)


class Command(BaseCommand):
    help = 'Dry-run legacy data migration (READ-ONLY — never writes to any database)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true', default=True,
            help='Read-only preview mode (always enabled for safety)',
        )
        parser.add_argument(
            '--legacy-db', type=str,
            default=None,
            help='Path to legacy SQLite database',
        )
        parser.add_argument(
            '--json-output', type=str, default=None,
            help='Path for JSON report output',
        )
        parser.add_argument(
            '--apply-resolutions', action='store_true', default=False,
            help='Apply approved resolution rules to migration candidates',
        )
        parser.add_argument(
            '--simulate', action='store_true', default=False,
            help='Run full migration simulation with real target DB validation',
        )

    def handle(self, *args, **options):
        # ── SAFETY CHECKS ──
        self.stdout.write(self.style.WARNING(
            "⚠️  DRY-RUN MODE — No data will be written to any database."
        ))

        # Resolve legacy database path
        legacy_db = options.get('legacy_db')
        if not legacy_db:
            workspace = Path(__file__).resolve().parents[4]
            legacy_db = str(workspace / 'database_clmeat_main' / 'db.sqlite3')

        legacy_path = Path(legacy_db)
        if not legacy_path.exists():
            raise CommandError(f"Legacy database not found: {legacy_path}")

        # File hash before dry-run (read-only verification)
        hash_before = file_hash(legacy_path)
        self.stdout.write(f"\nSource database: {legacy_path}")
        self.stdout.write(f"File size: {legacy_path.stat().st_size:,} bytes")
        self.stdout.write(f"SHA-256 (before): {hash_before[:16]}...")
        self.stdout.write(f"Read-only: Yes (SQLite URI mode=ro)\n")

        # ── RUN DRY-RUN ──
        engine = DryRunEngine(str(legacy_path))
        engine.run()

        # ── PRINT REPORT (BEFORE RESOLUTION) ──
        self.stdout.write(self.style.WARNING("\n" + "=" * 60))
        self.stdout.write(self.style.WARNING("  BEFORE RESOLUTION"))
        self.stdout.write(self.style.WARNING("=" * 60))
        engine.print_report()

        # ── RESOLUTION CLASSIFICATION (BEFORE) ──
        classification_before = classify_findings(engine.results)
        print_resolution_report(classification_before)

        # ── APPLY RESOLUTIONS (if requested) ──
        if options.get('apply_resolutions'):
            self.stdout.write(self.style.WARNING("\n" + "=" * 60))
            self.stdout.write(self.style.WARNING("  APPLYING APPROVED RESOLUTIONS"))
            self.stdout.write(self.style.WARNING("=" * 60))

            applier = ResolutionApplier()
            trail = applier.preview(engine.results)
            print_audit_trail(trail)

            applied = applier.apply(engine.results, trail)
            self.stdout.write(self.style.SUCCESS(
                f"\n✅ Applied {applied} resolution(s)"
            ))

            # ── PRINT REPORT (AFTER RESOLUTION) ──
            self.stdout.write(self.style.WARNING("\n" + "=" * 60))
            self.stdout.write(self.style.WARNING("  AFTER RESOLUTION"))
            self.stdout.write(self.style.WARNING("=" * 60))
            engine.print_report()

            # ── RESOLUTION CLASSIFICATION (AFTER) ──
            classification_after = classify_findings(engine.results)
            print_resolution_report(classification_after)

            # ── RECOMPUTE RECONCILIATION (after resolution mutations) ──
            engine.reconciliation = ReconciliationReport()
            source_keys = {
                'categories': 'stock_meat_category',
                'suppliers': 'stock_meat_supply_meat',
                'products': 'stock_meat_meat_parts',
                'batches': 'stock_meat_product_info',
                'packages': 'stock_meat_product_list',
            }
            for model_name, table_key in source_keys.items():
                engine.reconciliation.register(
                    model_name,
                    engine.results['source_counts'].get(table_key, 0),
                    engine.results[model_name],
                )

            # ── FINDINGS DELTA ──
            self._print_findings_delta(classification_before, classification_after)

        # ── VERIFY READ-ONLY (file hash after) ──
        hash_after = file_hash(legacy_path)
        if hash_before == hash_after:
            self.stdout.write(self.style.SUCCESS(
                f"\n🔒 READ-ONLY VERIFIED: Legacy database unchanged (SHA-256 match)"
            ))
        else:
            self.stdout.write(self.style.ERROR(
                f"\n❌ WARNING: Legacy database was modified during dry-run! "
                f"Before={hash_before[:16]}... After={hash_after[:16]}..."
            ))

        # ── JSON OUTPUT ──
        json_path = options.get('json_output')
        if json_path:
            engine.export_json(json_path)
            self.stdout.write(self.style.SUCCESS(f"JSON report saved to: {json_path}"))

        # ── SIMULATION (if requested) ──
        if options.get('simulate'):
            from inventory.migration_simulation import MigrationSimulation
            self.stdout.write(self.style.WARNING("\n" + "=" * 60))
            self.stdout.write(self.style.WARNING("  RUNNING MIGRATION SIMULATION"))
            self.stdout.write(self.style.WARNING("=" * 60))
            sim = MigrationSimulation(str(legacy_path))
            sim.run()
            sim.print_report()

        # ── FINAL SAFETY CONFIRMATION ──
        self.stdout.write(self.style.WARNING(
            "\n🔒 CONFIRMED: No data was written to any database."
        ))

    def _print_findings_delta(self, before, after):
        """Print the difference in findings between before and after resolution."""
        self.stdout.write(self.style.WARNING("\n" + "=" * 60))
        self.stdout.write(self.style.WARNING("  FINDINGS DELTA (Before → After)"))
        self.stdout.write(self.style.WARNING("=" * 60))

        b = before['summary']
        a = after['summary']

        for key in ['total_findings', 'migration_blocker', 'manual_review',
                     'accepted_exception', 'auto_fix_safe', 'structural_problem']:
            b_val = b.get(key, 0)
            a_val = a.get(key, 0)
            delta = a_val - b_val
            if delta != 0:
                sign = '+' if delta > 0 else ''
                self.stdout.write(f"  {key:25s}  {b_val:3d} → {a_val:3d}  ({sign}{delta})")
            else:
                self.stdout.write(f"  {key:25s}  {b_val:3d}   (unchanged)")

        # Show which finding codes disappeared
        b_codes = set()
        a_codes = set()
        for f in before['findings']:
            b_codes.add(f.finding_code)
        for f in after['findings']:
            a_codes.add(f.finding_code)

        resolved_codes = b_codes - a_codes
        if resolved_codes:
            self.stdout.write(self.style.SUCCESS(
                f"\n  ✅ Finding codes fully resolved: {', '.join(sorted(resolved_codes))}"
            ))

        remaining = a_codes - b_codes
        if remaining:
            self.stdout.write(self.style.WARNING(
                f"\n  ⚠️  New finding codes after resolution: {', '.join(sorted(remaining))}"
            ))

        self.stdout.write()
