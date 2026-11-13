"""SIGFIN entrypoint: shared selection once, selected workspace only."""
from pathlib import Path

import streamlit as st

from config.settings import (APP_VERSION, DEFAULT_SYMBOL, DEFAULT_TIMEFRAME, DEFAULT_PERIOD,
                             SUPPORTED_SYMBOLS, SUPPORTED_TIMEFRAMES, SUPPORTED_PERIODS)
from ui.design import BRAND
from ui.styles import apply_custom_css

ROOT = Path(__file__).resolve().parent
st.set_page_config(page_title=BRAND, page_icon=":material/monitoring:", layout="wide",
                   initial_sidebar_state="expanded")
apply_custom_css()
st.session_state['_ui_run'] = st.session_state.get('_ui_run', 0) + 1

with st.sidebar:
    st.caption("시그핀 · 금융 연구 작업 공간")
st.logo(str(ROOT / 'ui/assets/sigfin.svg'), size='large')

pages = [
    st.Page(ROOT / "app_pages/market.py", title="시장 분석", icon=":material/candlestick_chart:", default=True),
    st.Page(ROOT / "app_pages/strategy.py", title="전략·백테스트", icon=":material/query_stats:"),
    st.Page(ROOT / "app_pages/portfolio.py", title="포트폴리오", icon=":material/account_balance_wallet:"),
    st.Page(ROOT / "app_pages/macro.py", title="거시경제", icon=":material/public:"),
    st.Page(ROOT / "app_pages/research.py", title="AI 연구", icon=":material/hub:"),
    st.Page(ROOT / "app_pages/history.py", title="저장 이력", icon=":material/history:"),
]
page = st.navigation(pages, position="sidebar")

with st.sidebar:
    st.caption("연구용 가상 환경 · 실제 주문 없음")
    st.caption(f"계산·저장 버전 {APP_VERSION}")

st.title(page.title)
with st.container(key="global_context"):
    a, b, c = st.columns([2, 1, 1])
    a.selectbox("종목", SUPPORTED_SYMBOLS, index=SUPPORTED_SYMBOLS.index(DEFAULT_SYMBOL),
                key="symbol", persist_state="session")
    b.selectbox("봉 주기", SUPPORTED_TIMEFRAMES, index=SUPPORTED_TIMEFRAMES.index(DEFAULT_TIMEFRAME),
                key="timeframe", persist_state="session")
    c.selectbox("현재 조회 기간", SUPPORTED_PERIODS, index=SUPPORTED_PERIODS.index(DEFAULT_PERIOD),
                key="period", persist_state="session")
st.caption("확정 봉·UTC 기준 · 실제 거래소 주문 없음 · 과거 수익은 미래 성과를 보장하지 않습니다.")
# demo/offline.py supplies the persistent synthetic warning above every page.
page.run()
