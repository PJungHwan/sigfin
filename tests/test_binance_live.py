r"""선택적으로 실행하는 실제 공개 API 검증. 실패 시 테스트 데이터로 대체하지 않는다.

PowerShell:
    $env:RUN_BINANCE_LIVE_TESTS = '1'
    .\.venv\Scripts\python.exe -m unittest discover -s tests -p test_binance_live.py -v -f
    Remove-Item Env:RUN_BINANCE_LIVE_TESTS
"""

import os
from pathlib import Path
import unittest

import pandas as pd
from pandas.api.types import is_numeric_dtype

from collectors.binance import OHLCV_COLUMNS, fetch_binance_ohlcv
from config.settings import MAX_CANDLES, MAX_FETCH_REQUESTS, TIMEFRAME_MILLISECONDS
from ui.charts import create_candlestick_chart
from indicators.technical import INDICATOR_COLUMNS


@unittest.skipUnless(os.getenv("RUN_BINANCE_LIVE_TESTS") == "1", "실제 API 검증은 명시적으로 활성화해야 합니다.")
class BinanceLiveTests(unittest.TestCase):
    def test_streamlit_app_with_real_binance_data(self):
        from streamlit.testing.v1 import AppTest

        from services.market_data import load_market_data

        load_market_data.clear()
        try:
            app_path = Path(__file__).resolve().parents[1] / "app.py"
            app = AppTest.from_file(str(app_path), default_timeout=40).run()
            self.assertFalse(app.exception, [item.message for item in app.exception])
            self.assertFalse(app.error, [item.value for item in app.error])
            self.assertEqual(len(app.get("plotly_chart")), 1)
            self.assertEqual(len(app.metric), 3)
            self.assertEqual(len(app.sidebar.checkbox), 11)
            self.assertTrue(any(item.value == "DATA READY" for item in app.success))
            print(
                f"LIVE APP OK: latest_close={app.metric[0].value}, "
                f"candle_time={app.metric[2].value} UTC, chart=1, sidebar_checkboxes=11",
                flush=True,
            )
        finally:
            load_market_data.clear()

    def test_real_warmup_and_indicator_pipeline(self):
        from services.market_data import load_market_data

        for symbol, timeframe, period in (
            ("BTC/USDT", "1h", "1주"),
            ("ETH/USDT", "1d", "1주"),
            ("BTC/USDT", "5m", "1년"),
        ):
            with self.subTest(symbol=symbol, timeframe=timeframe, period=period):
                load_market_data.clear(symbol, timeframe, period)
                data = load_market_data(symbol, timeframe, period)
                self.assertEqual(data.attrs["warmup_collected"], 300)
                self.assertGreaterEqual(data["timestamp"].min(), data.attrs["analysis_start"])
                self.assertLessEqual(len(data), MAX_CANDLES)
                self.assertFalse(data[INDICATOR_COLUMNS].isna().any().any())
                self.assertEqual(data.attrs["missing_candles"], 0)
                self.assertLessEqual(data.attrs["request_count"], MAX_FETCH_REQUESTS)
                figure = create_candlestick_chart(
                    data, symbol, timeframe, show_rsi=True, show_macd=True, show_ema=True,
                    show_sma=True, show_bollinger=True, show_volume_sma=True,
                )
                self.assertEqual(len(figure.data), 14)
                self.assertTrue(figure.to_json())
                print(
                    f"LIVE INDICATORS OK {symbol} {timeframe} {period}: "
                    f"visible={len(data)}, warmup={data.attrs['warmup_collected']}, "
                    f"pages={data.attrs['request_count']}, "
                    f"RSI={data.iloc[-1]['rsi_14']:.4f}, "
                    f"MACD_HIST={data.iloc[-1]['macd_histogram']:.4f}",
                    flush=True,
                )

    def test_real_public_ohlcv_and_chart(self):
        cases = [
            ("BTC/USDT", "1h", "1주"),
            ("ETH/USDT", "1h", "1주"),
            ("BTC/USDT", "5m", "1주"),
            ("ETH/USDT", "15m", "1주"),
            ("BTC/USDT", "4h", "1개월"),
            ("ETH/USDT", "1d", "1년"),
            ("BTC/USDT", "5m", "1년"),
        ]
        for symbol, timeframe, period in cases:
            with self.subTest(symbol=symbol, timeframe=timeframe, period=period):
                data = fetch_binance_ohlcv(symbol, timeframe, period)
                self.assertEqual(list(data.columns), OHLCV_COLUMNS)
                self.assertGreater(len(data), 0)
                self.assertLessEqual(len(data), MAX_CANDLES)
                self.assertFalse(data.isna().any().any())
                self.assertTrue(data["timestamp"].is_monotonic_increasing)
                self.assertTrue(data["timestamp"].is_unique)
                self.assertEqual(str(data["timestamp"].dt.tz), "UTC")
                for column in OHLCV_COLUMNS[1:]:
                    self.assertTrue(is_numeric_dtype(data[column]))
                interval = pd.Timedelta(milliseconds=TIMEFRAME_MILLISECONDS[timeframe])
                self.assertLessEqual((data["timestamp"] + interval).max(), data.attrs["requested_end"])
                self.assertTrue((data["high"] >= data[["open", "low", "close"]].max(axis=1)).all())
                self.assertTrue((data["low"] <= data[["open", "high", "close"]].min(axis=1)).all())
                self.assertTrue((data["volume"] >= 0).all())
                self.assertEqual(data.attrs["missing_candles"], 0)
                self.assertLessEqual(data.attrs["request_count"], MAX_FETCH_REQUESTS)
                if timeframe == "5m" and period == "1년":
                    self.assertEqual(len(data), MAX_CANDLES)
                    self.assertTrue(data.attrs["is_partial"])
                else:
                    self.assertFalse(data.attrs["is_partial"], data.attrs["limit_reasons"])
                figure = create_candlestick_chart(data, symbol, timeframe)
                self.assertEqual(len(figure.data[0].close), len(data))
                self.assertTrue(figure.to_json())
                print(
                    f"LIVE OK {symbol} {timeframe} {period}: {len(data):,} candles, "
                    f"{data.attrs['request_count']} pages, "
                    f"{data.attrs['actual_start']} ~ {data.attrs['actual_end']}, "
                    f"latest_close={data.iloc[-1]['close']}, "
                    f"partial={data.attrs['is_partial']}",
                    flush=True,
                )
