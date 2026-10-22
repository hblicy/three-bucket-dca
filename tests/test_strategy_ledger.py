from __future__ import annotations

import copy
import json
import subprocess
import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from dca_tracker import strategy


ROW = {"date_of_purchase": "2026-10-19", "average_price": 75446, "purchase_price": 82900, "btc_holdings": 848517, "total_acquisition_cost": 64017}


def ledger_html(rows):
    return "<html><script type='application/json' id='__NEXT_DATA__'>" + json.dumps({"props": {"pageProps": {"bitcoinData": rows}}}) + "</script></html>"


class StrategyLedgerTest(unittest.TestCase):
    def parse(self, html):
        self.assertTrue(hasattr(strategy, "parse_btc_cost"), "missing ledger parser")
        return strategy.parse_btc_cost(html)

    def test_uses_latest_disclosed_average_instead_of_batch_price_or_rounded_total(self):
        older = {**ROW, "date_of_purchase": "2026-10-12", "average_price": 75444}
        result = self.parse(ledger_html([ROW, older]))
        self.assertEqual(result, {"avg_cost_usd": 75446.0, "as_of": "2026-10-19", "source_url": "https://www.strategy.com/ledger"})

    def test_latest_sale_record_is_still_used(self):
        result = self.parse(ledger_html([{**ROW, "sale": True, "count": -267}]))
        self.assertEqual(result["avg_cost_usd"], 75446)

    def test_rejects_missing_script_bad_json_and_empty_or_invalid_rows(self):
        samples = ["<html>Access Denied</html>", "<script id='__NEXT_DATA__'>not JSON</script>", ledger_html([]), ledger_html([None]), ledger_html([{"date_of_purchase": "bad"}]), ledger_html([{**ROW, "date_of_purchase": (date.today() + timedelta(days=2)).isoformat()}])]
        for html in samples:
            with self.subTest(html=html), self.assertRaises(ValueError):
                self.parse(html)

    def test_invalid_latest_average_does_not_fall_back_to_an_older_record(self):
        for value in (None, 0, -1, float("nan"), float("inf"), True, "75446"):
            rows = [{**ROW, "average_price": value}, {**ROW, "date_of_purchase": "2026-10-12", "average_price": 75444}]
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.parse(ledger_html(rows))

    def test_browser_runs_headless_with_timeout_and_private_profile(self):
        response = SimpleNamespace(returncode=0, stdout=ledger_html([ROW]), stderr="")
        with patch.object(strategy.shutil, "which", return_value="google-chrome"), patch.object(strategy.subprocess, "run", return_value=response) as run:
            result = strategy.fetch_btc_cost()
        self.assertEqual(result["avg_cost_usd"], 75446)
        args, kwargs = run.call_args
        self.assertIn("--headless=new", args[0])
        self.assertIn("--dump-dom", args[0])
        self.assertIn("https://www.strategy.com/ledger", args[0])
        self.assertTrue(any(value.startswith("--user-data-dir=") for value in args[0]))
        self.assertNotIn("--no-sandbox", args[0])
        self.assertEqual(kwargs["timeout"], 90)

    def test_browser_timeout_and_failed_exit_are_source_errors(self):
        self.assertTrue(hasattr(strategy, "StrategySourceError"))
        with patch.object(strategy.shutil, "which", return_value="google-chrome"), patch.object(strategy.subprocess, "run", side_effect=subprocess.TimeoutExpired("chrome", 90)):
            with self.assertRaises(strategy.StrategySourceError):
                strategy.fetch_btc_cost()
        with patch.object(strategy.shutil, "which", return_value="google-chrome"), patch.object(strategy.subprocess, "run", return_value=SimpleNamespace(returncode=1, stdout="", stderr="sandbox unavailable")):
            with self.assertRaisesRegex(strategy.StrategySourceError, "sandbox unavailable"):
                strategy.fetch_btc_cost()

    def test_missing_browser_is_an_identifiable_source_error(self):
        self.assertTrue(hasattr(strategy, "StrategySourceError"))
        with patch.object(strategy.shutil, "which", return_value=None), patch.object(strategy.Path, "is_file", return_value=False):
            with self.assertRaisesRegex(strategy.StrategySourceError, "Chrome|Chromium"):
                strategy.fetch_btc_cost()


if __name__ == "__main__":
    unittest.main()
