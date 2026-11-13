"""기존 카드/expander를 재사용하는 연구용 Mock Risk Manager UI."""

import pandas as pd
import streamlit as st
from pydantic import ValidationError

from risk.models import PositionSettings
from ui.components import metric_card

COLORS = {"LOW": "#16a34a", "MODERATE": "#ca8a04", "HIGH": "#ea580c", "CRITICAL": "#dc2626", "UNKNOWN": "#6b7280"}


def render_risk_manager_controls():
    enabled = st.checkbox("Risk Manager 활성화", key="risk_manager_enabled", persist_state="session")
    if not enabled:
        return False, None
    with st.expander("Mock Risk Manager · 가상 포지션 설정", expanded=True):
        if st.button("백테스트 설정 가져오기", key="rm_import_backtest"):
            for target, source, default in (("rm_capital", "bt_capital", 10000.), ("rm_fee", "bt_fee", .1),
                                             ("rm_slippage", "bt_slippage", .05), ("rm_stop", "risk_stop_loss_rate", 3.)):
                st.session_state[target] = float(st.session_state.get(source, default))
        columns = st.columns(3)
        capital = columns[0].number_input("가상 투자 원금 (USDT)", min_value=1., max_value=1e12,
            value=float(st.session_state.get("bt_capital", 10000.)), step=100., key="rm_capital", persist_state="session")
        allowance = columns[1].number_input("계좌 허용 손실률 (%)", min_value=0., max_value=100., value=1., step=.1, key="rm_allowance", persist_state="session")
        stop = columns[2].number_input("가상 손절 거리 (%)", min_value=.01, max_value=99.,
            value=float(st.session_state.get("risk_stop_loss_rate", 3.)), step=.1, key="rm_stop", persist_state="session")
        columns = st.columns(3)
        leverage = columns[0].number_input("가상 레버리지 (배)", min_value=1., max_value=100., value=1., step=1., key="rm_leverage", persist_state="session")
        fee = columns[1].number_input("Risk Manager 편도 수수료 (%)", min_value=0., max_value=5.,
            value=float(st.session_state.get("bt_fee", .1)), step=.01, key="rm_fee", persist_state="session")
        slip = columns[2].number_input("Risk Manager 편도 슬리피지 (%)", min_value=0., max_value=10.,
            value=float(st.session_state.get("bt_slippage", .05)), step=.01, key="rm_slippage", persist_state="session")
        st.caption("연구용 Mock 평가입니다. 원금×레버리지 전체 노출과 손실 한도로 제한한 규모를 별도로 계산합니다. "
                   "증거금·진입 수수료도 원금 내로 제한합니다. 기존 백테스트 설정/결과를 변경하지 않습니다.")
        st.caption("기준 손절가는 마지막 확정 종가에서 계산합니다. 실제 손절 체결·유동성·강제청산 가격을 보장하거나 예측하지 않습니다.")
        try:
            settings = PositionSettings(capital=capital, account_loss_rate=allowance/100, stop_distance=stop/100,
                                        leverage=leverage, fee_rate=fee/100, slippage_rate=slip/100)
        except (ValidationError, TypeError, ValueError):
            st.error("가상 리스크 입력이 유효하지 않습니다. 유한한 투자금·비율·레버리지를 확인하세요.")
            return True, None
    return True, settings


def display_number(value, unit="", digits=3):
    return "N/A" if value is None else f"{value:,.{digits}f}{unit}"


def render_risk_manager(state):
    st.markdown("**Risk Manager · 규칙 기반 Mock 위험 평가**")
    result = state.get("risk_result")
    if result is None:
        st.error("UNKNOWN · Risk Manager 미실행 또는 실행/검증 실패입니다. 정상 위험 평가가 아니며 연구 상태 ABSTAIN으로 취급합니다.")
        return
    a, b = st.columns(2)
    with a:
        metric_card("RISK LEVEL", result.risk_level, result.validation_status, COLORS[result.risk_level])
    with b:
        metric_card("RESEARCH ACTION", result.trading_action, "내부 연구 상태 · 주문/포지션 진입 아님")
    st.text(result.risk_summary)
    m = result.quantitative_metrics
    st.caption(f"{result.symbol} · {result.timeframe} · 자료 마감 {result.data_as_of_utc} · 확정 봉 {m.candle_count:,}개 / 유효 수익률 {m.return_count:,}개 · OpenAI API 미호출")
    st.caption(f"실제 관측 구간 {m.window_start_utc} ~ {m.data_as_of_utc} · 예상 간격 {m.expected_interval_seconds:g}초 / 관측 간격 {m.observed_intervals_seconds}초")
    a, b, c = st.columns(3)
    with a:
        metric_card("봉 수익률 변동성", display_number(m.volatility_per_bar_pct, "%"), "표본 표준편차 · ddof=1")
    with b:
        metric_card("종가 MDD", display_number(m.close_mdd_pct, "%"), "봉 내부 낙폭 아님")
    with c:
        metric_card("ATR (14)", display_number(m.atr_14, " USDT"), f"종가 대비 {display_number(m.atr_close_pct, '%')} · 연속 봉 {m.atr_contiguous_candles}개")
    st.caption(f"연율 변동성 {display_number(m.annualized_volatility_pct, '%')} = 봉 변동성 × {m.annualization_factor:.6g} "
               "(365일·24시간 거래의 제곱근 시간 가정) · 최악 종가 낙폭 시각 "+(m.mdd_trough_time_utc or "N/A"))
    st.caption(f"직전 거래량 평균 대비 {display_number(m.volume_ratio, '배')} · 최근/직전 변동성 {display_number(m.volatility_expansion_ratio, '배')}")
    if result.risk_level == "UNKNOWN":
        st.warning("필수 지표·표본·자료 품질이 부족하여 정상 위험 등급을 부여하지 않았습니다.")
    assessment = result.position_assessment
    if assessment is not None:
        st.markdown("**가상 포지션 시나리오 · 실제 거래 아님**")
        st.caption(f"허용 손실 {assessment.allowed_loss:,.4f} USDT · 원금×레버리지 전체 노출 {assessment.requested_full_notional:,.4f} USDT · "
                   f"해당 전체 노출의 예상 손절 손실 {assessment.requested_full_loss:,.4f} USDT")
        if result.risk_limit_exceeded:
            st.warning("전체 노출 가정이 위험 한도를 초과합니다. 아래의 별도 제한 규모가 전체 노출을 승인하거나 실제 체결을 보장하지 않습니다.")
        st.caption(f"비용 포함 제한 수량 {assessment.cost_aware_quantity:.8g} · 기준 명목 가치 {assessment.adjusted_notional:,.4f} USDT · "
                   f"제한 규모 최대 예상 손실 {assessment.adjusted_max_loss:,.4f} / 왕복 비용 {assessment.adjusted_max_cost:,.4f} USDT")
        st.dataframe(pd.DataFrame([row.model_dump() for row in assessment.scenarios]), hide_index=True, width="stretch")
        st.caption("LONG/SHORT는 양방향 가정 비교입니다. 거래 방향 추천이 아닙니다. 비용은 진입·청산 수수료와 불리한 슬리피지를 포함합니다.")
    for title, values in (("위험 등급 근거 및 임계값", result.grade_basis), ("주요 위험 요인", result.risk_factors), ("Risk Manager 분석 한계", result.limitations)):
        with st.expander(title, expanded=title == "위험 등급 근거 및 임계값"):
            for item in values:
                st.text("• "+item)
    with st.expander("Risk Manager 근거 및 JSON 상세"):
        st.dataframe(pd.DataFrame([item.model_dump() for item in result.evidence]), hide_index=True, width="stretch")
        st.json(result.model_dump())
