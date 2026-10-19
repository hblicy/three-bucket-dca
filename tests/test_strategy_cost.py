from __future__ import annotations

import copy
import gc
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import requests

from dca_tracker import cache, db


SOURCE_URL = "https://www.strategy.com/press/strategy-acquires-250-btc-and-repurchases-192-million-of-strc_10-12-2026"
PAYLOAD = {
    "asset": "BTC",
    "ticker": "MSTR",
    "asOf": "2026-10-11",
    "summary": {
        "avgCostUsd": 75444,
        "avgCostAsOf": "2026-10-11",
        "avgCostSource": "Strategy 新闻稿截至 2026-10-11（含费用）",
        "totalHolding": 848250,
        "totalCostUsd": 64000000000,
    },
    "sources": [{"name": "Strategy 新闻稿截至 2026-10-11（含费用）", "url": SOURCE_URL}],
    "transactions": [{"priceUsd": 86194}],
}


class StrategyCostTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(gc.collect)
        db_patch = patch.object(db, "DB_PATH", Path(self.temp.name) / "tracker.db")
        db_patch.start()
        self.addCleanup(db_patch.stop)
        db.init_db()
        self.response = Mock(spec=requests.Response)
        self.response.json.return_value = copy.deepcopy(PAYLOAD)
        request_patch = patch("requests.get", return_value=self.response)
        self.get = request_patch.start()
        self.addCleanup(request_patch.stop)

    def expire_cache(self):
        with db.connect() as conn:
            conn.execute(
                "UPDATE api_cache SET updated_at = ? WHERE cache_key = ?",
                ((datetime.now() - timedelta(hours=7)).isoformat(), cache.STRATEGY_BTC_COST_KEY),
            )

    def test_uses_disclosed_average_with_its_date_and_official_source(self):
        self.assertTrue(hasattr(cache, "strategy_btc_cost"), "missing Strategy cost reader")
        result = cache.strategy_btc_cost()
        self.assertEqual(result["avg_cost_usd"], 75444)
        self.assertEqual(result["as_of"], "2026-10-11")
        self.assertEqual(result["source_url"], SOURCE_URL)
        self.assertFalse(result["stale"])
        self.assertIsNone(result["error"])
        self.get.assert_called_once_with("https://lianyanshe.com/api/holdings/treasury?id=mstr", timeout=10)

    def test_reuses_cache_for_six_hours_then_fetches_new_cost(self):
        self.assertTrue(hasattr(cache, "strategy_btc_cost"), "missing Strategy cost reader")
        cache.strategy_btc_cost()
        self.response.json.return_value["summary"]["avgCostUsd"] = 76000
        self.assertEqual(cache.strategy_btc_cost()["avg_cost_usd"], 75444)
        self.assertEqual(self.get.call_count, 1)
        self.expire_cache()
        self.assertEqual(cache.strategy_btc_cost()["avg_cost_usd"], 76000)
        self.assertEqual(self.get.call_count, 2)

    def test_network_failure_preserves_last_valid_cost_and_cache_timestamp(self):
        self.assertTrue(hasattr(cache, "strategy_btc_cost"), "missing Strategy cost reader")
        cache.strategy_btc_cost()
        self.expire_cache()
        before = cache.get_cache(cache.STRATEGY_BTC_COST_KEY)["cache"]["updated_at"]
        self.get.side_effect = requests.Timeout("source timed out")
        with self.assertLogs("dca_tracker.cache", level="WARNING") as logs:
            result = cache.strategy_btc_cost()
        self.assertEqual(result["avg_cost_usd"], 75444)
        self.assertTrue(result["stale"])
        self.assertEqual(result["error"]["code"], "source_unavailable")
        self.assertEqual(result["error"]["message"], "source timed out")
        self.assertEqual(cache.get_cache(cache.STRATEGY_BTC_COST_KEY)["cache"]["updated_at"], before)
        self.assertIn("Traceback", "\n".join(logs.output))

    def test_initial_failure_returns_unavailable_without_persisting_fake_cost(self):
        self.assertTrue(hasattr(cache, "strategy_btc_cost"), "missing Strategy cost reader")
        self.get.side_effect = requests.ConnectionError("offline")
        with self.assertLogs("dca_tracker.cache", level="WARNING"):
            result = cache.strategy_btc_cost()
        self.assertIsNone(result["avg_cost_usd"])
        self.assertIsNone(result["as_of"])
        self.assertEqual(result["error"]["code"], "source_unavailable")
        self.assertIsNone(cache.get_cache(cache.STRATEGY_BTC_COST_KEY))

    def test_invalid_remote_cost_never_replaces_the_last_valid_value(self):
        self.assertTrue(hasattr(cache, "strategy_btc_cost"), "missing Strategy cost reader")
        cache.strategy_btc_cost()
        self.expire_cache()
        for value in (None, 0, -1, float("nan"), float("inf"), True, "75444"):
            with self.subTest(value=value):
                self.response.json.return_value["summary"]["avgCostUsd"] = value
                with self.assertLogs("dca_tracker.cache", level="WARNING"):
                    result = cache.strategy_btc_cost()
                self.assertEqual(result["avg_cost_usd"], 75444)
                self.assertTrue(result["stale"])
                self.assertEqual(result["error"]["code"], "invalid_source_data")

    def test_rejects_wrong_asset_invalid_date_and_untrusted_source_link(self):
        self.assertTrue(hasattr(cache, "strategy_btc_cost"), "missing Strategy cost reader")
        variants = []
        for field, value in (("asset", "ETH"), ("ticker", "OTHER")):
            payload = copy.deepcopy(PAYLOAD)
            payload[field] = value
            variants.append(payload)
        for value in (None, "not-a-date", (date.today() + timedelta(days=2)).isoformat()):
            payload = copy.deepcopy(PAYLOAD)
            payload["summary"]["avgCostAsOf"] = value
            variants.append(payload)
        payload = copy.deepcopy(PAYLOAD)
        payload["sources"][0]["url"] = "javascript:alert(1)"
        variants.extend([payload, {}, [], None])
        for payload in variants:
            with self.subTest(payload=payload):
                self.response.json.return_value = payload
                with self.assertLogs("dca_tracker.cache", level="WARNING"):
                    result = cache.strategy_btc_cost()
                self.assertIsNone(result["avg_cost_usd"])
                self.assertEqual(result["error"]["code"], "invalid_source_data")

    def test_invalid_json_is_an_identifiable_source_error(self):
        self.assertTrue(hasattr(cache, "strategy_btc_cost"), "missing Strategy cost reader")
        self.response.json.side_effect = requests.exceptions.JSONDecodeError("invalid JSON", "x", 0)
        with self.assertLogs("dca_tracker.cache", level="WARNING"):
            result = cache.strategy_btc_cost()
        self.assertEqual(result["error"]["code"], "invalid_source_data")

    def test_unexpected_programming_error_propagates(self):
        self.assertTrue(hasattr(cache, "strategy_btc_cost"), "missing Strategy cost reader")
        self.get.side_effect = RuntimeError("unexpected implementation error")
        with self.assertRaisesRegex(RuntimeError, "unexpected implementation error"):
            cache.strategy_btc_cost()

    def test_reference_failure_does_not_change_dashboard_advice_or_totals(self):
        self.assertTrue(hasattr(cache, "strategy_btc_cost"), "missing Strategy cost reader")
        self.get.side_effect = requests.Timeout("offline")
        decision = Mock(asset="BTC", price=82002.12, recommended_amount=100, data_ok=True)
        decision.reason = "DCA Score 33.5"
        summary = {"total_cost": 100, "total_value": 101}
        with patch.object(cache.rules, "all_decisions", return_value=[decision]), patch.object(
            cache.portfolio, "portfolio_summary", return_value=summary
        ):
            with self.assertLogs("dca_tracker.cache", level="WARNING"):
                result = cache.build_dashboard()
        self.assertIn("strategy_btc", result)
        self.assertFalse(result["has_errors"])
        self.assertEqual(result["monthly_plan"], 100)
        self.assertEqual(result["portfolio"], summary)
        self.assertEqual(result["decisions"][0]["reason"], "DCA Score 33.5")
        self.assertIsNone(result["strategy_btc"]["avg_cost_usd"])


if __name__ == "__main__":
    unittest.main()
