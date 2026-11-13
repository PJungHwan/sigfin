"""Six task adapters. Only the selected page/task renders or handles events."""

import streamlit as st

from ui.workspace_state import (common_context, current_strategy, current_backtest_settings,
    selected_macro, MACRO_CHOICES, prepare_portfolio,
    validate_current_backtest)
from ui.market_view import market_data, render_market, render_price_chart, indicator_controls


def task_selector(options, key):
    return st.segmented_control('작업 선택', options, default=options[0], key=key,
                                selection_mode='single', required=True, persist_state='session')


def market_page():
    render_market(*common_context())


def strategy_page():
    from ui.strategy_builder import render_strategy_builder
    from ui.backtest_view import (render_backtest_settings, render_backtest_actions,
                                  execute_or_get_result, risk_summary)
    from ui.experiment_view import render_experiments
    symbol, timeframe, period = common_context()
    task = task_selector(('전략 편집', '단일 백테스트', '전략 비교', '저장 실험 재현'), 'strategy_task')
    strategy = current_strategy()
    if task == '전략 편집':
        strategy = render_strategy_builder(expanded=True)
    with st.expander('공통 원금·거래 비용·위험관리', expanded=task == '단일 백테스트'):
        config, risk = render_backtest_settings(strategy)
    st.caption('현재 전략: ' + (strategy.name if strategy else '조건 검증 필요') + ' · ' + (risk_summary(risk) if risk else '위험 설정 오류'))
    if task == '저장 실험 재현':
        st.info('과거 고정 입력을 사용합니다. 현재 조회 조건·위험 설정은 업로드 실험을 덮어쓰지 않습니다.')
        render_experiments(None, strategy, config, risk, symbol, timeframe, period, panel='replay')
        return
    refresh = st.button('새로고침', key='refresh_market', icon=':material/refresh:')
    data = market_data(symbol, timeframe, period, refresh=refresh)
    if task == '전략 비교':
        render_experiments(data, strategy, config, risk, symbol, timeframe, period, panel='current')
        return
    if task == '단일 백테스트':
        requested, compare = render_backtest_actions(strategy, config, risk)
        if data is not None:
            result = execute_or_get_result(data, strategy, config, symbol, timeframe, period,
                requested, risk=risk, compare_requested=compare)
            with st.expander('가격·가상 체결 차트', expanded=False):
                indicator_controls()
                render_price_chart(data, symbol, timeframe, period,
                    executions=None if result is None else result.events)
        else:
            validate_current_backtest(None, strategy, config, risk, symbol, timeframe, period)
            st.info('확정 시장 데이터를 조회한 뒤 실행해 주세요.')
    else:
        validate_current_backtest(data, strategy, config, risk, symbol, timeframe, period)
        st.caption('조건 신호는 시장 분석의 ‘전략 신호 표시’에서 확인할 수 있습니다. 실제 체결 결과와 다릅니다.')


def portfolio_page():
    from ui.portfolio_view import render_portfolio, load_portfolio_prices, portfolio_now
    from ui.portfolio_risk_view import render_portfolio_risk
    from ui.storage_view import render_account_history
    from services.portfolio_service import quotes_from_frames
    _, timeframe, period = common_context()
    task = task_selector(('보유·가상 기록', '위험 분석', '거래·평가 이력'), 'portfolio_task')
    context = prepare_portfolio(timeframe, period)
    if task == '보유·가상 기록':
        render_portfolio(timeframe, period, workspace=True)
        return
    if context is None:
        st.info('보유·가상 기록에서 가상 계좌를 만들거나 저장 이력에서 복원해 주세요.')
        return
    if st.button('BTC/ETH 확정 가격 조회', key='pf_refresh_context'):
        try:
            frames = load_portfolio_prices(timeframe, period)
            quotes_from_frames(frames, now=portfolio_now())
            st.session_state['_portfolio_frames'] = frames
        except Exception:
            st.session_state['_portfolio_frames'] = {}
            st.error('가격 조회/검증에 실패했습니다. 원장은 보존하며 가짜 가격으로 대체하지 않습니다.')
        context = prepare_portfolio(timeframe, period)
    if task == '위험 분석':
        render_portfolio_risk(price_context=context)
    else:
        render_account_history(st.session_state['_portfolio_account'], context.quotes,
                              now=context.valuation.evaluated_at_utc)


def macro_page():
    from ui.macro_view import render_macro_controls, render_macro_data
    from ui.crypto_macro_view import render_crypto_macro_controls, render_crypto_macro_data
    symbol, _, _ = common_context()
    task = task_selector(('경제지표 조회', '암호화폐·거시경제 관계'), 'macro_task')
    st.caption('관측일 ≠ 발표일 · 현재 개정값을 과거 당시 공개값으로 사용하지 않습니다.')
    if task == '경제지표 조회':
        columns = st.columns(3)
        for i, (key, _, label) in enumerate(MACRO_CHOICES):
            columns[i % 3].checkbox(label, key=key, persist_state='session')
        selected = selected_macro()
        render_macro_data(selected, render_macro_controls(selected))
    else:
        render_crypto_macro_data(symbol, render_crypto_macro_controls(symbol))


def ai_page():
    from ui.research_view import render_research
    from ui.workflow_view import render_workflow_controls, render_workflow_result
    from ui.ai_analysis_view import render_ai_controls, render_ai_analysis, render_ai_budget_controls
    from ui.risk_manager_view import render_risk_manager_controls
    from ui.storage_view import render_workflow_save
    from ui.components import agent_status, metric_card
    symbol, timeframe, period = common_context()
    task = task_selector(('통합 리포트', '에이전트 워크플로우', '단일 AI 분석(고급)'), 'ai_task')
    render_ai_budget_controls()
    data = market_data(symbol, timeframe, period)
    strategy = current_strategy()
    config, risk = current_backtest_settings()
    validate_current_backtest(data, strategy, config, risk, symbol, timeframe, period)
    prepare_portfolio(timeframe, period)
    selected = selected_macro()
    st.caption('기술/Macro 자료와 Python 계산 결과를 분리합니다. Mock Debate·Risk는 실제 LLM 협업이 아닙니다.')
    if task == '통합 리포트':
        with st.expander('Mock Risk Manager 가상 설정 · 보유 계좌와 별개', expanded=False):
            enabled, settings = render_risk_manager_controls()
        if enabled and settings is None:
            st.error('Risk Manager 설정 오류를 수정해 주세요. 유효하지 않은 설정으로 실행하지 않습니다.')
            return
        render_research(data, symbol, timeframe, period, selected, strategy=strategy,
            backtest_config=config, backtest_risk=risk, risk_agent_settings=settings)
    elif task == '에이전트 워크플로우':
        controls = render_workflow_controls()
        slots = []
        with st.container(horizontal=True):
            for name in ('Technical Agent', 'Macro Agent', 'Bull Agent', 'Bear Agent', 'Risk Manager'):
                slot = st.empty()
                with slot: agent_status(name, 'WAITING' if len(slots) < 2 else 'NOT CONNECTED')
                slots.append(slot)
        render_workflow_result(data, symbol, timeframe, period, selected, controls, *slots)
        render_workflow_save(data, symbol, timeframe, period, selected, strategy=strategy,
                             backtest_config=config, backtest_risk=risk)
    else:
        controls = render_ai_controls(budget_controls=False)
        requested = st.button('AI 분석 실행 ▶', key='analyze_ai', type='primary')
        signal = st.empty()
        with signal: metric_card('AI SIGNAL · 단일 분석', '-', '현재 경로 결과만 표시')
        render_ai_analysis(data, symbol, timeframe, period, selected, controls, requested, signal)


def history_page():
    from ui.storage_view import render_analysis_history, render_account_history
    symbol, timeframe, period = common_context()
    task = task_selector(('연구 결과', '가상 계좌·평가 이력'), 'history_task')
    st.info('과거 저장 자료 조회 · 현재 분석/원장을 자동 교체하지 않습니다. API 재호출 없음')
    if task == '연구 결과':
        render_analysis_history(None, symbol, timeframe, period, (), show_save=False)
    else:
        context = prepare_portfolio(timeframe, period)
        if context is None:
            st.caption('저장된 가상 계좌가 없습니다. 계좌를 자동 생성하지 않습니다.')
        else:
            render_account_history(st.session_state['_portfolio_account'], context.quotes,
                                  now=context.valuation.evaluated_at_utc)
