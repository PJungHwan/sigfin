"""단일 LLM과 분리된 Mock/LLM LangGraph 실행. 버튼·비용 승인 게이트와 공유 원장."""

import hashlib
import json
from dataclasses import asdict

import pandas as pd
import streamlit as st

from config.settings import APP_VERSION, DEBATE_DEFAULT_ROUNDS, DEBATE_SUPPORTED_ROUNDS
from config.llm_settings import AI_MAX_INPUT_BYTES
from llm.budget import AIOptions, UsageLedger, estimate_cost, price_for
from llm.config import AIError, read_openai_config
from ui.ai_analysis_view import _usage
from graph.runner import run_workflow
from graph.state import NODES
from graph.workflow import get_workflow
from ui.components import agent_status
from ui.risk_manager_view import render_risk_manager_controls, render_risk_manager

LABELS = {"input_validator": "Input Validator", "technical_agent": "Technical Agent",
          "macro_agent": "Macro Agent", "research_aggregator": "Research Aggregator", "report_formatter": "Report Formatter",
          "evidence_preparation": "Evidence Preparation", "bull_agent": "Bull Agent", "bear_agent": "Bear Agent",
          "debate_round": "Debate Round", "debate_evaluator": "Debate Evaluator", "risk_manager": "Risk Manager"}


def render_workflow_controls():
    mode = st.selectbox("워크플로우 실행 모드", ("Mock Workflow", "LLM Workflow"), key="workflow_mode", persist_state="session")
    debate = st.checkbox("Bull/Bear Debate 활성화", key="debate_enabled", persist_state="session")
    risk_enabled, risk_settings = render_risk_manager_controls()
    if risk_enabled and mode != "Mock Workflow":
        st.warning("Mock Risk Manager는 Mock Workflow에서만 지원합니다. 기존 LLM 실행은 Risk Manager를 끄고 사용하세요.")
    rounds = DEBATE_DEFAULT_ROUNDS
    if debate:
        rounds = st.selectbox("토론 라운드 수", DEBATE_SUPPORTED_ROUNDS,
                              index=DEBATE_SUPPORTED_ROUNDS.index(DEBATE_DEFAULT_ROUNDS), key="debate_rounds", persist_state="session")
        st.caption("Debate 모드: Mock Debate · Technical/Macro도 Mock으로 실행 · 1라운드 주장 / 2라운드 상대 주장 검토 · 유료 API 미호출")
        if mode != "Mock Workflow":
            st.warning("Mock Debate는 Mock Workflow에서만 지원합니다. Mock Workflow를 선택하거나 Debate를 끄고 기존 LLM Workflow를 실행하세요.")
    config, options, approved = None, None, False
    if mode == "Mock Workflow":
        st.info("실제 LangGraph에서 규칙 기반 Mock 노드를 실행합니다. 실제 LLM 협업·LLM Debate가 아니며 유료 API 호출이 없습니다.")
    else:
        try:
            config = read_openai_config()
            options = AIOptions("live", st.session_state.get("ai_request_limit", 5), st.session_state.get("ai_cost_limit", .25),
                                st.session_state.get("ai_output_limit", 3000), 0)
        except AIError as error:
            st.error(f"LLM Workflow · {error.category}: {error}")
        st.warning("Technical/Macro별 OpenAI 유료 호출이 최대 두 번 발생합니다. 요약 데이터를 OpenAI에 전송하며 실패 요청의 과금 여부는 미확인입니다.")
        st.caption(f"모델: {config.model if config else '설정 오류'} · Macro 유효 자료가 없으면 해당 호출 생략 · Aggregator는 Python 규칙 기반")
        if config and not config.api_key:
            st.info("OPENAI_API_KEY가 없습니다. 본인의 .env 키를 설정하거나 기본 Mock Workflow를 사용하세요.")
        price = None if not config else price_for(config.model)
        if price is None:
            st.info("가격 미확인 모델은 호출을 차단합니다.")
        elif options:
            from llm.agent_contracts import TECH_PROMPT, MACRO_PROMPT, TechnicalLLMOutput, MacroLLMOutput, token_reservation
            upper = sum(estimate_cost(config.model, AI_MAX_INPUT_BYTES+token_reservation(prompt, "", schema), options.max_output_tokens)
                        for prompt, schema in ((TECH_PROMPT, TechnicalLLMOutput), (MACRO_PROMPT, MacroLLMOutput)))
            st.caption(f"워크플로우 예약 추정 최대 약 ${upper:.5f} USD · 가격 확인일 {price['verified_on']} · 정확한 토큰 수/청구액 아님")
        st.caption("단일 AI와 동일 세션 원장을 공유합니다. ‘AI 요청·비용 설정’의 기본 5회/$0.25 상한을 각 HTTP 시도에 적용합니다. "
                   "병렬 호출 전에 전체 비용을 예약하며 자동 재시도는 0회입니다. 이 한도는 OpenAI 계정 결제 한도를 대체하지 않습니다.")
        signature = (None if not config else (config.model, config.scope), None if not options else tuple(asdict(options).items()),
                     st.session_state.get("symbol"), st.session_state.get("timeframe"), st.session_state.get("period"))
        if st.session_state.get("_workflow_approval_signature") != signature:
            st.session_state["workflow_cost_approved"] = False
            st.session_state["_workflow_approval_signature"] = signature
        approved = st.checkbox("현재 LLM Workflow 모델·출력 제한·공유 세션 상한의 유료 요청 비용을 승인합니다.", key="workflow_cost_approved", persist_state="session")
    requested = st.button("Multi-Agent 분석 실행", key="run_multi_agent", width="stretch", type="primary",
        disabled=(debate or risk_enabled) and mode != "Mock Workflow" or risk_enabled and risk_settings is None)
    with st.expander("LangGraph 워크플로우", expanded=False):
        # 원격 Mermaid 이미지 렌더링 API를 호출하지 않는다.
        if debate:
            from graph.debate_workflow import get_debate_workflow
            graph = get_debate_workflow(risk_enabled=True) if risk_enabled else get_debate_workflow()
        else:
            graph = get_workflow(risk_enabled=True) if risk_enabled else get_workflow()
        st.mermaid_chart(graph.get_graph().draw_mermaid(), width="stretch")
        st.caption("Input → Technical/Macro 병렬 → 근거 준비 → Bull/Bear 병렬 → 상대 주장 검토 → Evaluator → 집계 → 리포트" if debate else
                   "Input 검증 성공 후 Technical/Macro 병렬 분기 → 두 분기의 종료를 기다리는 합류 → 집계 → 리포트. 입력 검증 실패는 END로 종료합니다.")
        if risk_enabled:
            st.caption("Mock Risk Manager는 분석/토론 결과가 준비된 뒤, Research Aggregator 직전에 실행됩니다. 위험 등급과 시장 방향은 별도로 표시합니다.")
    return requested, mode, config, options, approved, debate, rounds, risk_enabled, risk_settings


def input_signature(data, symbol, timeframe, period, selected, frames, *, include_collection_time=True):
    """기본 Workflow는 수집 버전까지 구분한다. 저장된 연구 보고서는 동일 관측의 재수집만 구분하지 않는다."""
    digest = hashlib.sha256()
    digest.update(json.dumps((symbol, timeframe, period, selected, APP_VERSION), ensure_ascii=False).encode())
    for frame in ([data] if data is not None else [])+[frames[key] for key in selected if key in frames and isinstance(frames[key], pd.DataFrame)]:
        digest.update(pd.util.hash_pandas_object(frame, index=True).values.tobytes())
        digest.update(str(tuple(frame.columns)).encode())
        # 키/임의 메타데이터는 상태나 로그에 저장하지 않는다.
        metadata_keys = ("is_partial", "missing_candles", "version_policy", "timeframe")
        if include_collection_time:
            metadata_keys = ("fetched_at",)+metadata_keys
        metadata = {key: str(frame.attrs.get(key)) for key in metadata_keys}
        digest.update(json.dumps(metadata, sort_keys=True).encode())
    return digest.hexdigest()


def _show_report(report):
    mode_label = report.execution_mode+(" / Mock Debate" if hasattr(report, "debate_mode") else "")
    st.markdown(f"**최종 분석 의견 · {mode_label}: {report.final_signal}**")
    if hasattr(report, "research_action"):
        st.markdown(f"**별도 위험 검토 상태: {report.research_action} · {report.risk_manager_status}**")
    st.caption(f"실행 ID {report.run_id} · {report.symbol} · {report.timeframe} · {report.execution_status} · "
               f"자료 마감 {report.data_as_of_utc} · 실행 시간 {report.duration_ms:.2f} ms")
    st.caption(f"시작 {report.started_at_utc} · 종료 {report.completed_at_utc} · {report.execution_mode}")
    for title, content in (("기술적 분석 요약", report.technical_summary), ("거시경제 분석 요약", report.macro_summary)):
        st.markdown(f"**{title}**")
        st.text(content)
    for title, items in (("상승 근거", report.bullish_factors), ("하락 근거", report.bearish_factors),
                          ("상충 근거", report.conflicts), ("위험 요인", report.risk_factors),
                          ("데이터 부족 항목", report.missing_data), ("분석의 한계", report.limitations)):
        with st.expander(f"워크플로우 · {title}", expanded=title == "상충 근거"):
            if not items:
                st.caption("해당 항목 없음")
            for item in items:
                st.text("• "+item)
    with st.expander("워크플로우 · 출처와 관측 시각", expanded=False):
        st.dataframe(pd.DataFrame([item.model_dump() for item in report.evidence]), hide_index=True, width="stretch")


def render_workflow_result(data, symbol, timeframe, period, selected_macro, requested, technical_slot, macro_slot,
                           bull_slot=None, bear_slot=None, risk_slot=None):
    requested, mode, config, options, approved, debate, rounds, risk_enabled, risk_settings = requested
    if risk_enabled and risk_slot is not None:
        with risk_slot:
            agent_status("Risk Manager", "WAITING")
    if debate:
        for title, slot in (("Bull Agent", bull_slot), ("Bear Agent", bear_slot)):
            if slot is not None:
                with slot:
                    agent_status(title, "WAITING")
    ledger = st.session_state.setdefault("_ai_ledger", UsageLedger())
    frames = st.session_state.get("_macro_results", {})
    macro = {key: frames[key] for key in selected_macro if key in frames and isinstance(frames[key], pd.DataFrame)}
    signature = (input_signature(data, symbol, timeframe, period, selected_macro, frames), mode,
                 None if config is None else (config.model, config.scope), None if options is None else tuple(asdict(options).items()), debate, rounds,
                 risk_enabled, risk_settings.model_dump_json() if risk_enabled and risk_settings is not None else None)
    if st.session_state.get("_workflow_signature") != signature:
        if "_workflow_execution" in st.session_state:
            st.info("워크플로우 입력 데이터·종목·기간·봉·거시 자료 또는 Debate/Risk Manager 설정이 변경되어 이전 결과를 무효화했습니다. 다시 실행해 주세요.")
        st.session_state.pop("_workflow_execution", None)
        st.session_state.pop("_debate_last_attempt", None)
        st.session_state["_workflow_signature"] = signature
    if requested:
        previous = st.session_state.get("_workflow_execution")
        st.session_state.pop("_workflow_execution", None)
        st.session_state.pop("_debate_last_attempt", None)
        with st.spinner(f"LangGraph {mode}를 실행합니다..."):
            try:
                execution = run_workflow(data, symbol, timeframe, macro_frames=macro,
                    mode=mode, config=config, options=options, ledger=ledger, requested=True, cost_approved=approved,
                    debate_enabled=debate, debate_rounds=rounds, risk_enabled=risk_enabled, risk_settings=risk_settings)
                if debate and previous is not None and any(error.category == "DUPLICATE_REQUEST" for error in execution.state["errors"]):
                    st.session_state["_debate_last_attempt"] = execution
                    st.session_state["_workflow_execution"] = previous
                else:
                    st.session_state["_workflow_execution"] = execution
            except AIError as error:
                st.error(f"LLM Workflow · {error.category}: {error}")
        _usage(ledger)
    execution = st.session_state.get("_workflow_execution")
    if execution is None:
        st.caption("현재 입력의 워크플로우 결과가 없습니다. 실행 버튼을 눌러 주세요.")
        return
    state = execution.state
    blocked_attempt = st.session_state.get("_debate_last_attempt")
    if blocked_attempt is not None:
        st.warning("동일 토론의 중복 실행을 차단했습니다. 아래에는 이전 실행 결과와 당시 상태를 유지합니다.")
        with st.expander("차단된 토론 시도 기록"):
            st.caption("run_id: "+blocked_attempt.state["run_id"])
            st.dataframe(pd.DataFrame([log.model_dump() for log in blocked_attempt.state["node_execution_logs"]]), hide_index=True)
    status = state["execution_status"]
    if status == "FAILED":
        st.error("LangGraph 워크플로우 실행/검증 실패입니다. 정상 완료 리포트로 표시하지 않습니다.")
    elif status == "PARTIAL":
        st.warning("LangGraph 부분 완료: 실패한 노드와 사용할 수 있는 결과를 구분해 표시합니다.")
    else:
        st.success(("LangGraph Mock Debate 실행 완료 · 유료 API 미호출" if state.get("debate_status") == "COMPLETED" else
                    "Mock Workflow 처리 완료 · Debate 상태 별도 확인") if debate else
                   "LangGraph Mock Workflow 실행 완료 · 유료 API 미호출" if mode == "Mock Workflow" else
                   "LLM Workflow 처리 완료 · 아래 에이전트별 실제 호출 여부와 자료 충분성을 확인하세요.")
    latest = {log.node: log for log in state["node_execution_logs"]}
    if risk_enabled and risk_slot is not None:
        with risk_slot:
            agent_status("Risk Manager", latest["risk_manager"].status)
    for node, slot in (("technical_agent", technical_slot), ("macro_agent", macro_slot)):
        with slot:
            agent_status(LABELS[node], status=latest[node].status)
    if debate:
        for node, slot in (("bull_agent", bull_slot), ("bear_agent", bear_slot)):
            if slot is not None:
                with slot:
                    agent_status(LABELS[node], status=latest[node].status)
    cols = st.columns(3)
    for column, node in zip(cols, ("input_validator", "research_aggregator", "report_formatter")):
        with column:
            agent_status(LABELS[node], status=latest[node].status)
    st.caption("아래 상태와 시간은 실행 후 기록입니다. 실시간 진행 화면이 아닙니다. 노드 COMPLETED와 자료 충분성은 서로 다릅니다.")
    executions = []
    for role in ("technical", "macro"):
        detail = state.get(role+"_execution")
        if detail is not None:
            executions.append({"agent": role, **detail.model_dump(), "duration_ms": latest[role+"_agent"].duration_ms})
    st.dataframe(pd.DataFrame(executions), hide_index=True, width="stretch")
    st.caption("api_called=False는 실제 LLM 미호출입니다. 사용량 미확인 요청은 예약 비용을 유지하며 실제 청구액을 확정하지 않습니다.")
    for key, title in (("technical_result", "Technical Agent 결과"), ("macro_result", "Macro Agent 결과"),
                        ("aggregation_result", "Research Aggregator 결과")):
        with st.expander(title, expanded=False):
            result = state.get(key)
            if result is None:
                st.caption("실행 실패 또는 미실행으로 유효한 결과가 없습니다.")
            else:
                st.json(result.model_dump())
            if key == "macro_result" and state.get("macro_metadata"):
                st.dataframe(pd.DataFrame(state["macro_metadata"]), hide_index=True, width="stretch")
                st.caption("발표/빈티지 값이 입력에 존재하더라도 출처·당시 가용성이 확인되지 않으면 방향 판단에 사용하지 않습니다.")
    report = state.get("final_report")
    if debate:
        from ui.debate_view import render_debate
        render_debate(state)
    if risk_enabled:
        render_risk_manager(state)
    if report is not None:
        _show_report(report)
    with st.expander("워크플로우 실행 로그", expanded=False):
        st.caption(f"run_id: {state['run_id']} · 전체 경과 {execution.duration_ms:.2f} ms · 모든 시각 UTC")
        st.dataframe(pd.DataFrame([log.model_dump() for log in latest.values()]), hide_index=True, width="stretch")
        events = sorted(state["node_execution_logs"], key=lambda log: log.ended_at_utc or log.started_at_utc or "")
        st.dataframe(pd.DataFrame([log.model_dump() for log in events]), hide_index=True, width="stretch")
        for error in state["errors"]:
            st.error(f"{LABELS.get(error.node, error.node)} · {error.category}: {error.message}")
