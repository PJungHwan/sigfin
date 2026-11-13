"""명시적 실행·비용 동의·세션 원장·입력 변경 무효화와 단일 LLM 결과 표시."""

from dataclasses import asdict

import pandas as pd
import streamlit as st

from config.llm_settings import AI_DEFAULT_OUTPUT_TOKENS, AI_MAX_INPUT_BYTES, AI_MAX_OUTPUT_TOKENS
from llm.budget import AIOptions, UsageLedger, estimate_cost, price_for
from llm.config import AIError, OpenAIConfig, read_openai_config
from llm.prompts import input_token_reservation
from services.ai_analysis_service import analyze_market
from services.ai_inputs import build_market_snapshot, snapshot_fingerprint, iso
from ui.components import metric_card


def render_ai_budget_controls():
    with st.expander("AI 요청·비용 설정", expanded=False):
        a, b = st.columns(2)
        maximum = a.number_input("세션 실 API 요청 상한", min_value=1, max_value=100, value=5, step=1, key="ai_request_limit", persist_state="session")
        budget = b.number_input("세션 추정 비용 상한 (USD)", min_value=.01, max_value=100., value=.25, step=.05,
                                format="%.2f", key="ai_cost_limit", persist_state="session")
        a, b = st.columns(2)
        output = a.number_input("응답 출력 토큰 상한", min_value=512, max_value=AI_MAX_OUTPUT_TOKENS,
                                value=AI_DEFAULT_OUTPUT_TOKENS, step=128, key="ai_output_limit", persist_state="session")
        retries = b.selectbox("한도/서버 오류 추가 시도", (0, 1), key="ai_retries", persist_state="session")
        st.caption("공유 세션 한도이며 계정 결제 한도를 대신하지 않습니다. 추가 시도는 단일 AI에만 적용합니다. Workflow 자동 재시도는 0회입니다.")
    return maximum, budget, output, retries


def render_ai_controls(*, budget_controls=True):
    st.caption("단일 LLM 분석 · 에이전트 워크플로우와 별도 실행 · 세션 사용량 원장 공유")
    config = None
    try:
        config = read_openai_config()
    except AIError as error:
        st.info(str(error))
    mode = st.selectbox("AI 실행 모드", ("mock", "live"), key="ai_mode",
                        format_func=lambda value: "Mock Mode · 테스트용 가상 응답 (API 호출 없음)" if value == "mock" else "OpenAI 실 API · 유료 요청", persist_state="session")
    if mode == "mock" and config is None:
        config = OpenAIConfig()  # 잘못된 실 API 설정도 오프라인 Mock 실행을 막지 않는다.
    if budget_controls:
        maximum, budget, output, retries = render_ai_budget_controls()
    else:
        maximum, budget, output, retries = (st.session_state.get(k, d) for k,d in
            (('ai_request_limit',5),('ai_cost_limit',.25),('ai_output_limit',AI_DEFAULT_OUTPUT_TOKENS),('ai_retries',0)))
    st.caption("SDK 자동 재시도 없음 · 타임아웃/연결/검증 오류 자동 재시도 없음 · 동일 입력 60초 중복 차단")
    try:
        options = AIOptions(mode, maximum, budget, output, retries)
    except AIError as error:
        st.error(str(error))
        return None
    st.caption(f"모델: {config.model if config else '설정 오류'} · 가격은 USD 기준 추정이며 실제 청구액과 다를 수 있습니다.")
    approved = False
    if mode == "mock":
        st.info("Mock Mode는 테스트용 가상 응답입니다. 실제 AI 분석·예측 결과가 아니며 OpenAI API를 호출하지 않습니다.")
    else:
        st.warning("실 API 실행에는 OpenAI 사용 요금이 발생합니다. 요약 시장 데이터와 선택한 거시 관측값을 OpenAI에 전송합니다. "
                   "실패 요청도 과금됐을 수 있으며 과금 여부를 단정하지 않습니다.")
        if config and config.api_key is None:
            st.info("OPENAI_API_KEY가 없습니다. 프로젝트 .env에 본인의 키를 설정하거나 Mock Mode를 선택하세요.")
        price = None if config is None else price_for(config.model)
        if price is None:
            st.info("모델 가격 미확인: 비용 상한을 검사할 수 없으므로 실 API 요청을 보류합니다.")
        else:
            # 실제 요약이 만들어지기 전에는 허용된 본문 최대 크기와 넉넉한 스키마/프레이밍을 사용한다.
            upper = estimate_cost(config.model, AI_MAX_INPUT_BYTES+16000, options.max_output_tokens)
            st.caption(f"가격 확인일 {price['verified_on']} · 입력 $ {price['input']}/1M · 출력 $ {price['output']}/1M tokens · "
                       f"요청당 예약 추정 최대 약 ${upper:.5f} (추가 시도당 별도, 정확한 토큰 수 아님)")
        signature = (None if config is None else (config.model, config.scope), tuple(asdict(options).items()))
        if st.session_state.get("_ai_approval_signature") != signature:
            st.session_state["ai_cost_approved"] = False
            st.session_state["_ai_approval_signature"] = signature
        approved = st.checkbox("현재 모델·출력 제한·세션 상한의 유료 API 요청을 승인합니다.", key="ai_cost_approved", persist_state="session")
    st.caption("‘AI 분석 실행 ▶’ 버튼을 누를 때만 실행합니다. FRED 설명은 거시경제 조회를 마친 선택 항목만 사용합니다.")
    return config, options, approved


def _usage(ledger):
    st.caption(f"이 세션 실 API 시도/예약 {ledger.request_count}회 · 기록된 추정/미확인 예약 비용 ${ledger.committed_usd:.5f}")
    if ledger.records:
        with st.expander("실 API 사용량·실패 기록", expanded=False):
            st.dataframe(pd.DataFrame(ledger.records), hide_index=True, width="stretch")
            st.caption("사용량 미확인 실패·중단 요청은 예약 비용을 유지합니다. 새 브라우저 세션/서버 재시작 시 원장이 초기화됩니다. "
                       "OpenAI 결제 대시보드의 실제 청구 상한을 대체하지 않습니다.")


def _display(result, snapshot):
    report = result.report
    if result.mode == "mock":
        st.warning("MOCK · 테스트용 가상 응답입니다. 실제 LLM을 호출한 분석이 아닙니다.")
    if result.status == "INSUFFICIENT_DATA":
        st.info("판단 근거 부족: 로컬 데이터 검사 결과입니다. OpenAI API를 호출하지 않았습니다.")
    st.caption(f"{report.symbol} · {report.timeframe} · {result.model} · 분석 시각 {report.analyzed_at_utc} · 가격 자료 마감 {report.data_as_of_utc}")
    st.caption(f"가격 봉 시작 {snapshot.candle_start_utc} · 수집 시각 {snapshot.fetched_at_utc} · 단일 LLM / 근거 대조 결과")
    for title, body in (("기술적 분석 요약", report.technical_summary), ("거시경제 분석 요약", report.macro_summary),
                        ("최종 분석 의견", report.conclusion)):
        st.markdown(f"**{title}**")
        st.text(body)
    for title, values in (("주요 상승 요인", report.bullish_factors), ("주요 하락 요인", report.bearish_factors),
                           ("위험 요인", report.risk_factors), ("분석 한계", report.limitations)):
        with st.expander(title, expanded=title in ("주요 상승 요인", "주요 하락 요인")):
            if not values:
                st.caption("해당 방향의 유효한 근거가 없습니다.")
            for value in values:
                st.text("• "+value)
    with st.expander("데이터 근거 및 입력 요약", expanded=False):
        st.dataframe(pd.DataFrame([item.model_dump() for item in report.evidence]), hide_index=True, width="stretch")
        st.json(snapshot.model_dump())
        st.caption("AI는 Python이 검증·계산한 근거 문장 목록에서 선택·구성합니다. 자유 서술·새로운 수치/사건은 거부합니다. "
                   "이 검증은 금융적 판단의 정확성이나 예측 성능 검증이 아닙니다.")
    if result.usage is None:
        st.caption("토큰 사용량: API 미호출" if result.status in ("MOCK", "INSUFFICIENT_DATA") else "토큰 사용량: 미확인")
    else:
        usage = result.usage
        st.caption(f"실제 보고된 토큰: 입력 {usage.input_tokens:,} / 출력 {usage.output_tokens:,} / 합계 {usage.total_tokens:,}")
    cost = result.estimated_cost_usd
    st.caption("사용량 기반 추정 비용: 미확인" if cost is None else f"사용량 기반 추정 비용: ${cost:.6f} USD (청구 확정액 아님)")


def render_ai_analysis(data, symbol, timeframe, period, selected_macro, controls, requested, signal_slot):
    ledger = st.session_state.setdefault("_ai_ledger", UsageLedger())
    result = None
    if controls is None:
        st.session_state.pop("_ai_result", None)
        _usage(ledger)
        return
    config, options, approved = controls
    frames = st.session_state.get("_macro_results", {})
    macro = {key: frames[key] for key in selected_macro if key in frames and isinstance(frames[key], pd.DataFrame)}
    try:
        if data is None:
            raise AIError("판단 근거 부족: 시장 데이터 조회 후 AI 분석을 실행하세요.")
        snapshot = build_market_snapshot(data, symbol, timeframe, macro_frames=macro)
    except AIError as error:
        st.session_state.pop("_ai_result", None)
        if requested:
            st.info(str(error))
        else:
            st.caption("시장 데이터가 준비되면 AI 분석을 실행할 수 있습니다.")
        _usage(ledger)
        return
    signature = (snapshot_fingerprint(snapshot), period, tuple(selected_macro), tuple(asdict(options).items()),
                 None if config is None else (config.model, config.scope))
    if st.session_state.get("_ai_signature") != signature:
        if "_ai_result" in st.session_state:
            st.info("AI 입력 데이터·종목·주기 또는 설정이 변경되어 이전 결과를 무효화했습니다. 다시 실행해 주세요.")
        st.session_state.pop("_ai_result", None)
        st.session_state["_ai_signature"] = signature
    if requested:
        st.session_state.pop("_ai_result", None)
        if config is None:
            st.error("OpenAI 설정 오류를 먼저 수정하세요.")
        else:
            with st.spinner("데이터 충분성과 근거를 확인하고 단일 AI 분석을 실행합니다..."):
                try:
                    if options.mode == "live" and snapshot.sufficient:
                        reserved = input_token_reservation(snapshot, iso(pd.Timestamp.now(tz="UTC")))
                        estimate = estimate_cost(config.model, reserved, options.max_output_tokens)
                        st.caption("이번 요청 예약 추정: 미확인" if estimate is None else f"이번 요청 예약 추정: ${estimate:.5f} USD")
                    result = analyze_market(snapshot, config, options, ledger, requested=True, cost_approved=approved)
                except AIError as error:
                    st.error(f"AI 분석 · {error.category}: {error}")
                else:
                    st.session_state["_ai_result"] = result
    result = st.session_state.get("_ai_result")
    if result is not None:
        _display(result, snapshot)
        with signal_slot:
            label = ("MOCK · " if result.mode == "mock" else "")+result.report.signal
            metric_card("AI SIGNAL", label, "단일 LLM 요약" if result.status == "VALIDATED" else "테스트/로컬 검사 · 실 API 미호출")
    else:
        st.caption("아직 현재 입력에 대한 AI 분석 결과가 없습니다.")
    _usage(ledger)
