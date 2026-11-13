"""노드는 담당 필드의 갱신만 반환한다. 예외 원문/원본 입력은 로그에 넣지 않는다."""

import time

import pandas as pd
from langgraph.runtime import Runtime
from pydantic import ValidationError

from agents.research import RuleTechnicalAgent, RuleMacroAgent
from config.settings import TIMEFRAME_MILLISECONDS, SUPPORTED_SYMBOLS
from graph.state import (NODES, WorkflowContext, WorkflowState, NodeLog, WorkflowError,
                         TechnicalResult, MacroResult, AggregationResult, WorkflowReport, AgentExecution)
from graph.validation import WorkflowValidationError, validate_technical, validate_macro
from llm.config import AIError
from llm.schemas import MarketSnapshot
from services.ai_inputs import build_market_snapshot, summarize_macro_frames, snapshot_fingerprint, iso

WORKFLOW_LIMITATIONS = [
    "실제 LangGraph 실행 위에서 동작하는 규칙 기반 Mock 분석이며 복수 LLM 협업/토론이 아닙니다.",
    "거시 시점 제약을 해소하지 않았으며 기술 요약을 실제 매매 지시로 사용하지 않습니다.",
]
CORE_COLUMNS = {"rsi_14", "macd", "macd_signal", "macd_histogram", "ema_20", "ema_60", "sma_20", "sma_60"}


def utc_now():
    return pd.Timestamp.now(tz="UTC").isoformat()


def tracked(name, function):
    def node(state: WorkflowState, runtime: Runtime[WorkflowContext]):
        started, clock = utc_now(), time.perf_counter()
        begin = NodeLog(node=name, status="RUNNING", started_at_utc=started, ended_at_utc=None)
        try:
            update = function(state, runtime.context)
            skipped = update.pop("_skipped", False)
            failed = update.pop("_failed", False)
            error_type = update.pop("_error_type", None)
        except Exception as error:
            if isinstance(error, AIError):
                category, message = error.category, "자료·API·비용 또는 응답 검증에 실패했습니다. 오류 유형을 확인하세요."
            elif name == "input_validator":
                category, message = "INPUT_VALIDATION", "입력의 종목·주기·확정 여부·시각·필수 지표/수치를 확인하세요."
            elif isinstance(error, (WorkflowValidationError, ValidationError)):
                category, message = "EVIDENCE_VALIDATION", "노드 결과가 입력 근거 또는 출력 계약 검증에 실패했습니다."
            else:
                category, message = "NODE_ERROR", "노드 처리 중 오류가 발생했습니다. 이 노드 결과는 사용하지 않습니다."
            update = {"errors": [WorkflowError(node=name, category=category, message=message)]}
            if name in ("input_validator", "research_aggregator", "report_formatter"):
                update["execution_status"] = "FAILED"
            end = NodeLog(node=name, status="FAILED", started_at_utc=started, ended_at_utc=utc_now(),
                          duration_ms=(time.perf_counter()-clock)*1000, error_type=category)
        else:
            end = NodeLog(node=name, status="FAILED" if failed else "SKIPPED" if skipped else "COMPLETED", started_at_utc=started,
                          ended_at_utc=utc_now(), duration_ms=(time.perf_counter()-clock)*1000, error_type=error_type)
        if name in ("technical_agent", "macro_agent"):
            key = "technical_execution" if name == "technical_agent" else "macro_execution"
            session = runtime.context.llm_session
            update[key] = (session.execution(name, end.error_type) if session else AgentExecution(
                execution_mode="Mock Workflow", model=None, api_called=False,
                validation_status="FAILED" if end.status == "FAILED" else "RULE_VALIDATED",
                usage=None, estimated_cost_usd=0., reserved_cost_usd=0., error_type=end.error_type))
        update["node_execution_logs"] = [begin, end]
        return update
    return node


def validate_input(state, context):
    symbol, timeframe = state["symbol"], state["timeframe"]
    if symbol not in SUPPORTED_SYMBOLS or timeframe not in TIMEFRAME_MILLISECONDS:
        raise WorkflowValidationError("지원하지 않는 입력")
    data = context.market_data
    if not isinstance(data, pd.DataFrame) or not CORE_COLUMNS.issubset(data.columns) or data.empty:
        raise WorkflowValidationError("필수 지표 컬럼이 없는 입력")
    if (data.attrs.get("symbol", symbol) != symbol or data.attrs.get("timeframe", timeframe) != timeframe
            or data.attrs.get("technical_indicators", {}).get("timeframe") not in (None, timeframe)):
        raise WorkflowValidationError("수집/계산 메타데이터의 종목·봉과 요청이 다름")
    times = data["timestamp"]
    interval = pd.Timedelta(milliseconds=TIMEFRAME_MILLISECONDS[timeframe])
    if times.dt.tz is None or (times+interval > pd.Timestamp(context.requested_at_utc)).any():
        raise WorkflowValidationError("미확정/미래 봉 또는 시각 오류")
    # 가격/기술 입력만 검증한다. Macro의 오류가 이 검증과 Technical 분기를 막지 않는다.
    snapshot = build_market_snapshot(data, symbol, timeframe, now=context.requested_at_utc)
    snapshot = MarketSnapshot.model_validate(snapshot)
    if CORE_COLUMNS.intersection(snapshot.missing_metrics):
        snapshot.sufficient = False
    if len({fact.reference_id for fact in snapshot.facts}) != len(snapshot.facts):
        raise WorkflowValidationError("중복 근거")
    if context.llm_session is not None:
        context.llm_session.prepare(snapshot, context.macro_frames, context.requested_at_utc, state["run_id"])
    return {"input_data": snapshot, "input_fingerprint": snapshot_fingerprint(snapshot),
            "data_as_of_utc": snapshot.data_as_of_utc, "execution_status": "RUNNING"}


def technical_node(agent):
    def analyze(state, context):
        snapshot = state["input_data"]
        # 원본 DataFrame은 복사하지 않는다. 작은 요약만 격리해 어댑터의 상태 변경을 방지한다.
        result = (context.llm_session.technical(snapshot.model_copy(deep=True)) if context.llm_session else
                  agent.analyze(snapshot.model_copy(deep=True)))
        return {"technical_result": validate_technical(result, snapshot, state.get("execution_mode", "Mock Workflow"))}
    return tracked("technical_agent", analyze)


def macro_node(agent):
    def analyze(state, context):
        facts, metadata = summarize_macro_frames(context.macro_frames, as_of=state["data_as_of_utc"],
                                                now=context.requested_at_utc, include_quality=True)
        copied_facts, copied_metadata = [fact.model_copy(deep=True) for fact in facts], [dict(item) for item in metadata]
        result = (context.llm_session.macro(copied_facts, copied_metadata) if context.llm_session else
                  agent.analyze(copied_facts, copied_metadata))
        return {"macro_input": facts, "macro_metadata": metadata,
                "macro_result": validate_macro(result, facts, metadata, state.get("execution_mode", "Mock Workflow"))}
    return tracked("macro_agent", analyze)


def aggregate(state, context):
    terminal = {log.node: log.status for log in state["node_execution_logs"] if log.status in ("COMPLETED", "FAILED")}
    if not {"technical_agent", "macro_agent"}.issubset(terminal):
        raise WorkflowValidationError("분기 합류 전 실행")
    snapshot = state["input_data"]
    if snapshot_fingerprint(snapshot) != state["input_fingerprint"]:
        raise WorkflowValidationError("입력 상태 변경")
    technical, macro = state.get("technical_result"), state.get("macro_result")
    mode = state.get("execution_mode", "Mock Workflow")
    if technical is not None:
        technical = validate_technical(technical, snapshot, mode)
    if macro is not None:
        macro = validate_macro(macro, state["macro_input"], state["macro_metadata"], mode)
    risks, missing = list(snapshot.risk_notes), list(snapshot.missing_metrics)
    limitations = (list(WORKFLOW_LIMITATIONS) if mode == "Mock Workflow" else [
        "역할별 LLM 응답을 검증하고 Python Aggregator로 통합합니다. 토론·다수결·추가 집계 LLM 호출은 없습니다.",
        "시점 미검증 Macro는 방향 판단이나 과거 백테스트 입력에 사용하지 않습니다."])
    conflicts = []
    evidence = []
    if technical is None:
        status, signal, summary = "FAILED", None, "Technical Agent 실행/검증 실패로 최종 방향 판단을 생성하지 않습니다."
        missing.append("Technical Agent 실행 실패 (자료 부족과 구분)")
    else:
        status = "COMPLETED" if macro is not None else "PARTIAL"
        evidence.extend(technical.evidence)
        risks.extend(technical.risk_factors)
        limitations.extend(technical.limitations)
        signal = technical.technical_signal
        if technical.bullish_factors and technical.bearish_factors:
            conflicts.append("기술적 상승·하락 근거가 동시에 있습니다. 근거 개수로 표결하지 않고 중립을 유지합니다.")
            if signal != "INSUFFICIENT_DATA":
                signal = "NEUTRAL"
        summary = ("기술 요약을 통합했으며 Macro는 현재 관측 문맥만 제공합니다. 시점 미검증 Macro를 방향 근거로 사용하지 않습니다."
                   if macro is not None else "Macro Agent 실행 실패를 명시하고 검증된 Technical 결과만 제한적으로 유지합니다.")
        if not snapshot.sufficient:
            missing.append("연속 확정 봉/핵심 기술 지표 부족")
    if macro is not None:
        evidence.extend(macro.macro_evidence)
        risks.extend(macro.macro_risk_factors)
        limitations.extend(macro.macro_limitations)
        if macro.macro_status == "INSUFFICIENT_DATA":
            missing.append("선택·조회된 거시경제 자료 부족")
    else:
        missing.append("Macro Agent 실행 실패 (자료 부족과 구분)")
        risks.append("Macro Agent 결과가 없어 통합 분석이 부분 완료 상태입니다.")
    return {"aggregation_result": AggregationResult(status=status, final_signal=signal, summary=summary,
            conflicts=conflicts, risk_factors=risks, missing_data=list(dict.fromkeys(missing)),
            evidence=evidence, limitations=list(dict.fromkeys(limitations)))}


def format_report(state, context):
    result = state.get("aggregation_result")
    if result is None or result.final_signal is None:
        return {"final_report": None, "execution_status": "FAILED", "_skipped": True}
    technical, macro = state["technical_result"], state.get("macro_result")
    report = WorkflowReport(
        run_id=state["run_id"], symbol=state["symbol"], timeframe=state["timeframe"], execution_mode=state.get("execution_mode", "Mock Workflow"),
        execution_status=result.status, data_as_of_utc=state["data_as_of_utc"], analyzed_at_utc=context.requested_at_utc,
        final_signal=result.final_signal, technical_summary=technical.technical_summary,
        macro_summary=macro.macro_summary if macro else "Macro Agent 실행 실패: 정상적인 거시 분석 결과가 없습니다.",
        bullish_factors=technical.bullish_factors, bearish_factors=technical.bearish_factors, conflicts=result.conflicts,
        risk_factors=result.risk_factors, missing_data=result.missing_data, evidence=result.evidence, limitations=result.limitations,
        started_at_utc=context.requested_at_utc, completed_at_utc=utc_now(), duration_ms=(time.perf_counter()-context.started_monotonic)*1000,
    )
    return {"final_report": report, "execution_status": result.status}
