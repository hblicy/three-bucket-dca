from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from dca_tracker.prices import _confirmed_2028_halving_time


def main() -> None:
    halving_time = _confirmed_2028_halving_time()
    state = "尚未挖出" if halving_time is None else halving_time.isoformat()
    print(f"BTC 区块 1050000 状态：{state}")


if __name__ == "__main__":
    main()
