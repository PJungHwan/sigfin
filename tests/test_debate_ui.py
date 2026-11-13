"""Mock Debate AppTest. 데이터 수집은 합성, OpenAI SDK와 외부 LLM 호출은 금지한다."""

from unittest.mock import patch
import unittest

from streamlit.testing.v1 import AppTest

from agents.bull_agent import BullAgent
from graph.debate_workflow import build_debate_workflow
from indicators.technical import add_technical_indicators
from llm.config import OpenAIConfig
from services.market_data import load_market_data
from services.macro_service import _load_cached
from test_app import APP_PATH, full_fixture_frame, fixture_frame
from test_fred import TEST_KEY
from test_macro_ui import mock_series


class DebateUITests(unittest.TestCase):
    def setUp(self):
        load_market_data.clear()
        _load_cached.clear()
        self.market = patch("services.market_data.fetch_binance_ohlcv", return_value=full_fixture_frame()).start()
        patch("ui.ai_analysis_view.read_openai_config", return_value=OpenAIConfig()).start()
        patch("ui.workflow_view.read_openai_config", return_value=OpenAIConfig()).start()
        patch("services.macro_service.get_api_key", return_value=TEST_KEY).start()
        self.fred = patch("services.macro_service.fetch_fred_series", side_effect=mock_series).start()
        self.sdk = patch("openai.OpenAI").start()
        self.single = patch("services.ai_analysis_service.request_analysis").start()
        self.addCleanup(patch.stopall)

    def tearDown(self):
        load_market_data.clear()
        _load_cached.clear()

    def app(self):
        app = AppTest.from_file(str(APP_PATH), default_timeout=45).run()
        self.assertFalse(app.exception)
        return app

    def enabled(self):
        app = self.app()
        app.checkbox(key="debate_enabled").check().run()
        return app

    def test_default_debate_off_and_no_fake_results_or_automatic_api(self):
        app = self.app()
        self.assertFalse(app.checkbox(key="debate_enabled").value)
        self.assertNotIn("_workflow_execution", app.session_state)
        self.assertTrue(any("Bull Agent" in item.value and "NOT CONNECTED" in item.value for item in app.markdown))
        app.checkbox(key="debate_enabled").check().run()
        app.selectbox(key="debate_rounds").select(1).run()
        self.assertNotIn("_workflow_execution", app.session_state)
        self.assertTrue(any("bull_agent" in item.value and "mermaid" in item.value for item in app.markdown))
        self.sdk.assert_not_called()

    def test_button_executes_real_graph_and_updates_bull_bear_states(self):
        app = self.enabled()
        app.button(key="run_multi_agent").click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        state = app.session_state["_workflow_execution"].state
        self.assertEqual(state["debate_status"], "COMPLETED")
        self.assertEqual(state["debate_round"], 2)
        for label in ("Bull Agent", "Bear Agent"):
            self.assertTrue(any(label in item.value and "COMPLETED" in item.value for item in app.markdown))
        self.assertTrue(any("Risk Manager" in item.value and "NOT CONNECTED" in item.value for item in app.markdown))
        self.assertTrue(any("Mock Debate" in item.value for item in app.success))
        self.assertTrue(any("실행 후 기록" in item.value for item in app.caption))
        self.assertTrue(any("반박" in item.value and "참조" in item.value for item in app.caption))
        self.assertEqual(self.market.call_count, 1)
        self.sdk.assert_not_called()
        self.single.assert_not_called()

    def test_round_one_view_and_round_setting_invalidate_old_result(self):
        app = self.enabled()
        app.selectbox(key="debate_rounds").select(1).run()
        app.button(key="run_multi_agent").click().run()
        state = app.session_state["_workflow_execution"].state
        self.assertEqual(state["debate_round"], 1)
        self.assertTrue(all(item.round == 1 for item in state["debate_history"]))
        app.selectbox(key="debate_rounds").select(2).run()
        self.assertNotIn("_workflow_execution", app.session_state)
        self.assertTrue(any("무효화" in item.value for item in app.info))

    def test_duplicate_keeps_previous_valid_result_and_records_blocked_attempt(self):
        app = self.enabled()
        app.button(key="run_multi_agent").click().run()
        first = app.session_state["_workflow_execution"].state["run_id"]
        app.button(key="run_multi_agent").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["_workflow_execution"].state["run_id"], first)
        self.assertEqual(app.session_state["_debate_last_attempt"].state["errors"][0].category, "DUPLICATE_REQUEST")
        self.assertTrue(any("중복" in item.value and "이전 실행 결과" in item.value for item in app.warning))
        self.assertEqual(app.session_state["_ai_ledger"].request_count, 0)
        self.sdk.assert_not_called()

    def test_display_toggles_use_same_result_and_existing_market_cache(self):
        app = self.enabled()
        app.button(key="run_multi_agent").click().run()
        run_id = app.session_state["_workflow_execution"].state["run_id"]
        app.checkbox(key="show_sma").check().run()
        app.run()
        self.assertEqual(app.session_state["_workflow_execution"].state["run_id"], run_id)
        self.assertEqual(self.market.call_count, 1)
        self.sdk.assert_not_called()

    def test_asset_timeframe_or_debate_toggle_invalidates(self):
        app = self.enabled()
        app.button(key="run_multi_agent").click().run()
        app.selectbox(key="symbol").select("ETH/USDT").run()
        self.assertNotIn("_workflow_execution", app.session_state)
        app.button(key="run_multi_agent").click().run()
        app.selectbox(key="timeframe").select("4h").run()
        self.assertNotIn("_workflow_execution", app.session_state)
        app.button(key="run_multi_agent").click().run()
        app.checkbox(key="debate_enabled").uncheck().run()
        self.assertNotIn("_workflow_execution", app.session_state)
        app.button(key="run_multi_agent").click().run()
        self.assertNotIn("debate_history", app.session_state["_workflow_execution"].state)
        self.assertTrue(any("Bull Agent" in item.value and "NOT CONNECTED" in item.value for item in app.markdown))

    def test_llm_mode_with_debate_cannot_activate_paid_path(self):
        app = self.enabled()
        app.selectbox(key="workflow_mode").select("LLM Workflow").run()
        self.assertTrue(app.button(key="run_multi_agent").disabled)
        self.assertTrue(any("Mock Workflow에서만 지원" in item.value for item in app.warning))
        app.checkbox(key="debate_enabled").uncheck().run()
        self.assertFalse(app.button(key="run_multi_agent").disabled)
        self.sdk.assert_not_called()

    def test_fred_cache_reuse_and_macro_selection_invalidates(self):
        app = self.enabled()
        app.checkbox(key="macro_cpi").check().run()
        app.button(key="load_macro").click().run()
        app.button(key="run_multi_agent").click().run()
        self.assertFalse(app.exception)
        state = app.session_state["_workflow_execution"].state
        self.assertEqual(state["debate_evidence"].macro_status, "CONTEXT_ONLY")
        self.assertTrue(any(fact.source_type == "MACRO" for fact in state["debate_evidence"].facts))
        self.assertEqual(self.fred.call_count, 1)
        app.checkbox(key="macro_unrate").check().run()
        self.assertNotIn("_workflow_execution", app.session_state)
        self.sdk.assert_not_called()

    def test_single_llm_mock_is_preserved_independently(self):
        app = self.enabled()
        app.button(key="analyze_ai").click().run()
        single = app.session_state["_ai_result"]
        app.button(key="run_multi_agent").click().run()
        self.assertEqual(app.session_state["_ai_result"], single)
        self.assertTrue(any("MOCK · NEUTRAL" in item.value for item in app.markdown))
        self.sdk.assert_not_called()

    def test_partial_position_failure_is_not_displayed_as_debate_success(self):
        class Broken(BullAgent):
            def propose(self, pool):
                raise RuntimeError("private-input")
        graph = build_debate_workflow(bull_agent=Broken())
        with patch("graph.debate_workflow.get_debate_workflow", return_value=graph):
            app = self.enabled()
            app.button(key="run_multi_agent").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["_workflow_execution"].state["execution_status"], "PARTIAL")
        self.assertTrue(any("부분 완료" in item.value for item in app.warning))
        self.assertFalse(any("Mock Debate 실행 완료" in item.value for item in app.success))
        self.assertTrue(any("Bull Agent" in item.value and "FAILED" in item.value for item in app.markdown))
        self.assertFalse(any("private-input" in item.value for item in app.text))

    def test_insufficient_data_has_skipped_cards_and_no_fabricated_conversation(self):
        self.market.return_value = fixture_frame()
        app = self.enabled()
        app.button(key="run_multi_agent").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["_workflow_execution"].state["debate_status"], "SKIPPED")
        self.assertFalse(app.session_state["_workflow_execution"].state["debate_history"])
        self.assertTrue(any("토론을 건너뛰었" in item.value for item in app.info))
        self.assertTrue(any("Bull Agent" in item.value and "SKIPPED" in item.value for item in app.markdown))
