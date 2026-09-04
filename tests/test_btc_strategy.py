from __future__ import annotations

import inspect
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
import pandas as pd
import requests

from dca_tracker import notify, prices, rules


class FakeResponse:
    def __init__(self, *, text: str = "", payload: dict | None = None, status_code: int = 200):
        self.text = text
        self._payload = payload or {}
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class BtcStrategyTest(unittest.TestCase):
    def test_score_uses_fixed_40_25_35_weights(self):
        score = prices.btc_dca_score(proxy_z=-1.0, ahr999=0.5, puell=1.5)
        self.assertEqual(score, 27.5)

    def test_score_requires_all_three_indicators(self):
        self.assertTrue(np.isnan(prices.btc_dca_score(proxy_z=-1.0, ahr999=0.5, puell=np.nan)))

    def test_score_rejects_non_finite_raw_indicators(self):
        cases = [
            (np.inf, 0.5, 1.5),
            (-np.inf, 0.5, 1.5),
            (-1.0, np.inf, 1.5),
            (-1.0, -np.inf, 1.5),
            (-1.0, 0.5, np.inf),
            (-1.0, 0.5, -np.inf),
        ]
        for proxy_z, ahr999, puell in cases:
            with self.subTest(proxy_z=proxy_z, ahr999=ahr999, puell=puell):
                self.assertTrue(np.isnan(prices.btc_dca_score(proxy_z, ahr999, puell)))

    def test_percentile_boundaries_map_to_confirmed_multipliers(self):
        cases = [
            (10.0, 4.0),
            (20.0, 2.0),
            (30.0, 1.0),
            (40.0, 0.5),
            (50.0, 0.0),
        ]
        for score, expected in cases:
            with self.subTest(score=score):
                frame = pd.DataFrame([
                    {
                        "Price": 100_000.0,
                        "proxy_z": 0.0,
                        "ahr999": 1.0,
                        "puell": 1.0,
                        "dca_score": score,
                        "p10": 10.0,
                        "p25": 20.0,
                        "p50": 30.0,
                        "p75": 40.0,
                    }
                ])
                with patch.object(rules.prices, "btc_indicators", return_value=frame):
                    decision = rules.btc_decision()
                self.assertTrue(decision.data_ok)
                self.assertEqual(decision.metrics["multiplier"], expected)

    def test_latest_incomplete_row_does_not_fall_back_to_stale_signal(self):
        frame = pd.DataFrame([
            {
                "Price": 90_000.0,
                "proxy_z": 0.0,
                "ahr999": 1.0,
                "puell": 1.0,
                "dca_score": 30.0,
                "p10": 10.0,
                "p25": 20.0,
                "p50": 30.0,
                "p75": 40.0,
            },
            {
                "Price": 100_000.0,
                "proxy_z": 0.0,
                "ahr999": 1.0,
                "puell": np.nan,
                "dca_score": np.nan,
                "p10": 10.0,
                "p25": 20.0,
                "p50": 30.0,
                "p75": 40.0,
            },
        ])
        with patch.object(rules.prices, "btc_indicators", return_value=frame):
            decision = rules.btc_decision()
        self.assertFalse(decision.data_ok)
        self.assertEqual(decision.status, "error")
        self.assertEqual(decision.recommended_amount, 0.0)

    def test_invalid_btc_base_amount_produces_no_formal_advice(self):
        frame = pd.DataFrame([{
            "Price": 100_000.0,
            "proxy_z": 0.0,
            "ahr999": 1.0,
            "puell": 1.0,
            "dca_score": 30.0,
            "p10": 10.0,
            "p25": 20.0,
            "p50": 40.0,
            "p75": 50.0,
        }])
        for invalid_amount in (float("nan"), float("inf"), 0.0, -100.0):
            with self.subTest(invalid_amount=invalid_amount):
                with (
                    patch.object(rules.prices, "btc_indicators", return_value=frame),
                    patch.object(rules, "BTC_BASE_AMOUNT", invalid_amount),
                ):
                    decision = rules.btc_decision()
                self.assertFalse(decision.data_ok)
                self.assertEqual(decision.status, "error")
                self.assertEqual(decision.recommended_amount, 0.0)

    def test_invalid_latest_raw_btc_close_is_rejected_before_dropping(self):
        dates = pd.to_datetime(["2026-09-03", "2026-09-04"])
        for invalid_close in (np.nan, np.inf, -np.inf, 0.0, -1.0):
            with self.subTest(invalid_close=invalid_close):
                frame = pd.DataFrame({"Date": dates, "Close": [100_000.0, invalid_close]})
                with patch.object(prices.yf, "download", return_value=frame):
                    with self.assertRaises(RuntimeError):
                        prices.btc_history()

    def test_stale_btc_history_is_rejected(self):
        observed_at = datetime(2026, 9, 4, 8, 0, tzinfo=timezone.utc)
        frame = pd.DataFrame({
            "Date": pd.to_datetime(["2026-08-01", "2026-08-02"]),
            "Close": [99_000.0, 100_000.0],
        })
        with (
            patch.object(prices.yf, "download", return_value=frame),
            patch.object(prices, "_utc_now", return_value=observed_at),
        ):
            with self.assertRaises(RuntimeError):
                prices.btc_history()

    def test_stale_equity_history_is_rejected(self):
        observed_at = datetime(2026, 9, 4, 8, 0, tzinfo=timezone.utc)
        frame = pd.DataFrame(
            {"Close": [99.0, 100.0]},
            index=pd.to_datetime(["2026-08-01", "2026-08-02"]),
        )
        with (
            patch.object(prices.yf, "download", return_value=frame),
            patch.object(prices, "_utc_now", return_value=observed_at),
        ):
            with self.assertRaises(RuntimeError):
                prices.history("CRCL")

    def test_block_reward_has_no_calendar_cutoff_before_actual_halving(self):
        try:
            reward = prices._block_reward(pd.Timestamp("2028-01-01"))
        except RuntimeError as exc:
            self.fail(f"2028-01-01 must not be a calendar cutoff: {exc}")
        self.assertEqual(reward, 3.125)

    def test_reward_switches_on_timestamp_of_block_1050000(self):
        resolver = getattr(prices, "_confirmed_2028_halving_time", None)
        self.assertIsNotNone(resolver)
        self.assertIn("confirmed_2028_halving_time", inspect.signature(prices._block_reward).parameters)

        block_timestamp = int(pd.Timestamp("2028-04-19 12:34:56", tz="UTC").timestamp())
        responses = [
            FakeResponse(text="1050000"),
            FakeResponse(text="0000000000000000000halving"),
            FakeResponse(payload={"height": 1_050_000, "timestamp": block_timestamp}),
            FakeResponse(text="1050000"),
            FakeResponse(text="0000000000000000000halving"),
        ]
        with TemporaryDirectory() as tmpdir:
            with (
                patch.object(prices, "BTC_HALVING_CACHE_FILE", Path(tmpdir) / "btc_halving.json", create=True),
                patch.object(
                    prices,
                    "_utc_now",
                    return_value=datetime(2028, 4, 19, 13, 0, tzinfo=timezone.utc),
                    create=True,
                ),
                patch("requests.get", side_effect=responses) as request_get,
            ):
                actual_time = resolver()

        self.assertEqual(actual_time, pd.Timestamp("2028-04-19 12:34:56"))
        self.assertEqual(
            request_get.call_args_list[0].args[0],
            "https://blockstream.info/api/blocks/tip/height",
        )
        self.assertEqual(
            request_get.call_args_list[1].args[0],
            "https://blockstream.info/api/block-height/1050000",
        )
        self.assertEqual(
            prices._block_reward(
                pd.Timestamp("2028-04-19 12:34:55"),
                confirmed_2028_halving_time=actual_time,
            ),
            3.125,
        )
        self.assertEqual(
            prices._block_reward(
                pd.Timestamp("2028-04-19 12:34:56"),
                confirmed_2028_halving_time=actual_time,
            ),
            1.5625,
        )

    def test_unmined_block_1050000_does_not_change_reward(self):
        resolver = getattr(prices, "_confirmed_2028_halving_time", None)
        self.assertIsNotNone(resolver)
        with TemporaryDirectory() as tmpdir:
            with (
                patch.object(prices, "BTC_HALVING_CACHE_FILE", Path(tmpdir) / "btc_halving.json", create=True),
                patch("requests.get", return_value=FakeResponse(text="1049999")),
            ):
                actual_time = resolver()
        self.assertIsNone(actual_time)

    def test_negative_tip_height_is_rejected(self):
        with TemporaryDirectory() as tmpdir:
            with (
                patch.object(prices, "BTC_HALVING_CACHE_FILE", Path(tmpdir) / "btc_halving.json", create=True),
                patch("requests.get", return_value=FakeResponse(text="-1")),
            ):
                with self.assertRaises(RuntimeError):
                    prices._fetch_confirmed_2028_halving_time()

    def test_fresh_unmined_halving_cache_avoids_network_request_when_far_from_target(self):
        resolver = getattr(prices, "_confirmed_2028_halving_time", None)
        self.assertIsNotNone(resolver)
        with TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "btc_halving.json"
            cache_file.write_text(json.dumps({
                "target_height": 1_050_000,
                "tip_height": 1_049_000,
                "halving_time": None,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }), encoding="utf-8")
            with (
                patch.object(prices, "BTC_HALVING_CACHE_FILE", cache_file, create=True),
                patch("requests.get") as request_get,
            ):
                self.assertIsNone(resolver())
        request_get.assert_not_called()

    def test_halving_cache_write_failure_is_not_reported_as_success(self):
        with TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "btc_halving.json"
            with (
                patch.object(prices, "BTC_HALVING_CACHE_FILE", cache_file, create=True),
                patch.object(Path, "write_text", side_effect=OSError("disk full")),
                self.assertRaisesRegex(OSError, "disk full"),
            ):
                prices._write_btc_halving_cache(1_049_000, None)

        self.assertFalse(cache_file.exists())

    def test_halving_cache_replace_failure_is_not_reported_as_success_and_cleans_temp_file(self):
        with TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "btc_halving.json"
            temp_file = cache_file.with_name(f"{cache_file.name}.{prices.os.getpid()}.tmp")
            with (
                patch.object(prices, "BTC_HALVING_CACHE_FILE", cache_file, create=True),
                patch.object(Path, "replace", side_effect=OSError("replace failed")),
                self.assertRaisesRegex(OSError, "replace failed"),
            ):
                prices._write_btc_halving_cache(1_049_000, None)

            self.assertFalse(cache_file.exists())
            self.assertFalse(temp_file.exists())

    def test_near_target_unmined_cache_refreshes_on_next_scheduled_update(self):
        resolver = getattr(prices, "_confirmed_2028_halving_time", None)
        self.assertIsNotNone(resolver)
        block_timestamp = int(pd.Timestamp("2028-04-19 12:34:56", tz="UTC").timestamp())
        responses = [
            FakeResponse(text="1050000"),
            FakeResponse(text="0000000000000000000halving"),
            FakeResponse(payload={"height": 1_050_000, "timestamp": block_timestamp}),
            FakeResponse(text="1050000"),
            FakeResponse(text="0000000000000000000halving"),
        ]
        with TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "btc_halving.json"
            cache_file.write_text(json.dumps({
                "target_height": 1_050_000,
                "tip_height": 1_049_999,
                "halving_time": None,
                "checked_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
            }), encoding="utf-8")
            with (
                patch.object(prices, "BTC_HALVING_CACHE_FILE", cache_file, create=True),
                patch.object(
                    prices,
                    "_utc_now",
                    return_value=datetime(2028, 4, 19, 13, 0, tzinfo=timezone.utc),
                    create=True,
                ),
                patch("requests.get", side_effect=responses) as request_get,
            ):
                actual_time = resolver()

        self.assertEqual(actual_time, pd.Timestamp("2028-04-19 12:34:56"))
        self.assertEqual(request_get.call_count, 5)

    def test_stale_near_target_cache_fails_closed_when_blockstream_is_unavailable(self):
        resolver = getattr(prices, "_confirmed_2028_halving_time", None)
        self.assertIsNotNone(resolver)
        with TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "btc_halving.json"
            cache_file.write_text(json.dumps({
                "target_height": 1_050_000,
                "tip_height": 1_049_999,
                "halving_time": None,
                "checked_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
            }), encoding="utf-8")
            with (
                patch.object(prices, "BTC_HALVING_CACHE_FILE", cache_file, create=True),
                patch("requests.get", side_effect=requests.ConnectionError("offline")) as request_get,
            ):
                with self.assertRaises(requests.ConnectionError):
                    resolver()

        request_get.assert_called_once()

    def test_stale_halving_cache_is_used_when_blockstream_is_unavailable(self):
        resolver = getattr(prices, "_confirmed_2028_halving_time", None)
        self.assertIsNotNone(resolver)
        with TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "btc_halving.json"
            cache_file.write_text(json.dumps({
                "target_height": 1_050_000,
                "tip_height": 1_049_000,
                "halving_time": None,
                "checked_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
            }), encoding="utf-8")
            with (
                patch.object(prices, "BTC_HALVING_CACHE_FILE", cache_file, create=True),
                patch("requests.get", side_effect=requests.ConnectionError("offline")),
            ):
                self.assertIsNone(resolver())

    def test_invalid_confirmed_halving_times_are_rejected_from_cache(self):
        observed_at = datetime.now(timezone.utc)
        invalid_times = [
            "1970-01-01T00:00:00Z",
            (observed_at + timedelta(days=1)).isoformat(),
        ]
        for halving_time in invalid_times:
            with self.subTest(halving_time=halving_time), TemporaryDirectory() as tmpdir:
                cache_file = Path(tmpdir) / "btc_halving.json"
                cache_file.write_text(json.dumps({
                    "target_height": 1_050_000,
                    "tip_height": 1_050_000,
                    "halving_time": halving_time,
                    "checked_at": observed_at.isoformat(),
                }), encoding="utf-8")
                with patch.object(prices, "BTC_HALVING_CACHE_FILE", cache_file, create=True):
                    self.assertIsNone(prices._read_btc_halving_cache())

    def test_halving_time_before_target_height_is_rejected_from_cache(self):
        observed_at = datetime(2028, 4, 19, 13, 0, tzinfo=timezone.utc)
        with TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "btc_halving.json"
            cache_file.write_text(json.dumps({
                "target_height": 1_050_000,
                "tip_height": 1_049_999,
                "halving_time": "2028-04-19T12:34:56Z",
                "checked_at": observed_at.isoformat(),
            }), encoding="utf-8")
            with (
                patch.object(prices, "BTC_HALVING_CACHE_FILE", cache_file, create=True),
                patch.object(prices, "_utc_now", return_value=observed_at),
            ):
                self.assertIsNone(prices._read_btc_halving_cache())

    def test_target_height_without_halving_time_is_rejected_from_cache(self):
        observed_at = datetime(2028, 4, 19, 13, 0, tzinfo=timezone.utc)
        with TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "btc_halving.json"
            cache_file.write_text(json.dumps({
                "target_height": 1_050_000,
                "tip_height": 1_050_000,
                "halving_time": None,
                "block_hash": None,
                "checked_at": observed_at.isoformat(),
            }), encoding="utf-8")
            with (
                patch.object(prices, "BTC_HALVING_CACHE_FILE", cache_file, create=True),
                patch.object(prices, "_utc_now", return_value=observed_at),
            ):
                self.assertIsNone(prices._read_btc_halving_cache())

    def test_unfinalized_halving_cache_is_rechecked_after_ttl(self):
        resolver = getattr(prices, "_confirmed_2028_halving_time", None)
        self.assertIsNotNone(resolver)
        observed_at = datetime(2028, 4, 19, 13, 5, tzinfo=timezone.utc)
        with TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "btc_halving.json"
            cache_file.write_text(json.dumps({
                "target_height": 1_050_000,
                "tip_height": 1_050_000,
                "halving_time": "2028-04-19T12:34:56Z",
                "block_hash": "a" * 64,
                "checked_at": (observed_at - timedelta(minutes=5)).isoformat(),
            }), encoding="utf-8")
            with (
                patch.object(prices, "BTC_HALVING_CACHE_FILE", cache_file, create=True),
                patch.object(prices, "_utc_now", return_value=observed_at),
                patch("requests.get", return_value=FakeResponse(text="1049999")) as request_get,
            ):
                actual_time = resolver()

        self.assertIsNone(actual_time)
        self.assertEqual(request_get.call_count, 1)

    def test_unfinalized_halving_cache_refreshes_replaced_target_block(self):
        resolver = getattr(prices, "_confirmed_2028_halving_time", None)
        self.assertIsNotNone(resolver)
        observed_at = datetime(2028, 4, 19, 13, 5, tzinfo=timezone.utc)
        replacement_time = pd.Timestamp("2028-04-19 12:45:00")
        replacement_timestamp = int(replacement_time.tz_localize("UTC").timestamp())
        replacement_hash = "b" * 64
        responses = [
            FakeResponse(text="1050001"),
            FakeResponse(text=replacement_hash),
            FakeResponse(payload={"height": 1_050_000, "timestamp": replacement_timestamp}),
            FakeResponse(text="1050001"),
            FakeResponse(text=replacement_hash),
        ]
        with TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "btc_halving.json"
            cache_file.write_text(json.dumps({
                "target_height": 1_050_000,
                "tip_height": 1_050_000,
                "halving_time": "2028-04-19T12:34:56Z",
                "block_hash": "a" * 64,
                "checked_at": (observed_at - timedelta(minutes=5)).isoformat(),
            }), encoding="utf-8")
            with (
                patch.object(prices, "BTC_HALVING_CACHE_FILE", cache_file, create=True),
                patch.object(prices, "_utc_now", return_value=observed_at),
                patch("requests.get", side_effect=responses) as request_get,
            ):
                actual_time = resolver()
                refreshed_cache = json.loads(cache_file.read_text(encoding="utf-8"))

        self.assertEqual(actual_time, replacement_time)
        self.assertEqual(request_get.call_count, 5)
        self.assertEqual(refreshed_cache["block_hash"], replacement_hash)

    def test_finality_uses_rechecked_tip_after_target_block_details(self):
        resolver = getattr(prices, "_confirmed_2028_halving_time", None)
        self.assertIsNotNone(resolver)
        observed_at = datetime(2028, 4, 19, 13, 5, tzinfo=timezone.utc)
        block_time = pd.Timestamp("2028-04-19 12:45:00")
        block_timestamp = int(block_time.tz_localize("UTC").timestamp())
        block_hash = "a" * 64
        responses = [
            FakeResponse(text="1050005"),
            FakeResponse(text=block_hash),
            FakeResponse(payload={"height": 1_050_000, "timestamp": block_timestamp}),
            FakeResponse(text="1050001"),
            FakeResponse(text=block_hash),
        ]
        with TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "btc_halving.json"
            with (
                patch.object(prices, "BTC_HALVING_CACHE_FILE", cache_file, create=True),
                patch.object(prices, "_utc_now", return_value=observed_at),
                patch("requests.get", side_effect=responses) as request_get,
            ):
                actual_time = resolver()
                refreshed_cache = json.loads(cache_file.read_text(encoding="utf-8"))

        self.assertEqual(actual_time, block_time)
        self.assertEqual(request_get.call_count, 5)
        self.assertEqual(refreshed_cache["tip_height"], 1_050_001)

    def test_inflight_target_block_replacement_fails_closed(self):
        resolver = getattr(prices, "_confirmed_2028_halving_time", None)
        self.assertIsNotNone(resolver)
        observed_at = datetime(2028, 4, 19, 13, 5, tzinfo=timezone.utc)
        block_timestamp = int(pd.Timestamp("2028-04-19 12:45:00", tz="UTC").timestamp())
        responses = [
            FakeResponse(text="1050005"),
            FakeResponse(text="a" * 64),
            FakeResponse(payload={"height": 1_050_000, "timestamp": block_timestamp}),
            FakeResponse(text="1050005"),
            FakeResponse(text="b" * 64),
        ]
        with TemporaryDirectory() as tmpdir:
            with (
                patch.object(prices, "BTC_HALVING_CACHE_FILE", Path(tmpdir) / "btc_halving.json", create=True),
                patch.object(prices, "_utc_now", return_value=observed_at),
                patch("requests.get", side_effect=responses),
            ):
                with self.assertRaises(RuntimeError):
                    resolver()

    def test_invalid_block_timestamp_after_latest_tip_reaches_target_fails_closed(self):
        resolver = getattr(prices, "_confirmed_2028_halving_time", None)
        self.assertIsNotNone(resolver)
        responses = [
            FakeResponse(text="1050000"),
            FakeResponse(text="0000000000000000000halving"),
            FakeResponse(payload={"height": 1_050_000, "timestamp": 0}),
        ]
        with TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "btc_halving.json"
            cache_file.write_text(json.dumps({
                "target_height": 1_050_000,
                "tip_height": 1_049_000,
                "halving_time": None,
                "checked_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
            }), encoding="utf-8")
            with (
                patch.object(prices, "BTC_HALVING_CACHE_FILE", cache_file, create=True),
                patch("requests.get", side_effect=responses),
            ):
                with self.assertRaises(RuntimeError):
                    resolver()

    def test_stale_halving_cache_is_used_when_blockstream_payload_is_invalid(self):
        resolver = getattr(prices, "_confirmed_2028_halving_time", None)
        self.assertIsNotNone(resolver)
        with TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "btc_halving.json"
            cache_file.write_text(json.dumps({
                "target_height": 1_050_000,
                "tip_height": 1_049_000,
                "halving_time": None,
                "checked_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
            }), encoding="utf-8")
            with (
                patch.object(prices, "BTC_HALVING_CACHE_FILE", cache_file, create=True),
                patch("requests.get", return_value=FakeResponse(text="invalid-height")),
            ):
                self.assertIsNone(resolver())

    def test_halving_day_indicator_uses_new_reward_after_block_is_confirmed(self):
        dates = pd.date_range("2023-01-01", "2028-04-19", freq="D")
        frame = pd.DataFrame({
            "Date": dates,
            "Price": np.linspace(20_000.0, 100_000.0, len(dates)),
        })
        halving_time = pd.Timestamp("2028-04-19 12:34:56")

        with (
            patch.object(prices, "btc_history", return_value=frame),
            patch.object(prices, "_confirmed_2028_halving_time", return_value=halving_time),
        ):
            indicators = prices.btc_indicators()

        self.assertEqual(indicators.iloc[-1]["Date"], pd.Timestamp("2028-04-19"))
        self.assertEqual(indicators.iloc[-1]["Reward"], 1.5625)

    def test_january_2028_decision_reaches_scheduled_reminder_before_halving(self):
        dates = pd.date_range("2023-01-01", "2028-01-03", freq="D")
        frame = pd.DataFrame({
            "Date": dates,
            "Price": np.linspace(20_000.0, 100_000.0, len(dates)),
        })
        monday = datetime(2028, 1, 3, 16, 0)

        with (
            patch.object(prices, "btc_history", return_value=frame),
            patch("dca_tracker.prices._confirmed_2028_halving_time", create=True, return_value=None),
            patch.object(notify, "_today", return_value=monday),
            patch.object(notify, "is_us_index_signal_due", return_value=False),
            patch.object(notify, "claim_reminder", return_value=True),
            patch.object(notify, "send_wework_text", return_value=True),
        ):
            result = notify.send_due_reminders()

        self.assertEqual(result["sent"], ["BTC"])
        self.assertNotIn("BTC", result["skipped"])

    def test_confirmed_2028_halving_reaches_complete_reminder_flow(self):
        dates = pd.date_range("2023-01-01", "2028-04-24", freq="D")
        frame = pd.DataFrame({
            "Date": dates,
            "Price": np.linspace(20_000.0, 100_000.0, len(dates)),
        })
        monday = datetime(2028, 4, 24, 16, 0)
        block_timestamp = int(pd.Timestamp("2028-04-19 12:34:56", tz="UTC").timestamp())
        responses = [
            FakeResponse(text="1050010"),
            FakeResponse(text="0000000000000000000halving"),
            FakeResponse(payload={"height": 1_050_000, "timestamp": block_timestamp}),
            FakeResponse(text="1050010"),
            FakeResponse(text="0000000000000000000halving"),
        ]

        with TemporaryDirectory() as tmpdir:
            with (
                patch.object(prices, "BTC_HALVING_CACHE_FILE", Path(tmpdir) / "btc_halving.json", create=True),
                patch.object(prices, "btc_history", return_value=frame),
                patch.object(
                    prices,
                    "_utc_now",
                    return_value=datetime(2028, 4, 19, 13, 0, tzinfo=timezone.utc),
                    create=True,
                ),
                patch("requests.get", side_effect=responses),
                patch.object(notify, "_today", return_value=monday),
                patch.object(notify, "is_us_index_signal_due", return_value=False),
                patch.object(notify, "claim_reminder", return_value=True),
                patch.object(notify, "send_wework_text", return_value=True),
            ):
                result = notify.send_due_reminders()

        self.assertEqual(result["sent"], ["BTC"])


if __name__ == "__main__":
    unittest.main()
