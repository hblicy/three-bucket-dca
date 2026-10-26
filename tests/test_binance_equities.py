from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pandas as pd
import requests

from dca_tracker import notify, prices, rules


NOW = datetime(2026, 10, 26, 9, 35, tzinfo=timezone.utc)
VALUES = {"CRCL": 83.63, "QQQM": 290.82, "VOO": 700.624}


def row(symbol, **changes):
    return {"s": symbol, "c": str(VALUES[symbol]), "t": "STOCK" if symbol == "CRCL" else "ETF", "mp": "PRE", **changes}


def response(*rows):
    result = Mock()
    result.json.return_value = {"code": "000000", "success": True, "data": list(rows)}
    return result


class BinanceEquitiesTest(unittest.TestCase):
    def setUp(self):
        cache = patch.object(prices, "_BINANCE_EQUITY_CACHE", {"quotes": {}, "errors": {}, "checked_at": None}, create=True)
        cache.start()
        self.addCleanup(cache.stop)
        clock = patch.object(prices, "_utc_now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def quote(self, symbol):
        return prices.equity_quote(symbol)

    def test_http_snapshot_gets_all_original_symbols_without_waiting_for_new_trades(self):
        payload = response(*(row(s) for s in VALUES))
        with patch.object(prices.requests, "get", return_value=payload) as get:
            quotes = {symbol: self.quote(symbol) for symbol in VALUES}
        get.assert_called_once_with("https://www.binance.com/bapi/equity/v1/public/equity/symbol/get-symbols-by-param", params={"symbols": "CRCL,QQQM,VOO"}, timeout=10)
        payload.raise_for_status.assert_called_once_with()
        for symbol, quote in quotes.items():
            self.assertEqual(quote["symbol"], symbol)
            self.assertEqual(quote["price"], VALUES[symbol])
            self.assertEqual(quote["source"], "Binance Stocks")
            self.assertEqual(quote["quote_asset"], "USD")
            self.assertEqual(quote["session"], "PRE")
            self.assertEqual(quote["updated_at"], NOW.isoformat())
            self.assertNotIn("price_at", quote, "snapshot has no source trade timestamp")
        quotes["CRCL"]["price"] = 0
        with patch.object(prices.requests, "get") as unused:
            self.assertEqual(self.quote("CRCL")["price"], VALUES["CRCL"])
        unused.assert_not_called()

    def test_expired_snapshot_reloads(self):
        with patch.object(prices.requests, "get", return_value=response(*(row(s) for s in VALUES))):
            self.quote("CRCL")
        prices._BINANCE_EQUITY_CACHE["checked_at"] -= 31
        payload = response(*(row(s, c="84.00") if s == "CRCL" else row(s) for s in VALUES))
        with patch.object(prices.requests, "get", return_value=payload) as get:
            self.assertEqual(self.quote("CRCL")["price"], 84)
        get.assert_called_once()

    def test_missing_symbol_fails_without_substituting_another_etf(self):
        payload = response(row("QQQM"), {"s": "SPY", "c": "600", "t": "ETF", "mp": "PRE"})
        with patch.object(prices.requests, "get", return_value=payload), self.assertLogs("dca_tracker.prices", level="WARNING"):
            with self.assertRaisesRegex(RuntimeError, "VOO"):
                self.quote("VOO")
        with patch.object(prices.requests, "get") as unused:
            self.assertEqual(self.quote("QQQM")["price"], VALUES["QQQM"])
        unused.assert_not_called()

    def test_bad_stock_does_not_remove_other_valid_stock_quotes(self):
        invalid_rows = [row("CRCL", c=value) for value in (None, True, "NaN", "Infinity", "-1", "0")]
        invalid_rows += [row("CRCL", t=value) for value in (None, "TOKEN", "ETF", [])]
        invalid_rows += [row("CRCL", mp=value) for value in (None, [], {})]
        for invalid in invalid_rows:
            prices._BINANCE_EQUITY_CACHE.update(quotes={}, errors={}, checked_at=None)
            payload = response(invalid, row("QQQM"), row("VOO"))
            with self.subTest(row=invalid), patch.object(prices.requests, "get", return_value=payload):
                with self.assertLogs("dca_tracker.prices", level="WARNING") as logs:
                    with self.assertRaisesRegex(RuntimeError, "CRCL"):
                        self.quote("CRCL")
                self.assertEqual(self.quote("VOO")["price"], VALUES["VOO"])
                self.assertIn("Traceback", "\n".join(logs.output))

    def test_error_envelope_is_not_reported_as_success(self):
        for invalid in (None, [], {}, {"code": "000000", "success": False, "data": []}, {"code": "100001", "success": True, "data": []}, {"code": "000000", "success": True, "data": {}}):
            payload = Mock()
            payload.json.return_value = invalid
            with self.subTest(payload=invalid), patch.object(prices.requests, "get", return_value=payload), self.assertLogs("dca_tracker.prices", level="WARNING"):
                with self.assertRaisesRegex(RuntimeError, "币安美股"):
                    self.quote("CRCL")

    def test_network_http_and_json_errors_preserve_their_cause(self):
        for error in (requests.Timeout("timeout"), requests.HTTPError("HTTP 429"), ValueError("invalid JSON")):
            payload = response()
            payload.raise_for_status.side_effect = error if isinstance(error, requests.HTTPError) else None
            payload.json.side_effect = error if not isinstance(error, requests.HTTPError) else None
            with self.subTest(error=error), patch.object(prices.requests, "get", return_value=payload), self.assertLogs("dca_tracker.prices", level="WARNING"):
                with self.assertRaisesRegex(RuntimeError, "币安美股") as caught:
                    self.quote("CRCL")
            self.assertIs(caught.exception.__cause__, error)

    def test_malformed_symbol_does_not_crash_the_other_quotes(self):
        payload = response(row("CRCL", s=["CRCL"]), row("QQQM"), row("VOO"))
        with patch.object(prices.requests, "get", return_value=payload), self.assertLogs("dca_tracker.prices", level="WARNING"):
            with self.assertRaisesRegex(RuntimeError, "CRCL"):
                self.quote("CRCL")
        self.assertEqual(self.quote("VOO")["price"], VALUES["VOO"])

    def test_unsupported_symbols_do_not_start_request(self):
        with patch.object(prices.requests, "get") as get:
            with self.assertRaisesRegex(ValueError, "QQQ"):
                self.quote("QQQ")
        get.assert_not_called()

    def test_regular_pre_post_and_closed_phases_keep_their_original_units(self):
        for session in ("PRE", "O", "POST", "ON", "C"):
            prices._BINANCE_EQUITY_CACHE.update(quotes={}, errors={}, checked_at=None)
            payload = response(*(row(s, mp=session) for s in VALUES))
            with patch.object(prices.requests, "get", return_value=payload):
                result = self.quote("CRCL")
            self.assertEqual(result["session"], session)
            self.assertEqual(result["quote_asset"], "USD")

    def test_crcl_uses_binance_display_quote_and_original_daily_signal(self):
        quote = {"price": 83.63, "symbol": "CRCL", "source": "Binance Stocks", "quote_asset": "USD", "updated_at": NOW.isoformat(), "session": "PRE"}
        with patch.object(prices, "history", return_value=pd.DataFrame({"Close": [100.0]*90})), patch.object(rules, "_crcl_usdc_risk", return_value={"alert": False, "drop_24h_pct": 0}), patch.object(prices, "equity_quote", return_value=quote):
            result = rules.crcl_decision()
        self.assertTrue(result.data_ok)
        self.assertEqual(result.price, 83.63)
        self.assertAlmostEqual(result.metrics["score"], 32.5 / 85 * 100)
        self.assertEqual(result.metrics["market_quote"], quote)
        message = notify.message(result)
        self.assertIn("当前价格：$83.63", message)
        self.assertIn("币安美股 CRCL", message)
        self.assertIn("取价时间：2026-10-26 17:35:00 北京时间", message)

    def test_current_price_metrics_can_use_quote_while_history_stays_daily(self):
        quote = {"price": 83.63}
        with patch.object(prices, "history", return_value=pd.DataFrame({"Close": [100.0, 110.0]})):
            self.assertEqual(prices.latest_price("CRCL")["price"], 110)
            result = prices.latest_price("CRCL", quote=quote)
        self.assertEqual(result["price"], 83.63)
        self.assertAlmostEqual(result["drawdown_pct"], (83.63/110-1)*100)


if __name__ == "__main__":
    unittest.main()
