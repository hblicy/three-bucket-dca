from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch

import app
from dca_tracker import cache, db


class CacheFreshnessTest(unittest.TestCase):
    def test_dashboard_rebuilds_instead_of_returning_stale_formal_advice(self):
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
            db_path = Path(tmpdir) / "data" / "dca_tracker.db"
            stale_payload = {
                "updated_at": "2025-01-01T00:00:00",
                "decisions": [{"data_ok": True, "recommended_amount": 999.0}],
            }
            with patch.object(db, "DB_PATH", db_path):
                db.init_db()
                with db.connect() as conn:
                    conn.execute(
                        "INSERT INTO api_cache(cache_key, payload, updated_at) VALUES (?, ?, ?)",
                        (
                            cache.DASHBOARD_KEY,
                            json.dumps(stale_payload),
                            (datetime.now() - timedelta(minutes=16)).isoformat(timespec="seconds"),
                        ),
                    )
                fresh_payload = {"updated_at": datetime.now().isoformat(timespec="seconds"), "decisions": []}
                with patch.object(cache, "build_dashboard", return_value=fresh_payload) as builder:
                    result = app.dashboard(None, False)

        builder.assert_called_once_with()
        self.assertFalse(result["cache"]["hit"])
        self.assertEqual(result["decisions"], [])


if __name__ == "__main__":
    unittest.main()
