import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from backtest.engine import run_backtest
from collectors.binance import DataCollectionError
from services.market_data import load_market_data
from strategies.models import (
    Condition, ConditionGroup, Direction, Indicator, Logic, Operand, OperandKind, Operator, Strategy,
)
from strategies.storage import strategy_to_dict
from test_app import APP_PATH, chart_names, full_fixture_frame, fixture_frame


def ui_strategy(short_only=False):
    long = () if short_only else (
        Condition("bt_long", Indicator.CLOSE, Operator.LT, Operand(OperandKind.NUMBER, 105)),
    )
    short = (Condition("bt_short", Indicator.CLOSE, Operator.GT,
                       Operand(OperandKind.NUMBER, 0 if short_only else 110)),)
    return Strategy("UI 백테스트 예제", ConditionGroup(Direction.LONG, Logic.AND, long),
                    ConditionGroup(Direction.SHORT, Logic.AND, short))


class BacktestUITests(unittest.TestCase):
    def setUp(self):
        load_market_data.clear()

    def tearDown(self):
        load_market_data.clear()

    def app(self, strategy=None):
        model = ui_strategy() if strategy is None else strategy
        app = AppTest.from_file(str(APP_PATH), default_timeout=30)
        app.session_state["_strategy_draft"] = strategy_to_dict(model)
        app.session_state["_strategy_revision"] = 0
        app.run()
        self.assertFalse(app.exception)
        return app

    def execute(self, app):
        app.button(key="run_backtest").click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        return app

    def assert_no_result(self, app):
        self.assertFalse(app.exception)
        self.assertFalse(any(item.label == "최종 평가 자산" for item in app.metric))
        self.assertFalse(any(name.startswith("가상 ") for name in chart_names(app)))

    def test_explicit_execution_and_three_charts_and_trade_table(self):
        with patch("services.market_data.fetch_binance_ohlcv", return_value=full_fixture_frame()) as fetch:
            app = self.app()
            self.assert_no_result(app)
            self.assertEqual(len(app.get("plotly_chart")), 1)
            self.execute(app)
            self.assertEqual(len(app.get("plotly_chart")), 3)
            self.assertTrue(any(item.label == "최종 평가 자산" for item in app.metric))
            self.assertIn("가상 LONG 진입", chart_names(app))
            self.assertIn("가상 LONG 청산", chart_names(app))
            self.assertTrue(any("손익 구분" in item.value.columns for item in app.dataframe))
            self.assertEqual(fetch.call_count, 1)

    def test_every_settings_change_invalidates_result(self):
        with patch("services.market_data.fetch_binance_ohlcv", return_value=full_fixture_frame()) as fetch:
            app = self.app()
            for key, value in (("bt_capital", 12000.0), ("bt_fee", 0.2),
                               ("bt_slippage", 0.1), ("bt_participation", 5.0)):
                self.execute(app)
                app.number_input(key=key).set_value(value).run()
                self.assert_no_result(app)
                self.assertTrue(any("무효화" in item.value for item in app.info))
            self.assertEqual(fetch.call_count, 1)

    def test_strategy_and_market_selection_changes_invalidate_result(self):
        with patch("services.market_data.fetch_binance_ohlcv", return_value=full_fixture_frame()):
            app = self.app()
            self.execute(app)
            app.text_input(key="strategy_name").set_value("다른 전략").run()
            self.assert_no_result(app)
            self.execute(app)
            app.selectbox(key="symbol").select("ETH/USDT").run()
            self.assert_no_result(app)
            self.execute(app)
            app.selectbox(key="period").select("1개월").run()
            self.assert_no_result(app)
            self.execute(app)
            app.selectbox(key="timeframe").select("4h").run()
            self.assert_no_result(app)

    def test_indicator_display_change_keeps_result_without_rerunning_engine(self):
        with patch("services.market_data.fetch_binance_ohlcv", return_value=full_fixture_frame()) as fetch:
            with patch("ui.backtest_view.run_backtest", wraps=run_backtest) as engine:
                app = self.app()
                self.execute(app)
                original = app.session_state["_backtest_key"]
                app.checkbox(key="show_rsi").uncheck().run()
                self.assertFalse(app.exception)
                self.assertEqual(app.session_state["_backtest_key"], original)
                self.assertTrue(any(item.label == "최종 평가 자산" for item in app.metric))
                self.assertEqual(engine.call_count, 1)
                self.assertEqual(fetch.call_count, 1)

    def test_changed_data_with_same_final_timestamp_and_price_invalidates_result(self):
        first = full_fixture_frame()
        changed = first.copy(deep=True)
        changed.loc[60, "close"] += 0.25
        with patch("services.market_data.fetch_binance_ohlcv", side_effect=[first, changed]) as fetch:
            app = self.app()
            self.execute(app)
            app.button(key="refresh_market").click().run()
            self.assert_no_result(app)
            self.assertTrue(any("무효화" in item.value for item in app.info))
            self.assertEqual(fetch.call_count, 2)

    def test_api_failure_clears_previous_result(self):
        with patch("services.market_data.fetch_binance_ohlcv", side_effect=[full_fixture_frame(), DataCollectionError("테스트 연결 실패")]):
            app = self.app()
            self.execute(app)
            app.button(key="refresh_market").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(any(item.label == "최종 평가 자산" for item in app.metric))
            self.assertTrue(any("테스트 연결 실패" in item.value for item in app.error))

    def test_invalid_strategy_clears_result_and_disables_execution(self):
        with patch("services.market_data.fetch_binance_ohlcv", return_value=full_fixture_frame()):
            app = self.app()
            self.execute(app)
            app.text_input(key="strategy_name").set_value("").run()
            self.assert_no_result(app)
            self.assertTrue(app.button(key="run_backtest").disabled)

    def test_insolvent_virtual_short_shows_error_without_breaking_market_chart(self):
        raw = fixture_frame()
        raw["open"] = 100.0
        raw["close"] = [100.0, 300.0]
        raw["high"] = [101.0, 301.0]
        raw["low"] = 99.0
        raw["volume"] = 100000.0
        with patch("services.market_data.fetch_binance_ohlcv", return_value=raw):
            app = self.app(ui_strategy(short_only=True))
            app.button(key="run_backtest").click().run()
            self.assert_no_result(app)
            self.assertTrue(any("백테스트 실행 실패" in item.value for item in app.error))
            self.assertEqual(len(app.get("plotly_chart")), 1)
