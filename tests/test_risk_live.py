"""실제 Binance API 테스트. 오프라인 합성 테스트와 별도로 명시적으로 활성화한다."""

import os
from pathlib import Path
import unittest

import numpy as np
from pandas.testing import assert_frame_equal
from streamlit.testing.v1 import AppTest

from backtest.engine import compare_risk, run_backtest
from backtest.models import BacktestConfig, RiskConfig
from services.market_data import load_market_data
from strategies.models import default_strategy


@unittest.skipUnless(os.getenv("RUN_BINANCE_LIVE_TESTS") == "1", "실제 API 테스트는 별도로 활성화합니다.")
class RiskLiveTests(unittest.TestCase):
    def test_real_btc_eth_risk_comparison_and_accounting(self):
        risk = RiskConfig(stop_loss_enabled=True, take_profit_enabled=True, trailing_stop_enabled=True)
        for symbol in ("BTC/USDT", "ETH/USDT"):
            with self.subTest(symbol=symbol):
                frame = load_market_data(symbol, "1h", "1개월")
                comparison = compare_risk(frame, default_strategy(), BacktestConfig(), risk=risk)
                plain = run_backtest(frame, default_strategy(), BacktestConfig())
                assert_frame_equal(plain.equity_curve, comparison.risk_off.equity_curve)
                for result in (comparison.risk_off, comparison.risk_on):
                    curve = result.equity_curve
                    np.testing.assert_allclose(curve.cash+curve.position_value, curve.equity)
                    np.testing.assert_allclose(10000+curve.realized_pnl+curve.unrealized_pnl, curve.equity)
                    self.assertTrue((curve[["cash", "equity", "free_cash"]] >= 0).all().all())
                print(f"LIVE RISK OK {symbol}: candles={len(frame)}, "
                      f"off={comparison.risk_off.metrics.final_equity:.4f}, "
                      f"on={comparison.risk_on.metrics.final_equity:.4f}, "
                      f"closed_on={comparison.risk_on.metrics.closed_trades}", flush=True)

    def test_real_streamlit_risk_comparison(self):
        path = Path(__file__).resolve().parents[1] / "app.py"
        app = AppTest.from_file(str(path), default_timeout=60).run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        app.checkbox(key="risk_stop_loss_enabled").check().run()
        app.checkbox(key="risk_trailing_stop_enabled").check().run()
        app.button(key="compare_risk").click().run()
        self.assertFalse(app.exception, [item.message for item in app.exception])
        self.assertFalse(app.error, [item.value for item in app.error])
        self.assertTrue(any("Risk OFF" in item.value.columns for item in app.dataframe))
        self.assertTrue(any(item.label == "최종 평가 자산" for item in app.metric))
        print("LIVE RISK APP OK: real data, risk controls, comparison, metrics and ledgers rendered", flush=True)
