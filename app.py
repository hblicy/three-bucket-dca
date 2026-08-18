from __future__ import annotations

import threading
import uuid
from time import monotonic

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from dca_tracker import cache, portfolio
from dca_tracker.config import FRED_API_KEY, HOST, PORT, READ_TOKEN, ROOT, WEWORK_BOT_WEBHOOK, WRITE_TOKEN
from dca_tracker.db import backup_db, connect, init_db, rows_to_dicts, write_lock
from dca_tracker.migration import migrate_all
from dca_tracker.portfolio import VALID_ASSETS, VALID_BUCKETS


WEB_DIR = ROOT / "web"

app = FastAPI(title="DCA Tracker", version="0.1.0")
app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")

_WRITE_ATTEMPTS: dict[str, list[float]] = {}
_WRITE_LIMIT = 30
_WRITE_WINDOW_SECONDS = 60


class TransactionIn(BaseModel):
    date: str
    bucket: str
    asset: str
    side: str = Field(default="buy", pattern="^(buy|sell)$")
    amount_usd: float = Field(gt=0)
    price: float = Field(gt=0)
    shares: float | None = Field(default=None, gt=0)
    note: str = ""

    @field_validator("bucket")
    @classmethod
    def validate_bucket(cls, value: str) -> str:
        value = value.strip().upper()
        if value not in VALID_BUCKETS:
            raise ValueError(f"unknown bucket: {value}")
        return value

    @field_validator("asset")
    @classmethod
    def validate_asset(cls, value: str) -> str:
        value = value.strip().upper()
        if value not in VALID_ASSETS:
            raise ValueError(f"unknown asset: {value}")
        return value


def require_write_token(token: str | None) -> None:
    if not WRITE_TOKEN:
        raise HTTPException(status_code=500, detail="DCA_WRITE_TOKEN 未配置")
    if token != WRITE_TOKEN:
        raise HTTPException(status_code=403, detail="写操作 token 错误")


def require_read_token(token: str | None) -> None:
    if READ_TOKEN and token != READ_TOKEN:
        raise HTTPException(status_code=403, detail="读操作 token 错误")


def rate_limit_write(request: Request) -> None:
    client = _client_key(request)
    now = monotonic()
    recent = [t for t in _WRITE_ATTEMPTS.get(client, []) if now - t < _WRITE_WINDOW_SECONDS]
    if len(recent) >= _WRITE_LIMIT:
        _WRITE_ATTEMPTS[client] = recent
        raise HTTPException(status_code=429, detail="写操作过于频繁，请稍后再试")
    recent.append(now)
    _WRITE_ATTEMPTS[client] = recent


def _client_key(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"


def _asset_filter_sql() -> tuple[str, tuple[str, ...]]:
    assets = tuple(sorted(VALID_ASSETS))
    placeholders = ",".join("?" for _ in assets)
    return f"UPPER(asset) IN ({placeholders})", assets


def refresh_cache_later() -> None:
    def _refresh() -> None:
        try:
            cache.refresh_all()
        except Exception as exc:
            print(f"background cache refresh failed: {exc}")

    threading.Thread(target=_refresh, daemon=True).start()


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/transactions")
def transactions_page():
    return FileResponse(WEB_DIR / "transactions.html")


@app.get("/settings")
def settings_page():
    return FileResponse(WEB_DIR / "settings.html")


@app.get("/rules")
def rules_page():
    return FileResponse(WEB_DIR / "rules.html")


@app.get("/api/health")
def health(x_dca_read_token: str | None = Header(default=None)):
    require_read_token(x_dca_read_token)
    return {"ok": True, "module": "DCA Tracker", "version": app.version}


@app.get("/api/dashboard")
def dashboard(x_dca_read_token: str | None = Header(default=None), refresh: bool = False):
    require_read_token(x_dca_read_token)
    if not refresh:
        cached = cache.get_cache(cache.DASHBOARD_KEY)
        if cached:
            return cached
    data = cache.build_dashboard()
    cache.set_cache(cache.DASHBOARD_KEY, data)
    data["cache"] = {"key": cache.DASHBOARD_KEY, "hit": False}
    return data


@app.get("/api/performance")
def performance_api(scope: str = "all", x_dca_read_token: str | None = Header(default=None), refresh: bool = False):
    require_read_token(x_dca_read_token)
    key = cache.performance_key(scope)
    if not refresh:
        cached = cache.get_cache(key)
        if cached:
            return cached
    try:
        data = cache.build_performance(scope)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    cache.set_cache(key, data)
    data["cache"] = {"key": key, "hit": False}
    return data


@app.get("/api/transactions")
def list_transactions(
    limit: int = 10,
    offset: int = 0,
    asset: str | None = None,
    bucket: str | None = None,
    side: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    q: str | None = None,
    x_dca_read_token: str | None = Header(default=None),
):
    require_read_token(x_dca_read_token)
    limit = max(1, min(limit, 1000))
    offset = max(0, offset)
    asset_filter, assets = _asset_filter_sql()
    where_parts = [asset_filter]
    params: list[str] = list(assets)
    if asset:
        asset = asset.strip().upper()
        if asset not in VALID_ASSETS:
            raise HTTPException(status_code=400, detail=f"未知资产: {asset}")
        where_parts.append("UPPER(asset) = ?")
        params.append(asset)
    if bucket:
        bucket = bucket.strip().upper()
        if bucket not in VALID_BUCKETS:
            raise HTTPException(status_code=400, detail=f"未知仓位: {bucket}")
        where_parts.append("bucket = ?")
        params.append(bucket)
    if side:
        side = side.strip().lower()
        if side not in {"buy", "sell"}:
            raise HTTPException(status_code=400, detail=f"未知方向: {side}")
        where_parts.append("side = ?")
        params.append(side)
    if date_from:
        where_parts.append("date >= ?")
        params.append(date_from)
    if date_to:
        where_parts.append("date <= ?")
        params.append(date_to)
    if q:
        like = f"%{q.strip()}%"
        where_parts.append("(UPPER(asset) LIKE UPPER(?) OR bucket LIKE ? OR note LIKE ?)")
        params.extend([like, like, like])
    where_sql = " AND ".join(where_parts)
    with connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM transactions WHERE {where_sql}", tuple(params)).fetchone()[0]
        rows = conn.execute(
            f"""
            SELECT * FROM transactions
            WHERE {where_sql}
            ORDER BY date DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (*params, limit, offset),
        ).fetchall()
    return {"rows": rows_to_dicts(rows), "total": total, "limit": limit, "offset": offset}


@app.post("/api/transactions")
def add_transaction(item: TransactionIn, request: Request, x_dca_token: str | None = Header(default=None)):
    rate_limit_write(request)
    require_write_token(x_dca_token)
    init_db()
    with write_lock():
        backup_db()
        shares = item.shares if item.shares is not None else item.amount_usd / item.price
        if item.side == "sell" and shares > 0:
            shares = -shares
        with connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO transactions(date, bucket, asset, side, amount_usd, price, shares, note, source, legacy_id, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'manual', ?, CURRENT_TIMESTAMP)
                """,
                (
                    item.date,
                    item.bucket,
                    item.asset,
                    item.side,
                    item.amount_usd,
                    item.price,
                    shares,
                    item.note,
                    f"manual-{uuid.uuid4().hex}",
                ),
            )
            row = conn.execute("SELECT * FROM transactions WHERE id = ?", (cur.lastrowid,)).fetchone()
    refresh_cache_later()
    return dict(row)


@app.put("/api/transactions/{tx_id}")
def update_transaction(tx_id: int, item: TransactionIn, request: Request, x_dca_token: str | None = Header(default=None)):
    rate_limit_write(request)
    require_write_token(x_dca_token)
    init_db()
    with write_lock():
        backup_db()
        shares = item.shares if item.shares is not None else item.amount_usd / item.price
        if item.side == "sell" and shares > 0:
            shares = -shares
        with connect() as conn:
            conn.execute(
                """
                UPDATE transactions
                SET date=?, bucket=?, asset=?, side=?, amount_usd=?, price=?, shares=?, note=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (item.date, item.bucket, item.asset, item.side, item.amount_usd, item.price, shares, item.note, tx_id),
            )
            row = conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="交易记录不存在")
    refresh_cache_later()
    return dict(row)


@app.delete("/api/transactions/{tx_id}")
def delete_transaction(tx_id: int, request: Request, x_dca_token: str | None = Header(default=None)):
    rate_limit_write(request)
    require_write_token(x_dca_token)
    init_db()
    with write_lock():
        backup_db()
        with connect() as conn:
            cur = conn.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))
    refresh_cache_later()
    return {"deleted": cur.rowcount}


@app.post("/api/migrate")
def migrate(request: Request, x_dca_token: str | None = Header(default=None)):
    rate_limit_write(request)
    require_write_token(x_dca_token)
    with write_lock():
        backup_db()
        result = migrate_all()
    refresh_cache_later()
    return result


@app.get("/api/settings")
def settings(x_dca_read_token: str | None = Header(default=None)):
    require_read_token(x_dca_read_token)
    with connect() as conn:
        migration = rows_to_dicts(conn.execute("SELECT * FROM migration_state ORDER BY source").fetchall())
        cache_rows = rows_to_dicts(conn.execute("SELECT cache_key, updated_at FROM api_cache ORDER BY cache_key").fetchall())
    return {
        "write_token_configured": bool(WRITE_TOKEN),
        "read_token_configured": bool(READ_TOKEN),
        "fred_configured": bool(FRED_API_KEY),
        "wework_configured": bool(WEWORK_BOT_WEBHOOK),
        "db_path": "data/dca_tracker.db",
        "migration_state": migration,
        "cache_state": cache_rows,
        "buckets": portfolio.BUCKET_NAMES,
    }


@app.get("/api/rules")
def rules_api(x_dca_read_token: str | None = Header(default=None)):
    require_read_token(x_dca_read_token)
    return {
        "BTC": "每周一 16:00 北京时间；Proxy vFinal + AHR999 + Puell 生成 DCA Score，按历史分位决定 0/0.5/1/2/4 倍基础金额。",
        "CRCL": "每周二 16:00 北京时间；价格分位、MA60 偏离、90日回撤、基本面评分合成 DCA Score，决定 0/0.5/1/2 倍基础金额。",
        "US_INDEX": "月末美股收盘后计算；下一个美股交易日 16:00 北京时间推送。奇数月 VOO 稳投 1x，偶数月 QQQM 按纳指 PE 分层，跌破 10 月均线时降一级。",
    }


if __name__ == "__main__":
    uvicorn.run("app:app", host=HOST, port=PORT, reload=False)
