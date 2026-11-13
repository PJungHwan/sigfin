"""UI-only context preparation. No rendering dependencies, orders or API calls."""

import streamlit as st

from backtest.models import BacktestConfig, RiskConfig
from portfolio.engine import evaluate
from services.portfolio_service import PortfolioPriceContext, quotes_from_frames
from strategies.storage import strategy_from_dict
from ui.strategy_builder import _initialize

MACRO_CHOICES = (("macro_cpi", "CPIAUCSL", "CPI"), ("macro_fed", "FEDFUNDS", "연방기금금리"),
                 ("macro_nfp", "PAYEMS", "비농업 고용"), ("macro_unrate", "UNRATE", "실업률"),
                 ("macro_dgs10", "DGS10", "10년물 국채금리"))


def current_strategy():
    _initialize()
    try:
        return strategy_from_dict(st.session_state['_strategy_draft'])
    except ValueError:
        return None


def current_backtest_settings():
    """Same defaults and percent conversion as the existing controls."""
    s = st.session_state
    try:
        config = BacktestConfig(s.get('bt_capital', 10000.), s.get('bt_fee', .1)/100,
            s.get('bt_slippage', .05)/100, s.get('bt_participation', 10.)/100)
        values = {}
        for key, default in (('stop_loss', 3.), ('take_profit', 6.), ('trailing_stop', 3.)):
            values[key+'_enabled'] = s.get('risk_'+key+'_enabled', False)
            values[key+'_rate'] = s.get('risk_'+key+'_rate', default)/100
        return config, RiskConfig(**values)
    except (ValueError, TypeError):
        return None, None


def selected_macro():
    return tuple(series for key, series, _ in MACRO_CHOICES if st.session_state.get(key, False))


def prepare_portfolio(timeframe, period):
    """Read existing ledger/history only; don't create a DB or fetch prices."""
    from ui.portfolio_view import autoload_account, portfolio_now
    from services.local_store import LocalStore, default_store_path, StorageError
    autoload_account()
    account = st.session_state.get('_portfolio_account')
    if account is None:
        return None
    if st.session_state.get('_portfolio_price_context') != (timeframe, period):
        st.session_state['_portfolio_frames'] = {}
        st.session_state['_portfolio_price_context'] = (timeframe, period)
    frames = st.session_state.get('_portfolio_frames', {})
    now = portfolio_now()
    try:
        quotes = quotes_from_frames(frames, now=now)
    except (ValueError, TypeError, KeyError):
        quotes = {}
    valuation = evaluate(account, quotes, now=now)
    st.session_state['_portfolio_valuation'] = valuation
    try:
        st.session_state['_portfolio_history'] = (LocalStore().load_valuations(account.account_id)
            if default_store_path().is_file() else [])
    except StorageError:
        st.session_state['_portfolio_history'] = []
        st.error('평가 이력을 읽지 못했습니다. 원본을 초기화하지 않습니다.')
    cached = st.session_state.get('_portfolio_risk')
    if cached is not None:
        from services.portfolio_risk_service import portfolio_risk_signature
        signature = portfolio_risk_signature(account, quotes, cached.settings, frames,
            st.session_state['_portfolio_history'], valuation=valuation)
        if signature != st.session_state.get('_portfolio_risk_signature'):
            st.session_state.pop('_portfolio_risk', None)
            st.session_state.pop('_portfolio_risk_logs', None)
            st.session_state['_portfolio_risk_signature'] = signature
    return PortfolioPriceContext(frames=frames, quotes=quotes, valuation=valuation)


def validate_current_backtest(data, strategy, config, risk, symbol, timeframe, period):
    from ui.backtest_view import clear_backtest_result, result_fingerprint
    if st.session_state.get('_backtest_key') is None:
        return
    if data is None or strategy is None or config is None or risk is None:
        clear_backtest_result()
    elif st.session_state['_backtest_key'] != result_fingerprint(data, strategy, config, symbol, timeframe, period, risk):
        clear_backtest_result()


def common_context():
    s = st.session_state
    return s['symbol'], s['timeframe'], s['period']
