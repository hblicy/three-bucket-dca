from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import requests

from . import prices
from .config import (
    BEIJING_TZ,
    BTC_BASE_AMOUNT,
    CRCL_BASE_AMOUNT,
    CRCL_FUNDAMENTALS_CACHE_TTL,
    FRED_API_KEY,
    US_INDEX_MONTHLY_AMOUNT,
)


@dataclass
class Decision:
    bucket: str
    bucket_name: str
    asset: str
    title: str
    status: str
    action: str
    recommended_amount: float
    price: float | None
    reason: str
    next_window: str
    schedule: str
    metrics: dict
    data_ok: bool = True


_fundamentals_cache: dict[str, object] = {"data": None, "timestamp": 0.0}


def _now() -> datetime:
    return datetime.now(ZoneInfo(BEIJING_TZ))


def btc_decision() -> Decision:
    try:
        df = prices.btc_indicators()
        latest = df.dropna(subset=["dca_score"]).iloc[-1]
        score = float(latest["dca_score"])
        p10, p25, p50, p75 = [float(latest[x]) for x in ["p10", "p25", "p50", "p75"]]
        thresholds = {"p10": p10, "p25": p25, "p50": p50, "p75": p75}
        if np.isnan(p10):
            multiplier = 0.0
        elif score <= p10:
            multiplier = 4.0
        elif score <= p25:
            multiplier = 2.0
        elif score <= p50:
            multiplier = 1.0
        elif score <= p75:
            multiplier = 0.5
        else:
            multiplier = 0.0
        amount = BTC_BASE_AMOUNT * multiplier
        action_map = {0.0: "暂停定投", 0.5: "轻仓定投", 1.0: "正常定投", 2.0: "加速定投", 4.0: "重仓定投"}
        return Decision(
            bucket="BTC_CYCLE",
            bucket_name="BTC：周期进攻仓",
            asset="BTC",
            title="BTC 周期进攻仓",
            status="buy" if amount > 0 else "pause",
            action=action_map.get(multiplier, "观察"),
            recommended_amount=round(amount, 2),
            price=float(latest["Price"]),
            reason=f"DCA Score {score:.1f}，分位阈值 P10/P25/P50/P75={p10:.1f}/{p25:.1f}/{p50:.1f}/{p75:.1f}，倍率 {multiplier:g}x。",
            next_window="每周一 16:00 北京时间",
            schedule="weekly_monday_1600",
            metrics={
                "dca_score": score,
                "multiplier": multiplier,
                "base_amount": BTC_BASE_AMOUNT,
                "thresholds": {key: None if np.isnan(value) else value for key, value in thresholds.items()},
            },
        )
    except Exception as exc:
        return _error_decision("BTC_CYCLE", "BTC：周期进攻仓", "BTC", "BTC 周期进攻仓", "每周一 16:00 北京时间", str(exc))


def _latest_fred_value(series_id: str) -> float | None:
    if not FRED_API_KEY:
        return None
    url = (
        "https://api.stlouisfed.org/fred/series/observations"
        f"?series_id={series_id}&api_key={FRED_API_KEY}&file_type=json&sort_order=desc&limit=1"
    )
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            return None
        observations = (resp.json().get("observations") or [])
        if not observations:
            return None
        raw_value = observations[0].get("value")
        return None if raw_value in (None, ".") else float(raw_value)
    except Exception:
        return None


def _crcl_fundamentals() -> dict:
    cached = _fundamentals_cache.get("data")
    age = time.time() - float(_fundamentals_cache.get("timestamp") or 0)
    if cached is not None and age < CRCL_FUNDAMENTALS_CACHE_TTL:
        return cached  # type: ignore[return-value]

    data = {
        "live": False,
        "note": "基本面接口获取失败，使用中性兜底分 7.5/15。",
        "score": 7.5,
        "scale_str": "--",
        "profit_str": "--",
        "share_str": "--",
        "macro_str": "--",
        "risk_str": "--",
    }
    try:
        resp = requests.get("https://stablecoins.llama.fi/stablecoins", timeout=10)
        resp.raise_for_status()
        llama_data = resp.json()

        usdt_mc = usdc_mc = dai_mc = 0.0
        usdc_1m_ago = usdc_24h_mc = 0.0
        for coin in llama_data.get("peggedAssets", []):
            symbol = (coin.get("symbol") or "").upper()
            current = float(coin.get("circulating", {}).get("peggedUSD") or 0)
            if symbol == "USDT":
                usdt_mc = current
            elif symbol == "USDC":
                usdc_mc = current
                usdc_1m_ago = float(coin.get("circulatingPrevMonth", {}).get("peggedUSD") or 0)
                usdc_24h_mc = float(coin.get("circulatingPrevDay", {}).get("peggedUSD") or 0)
            elif symbol == "DAI":
                dai_mc = current

        scale_growth = ((usdc_mc - usdc_1m_ago) / usdc_1m_ago * 100) if usdc_1m_ago > 0 else 0.0
        risk_24h_drop = ((usdc_24h_mc - usdc_mc) / usdc_24h_mc * 100) if usdc_24h_mc > 0 else 0.0
        stable_sum = usdt_mc + usdc_mc + dai_mc
        share_pct = (usdc_mc / stable_sum * 100) if stable_sum > 0 else 0.0

        tbill_rate = _latest_fred_value("DGS1MO")
        fed_rate = _latest_fred_value("FEDFUNDS")

        score = 0.0
        if scale_growth >= 3.0:
            score += 4.0
            data["scale_str"] = f"+{scale_growth:.1f}%（良好）"
        elif scale_growth >= 0.0:
            score += 2.0
            data["scale_str"] = f"+{scale_growth:.1f}%（中性）"
        else:
            data["scale_str"] = f"{scale_growth:.1f}%（收缩）"

        if tbill_rate is None:
            data["profit_str"] = "未配置 FRED（不加分）"
        elif tbill_rate >= 4.5:
            score += 4.0
            data["profit_str"] = f"{tbill_rate:.2f}%（良好）"
        elif tbill_rate >= 4.0:
            score += 2.0
            data["profit_str"] = f"{tbill_rate:.2f}%（正常）"
        else:
            data["profit_str"] = f"{tbill_rate:.2f}%（偏弱）"

        if share_pct >= 22.0:
            score += 3.0
            data["share_str"] = f"{share_pct:.1f}%（扩张）"
        elif share_pct >= 20.0:
            score += 1.5
            data["share_str"] = f"{share_pct:.1f}%（持平）"
        else:
            data["share_str"] = f"{share_pct:.1f}%（流失）"

        if fed_rate is None:
            data["macro_str"] = "未配置 FRED（不加分）"
        elif fed_rate >= 5.0:
            score += 4.0
            data["macro_str"] = f"{fed_rate:.2f}%（高位）"
        elif fed_rate >= 4.0:
            score += 2.0
            data["macro_str"] = f"{fed_rate:.2f}%（中高位）"
        else:
            data["macro_str"] = f"{fed_rate:.2f}%（低位）"

        if risk_24h_drop > 5.0:
            score = 0.0
            data["risk_str"] = f"跌 {risk_24h_drop:.1f}%（警报）"
        else:
            data["risk_str"] = "无异常（稳定）"

        data["score"] = score
        data["live"] = True
        data["note"] = "基本面数据来自 DefiLlama 和 FRED；FRED 未配置或不可用时，利率分项按保守口径不加分。"
    except Exception:
        pass

    _fundamentals_cache["data"] = data
    _fundamentals_cache["timestamp"] = time.time()
    return data


def crcl_decision() -> Decision:
    try:
        hist = prices.history("CRCL", period="1y")
        close = hist["Close"].dropna()
        current = float(close.iloc[-1])
        percentile = float((close.values < current).sum() / len(close) * 100)
        ma60 = float(close.rolling(window=min(60, len(close))).mean().iloc[-1])
        ma_dev = ((current - ma60) / ma60) * 100 if ma60 else 0.0
        high_90d = float(close.tail(90).max())
        drawdown = ((high_90d - current) / high_90d) * 100 if high_90d else 0.0
        fundamentals = _crcl_fundamentals()
        fund_score = float(fundamentals["score"])
        ma_mapped = float(np.interp(ma_dev, [-30, 30], [0, 25]))
        dd_mapped = float(np.interp(drawdown, [0, 60], [20, 0]))
        score = float(np.clip(percentile * 0.40 + ma_mapped + dd_mapped + fund_score, 0, 100))
        multiplier = 2.0 if score <= 25 else 1.0 if score <= 45 else 0.5 if score <= 65 else 0.0
        amount = CRCL_BASE_AMOUNT * multiplier
        return Decision(
            bucket="CRCL_GROWTH",
            bucket_name="CRCL：加密金融成长仓",
            asset="CRCL",
            title="CRCL 加密金融成长仓",
            status="buy" if amount > 0 else "pause",
            action="强提醒加速" if multiplier == 2 else "正常定投" if multiplier == 1 else "减量定投" if multiplier == 0.5 else "暂停定投",
            recommended_amount=round(amount, 2),
            price=current,
            reason=f"CRCL DCA Score {score:.1f}，价格分位 {percentile:.1f}%，MA60 偏离 {ma_dev:.1f}%，90日回撤 {drawdown:.1f}%。",
            next_window="每周二 16:00 北京时间",
            schedule="weekly_tuesday_1600",
            metrics={
                "score": score,
                "multiplier": multiplier,
                "base_amount": CRCL_BASE_AMOUNT,
                "fund_score": fund_score,
                "fundamentals": fundamentals,
            },
        )
    except Exception as exc:
        return _error_decision("CRCL_GROWTH", "CRCL：加密金融成长仓", "CRCL", "CRCL 加密金融成长仓", "每周二 16:00 北京时间", str(exc))


def us_index_decisions() -> list[Decision]:
    now = _now()
    target = "VOO" if now.month % 2 == 1 else "QQQM"
    other = "QQQM" if target == "VOO" else "VOO"
    month_type = "奇数" if now.month % 2 == 1 else "偶数"
    next_window = "月末美股收盘后计算；下一个美股交易日 16:00 北京时间推送"
    schedule = "month_end_next_us_trading_day_1600"
    off_cycle_reason = f"{now.month} 月为{month_type}月，本月目标资产为 {target}，{other} 为非本月目标资产。"
    out = []
    for symbol in [target, other]:
        try:
            metric = prices.latest_price(symbol)
            is_target = symbol == target
            multiplier = 0.0
            action = "非本月目标"
            reason = off_cycle_reason
            if is_target and symbol == "VOO":
                multiplier = 1.0
                action = "稳定定投"
                reason = f"{now.month} 月为{month_type}月，本月目标资产为 VOO；VOO 作为宽基核心，按基准金额 1x 买入。"
            elif is_target and symbol == "QQQM":
                pe_metric = prices.nasdaq_pe()
                trend = prices.monthly_trend("QQQM", months=10)
                pe = float(pe_metric["pe"])
                if pe >= 35:
                    multiplier = 0.5
                    pe_label = "估值偏贵"
                elif pe <= 25:
                    multiplier = 2.0
                    pe_label = "估值便宜"
                else:
                    multiplier = 1.0
                    pe_label = "估值正常"
                if trend["below_ma10m"]:
                    multiplier = {2.0: 1.0, 1.0: 0.5, 0.5: 0.25}[multiplier]
                    trend_note = "低于10个月均线，买入系数降一级"
                else:
                    trend_note = "高于10个月均线，按 PE 分层执行"
                action_map = {2.0: "估值加倍买入", 1.0: "正常定投", 0.5: "降速定投", 0.25: "小额定投"}
                action = action_map.get(multiplier, "观察")
                metric.update({
                    "nasdaq_pe": pe,
                    "nasdaq_pe_symbol": pe_metric["symbol"],
                    "nasdaq_pe_field": pe_metric["field"],
                    "nasdaq_pe_source": pe_metric.get("source", "live"),
                    "nasdaq_forward_pe": pe_metric.get("forward_pe"),
                    "nasdaq_pe_quotes": pe_metric.get("quotes", {}),
                    "qqqm_monthly_close": trend["monthly_close"],
                    "qqqm_ma10m": trend["ma10m"],
                    "qqqm_below_ma10m": trend["below_ma10m"],
                    "qqqm_ma10m_distance_pct": trend["ma10m_distance_pct"],
                })
                cash_note = "未用额度留作现金/短债池。" if multiplier < 1 else ""
                pe_source_note = (
                    "上次有效 trailingPE"
                    if pe_metric.get("source") == "last_valid_trailingPE"
                    else f"{pe_metric['symbol']} trailingPE"
                )
                reason = (
                    f"{now.month} 月为{month_type}月，本月目标资产为 QQQM；"
                    f"纳指 PE {pe:.1f}（{pe_label}，来源：{pe_source_note}），QQQM {trend_note}，买入系数 {multiplier:g}x。"
                    f"{cash_note}"
                )
            amount = US_INDEX_MONTHLY_AMOUNT * multiplier
            metric["multiplier"] = multiplier
            metric["base_amount"] = US_INDEX_MONTHLY_AMOUNT
            out.append(Decision(
                bucket="US_INDEX_CORE",
                bucket_name="QQQM + VOO：美股长期底仓",
                asset=symbol,
                title=f"{symbol} 美股长期底仓",
                status="buy" if amount > 0 else "off_cycle",
                action=action,
                recommended_amount=round(amount, 2),
                price=metric["price"],
                reason=reason,
                next_window=next_window,
                schedule=schedule,
                metrics=metric,
            ))
        except Exception as exc:
            out.append(_error_decision("US_INDEX_CORE", "QQQM + VOO：美股长期底仓", symbol, f"{symbol} 美股长期底仓", next_window, str(exc)))
    return out


def _error_decision(bucket: str, bucket_name: str, asset: str, title: str, window: str, error: str) -> Decision:
    return Decision(bucket, bucket_name, asset, title, "error", "数据获取失败", 0.0, None, error, window, "unknown", {"error": error}, False)


def all_decisions() -> list[Decision]:
    return [btc_decision(), crcl_decision(), *us_index_decisions()]
