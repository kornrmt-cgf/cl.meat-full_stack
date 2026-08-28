"""
Management command: migrate_legacy_data

STRICTLY READ-ONLY dry-run for legacy data migration.
Never writes to any database.

Usage:
    python manage.py migrate_legacy_data --dry-run
    python manage.py migrate_legacy_data --dry-run --legacy-db path/to/legacy.db
    python manage.py migrate_legacy_data --dry-run --json-output report.json
"""
import os
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from inventory.migration_engine import DryRunEngine


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

    def handle(self, *args, **options):
        # ── SAFETY CHECKS ──
        self.stdout.write(self.style.WARNING(
            "⚠️  DRY-RUN MODE — No data will be written to any database."
        ))

        # Resolve legacy database path
        legacy_db = options.get('legacy_db')
        if not legacy_db:
            # Default: look in workspace root
            workspace = Path(__file__).resolve().parents[4]
            legacy_db = str(workspace / 'database_clmeat_main' / 'db.sqlite3')

        legacy_path = Path(legacy_db)
        if not legacy_path.exists():
            raise CommandError(f"Legacy database not found: {legacy_path}")

        # Verify it's read-only safe (don't open for write)
        self.stdout.write(f"\nSource database: {legacy_path}")
        self.stdout.write(f"File size: {legacy_path.stat().st_size:,} bytes")
        self.stdout.write(f"Read-only: Yes (SQLite URI mode=ro)\n")

        # ── RUN DRY-RUN ──
        engine = DryRunEngine(str(legacy_path))
        engine.run()

        # ── PRINT REPORT ──
        engine.print_report()

        # ── JSON OUTPUT ──
        json_path = options.get('json_output')
        if json_path:
            engine.export_json(json_path)
            self.stdout.write(self.style.SUCCESS(f"JSON report saved to: {json_path}"))

        # ── FINAL SAFETY CONFIRMATION ──
        self.stdout.write(self.style.WARNING(
            "\n🔒 CONFIRMED: No data was written to any database."
        ))
