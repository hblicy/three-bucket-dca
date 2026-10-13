from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

from dca_tracker import performance


class ObservedDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 10, 13)


class PerformancePricesTest(unittest.TestCase):
    def test_weekend_start_uses_previous_close_for_valuation(self):
        history = pd.DataFrame(
            {"Close": [83.50, 83.87]},
            index=pd.to_datetime(["2026-10-09", "2026-10-12"]),
        )
        transaction = {
            "date": "2026-10-10",
            "bucket": "CRCL_GROWTH",
            "asset": "CRCL",
            "side": "buy",
            "amount_usd": 835.0,
            "price": 83.50,
            "shares": 10.0,
        }
        with (
            patch.object(performance, "date", ObservedDate),
            patch.object(performance.prices, "history", return_value=history),
            patch.object(performance, "_transactions", return_value=[transaction]),
        ):
            result = performance.performance("CRCL_GROWTH")

        self.assertTrue(result["data_ok"])
        self.assertEqual(result["errors"], [])
        self.assertEqual([point["value"] for point in result["series"]], [835.0, 835.0, 838.7, 838.7])
        self.assertTrue(all(not point["missing_prices"] for point in result["series"]))
        self.assertEqual(result["summary"]["value"], 838.7)

    def test_holiday_start_preserves_previous_quote_without_extra_output_dates(self):
        class HolidayDate(date):
            @classmethod
            def today(cls):
                return cls(2026, 12, 28)

        history = pd.DataFrame(
            {"Close": [100.0, 110.0]},
            index=pd.to_datetime(["2026-12-24", "2026-12-28"]),
        )
        with (
            patch.object(performance, "date", HolidayDate),
            patch.object(performance.prices, "history", return_value=history),
        ):
            maps, errors = performance._price_maps(["VOO"], date(2026, 12, 25))

        self.assertEqual(errors, [])
        self.assertEqual(maps["VOO"], {
            "2026-12-25": 100.0,
            "2026-12-26": 100.0,
            "2026-12-27": 100.0,
            "2026-12-28": 110.0,
        })

    def test_missing_previous_quote_is_not_filled_with_a_future_price(self):
        history = pd.DataFrame(
            {"Close": [83.87]},
            index=pd.to_datetime(["2026-10-12"]),
        )
        with (
            patch.object(performance, "date", ObservedDate),
            patch.object(performance.prices, "history", return_value=history),
        ):
            maps, errors = performance._price_maps(["CRCL"], date(2026, 10, 10))

        self.assertEqual(errors, [])
        self.assertEqual(maps["CRCL"], {"2026-10-12": 83.87, "2026-10-13": 83.87})

    def test_trading_day_start_uses_that_days_quote(self):
        history = pd.DataFrame(
            {"Close": [83.50, 83.87]},
            index=pd.to_datetime(["2026-10-09", "2026-10-12"]),
        )
        with (
            patch.object(performance, "date", ObservedDate),
            patch.object(performance.prices, "history", return_value=history),
        ):
            maps, errors = performance._price_maps(["CRCL"], date(2026, 10, 12))

        self.assertEqual(errors, [])
        self.assertEqual(maps["CRCL"], {"2026-10-12": 83.87, "2026-10-13": 83.87})


if __name__ == "__main__":
    unittest.main()
