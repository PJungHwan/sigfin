"""FRED 캐시와 CPI 파생 지표. 암호화폐 데이터/백테스트와 독립적으로 사용한다."""

import hashlib

import numpy as np
import pandas as pd
import streamlit as st

from collectors.fred import fetch_fred_series, get_api_key
from config.settings import FRED_CACHE_MAX_ENTRIES, FRED_CACHE_TTL_SECONDS
from models.macro import FredError, get_series, parse_macro_date


def credential_scope() -> str:
    """사용자 키 원문을 캐시 키/Session State에 저장하지 않고 자격 변경을 구분한다."""
    return hashlib.sha256(get_api_key().encode("utf-8")).hexdigest()


def calculate_cpi_yoy(data: pd.DataFrame) -> pd.DataFrame:
    """같은 달의 1년 전 값으로 계산. 12행 shift/자동 결측 채우기를 사용하지 않는다."""
    if not {"series_id", "observation_date", "value"}.issubset(data.columns) or not data.series_id.eq("CPIAUCSL").all():
        raise FredError("CPIAUCSL 관측 데이터가 필요합니다.", category="DATA_VALIDATION")
    result = data.copy(deep=True)
    dates = result["observation_date"]
    if dates.duplicated().any():
        raise FredError("CPI 전년동월비 계산에는 고유한 관측일이 필요합니다.", category="DATA_VALIDATION")
    reference = pd.Series(result.value.to_numpy(), index=dates)
    previous = reference.reindex(dates - pd.DateOffset(years=1)).to_numpy(dtype=float)
    current = result.value.to_numpy(dtype=float)
    valid = np.isfinite(current) & np.isfinite(previous) & (previous > 0)
    values = np.full(len(result), np.nan)
    values[valid] = (current[valid]/previous[valid]-1)*100
    result["yoy_pct"] = values
    result.attrs["yoy_formula"] = "(CPI_t / CPI_same_month_previous_year - 1) * 100"
    return result


@st.cache_data(ttl=FRED_CACHE_TTL_SECONDS, max_entries=FRED_CACHE_MAX_ENTRIES, show_spinner=False)
def _load_cached(series_id: str, start_date: str, end_date: str, key_scope: str, _api_key: str):
    spec = get_series(series_id)
    # CPI 분모 확보용 과거 12개월은 계산 후 숨긴다.
    fetch_start = ((pd.Timestamp(start_date).replace(day=1) - pd.DateOffset(years=1)).date().isoformat()
                   if series_id == "CPIAUCSL" else start_date)
    data = fetch_fred_series(series_id, fetch_start, end_date, api_key=_api_key)
    if series_id == "CPIAUCSL":
        data = calculate_cpi_yoy(data)
    # API의 허용 경계/계산용 워밍업과 사용자 표시 구간은 별도다. 양 끝 라벨을 포함한다.
    display_start, display_end = parse_macro_date(start_date), parse_macro_date(end_date)
    result = data.loc[data.observation_date.dt.date.between(display_start, display_end)].copy()
    if result.empty:
        raise FredError("요청한 표시 기간에 FRED 관측값이 없습니다.", category="EMPTY_RESPONSE")
    result.attrs.update(display_start=start_date, display_end=end_date,
                        missing_values=int(result.value.isna().sum()))
    if spec.frequency == "M":
        # 누락 월을 생성/보간하지 않고 개수만 보고한다.
        expected = pd.period_range(result.observation_date.iloc[0].strftime("%Y-%m"),
                                   result.observation_date.iloc[-1].strftime("%Y-%m"), freq="M")
        result.attrs["missing_months"] = len(expected)-len(result)
    return result


def load_macro_series(series_id: str, start_date: str, end_date: str, *, refresh: bool = False):
    """키는 호출 시마다 확인. 키가 없어지거나 바뀌면 다른 자격의 캐시를 재사용하지 않는다."""
    start, end = parse_macro_date(start_date), parse_macro_date(end_date)
    if start > end:
        raise FredError("조회 시작일은 종료일보다 늦을 수 없습니다.", category="VALIDATION")
    key = get_api_key()
    scope = hashlib.sha256(key.encode("utf-8")).hexdigest()
    if refresh:
        _load_cached.clear(series_id, start_date, end_date, scope, _api_key=key)
    return _load_cached(series_id, start_date, end_date, scope, _api_key=key)
