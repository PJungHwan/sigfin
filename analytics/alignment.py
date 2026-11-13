"""관측 월 라벨의 사후 정렬. FRED 공개 시점 추정이나 거시 값 forward-fill은 하지 않는다."""

import numpy as np
import pandas as pd

from analytics.models import AnalysisError, AnalysisConfig
from config.settings import TIMEFRAME_MILLISECONDS
from models.macro import SERIES


def utc_time(value):
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        raise AnalysisError("시간대가 지정된 유효한 시각이 필요합니다.") from None
    if pd.isna(timestamp) or timestamp.tzinfo is None:
        raise AnalysisError("시간대가 지정된 유효한 시각이 필요합니다.")
    return timestamp.tz_convert("UTC")


def _times(data, column):
    try:
        values = data[column].dt.tz_convert("UTC")
    except (KeyError, AttributeError, TypeError):
        raise AnalysisError(f"{column}은 시간대가 있는 datetime 컬럼이어야 합니다.") from None
    if values.isna().any() or not values.is_unique or not values.is_monotonic_increasing:
        raise AnalysisError("날짜는 결측·중복 없이 시간순이어야 합니다.")
    return values


def closed_prices(data, timeframe, as_of):
    if timeframe not in TIMEFRAME_MILLISECONDS or "close" not in data:
        raise AnalysisError("유효한 원천 봉과 종가가 필요합니다.")
    result = data.copy(deep=True)
    result["timestamp"] = _times(result, "timestamp")
    prices = pd.to_numeric(result.close, errors="coerce")
    if not np.isfinite(prices.to_numpy(dtype=float)).all() or (prices <= 0).any():
        raise AnalysisError("종가는 유한한 양수여야 합니다.")
    result["close"] = prices
    interval = pd.Timedelta(milliseconds=TIMEFRAME_MILLISECONDS[timeframe])
    if (result.timestamp != result.timestamp.dt.floor(interval)).any():
        raise AnalysisError("봉 시작 시각이 UTC 타임프레임 경계와 맞지 않습니다.")
    result["candle_end"] = result.timestamp + interval
    # 마지막 진행 봉이나 분석 기준 시각 이후에 마감된 봉을 소급 사용하지 않는다.
    return result.loc[result.candle_end <= utc_time(as_of)].copy()


def _month_index(values):
    return pd.DatetimeIndex(values).tz_convert("UTC").tz_localize(None).to_period("M")


def crypto_monthly(data, timeframe: str, *, as_of) -> pd.DataFrame:
    closed = closed_prices(data, timeframe, as_of)
    columns = ["crypto_close", "candle_end", "candle_count", "expected_candles",
               "crypto_return_pct", "crypto_partial", "baseline_available"]
    if closed.empty:
        return pd.DataFrame(columns=columns, index=pd.PeriodIndex([], freq="M"))
    # 9/30 일봉은 10/1 00:00에 마감한다. 그 종가는 9월 월말 종가에 속한다.
    closed["month"] = _month_index(closed.candle_end-pd.Timedelta(nanoseconds=1))
    grouped = closed.groupby("month").agg(crypto_close=("close", "last"), candle_end=("candle_end", "last"),
                                            candle_count=("close", "size"))
    result = grouped.reindex(pd.period_range(grouped.index.min(), grouped.index.max(), freq="M"))
    result["candle_count"] = result.candle_count.fillna(0).astype(int)
    starts = result.index.to_timestamp().tz_localize("UTC")
    ends = (result.index+1).to_timestamp().tz_localize("UTC")
    interval = pd.Timedelta(milliseconds=TIMEFRAME_MILLISECONDS[timeframe])
    result["expected_candles"] = ((ends-starts)/interval).astype(int)
    result["baseline_available"] = result.candle_end.shift(1).to_numpy() == starts.to_numpy()
    result["crypto_return_pct"] = (result.crypto_close/result.crypto_close.shift(1)-1)*100
    result["crypto_partial"] = ((result.candle_count != result.expected_candles) |
                                 (result.candle_end.to_numpy() != ends.to_numpy()) |
                                 ~result.baseline_available)
    return result[columns]


def periodic_crypto_returns(data, timeframe: str, *, rule: str, as_of) -> pd.DataFrame:
    """고정 UTC 시간/일 간격. 더 큰 원천 봉을 작은 봉으로 복제하지 않는다."""
    target = {"1h": pd.Timedelta(hours=1), "1d": pd.Timedelta(days=1)}
    if rule not in target or timeframe not in TIMEFRAME_MILLISECONDS:
        raise AnalysisError("시간별 또는 일별 수익률만 지원합니다.")
    columns = ["close", "candle_end", "return_pct", "complete"]
    if pd.Timedelta(milliseconds=TIMEFRAME_MILLISECONDS[timeframe]) > target[rule]:
        result = pd.DataFrame(columns=columns)
        result.attrs["unavailable_reason"] = "원천 봉보다 작은 주기의 수익률은 계산하지 않습니다."
        return result
    closed = closed_prices(data, timeframe, as_of)
    if closed.empty:
        return pd.DataFrame(columns=columns)
    freq = "h" if rule == "1h" else "D"
    closed["period"] = (closed.candle_end-pd.Timedelta(nanoseconds=1)).dt.floor(freq)
    grouped = closed.groupby("period").agg(close=("close", "last"), candle_end=("candle_end", "last"), count=("close", "size"))
    result = grouped.reindex(pd.date_range(grouped.index.min(), grouped.index.max(), freq=freq))
    expected = target[rule]/pd.Timedelta(milliseconds=TIMEFRAME_MILLISECONDS[timeframe])
    result["complete"] = ((result["count"] == expected) & (result.candle_end.to_numpy() == (result.index+target[rule]).to_numpy()))
    raw = (result.close/result.close.shift(1)-1)*100
    result["return_pct"] = raw.where(result.complete & result.complete.shift(1, fill_value=False))
    return result[columns]


def macro_monthly(data, config: AnalysisConfig, *, as_of):
    spec = SERIES[config.series_id]
    if not {"observation_date", "value", "series_id", "frequency", "units"}.issubset(data.columns):
        raise AnalysisError("FRED 관측 데이터 구조가 올바르지 않습니다.")
    if (not data.series_id.eq(config.series_id).all() or not data.frequency.eq(spec.frequency).all()
            or not data.units.eq(spec.units).all()):
        raise AnalysisError("FRED 지표·주기·단위가 설정과 일치하지 않습니다.")
    source = data.copy(deep=True)
    source["observation_date"] = _times(source, "observation_date")
    if (source.observation_date.dt.normalize() != source.observation_date).any():
        raise AnalysisError("FRED 관측일은 UTC 달력 날짜 라벨이어야 합니다.")
    if spec.frequency == "M" and (source.observation_date.dt.day != 1).any():
        raise AnalysisError("월별 FRED 관측일은 월초 라벨이어야 합니다.")
    try:
        source["value"] = pd.to_numeric(source.value, errors="raise")
    except (TypeError, ValueError):
        raise AnalysisError("FRED 관측값은 숫자 또는 결측값이어야 합니다.") from None
    if np.isinf(source.value.to_numpy(dtype=float)).any():
        raise AnalysisError("FRED 값에 무한대가 있습니다.")
    source = source.loc[source.observation_date <= utc_time(as_of).normalize()].copy()
    source["month"] = _month_index(source.observation_date)
    daily = source.drop(columns="month").copy() if spec.frequency == "D" else pd.DataFrame()
    if spec.frequency == "D":
        daily["daily_change_pp"] = daily.value.diff()  # 결측 행을 건너뛰거나 채우지 않는다.
        grouped = source.groupby("month").agg(macro_level=("value", config.dgs_aggregation),
                                                 macro_count=("value", "count"))
        grouped["macro_observation_date"] = source.loc[source.value.notna()].groupby("month").observation_date.last()
    else:
        grouped = source.set_index("month")[["value"]].rename(columns={"value": "macro_level"})
        grouped["macro_count"] = grouped.macro_level.notna().astype(int)
        grouped["macro_observation_date"] = source.set_index("month").observation_date
    if grouped.empty:
        index = pd.PeriodIndex([], freq="M")
    else:
        index = pd.period_range(grouped.index.min(), grouped.index.max(), freq="M")
    result = grouped.reindex(index)
    result["macro_count"] = result.macro_count.fillna(0).astype(int)
    previous = result.macro_level.shift(1)
    if config.series_id == "CPIAUCSL":
        result["macro_mom_pct"] = ((result.macro_level/previous-1)*100).where(previous > 0)
        yearly = result.macro_level.shift(12)
        result["macro_yoy_pct"] = ((result.macro_level/yearly-1)*100).where(yearly > 0)
        result["macro_change"] = result["macro_mom_pct" if config.transform == "mom_pct" else "macro_yoy_pct"]
    else:
        # 금리/실업률에는 % 변화율을 혼합하지 않는다. 수준 차이는 %p, PAYEMS는 천 명이다.
        result["macro_change"] = result.macro_level-previous
    result["macro_partial"] = (result.index+1).to_timestamp().tz_localize("UTC") > utc_time(as_of)
    return result, daily


def align_monthly(crypto, macro, *, start, end, include_partial=False):
    start, end = utc_time(start), utc_time(end)
    if start > end:
        raise AnalysisError("분석 시작일이 종료일보다 늦습니다.")
    # 봉 마감 기준 end는 배타적 달력 경계다. 월초 00:00이면 직전 월까지 표시한다.
    last = end-pd.Timedelta(nanoseconds=1) if end > start else end
    index = pd.period_range(start.tz_localize(None).to_period("M"), last.tz_localize(None).to_period("M"), freq="M")
    result = crypto.reindex(index).join(macro.reindex(index))
    boundary = ((index.to_timestamp().tz_localize("UTC") < start) |
                ((index+1).to_timestamp().tz_localize("UTC") > end))
    for column in ("crypto_partial", "macro_partial"):
        result[column] = result[column].fillna(True).astype(bool) | boundary
    result["crypto_eligible"] = True if include_partial else ~result.crypto_partial.fillna(True).astype(bool)
    result["macro_eligible"] = True if include_partial else ~result.macro_partial.fillna(True).astype(bool)
    return result


def normalized_levels(monthly):
    finite = np.isfinite(monthly[["crypto_close", "macro_level"]].to_numpy(dtype=float)).all(axis=1)
    if not finite.any():
        return pd.DataFrame(columns=["crypto_index", "macro_index"], index=pd.PeriodIndex([], freq="M"))
    base = monthly.index[finite][0]
    result = pd.DataFrame(index=monthly.loc[base:].index)
    result["crypto_index"] = monthly.loc[base:, "crypto_close"]/monthly.loc[base, "crypto_close"]*100
    if monthly.loc[base, "macro_level"] <= 0:
        result["macro_index"] = np.nan
        result.attrs["excluded_reason"] = "공통 기준월 거시 수준이 0 이하라 기준화에서 제외했습니다."
    else:
        result["macro_index"] = monthly.loc[base:, "macro_level"]/monthly.loc[base, "macro_level"]*100
    result.attrs["base_month"] = str(base)
    return result
