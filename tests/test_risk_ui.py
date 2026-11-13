"""실제 Streamlit 스크립트에서 리스크 설정·명시적 비교·무효화·마커를 검증한다."""

import json
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from backtest.engine import compare_risk
from collectors.binance import DataCollectionError
from services.market_data import load_market_data
from strategies.storage import strategy_to_dict
from test_app import APP_PATH, full_fixture_frame, chart_names
from test_backtest_ui import ui_strategy


class RiskUITests(unittest.TestCase):
    def setUp(self):
        load_market_data.clear()

    def tearDown(self):
        load_market_data.clear()

    def app(self):
        app = AppTest.from_file(str(APP_PATH), default_timeout=30)
        app.session_state["_strategy_draft"] = strategy_to_dict(ui_strategy())
        app.session_state["_strategy_revision"] = 0
        app.run()
        self.assertFalse(app.exception)
        return app

    def enable_target(self, app):
        app.checkbox(key="risk_take_profit_enabled").check().run()
        app.number_input(key="risk_take_profit_rate").set_value(2.0).run()
        self.assertFalse(app.exception)

    def test_defaults_off_and_disabled_inputs_do_not_auto_execute(self):
        with patch("services.market_data.fetch_binance_ohlcv", return_value=full_fixture_frame()) as fetch:
            app = self.app()
            for prefix in ("stop_loss", "take_profit", "trailing_stop"):
                self.assertFalse(app.checkbox(key=f"risk_{prefix}_enabled").value)
                self.assertTrue(app.number_input(key=f"risk_{prefix}_rate").disabled)
            self.assertFalse(any(item.label == "최종 평가 자산" for item in app.metric))
            self.assertEqual(fetch.call_count, 1)

    def test_risk_run_metrics_log_and_distinct_candlestick_marker(self):
        with patch("services.market_data.fetch_binance_ohlcv", return_value=full_fixture_frame()) as fetch:
            app = self.app()
            self.enable_target(app)
            app.button(key="run_backtest").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            result = app.session_state["_backtest_result"]
            self.assertTrue(result.risk.take_profit_enabled)
            self.assertIn("TAKE_PROFIT", result.trades.exit_reason.tolist())
            self.assertIn("리스크 청산 · 익절", chart_names(app))
            self.assertEqual(fetch.call_count, 1)

    def test_each_active_risk_setting_and_toggle_invalidates_old_result(self):
        with patch("services.market_data.fetch_binance_ohlcv", return_value=full_fixture_frame()) as fetch:
            app = self.app()
            for prefix in ("stop_loss", "take_profit", "trailing_stop"):
                app.button(key="run_backtest").click().run()
                app.checkbox(key=f"risk_{prefix}_enabled").check().run()
                self.assertFalse(any(item.label == "최종 평가 자산" for item in app.metric))
                self.assertTrue(any("무효화" in item.value for item in app.info))
                app.button(key="run_backtest").click().run()
                app.number_input(key=f"risk_{prefix}_rate").set_value(8.0).run()
                self.assertFalse(any(item.label == "최종 평가 자산" for item in app.metric))
                app.button(key="run_backtest").click().run()
                app.checkbox(key=f"risk_{prefix}_enabled").uncheck().run()
                self.assertFalse(any(item.label == "최종 평가 자산" for item in app.metric))
            self.assertFalse(app.exception)
            self.assertEqual(fetch.call_count, 1)

    def test_comparison_table_two_equity_lines_and_on_ledger(self):
        with patch("services.market_data.fetch_binance_ohlcv", return_value=full_fixture_frame()) as fetch:
            app = self.app()
            self.enable_target(app)
            app.button(key="compare_risk").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            comparison = app.session_state["_risk_comparison"]
            tables = [item.value for item in app.dataframe if "Risk OFF" in item.value.columns]
            self.assertEqual(len(tables), 1)
            self.assertEqual(len(tables[0]), 6)
            row = tables[0].loc[tables[0]["지표"] == "최종 평가 자산 (USDT)"].iloc[0]
            self.assertEqual(row["Risk ON"], f"{comparison.risk_on.metrics.final_equity:,.2f}")
            plots = [json.loads(item.proto.spec) for item in app.get("plotly_chart")]
            self.assertTrue(any({trace.get("name") for trace in plot["data"]} == {"Risk OFF", "Risk ON"}
                                for plot in plots))
            self.assertEqual(app.session_state["_backtest_result"].metrics, comparison.risk_on.metrics)
            self.assertEqual(fetch.call_count, 1)

    def test_all_disabled_comparison_identical_and_display_change_keeps_result(self):
        with patch("services.market_data.fetch_binance_ohlcv", return_value=full_fixture_frame()) as fetch:
            with patch("ui.backtest_view.compare_risk", wraps=compare_risk) as compare:
                app = self.app()
                app.button(key="compare_risk").click().run()
                previous = app.session_state["_backtest_key"]
                comparison = app.session_state["_risk_comparison"]
                self.assertEqual(comparison.risk_off.metrics, comparison.risk_on.metrics)
                app.checkbox(key="show_macd").uncheck().run()
                self.assertFalse(app.exception)
                self.assertEqual(app.session_state["_backtest_key"], previous)
                self.assertEqual(compare.call_count, 1)
                self.assertEqual(fetch.call_count, 1)

    def test_comparison_invalidated_on_risk_change_and_single_run_removes_comparison(self):
        with patch("services.market_data.fetch_binance_ohlcv", return_value=full_fixture_frame()):
            app = self.app()
            app.button(key="compare_risk").click().run()
            app.checkbox(key="risk_stop_loss_enabled").check().run()
            self.assertFalse(any("Risk OFF" in item.value.columns for item in app.dataframe))
            self.assertFalse(any(item.label == "최종 평가 자산" for item in app.metric))
            app.button(key="compare_risk").click().run()
            app.button(key="run_backtest").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(any("Risk OFF" in item.value.columns for item in app.dataframe))

    def test_api_error_clears_comparison_and_keeps_existing_ui(self):
        with patch("services.market_data.fetch_binance_ohlcv", side_effect=[
            full_fixture_frame(), DataCollectionError("리스크 테스트 네트워크 오류"),
        ]):
            app = self.app()
            app.button(key="compare_risk").click().run()
            app.button(key="refresh_market").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any("네트워크 오류" in item.value for item in app.error))
            self.assertFalse(any("Risk OFF" in item.value.columns for item in app.dataframe))
            self.assertEqual(len(app.sidebar.checkbox), 11)

    def test_comparison_engine_error_shown_without_partial_result(self):
        from backtest.models import BacktestError

        with patch("services.market_data.fetch_binance_ohlcv", return_value=full_fixture_frame()):
            app = self.app()
            with patch("ui.backtest_view.compare_risk", side_effect=BacktestError("검산 실패")):
                app.button(key="compare_risk").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any("검산 실패" in item.value for item in app.error))
            self.assertFalse(any(item.label == "최종 평가 자산" for item in app.metric))
