from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_env(path: Path | None = None) -> None:
    env_path = path or ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env()

DB_PATH = ROOT / os.environ.get("DCA_DB_PATH", "data/dca_tracker.db")
BACKUP_DIR = ROOT / "backups"
BACKUP_KEEP = int(os.environ.get("DCA_BACKUP_KEEP", "30"))
WRITE_TOKEN = os.environ.get("DCA_WRITE_TOKEN", "")
READ_TOKEN = os.environ.get("DCA_READ_TOKEN", "")
WEWORK_BOT_WEBHOOK = os.environ.get("WEWORK_BOT_WEBHOOK", "")
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")
HOST = os.environ.get("DCA_HOST", "127.0.0.1")
PORT = int(os.environ.get("DCA_PORT", "8020"))

BTC_BASE_AMOUNT = float(os.environ.get("BTC_BASE_AMOUNT", "100"))
CRCL_BASE_AMOUNT = float(os.environ.get("CRCL_BASE_AMOUNT", "100"))
US_INDEX_MONTHLY_AMOUNT = float(os.environ.get("US_INDEX_MONTHLY_AMOUNT", "500"))
CRCL_FUNDAMENTALS_CACHE_TTL = int(os.environ.get("CRCL_FUNDAMENTALS_CACHE_TTL", "3600"))

BEIJING_TZ = "Asia/Shanghai"

LEGACY_PATHS = {
    "btc": Path(os.environ.get("LEGACY_BTC_DB", ROOT.parent / "BTC_DDCA_old999" / "data" / "investments.db")),
    "crcl": Path(os.environ.get("LEGACY_CRCL_DB", ROOT.parent / "CRCL_DCA" / "crcl_investments.db")),
    "us_index": Path(os.environ.get("LEGACY_US_INDEX_DB", ROOT.parent / "US_INDEX_DCA" / "index_dca.db")),
}
