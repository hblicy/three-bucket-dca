from __future__ import annotations

import shutil
import sqlite3
import secrets
import threading
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import BACKUP_DIR, BACKUP_KEEP, BEIJING_TZ, DB_PATH

_THREAD_LOCK = threading.RLock()
_LOCK_STATE = threading.local()


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def write_lock():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_path = DB_PATH.with_suffix(".lock")
    depth = getattr(_LOCK_STATE, "depth", 0)
    if depth > 0:
        _LOCK_STATE.depth = depth + 1
        try:
            yield
        finally:
            _LOCK_STATE.depth -= 1
        return
    with _THREAD_LOCK:
        with lock_path.open("a+") as fh:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
                _LOCK_STATE.depth = 1
                try:
                    yield
                finally:
                    _LOCK_STATE.depth = 0
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                _LOCK_STATE.depth = 1
                try:
                    yield
                finally:
                    _LOCK_STATE.depth = 0
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                bucket TEXT NOT NULL,
                asset TEXT NOT NULL,
                side TEXT NOT NULL DEFAULT 'buy',
                amount_usd REAL NOT NULL,
                price REAL NOT NULL,
                shares REAL NOT NULL,
                note TEXT DEFAULT '',
                source TEXT DEFAULT 'manual',
                legacy_id TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source, legacy_id)
            );

            CREATE TABLE IF NOT EXISTS reminder_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bucket TEXT NOT NULL,
                asset TEXT NOT NULL,
                reminder_date TEXT NOT NULL,
                channel TEXT NOT NULL DEFAULT 'wework',
                sent_at TEXT NOT NULL,
                UNIQUE(bucket, asset, reminder_date, channel)
            );

            CREATE TABLE IF NOT EXISTS migration_state (
                source TEXT PRIMARY KEY,
                migrated_at TEXT NOT NULL,
                row_count INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS api_cache (
                cache_key TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )


def backup_db() -> Path | None:
    if not DB_PATH.exists():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(ZoneInfo(BEIJING_TZ)).strftime("%Y%m%d_%H%M%S")
    target = BACKUP_DIR / f"dca_tracker_{stamp}_{secrets.token_hex(4)}.db"
    with write_lock():
        with connect() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        shutil.copy2(DB_PATH, target)
        backups = sorted(BACKUP_DIR.glob("dca_tracker_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in backups[BACKUP_KEEP:]:
            old.unlink(missing_ok=True)
        return target


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    return [dict(row) for row in rows]
