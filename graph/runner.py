"""워크플로우 실행/종료 추적. LLM은 버튼·비용 승인 게이트 뒤에서만 허용하며 원격 추적은 끈다."""

from dataclasses import dataclass
import time
from uuid import uuid4

import pandas as pd
from langsmith import tracing_context

from graph.state import NODES, WorkflowContext, WorkflowState, NodeLog, WorkflowError
from graph.workflow import get_workflow
from graph.nodes import utc_now
from services.ai_inputs import iso
from config.settings import DEBATE_DEFAULT_ROUNDS, DEBATE_SUPPORTED_ROUNDS


@dataclass(frozen=True)
class WorkflowExecution:
    state: WorkflowState
    duration_ms: float
    completed_at_utc: str


def run_workflow(data, symbol, timeframe, *, macro_frames=None, now=None, workflow=None,
                 mode="Mock Workflow", config=None, options=None, ledger=None, requested=False, cost_approved=False,
                 debate_enabled=False, debate_rounds=DEBATE_DEFAULT_ROUNDS, cancel_event=None,
                 risk_enabled=False, risk_settings=None, risk_thresholds=None):
    from llm.config import AIError
    if type(debate_enabled) is not bool:
        raise AIError("Debate 활성화 설정이 유효하지 않습니다.", "CONFIGURATION")
    if type(risk_enabled) is not bool:
        raise AIError("Risk Manager 활성화 설정이 유효하지 않습니다.", "CONFIGURATION")
    if risk_enabled:
        if mode != "Mock Workflow":
            raise AIError("Mock Risk Manager는 Mock Workflow에서만 지원합니다. 기존 LLM 기능은 Risk Manager를 끄고 사용하세요.", "CONFIGURATION")
        if requested is not True:
            raise AIError("Risk Manager 분석 실행 버튼 입력이 필요합니다.", "NOT_REQUESTED")
        from risk.models import PositionSettings, RiskThresholds
        from pydantic import ValidationError
        try:
            risk_settings = PositionSettings.model_validate(risk_settings or PositionSettings())
            risk_thresholds = RiskThresholds.model_validate(risk_thresholds or RiskThresholds())
        except (ValidationError, TypeError, ValueError):
            raise AIError("가상 리스크 파라미터 또는 연구 임계값이 유효하지 않습니다.", "CONFIGURATION") from None
    if debate_enabled:
        if type(debate_rounds) is not int or debate_rounds not in DEBATE_SUPPORTED_ROUNDS:
            raise AIError("Mock Debate는 1~2라운드만 지원합니다.", "CONFIGURATION")
        if mode != "Mock Workflow":
            raise AIError("Mock Debate는 Mock Workflow에서만 실행합니다. 기존 LLM Workflow는 Debate를 끄고 실행하세요.", "CONFIGURATION")
        if requested is not True:
            raise AIError("Mock Debate 실행 버튼 입력이 필요합니다.", "NOT_REQUESTED")
    session = None
    if mode == "LLM Workflow":
        from agents.llm_agents import WorkflowLLMSession, validate_llm_access
        validate_llm_access(config, options, ledger, requested=requested, cost_approved=cost_approved)
        session = WorkflowLLMSession(config, options, ledger)
    elif mode != "Mock Workflow":
        raise AIError("지원하지 않는 워크플로우 실행 모드입니다.", "CONFIGURATION")
    started = time.perf_counter()
    requested_at = iso(pd.Timestamp.now(tz="UTC") if now is None else now)
    context = WorkflowContext(data, macro_frames or {}, requested_at, started, session)
    nodes = NODES
    if debate_enabled:
        from graph.debate_state import DebateContext, DEBATE_NODES, initial_debate_state
        from graph.debate_workflow import get_debate_workflow
        from graph.debate_nodes import finalize_debate
        context = DebateContext(market_data=data, macro_frames=macro_frames or {}, requested_at_utc=requested_at,
                                started_monotonic=started, rounds=debate_rounds, guard=ledger, cancel_event=cancel_event)
        nodes = DEBATE_NODES
    if risk_enabled:
        from graph.risk_state import RiskContext
        context = RiskContext(market_data=data, macro_frames=macro_frames or {}, requested_at_utc=requested_at,
            started_monotonic=started, rounds=debate_rounds, guard=ledger, cancel_event=cancel_event,
            risk_settings=risk_settings, risk_thresholds=risk_thresholds)
        nodes = (*nodes[:-2], "risk_manager", *nodes[-2:])
    state: WorkflowState = {
        "run_id": str(uuid4()), "symbol": symbol, "timeframe": timeframe, "execution_mode": mode, "input_data": None,
        "technical_execution": None, "macro_execution": None,
        "data_as_of_utc": None, "technical_result": None, "macro_result": None, "macro_input": [], "macro_metadata": [],
        "aggregation_result": None, "final_report": None, "execution_status": "WAITING", "errors": [],
        "node_execution_logs": [NodeLog(node=name, status="WAITING", started_at_utc=None, ended_at_utc=None) for name in nodes],
    }
    if debate_enabled:
        state.update(initial_debate_state(debate_rounds))
    if risk_enabled:
        state.update(risk_manager_enabled=True, risk_input=None, risk_result=None, research_action=None,
                     pre_risk_signal=None, risk_manager_status="WAITING")
    try:
        # 환경에 추적 키가 있어도 이 실행은 외부 LangSmith 전송을 하지 않는다. 전역 환경은 변경하지 않는다.
        with tracing_context(enabled=False):
            if workflow is not None:
                compiled = workflow
            elif debate_enabled:
                compiled = get_debate_workflow(risk_enabled=True) if risk_enabled else get_debate_workflow()
            else:
                compiled = get_workflow(risk_enabled=True) if risk_enabled else get_workflow()
            for update in compiled.stream(state, context=context, stream_mode="values",
                                          config={"max_concurrency": 2, "recursion_limit": 12, "callbacks": []}):
                state = update
            if debate_enabled:
                state = finalize_debate(state)
            if state["execution_status"] not in ("COMPLETED", "PARTIAL", "FAILED"):
                raise RuntimeError("Workflow did not reach a terminal status")
    except Exception:
        state = {**state, "execution_status": "FAILED", "final_report": None,
                 "errors": [*state["errors"], WorkflowError(node="graph_runner", category="GRAPH_ERROR",
                                                           message="그래프 실행이 중단됐습니다. 정상 완료 결과로 표시하지 않습니다.")]}
    finally:
        if session is not None:
            session.close()
    ended, duration = utc_now(), (time.perf_counter()-started)*1000
    logs = list(state["node_execution_logs"])
    interrupted = any(error.node == "graph_runner" for error in state["errors"])
    for name in nodes:
        events = [log for log in logs if log.node == name]
        if events[-1].status in ("WAITING", "RUNNING"):
            logs.append(NodeLog(node=name, status="FAILED" if interrupted else "SKIPPED", started_at_utc=None, ended_at_utc=ended,
                                error_type="GRAPH_ERROR" if interrupted else "PREDECESSOR_FAILED"))
    state = {**state, "node_execution_logs": logs}
    if state.get("final_report") is not None:
        state["final_report"] = state["final_report"].model_copy(update={"duration_ms": duration, "completed_at_utc": ended})
    if debate_enabled:
        state = finalize_debate(state)
    return WorkflowExecution(state, duration, ended)
