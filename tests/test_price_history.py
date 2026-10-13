from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event
from unittest.mock import patch

import numpy as np
import pandas as pd

from dca_tracker import prices


class PriceHistoryTest(unittest.TestCase):
    def overlapping_quotes(self, first_symbol, first_request, second_symbol, second_request):
        first_quote_started = Event()
        second_quote_started = Event()
        release_first_quote = Event()
        quotes = {"CRCL": 83.87, "QQQM": 307.19, "BTC-USD": 82_024.12}
        dates = pd.date_range("2026-10-09", periods=2, freq="D", name="Date")

        def quote_history(ticker, **kwargs):
            if ticker.ticker == first_symbol:
                first_quote_started.set()
                if not release_first_quote.wait(timeout=5):
                    raise RuntimeError("Timed out waiting to release first quote")
            elif ticker.ticker == second_symbol:
                second_quote_started.set()
            return pd.DataFrame({"Close": [quotes[ticker.ticker]] * 2}, index=dates)

        with (
            patch.object(prices.yf.Ticker, "history", new=quote_history),
            patch.object(
                prices,
                "_utc_now",
                return_value=datetime(2026, 10, 11, tzinfo=timezone.utc),
            ),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            first = executor.submit(first_request)
            try:
                self.assertTrue(first_quote_started.wait(timeout=5))
                second = executor.submit(second_request)
                if second_quote_started.wait(timeout=0.25):
                    second.result(timeout=5)
            finally:
                release_first_quote.set()
            try:
                return first.result(timeout=5), second.result(timeout=5)
            except RuntimeError as exc:
                self.fail(f"Overlapping quotes must keep each asset's price: {exc}")

    def test_concurrent_equity_quotes_keep_each_assets_price(self):
        crcl, qqqm = self.overlapping_quotes(
            "CRCL", lambda: prices.latest_price("CRCL"),
            "QQQM", lambda: prices.latest_price("QQQM"),
        )
        self.assertEqual(crcl["price"], 83.87)
        self.assertEqual(qqqm["price"], 307.19)

    def test_btc_indicators_and_equity_performance_keep_separate_history(self):
        btc, crcl = self.overlapping_quotes(
            "BTC-USD", prices.btc_history,
            "CRCL", lambda: prices.history("CRCL", start="2026-10-01"),
        )
        self.assertEqual(btc["Price"].tolist(), [82_024.12] * 2)
        self.assertEqual(crcl["Close"].tolist(), [83.87] * 2)
        self.assertTrue(btc.columns.is_unique)
        self.assertTrue(crcl.columns.is_unique)

    def test_invalid_latest_equity_close_does_not_use_an_older_price(self):
        dates = pd.date_range("2026-10-09", periods=2, freq="D", name="Date")
        for symbol in ("CRCL", "QQQM"):
            for invalid_close in (np.nan, np.inf, -np.inf, 0.0, -1.0):
                with self.subTest(symbol=symbol, invalid_close=invalid_close):
                    frame = pd.DataFrame({"Close": [100.0, invalid_close]}, index=dates)
                    with patch.object(prices.yf, "download", return_value=frame):
                        with self.assertRaisesRegex(RuntimeError, f"Invalid latest price for {symbol}"):
                            prices.latest_price(symbol)


if __name__ == "__main__":
    unittest.main()
