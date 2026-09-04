from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

from dca_tracker import prices, rules
from dca_tracker.notify import is_us_index_signal_due


class UsIndexStrategyTest(unittest.TestCase):
    def qqqm_decision(self, pe: float, below_ma10m: bool):
        now = datetime(2026, 8, 15, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        monthly_close = 99.0 if below_ma10m else 101.0
        trend = {
            "monthly_close": monthly_close,
            "ma10m": 100.0,
            "below_ma10m": below_ma10m,
            "ma10m_distance_pct": monthly_close - 100.0,
        }
        with (
            patch.object(rules, "_now", return_value=now),
            patch.object(rules.prices, "latest_price", side_effect=lambda symbol: {"price": 100.0}),
            patch.object(rules.prices, "nasdaq_pe", return_value={"pe": pe, "symbol": "QQQ", "field": "trailingPE"}),
            patch.object(rules.prices, "monthly_trend", return_value=trend),
        ):
            return rules.us_index_decisions()[0]

    def test_pe_35_enters_high_valuation_bucket(self):
        decision = self.qqqm_decision(35.0, below_ma10m=False)
        self.assertEqual(decision.asset, "QQQM")
        self.assertEqual(decision.recommended_amount, 250.0)
        self.assertEqual(decision.metrics["multiplier"], 0.5)

    def test_pe_25_enters_low_valuation_bucket(self):
        decision = self.qqqm_decision(25.0, below_ma10m=False)
        self.assertEqual(decision.asset, "QQQM")
        self.assertEqual(decision.recommended_amount, 1000.0)
        self.assertEqual(decision.metrics["multiplier"], 2.0)

    def test_close_equal_ma10m_is_not_below_trend(self):
        index = pd.date_range("2025-01-31", periods=12, freq="M")
        data = pd.DataFrame({"Close": [100.0] * len(index)}, index=index)
        with patch.object(prices, "history", return_value=data):
            trend = prices.monthly_trend("QQQM")
        self.assertFalse(trend["below_ma10m"])

    def test_voo_invalid_close_produces_no_formal_advice(self):
        now = datetime(2026, 9, 15, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        for invalid_close in (float("inf"), float("-inf"), 0.0, -1.0):
            with self.subTest(invalid_close=invalid_close):
                index = pd.date_range("2026-09-01", periods=2, freq="D")
                invalid_data = pd.DataFrame({"Close": [100.0, invalid_close]}, index=index)
                valid_data = pd.DataFrame({"Close": [100.0, 101.0]}, index=index)
                with (
                    patch.object(rules, "_now", return_value=now),
                    patch.object(prices, "history", side_effect=[invalid_data, valid_data]),
                ):
                    decision = rules.us_index_decisions()[0]
                self.assertEqual(decision.asset, "VOO")
                self.assertFalse(decision.data_ok)
                self.assertEqual(decision.status, "error")
                self.assertEqual(decision.recommended_amount, 0.0)

    def test_invalid_us_index_base_amount_produces_no_formal_advice(self):
        now = datetime(2026, 9, 15, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        for invalid_amount in (float("nan"), float("inf"), 0.0, -500.0):
            with self.subTest(invalid_amount=invalid_amount):
                with (
                    patch.object(rules, "_now", return_value=now),
                    patch.object(rules, "US_INDEX_MONTHLY_AMOUNT", invalid_amount),
                    patch.object(rules.prices, "latest_price", return_value={"price": 100.0}),
                ):
                    decisions = rules.us_index_decisions()
                self.assertTrue(all(not decision.data_ok for decision in decisions))
                self.assertTrue(all(decision.status == "error" for decision in decisions))
                self.assertTrue(all(decision.recommended_amount == 0.0 for decision in decisions))

    def test_qqqm_non_finite_monthly_close_produces_no_formal_advice(self):
        now = datetime(2026, 8, 15, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        daily_index = pd.date_range("2026-08-01", periods=2, freq="D")
        valid_daily_data = pd.DataFrame({"Close": [100.0, 101.0]}, index=daily_index)
        monthly_index = pd.date_range("2025-09-30", periods=12, freq="M")
        invalid_monthly_data = pd.DataFrame(
            {"Close": [100.0] * 11 + [float("inf")]},
            index=monthly_index,
        )
        with (
            patch.object(rules, "_now", return_value=now),
            patch.object(
                prices,
                "history",
                side_effect=[valid_daily_data, invalid_monthly_data, valid_daily_data],
            ),
            patch.object(
                prices,
                "nasdaq_pe",
                return_value={"pe": 30.0, "symbol": "QQQ", "field": "trailingPE"},
            ),
        ):
            decision = rules.us_index_decisions()[0]
        self.assertEqual(decision.asset, "QQQM")
        self.assertFalse(decision.data_ok)
        self.assertEqual(decision.status, "error")
        self.assertEqual(decision.recommended_amount, 0.0)

    def test_month_start_weekend_or_holiday_pushes_next_trading_day(self):
        tz = ZoneInfo("Asia/Shanghai")
        self.assertFalse(is_us_index_signal_due(datetime(2026, 8, 1, 16, 0, tzinfo=tz)))
        self.assertTrue(is_us_index_signal_due(datetime(2026, 8, 3, 16, 0, tzinfo=tz)))
        self.assertTrue(is_us_index_signal_due(datetime(2026, 8, 3, 16, 55, tzinfo=tz)))
        self.assertFalse(is_us_index_signal_due(datetime(2026, 8, 3, 17, 0, tzinfo=tz)))
        self.assertFalse(is_us_index_signal_due(datetime(2027, 1, 1, 16, 0, tzinfo=tz)))
        self.assertTrue(is_us_index_signal_due(datetime(2027, 1, 4, 16, 0, tzinfo=tz)))

    def test_nasdaq_pe_uses_trailing_pe_priority_only(self):
        class FakeTicker:
            def __init__(self, symbol):
                self.info = {
                    "QQQ": {"forwardPE": 20.0},
                    "QQQM": {"trailingPE": 30.0, "forwardPE": 18.0},
                    "^NDX": {"trailingPE": 40.0},
                }[symbol]

        with TemporaryDirectory() as tmpdir:
            with (
                patch.object(prices.yf, "Ticker", side_effect=FakeTicker),
                patch.object(prices, "NASDAQ_PE_CACHE_FILE", Path(tmpdir) / "nasdaq_pe.json"),
            ):
                pe = prices.nasdaq_pe()
        self.assertEqual(pe["symbol"], "QQQM")
        self.assertEqual(pe["field"], "trailingPE")
        self.assertEqual(pe["pe"], 30.0)
        self.assertEqual(pe["quotes"]["QQQ"]["forwardPE"], 20.0)

    def test_nasdaq_pe_rejects_non_finite_trailing_pe(self):
        class FakeTicker:
            def __init__(self, symbol):
                self.info = {"trailingPE": float("inf")}

        with TemporaryDirectory() as tmpdir:
            with (
                patch.object(prices.yf, "Ticker", side_effect=FakeTicker),
                patch.object(prices, "NASDAQ_PE_CACHE_FILE", Path(tmpdir) / "nasdaq_pe.json"),
            ):
                with self.assertRaises(RuntimeError):
                    prices.nasdaq_pe()

    def test_nasdaq_pe_falls_back_to_last_valid_trailing_pe(self):
        class FakeTicker:
            def __init__(self, symbol):
                self.info = {"forwardPE": 18.0}

        with TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "nasdaq_pe.json"
            cache_file.write_text(
                json.dumps({
                    "symbol": "QQQ",
                    "pe": 32.5,
                    "field": "trailingPE",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }),
                encoding="utf-8",
            )
            with (
                patch.object(prices.yf, "Ticker", side_effect=FakeTicker),
                patch.object(prices, "NASDAQ_PE_CACHE_FILE", cache_file),
            ):
                pe = prices.nasdaq_pe()
        self.assertEqual(pe["symbol"], "QQQ")
        self.assertEqual(pe["field"], "trailingPE")
        self.assertEqual(pe["source"], "last_valid_trailingPE")
        self.assertEqual(pe["pe"], 32.5)

    def test_nasdaq_pe_rejects_cache_older_than_35_days(self):
        class FakeTicker:
            def __init__(self, symbol):
                self.info = {"forwardPE": 18.0}

        with TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "nasdaq_pe.json"
            cache_file.write_text(
                json.dumps({
                    "symbol": "QQQ",
                    "pe": 32.5,
                    "field": "trailingPE",
                    "updated_at": (datetime.now(timezone.utc) - timedelta(days=36)).isoformat(),
                }),
                encoding="utf-8",
            )
            with (
                patch.object(prices.yf, "Ticker", side_effect=FakeTicker),
                patch.object(prices, "NASDAQ_PE_CACHE_FILE", cache_file),
            ):
                with self.assertRaises(RuntimeError):
                    prices.nasdaq_pe()

    def test_nasdaq_pe_rejects_invalid_or_future_cache_timestamp(self):
        timestamps = [
            "not-a-date",
            (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        ]
        for cached_at in timestamps:
            with self.subTest(cached_at=cached_at), TemporaryDirectory() as tmpdir:
                cache_file = Path(tmpdir) / "nasdaq_pe.json"
                cache_file.write_text(
                    json.dumps({
                        "symbol": "QQQ",
                        "pe": 32.5,
                        "field": "trailingPE",
                        "updated_at": cached_at,
                    }),
                    encoding="utf-8",
                )
                with patch.object(prices, "NASDAQ_PE_CACHE_FILE", cache_file):
                    self.assertIsNone(prices._read_last_valid_trailing_pe())

    def test_nasdaq_pe_rejects_cache_not_marked_trailing_pe(self):
        for field in ("forwardPE", None):
            with self.subTest(field=field), TemporaryDirectory() as tmpdir:
                payload = {
                    "symbol": "QQQ",
                    "pe": 30.0,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
                if field is not None:
                    payload["field"] = field
                cache_file = Path(tmpdir) / "nasdaq_pe.json"
                cache_file.write_text(json.dumps(payload), encoding="utf-8")
                with patch.object(prices, "NASDAQ_PE_CACHE_FILE", cache_file):
                    self.assertIsNone(prices._read_last_valid_trailing_pe())

    def test_nasdaq_pe_rejects_cache_from_unknown_symbol(self):
        with TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "nasdaq_pe.json"
            cache_file.write_text(
                json.dumps({
                    "symbol": "UNKNOWN",
                    "pe": 30.0,
                    "field": "trailingPE",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }),
                encoding="utf-8",
            )
            with patch.object(prices, "NASDAQ_PE_CACHE_FILE", cache_file):
                self.assertIsNone(prices._read_last_valid_trailing_pe())

    def test_nasdaq_pe_rejects_non_numeric_pe_types(self):
        for invalid_pe in (True, False, "20", "20.5"):
            with self.subTest(invalid_pe=invalid_pe), TemporaryDirectory() as tmpdir:
                cache_file = Path(tmpdir) / "nasdaq_pe.json"
                cache_file.write_text(
                    json.dumps({
                        "symbol": "QQQ",
                        "pe": invalid_pe,
                        "field": "trailingPE",
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }),
                    encoding="utf-8",
                )
                with patch.object(prices, "NASDAQ_PE_CACHE_FILE", cache_file):
                    self.assertIsNone(prices._read_last_valid_trailing_pe())

    def test_nasdaq_pe_rejects_non_numeric_live_trailing_pe_types(self):
        for invalid_pe in (True, False, "20", "20.5"):
            with self.subTest(invalid_pe=invalid_pe), TemporaryDirectory() as tmpdir:
                class FakeTicker:
                    def __init__(self, symbol):
                        self.info = {"trailingPE": invalid_pe}

                with (
                    patch.object(prices.yf, "Ticker", side_effect=FakeTicker),
                    patch.object(
                        prices,
                        "NASDAQ_PE_CACHE_FILE",
                        Path(tmpdir) / "nasdaq_pe.json",
                    ),
                ):
                    with self.assertRaises(RuntimeError):
                        prices.nasdaq_pe()

    def test_nasdaq_pe_rejects_non_object_cache(self):
        for payload in ([], "invalid", None):
            with self.subTest(payload=payload), TemporaryDirectory() as tmpdir:
                cache_file = Path(tmpdir) / "nasdaq_pe.json"
                cache_file.write_text(json.dumps(payload), encoding="utf-8")
                with patch.object(prices, "NASDAQ_PE_CACHE_FILE", cache_file):
                    try:
                        result = prices._read_last_valid_trailing_pe()
                    except Exception as exc:
                        self.fail(f"损坏的 PE 缓存不应抛出未处理异常: {exc}")
                self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
