from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from dca_tracker.db import backup_db


def create_required_backup() -> Path:
    backup = backup_db()
    if backup is None or not backup.is_file():
        raise RuntimeError("更新已中止：未生成有效的 SQLite 备份")
    return backup.resolve()


def main() -> None:
    print(create_required_backup())


if __name__ == "__main__":
    main()
