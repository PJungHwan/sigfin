# Frozen pre-navigation composition for existing renderer regressions.
# The actual six-page entrypoint is exercised in test_redesign_ui.py.
from math import isfinite

import streamlit as st

from collectors.binance import DataCollectionError
from config.settings import (
    APP_NAME,
    APP_ICON,
    APP_VERSION,
    DEFAULT_SYMBOL,
    DEFAULT_TIMEFRAME,
    DEFAULT_PERIOD,
    DATA_CACHE_TTL_SECONDS,
    SUPPORTED_PERIODS,
    SUPPORTED_SYMBOLS,
    SUPPORTED_TIMEFRAMES,
)
from services.market_data import load_market_data
from ui.charts import create_candlestick_chart
from ui.styles import apply_custom_css
from ui.components import metric_card, agent_status, rsi_metric_card, macd_metric_card
from ui.strategy_builder import render_strategy_builder
from ui.strategy_signals import render_signal_preview
from strategies.evaluator import StrategyEvaluationError, evaluate_strategy
from ui.backtest_view import render_backtest_controls, execute_or_get_result, clear_backtest_result
from ui.macro_view import render_macro_controls, render_macro_data
from ui.crypto_macro_view import render_crypto_macro_controls, render_crypto_macro_data
from ui.ai_analysis_view import render_ai_controls, render_ai_analysis
from ui.workflow_view import render_workflow_controls, render_workflow_result
from ui.portfolio_view import render_portfolio
import ui.portfolio_risk_view as portfolio_risk_view
from ui.storage_view import render_analysis_history
from ui.research_view import render_research
from ui.experiment_view import render_experiments


# ============================================================
# 페이지 기본 설정
# ============================================================

st.set_page_config(
    page_title=APP_NAME,
    page_icon=APP_ICON,
    layout="wide",
    initial_sidebar_state="expanded",
)

apply_custom_css()


# ============================================================
# 왼쪽 사이드바
# ============================================================

with st.sidebar:
    st.title("시장 환경 설정")

    symbol = st.selectbox(
        "종목",
        SUPPORTED_SYMBOLS,
        index=SUPPORTED_SYMBOLS.index(DEFAULT_SYMBOL),
        key="symbol",
    )

    timeframe = st.selectbox(
        "Timeframe",
        SUPPORTED_TIMEFRAMES,
        index=SUPPORTED_TIMEFRAMES.index(DEFAULT_TIMEFRAME),
        key="timeframe",
    )

    period = st.selectbox(
        "분석 기간",
        SUPPORTED_PERIODS,
        index=SUPPORTED_PERIODS.index(DEFAULT_PERIOD),
        key="period",
    )

    st.divider()

    st.subheader("기술적 지표")

    use_rsi = st.checkbox("RSI", value=True, key="show_rsi")
    use_macd = st.checkbox("MACD", value=True, key="show_macd")
    use_ema = st.checkbox("EMA", value=True, key="show_ema")
    use_sma = st.checkbox("SMA", key="show_sma")
    use_bollinger = st.checkbox("Bollinger Bands", key="show_bollinger")
    use_volume_sma = st.checkbox("거래량 평균선", value=True, key="show_volume_sma")

    st.divider()

    st.subheader("거시경제")

    use_cpi = st.checkbox("CPI", key="macro_cpi")
    use_fed_rate = st.checkbox("FED Rate", key="macro_fed")
    use_nfp = st.checkbox("NFP", key="macro_nfp")
    use_unrate = st.checkbox("실업률 (UNRATE)", key="macro_unrate")
    use_dgs10 = st.checkbox("10년물 국채금리 (DGS10)", key="macro_dgs10")
    selected_macro = tuple(series for enabled, series in (
        (use_cpi, "CPIAUCSL"), (use_fed_rate, "FEDFUNDS"), (use_nfp, "PAYEMS"),
        (use_unrate, "UNRATE"), (use_dgs10, "DGS10"),
    ) if enabled)

    st.divider()

    strategy = render_strategy_builder()

    st.divider()

    analyze_button = st.button(
        "AI 분석 실행 ▶",
        type="primary",
        key="analyze_ai",
        width="stretch",
    )

    st.caption(f"Version {APP_VERSION}")


# ============================================================
# 메인 헤더
# ============================================================

st.markdown(
    """
    <div class="main-title">
        Crypto Multi-Agent Research System
    </div>

    <div class="sub-title">
        Crypto × Macro × Multi-Agent AI Research
    </div>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# 현재 선택된 시장 정보
# ============================================================

header_left, header_right = st.columns([4, 1])

with header_left:
    st.subheader(symbol)
    st.caption(
        f"Timeframe: {timeframe} · 분석 기간: {period}"
    )

with header_right:
    market_status = st.empty()
    market_status.info("DATA LOADING")


# ============================================================
# 차트 영역
# 결과 자리를 먼저 확보하고, 독립적인 카드가 렌더링된 뒤 데이터를 조회한다.
# ============================================================

with st.container(border=True):
    chart_title, refresh_column = st.columns([5, 1])
    with chart_title:
        st.subheader("MARKET CHART")
        st.caption("Binance Spot · 확정 캔들 · 모든 시각 UTC")
    with refresh_column:
        refresh_button = st.button("새로고침", key="refresh_market", width="stretch")
    market_data_area = st.container()


# ============================================================
# 시장 지표
# ============================================================

st.subheader("Market Indicators")

indicator_1, indicator_2, indicator_3 = st.columns(3)

with indicator_1:
    rsi_card_slot = st.empty()
    with rsi_card_slot:
        metric_card("RSI", "-", "데이터 조회 중")

with indicator_2:
    macd_card_slot = st.empty()
    with macd_card_slot:
        metric_card("MACD", "-", "데이터 조회 중")

with indicator_3:
    ai_signal_card_slot = st.empty()
    with ai_signal_card_slot:
        metric_card("AI SIGNAL", "-")


with st.container(border=True):
    st.subheader("Strategy Signals")
    show_strategy_signals = st.toggle("신호 미리보기", value=False, key="show_strategy_signals")
    strategy_signal_area = st.container()
    if not show_strategy_signals:
        strategy_signal_area.caption("사이드바에서 전략 조건을 편집한 뒤 신호 미리보기를 켜 주세요.")


with st.container(border=True):
    st.subheader("백테스팅")
    backtest_settings, risk_settings, backtest_requested, compare_requested = render_backtest_controls(strategy)
    backtest_output = st.container()

with st.container(border=True):
    st.subheader("거시경제 데이터 · FRED")
    macro_controls = render_macro_controls(selected_macro)
    macro_output = st.container()

with st.container(border=True):
    st.subheader("Crypto × Macro 통합 분석")
    crypto_macro_controls = render_crypto_macro_controls(symbol)
    crypto_macro_output = st.container()

with st.container(border=True):
    st.subheader("AI 시장 분석 · 단일 LLM")
    ai_controls = render_ai_controls()
    ai_output = st.container()


# ============================================================
# AI Agent 영역
# ============================================================

st.subheader("AI Research Agents")
st.caption("Technical/Macro는 Mock/LLM 노드, Bull/Bear와 Risk Manager는 각각 활성화한 Mock 노드 상태를 표시합니다. STEP 9 단일 LLM 분석은 별도입니다.")

agent_1, agent_2, agent_3, agent_4, agent_5 = st.columns(5)

with agent_1:
    technical_workflow_slot = st.empty()
    with technical_workflow_slot:
        agent_status("Technical Agent", status="WAITING")

with agent_2:
    macro_workflow_slot = st.empty()
    with macro_workflow_slot:
        agent_status("Macro Agent", status="WAITING")

with agent_3:
    bull_workflow_slot = st.empty()
    with bull_workflow_slot:
        agent_status("Bull Agent", status="NOT CONNECTED")

with agent_4:
    bear_workflow_slot = st.empty()
    with bear_workflow_slot:
        agent_status("Bear Agent", status="NOT CONNECTED")

with agent_5:
    risk_workflow_slot = st.empty()
    with risk_workflow_slot:
        agent_status("Risk Manager", status="NOT CONNECTED")

with st.container(border=True):
    st.subheader("Multi-Agent 워크플로우 · LangGraph")
    workflow_requested = render_workflow_controls()
    workflow_output = st.container()


# ============================================================
# 시장 데이터 조회와 기존 기능은 AI 호출 여부와 독립적이다.
# ============================================================

market_data = None
with market_data_area:
    if refresh_button:
        load_market_data.clear(symbol, timeframe, period)

    try:
        with st.spinner("Binance 확정 캔들과 계산용 과거 데이터를 조회하고 지표를 계산합니다..."):
            market_data = load_market_data(symbol, timeframe, period)
    except DataCollectionError as error:
        market_status.error("DATA ERROR")
        st.error(str(error))
        if error.details is not None:
            st.caption(error.details.hint)
            with st.expander("데이터 조회 오류 진단", expanded=False):
                st.json(error.details.safe_dict())
        else:
            st.caption("종목과 기간을 확인한 뒤 새로고침해 주세요.")
        with rsi_card_slot:
            metric_card("RSI", "-", "데이터 조회 실패")
        with macd_card_slot:
            metric_card("MACD", "-", "데이터 조회 실패")
        if show_strategy_signals:
            strategy_signal_area.warning("시장 데이터 조회 후 전략 신호를 확인할 수 있습니다.")
        clear_backtest_result()
        backtest_output.info("시장 데이터를 조회한 뒤 백테스트를 실행해 주세요.")
    else:
        metadata = market_data.attrs
        if metadata["is_partial"]:
            market_status.warning("PARTIAL DATA")
            st.warning("일부 구간만 수집되었습니다. " + " ".join(metadata["limit_reasons"]))
        else:
            market_status.success("DATA READY")

        latest_candle = market_data.iloc[-1]
        with rsi_card_slot:
            rsi_metric_card(latest_candle["rsi_14"])
        with macd_card_slot:
            macd_metric_card(latest_candle["macd_histogram"])
        candle_change = (
            (latest_candle["close"] - latest_candle["open"]) / latest_candle["open"] * 100
        )
        close_column, change_column, timestamp_column = st.columns([2, 2, 3])
        with close_column:
            st.metric("최신 확정 종가", f"{latest_candle['close']:,.2f} USDT")
        with change_column:
            st.metric("확정 캔들 변동률", f"{candle_change:+.2f}%")
        with timestamp_column:
            st.metric(
                "마지막 데이터 시각 (UTC · 봉 시작)",
                latest_candle["timestamp"].strftime("%Y-%m-%d %H:%M"),
            )
        st.caption(
            "가격 변동률은 해당 확정 캔들의 시가 대비 종가입니다. "
            f"마지막 봉 마감: {metadata['actual_end']:%Y-%m-%d %H:%M} UTC"
        )
        st.caption(
            f"요청 기간: {metadata['requested_start']:%Y-%m-%d %H:%M} ~ "
            f"{metadata['requested_end']:%Y-%m-%d %H:%M} UTC · "
            f"실제 수집: {metadata['actual_start']:%Y-%m-%d %H:%M} ~ "
            f"{metadata['actual_end']:%Y-%m-%d %H:%M} UTC (마지막 봉 마감 기준) · "
            f"{len(market_data):,}개 확정 캔들"
        )
        if metadata.get("warmup_requested", 0):
            st.caption(
                f"지표 계산용 과거 확정 봉: {metadata['warmup_collected']}개 "
                f"(요청 {metadata['warmup_requested']}개) · 계산 후 분석 기간만 표시합니다."
            )
        for reason in metadata.get("warmup_reasons", ()):
            st.warning(reason)
        selected_indicators = [
            (use_rsi, "RSI", ["rsi_14"]),
            (use_macd, "MACD", ["macd_histogram"]),
            (use_ema, "EMA", ["ema_20", "ema_60"]),
            (use_sma, "SMA", ["sma_20", "sma_60"]),
            (use_bollinger, "Bollinger Bands", ["bb_upper", "bb_middle", "bb_lower"]),
            (use_volume_sma, "거래량 평균선", ["volume_sma_20"]),
        ]
        insufficient = [
            label for enabled, label, columns in selected_indicators
            if enabled and any(not isfinite(latest_candle[column]) for column in columns)
        ]
        if insufficient:
            st.info(
                f"마지막 확정 봉의 {', '.join(insufficient)} 계산에 필요한 연속 데이터가 "
                "부족합니다. 유효한 값이 계산되는 구간부터 지표를 표시합니다."
            )
        strategy_signals = None
        if show_strategy_signals:
            with strategy_signal_area:
                if strategy is None:
                    st.warning("사이드바의 전략 조건 검증 오류를 수정해 주세요.")
                else:
                    try:
                        strategy_signals = evaluate_strategy(market_data, strategy, timeframe=timeframe)
                    except StrategyEvaluationError as error:
                        st.error(f"전략 조건 평가 실패: {error}")
                    else:
                        render_signal_preview(market_data, strategy, strategy_signals)
        with backtest_output:
            backtest_result = execute_or_get_result(
                market_data, strategy, backtest_settings, symbol, timeframe, period, backtest_requested,
                risk=risk_settings, compare_requested=compare_requested,
            )
        st.plotly_chart(
            create_candlestick_chart(
                market_data, symbol, timeframe,
                show_rsi=use_rsi, show_macd=use_macd, show_ema=use_ema,
                show_sma=use_sma, show_bollinger=use_bollinger,
                show_volume_sma=use_volume_sma,
                signals=strategy_signals,
                executions=None if backtest_result is None else backtest_result.events,
            ),
            width="stretch",
            theme=None,
            key="market_chart",
            config={"scrollZoom": True, "displaylogo": False, "responsive": True},
        )
        if use_volume_sma and isfinite(latest_candle["volume_sma_20"]):
            volume_ratio = latest_candle["volume_ratio_20"]
            comparison = f"평균 대비 {volume_ratio:.2f}배" if isfinite(volume_ratio) else "평균이 0이어서 비율 없음"
            st.caption(
                f"마지막 확정 봉 거래량: {latest_candle['volume']:,.4f} {symbol.split('/')[0]} · "
                f"20봉 평균: {latest_candle['volume_sma_20']:,.4f} · {comparison}"
            )
        st.caption(
            f"조회 완료: {metadata['fetched_at']:%Y-%m-%d %H:%M:%S} UTC · "
            f"조회 결과는 {DATA_CACHE_TTL_SECONDS}초간 캐시됩니다. "
            "새로고침하면 최신 데이터를 다시 조회합니다."
        )

# Binance 성공 여부와 독립적으로 명시적 FRED 조회를 처리한다.
with macro_output:
    render_macro_data(selected_macro, macro_controls)

with crypto_macro_output:
    render_crypto_macro_data(symbol, crypto_macro_controls)

with ai_output:
    render_ai_analysis(market_data, symbol, timeframe, period, selected_macro, ai_controls,
                       analyze_button, ai_signal_card_slot)

with workflow_output:
    render_workflow_result(market_data, symbol, timeframe, period, selected_macro, workflow_requested,
                           technical_workflow_slot, macro_workflow_slot, bull_workflow_slot, bear_workflow_slot, risk_workflow_slot)

render_experiments(market_data, strategy, backtest_settings, risk_settings, symbol, timeframe, period)

portfolio_prices = render_portfolio(timeframe, period)
portfolio_risk_view.render_portfolio_risk(price_context=portfolio_prices)
render_research(market_data,symbol,timeframe,period,selected_macro,
                strategy=strategy,backtest_config=backtest_settings,backtest_risk=risk_settings,
                risk_agent_settings=workflow_requested[8])
render_analysis_history(market_data,symbol,timeframe,period,selected_macro,
                        strategy=strategy,backtest_config=backtest_settings,backtest_risk=risk_settings)
