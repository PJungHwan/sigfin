"""AppTest + 실제 StateGraph. 시장/FRED는 합성 응답이며 유료 호출을 차단한다."""

import unittest
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest

from graph.workflow import build_workflow
from llm.config import OpenAIConfig
from services.market_data import load_market_data
from services.macro_service import _load_cached
from test_app import APP_PATH, full_fixture_frame, fixture_frame
from test_fred import TEST_KEY
from test_macro_ui import mock_series


class RiskManagerUITests(unittest.TestCase):
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
        self.sdk.assert_not_called()
        self.single.assert_not_called()
        load_market_data.clear()
        _load_cached.clear()

    def app(self, risk=False, debate=False):
        app = AppTest.from_file(str(APP_PATH), default_timeout=45).run()
        self.assertFalse(app.exception)
        if debate:
            app.checkbox(key="debate_enabled").check().run()
        if risk:
            app.checkbox(key="risk_manager_enabled").check().run()
        return app

    def run_agent(self, app):
        app.button(key="run_multi_agent").click().run()
        self.assertFalse(app.exception)
        return app.session_state["_workflow_execution"].state

    def test_default_off_and_toggles_never_execute_automatically(self):
        with patch("ui.workflow_view.run_workflow") as run:
            app = self.app()
            self.assertFalse(app.checkbox(key="risk_manager_enabled").value)
            self.assertTrue(any("Risk Manager" in item.value and "NOT CONNECTED" in item.value for item in app.markdown))
            app.checkbox(key="risk_manager_enabled").check().run()
            app.number_input(key="rm_leverage").set_value(3.).run()
            self.assertNotIn("_workflow_execution", app.session_state)
            self.assertTrue(any("risk_manager" in item.value and "mermaid" in item.value for item in app.markdown))
        run.assert_not_called()
        self.assertEqual(self.market.call_count, 1)

    def test_all_four_debate_risk_combinations_and_actual_node_logs(self):
        for debate, risk in ((False, False), (False, True), (True, False), (True, True)):
            with self.subTest(debate=debate, risk=risk):
                app = self.app(risk=risk, debate=debate)
                state = self.run_agent(app)
                self.assertEqual(state["execution_status"], "COMPLETED")
                self.assertEqual("risk_result" in state, risk)
                self.assertEqual("debate_evaluation" in state, debate)
                if risk:
                    self.assertTrue(any("Risk Manager" in item.value and "COMPLETED" in item.value for item in app.markdown))
                    self.assertEqual(state["final_report"].risk_report.execution_mode, "Mock Risk Manager")
                    self.assertTrue(any("RISK LEVEL" in item.value for item in app.markdown))
                    self.assertTrue(any("RESEARCH ACTION" in item.value for item in app.markdown))
                    self.assertTrue(any(log.node == "risk_manager" and log.duration_ms is not None for log in state["node_execution_logs"]))
                else:
                    self.assertTrue(any("Risk Manager" in item.value and "NOT CONNECTED" in item.value for item in app.markdown))

    def test_each_position_setting_change_invalidates_and_does_not_fetch(self):
        app = self.app(risk=True)
        for key, value in (("rm_capital", 20000.), ("rm_allowance", 2.), ("rm_stop", 5.),
                           ("rm_leverage", 2.), ("rm_fee", .2), ("rm_slippage", .1)):
            self.run_agent(app)
            app.number_input(key=key).set_value(value).run()
            self.assertFalse(app.exception)
            self.assertNotIn("_workflow_execution", app.session_state)
            self.assertTrue(any("무효화" in item.value for item in app.info))
        self.assertEqual(self.market.call_count, 1)

    def test_display_toggle_keeps_report_without_recalculating_or_fetching(self):
        app = self.app(risk=True, debate=True)
        state = self.run_agent(app)
        app.checkbox(key="show_sma").check().run()
        app.run()
        self.assertEqual(app.session_state["_workflow_execution"].state["run_id"], state["run_id"])
        self.assertEqual(self.market.call_count, 1)
        self.assertFalse(app.exception)

    def test_disable_risk_invalidates_and_restores_existing_workflow(self):
        app = self.app(risk=True, debate=True)
        self.run_agent(app)
        app.checkbox(key="risk_manager_enabled").uncheck().run()
        self.assertNotIn("_workflow_execution", app.session_state)
        state = self.run_agent(app)
        self.assertNotIn("risk_result", state)
        self.assertEqual(state["debate_status"], "COMPLETED")
        self.assertFalse(hasattr(state["final_report"], "risk_report"))

    def test_backtest_settings_import_copies_only_into_risk_controls(self):
        app = self.app(risk=True)
        app.number_input(key="bt_capital").set_value(15000.).run()
        app.number_input(key="bt_fee").set_value(.2).run()
        app.number_input(key="bt_slippage").set_value(.1).run()
        app.button(key="rm_import_backtest").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.number_input(key="rm_capital").value, 15000.)
        self.assertEqual(app.number_input(key="rm_fee").value, .2)
        self.assertEqual(app.number_input(key="rm_slippage").value, .1)
        app.number_input(key="rm_capital").set_value(5000.).run()
        self.assertEqual(app.number_input(key="bt_capital").value, 15000.)
        self.assertNotIn("_backtest_result", app.session_state)
        state = self.run_agent(app)
        self.assertEqual(state["risk_result"].position_settings.capital, 5000.)

    def test_llm_risk_combination_is_blocked_and_old_llm_mode_remains(self):
        app = self.app(risk=True)
        app.selectbox(key="workflow_mode").select("LLM Workflow").run()
        self.assertTrue(app.button(key="run_multi_agent").disabled)
        self.assertTrue(any("Mock Risk Manager" in item.value and "Mock Workflow" in item.value for item in app.warning))
        app.checkbox(key="risk_manager_enabled").uncheck().run()
        self.assertFalse(app.button(key="run_multi_agent").disabled)
        self.assertNotIn("_workflow_execution", app.session_state)

    def test_zero_allowance_is_zero_quantity_and_abstain(self):
        app = self.app(risk=True)
        app.number_input(key="rm_allowance").set_value(0.).run()
        state = self.run_agent(app)
        report = state["risk_result"]
        self.assertEqual(report.position_assessment.cost_aware_quantity, 0.)
        self.assertEqual(report.trading_action, "ABSTAIN")
        self.assertTrue(any("ABSTAIN" in item.value and "별도 위험" in item.value for item in app.markdown))

    def test_short_data_is_unknown_and_partial_with_na_metrics(self):
        self.market.return_value = fixture_frame()
        app = self.app(risk=True)
        state = self.run_agent(app)
        self.assertEqual(state["risk_result"].risk_level, "UNKNOWN")
        self.assertEqual(state["execution_status"], "PARTIAL")
        self.assertEqual(state["research_action"], "ABSTAIN")
        self.assertTrue(any("정상 위험 등급" in item.value for item in app.warning))
        self.assertTrue(any("ATR (14)" in item.value and "N/A" in item.value for item in app.markdown))

    def test_failed_risk_is_partial_abstain_without_fabricated_grade(self):
        agent = Mock()
        agent.analyze.side_effect = RuntimeError("sk-test-private")
        graph = build_workflow(risk_enabled=True, risk_manager=agent)
        with patch("graph.runner.get_workflow", return_value=graph):
            app = self.app(risk=True)
            state = self.run_agent(app)
        self.assertEqual(state["execution_status"], "PARTIAL")
        self.assertIsNone(state["risk_result"])
        self.assertEqual(state["research_action"], "ABSTAIN")
        self.assertTrue(any("Risk Manager" in item.value and "FAILED" in item.value for item in app.markdown))
        self.assertTrue(any("UNKNOWN" in item.value and "ABSTAIN" in item.value for item in app.error))
        self.assertFalse(any("sk-test-private" in item.value for item in app.error))

    def test_fred_metadata_reused_without_direction_or_extra_requests(self):
        app = self.app(risk=True, debate=True)
        app.checkbox(key="macro_cpi").check().run()
        app.button(key="load_macro").click().run()
        state = self.run_agent(app)
        report = state["risk_result"]
        self.assertEqual(state["risk_input"].macro_status, "CONTEXT_ONLY")
        self.assertTrue(any(item.source_type == "MACRO" for item in report.evidence))
        self.assertTrue(any("빈티지" in item for item in report.limitations))
        self.assertEqual(self.fred.call_count, 1)
        self.assertEqual(self.market.call_count, 1)

    def test_single_mock_report_survives_risk_workflow_independently(self):
        app = self.app(risk=True)
        app.button(key="analyze_ai").click().run()
        single = app.session_state["_ai_result"]
        self.run_agent(app)
        self.assertEqual(app.session_state["_ai_result"], single)
        self.assertEqual(len(app.get("plotly_chart")), 1)
