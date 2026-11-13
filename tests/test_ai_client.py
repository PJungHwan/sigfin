"""공식 SDK를 httpx.MockTransport에 연결한다. 네트워크/유료 API 호출은 없다."""

import json
import logging
import unittest
from unittest.mock import patch

import httpx
import openai

from config.llm_settings import DEFAULT_OPENAI_MODEL
from llm.client import request_analysis, credential_log_filter
from llm.config import AIError, OpenAIConfig
from services.ai_analysis_service import mock_report
from test_ai_analysis import market_snapshot, NOW, TEST_OPENAI_KEY

REAL_OPENAI = openai.OpenAI


def api_response(text, *, status="completed", refusal=False):
    content = [{"type": "refusal", "refusal": "test refusal"}] if refusal else [
        {"type": "output_text", "text": text, "annotations": []}]
    return {"id": "resp_mock", "object": "response", "created_at": 1792050000,
            "model": DEFAULT_OPENAI_MODEL, "status": status,
            "output": [{"type": "message", "id": "msg_mock", "role": "assistant", "status": "completed", "content": content}],
            "usage": {"input_tokens": 1200, "output_tokens": 800, "total_tokens": 2000},
            "parallel_tool_calls": False, "tool_choice": "auto", "tools": [],
            "error": None, "incomplete_details": None}


class OpenAIClientTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = market_snapshot()
        self.report = mock_report(self.snapshot, NOW.isoformat())
        self.config = OpenAIConfig(api_key=TEST_OPENAI_KEY)

    def call(self, handler):
        calls = []
        def factory(**kwargs):
            calls.append(kwargs)
            return REAL_OPENAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False))
        with patch("llm.client.openai.OpenAI", side_effect=factory):
            result = request_analysis(self.snapshot, NOW.isoformat(), self.config, 3000)
        return result, calls

    def test_sdk_structured_outputs_schema_and_actual_parser(self):
        requests = []
        def handler(request):
            requests.append(json.loads(request.content))
            self.assertEqual(str(request.url), "https://api.openai.com/v1/responses")
            return httpx.Response(200, json=api_response(self.report.model_dump_json()))
        result, clients = self.call(handler)
        self.assertEqual(result.report, self.report)
        self.assertEqual(result.usage.total_tokens, 2000)
        self.assertEqual(clients[0]["max_retries"], 0)
        self.assertEqual(clients[0]["timeout"].connect, 5)
        payload = requests[0]
        self.assertEqual(payload["max_output_tokens"], 3000)
        self.assertFalse(payload["store"])
        self.assertEqual(payload["reasoning"], {"effort": "none"})
        self.assertNotIn("tools", payload)
        self.assertEqual(payload["text"]["format"]["type"], "json_schema")
        self.assertTrue(payload["text"]["format"]["strict"])
        self.assertFalse(payload["text"]["format"]["schema"]["additionalProperties"])
        self.assertNotIn(TEST_OPENAI_KEY, json.dumps(payload))

    def test_auth_rate_limit_permission_and_http_errors_are_sanitized(self):
        for status, category in ((401, "AUTHENTICATION"), (403, "PERMISSION_DENIED"),
                                 (429, "RATE_LIMIT"), (500, "SERVER_ERROR"), (400, "API_ERROR")):
            count = []
            def handler(request):
                count.append(request)
                return httpx.Response(status, json={"error": {"message": TEST_OPENAI_KEY, "type": "test_error", "code": "test"}})
            with self.subTest(status=status), self.assertRaises(AIError) as caught:
                self.call(handler)
            self.assertEqual(caught.exception.category, category)
            self.assertEqual(len(count), 1)
            self.assertNotIn(TEST_OPENAI_KEY, str(caught.exception))
            self.assertTrue(caught.exception.__suppress_context__)

    def test_timeout_and_network_connection_errors(self):
        for exception, category in ((httpx.ReadTimeout, "TIMEOUT"), (httpx.ConnectError, "NETWORK_ERROR")):
            def handler(request):
                raise exception(TEST_OPENAI_KEY, request=request)
            with self.subTest(category=category), self.assertRaises(AIError) as caught:
                self.call(handler)
            self.assertEqual(caught.exception.category, category)
            self.assertNotIn(TEST_OPENAI_KEY, str(caught.exception))

    def test_bad_output_json_or_schema_still_preserves_reported_usage(self):
        for text in ("{bad JSON", "{}", self.report.model_dump_json().replace('"NEUTRAL"', '"GUARANTEED"')):
            with self.subTest(text=text[:15]), self.assertRaises(AIError) as caught:
                self.call(lambda request: httpx.Response(200, json=api_response(text)))
            self.assertEqual(caught.exception.category, "RESPONSE_VALIDATION")
            self.assertEqual(caught.exception.usage.total_tokens, 2000)

    def test_refusal_incomplete_and_missing_usage(self):
        for status, refusal, expected in (("completed", True, "REFUSAL"), ("incomplete", False, "INCOMPLETE_RESPONSE")):
            with self.subTest(status=status), self.assertRaises(AIError) as caught:
                self.call(lambda request: httpx.Response(200, json=api_response("{}", status=status, refusal=refusal)))
            self.assertEqual(caught.exception.category, expected)
            self.assertIsNotNone(caught.exception.usage)
        payload = api_response(self.report.model_dump_json())
        payload["usage"] = None
        result, _ = self.call(lambda request: httpx.Response(200, json=payload))
        self.assertIsNone(result.usage)

    def test_missing_key_and_unverified_model_never_construct_client(self):
        with patch("llm.client.openai.OpenAI") as client:
            for config, category in ((OpenAIConfig(), "KEY_MISSING"),
                                     (OpenAIConfig("unverified-model", TEST_OPENAI_KEY), "MODEL_UNVERIFIED")):
                with self.subTest(category=category), self.assertRaises(AIError) as caught:
                    request_analysis(self.snapshot, NOW.isoformat(), config, 3000)
                self.assertEqual(caught.exception.category, category)
            client.assert_not_called()

    def test_debug_logs_redact_key_and_authorization(self):
        logger = logging.getLogger("openai._base_client")
        with self.assertLogs(logger, level="DEBUG") as logs:
            with credential_log_filter(TEST_OPENAI_KEY):
                logger.debug("Authorization: Bearer %s", TEST_OPENAI_KEY)
        self.assertNotIn(TEST_OPENAI_KEY, " ".join(logs.output))
        self.assertIn("REDACTED", " ".join(logs.output))
