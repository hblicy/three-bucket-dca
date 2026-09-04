from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from dca_tracker import db, migration
from scripts import migrate_legacy


class MigrationSafetyTest(unittest.TestCase):
    def test_repeated_legacy_migration_preserves_existing_edited_row(self):
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            root = Path(tmpdir)
            legacy_db = root / "legacy_btc.db"
            with sqlite3.connect(legacy_db) as conn:
                conn.execute(
                    "CREATE TABLE investments (id INTEGER PRIMARY KEY, date TEXT, amount REAL, price REAL, btc_bought REAL, notes TEXT)"
                )
                conn.execute(
                    "INSERT INTO investments VALUES (1, '2026-01-01', 100, 50000, 0.002, 'legacy')"
                )

            with (
                patch.object(db, "DB_PATH", root / "data" / "dca_tracker.db"),
                patch.object(db, "BACKUP_DIR", root / "backups"),
                patch.dict(migration.LEGACY_PATHS, {"btc": legacy_db}),
            ):
                db.init_db()
                migration.migrate_btc()
                with db.connect() as conn:
                    conn.execute(
                        "UPDATE transactions SET amount_usd=777, note='edited' WHERE source='BTC_DDCA_old999' AND legacy_id='1'"
                    )
                with sqlite3.connect(legacy_db) as conn:
                    conn.execute("UPDATE investments SET amount=200, notes='changed source' WHERE id=1")
                migration.migrate_btc()
                with db.connect() as conn:
                    row = conn.execute(
                        "SELECT amount_usd, note FROM transactions WHERE source='BTC_DDCA_old999' AND legacy_id='1'"
                    ).fetchone()
                self.assertEqual(float(row["amount_usd"]), 777.0)
                self.assertEqual(row["note"], "edited")

    def test_manual_migration_backs_up_before_import(self):
        runner = getattr(migrate_legacy, "main", None)
        self.assertIsNotNone(runner)
        events = []
        with (
            patch.object(migrate_legacy, "backup_db", side_effect=lambda: events.append("backup")),
            patch.object(migrate_legacy, "migrate_all", side_effect=lambda: events.append("migrate") or {}),
        ):
            runner()
        self.assertEqual(events, ["backup", "migrate"])
