"""연구용 Risk Manager 수치/근거/실제 StateGraph 검증. 합성 입력이며 유료 API 호출 없음."""

from copy import deepcopy
from math import sqrt
from unittest.mock import Mock, patch
import unittest

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal
from pydantic import ValidationError

from agents.risk_manager import RiskManager
from graph.runner import run_workflow
from graph.workflow import build_workflow
from graph.debate_workflow import build_debate_workflow
from graph.risk_nodes import build_risk_input
from llm.config import AIError, OpenAIConfig
from risk.metrics import calculate_atr, calculate_risk_metrics
from risk.models import PositionSettings, RiskThresholds, RiskDataError
from risk.position import simulate_position
from risk.evaluator import classify_risk, evaluate_risk, validate_risk_report
from test_ai_analysis import NOW, TEST_OPENAI_KEY
from test_workflow import market, macro_frames, statuses


def prices(closes, *, volume=None):
    close = np.asarray(closes, dtype=float)
    return pd.DataFrame({"timestamp": pd.date_range("2026-10-01", periods=len(close), freq="h", tz="UTC"),
        "open": close, "high": close+1, "low": close-1, "close": close,
        "volume": np.asarray(volume, dtype=float) if volume is not None else np.full(len(close), 10.)})


def execute(*, debate=False, **kwargs):
    return run_workflow(kwargs.pop("data", market()), "BTC/USDT", "1h", now=NOW, requested=True,
                        debate_enabled=debate, risk_enabled=kwargs.pop("risk_enabled", True), **kwargs)


class QuantitativeRiskTests(unittest.TestCase):
    def test_sample_volatility_annualization_and_observed_frequency(self):
        data = prices([100, 110, 99])
        result = calculate_risk_metrics(data, "1h", now=NOW)
        self.assertAlmostEqual(result.volatility_per_bar_pct, sqrt(.02)*100)
        self.assertAlmostEqual(result.annualized_volatility_pct, sqrt(.02)*100*sqrt(365*24))
        self.assertEqual(result.observed_intervals_seconds, [3600.])
        self.assertEqual(result.return_count, 2)
        self.assertEqual(result.candle_count, 3)

    def test_close_mdd_and_actual_peak_trough_close_times(self):
        data = prices([100, 120, 90, 110])
        result = calculate_risk_metrics(data, "1h", now=NOW)
        self.assertAlmostEqual(result.close_mdd_pct, 25.)
        self.assertEqual(result.mdd_peak_time_utc, "2026-10-01T02:00:00+00:00")
        self.assertEqual(result.mdd_trough_time_utc, "2026-10-01T03:00:00+00:00")
        flat = calculate_risk_metrics(prices([100]*60), "1h", now=NOW)
        self.assertEqual(flat.close_mdd_pct, 0.)
        self.assertIsNone(flat.mdd_trough_time_utc)

    def test_true_range_gap_and_wilder_seed_recurrence_by_hand(self):
        # TR = 2, 11, 12. ATR(2) seed=6.5, 다음=(6.5+12)/2=9.25.
        atr = calculate_atr(prices([100, 110, 99]), period=2)
        self.assertTrue(pd.isna(atr.iloc[0]))
        self.assertEqual(atr.iloc[1], 6.5)
        self.assertEqual(atr.iloc[2], 9.25)

    def test_atr14_warmup_and_ratio(self):
        data = prices([100]*15)
        data.loc[14, "high"] = 103.
        atr = calculate_atr(data)
        self.assertTrue(atr.iloc[:13].isna().all())
        self.assertEqual(atr.iloc[13], 2.)
        self.assertAlmostEqual(atr.iloc[14], 30/14)
        result = calculate_risk_metrics(data, "1h", now=NOW)
        self.assertAlmostEqual(result.atr_close_pct, 30/14)
        self.assertEqual(result.atr_contiguous_candles, 15)

    def test_duplicate_dataframe_index_does_not_apply_future_atr_to_previous_rows(self):
        data = prices([100]*15)
        data.index = [0]*len(data)
        atr = calculate_atr(data)
        self.assertTrue(atr.iloc[:13].isna().all())
        self.assertEqual(atr.iloc[13], 2.)

    def test_gap_excludes_return_and_resets_atr(self):
        data = prices([100]*30).drop(index=16)
        result = calculate_risk_metrics(data, "1h", now=NOW)
        self.assertEqual(result.missing_candles, 1)
        self.assertEqual(result.return_count, len(data)-2)
        self.assertIsNone(result.atr_14)
        self.assertEqual(result.atr_contiguous_candles, 13)
        self.assertTrue(result.quality_issues)

    def test_partial_collection_is_quality_failure_even_when_metrics_exist(self):
        data = prices([100]*70)
        data.attrs["is_partial"] = True
        metrics = calculate_risk_metrics(data, "1h", now=NOW)
        level, _ = classify_risk(metrics, RiskThresholds())
        self.assertEqual(level, "UNKNOWN")

    def test_short_or_empty_data_has_na_not_fabricated_zero_metrics(self):
        for data in (prices([100]), prices([])):
            with self.subTest(count=len(data)):
                metrics = calculate_risk_metrics(data, "1h", now=NOW)
                self.assertIsNone(metrics.volatility_per_bar_pct)
                self.assertIsNone(metrics.close_mdd_pct)
                self.assertIsNone(metrics.atr_14)
                self.assertEqual(classify_risk(metrics, RiskThresholds())[0], "UNKNOWN")

    def test_recent_volatility_comparison_by_hand(self):
        closes = [100.]
        for change in (.01, -.01, .03, -.03):
            closes.append(closes[-1]*(1+change))
        policy = RiskThresholds(min_returns=2, volatility_window=2)
        metrics = calculate_risk_metrics(prices(closes), "1h", now=NOW, thresholds=policy)
        self.assertAlmostEqual(metrics.volatility_expansion_ratio, 3.)
        self.assertTrue(metrics.volatility_expanding)

    def test_volume_spike_uses_previous_bars_not_current_in_average(self):
        metrics = calculate_risk_metrics(prices([100]*25, volume=[10]*24+[40]), "1h", now=NOW)
        self.assertEqual(metrics.previous_volume_sma, 10.)
        self.assertEqual(metrics.volume_ratio, 4.)
        self.assertTrue(metrics.volume_spike)

    def test_zero_volume_average_is_unknown_not_division_by_zero(self):
        metrics = calculate_risk_metrics(prices([100]*70, volume=[0]*69+[10]), "1h", now=NOW)
        self.assertIsNone(metrics.volume_ratio)
        self.assertEqual(classify_risk(metrics, RiskThresholds())[0], "UNKNOWN")

    def test_invalid_prices_times_duplicates_and_future_candles_rejected(self):
        for kind in ("infinite", "negative", "duplicate", "future", "nan"):
            data = prices([100]*70)
            if kind == "infinite":
                data.loc[0, "high"] = np.inf
            elif kind == "negative":
                data.loc[0, "volume"] = -1.
            elif kind == "duplicate":
                data.loc[1, "timestamp"] = data.loc[0, "timestamp"]
            elif kind == "future":
                data.loc[data.index[-1], "timestamp"] = NOW
            else:
                data.loc[0, "close"] = np.nan
            with self.subTest(kind=kind), self.assertRaises(RiskDataError):
                calculate_risk_metrics(data, "1h", now=NOW)

    def test_metrics_and_atr_are_causal_and_original_unchanged(self):
        data = prices([100+i%4 for i in range(80)])
        before = data.copy(deep=True)
        prefix = calculate_atr(data.iloc[:60])
        full = calculate_atr(data)
        pd.testing.assert_series_equal(prefix, full.iloc[:60])
        calculate_risk_metrics(data, "1h", now=NOW)
        assert_frame_equal(data, before)

    def test_boolean_and_string_prices_are_not_accepted_as_market_numbers(self):
        for value in (True, "100"):
            data = prices([100]*70)
            data["close"] = value
            with self.subTest(value=value), self.assertRaises(RiskDataError):
                calculate_risk_metrics(data, "1h", now=NOW)

    def test_timezone_conversion_preserves_same_utc_observation(self):
        utc = prices([100]*70)
        local = utc.copy()
        local["timestamp"] = local.timestamp.dt.tz_convert("Asia/Seoul")
        self.assertEqual(calculate_risk_metrics(utc, "1h", now=NOW), calculate_risk_metrics(local, "1h", now=NOW))


class RiskGradeTests(unittest.TestCase):
    def setUp(self):
        self.metrics = calculate_risk_metrics(prices([100]*80), "1h", now=NOW).model_copy(update={
            "annualized_volatility_pct": 0., "close_mdd_pct": 0., "atr_close_pct": 0., "volatility_expanding": False,
            "volume_spike": False, "quality_issues": []})

    def test_all_three_research_threshold_boundaries(self):
        policy = RiskThresholds()
        for name in ("annualized_volatility_pct", "close_mdd_pct", "atr_close_pct"):
            for index, boundary in enumerate(getattr(policy, name)):
                below = self.metrics.model_copy(update={name: float(np.nextafter(boundary, -np.inf))})
                exact = self.metrics.model_copy(update={name: boundary})
                with self.subTest(metric=name, boundary=boundary):
                    self.assertEqual(classify_risk(below, policy)[0], ("LOW", "MODERATE", "HIGH")[index])
                    self.assertEqual(classify_risk(exact, policy)[0], ("MODERATE", "HIGH", "CRITICAL")[index])

    def test_data_quality_and_required_analysis_override_grade_to_unknown(self):
        for update in ({"return_count": 1}, {"atr_close_pct": None}, {"quality_issues": ["missing"]}):
            self.assertEqual(classify_risk(self.metrics.model_copy(update=update), RiskThresholds())[0], "UNKNOWN")
        self.assertEqual(classify_risk(self.metrics, RiskThresholds(), analysis_valid=False)[0], "UNKNOWN")
        self.assertEqual(classify_risk(self.metrics, RiskThresholds(), technical_sufficient=False)[0], "UNKNOWN")

    def test_expansion_and_spike_raise_only_defined_grade_floor(self):
        self.assertEqual(classify_risk(self.metrics.model_copy(update={"volatility_expanding": True}), RiskThresholds())[0], "HIGH")
        self.assertEqual(classify_risk(self.metrics.model_copy(update={"volume_spike": True}), RiskThresholds())[0], "MODERATE")

    def test_invalid_policy_is_not_silently_accepted(self):
        with self.assertRaises(ValidationError):
            RiskThresholds(annualized_volatility_pct=(80., 40., 150.))


class PositionRiskTests(unittest.TestCase):
    def test_zero_cost_hand_calculation(self):
        result = simulate_position(100., PositionSettings(capital=1000., account_loss_rate=.02, stop_distance=.1, fee_rate=0., slippage_rate=0.))
        self.assertEqual(result.allowed_loss, 20.)
        self.assertAlmostEqual(result.cost_aware_quantity, 2.)
        self.assertAlmostEqual(result.adjusted_notional, 200.)
        self.assertAlmostEqual(result.adjusted_max_loss, 20.)
        self.assertEqual(result.adjusted_max_cost, 0.)

    def test_both_sides_round_trip_fees_slippage_by_hand(self):
        settings = PositionSettings(capital=1000., account_loss_rate=.02, stop_distance=.1, fee_rate=.01, slippage_rate=.01)
        result = simulate_position(100., settings)
        long, short = result.scenarios
        quantity = 20/14.201
        self.assertAlmostEqual(result.cost_aware_quantity, quantity)
        self.assertEqual(long.entry_fill, 101.)
        self.assertEqual(long.stop_reference_price, 90.)
        self.assertAlmostEqual(long.exit_fill, 89.1)
        self.assertAlmostEqual(long.estimated_cost, quantity*3.801)
        self.assertAlmostEqual(long.estimated_loss, quantity*13.801)
        self.assertAlmostEqual(short.entry_fill, 99.)
        self.assertAlmostEqual(short.stop_reference_price, 110.)
        self.assertAlmostEqual(short.exit_fill, 111.1)
        self.assertAlmostEqual(short.estimated_cost, quantity*4.201)
        self.assertAlmostEqual(short.estimated_loss, 20.)
        for row in result.scenarios:
            self.assertAlmostEqual(row.estimated_loss, quantity*100*.1+row.estimated_cost)

    def test_leverage_changes_full_exposure_and_margin_not_loss_budget(self):
        settings = PositionSettings(capital=1000., account_loss_rate=.01, stop_distance=.1, fee_rate=0., slippage_rate=0.)
        a = simulate_position(100., settings)
        b = simulate_position(100., settings.model_copy(update={"leverage": 10.}))
        self.assertEqual(a.allowed_loss, b.allowed_loss)
        self.assertEqual(a.cost_aware_quantity, b.cost_aware_quantity)
        self.assertEqual(b.requested_full_notional, 10*a.requested_full_notional)
        self.assertAlmostEqual(b.scenarios[0].margin_required, a.scenarios[0].margin_required/10)

    def test_margin_plus_entry_fee_cannot_exceed_capital(self):
        result = simulate_position(100., PositionSettings(capital=1000., account_loss_rate=.9, stop_distance=.01, fee_rate=.01, slippage_rate=.01))
        for row in result.scenarios:
            self.assertLessEqual(row.margin_required+row.entry_fee, 1000.+1e-9)
        self.assertLessEqual(result.adjusted_max_loss, result.allowed_loss+1e-9)

    def test_full_exposure_limit_and_adjusted_limit_are_distinct(self):
        result = simulate_position(100., PositionSettings())
        self.assertTrue(result.full_exposure_limit_exceeded)
        self.assertFalse(result.adjusted_limit_exceeded)
        self.assertGreater(result.requested_full_loss, result.allowed_loss)
        self.assertLessEqual(result.adjusted_max_loss, result.allowed_loss+1e-9)

    def test_zero_loss_budget_allows_no_position(self):
        result = simulate_position(100., PositionSettings(account_loss_rate=0.))
        self.assertEqual(result.cost_aware_quantity, 0.)
        self.assertEqual(result.adjusted_max_loss, 0.)

    def test_invalid_settings_and_precision_impossibility_rejected(self):
        for update in ({"capital": True}, {"capital": 0.}, {"capital": np.inf}, {"stop_distance": 0.},
                       {"stop_distance": 1.}, {"leverage": 101.}, {"fee_rate": -1.}, {"account_loss_rate": -1.}):
            with self.subTest(update=update), self.assertRaises(ValidationError):
                PositionSettings(**update)
        with self.assertRaises(RiskDataError):
            simulate_position(100., PositionSettings(stop_distance=1e-300, fee_rate=0., slippage_rate=0.))


class RiskManagerGraphTests(unittest.TestCase):
    def test_four_graph_combinations_and_real_node_status(self):
        for debate_on in (False, True):
            for risk_on in (False, True):
                with self.subTest(debate=debate_on, risk=risk_on):
                    result = execute(debate=debate_on, risk_enabled=risk_on)
                    self.assertEqual(result.state["execution_status"], "COMPLETED")
                    self.assertEqual("risk_manager" in statuses(result), risk_on)
                    if risk_on:
                        self.assertEqual(statuses(result)["risk_manager"], "COMPLETED")
                        self.assertEqual(result.state["risk_result"].execution_mode, "Mock Risk Manager")
                        self.assertIsNotNone(result.state["final_report"].risk_report)
                        self.assertEqual(result.state["research_action"], result.state["risk_result"].trading_action)

    def test_debate_evaluator_is_passed_before_risk_node(self):
        result = execute(debate=True)
        state = result.state
        starts = {log.node: log.started_at_utc for log in state["node_execution_logs"] if log.status == "RUNNING"}
        ends = {log.node: log.ended_at_utc for log in state["node_execution_logs"] if log.status == "COMPLETED"}
        self.assertGreaterEqual(starts["risk_manager"], ends["debate_evaluator"])
        self.assertGreaterEqual(starts["research_aggregator"], ends["risk_manager"])
        self.assertEqual(state["risk_input"].debate_claim_ids, state["debate_evaluation"].validated_message_ids)

    def test_risk_off_preserves_step12_report_and_aggregator(self):
        a = execute(debate=True, risk_enabled=False)
        b = run_workflow(market(), "BTC/USDT", "1h", now=NOW, requested=True, debate_enabled=True)
        ignored = {"run_id", "started_at_utc", "completed_at_utc", "duration_ms"}
        self.assertEqual(a.state["aggregation_result"], b.state["aggregation_result"])
        self.assertEqual(a.state["final_report"].model_dump(exclude=ignored), b.state["final_report"].model_dump(exclude=ignored))
        self.assertNotIn("risk_result", a.state)

    def test_unknown_on_missing_samples_and_partial_data(self):
        for frame in (market(short=True), market().iloc[::2].copy()):
            result = execute(data=frame)
            self.assertEqual(result.state["risk_result"].risk_level, "UNKNOWN")
            self.assertEqual(result.state["research_action"], "ABSTAIN")
            self.assertEqual(result.state["execution_status"], "PARTIAL")

    def test_optional_macro_missing_is_different_from_macro_failure(self):
        normal = execute()
        self.assertNotEqual(normal.state["risk_result"].risk_level, "UNKNOWN")
        agent = Mock()
        agent.analyze.side_effect = RuntimeError("private")
        bad = execute(workflow=build_workflow(risk_enabled=True, macro_agent=agent))
        self.assertEqual(bad.state["risk_result"].risk_level, "UNKNOWN")
        self.assertEqual(bad.state["research_action"], "ABSTAIN")

    def test_technical_failure_is_not_a_successful_risk_analysis(self):
        agent = Mock()
        agent.analyze.side_effect = RuntimeError("private")
        for debate in (False, True):
            graph = (build_debate_workflow if debate else build_workflow)(risk_enabled=True, technical_agent=agent)
            state = execute(debate=debate, workflow=graph).state
            self.assertEqual(state["execution_status"], "FAILED")
            self.assertEqual(state["risk_result"].risk_level, "UNKNOWN")
            self.assertEqual(state["research_action"], "ABSTAIN")
            self.assertIsNone(state["final_report"])

    def test_review_requires_adequate_data_and_no_exposure_limit_breach(self):
        inputs = execute().state["risk_input"].model_copy(deep=True)
        inputs.quantitative_metrics = calculate_risk_metrics(prices([100]*70), "1h", now=NOW).model_copy(update={"stale": False})
        inputs.position_settings = PositionSettings(account_loss_rate=.5, fee_rate=0., slippage_rate=0.)
        result = evaluate_risk(inputs)
        self.assertEqual(result.risk_level, "MODERATE")
        self.assertFalse(result.risk_limit_exceeded)
        self.assertEqual(result.trading_action, "REVIEW")
        inputs.quantitative_metrics.annualized_volatility_pct = 150.
        self.assertEqual(evaluate_risk(inputs).trading_action, "ABSTAIN")

    def test_position_limit_cannot_be_ignored_but_direction_is_preserved(self):
        result = execute()
        state = result.state
        self.assertTrue(state["risk_result"].risk_limit_exceeded)
        self.assertIn(state["research_action"], ("HOLD", "ABSTAIN"))
        self.assertEqual(state["final_report"].research_action, state["research_action"])
        self.assertEqual(state["final_report"].final_signal, state["pre_risk_signal"])
        self.assertTrue(any("위험 검토 상태" in text for text in state["aggregation_result"].conflicts))

    def test_risk_agent_failure_is_partial_and_abstain(self):
        agent = Mock()
        agent.analyze.side_effect = RuntimeError("sk-private-test-not-for-logs")
        for debate_on in (False, True):
            graph = build_debate_workflow(risk_enabled=True, risk_manager=agent) if debate_on else build_workflow(risk_enabled=True, risk_manager=agent)
            result = execute(debate=debate_on, workflow=graph)
            self.assertEqual(result.state["execution_status"], "PARTIAL")
            self.assertEqual(statuses(result)["risk_manager"], "FAILED")
            self.assertEqual(result.state["research_action"], "ABSTAIN")
            self.assertIsNone(result.state["final_report"].risk_report)
            self.assertIsNotNone(result.state["technical_result"])
            self.assertNotIn("sk-private-test-not-for-logs", str(result))

    def test_fake_reference_value_time_or_research_action_rejected(self):
        for field in ("id", "value", "time", "action"):
            class Fake(RiskManager):
                def analyze(self, inputs):
                    result = super().analyze(inputs)
                    if field == "id":
                        result.supporting_evidence_ids = ["unknown"]
                    elif field == "value":
                        result.evidence[0].metric_value += 1
                    elif field == "time":
                        result.evidence[0].observation_time = "2027-01-01T00:00:00Z"
                    else:
                        result.trading_action = "REVIEW"
                    return result
            with self.subTest(field=field):
                result = execute(workflow=build_workflow(risk_enabled=True, risk_manager=Fake()))
                self.assertIsNone(result.state["risk_result"])
                self.assertEqual(result.state["research_action"], "ABSTAIN")
                self.assertEqual(result.state["errors"][-1].category, "EVIDENCE_VALIDATION")

    def test_future_candle_stops_before_risk_not_fake_low_grade(self):
        data = market()
        data.loc[data.index[-1], "timestamp"] = NOW
        result = execute(data=data)
        self.assertEqual(result.state["execution_status"], "FAILED")
        self.assertEqual(statuses(result)["risk_manager"], "SKIPPED")
        self.assertIsNone(result.state["risk_result"])

    def test_current_macro_and_forged_vintage_are_context_only(self):
        frames = macro_frames()
        for frame in frames.values():
            frame["vintage_date"] = frame.observation_date
        state = execute(macro_frames=frames).state
        self.assertEqual(state["risk_input"].macro_status, "CONTEXT_ONLY")
        self.assertTrue(any(item.source_type == "MACRO" for item in state["risk_result"].evidence))
        self.assertTrue(any("발표일" in text for text in state["risk_result"].limitations))

    def test_settings_change_does_not_reuse_old_debate_duplicate_scope(self):
        from llm.budget import UsageLedger
        ledger = UsageLedger()
        first = execute(debate=True, ledger=ledger)
        second = execute(debate=True, ledger=ledger, risk_settings=PositionSettings(capital=5000.))
        self.assertEqual(first.state["execution_status"], "COMPLETED")
        self.assertEqual(second.state["execution_status"], "COMPLETED")
        self.assertFalse(ledger.records)

    def test_reproducibility_and_original_frames_unchanged(self):
        data, frames = market(), macro_frames()
        before = data.copy(deep=True)
        copies = {key: value.copy(deep=True) for key, value in frames.items()}
        a, b = [execute(data=data, macro_frames=frames).state for _ in range(2)]
        self.assertEqual(a["risk_result"], b["risk_result"])
        assert_frame_equal(data, before)
        for key in frames:
            assert_frame_equal(frames[key], copies[key])

    def test_mock_risk_no_paid_api_or_external_trace(self):
        with patch("openai.OpenAI") as sdk, patch("httpx.Client.send") as http, patch("requests.sessions.Session.send") as requests, \
                patch("langsmith.client.Client.create_run") as trace:
            result = execute(debate=True)
        self.assertEqual(result.state["execution_status"], "COMPLETED")
        for spy in (sdk, http, requests, trace):
            spy.assert_not_called()
        with patch("openai.OpenAI") as sdk, self.assertRaises(AIError):
            execute(mode="LLM Workflow", config=OpenAIConfig(api_key=TEST_OPENAI_KEY), cost_approved=True)
        sdk.assert_not_called()
