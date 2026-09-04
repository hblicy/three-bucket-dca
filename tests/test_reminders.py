from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from dca_tracker import notify
from dca_tracker.notify import is_due
from dca_tracker.rules import Decision


def decision(bucket: str, status: str, amount: float, data_ok: bool = True) -> Decision:
    return Decision(
        bucket=bucket,
        bucket_name=bucket,
        asset="BTC" if bucket == "BTC_CYCLE" else "CRCL" if bucket == "CRCL_GROWTH" else "QQQM",
        title="test",
        status=status,
        action="test",
        recommended_amount=amount,
        price=100.0,
        reason="test",
        next_window="test",
        schedule="test",
        metrics={},
        data_ok=data_ok,
    )


class ReminderTest(unittest.TestCase):
    tz = ZoneInfo("Asia/Shanghai")

    def test_btc_zero_amount_pause_is_due_on_monday(self):
        now = datetime(2026, 9, 7, 16, 0, tzinfo=self.tz)
        self.assertTrue(is_due(decision("BTC_CYCLE", "pause", 0.0), now))

    def test_crcl_zero_amount_pause_is_due_on_tuesday(self):
        now = datetime(2026, 9, 8, 16, 0, tzinfo=self.tz)
        self.assertTrue(is_due(decision("CRCL_GROWTH", "pause", 0.0), now))

    def test_error_decision_is_never_due(self):
        now = datetime(2026, 9, 7, 16, 0, tzinfo=self.tz)
        self.assertFalse(is_due(decision("BTC_CYCLE", "error", 0.0, data_ok=False), now))

    def test_us_index_off_cycle_zero_amount_is_not_due(self):
        now = datetime(2026, 9, 1, 16, 0, tzinfo=self.tz)
        self.assertFalse(is_due(decision("US_INDEX_CORE", "off_cycle", 0.0), now))

    def test_btc_zero_amount_pause_runs_complete_send_flow(self):
        now = datetime(2026, 9, 7, 16, 0, tzinfo=self.tz)
        pause = decision("BTC_CYCLE", "pause", 0.0)
        pause.action = "暂停定投"
        with (
            patch.object(notify, "_today", return_value=now),
            patch.object(notify, "btc_decision", return_value=pause),
            patch.object(notify, "claim_reminder", return_value=True),
            patch.object(notify, "send_wework_text", return_value=True) as sender,
        ):
            result = notify.send_due_reminders()
        self.assertEqual(result["sent"], ["BTC"])
        self.assertIn("暂停定投", sender.call_args.args[0])
        self.assertIn("建议金额：$0.00", sender.call_args.args[0])

    def test_crcl_zero_amount_pause_runs_complete_send_flow(self):
        now = datetime(2026, 9, 8, 16, 0, tzinfo=self.tz)
        pause = decision("CRCL_GROWTH", "pause", 0.0)
        pause.action = "暂停定投"
        with (
            patch.object(notify, "_today", return_value=now),
            patch.object(notify, "crcl_decision", return_value=pause),
            patch.object(notify, "claim_reminder", return_value=True),
            patch.object(notify, "send_wework_text", return_value=True) as sender,
        ):
            result = notify.send_due_reminders()
        self.assertEqual(result["sent"], ["CRCL"])
        self.assertIn("暂停定投", sender.call_args.args[0])
        self.assertIn("建议金额：$0.00", sender.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
