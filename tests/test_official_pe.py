import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from dca_tracker import prices, rules


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

    def test_cache_requires_official_source_symbol_and_field(self):
        with self.request(dict(PAYLOAD)):
            prices.nasdaq_pe()
        original = json.loads(self.path.read_text(encoding='utf-8'))
        for field, value in (('source', None), ('source', 'yahoo'),
                             ('symbol', 'QQQM'), ('symbol', '^NDX'),
                             ('field', 'trailingPE'), ('field', 'forwardPE')):
            with self.subTest(field=field, value=value):
                raw = json.dumps({**original, field: value})
                self.path.write_text(raw, encoding='utf-8')
                with patch.object(prices.requests, 'get', side_effect=requests.Timeout('offline')):
                    with self.assertRaisesRegex(RuntimeError, '没有有效缓存'):
                        prices.nasdaq_pe()
                self.assertEqual(self.path.read_text(encoding='utf-8'), raw)

    def test_cache_requires_valid_disclosure_date_even_if_recently_retrieved(self):
        with self.request(dict(PAYLOAD)):
            prices.nasdaq_pe()
        original = json.loads(self.path.read_text(encoding='utf-8'))
        for as_of in (None, 'invalid', '2026-10-31', '2026-09-24'):
            with self.subTest(as_of=as_of):
                self.path.write_text(json.dumps({**original, 'as_of': as_of}), encoding='utf-8')
                with patch.object(prices.requests, 'get', side_effect=requests.Timeout('offline')):
                    with self.assertRaisesRegex(RuntimeError, '没有有效缓存'):
                        prices.nasdaq_pe()

    def test_official_cache_at_35_day_disclosure_boundary_is_usable(self):
        as_of = (NOW - timedelta(days=35)).date().isoformat()
        with self.request({**PAYLOAD, 'effectiveDate': as_of}):
            prices.nasdaq_pe()
        raw = self.path.read_text(encoding='utf-8')
        with patch.object(prices.requests, 'get', side_effect=requests.Timeout('offline')):
            result = prices.nasdaq_pe()
        self.assertEqual(result['source'], 'last_valid_official_pe')
        self.assertEqual(result['as_of'], as_of)
        self.assertEqual(self.path.read_text(encoding='utf-8'), raw)

    def test_legacy_cache_cannot_generate_formal_qqqm_advice(self):
        self.path.write_text(json.dumps({
            'symbol': 'QQQ', 'pe': 20.0, 'field': 'trailingPE',
            'updated_at': NOW.isoformat(),
        }), encoding='utf-8')
        raw = self.path.read_text(encoding='utf-8')
        trend = {'monthly_close': 110.0, 'ma10m': 100.0,
                 'below_ma10m': False, 'ma10m_distance_pct': 10.0}
        with (
            patch.object(rules, '_now', return_value=NOW),
            patch.object(prices.requests, 'get', side_effect=requests.Timeout('offline')),
            patch.object(prices, 'equity_quote', return_value={'price': 110.0}),
            patch.object(prices, 'latest_price', side_effect=lambda *args, **kwargs: {'price': 110.0}),
            patch.object(prices, 'monthly_trend', return_value=trend),
        ):
            decisions = {decision.asset: decision for decision in rules.us_index_decisions()}
        self.assertFalse(decisions['QQQM'].data_ok)
        self.assertEqual(decisions['QQQM'].status, 'error')
        self.assertEqual(decisions['QQQM'].recommended_amount, 0.0)
        self.assertIn('没有有效缓存', decisions['QQQM'].reason)
        self.assertTrue(decisions['VOO'].data_ok)
        self.assertEqual(decisions['VOO'].status, 'off_cycle')
        self.assertEqual(self.path.read_text(encoding='utf-8'), raw)

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
