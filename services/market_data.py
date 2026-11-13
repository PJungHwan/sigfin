"""Streamlit 데이터 캐시. 새로고침은 선택한 조회 조합의 캐시만 비운다."""

import pandas as pd
import streamlit as st

from collectors.binance import fetch_binance_ohlcv
from config.settings import (
    DATA_CACHE_MAX_ENTRIES, DATA_CACHE_TTL_SECONDS, INDICATOR_WARMUP_CANDLES,
)
from indicators.technical import add_technical_indicators


@st.cache_data(
    ttl=DATA_CACHE_TTL_SECONDS,
    max_entries=DATA_CACHE_MAX_ENTRIES,
    show_spinner=False,
)
def load_market_data(symbol: str, timeframe: str, period: str, *, start_time: str | None = None) -> pd.DataFrame:
    """워밍업을 포함해 계산하고 선택 기간만 반환한다. 표시 선택은 캐시 키와 무관하다."""
    options = {"warmup_candles": INDICATOR_WARMUP_CANDLES}
    if start_time is not None:
        options = {"warmup_candles": 0, "start_time": pd.Timestamp(start_time)}
    raw = fetch_binance_ohlcv(symbol, timeframe, period, **options)
    calculated = add_technical_indicators(raw, timeframe=timeframe)
    analysis_start = raw.attrs.get("analysis_start")
    if analysis_start is not None:
        calculated = calculated.loc[calculated["timestamp"] >= analysis_start].copy()
    calculated.attrs["actual_start"] = calculated.iloc[0]["timestamp"]
    return calculated
