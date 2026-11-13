"""명시적으로 활성화하는 실제 API + 전략 UI 검증. 단위 테스트 입력으로 대체하지 않는다."""

import os
from pathlib import Path
import unittest

from streamlit.testing.v1 import AppTest

from services.market_data import load_market_data
from strategies.evaluator import evaluate_strategy
from strategies.models import default_strategy
from strategies.storage import strategy_from_json, strategy_to_json


@unittest.skipUnless(os.getenv("RUN_BINANCE_LIVE_TESTS") == "1", "실제 API 검증은 별도로 활성화합니다.")
class StrategyLiveTests(unittest.TestCase):
    def test_real_data_evaluation_and_json_roundtrip(self):
        for symbol in ("BTC/USDT", "ETH/USDT"):
            with self.subTest(symbol=symbol):
                data = load_market_data(symbol, "1h", "1주")
                strategy = strategy_from_json(strategy_to_json(default_strategy()))
                result = evaluate_strategy(data, strategy)
                self.assertTrue(result.long.index.equals(data.index))
                self.assertEqual(result.long.dtype, bool)
                self.assertEqual(result.short.dtype, bool)
                self.assertFalse(result.conflict.any())
                self.assertTrue((result.long == data["rsi_14"].lt(30)).all())
                self.assertTrue((result.short == data["rsi_14"].gt(70)).all())
                print(f"LIVE STRATEGY OK {symbol}: candles={len(data)}, "
                      f"LONG={int(result.long.sum())}, SHORT={int(result.short.sum())}, "
                      f"CONFLICT={int(result.conflict.sum())}", flush=True)

    def test_real_streamlit_builder_and_signal_preview(self):
        path = Path(__file__).resolve().parents[1] / "app.py"
        app = AppTest.from_file(str(path), default_timeout=40).run()
        self.assertFalse(app.exception, [item.message for item in app.exception])
        self.assertFalse(app.error, [item.value for item in app.error])
        app.toggle(key="show_strategy_signals").set_value(True).run()
        self.assertFalse(app.exception, [item.message for item in app.exception])
        self.assertFalse(app.error, [item.value for item in app.error])
        self.assertEqual(len(app.get("plotly_chart")), 1)
        self.assertTrue(any(item.label == "LONG 조건 충족" for item in app.metric))
        self.assertGreaterEqual(len(app.dataframe), 2)
        print("LIVE STRATEGY APP OK: builder, evaluation, chart and condition tables rendered", flush=True)
