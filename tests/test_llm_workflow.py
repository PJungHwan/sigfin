"""실제 SDK/StateGraph + 모의 HTTP. 어떤 테스트도 유료 OpenAI 요청을 보내지 않는다."""

from concurrent.futures import ThreadPoolExecutor
import json
from threading import Barrier
import unittest
from unittest.mock import patch

import httpx
from pandas.testing import assert_frame_equal
from pydantic import ValidationError

from config.llm_settings import DEFAULT_OPENAI_MODEL
from graph.nodes import aggregate
from graph.runner import run_workflow
from llm.agent_contracts import (TechnicalLLMOutput, MacroLLMOutput, validate_technical_output,
                                validate_macro_output, TECH_PROMPT, MACRO_PROMPT)
from llm.budget import UsageLedger, AIOptions, estimate_cost
from llm.config import AIError, OpenAIConfig
from test_ai_analysis import TEST_OPENAI_KEY, NOW
from test_ai_client import REAL_OPENAI, api_response
from test_workflow import market, macro_frames, statuses


def output_for(content):
    facts = content["facts"]
    evidence = [{key: value for key, value in fact.items() if key not in ("unit", "role")} for fact in facts]
    ids = [fact["reference_id"] for fact in facts]
    if content["role"] == "technical_agent":
        return {"signal": "NEUTRAL", "technical_summary": content["summaries"]["NEUTRAL"],
                "bullish_factors": [fact["explanation"] for fact in facts if fact["role"] == "bullish"],
                "bearish_factors": [fact["explanation"] for fact in facts if fact["role"] == "bearish"],
                "evidence_ids": ids, "evidence": evidence,
                "risk_factors": content["risk_factors"], "limitations": content["limitations"]}
    return {key: content[key] for key in ("macro_summary", "macro_risk_factors", "macro_limitations", "macro_status")} | {
        "macro_evidence_ids": ids, "macro_evidence": evidence}


class LLMWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.config = OpenAIConfig(api_key=TEST_OPENAI_KEY)
        self.options = AIOptions(mode="live")
        self.ledger = UsageLedger()
        self.requests = []

    def factory(self, handler):
        def create(**kwargs):
            self.assertEqual(kwargs["max_retries"], 0)
            return REAL_OPENAI(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False))
        return create

    def handler(self, request):
        self.assertEqual(str(request.url), "https://api.openai.com/v1/responses")
        payload = json.loads(request.content)
        content = json.loads(payload["input"][0]["content"])
        self.requests.append((payload, content))
        return httpx.Response(200, json=api_response(json.dumps(output_for(content), ensure_ascii=False)))

    def run_graph(self, *, frames=None, data=None, handler=None, **kwargs):
        with patch("llm.client.openai.OpenAI", side_effect=self.factory(handler or self.handler)):
            return run_workflow(market() if data is None else data, "BTC/USDT", "1h", macro_frames=frames,
                now=NOW, mode="LLM Workflow", config=kwargs.pop("config", self.config),
                options=kwargs.pop("options", self.options), ledger=kwargs.pop("ledger", self.ledger),
                requested=kwargs.pop("requested", True), cost_approved=kwargs.pop("cost_approved", True), **kwargs)

    def test_two_role_sdk_parsing_and_graph_join(self):
        result = self.run_graph(frames=macro_frames())
        state = result.state
        self.assertEqual(state["execution_status"], "COMPLETED")
        self.assertEqual(state["final_report"].execution_mode, "LLM Workflow")
        self.assertEqual(len(self.requests), 2)
        self.assertEqual({payload["instructions"] for payload, _ in self.requests}, {TECH_PROMPT, MACRO_PROMPT})
        self.assertEqual({payload["text"]["format"]["name"] for payload, _ in self.requests}, {"TechnicalLLMOutput", "MacroLLMOutput"})
        self.assertEqual(len([log for log in state["node_execution_logs"] if log.node == "research_aggregator" and log.status == "COMPLETED"]), 1)
        for payload, content in self.requests:
            self.assertFalse(payload["store"])
            self.assertTrue(payload["text"]["format"]["strict"])
            self.assertNotIn(TEST_OPENAI_KEY, json.dumps(payload))
            self.assertNotIn("timestamp", content)
            if content["role"] == "macro_agent":
                self.assertTrue(all("missing_values" in item and "observation_count" in item for item in content["metadata"]))
        for role in ("technical", "macro"):
            detail = state[role+"_execution"]
            self.assertTrue(detail.api_called)
            self.assertEqual(detail.validation_status, "VALIDATED")
            self.assertEqual(detail.model, DEFAULT_OPENAI_MODEL)
            self.assertEqual(detail.usage.total_tokens, 2000)
            self.assertAlmostEqual(detail.estimated_cost_usd, estimate_cost(DEFAULT_OPENAI_MODEL, 1200, 800))

    def test_real_parallel_http_branches_reserve_before_sending(self):
        barrier = Barrier(2)
        def handler(request):
            self.assertEqual(self.ledger.request_count, 2)
            barrier.wait(timeout=10)
            return self.handler(request)
        result = self.run_graph(frames=macro_frames(), handler=handler)
        self.assertEqual(result.state["execution_status"], "COMPLETED")
        self.assertEqual(len(self.requests), 2)
        self.assertTrue(all(record["status"] == "VALIDATED" for record in self.ledger.records))

    def test_no_macro_skips_paid_call_but_keeps_technical(self):
        result = self.run_graph()
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(result.state["macro_result"].macro_status, "INSUFFICIENT_DATA")
        self.assertFalse(result.state["macro_execution"].api_called)
        self.assertEqual(result.state["macro_execution"].estimated_cost_usd, 0.)
        self.assertEqual(result.state["execution_status"], "COMPLETED")

    def test_short_technical_skips_call_and_macro_can_run_independently(self):
        result = self.run_graph(data=market(short=True), frames=macro_frames())
        self.assertEqual(len(self.requests), 1)
        self.assertFalse(result.state["technical_execution"].api_called)
        self.assertEqual(result.state["technical_result"].technical_signal, "INSUFFICIENT_DATA")
        self.assertTrue(result.state["macro_execution"].api_called)
        self.assertEqual(result.state["final_report"].final_signal, "INSUFFICIENT_DATA")

    def test_both_insufficient_are_local_no_sdk(self):
        with patch("agents.llm_agents.request_structured") as request:
            result = self.run_graph(data=market(short=True))
        request.assert_not_called()
        self.assertFalse(self.ledger.records)
        self.assertEqual(result.state["execution_status"], "COMPLETED")
        self.assertEqual(result.state["final_report"].final_signal, "INSUFFICIENT_DATA")

    def test_invalid_market_has_no_reservation_or_api(self):
        data = market().drop(columns="rsi_14")
        result = self.run_graph(data=data)
        self.assertEqual(result.state["execution_status"], "FAILED")
        self.assertFalse(self.ledger.records)
        self.assertFalse(self.requests)

    def test_gates_requested_approval_key_model_price(self):
        cases = [({"requested": False}, "NOT_REQUESTED"), ({"requested": "true"}, "NOT_REQUESTED"),
                 ({"cost_approved": False}, "COST_APPROVAL_REQUIRED"), ({"cost_approved": "true"}, "COST_APPROVAL_REQUIRED"),
                 ({"config": OpenAIConfig()}, "KEY_MISSING"),
                 ({"config": OpenAIConfig("unknown", TEST_OPENAI_KEY)}, "MODEL_UNVERIFIED"),
                 ({"options": AIOptions(mode="live", retries=1)}, "CONFIGURATION")]
        for kwargs, category in cases:
            with self.subTest(category=category), self.assertRaises(AIError) as caught:
                self.run_graph(**kwargs)
            self.assertEqual(caught.exception.category, category)
        with patch("agents.llm_agents.price_for", return_value=None), self.assertRaises(AIError) as caught:
            self.run_graph()
        self.assertEqual(caught.exception.category, "PRICE_UNKNOWN")
        self.assertFalse(self.requests)
        self.assertFalse(self.ledger.records)

    def test_duplicate_whole_workflow_blocked_without_more_calls(self):
        self.assertEqual(self.run_graph().state["execution_status"], "COMPLETED")
        result = self.run_graph()
        self.assertEqual(result.state["execution_status"], "FAILED")
        self.assertEqual(result.state["errors"][0].category, "DUPLICATE_REQUEST")
        self.assertEqual(len(self.requests), 1)

    def test_batch_request_or_cost_limit_is_atomic(self):
        for options, category in ((AIOptions(mode="live", max_requests=1), "REQUEST_LIMIT"),
                                  (AIOptions(mode="live", cost_limit_usd=.001), "COST_LIMIT")):
            with self.subTest(category=category):
                ledger = UsageLedger()
                result = self.run_graph(frames=macro_frames(), options=options, ledger=ledger)
                self.assertEqual(result.state["errors"][0].category, category)
                self.assertFalse(ledger.records)
        self.assertFalse(self.requests)

    def test_concurrent_whole_runs_cannot_overspend_or_duplicate(self):
        barrier = Barrier(2)
        options = AIOptions(mode="live", max_requests=2, cost_limit_usd=.05)
        def reserve(key):
            barrier.wait(timeout=5)
            try:
                return self.ledger.reserve_many(options, self.config.model, [.02, .02], NOW.isoformat(), key)
            except AIError as error:
                return error.category
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(reserve, ["same", "same"]))
        self.assertEqual(sum(isinstance(result, list) for result in results), 1)
        self.assertIn("DUPLICATE_REQUEST", results)
        self.assertEqual(self.ledger.request_count, 2)
        self.assertAlmostEqual(self.ledger.committed_usd, .04)

    def test_parallel_different_keys_enforce_total_cost(self):
        options = AIOptions(mode="live", max_requests=5, cost_limit_usd=.05)
        def reserve(key):
            try:
                return self.ledger.reserve_many(options, self.config.model, [.03], NOW.isoformat(), key)
            except AIError as error:
                return error.category
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(reserve, ["a", "b"]))
        self.assertIn("COST_LIMIT", results)
        self.assertAlmostEqual(self.ledger.committed_usd, .03)

    def test_unattempted_reservations_can_be_released_but_attempted_cannot(self):
        records = self.ledger.reserve_many(self.options, self.config.model, [.02, .03], NOW.isoformat(), "release")
        records[0]["status"] = "ATTEMPTED"
        self.ledger.cancel_unattempted(records)
        self.assertEqual(self.ledger.request_count, 1)
        self.assertAlmostEqual(self.ledger.committed_usd, .02)
        self.assertEqual(records[1]["status"], "CANCELLED_NOT_SENT")

    def test_invalid_cost_batch_never_partially_reserves(self):
        for value in (-.01, float("nan"), float("inf"), True):
            with self.subTest(value=value), self.assertRaises(AIError):
                self.ledger.reserve_many(self.options, self.config.model, [.02, value], NOW.isoformat(), "bad")
            self.assertFalse(self.ledger.records)

    def test_graph_abort_releases_only_provably_unsent_reservations(self):
        from langgraph.graph import StateGraph, START, END
        from graph.nodes import tracked, validate_input
        from graph.state import WorkflowState, WorkflowContext
        builder = StateGraph(WorkflowState, context_schema=WorkflowContext)
        builder.add_node("input_validator", tracked("input_validator", validate_input))
        builder.add_edge(START, "input_validator")
        builder.add_edge("input_validator", END)
        result = self.run_graph(frames=macro_frames(), workflow=builder.compile())
        self.assertEqual(result.state["execution_status"], "FAILED")
        self.assertTrue(all(record["status"] == "CANCELLED_NOT_SENT" for record in self.ledger.records))
        self.assertEqual(len(self.ledger.records), 2)
        self.assertEqual(self.ledger.request_count, 0)
        self.assertEqual(self.ledger.committed_usd, 0.)
        self.assertFalse(self.requests)

    def test_step9_and_workflow_really_share_one_budget(self):
        from llm.client import ClientResult
        from llm.schemas import TokenUsage
        from services.ai_analysis_service import analyze_market, mock_report
        from test_ai_analysis import market_snapshot
        snapshot = market_snapshot()
        options = AIOptions(mode="live", max_requests=2)
        response = ClientResult(mock_report(snapshot, NOW.isoformat()), TokenUsage(input_tokens=10, output_tokens=10, total_tokens=20), self.config.model)
        with patch("services.ai_analysis_service.request_analysis", return_value=response):
            analyze_market(snapshot, self.config, options, self.ledger, requested=True, cost_approved=True, now=NOW)
        result = self.run_graph(frames=macro_frames(), options=options)
        self.assertEqual(result.state["errors"][0].category, "REQUEST_LIMIT")
        self.assertFalse(self.requests)
        self.assertEqual(self.ledger.request_count, 1)

    def test_failure_retains_known_usage_and_partial_macro_failure(self):
        def handler(request):
            content = json.loads(json.loads(request.content)["input"][0]["content"])
            if content["role"] == "macro_agent":
                bad = output_for(content)
                bad["macro_evidence"][0]["metric_value"] += 1
                return httpx.Response(200, json=api_response(json.dumps(bad)))
            return self.handler(request)
        result = self.run_graph(frames=macro_frames(), handler=handler)
        self.assertEqual(result.state["execution_status"], "PARTIAL")
        self.assertIsNotNone(result.state["technical_result"])
        self.assertIsNone(result.state["macro_result"])
        self.assertEqual(result.state["macro_execution"].error_type, "EVIDENCE_VALIDATION")
        self.assertEqual(result.state["macro_execution"].usage.total_tokens, 2000)
        self.assertEqual(result.state["final_report"].execution_status, "PARTIAL")

    def test_technical_failure_is_failed_and_macro_survives(self):
        def handler(request):
            content = json.loads(json.loads(request.content)["input"][0]["content"])
            if content["role"] == "technical_agent":
                return httpx.Response(200, json=api_response("{}"))
            return self.handler(request)
        result = self.run_graph(frames=macro_frames(), handler=handler)
        self.assertEqual(result.state["execution_status"], "FAILED")
        self.assertIsNone(result.state["final_report"])
        self.assertIsNotNone(result.state["macro_result"])
        self.assertEqual(result.state["technical_execution"].error_type, "RESPONSE_VALIDATION")

    def test_auth_rate_timeout_are_not_retried_and_reservation_retained(self):
        for status, category in ((401, "AUTHENTICATION"), (429, "RATE_LIMIT"), (0, "TIMEOUT")):
            calls = []
            def handler(request):
                calls.append(1)
                if status == 0:
                    raise httpx.ReadTimeout(TEST_OPENAI_KEY, request=request)
                return httpx.Response(status, json={"error": {"message": TEST_OPENAI_KEY}})
            with self.subTest(category=category):
                ledger = UsageLedger()
                result = self.run_graph(handler=handler, ledger=ledger)
                self.assertEqual(result.state["errors"][0].category, category)
                self.assertEqual(len(calls), 1)
                self.assertGreater(ledger.committed_usd, 0.)
                self.assertIsNone(ledger.records[0]["usage"])
                self.assertNotIn(TEST_OPENAI_KEY, str(result))

    def test_bad_macro_input_failure_keeps_technical_and_cost_one_call(self):
        frames = macro_frames()
        frames["FEDFUNDS"]["units"] = "wrong unit"
        result = self.run_graph(frames=frames)
        self.assertEqual(result.state["execution_status"], "PARTIAL")
        self.assertEqual(len(self.requests), 1)
        self.assertFalse(result.state["macro_execution"].api_called)

    def test_macro_cannot_become_directional_even_with_untrusted_vintage(self):
        frames = macro_frames()
        for frame in frames.values():
            frame["available_at"] = frame.observation_date
            frame["vintage_date"] = frame.observation_date
        result = self.run_graph(frames=frames)
        self.assertEqual(result.state["macro_result"].macro_status, "CONTEXT_ONLY")
        self.assertTrue(all(item["release_time"] == item["vintage"] == "unknown" for item in result.state["macro_metadata"]))
        for _, content in self.requests:
            if content["role"] == "macro_agent":
                self.assertTrue(all(item["vintage"] == "unknown" for item in content["metadata"]))

    def test_aggregator_revalidates_llm_result_and_original_data_unchanged(self):
        data, frames = market(), macro_frames()
        before = data.copy(deep=True)
        result = self.run_graph(data=data, frames=frames)
        assert_frame_equal(data, before)
        state = dict(result.state)
        state["technical_result"].evidence[0].metric_value += 1
        with self.assertRaises(AIError):
            aggregate(state, None)

    def test_mock_workflow_does_not_use_injected_llm_options(self):
        with patch("llm.client.openai.OpenAI") as sdk:
            result = run_workflow(market(), "BTC/USDT", "1h", now=NOW, mode="Mock Workflow", config=self.config,
                                  options=self.options, ledger=self.ledger, requested=True, cost_approved=True)
        sdk.assert_not_called()
        self.assertFalse(self.ledger.records)
        self.assertFalse(result.state["technical_execution"].api_called)
        self.assertEqual(result.state["technical_execution"].execution_mode, "Mock Workflow")


class RoleEvidenceTests(unittest.TestCase):
    def setUp(self):
        from services.ai_inputs import build_market_snapshot, summarize_macro_frames
        from llm.agent_contracts import agent_payload
        self.snapshot = build_market_snapshot(market(), "BTC/USDT", "1h", now=NOW)
        self.facts, self.metadata = summarize_macro_frames(macro_frames(), as_of=self.snapshot.data_as_of_utc, now=NOW)
        self.tech = output_for(json.loads(agent_payload("technical_agent", self.snapshot)))
        self.macro = output_for(json.loads(agent_payload("macro_agent", facts=self.facts, metadata=self.metadata)))

    def test_schema_and_grounding_are_separate(self):
        self.assertEqual(validate_technical_output(TechnicalLLMOutput.model_validate(self.tech), self.snapshot).signal, "NEUTRAL")
        self.assertEqual(validate_macro_output(MacroLLMOutput.model_validate(self.macro), self.facts).macro_status, "CONTEXT_ONLY")
        value = dict(self.tech, evidence_ids=["invented"])
        TechnicalLLMOutput.model_validate(value)
        with self.assertRaises(AIError):
            validate_technical_output(value, self.snapshot)
        with self.assertRaises(ValidationError):
            TechnicalLLMOutput.model_validate({"signal": "NEUTRAL"})

    def test_numeric_time_reference_source_and_explanation_fabrication_rejected(self):
        for field, value in (("metric_value", 999999.), ("observation_time", "2027-01-01T00:00:00Z"),
                             ("source_type", "MACRO"), ("explanation", "새 전쟁으로 폭락했습니다.")):
            payload = json.loads(json.dumps(self.tech))
            payload["evidence"][0][field] = value
            with self.subTest(field=field), self.assertRaises(AIError):
                validate_technical_output(payload, self.snapshot)

    def test_arbitrary_narrative_or_missing_opposing_factors_rejected(self):
        for update in ({"technical_summary": "가격이 999999 USDT에 도달합니다."}, {"bullish_factors": ["매수 주문을 실행하세요."]},
                       {"risk_factors": []}, {"limitations": []}):
            with self.subTest(update=update), self.assertRaises(AIError):
                validate_technical_output(dict(self.tech, **update), self.snapshot)
        for field in ("bullish_factors", "bearish_factors"):
            if self.tech[field]:
                with self.assertRaises(AIError):
                    validate_technical_output(dict(self.tech, **{field: []}), self.snapshot)

    def test_unsupported_direction_is_rejected_not_corrected(self):
        from llm.agent_contracts import technical_catalog
        allowed = technical_catalog(self.snapshot)["allowed_signals"]
        direction = "BEARISH" if "BEARISH" not in allowed else "BULLISH"
        from llm.prompts import SUMMARIES
        with self.assertRaises(AIError):
            validate_technical_output(dict(self.tech, signal=direction, technical_summary=SUMMARIES[direction]), self.snapshot)

    def test_macro_direction_wrong_number_date_and_publication_claim_rejected(self):
        for update in ({"macro_status": "BULLISH"}, {"macro_summary": "관측일에 발표되어 BTC 상승을 보장합니다."},
                       {"macro_evidence_ids": ["technical:close"]}):
            with self.subTest(update=update), self.assertRaises((AIError, ValidationError)):
                validate_macro_output(dict(self.macro, **update), self.facts)
        for field, value in (("metric_value", 0.), ("observation_time", "2027-01-01")):
            payload = json.loads(json.dumps(self.macro))
            payload["macro_evidence"][0][field] = value
            with self.subTest(field=field), self.assertRaises(AIError):
                validate_macro_output(payload, self.facts)
