from __future__ import annotations

import unittest
from unittest.mock import patch


class HalvingUpdateTest(unittest.TestCase):
    def test_lightweight_update_checks_halving_state_once(self):
        try:
            from scripts import update_btc_halving
        except ImportError as exc:
            self.fail(f"缺少 BTC 减半轻量更新脚本: {exc}")

        with patch.object(
            update_btc_halving,
            "_confirmed_2028_halving_time",
            return_value=None,
        ) as resolver:
            update_btc_halving.main()

        resolver.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
