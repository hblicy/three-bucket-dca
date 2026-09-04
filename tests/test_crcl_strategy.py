from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import numpy as np
import pandas as pd

from dca_tracker import rules


class FakeResponse:
    def __init__(
        self,
        current: float,
        previous_day: float,
        *,
        current_date: datetime | None = None,
    ):
        self.current = current
        self.previous_day = previous_day
        self.current_date = current_date or datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    def raise_for_status(self):
        return None

    def json(self):
        previous_date = self.current_date - timedelta(days=1)
        return [
            {
                "date": str(int(previous_date.timestamp())),
                "totalCirculating": {"peggedUSD": self.previous_day},
            },
            {
                "date": str(int(self.current_date.timestamp())),
                "totalCirculating": {"peggedUSD": self.current},
            },
        ]


class CrclStrategyTest(unittest.TestCase):
    def setUp(self):
        rules._crcl_risk_cache.update({"data": None, "timestamp": 0.0})

    def decision(
        self,
        closes,
        usdc_current=100.0,
        usdc_previous_day=100.0,
        *,
        usdc_current_date: datetime | None = None,
    ):
        history = pd.DataFrame({"Close": closes})
        with (
            patch.object(rules.prices, "history", return_value=history),
            patch.object(
                rules.requests,
                "get",
                return_value=FakeResponse(
                    usdc_current,
                    usdc_previous_day,
                    current_date=usdc_current_date,
                ),
            ),
        ):
            return rules.crcl_decision()

    def test_price_score_is_normalized_from_85_points(self):
        decision = self.decision([100.0] * 90)
        expected_raw = 0.0 * 0.40 + 12.5 + 20.0
        self.assertTrue(decision.data_ok)
        self.assertAlmostEqual(decision.metrics.get("technical_raw", -1), expected_raw)
        self.assertAlmostEqual(decision.metrics["score"], expected_raw / 85.0 * 100.0)
        self.assertEqual(decision.metrics["multiplier"], 1.0)
        self.assertEqual(decision.recommended_amount, 100.0)

    def test_price_inputs_keep_expensive_and_cheap_directions(self):
        expensive = self.decision(np.arange(1.0, 91.0))
        self.setUp()
        cheap = self.decision(np.arange(90.0, 0.0, -1.0))
        self.assertEqual(expensive.metrics["multiplier"], 0.0)
        self.assertEqual(cheap.metrics["multiplier"], 2.0)
        self.assertGreater(expensive.metrics["score"], cheap.metrics["score"])

    def test_usdc_24h_drop_over_five_percent_is_hard_pause(self):
        decision = self.decision([100.0] * 90, usdc_current=94.0, usdc_previous_day=100.0)
        self.assertTrue(decision.data_ok)
        self.assertEqual(decision.status, "pause")
        self.assertEqual(decision.recommended_amount, 0.0)
        self.assertEqual(decision.metrics.get("multiplier"), 0.0)
        self.assertTrue(decision.metrics.get("usdc_risk", {}).get("alert"))

    def test_stale_usdc_source_date_produces_no_formal_advice(self):
        stale_date = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=2)
        decision = self.decision(
            [100.0] * 90,
            usdc_current=100.0,
            usdc_previous_day=100.0,
            usdc_current_date=stale_date,
        )
        self.assertFalse(decision.data_ok)
        self.assertEqual(decision.status, "error")
        self.assertEqual(decision.recommended_amount, 0.0)
        self.assertIn("USDC 流通量源数据已过期", decision.reason)

    def test_crcl_risk_cache_is_refreshed_after_five_minutes(self):
        rules._crcl_risk_cache.update({
            "data": {"drop_24h_pct": 0.0, "alert": False, "source": "stale-cache"},
            "timestamp": 1_000.0,
        })
        with (
            patch.object(rules.time, "time", return_value=1_301.0),
            patch.object(rules.requests, "get", return_value=FakeResponse(100.0, 100.0)) as getter,
        ):
            risk = rules._crcl_usdc_risk()
        getter.assert_called_once_with(
            "https://stablecoins.llama.fi/stablecoincharts/all?stablecoin=2",
            timeout=10,
        )
        self.assertNotEqual(risk["source"], "stale-cache")

    def test_in_memory_cache_rechecks_usdc_source_date(self):
        stale_source_date = (
            datetime.now(timezone.utc).date() - timedelta(days=2)
        ).isoformat()
        rules._crcl_risk_cache.update({
            "data": {
                "drop_24h_pct": 0.0,
                "alert": False,
                "source": "stale-cache",
                "source_date": stale_source_date,
            },
            "timestamp": 1_000.0,
        })
        with (
            patch.object(rules.time, "time", return_value=1_100.0),
            patch.object(
                rules.requests,
                "get",
                return_value=FakeResponse(100.0, 100.0),
            ) as getter,
        ):
            risk = rules._crcl_usdc_risk()

        getter.assert_called_once()
        self.assertEqual(risk["source"], "DefiLlama")

    def test_usdc_drop_equal_to_five_percent_does_not_pause(self):
        decision = self.decision([100.0] * 90, usdc_current=95.0, usdc_previous_day=100.0)
        self.assertTrue(decision.data_ok)
        self.assertEqual(decision.status, "buy")
        self.assertFalse(decision.metrics["usdc_risk"]["alert"])

    def test_crcl_score_boundaries_use_confirmed_open_closed_ranges(self):
        multiplier_for = getattr(rules, "_crcl_multiplier", None)
        self.assertIsNotNone(multiplier_for)
        cases = [
            (25.0, 2.0),
            (25.000001, 1.0),
            (45.0, 1.0),
            (45.000001, 0.5),
            (65.0, 0.5),
            (65.000001, 0.0),
        ]
        for score, expected in cases:
            with self.subTest(score=score):
                self.assertEqual(multiplier_for(score), expected)

    def test_usdc_growth_reason_does_not_describe_negative_drop(self):
        decision = self.decision([100.0] * 90, usdc_current=110.0, usdc_previous_day=100.0)
        self.assertTrue(decision.data_ok)
        self.assertIn("增加 10.0%", decision.reason)
        self.assertNotIn("下降 -", decision.reason)

    def test_usdc_unchanged_reason_does_not_describe_negative_zero_growth(self):
        decision = self.decision([100.0] * 90, usdc_current=100.0, usdc_previous_day=100.0)
        self.assertTrue(decision.data_ok)
        self.assertIn("持平 0.0%", decision.reason)
        self.assertNotIn("-0.0%", decision.reason)

    def test_non_finite_usdc_values_produce_no_formal_advice(self):
        cases = [
            (float("nan"), 100.0),
            (float("inf"), 100.0),
            (100.0, float("nan")),
            (100.0, float("inf")),
        ]
        for current, previous_day in cases:
            with self.subTest(current=current, previous_day=previous_day):
                self.setUp()
                decision = self.decision(
                    [100.0] * 90,
                    usdc_current=current,
                    usdc_previous_day=previous_day,
                )
                self.assertFalse(decision.data_ok)
                self.assertEqual(decision.status, "error")
                self.assertEqual(decision.recommended_amount, 0.0)

    def test_usdc_risk_data_failure_produces_no_formal_advice(self):
        history = pd.DataFrame({"Close": [100.0] * 90})
        with (
            patch.object(rules.prices, "history", return_value=history),
            patch.object(rules.requests, "get", side_effect=RuntimeError("network down")),
        ):
            decision = rules.crcl_decision()
        self.assertFalse(decision.data_ok)
        self.assertEqual(decision.status, "error")
        self.assertEqual(decision.recommended_amount, 0.0)

    def test_incomplete_price_history_produces_no_formal_advice(self):
        decision = self.decision([100.0] * 89)
        self.assertFalse(decision.data_ok)
        self.assertEqual(decision.status, "error")
        self.assertEqual(decision.recommended_amount, 0.0)

    def test_invalid_latest_raw_close_produces_no_formal_advice(self):
        usdc_risk = {"drop_24h_pct": 0.0, "alert": False, "source": "test"}
        for invalid_close in (np.nan, np.inf, -np.inf):
            with self.subTest(invalid_close=invalid_close):
                history = pd.DataFrame({"Close": [100.0] * 90 + [invalid_close]})
                with (
                    patch.object(rules.prices.yf, "download", return_value=history),
                    patch.object(rules, "_crcl_usdc_risk", return_value=usdc_risk),
                ):
                    decision = rules.crcl_decision()
                self.assertFalse(decision.data_ok)
                self.assertEqual(decision.status, "error")
                self.assertEqual(decision.recommended_amount, 0.0)

    def test_invalid_crcl_base_amount_produces_no_formal_advice(self):
        history = pd.DataFrame({"Close": [100.0] * 90})
        risk = {"drop_24h_pct": 0.0, "alert": False, "source": "test"}
        for invalid_amount in (float("nan"), float("inf"), 0.0, -100.0):
            with self.subTest(invalid_amount=invalid_amount):
                with (
                    patch.object(rules, "CRCL_BASE_AMOUNT", invalid_amount),
                    patch.object(rules.prices, "history", return_value=history),
                    patch.object(rules, "_crcl_usdc_risk", return_value=risk),
                ):
                    decision = rules.crcl_decision()
                self.assertFalse(decision.data_ok)
                self.assertEqual(decision.status, "error")
                self.assertEqual(decision.recommended_amount, 0.0)


if __name__ == "__main__":
    unittest.main()
