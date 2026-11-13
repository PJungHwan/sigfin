"""워밍업 수집 → 계산 → 표시 구간 선택의 통합 검증. 입력은 테스트 전용이다."""

import unittest
from unittest.mock import Mock, patch

import pandas as pd
from pandas.testing import assert_frame_equal

from collectors.binance import fetch_binance_ohlcv, DataCollectionError
from config.settings import INDICATOR_WARMUP_CANDLES, OHLCV_COLUMNS, TIMEFRAME_MILLISECONDS
from indicators.technical import INDICATOR_COLUMNS, add_technical_indicators
from services.market_data import load_market_data

NOW = pd.Timestamp("2026-10-14T12:03:00Z")


def fake_ohlcv_page(symbol, *, timeframe, since, limit, params):
    step = TIMEFRAME_MILLISECONDS[timeframe]
    rows = []
    for timestamp in range(since, since + limit * step, step):
        close = 100.0 + (timestamp // step) % 100
        rows.append([timestamp, close - 1, close + 2, close - 2, close, 10.0])
    return rows


class MarketDataTests(unittest.TestCase):
    def setUp(self):
        load_market_data.clear()
        self.exchange = Mock()
        self.exchange.fetch_ohlcv.side_effect = fake_ohlcv_page
        self.exchange_patch = patch("collectors.binance._get_exchange", return_value=self.exchange)
        self.exchange_patch.start()

    def tearDown(self):
        self.exchange_patch.stop()
        load_market_data.clear()

    def fetch_at_fixed_time(self, *args, **kwargs):
        return fetch_binance_ohlcv(*args, now=NOW, **kwargs)

    def test_warmup_is_hidden_and_first_visible_indicators_are_valid(self):
        for timeframe in ("1h", "1d"):
            with self.subTest(timeframe=timeframe):
                with patch("services.market_data.fetch_binance_ohlcv", side_effect=self.fetch_at_fixed_time):
                    visible = load_market_data("BTC/USDT", timeframe, "1주")
                without_warmup = fetch_binance_ohlcv("BTC/USDT", timeframe, "1주", now=NOW)
                assert_frame_equal(visible[OHLCV_COLUMNS].reset_index(drop=True), without_warmup)
                self.assertGreaterEqual(visible["timestamp"].min(), visible.attrs["requested_start"])
                self.assertFalse(visible[INDICATOR_COLUMNS].iloc[0].isna().any())
                self.assertEqual(visible.attrs["warmup_collected"], INDICATOR_WARMUP_CANDLES)
                self.assertEqual(visible.attrs["actual_start"], visible.iloc[0]["timestamp"])

    def test_calculation_precedes_trimming_without_changing_values(self):
        with patch("services.market_data.fetch_binance_ohlcv", side_effect=self.fetch_at_fixed_time):
            visible = load_market_data("BTC/USDT", "1h", "1주")
        raw = fetch_binance_ohlcv("BTC/USDT", "1h", "1주", now=NOW, warmup_candles=300)
        expected = add_technical_indicators(raw)
        expected = expected.loc[expected["timestamp"] >= raw.attrs["analysis_start"]]
        assert_frame_equal(visible, expected)

    def test_cache_and_targeted_refresh_cover_collection_and_indicators(self):
        with patch("services.market_data.fetch_binance_ohlcv", side_effect=self.fetch_at_fixed_time) as fetch:
            first = load_market_data("BTC/USDT", "1h", "1주")
            second = load_market_data("BTC/USDT", "1h", "1주")
            assert_frame_equal(first, second)
            self.assertEqual(fetch.call_count, 1)
            load_market_data.clear("BTC/USDT", "1h", "1주")
            third = load_market_data("BTC/USDT", "1h", "1주")
            assert_frame_equal(first, third)
            self.assertEqual(fetch.call_count, 2)

    def test_failed_fetch_is_not_cached_and_next_call_can_recover(self):
        raw = self.fetch_at_fixed_time("BTC/USDT", "1h", "1주", warmup_candles=300)
        with patch("services.market_data.fetch_binance_ohlcv", side_effect=[
            DataCollectionError("network failure"), raw,
        ]) as fetch:
            with self.assertRaises(DataCollectionError):
                load_market_data("BTC/USDT", "1h", "1주")
            recovered = load_market_data("BTC/USDT", "1h", "1주")
            self.assertFalse(recovered.empty)
            self.assertEqual(fetch.call_count, 2)
            assert_frame_equal(recovered, load_market_data("BTC/USDT", "1h", "1주"))
            self.assertEqual(fetch.call_count, 2)
