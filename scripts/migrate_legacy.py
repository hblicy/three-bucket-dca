from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from dca_tracker.migration import migrate_all
from dca_tracker.db import write_lock


if __name__ == "__main__":
    with write_lock():
        result = migrate_all()
    for source, stats in result.items():
        print(f"{source}: {stats['scanned']} row(s) scanned, {stats['imported']} row(s) imported, status={stats['status']}")
