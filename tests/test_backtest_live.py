"""선택적으로 실행하는 실제 Binance 데이터와 백테스트 UI 연동 검증."""

import os
from pathlib import Path
import unittest

import numpy as np
from streamlit.testing.v1 import AppTest

from backtest.engine import run_backtest
from backtest.models import BacktestConfig
from services.market_data import load_market_data
from strategies.models import default_strategy


@unittest.skipUnless(os.getenv("RUN_BINANCE_LIVE_TESTS") == "1", "실제 API 테스트는 별도로 활성화합니다.")
class BacktestLiveTests(unittest.TestCase):
    def test_real_btc_eth_backtest_ledger(self):
        for symbol in ("BTC/USDT", "ETH/USDT"):
            with self.subTest(symbol=symbol):
                data = load_market_data(symbol, "1h", "1개월")
                result = run_backtest(data, default_strategy(), BacktestConfig())
                curve = result.equity_curve
                self.assertTrue((curve[["cash", "free_cash", "equity"]] >= 0).all().all())
                np.testing.assert_allclose(curve["cash"] + curve["position_value"], curve["equity"])
                np.testing.assert_allclose(10000 + curve["realized_pnl"] + curve["unrealized_pnl"], curve["equity"])
                fills = result.events.loc[result.events["status"] == "FILLED"]
                self.assertTrue((fills["execution_bar"] == fills["signal_bar"] + 1).all())
                self.assertTrue((fills["execution_timestamp"] > fills["signal_timestamp"]).all())
                print(f"LIVE BACKTEST OK {symbol}: candles={len(data)}, closed={result.metrics.closed_trades}, "
                      f"final_equity={result.metrics.final_equity:.4f}, fees={result.metrics.total_fees:.4f}", flush=True)

    def test_real_streamlit_backtest_execution(self):
        path = Path(__file__).resolve().parents[1] / "app.py"
        app = AppTest.from_file(str(path), default_timeout=40).run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        app.button(key="run_backtest").click().run()
        self.assertFalse(app.exception, [item.message for item in app.exception])
        self.assertFalse(app.error, [item.value for item in app.error])
        self.assertTrue(any(item.label == "최종 평가 자산" for item in app.metric))
        self.assertGreaterEqual(len(app.get("plotly_chart")), 2)
        print("LIVE BACKTEST APP OK: settings, explicit run, metrics, equity and trade ledger rendered", flush=True)
