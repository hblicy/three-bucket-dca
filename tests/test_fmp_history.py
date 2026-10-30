from __future__ import annotations

import unittest
import traceback
import ast
import inspect
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import pandas as pd
import requests

from dca_tracker import prices


NOW = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)


def response(payload):
    result = Mock()
    result.status_code = 200
    result.json.return_value = payload
    return result


def kline(day, close=100):
    stamp = int(day.timestamp() * 1000)
    return [stamp, str(close), str(close + 1), str(close - 1), str(close), '10',
            stamp + 86_399_999, '1000', 2, '5', '500', '0']


class FmpHistoryTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(hasattr(prices, '_fetch_binance_history'), 'Historical providers have not been switched')
        self.key = patch.object(prices, 'FMP_API_KEY', 'test-key')
        self.key.start()
        self.addCleanup(self.key.stop)
        clock = patch.object(prices, '_utc_now', return_value=NOW)
        self.clock = clock.start()
        self.addCleanup(clock.stop)
        self.cache = patch.dict(prices._HISTORY_CACHE, clear=True)
        self.cache.start()
        self.addCleanup(self.cache.stop)

    def daily(self, symbol='QQQM'):
        return [
            {'symbol': symbol, 'date': '2026-10-29', 'open': 101, 'high': 103, 'low': 100, 'close': 102, 'volume': 20},
            {'symbol': symbol, 'date': '2026-10-28', 'open': 100, 'high': 102, 'low': 99, 'close': 101, 'volume': 10},
        ]

    def test_equities_use_fmp_close_and_sorted_dates_with_one_shared_download(self):
        with patch.object(prices.requests, 'get', return_value=response(self.daily())) as get:
            first = prices.history('QQQM', period='1y')
            second = prices.history('QQQM', period='5y')
        self.assertEqual(first['Close'].tolist(), [101, 102])
        self.assertEqual(first.index.name, 'Date')
        self.assertEqual(get.call_count, 1)
        self.assertEqual(get.call_args.args[0], 'https://financialmodelingprep.com/stable/historical-price-eod/full')
        self.assertEqual(get.call_args.kwargs['params']['symbol'], 'QQQM')
        first.iloc[-1, first.columns.get_loc('Close')] = 999
        self.assertEqual(second['Close'].iloc[-1], 102)

    def test_each_original_equity_symbol_is_preserved(self):
        for symbol in ('CRCL', 'QQQM', 'VOO'):
            with self.subTest(symbol=symbol), patch.object(prices.requests, 'get', return_value=response(self.daily(symbol))) as get:
                prices.history(symbol)
                self.assertEqual(get.call_args.kwargs['params']['symbol'], symbol)

    def test_missing_key_fails_before_network_and_does_not_use_yahoo(self):
        with patch.object(prices, 'FMP_API_KEY', ''), patch.object(prices.requests, 'get') as get:
            with self.assertRaisesRegex(RuntimeError, 'FMP_API_KEY'):
                prices.history('QQQM')
        get.assert_not_called()

    def test_bad_latest_close_is_rejected_and_not_dropped(self):
        for value in (None, 0, -1, float('nan'), float('inf'), True):
            rows = self.daily()
            rows[0]['close'] = value
            with self.subTest(value=value), patch.object(prices.requests, 'get', return_value=response(rows)):
                with self.assertRaises(RuntimeError):
                    prices.history('QQQM')

    def test_http_error_keeps_failure_context_without_key_in_message(self):
        bad = response([])
        bad.raise_for_status.side_effect = requests.HTTPError('https://provider.example?apikey=test-key')
        with patch.object(prices.requests, 'get', return_value=bad):
            with self.assertRaises(RuntimeError) as caught:
                prices.history('QQQM')
        self.assertNotIn('test-key', str(caught.exception))
        self.assertNotIn('test-key', ''.join(traceback.format_exception(caught.exception)))
        self.assertIsNotNone(caught.exception.__cause__)

    def test_future_wrong_symbol_or_duplicate_bars_are_rejected(self):
        for change in ('future', 'symbol', 'duplicate'):
            rows = self.daily()
            if change == 'future':
                rows[0]['date'] = '2026-11-10'
            elif change == 'symbol':
                rows[0]['symbol'] = 'QQQ'
            else:
                rows.append(dict(rows[0]))
            with self.subTest(change=change), patch.object(prices.requests, 'get', return_value=response(rows)):
                with self.assertRaises(RuntimeError):
                    prices.history('QQQM')

    def test_binance_pages_full_btcusdt_history_without_fmp_or_yahoo(self):
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        first = [kline(start + timedelta(days=i), 100 + i) for i in range(1000)]
        second = [kline(start + timedelta(days=i), 100 + i) for i in range(1000, 1002)]
        self.clock.return_value = start + timedelta(days=1002, hours=12)
        with patch.object(prices.requests, 'get', side_effect=[response(first), response(second)]) as get:
            frame = prices.btc_history()
        self.assertEqual(len(frame), 1002)
        self.assertEqual(frame['Price'].iloc[-1], 1101)
        self.assertEqual(get.call_count, 2)
        self.assertTrue(all(call.args[0] == 'https://data-api.binance.vision/api/v3/klines' for call in get.call_args_list))
        self.assertTrue(all(call.kwargs['params']['symbol'] == 'BTCUSDT' for call in get.call_args_list))
        self.assertEqual(get.call_args_list[1].kwargs['params']['startTime'], first[-1][0] + 86_400_000)

    def test_btc_history_and_performance_share_same_cached_currency_series(self):
        rows = [kline(NOW - timedelta(days=2), 100), kline(NOW - timedelta(days=1), 110)]
        # Klines use UTC midnight, not the time of the request.
        rows = [kline(datetime.fromtimestamp(row[0] / 1000, timezone.utc).replace(hour=0), float(row[4])) for row in rows]
        with patch.object(prices.requests, 'get', return_value=response(rows)) as get:
            indicators = prices.btc_history()
            performance = prices.history('BTC-USD', start='2026-10-01')
        self.assertEqual(indicators['Price'].tolist(), performance['Close'].tolist())
        self.assertEqual(get.call_count, 1)

    def test_failed_or_nonprogressing_binance_page_does_not_return_partial_history(self):
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        page = [kline(start + timedelta(days=i)) for i in range(1000)]
        for second in (requests.Timeout('offline'), response(page)):
            with self.subTest(second=second), patch.object(prices.requests, 'get', side_effect=[response(page), second]):
                with self.assertRaises(RuntimeError):
                    prices.btc_history()

    def test_stale_btc_history_is_rejected(self):
        rows = [kline(datetime(2026, 1, day, tzinfo=timezone.utc)) for day in (1, 2)]
        with patch.object(prices.requests, 'get', return_value=response(rows)):
            with self.assertRaisesRegex(RuntimeError, 'Stale'):
                prices.btc_history()

    def test_missing_btc_day_is_not_counted_as_a_continuous_200_week_history(self):
        rows = [kline(NOW.replace(hour=0) - timedelta(days=3)), kline(NOW.replace(hour=0) - timedelta(days=1))]
        with patch.object(prices.requests, 'get', return_value=response(rows)):
            with self.assertRaises(RuntimeError):
                prices.btc_history()

    def test_pricing_module_has_no_yahoo_client_or_network_endpoint(self):
        source = inspect.getsource(prices)
        modules = [node.module for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ImportFrom)]
        modules += [alias.name for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Import) for alias in node.names]
        self.assertNotIn('yfinance', modules)
        self.assertNotIn('finance.yahoo.com', source)
