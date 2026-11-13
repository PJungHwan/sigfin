"""실제 LangGraph에 합성 데이터/규칙 노드를 실행한다. 금융 API·LLM 호출 없음."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
import json
import os
from threading import Barrier
import unittest
from unittest.mock import patch, Mock

import pandas as pd
from pandas.testing import assert_frame_equal

from agents.research import RuleTechnicalAgent, RuleMacroAgent, technical_signal
from graph.nodes import aggregate
from graph.runner import run_workflow
from graph.state import NODES, WorkflowContext
from graph.validation import WorkflowValidationError, validate_technical, validate_macro
from graph.workflow import build_workflow, get_workflow
from indicators.technical import add_technical_indicators
from llm.schemas import Fact
from services.ai_inputs import build_market_snapshot, snapshot_fingerprint
from test_app import full_fixture_frame, fixture_frame
from test_ai_analysis import NOW
from test_macro_ui import mock_series
from test_fred import TEST_KEY


def market(short=False):
    return add_technical_indicators(fixture_frame() if short else full_fixture_frame())


def macro_frames():
    frames = {series: mock_series(series, "2025-01-01", "2026-10-14", api_key=TEST_KEY)
              for series in ("CPIAUCSL", "FEDFUNDS", "UNRATE", "PAYEMS", "DGS10")}
    for frame in frames.values():
        frame.attrs["fetched_at"] = NOW
    return frames


def statuses(result):
    return {log.node: log.status for log in result.state["node_execution_logs"]}


class WorkflowGraphTests(unittest.TestCase):
    def test_compile_and_diagram_have_start_branches_join_formatter_end(self):
        graph = build_workflow()
        diagram = graph.get_graph().draw_mermaid()
        for name in (*NODES, "__start__", "__end__"):
            self.assertIn(name, diagram)
        self.assertIn("technical_agent --> research_aggregator", diagram)
        self.assertIn("macro_agent --> research_aggregator", diagram)

    def test_end_to_end_real_graph_receives_both_results_and_ends_once(self):
        result = run_workflow(market(), "BTC/USDT", "1h", macro_frames=macro_frames(), now=NOW)
        self.assertEqual(result.state["execution_status"], "COMPLETED")
        self.assertFalse(result.state["errors"])
        self.assertEqual(set(statuses(result).values()), {"COMPLETED"})
        report = result.state["final_report"]
        self.assertEqual(report.execution_mode, "Mock Workflow")
        self.assertEqual(report.data_as_of_utc, "2026-10-14T12:00:00+00:00")
        self.assertEqual(len(result.state["macro_input"]), 5)
        self.assertTrue(any(item.source_type == "MACRO" for item in report.evidence))
        terminal = [log for log in result.state["node_execution_logs"] if log.node == "research_aggregator" and log.status == "COMPLETED"]
        self.assertEqual(len(terminal), 1)
        starts = {log.node: log.started_at_utc for log in result.state["node_execution_logs"] if log.status == "RUNNING"}
        ends = {log.node: log.ended_at_utc for log in result.state["node_execution_logs"] if log.status == "COMPLETED"}
        self.assertGreaterEqual(starts["research_aggregator"], max(ends["technical_agent"], ends["macro_agent"]))

    def test_parallel_branches_reach_a_barrier_without_state_collision(self):
        barrier = Barrier(2)
        class Tech:
            def analyze(self, snapshot):
                barrier.wait(timeout=5)
                return RuleTechnicalAgent().analyze(snapshot)
        class Macro:
            def analyze(self, facts, metadata):
                barrier.wait(timeout=5)
                return RuleMacroAgent().analyze(facts, metadata)
        graph = build_workflow(technical_agent=Tech(), macro_agent=Macro())
        result = run_workflow(market(), "BTC/USDT", "1h", workflow=graph, now=NOW)
        self.assertEqual(result.state["execution_status"], "COMPLETED")
        self.assertIsNotNone(result.state["technical_result"])
        self.assertIsNotNone(result.state["macro_result"])
        self.assertEqual(len(result.state["node_execution_logs"]), 15)

    def test_shared_compiled_graph_keeps_concurrent_run_ids_and_state_independent(self):
        data = market()
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(run_workflow, data, symbol, "1h", now=NOW) for symbol in ("BTC/USDT", "ETH/USDT")]
            results = [future.result(timeout=15) for future in futures]
        self.assertNotEqual(results[0].state["run_id"], results[1].state["run_id"])
        self.assertEqual([result.state["final_report"].symbol for result in results], ["BTC/USDT", "ETH/USDT"])
        self.assertTrue(all(len(result.state["node_execution_logs"]) == 15 for result in results))

    def test_schema_invalid_symbol_timeframe_missing_columns_and_no_data_fail_at_validator(self):
        for data, symbol, timeframe in ((market(), "BAD", "1h"), (market(), "BTC/USDT", "bad"),
                                        (market().drop(columns="rsi_14"), "BTC/USDT", "1h"),
                                        (None, "BTC/USDT", "1h"), (market().iloc[:0], "BTC/USDT", "1h")):
            with self.subTest(symbol=symbol, timeframe=timeframe):
                result = run_workflow(data, symbol, timeframe, now=NOW)
                self.assertEqual(result.state["execution_status"], "FAILED")
                self.assertIsNone(result.state["final_report"])
                self.assertEqual(statuses(result)["input_validator"], "FAILED")
                self.assertEqual(statuses(result)["research_aggregator"], "SKIPPED")
                self.assertEqual(result.state["errors"][0].category, "INPUT_VALIDATION")

    def test_future_open_bar_invalid_numbers_and_fetch_time_fail(self):
        for kind in ("future", "price", "time", "indicator"):
            data = market()
            if kind == "future":
                data.loc[data.index[-1], "timestamp"] = NOW
            elif kind == "price":
                data.loc[data.index[-1], "close"] = float("inf")
            elif kind == "indicator":
                data.loc[data.index[-1], "rsi_14"] = 101
            else:
                data.attrs["fetched_at"] = NOW+pd.Timedelta(days=1)
            with self.subTest(kind=kind):
                result = run_workflow(data, "BTC/USDT", "1h", now=NOW)
                self.assertEqual(result.state["execution_status"], "FAILED")
                self.assertIsNone(result.state["final_report"])

    def test_initial_nan_means_insufficient_data_not_failed_execution(self):
        result = run_workflow(market(short=True), "BTC/USDT", "1h", now=NOW)
        self.assertEqual(result.state["execution_status"], "COMPLETED")
        self.assertEqual(result.state["final_report"].final_signal, "INSUFFICIENT_DATA")
        self.assertFalse(result.state["errors"])
        data = market()
        data.loc[data.index[-1], "macd_signal"] = float("nan")
        result = run_workflow(data, "BTC/USDT", "1h", now=NOW)
        self.assertEqual(result.state["final_report"].final_signal, "INSUFFICIENT_DATA")
        self.assertFalse(result.state["errors"])

    def test_collector_and_indicator_metadata_must_match_requested_asset_timeframe(self):
        for attribute, value in (("symbol", "ETH/USDT"), ("timeframe", "5m"),
                                 ("technical_indicators", {"timeframe": "4h"})):
            data = market()
            data.attrs[attribute] = value
            with self.subTest(attribute=attribute):
                result = run_workflow(data, "BTC/USDT", "1h", now=NOW)
                self.assertEqual(result.state["execution_status"], "FAILED")
                self.assertIsNone(result.state["final_report"])

    def test_missing_macro_does_not_delete_technical_result(self):
        result = run_workflow(market(), "BTC/USDT", "1h", now=NOW)
        self.assertEqual(result.state["macro_result"].macro_status, "INSUFFICIENT_DATA")
        self.assertIsNotNone(result.state["technical_result"])
        self.assertEqual(result.state["execution_status"], "COMPLETED")
        self.assertIn("선택·조회된 거시경제 자료 부족", result.state["final_report"].missing_data)

    def test_missing_or_forged_publication_vintage_never_enables_directional_macro(self):
        frames = macro_frames()
        for frame in frames.values():
            frame["available_at"] = frame.observation_date
            frame["vintage_date"] = frame.observation_date
        result = run_workflow(market(), "BTC/USDT", "1h", macro_frames=frames, now=NOW)
        self.assertEqual(result.state["macro_result"].macro_status, "CONTEXT_ONLY")
        self.assertTrue(all(item.source_type == "MACRO" for item in result.state["macro_result"].macro_evidence))
        self.assertTrue(all(item["release_time"] == item["vintage"] == "unknown" for item in result.state["macro_metadata"]))
        self.assertTrue(any("빈티지" in text for text in result.state["final_report"].limitations))
        self.assertEqual(result.state["final_report"].final_signal, run_workflow(market(), "BTC/USDT", "1h", now=NOW).state["final_report"].final_signal)

    def test_bad_macro_input_is_partial_failure_not_insufficient_or_technical_failure(self):
        frames = macro_frames()
        frames["CPIAUCSL"]["units"] = "wrong unit"
        result = run_workflow(market(), "BTC/USDT", "1h", macro_frames=frames, now=NOW)
        self.assertEqual(result.state["execution_status"], "PARTIAL")
        self.assertEqual(statuses(result)["macro_agent"], "FAILED")
        self.assertEqual(statuses(result)["technical_agent"], "COMPLETED")
        self.assertIsNotNone(result.state["final_report"])
        self.assertIsNone(result.state["macro_result"])

    def test_agent_failure_logs_static_errors_without_secret_and_other_branch_survives(self):
        secret = "sk-private-should-never-be-in-logs"
        agent = Mock()
        agent.analyze.side_effect = RuntimeError(secret)
        result = run_workflow(market(), "BTC/USDT", "1h", workflow=build_workflow(technical_agent=agent), now=NOW)
        self.assertEqual(result.state["execution_status"], "FAILED")
        self.assertIsNone(result.state["final_report"])
        self.assertIsNotNone(result.state["macro_result"])
        self.assertEqual(statuses(result)["technical_agent"], "FAILED")
        self.assertEqual(statuses(result)["report_formatter"], "SKIPPED")
        self.assertNotIn(secret, str(result))

    def test_fabricated_evidence_and_missing_conflicting_factors_are_rejected(self):
        class Fake:
            def analyze(self, snapshot):
                result = RuleTechnicalAgent().analyze(snapshot)
                result.evidence[0].metric_value += 1
                return result
        result = run_workflow(market(), "BTC/USDT", "1h", workflow=build_workflow(technical_agent=Fake()), now=NOW)
        self.assertEqual(result.state["errors"][0].category, "EVIDENCE_VALIDATION")
        self.assertIsNone(result.state["final_report"])
        self.assertIsNone(result.state["technical_result"])

    def test_two_parallel_failures_append_both_errors_without_overwriting(self):
        technical, macro = Mock(), Mock()
        technical.analyze.side_effect = RuntimeError("private technical error")
        macro.analyze.side_effect = RuntimeError("private macro error")
        graph = build_workflow(technical_agent=technical, macro_agent=macro)
        result = run_workflow(market(), "BTC/USDT", "1h", workflow=graph, now=NOW)
        self.assertEqual({error.node for error in result.state["errors"]}, {"technical_agent", "macro_agent"})
        self.assertEqual(len(result.state["errors"]), 2)
        self.assertEqual(result.state["execution_status"], "FAILED")
        self.assertIsNone(result.state["final_report"])
        self.assertNotIn("private technical error", str(result))

    def test_aggregator_checks_both_completion_and_results_again(self):
        result = run_workflow(market(), "BTC/USDT", "1h", now=NOW)
        state = dict(result.state)
        state["node_execution_logs"] = [log for log in state["node_execution_logs"] if log.node != "macro_agent"]
        with self.assertRaises(WorkflowValidationError):
            aggregate(state, None)
        state = deepcopy(result.state)
        state["technical_result"].evidence[0].metric_value += 1
        with self.assertRaises(WorkflowValidationError):
            aggregate(state, None)

    def test_logs_contain_each_transition_duration_and_parallel_events(self):
        result = run_workflow(market(), "BTC/USDT", "1h", now=NOW)
        for node in NODES:
            events = [log for log in result.state["node_execution_logs"] if log.node == node]
            self.assertEqual([log.status for log in events], ["WAITING", "RUNNING", "COMPLETED"])
            self.assertGreaterEqual(events[-1].duration_ms, 0)
            self.assertGreaterEqual(events[-1].ended_at_utc, events[-1].started_at_utc)

    def test_analysis_reproducibility_and_original_frames_unchanged(self):
        data, frames = market(), macro_frames()
        before, macro_before = data.copy(deep=True), {key: frame.copy(deep=True) for key, frame in frames.items()}
        results = [run_workflow(data, "BTC/USDT", "1h", macro_frames=frames, now=NOW) for _ in range(2)]
        ignored = {"run_id", "duration_ms", "started_at_utc", "completed_at_utc"}
        self.assertEqual(results[0].state["final_report"].model_dump(exclude=ignored), results[1].state["final_report"].model_dump(exclude=ignored))
        assert_frame_equal(data, before)
        for key in frames:
            assert_frame_equal(frames[key], macro_before[key])

    def test_no_paid_api_or_external_trace_even_if_tracing_environment_enabled(self):
        with patch.dict(os.environ, {"LANGSMITH_TRACING": "true", "LANGCHAIN_TRACING_V2": "true"}), \
                patch("openai.OpenAI") as client, patch("requests.sessions.Session.send") as send, \
                patch("httpx.Client.send") as http, patch("langsmith.client.Client.create_run") as trace:
            result = run_workflow(market(), "BTC/USDT", "1h", now=NOW)
        self.assertEqual(result.state["execution_status"], "COMPLETED")
        for mock in (client, send, http, trace):
            mock.assert_not_called()


class DeterministicAgentTests(unittest.TestCase):
    def snapshot(self, *, rsi, spread, histogram):
        snapshot = build_market_snapshot(market(), "BTC/USDT", "1h", now=NOW)
        values = {"rsi_14": rsi, "ema_spread_pct": spread, "sma_spread_pct": spread, "macd_histogram": histogram}
        for fact in snapshot.facts:
            if fact.metric_name in values:
                fact.metric_value = float(values[fact.metric_name])
                fact.explanation = f"합성 테스트 {fact.metric_name}: {fact.metric_value}."
                fact.role = "bullish" if (fact.metric_value > (50 if fact.metric_name == "rsi_14" else 0)) else "bearish"
        return snapshot

    def test_conjunction_rules_and_rsi_boundaries(self):
        for rsi, spread, histogram, expected in ((60, 1, 2, "BULLISH"), (40, -1, -2, "BEARISH"),
                                                  (70, 1, 2, "NEUTRAL"), (30, -1, -2, "NEUTRAL"),
                                                  (50, 1, 2, "NEUTRAL"), (60, -1, 2, "NEUTRAL")):
            with self.subTest(rsi=rsi, spread=spread):
                snapshot = self.snapshot(rsi=rsi, spread=spread, histogram=histogram)
                self.assertEqual(technical_signal(snapshot), expected)

    def test_conflicting_factors_are_explicit_and_aggregator_never_votes(self):
        execution = run_workflow(market(), "BTC/USDT", "1h", now=NOW)
        snapshot = self.snapshot(rsi=60, spread=1, histogram=-2)
        state = dict(execution.state)
        state.update(input_data=snapshot, input_fingerprint=snapshot_fingerprint(snapshot), technical_result=RuleTechnicalAgent().analyze(snapshot))
        result = aggregate(state, None)["aggregation_result"]
        self.assertEqual(result.final_signal, "NEUTRAL")
        self.assertTrue(result.conflicts)
        self.assertTrue(state["technical_result"].bullish_factors)
        self.assertTrue(state["technical_result"].bearish_factors)
