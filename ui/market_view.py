"""Market workspace: existing cached prices/indicators and exact Plotly data."""

from math import isfinite
import streamlit as st

from collectors.binance import DataCollectionError
from services.market_data import load_market_data
from strategies.evaluator import evaluate_strategy, StrategyEvaluationError
from ui.charts import create_candlestick_chart
from ui.components import metric_card, rsi_metric_card, macd_metric_card
from ui.design import style_figure
from ui.strategy_signals import render_signal_preview
from ui.workspace_state import current_strategy, current_backtest_settings, validate_current_backtest

INDICATORS = (('show_rsi', 'RSI', True), ('show_macd', 'MACD', True), ('show_ema', 'EMA', True),
              ('show_sma', 'SMA', False), ('show_bollinger', 'Bollinger Bands', False),
              ('show_volume_sma', '거래량 평균선', True))


def market_data(symbol, timeframe, period, *, refresh=False):
    if refresh:
        load_market_data.clear(symbol, timeframe, period)
    try:
        with st.spinner('확정 캔들과 기술지표를 조회합니다…'):
            data = load_market_data(symbol, timeframe, period)
    except DataCollectionError as error:
        st.error(str(error))
        st.caption('데이터 조회 실패 · 실제 가격을 임의 값으로 대체하지 않습니다.')
        if error.details is not None:
            st.caption(error.details.hint)
            with st.expander('데이터 조회 오류 진단', expanded=False):
                st.json(error.details.safe_dict(), expanded=False)
        return None
    if data.attrs.get('demo_synthetic'):
        st.caption('자료 출처: 오프라인 합성 시연 · 실제 Binance 가격이 아닙니다.')
    else:
        st.caption('자료 출처: Binance 공개 현물 OHLCV · 확정 봉만 사용')
    if data.attrs.get('is_partial'):
        st.warning('일부 구간 자료 · ' + ' '.join(data.attrs.get('limit_reasons', ())))
    st.caption(f"마지막 봉 마감 {data.attrs['actual_end']:%Y-%m-%d %H:%M} UTC · 수집 {data.attrs['fetched_at']:%Y-%m-%d %H:%M:%S} UTC")
    return data


def indicator_controls():
    with st.expander('차트 지표 설정', expanded=False):
        columns = st.columns(3)
        for i, (key, label, default) in enumerate(INDICATORS):
            columns[i % 3].checkbox(label, value=default, key=key, persist_state='session')


def render_price_chart(data, symbol, timeframe, period, *, signals=None, executions=None):
    figure = create_candlestick_chart(data, symbol, timeframe,
        **{key: st.session_state.get(key, default) for key, _, default in INDICATORS},
        signals=signals, executions=executions)
    st.plotly_chart(style_figure(figure), theme=None, width='stretch', key='market_chart',
        config={'scrollZoom': True, 'displaylogo': False, 'responsive': True})


def render_market(symbol, timeframe, period):
    refresh = st.button('새로고침', key='refresh_market', icon=':material/refresh:')
    data = market_data(symbol, timeframe, period, refresh=refresh)
    strategy = current_strategy()
    config, risk = current_backtest_settings()
    validate_current_backtest(data, strategy, config, risk, symbol, timeframe, period)
    if data is None:
        return
    last = data.iloc[-1]
    columns = st.columns(4)
    with columns[0]: metric_card('최신 확정 종가', f"{last['close']:,.2f}", 'USDT')
    with columns[1]: metric_card('확정 봉 변동률', f"{(last['close']/last['open']-1)*100:+.2f}%", '해당 봉 시가 대비 종가')
    with columns[2]: rsi_metric_card(last['rsi_14'])
    with columns[3]: macd_metric_card(last['macd_histogram'])
    indicator_controls()
    preview = st.toggle('전략 신호 표시', key='show_strategy_signals', persist_state='session')
    signals = None
    if preview:
        if strategy is None:
            st.warning('전략·백테스트에서 조건 검증 오류를 수정해 주세요.')
        else:
            try:
                signals = evaluate_strategy(data, strategy, timeframe=timeframe)
            except StrategyEvaluationError as error:
                st.error(f'전략 조건 평가 실패: {error}')
    result = st.session_state.get('_backtest_result')
    render_price_chart(data, symbol, timeframe, period, signals=signals,
                       executions=None if result is None else result.events)
    with st.expander('데이터 품질·실제 수집 구간', expanded=False):
        meta = data.attrs
        st.caption(f"요청 {meta['requested_start']} ~ {meta['requested_end']} · 실제 {meta['actual_start']} ~ {meta['actual_end']} UTC · {len(data):,}개 확정 봉")
        st.caption(f"계산용 워밍업 {meta.get('warmup_collected', 0)}봉 · 가격 변화율은 해당 확정 봉의 시가 기준")
        for reason in meta.get('warmup_reasons', ()): st.warning(reason)
        missing = [name for key, name, default in INDICATORS if st.session_state.get(key, default)
            and any(not isfinite(last[c]) for c in {'show_rsi':['rsi_14'], 'show_macd':['macd_histogram'],
                'show_ema':['ema_20','ema_60'], 'show_sma':['sma_20','sma_60'],
                'show_bollinger':['bb_upper','bb_middle','bb_lower'], 'show_volume_sma':['volume_sma_20']}[key])]
        if missing: st.info(', '.join(missing) + ' · 계산에 필요한 연속 확정 봉이 부족합니다.')
        if isfinite(last['volume_sma_20']):
            st.caption(f"거래량 {last['volume']:,.4f} · 20봉 평균 {last['volume_sma_20']:,.4f} {symbol.split('/')[0]}")
            ratio=last['volume_ratio_20']
            st.caption(f'평균 대비 {ratio:.2f}배' if isfinite(ratio) else '평균이 0이어서 거래량 비율 없음')
    if signals is not None:
        with st.expander('전략 조건 충족 상세', expanded=False):
            render_signal_preview(data, strategy, signals)
