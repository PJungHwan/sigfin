"""AppTest의 LLM 버튼/승인/결과 무효화. SDK는 전부 MockTransport로만 실행한다."""

import json
import unittest
from unittest.mock import patch

import httpx
from streamlit.testing.v1 import AppTest

from llm.config import OpenAIConfig
from services.market_data import load_market_data
from services.macro_service import _load_cached
from test_ai_analysis import TEST_OPENAI_KEY
from test_ai_client import REAL_OPENAI, api_response
from test_app import APP_PATH, full_fixture_frame
from test_fred import TEST_KEY
from test_macro_ui import mock_series
from test_llm_workflow import output_for


class LLMWorkflowUITests(unittest.TestCase):
    def setUp(self):
        load_market_data.clear()
        _load_cached.clear()
        self.requests = []
        self.market = patch("services.market_data.fetch_binance_ohlcv", return_value=full_fixture_frame()).start()
        patch("ui.ai_analysis_view.read_openai_config", return_value=OpenAIConfig()).start()
        self.config = patch("ui.workflow_view.read_openai_config", return_value=OpenAIConfig(api_key=TEST_OPENAI_KEY)).start()
        patch("services.macro_service.get_api_key", return_value=TEST_KEY).start()
        patch("services.macro_service.fetch_fred_series", side_effect=mock_series).start()
        self.single = patch("services.ai_analysis_service.request_analysis").start()
        self.sdk = patch("llm.client.openai.OpenAI", side_effect=self.factory).start()
        self.addCleanup(patch.stopall)

    def tearDown(self):
        load_market_data.clear()
        _load_cached.clear()

    def factory(self, **kwargs):
        return REAL_OPENAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(self.handler)))

    def handler(self, request):
        content = json.loads(json.loads(request.content)["input"][0]["content"])
        self.requests.append(content)
        return httpx.Response(200, json=api_response(json.dumps(output_for(content))))

    def app(self):
        app = AppTest.from_file(str(APP_PATH), default_timeout=40).run()
        self.assertFalse(app.exception)
        return app

    def llm(self, app):
        app.selectbox(key="workflow_mode").select("LLM Workflow").run()
        self.assertFalse(app.exception)
        return app

    def test_default_mock_and_mode_switch_never_auto_call(self):
        app = self.app()
        self.assertEqual(app.selectbox(key="workflow_mode").value, "Mock Workflow")
        self.llm(app)
        app.checkbox(key="workflow_cost_approved").check().run()
        app.checkbox(key="show_sma").check().run()
        app.run()
        self.sdk.assert_not_called()
        self.single.assert_not_called()
        self.assertEqual(self.market.call_count, 1)
        self.assertTrue(any("대체하지" in item.value for item in app.caption))

    def test_missing_approval_blocks_button_and_then_approved_runs(self):
        app = self.llm(self.app())
        app.button(key="run_multi_agent").click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any("COST_APPROVAL_REQUIRED" in item.value for item in app.error))
        self.sdk.assert_not_called()
        app.checkbox(key="workflow_cost_approved").check().run()
        app.button(key="run_multi_agent").click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        state = app.session_state["_workflow_execution"].state
        self.assertEqual(state["execution_mode"], "LLM Workflow")
        self.assertEqual(state["execution_status"], "COMPLETED")
        self.assertTrue(state["technical_execution"].api_called)
        self.assertFalse(state["macro_execution"].api_called)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(app.session_state["_ai_ledger"].request_count, 1)
        app.button(key="run_multi_agent").click().run()
        self.assertEqual(app.session_state["_workflow_execution"].state["errors"][0].category, "DUPLICATE_REQUEST")
        self.assertEqual(len(self.requests), 1)

    def test_two_loaded_macro_and_technical_calls_reuse_existing_data(self):
        app = self.llm(self.app())
        app.checkbox(key="macro_cpi").check().run()
        app.button(key="load_macro").click().run()
        app.checkbox(key="workflow_cost_approved").check().run()
        app.button(key="run_multi_agent").click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        state = app.session_state["_workflow_execution"].state
        self.assertEqual(state["macro_result"].macro_status, "CONTEXT_ONLY")
        self.assertEqual({item["role"] for item in self.requests}, {"technical_agent", "macro_agent"})
        self.assertEqual(self.market.call_count, 1)
        self.assertTrue(any("NOT CONNECTED" in item.value for item in app.markdown))
        run_id = state["run_id"]
        app.checkbox(key="show_sma").check().run()
        self.assertEqual(app.session_state["_workflow_execution"].state["run_id"], run_id)
        self.assertEqual(len(self.requests), 2)

    def test_mode_and_settings_changes_invalidate_and_reset_approval(self):
        app = self.llm(self.app())
        app.checkbox(key="workflow_cost_approved").check().run()
        app.button(key="run_multi_agent").click().run()
        app.number_input(key="ai_output_limit").set_value(2000).run()
        self.assertNotIn("_workflow_execution", app.session_state)
        self.assertFalse(app.checkbox(key="workflow_cost_approved").value)
        app.selectbox(key="workflow_mode").select("Mock Workflow").run()
        app.button(key="run_multi_agent").click().run()
        self.assertEqual(app.session_state["_workflow_execution"].state["execution_mode"], "Mock Workflow")
        self.llm(app)
        self.assertNotIn("_workflow_execution", app.session_state)
        self.assertEqual(len(self.requests), 1)

    def test_key_missing_and_unknown_price_do_not_call(self):
        self.config.return_value = OpenAIConfig()
        app = self.llm(self.app())
        app.checkbox(key="workflow_cost_approved").check().run()
        app.button(key="run_multi_agent").click().run()
        self.assertTrue(any("KEY_MISSING" in item.value for item in app.error))
        self.sdk.assert_not_called()
        self.config.return_value = OpenAIConfig("unknown", TEST_OPENAI_KEY)
        app.run()
        self.assertFalse(app.checkbox(key="workflow_cost_approved").value)
        app.checkbox(key="workflow_cost_approved").check().run()
        app.button(key="run_multi_agent").click().run()
        self.assertTrue(any("MODEL_UNVERIFIED" in item.value for item in app.error))
        self.sdk.assert_not_called()

    def test_shared_request_limit_blocks_entire_two_call_batch(self):
        app = self.llm(self.app())
        app.checkbox(key="macro_cpi").check().run()
        app.button(key="load_macro").click().run()
        app.number_input(key="ai_request_limit").set_value(1).run()
        app.checkbox(key="workflow_cost_approved").check().run()
        app.button(key="run_multi_agent").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["_workflow_execution"].state["errors"][0].category, "REQUEST_LIMIT")
        self.assertFalse(app.session_state["_ai_ledger"].records)
        self.sdk.assert_not_called()

    def test_macro_api_failure_displays_partial_not_full_success(self):
        def handler(request):
            content = json.loads(json.loads(request.content)["input"][0]["content"])
            self.requests.append(content)
            if content["role"] == "macro_agent":
                return httpx.Response(429, json={"error": {"message": TEST_OPENAI_KEY}})
            return httpx.Response(200, json=api_response(json.dumps(output_for(content))))
        self.handler = handler
        app = self.llm(self.app())
        app.checkbox(key="macro_cpi").check().run()
        app.button(key="load_macro").click().run()
        app.checkbox(key="workflow_cost_approved").check().run()
        app.button(key="run_multi_agent").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["_workflow_execution"].state["execution_status"], "PARTIAL")
        self.assertTrue(any("부분 완료" in item.value for item in app.warning))
        self.assertFalse(any("LLM Workflow 처리 완료" in item.value for item in app.success))
        self.assertEqual(len(self.requests), 2)
