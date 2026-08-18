from __future__ import annotations

import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from dca_tracker.db import backup_db, init_db, write_lock
from dca_tracker.migration import migrate_all
from dca_tracker.notify import send_due_reminders
from dca_tracker.cache import refresh_all


if __name__ == "__main__":
    init_db()
    if os.environ.get("DCA_UPDATE_MIGRATE_LEGACY", "").lower() in {"1", "true", "yes"}:
        with write_lock():
            backup = backup_db()
            if backup:
                print(f"backup: {backup}")
            print("migration:", migrate_all())
    print("cache:", refresh_all())
    print("reminders:", send_due_reminders())
