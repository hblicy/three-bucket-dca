from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pandas as pd

from dca_tracker import prices, rules
from dca_tracker.notify import is_due, is_us_index_signal_due


class UsIndexStrategyTest(unittest.TestCase):
    month_ends_2024 = (
        "2024-01-31", "2024-02-29", "2024-03-28", "2024-04-30", "2024-05-31",
        "2024-06-28", "2024-07-31", "2024-08-30", "2024-09-30", "2024-10-31", "2024-11-29",
    )

    def confirmed_month_data(self):
        return pd.DataFrame({"Close": [100.0] * 10 + [90.0]}, index=pd.to_datetime(self.month_ends_2024))

    def setUp(self):
        source = patch.object(prices, "equity_quote", side_effect=lambda symbol: {"price": 100.0, "symbol": symbol, "source": "Binance Stocks", "quote_asset": "USD", "updated_at": "2026-10-26T09:00:00+00:00", "price_at": "2026-10-26T09:00:00+00:00", "session": "O"}, create=True)
        source.start()
        self.addCleanup(source.stop)

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
            patch.object(rules.prices, "latest_price", side_effect=lambda symbol, **kwargs: {"price": 100.0}),
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
        data = self.confirmed_month_data().assign(Close=100.0)
        now = datetime(2024, 12, 2, 8, tzinfo=timezone.utc)
        with patch.object(prices, "history", return_value=data), patch.object(prices, "_utc_now", return_value=now):
            trend = prices.monthly_trend("QQQM")
        self.assertFalse(trend["below_ma10m"])

    def test_month_end_uses_new_york_early_close(self):
        data = self.confirmed_month_data()
        for hour, minute, expected in ((17, 59, 100.0), (18, 0, 90.0)):
            with (
                self.subTest(hour=hour, minute=minute),
                patch.object(prices, "history", return_value=data),
                patch.object(prices, "_utc_now", return_value=datetime(2024, 11, 29, hour, minute, tzinfo=timezone.utc)),
            ):
                trend = prices.monthly_trend("QQQM")
                self.assertEqual(trend["monthly_close"], expected)
                self.assertEqual(trend["ma10m"], 99.0 if expected == 90.0 else 100.0)

    def test_open_month_data_does_not_change_signal(self):
        data = self.confirmed_month_data()
        data.loc[pd.Timestamp("2024-12-12"), "Close"] = 120.0
        now = datetime(2024, 12, 13, 1, tzinfo=timezone.utc)
        with patch.object(prices, "history", return_value=data), patch.object(prices, "_utc_now", return_value=now):
            trend = prices.monthly_trend("QQQM")
        self.assertEqual(trend["monthly_close"], 90.0)
        self.assertEqual(trend["ma10m"], 99.0)
        self.assertTrue(trend["below_ma10m"])

    def test_qqqm_reminder_waits_for_month_end_close(self):
        now = datetime(2024, 12, 2, 16, tzinfo=ZoneInfo("Asia/Shanghai"))
        data = self.confirmed_month_data().rename(index={pd.Timestamp("2024-11-29"): pd.Timestamp("2024-11-27")})
        with (
            patch.object(rules, "_now", return_value=now),
            patch.object(prices, "_utc_now", return_value=now.astimezone(timezone.utc)),
            patch.object(prices, "history", return_value=data),
            patch.object(prices, "nasdaq_pe", return_value={"pe": 30.0, "symbol": "QQQ", "field": "trailingPE"}),
        ):
            with self.assertLogs("dca_tracker.rules", level="ERROR"):
                decision = rules.us_index_decisions()[0]
            data.loc[pd.Timestamp("2024-11-29"), "Close"] = 90.0
            recovered = rules.us_index_decisions()[0]
        self.assertEqual(decision.asset, "QQQM")
        self.assertFalse(decision.data_ok)
        self.assertIn("2024-11-29", decision.reason)
        self.assertFalse(is_due(decision, now))
        self.assertEqual(recovered.recommended_amount, 250.0)
        self.assertTrue(is_due(recovered, now))

    def test_quote_filled_month_end_is_not_confirmed_close(self):
        data = self.confirmed_month_data()
        data.loc[pd.Timestamp("2024-11-29"), "Close"] = float("nan")
        quote = {"symbol": "QQQM", "price": 90.0,
                 "timestamp": datetime(2024, 11, 29, 17, 59, tzinfo=timezone.utc).timestamp()}
        now = datetime(2024, 12, 2, 8, tzinfo=timezone.utc)
        with (
            patch.dict(prices._HISTORY_CACHE, clear=True),
            patch.object(prices, "_utc_now", return_value=now),
            patch.object(prices, "_fetch_fmp_history", return_value=data),
            patch.object(prices, "fmp_request", return_value=[quote]),
        ):
            self.assertEqual(prices.history("QQQM").iloc[-1]["Close"], 90.0)
            with self.assertRaisesRegex(RuntimeError, "2024-11-29"):
                prices.monthly_trend("QQQM")

    def test_december_31_trades_when_new_years_day_is_saturday(self):
        data = pd.DataFrame({"Close": [100.0] * 9 + [110.0]})
        data.index = pd.to_datetime([
            "2027-03-31", "2027-04-30", "2027-05-28", "2027-06-30", "2027-07-30",
            "2027-08-31", "2027-09-30", "2027-10-29", "2027-11-30", "2027-12-31",
        ])
        now = datetime(2028, 1, 3, 8, tzinfo=timezone.utc)
        with patch.object(prices, "history", return_value=data), patch.object(prices, "_utc_now", return_value=now):
            trend = prices.monthly_trend("QQQM")
        self.assertEqual(trend["monthly_close"], 110.0)
        self.assertEqual(trend["ma10m"], 101.0)

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

    def test_nasdaq_pe_preserves_official_field_instead_of_forward_pe(self):
        item = {"symbol": "QQQ", "field": "priceToEarningsRatio", "pe": 30.0,
                "forward_pe": 18.0, "source": "invesco", "quotes": {},
                "as_of": datetime.now(timezone.utc).date().isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat()}
        with TemporaryDirectory() as tmpdir:
            with (
                patch.object(prices, "_fetch_invesco_pe", return_value=item),
                patch.object(prices, "NASDAQ_PE_CACHE_FILE", Path(tmpdir) / "nasdaq_pe.json"),
            ):
                pe = prices.nasdaq_pe()
        self.assertEqual(pe["symbol"], "QQQ")
        self.assertEqual(pe["field"], "priceToEarningsRatio")
        self.assertEqual(pe["pe"], 30.0)
        self.assertEqual(pe["forward_pe"], 18.0)

    def test_nasdaq_pe_rejects_non_finite_trailing_pe(self):
        response = Mock()
        response.json.return_value = {"cusip": "46090E103", "currencyCode": "USD", "priceToEarningsRatio": float("inf")}
        with TemporaryDirectory() as tmpdir:
            with (
                patch.object(prices.requests, "get", return_value=response),
                patch.object(prices, "NASDAQ_PE_CACHE_FILE", Path(tmpdir) / "nasdaq_pe.json"),
            ):
                with self.assertRaises(RuntimeError):
                    prices.nasdaq_pe()

    def test_nasdaq_pe_rejects_legacy_trailing_pe_cache(self):
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
                patch.object(prices, "_fetch_invesco_pe", side_effect=ValueError("source unavailable")),
                patch.object(prices, "NASDAQ_PE_CACHE_FILE", cache_file),
            ):
                with self.assertRaisesRegex(RuntimeError, "没有有效缓存"):
                    prices.nasdaq_pe()

    def test_nasdaq_pe_rejects_cache_older_than_35_days(self):
        with TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "nasdaq_pe.json"
            cache_file.write_text(
                json.dumps({
                    "symbol": "QQQ",
                    "pe": 32.5,
                    "field": "priceToEarningsRatio",
                    "source": "invesco",
                    "as_of": datetime.now(timezone.utc).date().isoformat(),
                    "updated_at": (datetime.now(timezone.utc) - timedelta(days=36)).isoformat(),
                }),
                encoding="utf-8",
            )
            with (
                patch.object(prices, "_fetch_invesco_pe", side_effect=ValueError("source unavailable")),
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
                        "field": "priceToEarningsRatio",
                        "source": "invesco",
                        "as_of": datetime.now(timezone.utc).date().isoformat(),
                        "updated_at": cached_at,
                    }),
                    encoding="utf-8",
                )
                with patch.object(prices, "NASDAQ_PE_CACHE_FILE", cache_file):
                    self.assertIsNone(prices._read_last_valid_trailing_pe())

    def test_nasdaq_pe_rejects_cache_not_marked_official_pe(self):
        for field in ("forwardPE", None):
            with self.subTest(field=field), TemporaryDirectory() as tmpdir:
                payload = {
                    "symbol": "QQQ",
                    "pe": 30.0,
                    "source": "invesco",
                    "as_of": datetime.now(timezone.utc).date().isoformat(),
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
                    "field": "priceToEarningsRatio",
                    "source": "invesco",
                    "as_of": datetime.now(timezone.utc).date().isoformat(),
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
                        "field": "priceToEarningsRatio",
                        "source": "invesco",
                        "as_of": datetime.now(timezone.utc).date().isoformat(),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }),
                    encoding="utf-8",
                )
                with patch.object(prices, "NASDAQ_PE_CACHE_FILE", cache_file):
                    self.assertIsNone(prices._read_last_valid_trailing_pe())

    def test_nasdaq_pe_rejects_non_numeric_live_trailing_pe_types(self):
        for invalid_pe in (True, False, "20", "20.5"):
            with self.subTest(invalid_pe=invalid_pe), TemporaryDirectory() as tmpdir:
                response = Mock()
                response.json.return_value = {"cusip": "46090E103", "currencyCode": "USD", "priceToEarningsRatio": invalid_pe}
                with (
                    patch.object(prices.requests, "get", return_value=response),
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
