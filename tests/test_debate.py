"""합성 시장/지표를 실제 StateGraph에 전달한다. 유료 API·실제 거래·외부 추적 없음."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier, Event
import unittest
from unittest.mock import Mock, patch

import pandas as pd
from pandas.testing import assert_frame_equal
from pydantic import ValidationError

from agents.bull_agent import BullAgent
from agents.bear_agent import BearAgent
from graph.debate_nodes import evaluate_debate, aggregate_debate
from graph.debate_state import DEBATE_NODES
from graph.debate_validation import DebateValidationError, validate_position, validate_responses, validate_evidence
from graph.debate_workflow import build_debate_workflow, get_debate_workflow
from graph.runner import run_workflow
from indicators.technical import add_technical_indicators
from llm.budget import UsageLedger, AIOptions
from llm.config import AIError, OpenAIConfig
from test_ai_analysis import NOW, TEST_OPENAI_KEY
from test_workflow import market, macro_frames, statuses


def mixed_market():
    # 알려진 합성 지표: 평균 차이는 양수, RSI/MACD Histogram은 음수다.
    data = market()
    data.loc[data.index[-1], "rsi_14"] = 45.
    data.loc[data.index[-1], "macd_histogram"] = -1.
    data.loc[data.index[-1], "macd_signal"] = data.iloc[-1].macd+1.
    return data


def debate(data=None, **kwargs):
    return run_workflow(mixed_market() if data is None else data, "BTC/USDT", "1h", now=NOW,
                        requested=True, debate_enabled=True, **kwargs)


class DebateGraphTests(unittest.TestCase):
    def test_actual_stategraph_compile_structure_and_no_loop(self):
        diagram = get_debate_workflow().get_graph().draw_mermaid()
        for name in DEBATE_NODES:
            self.assertIn(name, diagram)
        for edge in ("bull_agent --> debate_round", "bear_agent --> debate_round", "debate_evaluator --> research_aggregator"):
            self.assertIn(edge, diagram)
        self.assertNotIn("debate_round --> bull_agent", diagram)

    def test_full_graph_rounds_and_real_claim_inputs(self):
        result = debate(macro_frames=macro_frames())
        state = result.state
        self.assertEqual(state["execution_status"], "COMPLETED")
        self.assertEqual(state["debate_status"], "COMPLETED")
        self.assertEqual(state["debate_round"], 2)
        self.assertEqual(len(state["node_execution_logs"]), 30)
        self.assertEqual(set(statuses(result).values()), {"COMPLETED"})
        self.assertEqual(state["final_report"].debate_mode, "Mock Debate")
        self.assertEqual(state["final_report"].final_signal, "NEUTRAL")
        self.assertEqual(state["final_report"].debate_evaluation, state["debate_evaluation"])
        starts = {log.node: log.started_at_utc for log in state["node_execution_logs"] if log.status == "RUNNING"}
        ends = {log.node: log.ended_at_utc for log in state["node_execution_logs"] if log.status == "COMPLETED"}
        self.assertGreaterEqual(starts["evidence_preparation"], max(ends["technical_agent"], ends["macro_agent"]))
        self.assertGreaterEqual(starts["debate_round"], max(ends["bull_agent"], ends["bear_agent"]))
        self.assertGreaterEqual(starts["debate_evaluator"], ends["debate_round"])

    def test_bull_and_bear_proposals_are_parallel(self):
        barrier = Barrier(2)
        class Bull(BullAgent):
            def propose(self, pool):
                barrier.wait(timeout=5)
                return super().propose(pool)
        class Bear(BearAgent):
            def propose(self, pool):
                barrier.wait(timeout=5)
                return super().propose(pool)
        result = debate(workflow=build_debate_workflow(bull_agent=Bull(), bear_agent=Bear()))
        self.assertEqual(result.state["execution_status"], "COMPLETED")

    def test_round_two_uses_opponent_messages_and_both_evidence_sets(self):
        state = debate().state
        initial = {item.claim_id: item for item in state["debate_history"] if item.round == 1 and item.message_type == "CLAIM"}
        replies = [item for item in state["debate_history"] if item.round == 2 and item.message_type == "REBUTTAL"]
        self.assertTrue(replies)
        for item in replies:
            target = initial[item.target_claim_id]
            self.assertNotEqual(item.speaker, target.speaker)
            self.assertIn(target.claim_id, item.content)
            self.assertTrue(set(target.evidence_ids).issubset(item.evidence_ids))
            self.assertEqual(len(item.evidence_ids), 2)
            self.assertNotEqual(item.content, target.content)
            self.assertEqual(item.validation_status, "VALID")
        self.assertEqual(set(state["debate_evaluation"].addressed_claim_ids), set(initial))

    def test_no_directional_facts_does_not_force_claims(self):
        raw = market()
        raw.loc[:, ["open", "high", "low", "close"]] = 100.
        data = add_technical_indicators(raw)
        state = debate(data).state
        self.assertEqual(state["execution_status"], "COMPLETED")
        self.assertFalse(state["debate_evaluation"].valid_bull_claims)
        self.assertFalse(state["debate_evaluation"].valid_bear_claims)
        self.assertTrue(all(item.message_type == "INSUFFICIENT_EVIDENCE" for item in state["debate_history"]))

    def test_opposing_observations_acknowledged_and_concession_when_no_counter(self):
        state = debate(market()).state
        self.assertFalse(state["bear_result"].supporting_evidence_ids)
        self.assertTrue(state["bear_result"].counterarguments)
        self.assertEqual(state["bear_result"].messages[0].message_type, "INSUFFICIENT_EVIDENCE")
        self.assertTrue(any(item.speaker == "BEAR" and item.message_type == "CONCESSION" for item in state["debate_history"]))
        self.assertFalse(any(item.speaker == "BEAR" and item.message_type == "CLAIM" for item in state["debate_history"]))

    def test_round_one_stops_without_rebuttals(self):
        result = debate(debate_rounds=1)
        self.assertEqual(result.state["debate_round"], 1)
        self.assertEqual({item.round for item in result.state["debate_history"]}, {1})
        self.assertEqual(statuses(result)["debate_round"], "SKIPPED")
        self.assertFalse(result.state["debate_evaluation"].addressed_claim_ids)

    def test_invalid_rounds_and_modes_block_before_api(self):
        for value in (0, 3, -1, True, "2", 1.5):
            with self.subTest(value=value), self.assertRaises(AIError):
                debate(debate_rounds=value)
        with patch("openai.OpenAI") as sdk:
            with self.assertRaises(AIError):
                debate(mode="LLM Workflow", config=OpenAIConfig(api_key=TEST_OPENAI_KEY), options=AIOptions(mode="live"),
                       ledger=UsageLedger(), cost_approved=True)
        sdk.assert_not_called()
        with self.assertRaises(AIError):
            run_workflow(market(), "BTC/USDT", "1h", now=NOW, debate_enabled=True)

    def test_insufficient_technical_skips_debate_without_fake_transcript(self):
        result = debate(market(short=True))
        self.assertEqual(result.state["debate_status"], "SKIPPED")
        self.assertEqual(result.state["debate_evaluation"].evaluation_status, "INSUFFICIENT_DATA")
        self.assertFalse(result.state["debate_history"])
        self.assertEqual(result.state["final_report"].final_signal, "INSUFFICIENT_DATA")
        for name in ("bull_agent", "bear_agent", "debate_round"):
            self.assertEqual(statuses(result)[name], "SKIPPED")

    def test_macro_missing_is_not_failed_debate_and_never_directional(self):
        state = debate().state
        self.assertEqual(state["macro_result"].macro_status, "INSUFFICIENT_DATA")
        self.assertEqual(state["debate_status"], "COMPLETED")
        self.assertTrue(any("Macro" in item for item in state["debate_evaluation"].missing_evidence))
        self.assertTrue(state["technical_result"])
        with_macro = debate(macro_frames=macro_frames()).state
        for item in with_macro["debate_history"]:
            self.assertTrue(all(fact.source_type == "TECHNICAL" for fact in item.citations))
        self.assertEqual(state["final_report"].final_signal, with_macro["final_report"].final_signal)

    def test_forged_macro_publication_and_vintage_do_not_enable_direction(self):
        frames = macro_frames()
        for frame in frames.values():
            frame["available_at"] = frame.observation_date
            frame["vintage_date"] = frame.observation_date
        state = debate(macro_frames=frames).state
        self.assertTrue(all(item["release_time"] == item["vintage"] == "unknown" for item in state["macro_metadata"]))
        self.assertEqual(state["debate_evidence"].macro_status, "CONTEXT_ONLY")
        self.assertFalse(any(fact.source_type == "MACRO" for item in state["debate_history"] for fact in item.citations))

    def test_bad_macro_is_partial_failure_and_preserves_both_positions(self):
        frames = macro_frames()
        frames["FEDFUNDS"]["units"] = "wrong"
        state = debate(macro_frames=frames).state
        self.assertEqual(state["execution_status"], "PARTIAL")
        self.assertEqual(state["debate_status"], "PARTIAL")
        self.assertIsNotNone(state["bull_result"])
        self.assertIsNotNone(state["bear_result"])

    def test_technical_failure_is_failed_and_macro_preserved(self):
        agent = Mock()
        agent.analyze.side_effect = RuntimeError("sk-private-not-for-logs")
        result = debate(macro_frames=macro_frames(), workflow=build_debate_workflow(technical_agent=agent))
        self.assertEqual(result.state["execution_status"], "FAILED")
        self.assertIsNotNone(result.state["macro_result"])
        self.assertFalse(result.state["debate_history"])
        self.assertIsNone(result.state["final_report"])
        self.assertEqual(statuses(result)["bull_agent"], "SKIPPED")
        self.assertNotIn("sk-private-not-for-logs", str(result))

    def test_one_position_failure_produces_partial_not_success(self):
        bull = Mock()
        bull.propose.side_effect = RuntimeError("private-input")
        state = debate(workflow=build_debate_workflow(bull_agent=bull)).state
        self.assertEqual(state["execution_status"], "PARTIAL")
        self.assertEqual(state["debate_status"], "PARTIAL")
        self.assertIsNone(state["bull_result"])
        self.assertIsNotNone(state["bear_result"])
        self.assertEqual({item.speaker for item in state["debate_history"]}, {"BEAR"})
        self.assertTrue(state["debate_evaluation"].rejected_claims)
        self.assertNotIn("private-input", str(state))

    def test_both_position_failures_are_failed_debate_with_partial_original_analysis(self):
        bull, bear = Mock(), Mock()
        bull.propose.side_effect = RuntimeError("bull")
        bear.propose.side_effect = RuntimeError("bear")
        state = debate(workflow=build_debate_workflow(bull_agent=bull, bear_agent=bear)).state
        self.assertEqual(state["debate_status"], "FAILED")
        self.assertEqual(state["execution_status"], "PARTIAL")
        self.assertEqual(len(state["debate_evaluation"].rejected_claims), 2)
        self.assertEqual(state["final_report"].execution_status, "PARTIAL")

    def test_invalid_bull_evidence_is_rejected_and_not_published(self):
        class Fake(BullAgent):
            def propose(self, pool):
                result = super().propose(pool)
                result.bullish_claims[0].citations[0].metric_value += 1000
                return result
        result = debate(workflow=build_debate_workflow(bull_agent=Fake()))
        self.assertEqual(statuses(result)["bull_agent"], "FAILED")
        self.assertIsNone(result.state["bull_result"])
        self.assertFalse(any(item.speaker == "BULL" for item in result.state["debate_history"]))
        self.assertEqual(result.state["errors"][0].category, "EVIDENCE_VALIDATION")

    def test_round_failure_preserves_other_verified_rebuttals(self):
        class Fake(BullAgent):
            def respond(self, pool, opponent):
                result = super().respond(pool, opponent)
                result[0].target_claim_id = "does-not-exist"
                return result
        result = debate(workflow=build_debate_workflow(bull_agent=Fake()))
        self.assertEqual(result.state["execution_status"], "PARTIAL")
        self.assertEqual(statuses(result)["debate_round"], "FAILED")
        self.assertEqual({item.speaker for item in result.state["debate_history"] if item.round == 2}, {"BEAR"})
        self.assertTrue(result.state["debate_evaluation"].rejected_claims)

    def test_aggregator_failure_keeps_history_but_no_normal_report(self):
        with patch("graph.debate_workflow.aggregate_debate", side_effect=RuntimeError("private")):
            graph = build_debate_workflow()
        result = debate(workflow=graph)
        self.assertEqual(result.state["execution_status"], "FAILED")
        self.assertIsNone(result.state["final_report"])
        self.assertTrue(result.state["debate_history"])
        self.assertEqual(statuses(result)["research_aggregator"], "FAILED")

    def test_evaluator_does_not_count_claims_to_select_a_winner(self):
        state = debate().state
        evaluation = state["debate_evaluation"]
        self.assertNotEqual(len(evaluation.valid_bull_claims), len(evaluation.valid_bear_claims))
        self.assertTrue(evaluation.unresolved_conflicts)
        self.assertEqual(evaluation.suggested_signal, "NEUTRAL")
        self.assertEqual(state["final_report"].final_signal, "NEUTRAL")

    def test_debate_report_preserves_original_technical_macro_evidence(self):
        frames = macro_frames()
        base = run_workflow(mixed_market(), "BTC/USDT", "1h", macro_frames=frames, now=NOW).state
        state = debate(macro_frames=frames).state
        self.assertEqual(state["technical_result"], base["technical_result"])
        self.assertEqual(state["macro_result"], base["macro_result"])
        self.assertEqual(state["aggregation_result"].evidence, base["aggregation_result"].evidence)
        self.assertTrue(state["final_report"].debate_summary)

    def test_debate_disabled_matches_step11_exact_analysis(self):
        args = (mixed_market(), "BTC/USDT", "1h")
        a = run_workflow(*args, now=NOW)
        b = run_workflow(*args, now=NOW, debate_enabled=False, debate_rounds=1)
        ignore = {"run_id", "started_at_utc", "completed_at_utc", "duration_ms"}
        self.assertEqual(a.state["final_report"].model_dump(exclude=ignore), b.state["final_report"].model_dump(exclude=ignore))
        self.assertEqual(a.state["aggregation_result"], b.state["aggregation_result"])
        self.assertNotIn("debate_history", b.state)

    def test_reproducible_transcript_evaluation_and_original_data_unchanged(self):
        data, frames = mixed_market(), macro_frames()
        before, copies = data.copy(deep=True), {key: frame.copy(deep=True) for key, frame in frames.items()}
        a, b = [debate(data, macro_frames=frames).state for _ in range(2)]
        self.assertEqual(a["debate_history"], b["debate_history"])
        self.assertEqual(a["debate_evaluation"], b["debate_evaluation"])
        self.assertNotEqual(a["run_id"], b["run_id"])
        assert_frame_equal(data, before)
        for key in frames:
            assert_frame_equal(frames[key], copies[key])

    def test_shared_compiled_graph_keeps_concurrent_states_independent(self):
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: debate(workflow=get_debate_workflow()), range(2)))
        self.assertEqual(results[0].state["debate_history"], results[1].state["debate_history"])
        self.assertNotEqual(results[0].state["run_id"], results[1].state["run_id"])
        self.assertTrue(all(result.state["execution_status"] == "COMPLETED" for result in results))

    def test_logs_include_all_nodes_utc_duration_and_alias(self):
        result = debate()
        self.assertEqual(result.state["execution_logs"], result.state["node_execution_logs"])
        for node in DEBATE_NODES:
            events = [log for log in result.state["execution_logs"] if log.node == node]
            self.assertEqual([log.status for log in events], ["WAITING", "RUNNING", "COMPLETED"])
            self.assertGreaterEqual(events[-1].duration_ms, 0.)
            self.assertGreaterEqual(events[-1].ended_at_utc, events[-1].started_at_utc)

    def test_cancel_before_input_has_no_claims_and_skipped_nodes(self):
        event = Event()
        event.set()
        result = debate(cancel_event=event)
        self.assertEqual(result.state["debate_status"], "CANCELLED")
        self.assertEqual(result.state["execution_status"], "FAILED")
        self.assertFalse(result.state["debate_history"])
        self.assertEqual(statuses(result)["bull_agent"], "SKIPPED")

    def test_cancel_during_rebuttal_preserves_valid_prefix(self):
        event = Event()
        class Cancel(BullAgent):
            def respond(self, pool, opponent):
                values = super().respond(pool, opponent)
                event.set()
                return values
        result = debate(cancel_event=event, workflow=build_debate_workflow(bull_agent=Cancel()))
        self.assertEqual(result.state["debate_status"], "CANCELLED")
        self.assertEqual(result.state["execution_status"], "FAILED")
        self.assertTrue(any(item.round == 2 and item.speaker == "BULL" for item in result.state["debate_history"]))
        self.assertIsNone(result.state["final_report"])
        self.assertEqual(statuses(result)["debate_evaluator"], "SKIPPED")
        self.assertIsNotNone(result.state["technical_result"])

    def test_duplicate_execution_is_blocked_without_budget_charge(self):
        ledger = UsageLedger()
        a = debate(ledger=ledger)
        b = debate(ledger=ledger)
        self.assertEqual(a.state["execution_status"], "COMPLETED")
        self.assertEqual(b.state["execution_status"], "FAILED")
        self.assertEqual(b.state["errors"][0].category, "DUPLICATE_REQUEST")
        self.assertFalse(b.state["debate_history"])
        self.assertEqual(ledger.request_count, 0)
        self.assertEqual(ledger.committed_usd, 0.)
        self.assertEqual(debate(ledger=ledger, debate_rounds=1).state["execution_status"], "COMPLETED")

    def test_mock_debate_never_calls_openai_network_or_remote_traces(self):
        with patch("openai.OpenAI") as sdk, patch("httpx.Client.send") as http, \
                patch("requests.sessions.Session.send") as requests, patch("langsmith.client.Client.create_run") as trace:
            result = debate(macro_frames=macro_frames(), config=OpenAIConfig(api_key=TEST_OPENAI_KEY), cost_approved=True)
        self.assertEqual(result.state["execution_status"], "COMPLETED")
        for spy in (sdk, http, requests, trace):
            spy.assert_not_called()


class DebateValidationTests(unittest.TestCase):
    def setUp(self):
        self.state = debate().state
        self.pool = self.state["debate_evidence"]

    def test_wrong_id_value_unit_source_timestamp_and_content_rejected(self):
        changes = (("reference_id", "unknown"), ("metric_value", 999999.), ("unit", "invented unit"),
                   ("source_type", "MACRO"), ("observation_time", "2027-01-01T00:00:00Z"))
        for field, value in changes:
            result = deepcopy(self.state["bull_result"])
            setattr(result.bullish_claims[0].citations[0], field, value)
            with self.subTest(field=field), self.assertRaises(DebateValidationError):
                validate_position(result, self.pool, "BULL")
        result = deepcopy(self.state["bull_result"])
        result.bullish_claims[0].content = "가격이 내일 반드시 상승합니다."
        with self.assertRaises(DebateValidationError):
            validate_position(result, self.pool, "BULL")

    def test_missing_evidence_and_duplicate_or_contradictory_claims_rejected(self):
        for kind in ("id", "duplicate", "direction", "opposing"):
            value = deepcopy(self.state["bull_result"])
            if kind == "id":
                value.supporting_evidence_ids = ["unknown"]
            elif kind == "duplicate":
                value.bullish_claims.append(deepcopy(value.bullish_claims[0]))
            elif kind == "direction":
                value.bullish_claims[0].speaker = "BEAR"
            else:
                value.counterarguments = []
            with self.subTest(kind=kind), self.assertRaises((DebateValidationError, ValidationError)):
                validate_position(value, self.pool, "BULL")

    def test_rebuttal_target_and_evidence_must_match_actual_opponent(self):
        opponent = self.state["bear_result"]
        replies = BullAgent().respond(self.pool, opponent)
        self.assertTrue(validate_responses(replies, self.pool, "BULL", opponent))
        for field, value in (("target_claim_id", "missing"), ("evidence_ids", ["unknown"]), ("content", "최초 주장 복사")):
            changed = deepcopy(replies)
            setattr(changed[0], field, value)
            with self.subTest(field=field), self.assertRaises(DebateValidationError):
                validate_responses(changed, self.pool, "BULL", opponent)

    def test_future_observation_or_macro_direction_is_invalid(self):
        pool = deepcopy(self.pool)
        pool.facts[0].observation_time = NOW.isoformat()
        with self.assertRaises(DebateValidationError):
            validate_evidence(pool)
        pool = deepcopy(self.pool)
        pool.facts[0].source_type = "MACRO"
        pool.facts[0].role = "bullish"
        with self.assertRaises(DebateValidationError):
            validate_evidence(pool)

    def test_evaluator_rejects_modified_transcript(self):
        state = deepcopy(self.state)
        item = next(item for item in state["debate_history"] if item.speaker == "BULL" and item.round == 1)
        item.citations[0].metric_value += 10
        result = evaluate_debate(state)
        self.assertTrue(result.rejected_claims)
        self.assertFalse(result.valid_bull_claims)
        self.assertEqual(result.evaluation_status, "PARTIAL")

    def test_aggregator_revalidates_evidence_and_evaluation(self):
        state = deepcopy(self.state)
        state["debate_evaluation"].debate_summary = "수익 보장"
        with self.assertRaises(DebateValidationError):
            aggregate_debate(state, None)
        state = deepcopy(self.state)
        state["debate_evidence"].facts[0].unit = "wrong"
        with self.assertRaises(DebateValidationError):
            aggregate_debate(state, None)
