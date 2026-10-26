from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pandas as pd
import requests

from dca_tracker import notify, portfolio, prices, rules


QUOTE = {"price": 80014.0, "symbol": "BTCUSDT", "source": "Binance Spot", "quote_asset": "USDT", "updated_at": "2026-10-26T08:00:00+00:00"}
SIGNAL = {"Price": 79000.0, "proxy_z": 0.0, "ahr999": 1.0, "puell": 1.0, "dca_score": 33.5, "p10": 16.8, "p25": 26.5, "p50": 39.5, "p75": 59.0}


class BinanceSpotTest(unittest.TestCase):
    def fetch(self):
        self.assertTrue(hasattr(prices, "btc_spot_quote"), "missing Binance BTC spot quote")
        return prices.btc_spot_quote()

    def test_fetches_public_binance_spot_price_and_records_collection_time(self):
        response = Mock()
        response.json.return_value = {"symbol": "BTCUSDT", "price": "80014.00000000"}
        now = datetime(2026, 10, 26, 8, tzinfo=timezone.utc)
        with patch.object(prices.requests, "get", return_value=response) as get, patch.object(prices, "_utc_now", return_value=now):
            result = self.fetch()
        self.assertEqual(result, QUOTE)
        get.assert_called_once_with("https://data-api.binance.vision/api/v3/ticker/price", params={"symbol": "BTCUSDT"}, timeout=10)
        response.raise_for_status.assert_called_once_with()

    def test_invalid_or_mismatched_quotes_fail_with_context(self):
        payloads = [None, [], {}, {"symbol": "ETHUSDT", "price": "80014"}]
        payloads += [{"symbol": "BTCUSDT", "price": value} for value in (None, True, 0, -1, "NaN", "Infinity", "invalid", [])]
        for payload in payloads:
            response = Mock()
            response.json.return_value = payload
            with self.subTest(payload=payload), patch.object(prices.requests, "get", return_value=response):
                with self.assertLogs("dca_tracker.prices", level="WARNING") as logs:
                    with self.assertRaisesRegex(RuntimeError, "币安现货"):
                        self.fetch()
                self.assertIn("Traceback", "\n".join(logs.output))

    def test_network_http_and_json_errors_preserve_causes(self):
        for error in (requests.Timeout("timeout"), requests.HTTPError("HTTP 429"), ValueError("invalid JSON")):
            response = Mock()
            response.json.side_effect = error
            with self.subTest(error=error), patch.object(prices.requests, "get", return_value=response):
                with self.assertLogs("dca_tracker.prices", level="WARNING"):
                    with self.assertRaisesRegex(RuntimeError, "币安现货") as caught:
                        self.fetch()
                self.assertIs(caught.exception.__cause__, error)

    def test_request_timeout_is_identifiable(self):
        error = requests.Timeout("network timeout")
        with patch.object(prices.requests, "get", side_effect=error), self.assertLogs("dca_tracker.prices", level="WARNING"):
            with self.assertRaisesRegex(RuntimeError, "币安现货") as caught:
                self.fetch()
        self.assertIs(caught.exception.__cause__, error)

    def test_unexpected_source_error_is_not_swallowed(self):
        with patch.object(prices.requests, "get", side_effect=RuntimeError("implementation failed")):
            with self.assertRaisesRegex(RuntimeError, "implementation failed"):
                self.fetch()

    def test_decision_uses_spot_quote_without_changing_dca_signal(self):
        with patch.object(prices, "btc_indicators", return_value=pd.DataFrame([SIGNAL])), patch.object(prices, "btc_spot_quote", return_value=QUOTE, create=True):
            result = rules.btc_decision()
        self.assertTrue(result.data_ok)
        self.assertEqual(result.price, 80014)
        self.assertEqual(result.metrics["spot_quote"], QUOTE)
        self.assertEqual(result.metrics["dca_score"], 33.5)
        self.assertEqual(result.metrics["multiplier"], 1)
        self.assertEqual(result.recommended_amount, 100)

    def test_spot_failure_does_not_relabel_yahoo_as_binance_or_send_advice(self):
        with patch.object(prices, "btc_indicators", return_value=pd.DataFrame([SIGNAL])), patch.object(prices, "btc_spot_quote", side_effect=RuntimeError("币安现货取价失败"), create=True):
            with self.assertLogs("dca_tracker.rules", level="ERROR"):
                result = rules.btc_decision()
        self.assertFalse(result.data_ok)
        self.assertIsNone(result.price)
        self.assertEqual(result.recommended_amount, 0)
        self.assertIn("币安现货", result.reason)
        self.assertFalse(notify.is_due(result, datetime(2026, 10, 26, 16, tzinfo=notify.ZoneInfo("Asia/Shanghai"))))

    def test_current_portfolio_prices_use_same_binance_source(self):
        with patch.object(portfolio, "VALID_ASSETS", {"BTC", "CRCL"}), patch.object(prices, "btc_spot_quote", return_value=QUOTE, create=True) as spot, patch.object(prices, "equity_quote", return_value={"price": 50}, create=True) as equity:
            result = portfolio.current_prices()
        self.assertEqual(result, {"BTC": 80014, "CRCL": 50})
        spot.assert_called_once_with()
        equity.assert_called_once_with("CRCL")

    def test_btc_reminder_identifies_spot_unit_source_and_collection_time(self):
        with patch.object(prices, "btc_indicators", return_value=pd.DataFrame([SIGNAL])), patch.object(prices, "btc_spot_quote", return_value=QUOTE, create=True):
            decision = rules.btc_decision()
        decision.price = QUOTE["price"]
        decision.metrics["spot_quote"] = QUOTE
        text = notify.message(decision)
        self.assertIn("当前价格：80,014.00 USDT", text)
        self.assertIn("币安现货 BTC/USDT", text)
        self.assertIn("2026-10-26 16:00:00 北京时间", text)
        self.assertIn("建议金额：$100.00", text)
        self.assertIn("DCA Score 33.5", text)


if __name__ == "__main__":
    unittest.main()
