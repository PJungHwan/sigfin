"""실제 LangGraph + AppTest. 시세·FRED는 합성이며 LLM/네트워크 호출은 차단한다."""

import unittest
from unittest.mock import patch

import pandas as pd
from streamlit.testing.v1 import AppTest

from collectors.binance import DataCollectionError
from llm.config import OpenAIConfig
from services.market_data import load_market_data
from services.macro_service import _load_cached
from test_app import APP_PATH, full_fixture_frame
from test_macro_ui import mock_series
from test_fred import TEST_KEY


class WorkflowUITests(unittest.TestCase):
    def setUp(self):
        load_market_data.clear()
        _load_cached.clear()
        self.market = patch("services.market_data.fetch_binance_ohlcv", return_value=full_fixture_frame()).start()
        patch("ui.ai_analysis_view.read_openai_config", return_value=OpenAIConfig()).start()
        patch("services.macro_service.get_api_key", return_value=TEST_KEY).start()
        self.api = patch("services.ai_analysis_service.request_analysis").start()
        self.addCleanup(patch.stopall)

    def tearDown(self):
        load_market_data.clear()
        _load_cached.clear()

    def app(self):
        app = AppTest.from_file(str(APP_PATH), default_timeout=40).run()
        self.assertFalse(app.exception)
        return app

    def test_diagram_default_waiting_no_automatic_workflow_or_api(self):
        with patch("ui.workflow_view.run_workflow") as run:
            app = self.app()
            app.checkbox(key="show_rsi").uncheck().run()
            app.run()
        run.assert_not_called()
        self.api.assert_not_called()
        self.assertEqual(sum("mermaid" in item.value and "research_aggregator" in item.value for item in app.markdown), 1)
        self.assertEqual(len(app.get("plotly_chart")), 1)
        self.assertEqual(self.market.call_count, 1)
        self.assertTrue(any("NOT CONNECTED" in item.value for item in app.markdown))

    def test_button_runs_real_graph_and_connects_agent_cards(self):
        app = self.app()
        app.button(key="run_multi_agent").click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        state = app.session_state["_workflow_execution"].state
        self.assertEqual(state["execution_status"], "COMPLETED")
        self.assertEqual(state["macro_result"].macro_status, "INSUFFICIENT_DATA")
        self.assertTrue(any("COMPLETED" in item.value and "Technical Agent" in item.value for item in app.markdown))
        self.assertTrue(any("실행 후 기록" in item.value for item in app.caption))
        self.api.assert_not_called()
        self.assertEqual(self.market.call_count, 1)

    def test_single_llm_mock_and_workflow_results_are_independent(self):
        app = self.app()
        app.button(key="analyze_ai").click().run()
        single = app.session_state["_ai_result"]
        app.button(key="run_multi_agent").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["_ai_result"], single)
        self.assertEqual(app.session_state["_workflow_execution"].state["final_report"].execution_mode, "Mock Workflow")
        self.assertTrue(any("MOCK · NEUTRAL" in item.value for item in app.markdown))
        self.api.assert_not_called()

    def test_rerun_and_display_toggles_keep_same_run_and_cache(self):
        app = self.app()
        app.button(key="run_multi_agent").click().run()
        run_id = app.session_state["_workflow_execution"].state["run_id"]
        app.checkbox(key="show_sma").check().run()
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["_workflow_execution"].state["run_id"], run_id)
        self.assertEqual(self.market.call_count, 1)
        self.api.assert_not_called()

    def test_symbol_timeframe_period_refresh_invalidate_old_result(self):
        for key, value in (("symbol", "ETH/USDT"), ("timeframe", "4h"), ("period", "1년")):
            app = self.app()
            app.button(key="run_multi_agent").click().run()
            app.selectbox(key=key).select(value).run()
            self.assertFalse(app.exception)
            self.assertTrue(any("워크플로우" in item.value and "무효화" in item.value for item in app.info))
            self.assertNotIn("_workflow_execution", app.session_state)
        app = self.app()
        app.button(key="run_multi_agent").click().run()
        data = full_fixture_frame()
        data.attrs["fetched_at"] += pd.Timedelta(seconds=1)
        self.market.return_value = data
        app.button(key="refresh_market").click().run()
        self.assertNotIn("_workflow_execution", app.session_state)

    def test_fred_reused_without_extra_fetch_and_selection_invalidates(self):
        with patch("services.macro_service.fetch_fred_series", side_effect=mock_series) as fetch:
            app = self.app()
            app.checkbox(key="macro_cpi").check().run()
            app.button(key="load_macro").click().run()
            app.button(key="run_multi_agent").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state["_workflow_execution"].state["macro_result"].macro_status, "CONTEXT_ONLY")
            self.assertEqual(fetch.call_count, 1)
            app.checkbox(key="macro_unrate").check().run()
            self.assertNotIn("_workflow_execution", app.session_state)
        self.api.assert_not_called()

    def test_without_keys_or_market_data_is_failed_graph_with_logs(self):
        self.market.side_effect = DataCollectionError("테스트 네트워크 오류")
        from models.macro import FredKeyError
        with patch("services.macro_service.get_api_key", side_effect=FredKeyError("키 없음")):
            app = self.app()
            app.button(key="run_multi_agent").click().run()
        self.assertFalse(app.exception)
        state = app.session_state["_workflow_execution"].state
        self.assertEqual(state["execution_status"], "FAILED")
        self.assertIsNone(state["final_report"])
        self.assertTrue(state["node_execution_logs"])
        self.api.assert_not_called()
