from __future__ import annotations

import unittest
from datetime import datetime
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

    def test_nasdaq_pe_falls_back_to_last_valid_trailing_pe(self):
        class FakeTicker:
            def __init__(self, symbol):
                self.info = {"forwardPE": 18.0}

        with TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "nasdaq_pe.json"
            cache_file.write_text('{"symbol":"QQQ","pe":32.5,"field":"trailingPE","updated_at":"2026-01-01T00:00:00Z"}', encoding="utf-8")
            with (
                patch.object(prices.yf, "Ticker", side_effect=FakeTicker),
                patch.object(prices, "NASDAQ_PE_CACHE_FILE", cache_file),
            ):
                pe = prices.nasdaq_pe()
        self.assertEqual(pe["symbol"], "QQQ")
        self.assertEqual(pe["field"], "trailingPE")
        self.assertEqual(pe["source"], "last_valid_trailingPE")
        self.assertEqual(pe["pe"], 32.5)


if __name__ == "__main__":
    unittest.main()
