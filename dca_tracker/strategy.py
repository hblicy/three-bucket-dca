from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from tempfile import TemporaryDirectory


STRATEGY_COST_URL = "https://www.strategy.com/ledger"
CHROME_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"


class StrategySourceError(RuntimeError):
    pass


class _NextDataParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("id") == "__NEXT_DATA__":
            self.active = True

    def handle_endtag(self, tag):
        if tag == "script":
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)


def parse_btc_cost(html: str) -> dict:
    parser = _NextDataParser()
    parser.feed(html)
    parser.close()
    if not parser.parts:
        raise ValueError("Strategy 账本缺少 __NEXT_DATA__，可能是页面改版或访问被拒")
    try:
        rows = json.loads("".join(parser.parts))["props"]["pageProps"]["bitcoinData"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError("Strategy 账本 bitcoinData 格式无效") from exc
    if not isinstance(rows, list) or not rows:
        raise ValueError("Strategy 账本没有买卖记录")
    dated_rows = []
    for row in rows:
        raw_date = row.get("date_of_purchase") if isinstance(row, dict) else None
        if not isinstance(raw_date, str):
            raise ValueError("Strategy 账本披露日期缺失")
        try:
            reported_date = date.fromisoformat(raw_date)
        except ValueError as exc:
            raise ValueError("Strategy 账本披露日期无效") from exc
        if reported_date > datetime.now(timezone.utc).date():
            raise ValueError("Strategy 账本披露日期在未来")
        dated_rows.append((reported_date, row))
    reported_date, latest = max(dated_rows, key=lambda item: item[0])
    value = latest.get("average_price")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Strategy BTC 累计持仓均价必须为有限正数")
    try:
        avg_cost = float(value)
    except OverflowError as exc:
        raise ValueError("Strategy BTC 累计持仓均价超出数值范围") from exc
    if not math.isfinite(avg_cost) or avg_cost <= 0:
        raise ValueError("Strategy BTC 累计持仓均价必须为有限正数")
    return {"avg_cost_usd": avg_cost, "as_of": reported_date.isoformat(), "source_url": STRATEGY_COST_URL}


def fetch_btc_cost() -> dict:
    chrome = next((found for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser") if (found := shutil.which(name))), None)
    if chrome is None and os.name == "nt":
        installed = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe"
        if installed.is_file():
            chrome = str(installed)
    if chrome is None:
        raise StrategySourceError("未找到 Chrome/Chromium；官方账本后台采集需要已安装的浏览器")
    with TemporaryDirectory(prefix="dca-strategy-chrome-", ignore_cleanup_errors=True) as profile:
        command = [chrome, "--headless=new", "--disable-gpu", "--no-first-run", f"--user-data-dir={profile}", f"--user-agent={CHROME_USER_AGENT}", "--dump-dom", STRATEGY_COST_URL]
        try:
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=90, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired as exc:
            raise StrategySourceError("Strategy 官方账本浏览器采集超时（90 秒）") from exc
        except OSError as exc:
            raise StrategySourceError(f"Chrome 无法启动：{exc}") from exc
    if result.returncode != 0:
        raise StrategySourceError(f"Chrome 退出码 {result.returncode}：{result.stderr.strip()[-1000:]}")
    return parse_btc_cost(result.stdout)
