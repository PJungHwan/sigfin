"""합성 시장 데이터/가상 응답만 사용한다. OpenAI 네트워크 요청은 금지한다."""

from datetime import timedelta
import json
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from config.llm_settings import DEFAULT_OPENAI_MODEL, MODEL_PRICES
from indicators.technical import add_technical_indicators
from llm.budget import AIOptions, UsageLedger, estimate_cost, price_for
from llm.client import ClientResult
from llm.config import AIError, OpenAIConfig, read_openai_config
from llm.prompts import request_payload, input_token_reservation
from llm.schemas import TokenUsage
from llm.validators import validate_report
from services.ai_inputs import build_market_snapshot, snapshot_fingerprint
from services.ai_analysis_service import analyze_market, mock_report
from test_app import full_fixture_frame, fixture_frame
from test_macro_ui import mock_series
from test_fred import TEST_KEY

NOW = pd.Timestamp("2026-10-15T12:00Z")
TEST_OPENAI_KEY = "sk-test-NOT-A-REAL-KEY"


def market_snapshot(short=False, macro=False):
    data = add_technical_indicators(fixture_frame() if short else full_fixture_frame())
    frames = None
    if macro:
        frames = {series: mock_series(series, "2025-01-01", "2026-10-14", api_key=TEST_KEY)
                  for series in ("CPIAUCSL", "FEDFUNDS", "UNRATE", "PAYEMS", "DGS10")}
        for frame in frames.values():
            frame.attrs["fetched_at"] = NOW
    return build_market_snapshot(data, "BTC/USDT", "1h", macro_frames=frames, now=NOW)


class AIInputTests(unittest.TestCase):
    def test_verified_technical_summary_and_raw_candles_not_sent(self):
        data = add_technical_indicators(full_fixture_frame())
        before = data.copy(deep=True)
        snapshot = build_market_snapshot(data, "BTC/USDT", "1h", now=NOW)
        values = {fact.metric_name: fact.metric_value for fact in snapshot.facts}
        self.assertEqual(values["close"], data.iloc[-1].close)
        self.assertEqual(values["rsi_14"], data.iloc[-1].rsi_14)
        self.assertAlmostEqual(values["macd_histogram"], values["macd"]-values["macd_signal"])
        self.assertEqual(snapshot.data_as_of_utc, "2026-10-14T12:00:00+00:00")
        self.assertEqual(snapshot.candle_start_utc, "2026-10-14T11:00:00+00:00")
        self.assertLess(len(snapshot.facts), 40)
        payload = request_payload(snapshot, NOW.isoformat())
        self.assertLess(len(payload.encode()), 24000)
        self.assertNotIn('"timestamp":', payload)
        self.assertGreater(input_token_reservation(snapshot, NOW.isoformat()), len(payload.encode()))
        assert_frame_equal(data, before)

    def test_insufficient_and_nan_indicators_no_fake_values(self):
        snapshot = market_snapshot(short=True)
        self.assertFalse(snapshot.sufficient)
        self.assertIn("rsi_14", snapshot.missing_metrics)
        self.assertNotIn("rsi_14", {fact.metric_name for fact in snapshot.facts})
        self.assertNotIn("NaN", snapshot.model_dump_json())

    def test_future_unconfirmed_candles_not_used(self):
        data = add_technical_indicators(full_fixture_frame())
        snapshot = build_market_snapshot(data, "BTC/USDT", "1h", now=NOW)
        future = data.iloc[[-1]].copy()
        future["timestamp"] = NOW
        combined = pd.concat([data, future], ignore_index=True)
        combined.attrs = data.attrs.copy()
        actual = build_market_snapshot(combined, "BTC/USDT", "1h", now=NOW)
        self.assertEqual(snapshot_fingerprint(snapshot), snapshot_fingerprint(actual))

    def test_invalid_ohlcv_indicator_and_timestamps_are_blocked(self):
        data = add_technical_indicators(full_fixture_frame())
        for column, value in (("low", -1.), ("volume", -1.), ("close", np.inf), ("rsi_14", 101.), ("macd_histogram", 999.)):
            bad = data.copy()
            bad.loc[bad.index[-1], column] = value
            with self.subTest(column=column), self.assertRaises(AIError):
                build_market_snapshot(bad, "BTC/USDT", "1h", now=NOW)
        for stamp in ("2026-10-16T00:00Z", "2026-10-14"):
            bad = data.copy()
            bad.attrs["fetched_at"] = stamp
            with self.subTest(stamp=stamp), self.assertRaises(AIError):
                build_market_snapshot(bad, "BTC/USDT", "1h", now=NOW)

    def test_all_macro_units_observation_labels_and_vintage_constraints(self):
        snapshot = market_snapshot(macro=True)
        facts = [fact for fact in snapshot.facts if fact.source_type == "MACRO"]
        self.assertEqual(len(facts), 5)
        self.assertTrue(all(fact.role == "context" for fact in facts))
        self.assertEqual(next(fact.unit for fact in facts if fact.metric_name == "PAYEMS"), "천 명")
        self.assertEqual(next(fact.unit for fact in facts if fact.metric_name == "FEDFUNDS"), "%")
        self.assertTrue(all(row["release_time"] == row["vintage"] == "unknown" for row in snapshot.macro_metadata))
        self.assertIn("발표 시각", snapshot.macro_summary)
        self.assertTrue(all(pd.Timestamp(fact.observation_time) <= pd.Timestamp(snapshot.data_as_of_utc) for fact in facts))

    def test_key_missing_invalid_model_and_safe_config_repr(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "", "OPENAI_MODEL": ""}), patch("llm.config.ENV_PATH") as path:
            path.is_file.return_value = False
            config = read_openai_config()
        self.assertIsNone(config.api_key)
        self.assertEqual(config.model, DEFAULT_OPENAI_MODEL)
        self.assertNotIn(TEST_OPENAI_KEY, repr(OpenAIConfig(api_key=TEST_OPENAI_KEY)))
        with patch.dict("os.environ", {"OPENAI_MODEL": "bad/model"}):
            with self.assertRaises(AIError):
                read_openai_config()


class AIEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = market_snapshot(macro=True)
        self.report = mock_report(self.snapshot, NOW.isoformat())

    def test_normal_structured_result_roundtrip(self):
        report = validate_report(self.report.model_dump_json(), self.snapshot, NOW.isoformat())
        self.assertEqual(report.signal, "NEUTRAL")
        self.assertEqual(report, self.report)

    def test_non_json_schema_missing_and_extra_fields_invalid_signal(self):
        cases = ["not JSON", "{}"]
        for update in ({"confidence": .99}, {"signal": "CERTAIN_PROFIT"}, {"evidence": []}):
            value = self.report.model_dump()
            value.update(update)
            cases.append(json.dumps(value))
        for value in cases:
            with self.subTest(), self.assertRaises(AIError):
                validate_report(value, self.snapshot, NOW.isoformat())

    def test_fabricated_numeric_reference_source_time_or_text_rejected(self):
        for column, value in (("metric_value", 999999.), ("reference_id", "unknown:event"),
                              ("source_type", "MACRO"), ("observation_time", "2020-01-01T00:00:00+00:00"),
                              ("explanation", "금리 인하 발표로 가격이 상승했습니다.")):
            payload = self.report.model_dump()
            payload["evidence"][0][column] = value
            with self.subTest(column=column), self.assertRaises(AIError):
                validate_report(payload, self.snapshot, NOW.isoformat())

    def test_fabricated_price_date_event_in_any_narrative_rejected(self):
        for field in ("technical_summary", "macro_summary", "conclusion"):
            for text in ("가격은 999999 USDT입니다.", "2027-01-01에 발표되었습니다.", "입력에 없는 전쟁으로 폭락했습니다."):
                payload = self.report.model_dump()
                payload[field] = text
                with self.subTest(field=field), self.assertRaises(AIError):
                    validate_report(payload, self.snapshot, NOW.isoformat())

    def test_wrong_symbol_timeframe_and_analysis_time_rejected(self):
        for field, value in (("symbol", "ETH/USDT"), ("timeframe", "1d"),
                             ("analyzed_at_utc", "2025-01-01T00:00Z"), ("data_as_of_utc", "2025-01-01T00:00Z")):
            payload = self.report.model_dump()
            payload[field] = value
            with self.subTest(field=field), self.assertRaises(AIError):
                validate_report(payload, self.snapshot, NOW.isoformat())

    def test_missing_opposing_evidence_and_macro_directional_use_blocked(self):
        payload = self.report.model_dump()
        if payload["bullish_factors"]:
            payload["bullish_factors"] = []
            with self.assertRaises(AIError):
                validate_report(payload, self.snapshot, NOW.isoformat())
        payload = self.report.model_dump()
        macro = next(fact for fact in self.snapshot.facts if fact.source_type == "MACRO")
        payload["bullish_factors"] = [macro.explanation]
        with self.assertRaises(AIError):
            validate_report(payload, self.snapshot, NOW.isoformat())


class AIExecutionAndBudgetTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = market_snapshot()
        self.config = OpenAIConfig(api_key=TEST_OPENAI_KEY)
        self.ledger = UsageLedger()
        self.report = mock_report(self.snapshot, NOW.isoformat())
        self.usage = TokenUsage(input_tokens=1000, output_tokens=500, total_tokens=1500)

    def run_analysis(self, *, options=None, **kwargs):
        return analyze_market(self.snapshot, self.config, options or AIOptions(mode="live"), self.ledger,
                              requested=kwargs.pop("requested", True), cost_approved=kwargs.pop("cost_approved", True), now=NOW, **kwargs)

    def test_mock_mode_never_calls_sdk_and_is_labelled(self):
        with patch("services.ai_analysis_service.request_analysis") as request:
            result = self.run_analysis(options=AIOptions())
        request.assert_not_called()
        self.assertEqual(result.mode, "mock")
        self.assertEqual(result.status, "MOCK")
        self.assertIsNone(result.usage)
        self.assertEqual(len(self.ledger.records), 0)

    def test_not_requested_cost_approval_or_key_missing_never_calls_sdk(self):
        with patch("services.ai_analysis_service.request_analysis") as request:
            for kwargs, category in (({"requested": False}, "NOT_REQUESTED"),
                                     ({"requested": "false"}, "NOT_REQUESTED"),
                                     ({"cost_approved": False}, "COST_APPROVAL_REQUIRED"),
                                     ({"cost_approved": "false"}, "COST_APPROVAL_REQUIRED")):
                with self.subTest(category=category), self.assertRaises(AIError) as caught:
                    self.run_analysis(**kwargs)
                self.assertEqual(caught.exception.category, category)
            self.config = OpenAIConfig()
            with self.assertRaises(AIError) as caught:
                self.run_analysis()
            self.assertEqual(caught.exception.category, "KEY_MISSING")
        request.assert_not_called()

    def test_short_data_is_local_and_consumes_no_paid_request(self):
        self.snapshot = market_snapshot(short=True)
        with patch("services.ai_analysis_service.request_analysis") as request:
            result = self.run_analysis(cost_approved=False)
        self.assertEqual(result.report.signal, "INSUFFICIENT_DATA")
        request.assert_not_called()
        self.assertFalse(self.ledger.records)

    def test_success_token_usage_and_cost_recorded(self):
        with patch("services.ai_analysis_service.request_analysis", return_value=ClientResult(self.report, self.usage, DEFAULT_OPENAI_MODEL)):
            result = self.run_analysis()
        self.assertAlmostEqual(result.estimated_cost_usd, (1000*.75+500*4.5)/1e6)
        record = self.ledger.records[0]
        self.assertEqual(record["status"], "VALIDATED")
        self.assertEqual(record["usage"]["total_tokens"], 1500)
        self.assertNotIn(TEST_OPENAI_KEY, str(record))

    def test_duplicate_request_is_blocked_even_after_failure(self):
        with patch("services.ai_analysis_service.request_analysis", side_effect=AIError("timeout", "TIMEOUT")) as request:
            with self.assertRaises(AIError):
                self.run_analysis()
            with self.assertRaises(AIError) as caught:
                self.run_analysis()
            self.assertEqual(caught.exception.category, "DUPLICATE_REQUEST")
            self.assertEqual(request.call_count, 1)
        self.assertGreater(self.ledger.committed_usd, 0)
        self.assertEqual(self.ledger.records[0]["billing"], "미확인")

    def test_duplicate_window_expires(self):
        ledger = UsageLedger()
        ledger.check_duplicate("one", clock=1)
        with self.assertRaises(AIError):
            ledger.check_duplicate("one", clock=2)
        ledger.check_duplicate("one", clock=62)

    def test_count_cost_and_unknown_price_limits(self):
        for options, prior, category in ((AIOptions(mode="live", max_requests=1), True, "REQUEST_LIMIT"),
                                          (AIOptions(mode="live", cost_limit_usd=.000001), False, "COST_LIMIT")):
            self.ledger = UsageLedger()
            if prior:
                self.ledger.reserve(options, DEFAULT_OPENAI_MODEL, .001, NOW.isoformat())
            with patch("services.ai_analysis_service.request_analysis") as request, self.assertRaises(AIError) as caught:
                self.run_analysis(options=options)
            self.assertEqual(caught.exception.category, category)
            request.assert_not_called()
        with patch("services.ai_analysis_service.estimate_cost", return_value=None), self.assertRaises(AIError) as caught:
            self.ledger = UsageLedger()
            self.run_analysis()
        self.assertEqual(caught.exception.category, "PRICE_UNKNOWN")

    def test_limited_retry_reserves_each_attempt_and_timeout_does_not_retry(self):
        for category, expected_calls in (("RATE_LIMIT", 2), ("SERVER_ERROR", 2), ("TIMEOUT", 1), ("AUTHENTICATION", 1)):
            self.ledger = UsageLedger()
            with self.subTest(category=category), patch("services.ai_analysis_service.time.sleep"), \
                    patch("services.ai_analysis_service.request_analysis", side_effect=[AIError("safe error", category), ClientResult(self.report, self.usage, DEFAULT_OPENAI_MODEL)]) as request:
                if expected_calls == 1:
                    with self.assertRaises(AIError):
                        self.run_analysis(options=AIOptions(mode="live", retries=1))
                else:
                    result = self.run_analysis(options=AIOptions(mode="live", retries=1))
                    self.assertEqual(result.status, "VALIDATED")
                self.assertEqual(request.call_count, expected_calls)
                self.assertEqual(len(self.ledger.records), expected_calls)

    def test_semantic_failure_keeps_usage_and_cost_and_no_result(self):
        report = self.report.model_copy(update={"conclusion": "수익률 999% 보장"})
        with patch("services.ai_analysis_service.request_analysis", return_value=ClientResult(report, self.usage, DEFAULT_OPENAI_MODEL)), self.assertRaises(AIError):
            self.run_analysis()
        self.assertEqual(self.ledger.records[0]["status"], "EVIDENCE_VALIDATION")
        self.assertIsNotNone(self.ledger.records[0]["usage"])

    def test_price_expiry_unknown_model_and_option_validation(self):
        self.assertIsNone(price_for("not-a-model"))
        verified = MODEL_PRICES[DEFAULT_OPENAI_MODEL]["verified_on"]
        self.assertIsNone(price_for(DEFAULT_OPENAI_MODEL, today=verified+timedelta(days=31)))
        self.assertIsNotNone(price_for(DEFAULT_OPENAI_MODEL, today=verified))
        for kwargs in ({"max_requests": 0}, {"cost_limit_usd": -1}, {"cost_limit_usd": "bad"},
                       {"max_output_tokens": 99999}, {"retries": 2}, {"mode": "unknown"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(AIError):
                AIOptions(**kwargs)
