"""백테스트 설정·명시적 실행·결과 무효화. 계산은 backtest 패키지가 담당한다."""

from dataclasses import asdict
import hashlib
import json

import pandas as pd
import streamlit as st

from backtest.engine import run_backtest, compare_risk
from backtest.models import BacktestConfig, BacktestError, BacktestResult, RiskConfig, RiskComparison
from config.settings import APP_VERSION
from strategies.evaluator import StrategyEvaluationError
from strategies.models import Strategy, describe_group
from strategies.storage import strategy_to_json
from ui.backtest_charts import equity_chart, trade_pnl_chart, comparison_equity_chart


def risk_summary(risk: RiskConfig) -> str:
    summary = [f"{label} {rate*100:g}%" for label, rate in zip(
        ("손절", "익절", "트레일링"), risk.effective_settings().values()) if rate is not None]
    return " · ".join(summary) if summary else "모두 OFF · 신호 기반 체결"


def render_backtest_settings(strategy: Strategy | None):
    first, second, third = st.columns(3)
    capital = first.number_input("초기 투자금 (USDT)", min_value=1.0, value=10000.0,
                                step=100.0, key="bt_capital", persist_state="session")
    fee = second.number_input("편도 수수료 (%)", min_value=0.0, max_value=5.0, value=0.1,
                              step=0.01, key="bt_fee", persist_state="session")
    slippage = third.number_input("편도 슬리피지 (%)", min_value=0.0, max_value=10.0, value=0.05,
                                   step=0.01, key="bt_slippage", persist_state="session")
    with st.expander("체결 가정 및 연구적 한계", expanded=False):
        participation = st.number_input("직전 확정 봉 거래량 대비 최대 참여율 (%)", min_value=0.01,
                                         max_value=100.0, value=10.0, step=1.0, key="bt_participation", persist_state="session")
        st.caption(
            "신호 다음 연속 봉의 시가에 편도 비용을 반영합니다. 진입은 자금·거래량 한도로 수량을 "
            "제한하고, 전량 청산에 필요한 물량이 부족하면 미체결 처리합니다."
        )
        st.caption(
            "SHORT는 Binance Spot의 실제 거래가 아닌 1배 가상 숏입니다. OHLCV는 호가·실제 체결 "
            "유동성을 완전히 재현하지 못하며 수수료·슬리피지·거래량 한도는 가정값입니다. "
            "펀딩비·거래소 청산·증거금 제약은 제외합니다. 평가 자산이 음수가 되는 경로는 오류로 중단합니다."
        )
        st.caption(
            "과거 수익률은 미래 수익을 보장하지 않습니다. 전략 개발과 검증에 같은 데이터를 사용하면 "
            "과최적화 위험이 있습니다."
        )
    st.caption("확정 봉 신호 → 다음 봉 시가 가상 체결 · 단일 포지션 · 가상 SHORT · 종료 시 미청산 포지션은 종가 평가")
    if strategy is not None:
        with st.expander(f"백테스트 전략: {strategy.name}", expanded=False):
            st.text(describe_group(strategy.long) + "\n\n" + describe_group(strategy.short))
    config = None
    try:
        config = BacktestConfig(capital, fee/100, slippage/100, participation/100)
    except BacktestError as error:
        st.error(str(error))
    risk = render_risk_controls()
    return config, risk


def render_backtest_actions(strategy, config, risk):
    requested = st.button("백테스팅 실행", key="run_backtest", type="primary",
                          width="stretch", disabled=config is None or strategy is None or risk is None)
    compare_requested = st.button("Risk ON/OFF 비교 실행", key="compare_risk", width="stretch",
                                  disabled=config is None or strategy is None or risk is None)
    return requested, compare_requested


def render_backtest_controls(strategy: Strategy | None):
    config, risk = render_backtest_settings(strategy)
    requested, compare_requested = render_backtest_actions(strategy, config, risk)
    return config, risk, requested, compare_requested


def render_risk_controls():
    with st.expander("Risk Management · 손절·익절·트레일링", expanded=False):
        columns = st.columns(3)
        values = {}
        for column, key, label, default in zip(
            columns, ("stop_loss", "take_profit", "trailing_stop"),
            ("Stop Loss", "Take Profit", "Trailing Stop"), (3.0, 6.0, 3.0),
        ):
            enabled = column.checkbox(f"{label} 활성화", key=f"risk_{key}_enabled", persist_state="session")
            rate = column.number_input(f"{label} 비율 (%)", min_value=0.01, max_value=99.0,
                                       value=default, step=0.1, disabled=not enabled, key=f"risk_{key}_rate", persist_state="session")
            values[f"{key}_enabled"] = enabled
            values[f"{key}_rate"] = rate/100
        try:
            risk = RiskConfig(**values)
        except BacktestError as error:
            st.error(str(error))
            return None
        st.caption("현재 리스크: " + risk_summary(risk))
        st.caption(
            "우선순위: 미체결 리스크 재시도 → 시가 갭 리스크 → 신호 시가 청산 → 봉 내부 리스크. "
            "동일 봉 손절·익절 접촉은 손절 우선, 손절·트레일링은 더 불리한 기준가를 사용합니다."
        )
        st.caption(
            "진입 봉도 진입 직후부터 리스크를 적용합니다. 트레일링은 직전 봉까지의 극값만 사용합니다. "
            "봉 내부 체결 시각은 알 수 없어 봉 위치만 표시합니다. 청산 물량 부족 시 부분 청산 없이 "
            "보유하며 다음 관측 봉 시가에 재시도합니다. 진입·청산은 봉별 거래량 한도를 공유합니다."
        )
    return risk


def result_fingerprint(data, strategy, settings, symbol, timeframe, period, risk=None):
    """지표 표시 선택을 제외한 전략·설정·시장 조건과 데이터 내용 전체를 식별한다."""
    digest = hashlib.sha256()
    payload = {"strategy": strategy_to_json(strategy), "settings": asdict(settings),
               "symbol": symbol, "timeframe": timeframe, "period": period,
               "app_version": APP_VERSION,
               "risk": (risk or RiskConfig()).effective_settings(),
               "columns": data.columns.tolist()}
    digest.update(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    digest.update(pd.util.hash_pandas_object(data, index=True).to_numpy().tobytes())
    return digest.hexdigest()


def clear_backtest_result():
    st.session_state.pop("_backtest_result", None)
    st.session_state.pop("_backtest_key", None)
    st.session_state.pop("_risk_comparison", None)


def execute_or_get_result(data, strategy, settings, symbol, timeframe, period, requested,
                          *, risk=None, compare_requested=False):
    if strategy is None or settings is None or risk is None:
        clear_backtest_result()
        st.info("유효한 전략과 백테스트 설정을 준비한 뒤 실행해 주세요.")
        return None
    fingerprint = result_fingerprint(data, strategy, settings, symbol, timeframe, period, risk)
    previous = st.session_state.get("_backtest_key")
    if previous is not None and previous != fingerprint:
        clear_backtest_result()
        st.info("전략·시장·설정 또는 데이터가 변경되어 이전 백테스트 결과를 무효화했습니다. 다시 실행해 주세요.")
    if requested or compare_requested:
        clear_backtest_result()
        try:
            with st.spinner("다음 봉 체결과 비용을 반영해 백테스트합니다..."):
                if compare_requested:
                    comparison = compare_risk(data, strategy, settings, timeframe=timeframe, risk=risk)
                    result = comparison.risk_on
                    st.session_state["_risk_comparison"] = comparison
                else:
                    result = run_backtest(data, strategy, settings, timeframe=timeframe, risk=risk)
        except (BacktestError, StrategyEvaluationError) as error:
            st.error(f"백테스트 실행 실패: {error}")
            return None
        st.session_state["_backtest_result"] = result
        st.session_state["_backtest_key"] = fingerprint
    result = st.session_state.get("_backtest_result")
    if result is None:
        st.caption("백테스팅 실행 버튼을 누르면 현재 전략과 데이터의 결과를 계산합니다.")
    else:
        comparison = st.session_state.get("_risk_comparison")
        if comparison is not None:
            render_risk_comparison(comparison)
        render_backtest_result(result)
    return result


def _format(value, suffix=""):
    return "N/A" if value is None else f"{value:,.2f}{suffix}"


def render_risk_comparison(comparison: RiskComparison):
    st.markdown("**동일 데이터·전략·비용의 Risk OFF / ON 비교**")
    fields = (("누적 수익률 (%)", "cumulative_return_pct"), ("승률 (%)", "win_rate_pct"),
              ("MDD (%)", "max_drawdown_pct"), ("최종 평가 자산 (USDT)", "final_equity"),
              ("종료 거래 수", "closed_trades"), ("평균 거래 순손익 (USDT)", "average_trade_pnl"))
    table = pd.DataFrame({"지표": [label for label, _ in fields]})
    for name, result in (("Risk OFF", comparison.risk_off), ("Risk ON", comparison.risk_on)):
        table[name] = [_format(getattr(result.metrics, key)) for _, key in fields]
    st.dataframe(table, hide_index=True, width="stretch")
    st.plotly_chart(comparison_equity_chart(comparison), width="stretch", theme=None, key="risk_comparison_equity")
    st.caption("리스크 적용이 수익률이나 MDD 개선을 보장하지 않습니다. 아래 지표·거래 내역·가격 차트 체결 마커는 Risk ON 결과입니다.")
    if not comparison.risk_on.risk.enabled:
        st.info("모든 리스크가 OFF이므로 두 비교 결과는 동일합니다.")
    with st.expander("Risk OFF 거래·이벤트 원장", expanded=False):
        st.dataframe(comparison.risk_off.trades, hide_index=True, width="stretch")
        st.dataframe(comparison.risk_off.events, hide_index=True, width="stretch")


def render_backtest_result(result: BacktestResult):
    metrics = result.metrics
    st.caption(f"실행 전략: {json.loads(result.strategy_json)['name']} · 종료 상태: {result.final_position.value}")
    st.caption("적용 리스크: " + risk_summary(result.risk))
    if not result.equity_curve.empty:
        st.caption(
            f"실제 평가 구간: {result.equity_curve.iloc[0]['timestamp']:%Y-%m-%d %H:%M} ~ "
            f"{result.equity_curve.iloc[-1]['timestamp']:%Y-%m-%d %H:%M} UTC · "
            f"Timeframe: {result.timeframe}"
        )
    a, b, c, d = st.columns(4)
    a.metric("최종 평가 자산", _format(metrics.final_equity, " USDT"))
    b.metric("누적 수익률", _format(metrics.cumulative_return_pct, "%"))
    c.metric("승률 (종료 거래 기준)", _format(metrics.win_rate_pct, "%"))
    d.metric("MDD", _format(metrics.max_drawdown_pct, "%"))
    a, b, c, d = st.columns(4)
    a.metric("종료 거래 수", metrics.closed_trades)
    b.metric("평균 종료 거래 순손익", _format(metrics.average_trade_pnl, " USDT"))
    c.metric("총 거래 수수료", _format(metrics.total_fees, " USDT"))
    d.metric("미체결 주문 수", metrics.rejected_orders)
    a, b = st.columns(2)
    a.metric("실현 손익 (지급 수수료 포함)", _format(metrics.realized_pnl, " USDT"))
    b.metric("미실현 가격 손익", _format(metrics.unrealized_pnl, " USDT"))
    st.caption("최종 자산 = 초기 투자금 + 실현 손익 + 미실현 손익 · 열린 거래의 진입 수수료도 지급 비용으로 인식합니다.")
    if result.equity_curve.empty:
        st.info("평가할 캔들 데이터가 없어 수익률과 MDD는 N/A입니다.")
    else:
        st.plotly_chart(equity_chart(result), width="stretch", theme=None, key="backtest_equity")
    if metrics.closed_trades:
        st.plotly_chart(trade_pnl_chart(result), width="stretch", theme=None, key="backtest_trade_pnl")
    else:
        st.info("종료된 거래가 없습니다. 승률과 평균 거래 손익은 N/A입니다.")
    if not result.trades.empty:
        table = result.trades.copy()
        table["손익 구분"] = [
            "미청산" if status == "OPEN" else "수익" if pnl > 0 else "손실" if pnl < 0 else "보합"
            for status, pnl in zip(table["status"], table["net_pnl"])
        ]
        st.dataframe(table, hide_index=True, width="stretch")
    with st.expander("체결·미체결 이벤트 원장 및 회계", expanded=False):
        st.caption("신호는 다음 봉 시가 체결입니다. INTRABAR_OHLC 리스크 청산은 정확한 체결 시각을 알 수 없어 execution_timestamp가 비어 있으며 execution_candle_timestamp로 해당 봉을 기록합니다.")
        st.dataframe(result.events, hide_index=True, width="stretch")
        st.dataframe(result.equity_curve, hide_index=True, width="stretch")
        st.json(asdict(result.settings))
        st.json(result.risk.effective_settings())
