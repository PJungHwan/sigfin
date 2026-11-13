"""Streamlit 실 UI/세션 테스트. 모든 시장·거시·LLM 응답은 테스트 전용이다."""

import unittest
from unittest.mock import patch

import pandas as pd
from streamlit.testing.v1 import AppTest

from config.llm_settings import DEFAULT_OPENAI_MODEL
from llm.client import ClientResult
from llm.config import AIError, OpenAIConfig
from llm.schemas import TokenUsage
from services.ai_analysis_service import mock_report
from services.market_data import load_market_data
from test_app import APP_PATH, full_fixture_frame, fixture_frame
from test_ai_analysis import TEST_OPENAI_KEY
from test_fred import TEST_KEY
from test_macro_ui import mock_series


class AIUITests(unittest.TestCase):
    def setUp(self):
        load_market_data.clear()
        self.market = patch("services.market_data.fetch_binance_ohlcv", return_value=full_fixture_frame()).start()
        self.config = patch("ui.ai_analysis_view.read_openai_config", return_value=OpenAIConfig()).start()
        patch("services.macro_service.get_api_key", return_value=TEST_KEY).start()
        self.request = patch("services.ai_analysis_service.request_analysis").start()
        self.addCleanup(patch.stopall)

    def tearDown(self):
        load_market_data.clear()

    def app(self):
        app = AppTest.from_file(str(APP_PATH), default_timeout=35).run()
        self.assertFalse(app.exception)
        return app

    def live_ready(self):
        self.config.return_value = OpenAIConfig(api_key=TEST_OPENAI_KEY)
        def request(snapshot, analyzed_at, config, max_output):
            return ClientResult(mock_report(snapshot, analyzed_at), TokenUsage(input_tokens=1000, output_tokens=500, total_tokens=1500), config.model)
        self.request.side_effect = request
        app = self.app()
        app.selectbox(key="ai_mode").select("live").run()
        return app

    def test_initial_rerun_and_widget_changes_do_not_call_api(self):
        app = self.app()
        app.run()
        app.checkbox(key="show_rsi").uncheck().run()
        app.checkbox(key="macro_cpi").check().run()
        self.request.assert_not_called()
        self.assertEqual(self.market.call_count, 1)
        self.assertEqual(len(app.sidebar.checkbox), 11)

    def test_mock_button_renders_report_and_labelled_signal_without_api(self):
        app = self.app()
        app.button(key="analyze_ai").click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        self.request.assert_not_called()
        self.assertTrue(any("MOCK · NEUTRAL" in text.value for text in app.markdown))
        self.assertTrue(any("가상 응답" in text.value for text in app.warning))
        self.assertTrue(any("단일 LLM" in text.value for text in app.caption))
        self.assertNotIn(TEST_OPENAI_KEY, str(app.session_state["_ai_result"]))

    def test_mock_is_available_even_when_real_api_configuration_is_invalid(self):
        self.config.side_effect = AIError("실 API 설정 오류", "CONFIGURATION")
        app = self.app()
        app.button(key="analyze_ai").click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        self.assertTrue(any("MOCK · NEUTRAL" in text.value for text in app.markdown))
        self.request.assert_not_called()

    def test_missing_key_and_unapproved_paid_request_do_not_call_api(self):
        app = self.app()
        app.selectbox(key="ai_mode").select("live").run()
        app.button(key="analyze_ai").click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any("COST_APPROVAL_REQUIRED" in text.value for text in app.error))
        app.checkbox(key="ai_cost_approved").check().run()
        app.button(key="analyze_ai").click().run()
        self.assertTrue(any("KEY_MISSING" in text.value for text in app.error))
        self.request.assert_not_called()

    def test_live_button_with_mocked_sdk_records_tokens_and_no_auto_repeat(self):
        app = self.live_ready()
        app.checkbox(key="ai_cost_approved").check().run()
        self.request.assert_not_called()
        app.button(key="analyze_ai").click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        self.assertEqual(self.request.call_count, 1)
        self.assertTrue(any("실제 보고된 토큰" in text.value for text in app.caption))
        app.checkbox(key="show_sma").check().run()
        app.run()
        self.assertEqual(self.request.call_count, 1)
        app.button(key="analyze_ai").click().run()
        self.assertTrue(any("DUPLICATE_REQUEST" in text.value for text in app.error))
        self.assertEqual(self.request.call_count, 1)

    def test_symbol_timeframe_period_and_limit_changes_invalidate_result(self):
        for key, value in (("symbol", "ETH/USDT"), ("timeframe", "4h"), ("period", "1년")):
            with self.subTest(key=key):
                app = self.app()
                app.button(key="analyze_ai").click().run()
                app.selectbox(key=key).select(value).run()
                self.assertFalse(app.exception)
                self.assertNotIn("MOCK · NEUTRAL", " ".join(text.value for text in app.markdown))
        app = self.app()
        app.button(key="analyze_ai").click().run()
        app.number_input(key="ai_request_limit").set_value(3).run()
        self.assertTrue(any("무효화" in text.value for text in app.info))
        self.request.assert_not_called()

    def test_refresh_of_input_data_invalidates_result(self):
        app = self.app()
        app.button(key="analyze_ai").click().run()
        changed = full_fixture_frame()
        changed.attrs["fetched_at"] += pd.Timedelta(seconds=1)
        self.market.return_value = changed
        app.button(key="refresh_market").click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any("무효화" in text.value for text in app.info))
        self.request.assert_not_called()

    def test_settings_change_resets_paid_approval(self):
        app = self.live_ready()
        app.checkbox(key="ai_cost_approved").check().run()
        self.assertTrue(app.checkbox(key="ai_cost_approved").value)
        app.number_input(key="ai_output_limit").set_value(4000).run()
        self.assertFalse(app.checkbox(key="ai_cost_approved").value)
        self.request.assert_not_called()

    def test_short_data_uses_local_insufficient_status(self):
        self.market.return_value = fixture_frame()
        app = self.app()
        app.button(key="analyze_ai").click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any("INSUFFICIENT_DATA" in text.value for text in app.markdown))
        self.request.assert_not_called()

    def test_fred_loaded_results_reused_and_macro_observation_limit_preserved(self):
        from services.macro_service import _load_cached
        _load_cached.clear()
        with patch("services.macro_service.fetch_fred_series", side_effect=mock_series) as fetch:
            app = self.app()
            app.checkbox(key="macro_cpi").check().run()
            app.button(key="load_macro").click().run()
            app.button(key="analyze_ai").click().run()
            self.assertFalse(app.exception)
            result = app.session_state["_ai_result"]
            self.assertTrue(any(evidence.source_type == "MACRO" for evidence in result.report.evidence))
            self.assertIn("빈티지", result.report.macro_summary)
            self.assertEqual(fetch.call_count, 1)
        _load_cached.clear()
