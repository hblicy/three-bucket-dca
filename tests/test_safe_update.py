from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


class SafeUpdateBackupTest(unittest.TestCase):
    def test_required_backup_rejects_missing_output(self):
        try:
            from scripts import backup_for_update
        except ImportError as exc:
            self.fail(f"缺少更新前备份检查模块: {exc}")

        with TemporaryDirectory() as tmpdir:
            missing_path = Path(tmpdir) / "missing.db"
            for result in (None, missing_path):
                with self.subTest(result=result), patch.object(
                    backup_for_update, "backup_db", return_value=result
                ):
                    with self.assertRaises(RuntimeError):
                        backup_for_update.create_required_backup()


if __name__ == "__main__":
    unittest.main()
