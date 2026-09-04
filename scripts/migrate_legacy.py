from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from dca_tracker.migration import migrate_all
from dca_tracker.db import backup_db, write_lock


def main() -> None:
    with write_lock():
        backup = backup_db()
        if backup is not None:
            print(f"backup: {backup}")
        result = migrate_all()
    for source, stats in result.items():
        print(f"{source}: {stats['scanned']} row(s) scanned, {stats['imported']} row(s) imported, status={stats['status']}")


if __name__ == "__main__":
    main()
