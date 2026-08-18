from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from .config import LEGACY_PATHS
from .db import connect, init_db, write_lock


def _legacy_conn(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row
    return conn


def _empty_stats() -> dict[str, int | str]:
    return {"scanned": 0, "imported": 0, "status": "missing_source"}


def migrate_btc() -> dict[str, int | str]:
    src = _legacy_conn(LEGACY_PATHS["btc"])
    if src is None:
        return _empty_stats()
    rows = src.execute("SELECT id, date, amount, price, btc_bought, notes FROM investments ORDER BY id").fetchall()
    with write_lock(), connect() as dst:
        dst.execute("DELETE FROM transactions WHERE source = 'BTC_DDCA_old999'")
        count = 0
        for row in rows:
            cur = dst.execute(
                """
                INSERT OR IGNORE INTO transactions
                (date, bucket, asset, side, amount_usd, price, shares, note, source, legacy_id)
                VALUES (?, ?, ?, 'buy', ?, ?, ?, ?, 'BTC_DDCA_old999', ?)
                """,
                (
                    row["date"],
                    "BTC_CYCLE",
                    "BTC",
                    float(row["amount"]),
                    float(row["price"]),
                    float(row["btc_bought"]),
                    row["notes"] or "",
                    str(row["id"]),
                ),
            )
            count += cur.rowcount
        dst.execute(
            "INSERT OR REPLACE INTO migration_state(source, migrated_at, row_count) VALUES (?, ?, ?)",
            ("BTC_DDCA_old999", datetime.utcnow().isoformat(timespec="seconds") + "Z", len(rows)),
        )
    return {"scanned": len(rows), "imported": count, "status": "ok"}


def migrate_crcl() -> dict[str, int | str]:
    src = _legacy_conn(LEGACY_PATHS["crcl"])
    if src is None:
        return _empty_stats()
    rows = src.execute("SELECT id, type, date, amount_usd, price, shares, notes FROM investments ORDER BY id").fetchall()
    with write_lock(), connect() as dst:
        dst.execute("DELETE FROM transactions WHERE source = 'CRCL_DCA'")
        count = 0
        for row in rows:
            side = row["type"] or ("sell" if float(row["shares"]) < 0 else "buy")
            cur = dst.execute(
                """
                INSERT OR IGNORE INTO transactions
                (date, bucket, asset, side, amount_usd, price, shares, note, source, legacy_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'CRCL_DCA', ?)
                """,
                (
                    row["date"],
                    "CRCL_GROWTH",
                    "CRCL",
                    side,
                    float(row["amount_usd"]),
                    float(row["price"]),
                    float(row["shares"]),
                    row["notes"] or "",
                    str(row["id"]),
                ),
            )
            count += cur.rowcount
        dst.execute(
            "INSERT OR REPLACE INTO migration_state(source, migrated_at, row_count) VALUES (?, ?, ?)",
            ("CRCL_DCA", datetime.utcnow().isoformat(timespec="seconds") + "Z", len(rows)),
        )
    return {"scanned": len(rows), "imported": count, "status": "ok"}


def migrate_us_index() -> dict[str, int | str]:
    src = _legacy_conn(LEGACY_PATHS["us_index"])
    if src is None:
        return _empty_stats()
    rows = src.execute("SELECT id, date, symbol, amount_usd, price, shares, note FROM investments ORDER BY id").fetchall()
    with write_lock(), connect() as dst:
        dst.execute("DELETE FROM transactions WHERE source = 'US_INDEX_DCA'")
        count = 0
        for row in rows:
            cur = dst.execute(
                """
                INSERT OR IGNORE INTO transactions
                (date, bucket, asset, side, amount_usd, price, shares, note, source, legacy_id)
                VALUES (?, ?, ?, 'buy', ?, ?, ?, ?, 'US_INDEX_DCA', ?)
                """,
                (
                    row["date"],
                    "US_INDEX_CORE",
                    row["symbol"].upper(),
                    float(row["amount_usd"]),
                    float(row["price"]),
                    float(row["shares"]),
                    row["note"] or "",
                    str(row["id"]),
                ),
            )
            count += cur.rowcount
        dst.execute(
            "INSERT OR REPLACE INTO migration_state(source, migrated_at, row_count) VALUES (?, ?, ?)",
            ("US_INDEX_DCA", datetime.utcnow().isoformat(timespec="seconds") + "Z", len(rows)),
        )
    return {"scanned": len(rows), "imported": count, "status": "ok"}


def migrate_all() -> dict:
    init_db()
    return {
        "BTC_DDCA_old999": migrate_btc(),
        "CRCL_DCA": migrate_crcl(),
        "US_INDEX_DCA": migrate_us_index(),
    }
