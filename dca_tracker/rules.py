from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import requests

from . import prices
from .config import (
    BEIJING_TZ,
    BTC_BASE_AMOUNT,
    CRCL_BASE_AMOUNT,
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


CRCL_RISK_CACHE_TTL_SECONDS = 300
USDC_SUPPLY_MAX_AGE = timedelta(days=1)
USDC_HISTORY_URL = "https://stablecoins.llama.fi/stablecoincharts/all?stablecoin=2"
_crcl_risk_cache: dict[str, object] = {"data": None, "timestamp": 0.0}


def _now() -> datetime:
    return datetime.now(ZoneInfo(BEIJING_TZ))


def _validated_base_amount(value: object, name: str) -> float:
    try:
        amount = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{name} 必须是有限正数") from exc
    if not np.isfinite(amount) or amount <= 0:
        raise RuntimeError(f"{name} 必须是有限正数")
    return amount


def btc_decision() -> Decision:
    try:
        base_amount = _validated_base_amount(BTC_BASE_AMOUNT, "BTC_BASE_AMOUNT")
        df = prices.btc_indicators()
        latest = df.iloc[-1]
        required = ["Price", "proxy_z", "ahr999", "puell", "dca_score", "p10", "p25", "p50", "p75"]
        if not all(np.isfinite(float(latest[column])) for column in required):
            raise RuntimeError("BTC 最新指标不完整，暂停生成正式建议")
        score = float(latest["dca_score"])
        p10, p25, p50, p75 = [float(latest[x]) for x in ["p10", "p25", "p50", "p75"]]
        thresholds = {"p10": p10, "p25": p25, "p50": p50, "p75": p75}
        if score <= p10:
            multiplier = 4.0
        elif score <= p25:
            multiplier = 2.0
        elif score <= p50:
            multiplier = 1.0
        elif score <= p75:
            multiplier = 0.5
        else:
            multiplier = 0.0
        amount = base_amount * multiplier
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
                "base_amount": base_amount,
                "thresholds": thresholds,
            },
        )
    except Exception as exc:
        return _error_decision("BTC_CYCLE", "BTC：周期进攻仓", "BTC", "BTC 周期进攻仓", "每周一 16:00 北京时间", str(exc))


def _validate_usdc_source_date(value: object) -> str:
    try:
        source_date = datetime.fromisoformat(str(value)).date()
    except (TypeError, ValueError) as exc:
        raise RuntimeError("USDC 流通量源数据日期无效") from exc
    source_age = datetime.now(timezone.utc).date() - source_date
    if source_age < timedelta(0):
        raise RuntimeError("USDC 流通量源数据日期在未来")
    if source_age > USDC_SUPPLY_MAX_AGE:
        raise RuntimeError("USDC 流通量源数据已过期")
    return source_date.isoformat()


def _crcl_usdc_risk() -> dict:
    cached = _crcl_risk_cache.get("data")
    age = time.time() - float(_crcl_risk_cache.get("timestamp") or 0)
    if isinstance(cached, dict) and 0 <= age < CRCL_RISK_CACHE_TTL_SECONDS:
        try:
            _validate_usdc_source_date(cached.get("source_date"))
        except RuntimeError:
            pass
        else:
            return cached

    resp = requests.get(USDC_HISTORY_URL, timeout=10)
    resp.raise_for_status()
    history = resp.json()
    if not isinstance(history, list) or len(history) < 2:
        raise RuntimeError("DefiLlama 未返回足够的 USDC 历史流通量")

    previous_row, current_row = history[-2:]
    try:
        previous_date = datetime.fromtimestamp(int(previous_row["date"]), timezone.utc)
        current_date = datetime.fromtimestamp(int(current_row["date"]), timezone.utc)
        previous_day = float(previous_row["totalCirculating"]["peggedUSD"])
        current = float(current_row["totalCirculating"]["peggedUSD"])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError("USDC 流通量数据格式异常") from exc
    if not all(np.isfinite(value) and value > 0 for value in (current, previous_day)):
        raise RuntimeError("USDC 流通量数据无效")
    if current_date - previous_date != timedelta(days=1):
        raise RuntimeError("USDC 流通量源数据不是连续 24 小时")

    source_date = _validate_usdc_source_date(current_date.date().isoformat())

    drop_pct = (previous_day - current) / previous_day * 100
    if not np.isfinite(drop_pct):
        raise RuntimeError("USDC 24 小时流通量变化无效")
    data = {
        "drop_24h_pct": drop_pct,
        "alert": drop_pct > 5.0,
        "source": "DefiLlama",
        "source_date": source_date,
    }
    _crcl_risk_cache["data"] = data
    _crcl_risk_cache["timestamp"] = time.time()
    return data


def _crcl_multiplier(score: float) -> float:
    if score <= 25:
        return 2.0
    if score <= 45:
        return 1.0
    if score <= 65:
        return 0.5
    return 0.0


def crcl_decision() -> Decision:
    try:
        base_amount = _validated_base_amount(CRCL_BASE_AMOUNT, "CRCL_BASE_AMOUNT")
        hist = prices.history("CRCL", period="1y")
        close = hist["Close"].replace([np.inf, -np.inf], np.nan).dropna()
        if len(close) < 90 or (close <= 0).any():
            raise RuntimeError("CRCL 有效价格不足 90 个交易日，暂停生成正式建议")
        current = float(close.iloc[-1])
        percentile = float((close.values < current).sum() / len(close) * 100)
        ma60 = float(close.tail(60).mean())
        ma_dev = ((current - ma60) / ma60) * 100 if ma60 else 0.0
        high_90d = float(close.tail(90).max())
        drawdown = ((high_90d - current) / high_90d) * 100 if high_90d else 0.0
        ma_mapped = float(np.interp(ma_dev, [-30, 30], [0, 25]))
        dd_mapped = float(np.interp(drawdown, [0, 60], [20, 0]))
        technical_raw = float(np.clip(percentile * 0.40 + ma_mapped + dd_mapped, 0, 85))
        score = technical_raw / 85.0 * 100.0
        multiplier = _crcl_multiplier(score)
        usdc_risk = _crcl_usdc_risk()
        if usdc_risk["alert"]:
            multiplier = 0.0
        amount = base_amount * multiplier
        if usdc_risk["alert"]:
            risk_note = f"USDC 24 小时流通量下降 {usdc_risk['drop_24h_pct']:.1f}%，触发硬暂停。"
        elif usdc_risk["drop_24h_pct"] > 0:
            risk_note = f"USDC 24 小时流通量下降 {usdc_risk['drop_24h_pct']:.1f}%，未触发暂停。"
        elif usdc_risk["drop_24h_pct"] < 0:
            risk_note = f"USDC 24 小时流通量增加 {-usdc_risk['drop_24h_pct']:.1f}%，未触发暂停。"
        else:
            risk_note = "USDC 24 小时流通量持平 0.0%，未触发暂停。"
        return Decision(
            bucket="CRCL_GROWTH",
            bucket_name="CRCL：加密金融成长仓",
            asset="CRCL",
            title="CRCL 加密金融成长仓",
            status="buy" if amount > 0 else "pause",
            action="强提醒加速" if multiplier == 2 else "正常定投" if multiplier == 1 else "减量定投" if multiplier == 0.5 else "暂停定投",
            recommended_amount=round(amount, 2),
            price=current,
            reason=f"CRCL DCA Score {score:.1f}，价格分位 {percentile:.1f}%，MA60 偏离 {ma_dev:.1f}%，距90日高点回撤 {drawdown:.1f}%。{risk_note}",
            next_window="每周二 16:00 北京时间",
            schedule="weekly_tuesday_1600",
            metrics={
                "score": score,
                "technical_raw": technical_raw,
                "multiplier": multiplier,
                "base_amount": base_amount,
                "price_percentile": percentile,
                "ma60": ma60,
                "ma60_deviation_pct": ma_dev,
                "drawdown_90d_pct": drawdown,
                "usdc_risk": usdc_risk,
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
            base_amount = _validated_base_amount(
                US_INDEX_MONTHLY_AMOUNT, "US_INDEX_MONTHLY_AMOUNT"
            )
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
                pe_source_note = (
                    "上次有效 trailingPE"
                    if pe_metric.get("source") == "last_valid_trailingPE"
                    else f"{pe_metric['symbol']} trailingPE"
                )
                reason = (
                    f"{now.month} 月为{month_type}月，本月目标资产为 QQQM；"
                    f"纳指 PE {pe:.1f}（{pe_label}，来源：{pe_source_note}），QQQM {trend_note}，买入系数 {multiplier:g}x。"
                )
            amount = base_amount * multiplier
            metric["multiplier"] = multiplier
            metric["base_amount"] = base_amount
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
