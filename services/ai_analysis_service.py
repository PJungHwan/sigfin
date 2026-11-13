"""Streamlit 독립적인 실행 게이트·예산·근거 검증. 자동 수집/주문/백테스트 호출 없음."""

from dataclasses import dataclass
import hashlib
import time

import pandas as pd

from config.llm_settings import AI_MAX_INPUT_BYTES, SUPPORTED_OPENAI_MODELS
from llm.budget import AIOptions, UsageLedger, estimate_cost
from llm.client import request_analysis
from llm.config import AIError
from llm.prompts import SUMMARIES, CONCLUSIONS, request_payload, input_token_reservation
from llm.schemas import AnalysisReport, Evidence, TokenUsage
from llm.validators import validate_report
from services.ai_inputs import iso, snapshot_fingerprint


@dataclass(frozen=True)
class AnalysisResult:
    report: AnalysisReport
    mode: str
    model: str
    usage: TokenUsage | None
    estimated_cost_usd: float | None
    input_fingerprint: str
    status: str


def mock_report(snapshot, analyzed_at):
    """테스트용 가상 응답. 실제 LLM/성능/예측이 아니며 중립만 사용한다."""
    signal = "NEUTRAL" if snapshot.sufficient else "INSUFFICIENT_DATA"
    bullish = [fact.explanation for fact in snapshot.facts if fact.role == "bullish"][:6]
    bearish = [fact.explanation for fact in snapshot.facts if fact.role == "bearish"][:6]
    selected = set(bullish+bearish)
    evidence = [Evidence(**fact.model_dump(exclude={"role", "unit"})) for fact in snapshot.facts
                if fact.explanation in selected or fact.source_type == "MACRO" or fact.metric_name == "close"]
    return AnalysisReport(symbol=snapshot.symbol, timeframe=snapshot.timeframe, analyzed_at_utc=analyzed_at,
                          data_as_of_utc=snapshot.data_as_of_utc, signal=signal, technical_summary=SUMMARIES[signal],
                          macro_summary=snapshot.macro_summary, bullish_factors=bullish, bearish_factors=bearish,
                          risk_factors=snapshot.risk_notes, evidence=evidence, limitations=snapshot.limitations,
                          conclusion=CONCLUSIONS[signal])


def analyze_market(snapshot, config, options: AIOptions, ledger: UsageLedger, *, requested: bool,
                   cost_approved: bool = False, now=None):
    if requested is not True:
        raise AIError("사용자의 AI 분석 실행 버튼 입력이 필요합니다.", "NOT_REQUESTED")
    analyzed_at = iso(pd.Timestamp.now(tz="UTC") if now is None else now)
    if len(request_payload(snapshot, analyzed_at).encode()) > AI_MAX_INPUT_BYTES:
        raise AIError("AI 입력 요약 크기 제한을 초과했습니다.")
    fingerprint = snapshot_fingerprint(snapshot)
    with ledger.lock:
        if not snapshot.sufficient:
            report = validate_report(mock_report(snapshot, analyzed_at), snapshot, analyzed_at)
            return AnalysisResult(report, options.mode, "LOCAL · 데이터 부족 검사", None, 0., fingerprint, "INSUFFICIENT_DATA")
        if options.mode == "live":
            if cost_approved is not True:
                raise AIError("실 API의 비용 안내를 확인하고 유료 요청을 승인해야 합니다.", "COST_APPROVAL_REQUIRED")
            if not config.api_key:
                raise AIError("OPENAI_API_KEY가 없습니다. .env를 설정하거나 Mock Mode를 사용하세요.", "KEY_MISSING")
            if config.model not in SUPPORTED_OPENAI_MODELS:
                raise AIError("Structured Outputs 지원을 확인하지 못한 모델입니다.", "MODEL_UNVERIFIED")
        key = hashlib.sha256(f"{options.mode}:{config.model}:{config.scope}:{fingerprint}".encode()).hexdigest()
        ledger.check_duplicate(key, remember=False)
        if options.mode == "mock":
            ledger.check_duplicate(key)
            report = validate_report(mock_report(snapshot, analyzed_at), snapshot, analyzed_at)
            return AnalysisResult(report, "mock", "MOCK · 테스트용 가상 응답", None, 0., fingerprint, "MOCK")
        reserved_tokens = input_token_reservation(snapshot, analyzed_at)
        estimate = estimate_cost(config.model, reserved_tokens, options.max_output_tokens)
        for attempt in range(options.retries+1):
            record = ledger.reserve(options, config.model, estimate, analyzed_at)
            if attempt == 0:
                ledger.check_duplicate(key)
            try:
                response = request_analysis(snapshot, analyzed_at, config, options.max_output_tokens)
            except AIError as error:
                ledger.finish(record, status=error.category, usage=error.usage)
                # 불명확한 타임아웃/연결 오류/검증 오류는 자동 재시도하지 않는다.
                if error.category in ("RATE_LIMIT", "SERVER_ERROR") and attempt < options.retries:
                    time.sleep(1)
                    continue
                raise
            ledger.finish(record, status="RESPONSE_RECEIVED", usage=response.usage)
            try:
                report = validate_report(response.report, snapshot, analyzed_at)
            except AIError:
                record["status"] = "EVIDENCE_VALIDATION"
                raise
            record["status"] = "VALIDATED"
            cost = (None if response.usage is None else estimate_cost(config.model, response.usage.input_tokens, response.usage.output_tokens))
            return AnalysisResult(report, "live", response.model, response.usage, cost, fingerprint, "VALIDATED")
    raise AIError("AI 분석 결과가 없습니다.", "RESPONSE_VALIDATION")
