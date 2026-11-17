from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from requests import ConnectionError

import app
from dca_tracker import cache, db, fear_greed


class FearGreedTest(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        path = patch.object(db, "DB_PATH", Path(temp.name) / "test.db")
        path.start()
        self.addCleanup(path.stop)
        db.init_db()
        self.now = datetime(2026, 11, 17, 8, tzinfo=timezone.utc)
        self.send = Mock(return_value=True)
        self.request = patch.object(fear_greed.requests, "get")
        self.get = self.request.start()
        self.addCleanup(self.request.stop)

    def set_reading(self, value, at=None):
        timestamp = int((at or self.now).replace(hour=0).timestamp())
        self.get.return_value.json.return_value = {
            "data": [{"value": value, "timestamp": str(timestamp)}],
            "metadata": {"error": None},
        }

    def poll(self):
        return fear_greed.poll(self.send, now=self.now)

    def advance(self, value):
        self.now += timedelta(days=1)
        self.set_reading(value)
        return self.poll()

    def test_extreme_values_send_once_each_index_date_even_on_consecutive_days(self):
        self.set_reading("19")
        self.assertEqual(self.poll(), "sent")
        self.assertIn("19/100", self.send.call_args.args[0])
        self.assertEqual(self.poll(), "unchanged")
        self.assertEqual(self.get.call_count, 1)
        self.assertEqual(self.advance("0"), "sent")
        self.assertEqual(self.poll(), "unchanged")
        self.assertEqual(self.advance("20"), "unchanged")
        self.assertEqual(self.advance("80"), "unchanged")
        self.assertEqual(self.send.call_count, 2)
        self.assertEqual(self.advance("81"), "sent")
        self.assertIn("81/100", self.send.call_args.args[0])
        self.assertEqual(self.advance("100"), "sent")
        self.assertEqual(self.poll(), "unchanged")
        self.assertEqual(self.send.call_count, 4)

    def test_failed_delivery_retries_the_same_reading(self):
        self.set_reading("93")
        self.send.side_effect = [False, True]
        self.assertEqual(self.poll(), "send_failed")
        self.assertEqual(self.poll(), "sent")
        self.assertEqual(self.poll(), "unchanged")
        self.assertEqual(self.send.call_count, 2)
        self.assertEqual(self.get.call_count, 1)

    def test_overlapping_jobs_do_not_send_the_same_alert(self):
        self.set_reading("9")
        self.send.side_effect = lambda _: self.assertEqual(self.poll(), "in_progress") or True
        self.assertEqual(self.poll(), "sent")
        self.assertEqual(self.send.call_count, 1)

    def test_bad_or_yesterday_reading_keeps_last_value_without_sending(self):
        self.set_reading("55")
        self.poll()
        self.now += timedelta(days=1)
        for value, at in [(True, self.now), ("55.5", self.now), ("101", self.now),
                          ("95", self.now + timedelta(days=1)), ("95", self.now - timedelta(days=1))]:
            with self.subTest(value=value, at=at), patch.object(fear_greed, "logger"):
                self.set_reading(value, at)
                self.poll()
                result = fear_greed.snapshot(now=self.now)
                self.assertEqual(result["value"], 55)
                self.assertTrue(result["stale"])
        self.get.side_effect = ConnectionError("offline")
        with patch.object(fear_greed, "logger"):
            self.assertEqual(self.poll(), "unavailable")
        self.send.assert_not_called()

    def test_cached_dashboard_reads_the_latest_index_without_fetching(self):
        cache.set_cache(cache.DASHBOARD_KEY, {"decisions": []})
        self.set_reading("17")
        self.poll()
        with patch.object(fear_greed, "_now", return_value=self.now):
            result = app.dashboard(None, False)
        self.assertEqual(result["fear_greed"], {"value": 17, "as_of": "2026-11-17", "stale": False})
        self.assertEqual(self.get.call_count, 1)


if __name__ == "__main__":
    unittest.main()
