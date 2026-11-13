"""JSON 구조뿐 아니라 원본 값/날짜/근거 문장과 정확히 대조한다."""

from math import isclose

from pydantic import ValidationError

from llm.config import AIError
from llm.prompts import SUMMARIES, CONCLUSIONS
from llm.schemas import AnalysisReport, Evidence


def validate_report(value, snapshot, analyzed_at):
    try:
        report = (AnalysisReport.model_validate_json(value) if isinstance(value, str)
                  else AnalysisReport.model_validate(value))
    except (ValidationError, ValueError, TypeError):
        raise AIError("AI 응답이 JSON/출력 스키마 검증을 통과하지 못했습니다.", "RESPONSE_VALIDATION") from None
    if (report.symbol != snapshot.symbol or report.timeframe != snapshot.timeframe or
            report.data_as_of_utc != snapshot.data_as_of_utc or report.analyzed_at_utc != analyzed_at):
        raise AIError("AI 응답의 종목·주기·분석 시각 또는 데이터 시각이 입력과 다릅니다.", "EVIDENCE_VALIDATION")
    facts = {fact.reference_id: fact for fact in snapshot.facts}
    seen = set()
    for evidence in report.evidence:
        fact = facts.get(evidence.reference_id)
        expected = None if fact is None else Evidence(**fact.model_dump(exclude={"role", "unit"}))
        if (expected is None or evidence.reference_id in seen or
                not isclose(evidence.metric_value, expected.metric_value, rel_tol=0, abs_tol=1e-10) or
                evidence.model_dump(exclude={"metric_value"}) != expected.model_dump(exclude={"metric_value"})):
            raise AIError("AI 응답에 입력에 없는 근거·수치·날짜 또는 설명이 있습니다.", "EVIDENCE_VALIDATION")
        seen.add(evidence.reference_id)
    if (report.technical_summary != SUMMARIES[report.signal] or report.conclusion != CONCLUSIONS[report.signal]
            or report.macro_summary != snapshot.macro_summary or report.risk_factors != snapshot.risk_notes
            or report.limitations != snapshot.limitations):
        raise AIError("AI 응답에 허용하지 않은 수치·사건·자유 서술이 있습니다.", "EVIDENCE_VALIDATION")
    for role, selected in (("bullish", report.bullish_factors), ("bearish", report.bearish_factors)):
        allowed = {fact.explanation: fact.reference_id for fact in snapshot.facts
                   if fact.role == role and fact.source_type == "TECHNICAL"}
        if (len(selected) != len(set(selected)) or any(text not in allowed for text in selected)
                or (snapshot.sufficient and allowed and not selected)):
            raise AIError("상승·하락 요인의 근거가 없거나 상충 근거가 누락됐습니다.", "EVIDENCE_VALIDATION")
        if any(allowed[text] not in seen for text in selected):
            raise AIError("분석 요인에 대응하는 evidence가 없습니다.", "EVIDENCE_VALIDATION")
    required = {fact.reference_id for fact in snapshot.facts if fact.source_type == "MACRO" or fact.metric_name == "close"}
    if not required.issubset(seen):
        raise AIError("가격 또는 거시 설명에 필요한 근거가 누락됐습니다.", "EVIDENCE_VALIDATION")
    if (not snapshot.sufficient and report.signal != "INSUFFICIENT_DATA") or (snapshot.sufficient and report.signal == "INSUFFICIENT_DATA"):
        raise AIError("데이터 충분성 상태와 AI 판단이 일치하지 않습니다.", "EVIDENCE_VALIDATION")
    if ((report.signal == "BULLISH" and len(report.bullish_factors) < 2) or
            (report.signal == "BEARISH" and len(report.bearish_factors) < 2)):
        raise AIError("방향 판단을 뒷받침할 기술적 근거가 부족합니다.", "EVIDENCE_VALIDATION")
    return report
