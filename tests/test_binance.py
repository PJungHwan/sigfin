"""네트워크 없이 페이지네이션과 데이터 품질을 검증하는 명시적 테스트 입력."""

import unittest
from unittest.mock import Mock, patch

import ccxt
import pandas as pd

from collectors.binance import DataCollectionError, OHLCV_COLUMNS, fetch_binance_ohlcv
from config.settings import MAX_CANDLES, MAX_FETCH_REQUESTS, MAX_WARMUP_CANDLES, TIMEFRAME_MILLISECONDS

NOW = pd.Timestamp("2026-10-14T12:03:00Z")


def candle(timestamp, close=101.0):
    return [timestamp, 100.0, 103.0, 98.0, close, 12.5]


def full_page(symbol, *, timeframe, since, limit, params):
    interval = TIMEFRAME_MILLISECONDS[timeframe]
    return [candle(timestamp) for timestamp in range(since, since + limit * interval, interval)]


class BinanceCollectorTests(unittest.TestCase):
    def fetch(self, callback=full_page, **kwargs):
        exchange = Mock()
        exchange.fetch_ohlcv.side_effect = callback
        with patch("collectors.binance._get_exchange", return_value=exchange):
            data = fetch_binance_ohlcv(
                kwargs.pop("symbol", "BTC/USDT"),
                kwargs.pop("timeframe", "1h"),
                kwargs.pop("period", "1주"),
                now=kwargs.pop("now", NOW),
                **kwargs,
            )
        return data, exchange

    def test_pagination_and_dataframe_contract(self):
        data, exchange = self.fetch(timeframe="5m")
        self.assertEqual(list(data.columns), OHLCV_COLUMNS)
        self.assertEqual(len(data), 2015)
        self.assertEqual(exchange.fetch_ohlcv.call_count, 3)
        self.assertFalse(data.attrs["is_partial"])
        self.assertFalse(data.isna().any().any())
        self.assertTrue(data["timestamp"].is_monotonic_increasing)
        self.assertTrue(data["timestamp"].is_unique)
        self.assertEqual(str(data["timestamp"].dt.tz), "UTC")
        calls = exchange.fetch_ohlcv.call_args_list
        self.assertEqual([call.kwargs["limit"] for call in calls], [1000, 1000, 15])
        self.assertEqual(calls[1].kwargs["since"] - calls[0].kwargs["since"], 1000 * 300000)
        self.assertLess(data["timestamp"].max(), data.attrs["closed_end"])

    def test_latest_window_is_capped(self):
        data, exchange = self.fetch(timeframe="5m", period="1년")
        self.assertEqual(len(data), MAX_CANDLES)
        self.assertEqual(exchange.fetch_ohlcv.call_count, 5)
        self.assertTrue(data.attrs["is_partial"])
        self.assertGreater(data.attrs["collection_start"], data.attrs["requested_start"])
        self.assertEqual(data.attrs["actual_end"], data.attrs["closed_end"])
        self.assertIn("제한", " ".join(data.attrs["limit_reasons"]))

    def test_all_supported_timeframes(self):
        for timeframe, interval_ms in TIMEFRAME_MILLISECONDS.items():
            with self.subTest(timeframe=timeframe):
                data, _ = self.fetch(timeframe=timeframe)
                differences = data["timestamp"].diff().dropna()
                self.assertTrue((differences == pd.Timedelta(milliseconds=interval_ms)).all())
                self.assertFalse(data.attrs["is_partial"])

    def test_calendar_periods(self):
        now = pd.Timestamp("2024-03-31T12:00:00Z")
        starts = {
            "1주": "2024-03-24T12:00:00Z",
            "1개월": "2024-02-29T12:00:00Z",
            "3개월": "2023-12-31T12:00:00Z",
            "6개월": "2023-09-30T12:00:00Z",
            "1년": "2023-03-31T12:00:00Z",
        }
        for period, expected in starts.items():
            with self.subTest(period=period):
                data, _ = self.fetch(timeframe="1d", period=period, now=now)
                self.assertEqual(data.attrs["requested_start"], pd.Timestamp(expected))

    def test_non_utc_input_is_normalized(self):
        data, _ = self.fetch(now=NOW.tz_convert("Asia/Seoul"))
        self.assertEqual(data.attrs["requested_end"], NOW)
        self.assertEqual(str(data["timestamp"].dt.tz), "UTC")

    def test_in_progress_candle_is_excluded_and_duplicates_are_sorted(self):
        def response(symbol, *, timeframe, since, limit, params):
            rows = full_page(symbol, timeframe=timeframe, since=since, limit=limit, params=params)
            rows += [candle(since, close=102), candle(params["endTime"] + 1)]
            return list(reversed(rows))

        data, _ = self.fetch(response)
        self.assertEqual(len(data), 167)
        self.assertTrue(data["timestamp"].is_unique)
        self.assertTrue(data["timestamp"].is_monotonic_increasing)
        self.assertEqual(data.attrs["removed_duplicates"], 1)
        self.assertLess(data["timestamp"].max(), data.attrs["closed_end"])

    def test_invalid_and_missing_rows_are_reported_without_filling(self):
        def response(symbol, *, timeframe, since, limit, params):
            rows = full_page(symbol, timeframe=timeframe, since=since, limit=limit, params=params)
            rows[1][4] = None
            rows[2][5] = float("inf")
            rows[3][2] = 90  # high < open
            rows[4][0] += 1  # misaligned timestamp
            rows[5] = [rows[5][0]]
            return rows

        data, _ = self.fetch(response)
        self.assertEqual(len(data), 162)
        self.assertFalse(data.isna().any().any())
        self.assertEqual(data.attrs["invalid_rows"], 5)
        self.assertEqual(data.attrs["missing_candles"], 5)
        self.assertEqual(data.attrs["gap_count"], 5)
        self.assertTrue(data.attrs["is_partial"])

    def test_short_pages_continue(self):
        def response(symbol, *, timeframe, since, limit, params):
            return full_page(symbol, timeframe=timeframe, since=since, limit=min(40, limit), params=params)

        data, exchange = self.fetch(response)
        self.assertEqual(len(data), 167)
        self.assertEqual(exchange.fetch_ohlcv.call_count, 5)
        self.assertFalse(data.attrs["is_partial"])

    def test_request_budget_stops_tiny_pages(self):
        def response(symbol, *, timeframe, since, limit, params):
            return [candle(since)]

        data, exchange = self.fetch(response)
        self.assertEqual(exchange.fetch_ohlcv.call_count, MAX_FETCH_REQUESTS)
        self.assertEqual(len(data), MAX_FETCH_REQUESTS)
        self.assertIn("페이지 요청 제한", " ".join(data.attrs["limit_reasons"]))

    def test_non_advancing_pages_stop(self):
        first_page = None

        def response(symbol, *, timeframe, since, limit, params):
            nonlocal first_page
            if first_page is None:
                first_page = full_page(symbol, timeframe=timeframe, since=since, limit=limit, params=params)
            return first_page

        data, exchange = self.fetch(response, timeframe="5m")
        self.assertEqual(exchange.fetch_ohlcv.call_count, 2)
        self.assertEqual(len(data), 1000)
        self.assertIn("반복 호출", " ".join(data.attrs["limit_reasons"]))

    def test_partial_network_failure_preserves_real_response(self):
        calls = 0

        def response(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ccxt.NetworkError("test connection failure")
            return full_page(*args, **kwargs)

        data, _ = self.fetch(response, timeframe="5m")
        self.assertEqual(len(data), 1000)
        self.assertTrue(data.attrs["is_partial"])
        self.assertIn("연결하지 못했습니다", " ".join(data.attrs["limit_reasons"]))

    def test_partial_empty_response(self):
        calls = 0

        def response(*args, **kwargs):
            nonlocal calls
            calls += 1
            return full_page(*args, **kwargs) if calls == 1 else []

        data, _ = self.fetch(response, timeframe="5m")
        self.assertEqual(len(data), 1000)
        self.assertIn("빈 응답", " ".join(data.attrs["limit_reasons"]))

    def test_initial_errors_do_not_return_fabricated_data(self):
        for error, message in [
            (ccxt.NetworkError("test"), "연결하지 못했습니다"),
            (ccxt.RateLimitExceeded("test"), "요청 한도"),
            (ccxt.ExchangeError("test"), "처리하지 못했습니다"),
        ]:
            with self.subTest(error=type(error).__name__):
                with self.assertRaisesRegex(DataCollectionError, message):
                    self.fetch(error)

    def test_empty_or_malformed_response_raises(self):
        for response in ([], {"unexpected": "response"}, [[None] * 6]):
            with self.subTest(response=response):
                with self.assertRaises(DataCollectionError):
                    self.fetch(lambda *args, **kwargs: response)

    def test_invalid_inputs_never_call_exchange(self):
        for kwargs in (
            {"symbol": "SOL/USDT"},
            {"timeframe": "1m"},
            {"period": "2년"},
            {"max_candles": 0},
            {"max_candles": MAX_CANDLES + 1},
            {"now": pd.Timestamp("2026-10-14")},
        ):
            with self.subTest(kwargs=kwargs):
                with patch("collectors.binance._get_exchange") as get_exchange:
                    with self.assertRaises(ValueError):
                        fetch_binance_ohlcv(
                            kwargs.get("symbol", "BTC/USDT"),
                            kwargs.get("timeframe", "1h"),
                            kwargs.get("period", "1주"),
                            now=kwargs.get("now", NOW),
                            max_candles=kwargs.get("max_candles", MAX_CANDLES),
                        )
                    get_exchange.assert_not_called()

    def test_fractional_start_does_not_include_earlier_candle(self):
        data, _ = self.fetch(now=pd.Timestamp("2026-10-14T12:00:00.000001Z"))
        self.assertGreaterEqual(data["timestamp"].min(), data.attrs["requested_start"])

    def test_warmup_precedes_requested_analysis_window(self):
        data, exchange = self.fetch(warmup_candles=300)
        self.assertEqual(len(data), 467)
        self.assertEqual(data.attrs["warmup_collected"], 300)
        self.assertEqual(data.attrs["warmup_missing_candles"], 0)
        visible = data.loc[data["timestamp"] >= data.attrs["analysis_start"]]
        self.assertEqual(len(visible), 167)
        self.assertGreaterEqual(visible["timestamp"].min(), data.attrs["requested_start"])
        self.assertFalse(data.attrs["is_partial"])
        self.assertEqual(exchange.fetch_ohlcv.call_count, 1)

    def test_warmup_is_bounded_in_addition_to_display_limit(self):
        data, exchange = self.fetch(timeframe="5m", period="1년", warmup_candles=300)
        self.assertEqual(len(data), MAX_CANDLES + MAX_WARMUP_CANDLES)
        self.assertEqual(len(data.loc[data["timestamp"] >= data.attrs["analysis_start"]]), MAX_CANDLES)
        self.assertEqual(exchange.fetch_ohlcv.call_count, 6)
        self.assertLessEqual(exchange.fetch_ohlcv.call_count, MAX_FETCH_REQUESTS)

    def test_unavailable_warmup_does_not_hide_complete_visible_data(self):
        def response(symbol, *, timeframe, since, limit, params):
            rows = full_page(symbol, timeframe=timeframe, since=since, limit=limit, params=params)
            return rows[300:]

        data, _ = self.fetch(response, warmup_candles=300)
        self.assertEqual(len(data), 167)
        self.assertEqual(data.attrs["missing_candles"], 0)
        self.assertEqual(data.attrs["warmup_collected"], 0)
        self.assertTrue(data.attrs["warmup_reasons"])
        self.assertFalse(data.attrs["is_partial"])

    def test_warmup_only_after_api_failure_is_an_error(self):
        calls = 0

        def response(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return full_page(*args, **kwargs)[:200]
            raise ccxt.NetworkError("test failure after warmup")

        with self.assertRaisesRegex(DataCollectionError, "계산용 과거 데이터만"):
            self.fetch(response, warmup_candles=300)

    def test_invalid_warmup_limit_never_calls_exchange(self):
        for warmup in (-1, MAX_WARMUP_CANDLES + 1, True):
            with patch("collectors.binance._get_exchange") as get_exchange:
                with self.assertRaises(ValueError):
                    fetch_binance_ohlcv("BTC/USDT", "1h", "1주", warmup_candles=warmup)
                get_exchange.assert_not_called()


if __name__ == "__main__":
    unittest.main()
