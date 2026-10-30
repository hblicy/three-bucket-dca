import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from dca_tracker import prices


NOW = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
PAYLOAD = {'cusip': '46090E103', 'currencyCode': 'USD', 'effectiveDate': '2026-09-30',
           'priceToEarningsRatio': 29.480945, 'forwardPriceToEarningsRatio': 25.752738}


class OfficialPeTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(hasattr(prices, '_fetch_invesco_pe'), 'Official PE source has not been connected')
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'nasdaq_pe.json'
        cache = patch.object(prices, 'NASDAQ_PE_CACHE_FILE', self.path)
        cache.start()
        self.addCleanup(cache.stop)
        clock = patch.object(prices, '_utc_now', return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def request(self, payload):
        response = Mock()
        response.json.return_value = payload
        return patch.object(prices.requests, 'get', return_value=response)

    def test_official_nonforward_pe_preserves_identity_date_and_source(self):
        with self.request(dict(PAYLOAD)) as get:
            result = prices.nasdaq_pe()
        self.assertEqual(result['pe'], 29.480945)
        self.assertEqual(result['forward_pe'], 25.752738)
        self.assertEqual(result['symbol'], 'QQQ')
        self.assertEqual(result['field'], 'priceToEarningsRatio')
        self.assertEqual(result['source'], 'invesco')
        self.assertEqual(result['as_of'], '2026-09-30')
        self.assertTrue(get.call_args.args[0].startswith('https://dng-api.invesco.com/'))
        self.assertEqual(json.loads(self.path.read_text())['as_of'], '2026-09-30')

    def test_forward_pe_does_not_replace_missing_invalid_or_non_numeric_pe(self):
        for value in (None, 0, -1, float('nan'), float('inf'), True, '29.48'):
            with self.subTest(value=value), self.request({**PAYLOAD, 'priceToEarningsRatio': value}):
                with self.assertRaises(RuntimeError):
                    prices.nasdaq_pe()

    def test_official_identity_currency_future_and_stale_dates_are_rejected(self):
        for field, value in (('cusip','other'), ('currencyCode','EUR'), ('effectiveDate','2026-11-01'),
                             ('effectiveDate','2026-08-31'), ('effectiveDate','invalid')):
            with self.subTest(field=field, value=value), self.request({**PAYLOAD, field: value}):
                with self.assertRaises(RuntimeError):
                    prices.nasdaq_pe()

    def test_valid_official_cache_is_used_when_source_temporarily_fails(self):
        with self.request(dict(PAYLOAD)):
            prices.nasdaq_pe()
        with patch.object(prices.requests, 'get', side_effect=requests.Timeout('offline')):
            result = prices.nasdaq_pe()
        self.assertEqual(result['source'], 'last_valid_official_pe')
        self.assertEqual(result['as_of'], '2026-09-30')
        self.assertEqual(result['pe'], PAYLOAD['priceToEarningsRatio'])

    def test_retrieval_date_does_not_make_an_old_disclosure_fresh(self):
        with self.request(dict(PAYLOAD)):
            prices.nasdaq_pe()
        with patch.object(prices, '_utc_now', return_value=NOW + timedelta(days=6)), patch.object(prices.requests, 'get', side_effect=requests.Timeout('offline')):
            with self.assertRaises(RuntimeError):
                prices.nasdaq_pe()

    def test_unexpected_programming_error_is_not_swallowed(self):
        with patch.object(prices, '_fetch_invesco_pe', side_effect=TypeError('bug')):
            with self.assertRaisesRegex(TypeError, 'bug'):
                prices.nasdaq_pe()
