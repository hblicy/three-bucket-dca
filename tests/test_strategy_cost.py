from __future__ import annotations

import gc
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from dca_tracker import cache, db, strategy


COST = {"avg_cost_usd": 75446.0, "as_of": "2026-10-19", "source_url": "https://www.strategy.com/ledger"}


class StrategyCostTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(gc.collect)
        db_patch = patch.object(db, "DB_PATH", Path(self.temp.name) / "tracker.db")
        db_patch.start()
        self.addCleanup(db_patch.stop)
        db.init_db()
        source_patch = patch.object(strategy, "fetch_btc_cost", return_value=dict(COST))
        self.fetch = source_patch.start()
        self.addCleanup(source_patch.stop)

    def expire_attempt(self):
        with db.connect() as conn:
            conn.execute(
                "UPDATE api_cache SET updated_at = ? WHERE cache_key = ?",
                ((datetime.now() - timedelta(hours=25)).isoformat(), cache.STRATEGY_BTC_COST_ATTEMPT_KEY),
            )

    def test_dashboard_read_never_launches_collection(self):
        result = cache.strategy_btc_cost()
        self.assertIsNone(result["avg_cost_usd"])
        self.fetch.assert_not_called()

    def test_background_refresh_stores_official_average_date_and_source(self):
        self.assertTrue(hasattr(cache, "refresh_strategy_btc_cost"))
        result = cache.refresh_strategy_btc_cost()
        self.assertEqual(result["avg_cost_usd"], 75446)
        self.assertEqual(result["as_of"], "2026-10-19")
        self.assertEqual(result["source_url"], COST["source_url"])
        self.assertFalse(result["stale"])
        self.assertIsNone(result["error"])
        self.assertEqual(cache.strategy_btc_cost()["avg_cost_usd"], 75446)
        self.fetch.assert_called_once_with()

    def test_checks_once_per_day_then_refreshes(self):
        self.assertTrue(hasattr(cache, "refresh_strategy_btc_cost"))
        cache.refresh_strategy_btc_cost()
        self.fetch.return_value = {**COST, "avg_cost_usd": 76000}
        self.assertEqual(cache.refresh_strategy_btc_cost()["avg_cost_usd"], 75446)
        self.assertEqual(self.fetch.call_count, 1)
        self.expire_attempt()
        self.assertEqual(cache.refresh_strategy_btc_cost()["avg_cost_usd"], 76000)
        self.assertEqual(self.fetch.call_count, 2)

    def test_concurrent_background_refreshes_only_launch_one_browser(self):
        entered = Event()
        release = Event()

        def fetch():
            if entered.is_set():
                return dict(COST)
            entered.set()
            if not release.wait(timeout=5):
                raise RuntimeError("test did not release source fetch")
            return dict(COST)

        self.fetch.side_effect = fetch
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(cache.refresh_strategy_btc_cost)
            try:
                self.assertTrue(entered.wait(timeout=5))
                second = executor.submit(cache.refresh_strategy_btc_cost)
                second.result(timeout=2)
                self.assertEqual(self.fetch.call_count, 1)
                # The browser must not hold the database lock used by transactions.
                cache.set_cache("unrelated", {"written": True})
            finally:
                release.set()
            self.assertEqual(first.result(timeout=5)["avg_cost_usd"], 75446)
        self.assertFalse(cache.strategy_btc_cost()["stale"])

    def test_abandoned_collection_can_retry_after_five_minutes(self):
        cache.set_cache(cache.STRATEGY_BTC_COST_ATTEMPT_KEY, {"pending": "abandoned", "error": None})
        cache.refresh_strategy_btc_cost()
        self.fetch.assert_not_called()
        with db.connect() as conn:
            conn.execute(
                "UPDATE api_cache SET updated_at = ? WHERE cache_key = ?",
                ((datetime.now() - timedelta(minutes=6)).isoformat(), cache.STRATEGY_BTC_COST_ATTEMPT_KEY),
            )
        self.assertEqual(cache.refresh_strategy_btc_cost()["avg_cost_usd"], 75446)
        self.fetch.assert_called_once_with()

    def test_replaced_collection_cannot_overwrite_newer_success(self):
        cache.set_cache(cache.STRATEGY_BTC_COST_KEY, COST)

        def replaced_fetch():
            cache.set_cache(cache.STRATEGY_BTC_COST_KEY, {**COST, "avg_cost_usd": 76000})
            cache.set_cache(cache.STRATEGY_BTC_COST_ATTEMPT_KEY, {"error": None})
            raise strategy.StrategySourceError("older browser failed")

        self.fetch.side_effect = replaced_fetch
        with self.assertLogs("dca_tracker.cache", level="WARNING"):
            result = cache.refresh_strategy_btc_cost()
        self.assertEqual(result["avg_cost_usd"], 76000)
        self.assertFalse(result["stale"])
        self.assertIsNone(result["error"])

    def test_failure_keeps_last_value_and_success_timestamp(self):
        self.assertTrue(hasattr(cache, "refresh_strategy_btc_cost"))
        cache.refresh_strategy_btc_cost()
        before = cache.get_cache(cache.STRATEGY_BTC_COST_KEY)["cache"]["updated_at"]
        self.expire_attempt()
        self.fetch.side_effect = strategy.StrategySourceError("Chrome timed out")
        with self.assertLogs("dca_tracker.cache", level="WARNING") as logs:
            result = cache.refresh_strategy_btc_cost()
        self.assertEqual(result["avg_cost_usd"], 75446)
        self.assertTrue(result["stale"])
        self.assertEqual(result["error"]["code"], "source_unavailable")
        self.assertEqual(cache.get_cache(cache.STRATEGY_BTC_COST_KEY)["cache"]["updated_at"], before)
        self.assertIn("Traceback", "\n".join(logs.output))
        self.assertTrue(cache.strategy_btc_cost()["stale"])

    def test_failed_attempt_does_not_relaunch_browser_on_every_update(self):
        self.assertTrue(hasattr(cache, "refresh_strategy_btc_cost"))
        self.fetch.side_effect = strategy.StrategySourceError("Chrome missing")
        with self.assertLogs("dca_tracker.cache", level="WARNING"):
            first = cache.refresh_strategy_btc_cost()
        self.assertIsNone(first["avg_cost_usd"])
        self.assertIsNone(cache.get_cache(cache.STRATEGY_BTC_COST_KEY))
        self.assertTrue(cache.refresh_strategy_btc_cost()["stale"])
        self.assertEqual(self.fetch.call_count, 1)
        self.expire_attempt()
        self.fetch.side_effect = None
        self.assertEqual(cache.refresh_strategy_btc_cost()["avg_cost_usd"], 75446)
        self.assertEqual(self.fetch.call_count, 2)

    def test_source_change_refreshes_preexisting_third_party_cache(self):
        self.assertTrue(hasattr(cache, "refresh_strategy_btc_cost"))
        cache.set_cache(cache.STRATEGY_BTC_COST_KEY, {"avg_cost_usd": 75444, "as_of": "2026-10-11", "source_url": "https://www.strategy.com/press/previous"})
        self.assertEqual(cache.strategy_btc_cost()["avg_cost_usd"], 75444)
        self.fetch.assert_not_called()
        self.assertEqual(cache.refresh_strategy_btc_cost()["avg_cost_usd"], 75446)
        self.fetch.assert_called_once_with()

    def test_invalid_source_is_identifiable_and_preserves_last_value(self):
        self.assertTrue(hasattr(cache, "refresh_strategy_btc_cost"))
        cache.refresh_strategy_btc_cost()
        self.expire_attempt()
        self.fetch.side_effect = ValueError("missing __NEXT_DATA__")
        with self.assertLogs("dca_tracker.cache", level="WARNING"):
            result = cache.refresh_strategy_btc_cost()
        self.assertEqual(result["avg_cost_usd"], 75446)
        self.assertEqual(result["error"]["code"], "invalid_source_data")

    def test_unexpected_programming_error_propagates(self):
        self.assertTrue(hasattr(cache, "refresh_strategy_btc_cost"))
        self.fetch.side_effect = RuntimeError("unexpected implementation error")
        with self.assertRaisesRegex(RuntimeError, "unexpected implementation error"):
            cache.refresh_strategy_btc_cost()

    def test_reference_failure_does_not_change_dashboard_advice_or_totals(self):
        self.assertTrue(hasattr(cache, "refresh_strategy_btc_cost"))
        self.fetch.side_effect = strategy.StrategySourceError("offline")
        decision = Mock(asset="BTC", price=82002.12, recommended_amount=100, data_ok=True)
        decision.reason = "DCA Score 33.5"
        summary = {"total_cost": 100, "total_value": 101}
        with self.assertLogs("dca_tracker.cache", level="WARNING"):
            cache.refresh_strategy_btc_cost()
        with patch.object(cache.rules, "all_decisions", return_value=[decision]), patch.object(cache.portfolio, "portfolio_summary", return_value=summary):
            result = cache.build_dashboard()
        self.assertFalse(result["has_errors"])
        self.assertEqual(result["monthly_plan"], 100)
        self.assertEqual(result["portfolio"], summary)
        self.assertIsNone(result["strategy_btc"]["avg_cost_usd"])
        self.assertEqual(self.fetch.call_count, 1)

    def test_scheduled_update_refreshes_cost_before_caching_dashboard(self):
        with patch.object(cache.rules, "all_decisions", return_value=[]), patch.object(cache.portfolio, "portfolio_summary", return_value={}), patch.object(cache, "build_performance", return_value={"series": []}):
            cache.refresh_all()
        dashboard = cache.get_cache(cache.DASHBOARD_KEY)
        self.assertEqual(dashboard["strategy_btc"]["avg_cost_usd"], 75446)
        self.fetch.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
