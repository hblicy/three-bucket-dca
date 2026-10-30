from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import date, datetime, timezone
from threading import Event
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd

from dca_tracker import performance, prices, rules


class MissingEquityCloseTest(unittest.TestCase):
    def setUp(self):
        source = patch.object(prices, "equity_quote", side_effect=lambda symbol: {"price": 82.3, "symbol": symbol, "source": "Binance Stocks", "quote_asset": "USD", "updated_at": "2026-10-26T09:00:00+00:00", "price_at": "2026-10-26T09:00:00+00:00", "session": "O"}, create=True)
        source.start()
        self.addCleanup(source.stop)
        self.frame = pd.DataFrame(
            {
                "Open": [75.27, 86.35],
                "High": [87.76, 87.52],
                "Low": [75.27, 80.62],
                "Close": [86.97, np.nan],
                "Adj Close": [86.97, np.nan],
                "Volume": [16_100_200, 11_252_057],
            },
            index=pd.to_datetime(["2026-10-15", "2026-10-16"]),
        )
        self.metadata = {
            "symbol": "CRCL",
            "price": 82.22,
            "timestamp": int(datetime(2026, 10, 16, 20, tzinfo=timezone.utc).timestamp()),
        }
        patches = ExitStack()
        self.addCleanup(patches.close)
        patches.enter_context(patch.dict(prices._HISTORY_CACHE, clear=True))
        self.download = patches.enter_context(patch.object(prices, "_fetch_fmp_history", return_value=self.frame))
        self.quote = Mock()
        self.ticker = self.quote
        patches.enter_context(patch.object(prices, "fmp_request", side_effect=lambda path, params: [self.quote()]))
        self.quote.return_value = self.metadata
        self.now = patches.enter_context(patch.object(
            prices, "_utc_now", return_value=datetime(2026, 10, 17, 1, 30, tzinfo=timezone.utc),
        ))

    def test_missing_latest_close_uses_same_day_quote_for_each_equity(self):
        for symbol, expected_price in (("CRCL", 82.22), ("QQQM", 292.26), ("VOO", 701.61)):
            with self.subTest(symbol=symbol):
                self.quote.return_value = self.metadata | {"symbol": symbol, "price": expected_price}
                with self.assertLogs("dca_tracker.prices", level="WARNING") as logs:
                    result = prices.latest_price(symbol)
                self.assertEqual(result["price"], expected_price)
                self.assertTrue(any(symbol in line and "2026-10-16" in line for line in logs.output))
                self.assertTrue(pd.isna(self.frame["Close"].iloc[-1]))

    def test_start_history_repairs_prices_used_by_performance(self):
        class ObservedDate(date):
            @classmethod
            def today(cls):
                return cls(2026, 10, 17)

        with patch.object(performance, "date", ObservedDate), self.assertLogs("dca_tracker.prices", level="WARNING"):
            maps, errors = performance._price_maps(["CRCL"], date(2026, 10, 16))
        self.assertEqual(errors, [])
        self.assertEqual(maps["CRCL"], {"2026-10-16": 82.22, "2026-10-17": 82.22})

    def test_valid_latest_close_does_not_fetch_metadata(self):
        self.frame.loc[self.frame.index[-1], "Close"] = 82.30
        self.assertEqual(prices.latest_price("CRCL")["price"], 82.30)
        self.ticker.assert_not_called()

    def test_nonmissing_invalid_close_is_not_repaired(self):
        for value in (np.inf, -np.inf, 0.0, -1.0):
            with self.subTest(value=value):
                self.frame.loc[self.frame.index[-1], "Close"] = value
                with self.assertRaisesRegex(RuntimeError, "Invalid latest price for CRCL"):
                    prices.latest_price("CRCL")
        self.ticker.assert_not_called()

    def test_invalid_quote_prices_are_rejected(self):
        for value in (None, np.nan, np.inf, -np.inf, 0.0, -1.0, True, "82.22"):
            with self.subTest(value=value):
                self.quote.return_value = self.metadata | {"price": value}
                with self.assertRaisesRegex(RuntimeError, "CRCL"):
                    prices.latest_price("CRCL")

    def test_missing_or_wrong_quote_identity_is_rejected(self):
        for field, value in (
            ("symbol", None), ("symbol", "QQQM"),
        ):
            with self.subTest(field=field, value=value):
                self.quote.return_value = self.metadata | {field: value}
                with self.assertRaisesRegex(RuntimeError, "CRCL"):
                    prices.latest_price("CRCL")

    def test_invalid_quote_timestamps_are_rejected(self):
        for value in (None, True, "1792180800", 0, -1, np.nan, np.inf, 1e30):
            with self.subTest(value=value):
                self.quote.return_value = self.metadata | {"timestamp": value}
                with self.assertRaisesRegex(RuntimeError, "CRCL"):
                    prices.latest_price("CRCL")

    def test_quote_must_match_the_latest_exchange_date(self):
        for timestamp in (
            datetime(2026, 10, 15, 20, tzinfo=timezone.utc),
            datetime(2026, 10, 16, 0, 30, tzinfo=timezone.utc),
            datetime(2026, 10, 17, 20, tzinfo=timezone.utc),
        ):
            with self.subTest(timestamp=timestamp):
                self.quote.return_value = self.metadata | {"timestamp": int(timestamp.timestamp())}
                with self.assertRaisesRegex(RuntimeError, "CRCL"):
                    prices.latest_price("CRCL")

    def test_date_matching_uses_the_exchange_timezone(self):
        timestamp = datetime(2026, 10, 17, 0, 30, tzinfo=timezone.utc)
        self.metadata["timestamp"] = int(timestamp.timestamp())
        with self.assertLogs("dca_tracker.prices", level="WARNING"):
            self.assertEqual(prices.latest_price("CRCL")["price"], 82.22)

    def test_future_quote_is_rejected_even_on_the_same_exchange_date(self):
        self.now.return_value = datetime(2026, 10, 16, 19, tzinfo=timezone.utc)
        with self.assertRaisesRegex(RuntimeError, "CRCL"):
            prices.latest_price("CRCL")

    def test_same_day_quote_does_not_bypass_history_freshness(self):
        self.now.return_value = datetime(2026, 10, 23, tzinfo=timezone.utc)
        with self.assertRaisesRegex(RuntimeError, "CRCL"):
            prices.latest_price("CRCL")

    def test_metadata_failure_propagates(self):
        self.quote.side_effect = RuntimeError("metadata request failed")
        with self.assertRaisesRegex(RuntimeError, "metadata request failed"):
            prices.latest_price("CRCL")

    def test_metadata_lookup_is_isolated_from_another_download(self):
        metadata_started = Event()
        release_metadata = Event()
        second_requested = Event()
        second_download_started = Event()

        def download(symbol, **kwargs):
            if symbol == "QQQM":
                second_download_started.set()
                return self.frame.assign(Close=[299.79, 292.26])
            return self.frame

        def metadata():
            metadata_started.set()
            if not release_metadata.wait(timeout=5):
                raise RuntimeError("Timed out waiting to release metadata")
            return self.metadata

        def other_quote():
            second_requested.set()
            return prices.latest_price("QQQM")

        self.download.side_effect = download
        self.quote.side_effect = metadata
        with self.assertLogs("dca_tracker.prices", level="WARNING"), ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(prices.latest_price, "CRCL")
            try:
                self.assertTrue(metadata_started.wait(timeout=5))
                second = executor.submit(other_quote)
                self.assertTrue(second_requested.wait(timeout=5))
                self.assertTrue(second_download_started.wait(timeout=0.25))
            finally:
                release_metadata.set()
            self.assertEqual(first.result(timeout=5)["price"], 82.22)
            self.assertEqual(second.result(timeout=5)["price"], 292.26)

    def test_unusable_metadata_produces_no_formal_advice(self):
        self.quote.return_value = {}
        for decision in [rules.crcl_decision(), *rules.us_index_decisions()]:
            self.assertFalse(decision.data_ok)
            self.assertEqual(decision.status, "error")
            self.assertEqual(decision.recommended_amount, 0.0)

    def test_btc_missing_close_is_not_repaired_with_equity_metadata(self):
        with patch.object(prices, "_fetch_binance_history", return_value=self.frame), self.assertRaisesRegex(RuntimeError, "Invalid latest price for BTCUSDT"):
            prices.latest_price("BTC")
        self.ticker.assert_not_called()


if __name__ == "__main__":
    unittest.main()
