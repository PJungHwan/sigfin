"""확정 봉의 표본 변동성/종가 MDD/Wilder ATR. 갭을 수익률이나 ATR로 연결하지 않는다."""

from math import sqrt

import numpy as np
import pandas as pd

from backtest.metrics import maximum_drawdown
from config.risk_settings import ATR_PERIOD, ANNUAL_DAYS
from config.settings import OHLCV_COLUMNS, TIMEFRAME_MILLISECONDS
from risk.models import QuantitativeMetrics, RiskDataError, RiskThresholds


def calculate_atr(data, period=ATR_PERIOD, *, timeframe="1h"):
    """TR=max(H-L, |H-prevC|, |L-prevC|). 각 연속 구간 첫 TR=H-L.

    첫 period개 TR의 산술평균으로 seed, 이후 (ATR*(period-1)+TR)/period.
    구간 초반/갭 이후 period 미만은 NaN. 입력 순서/원본은 변경하지 않는다.
    """
    if type(period) is not int or period < 1 or timeframe not in TIMEFRAME_MILLISECONDS:
        raise RiskDataError("ATR 기간 또는 봉 설정이 유효하지 않습니다.")
    output = pd.Series(np.nan, index=data.index, dtype=float, name=f"atr_{period}")
    interval = pd.Timedelta(milliseconds=TIMEFRAME_MILLISECONDS[timeframe])
    previous_time = previous_close = atr = None
    seed = []
    for position, (_, row) in enumerate(data.iterrows()):
        stamp = row.timestamp
        if previous_time is None or stamp-previous_time != interval:
            previous_close = atr = None
            seed = []
        high, low, close = float(row.high), float(row.low), float(row.close)
        if not np.isfinite([high, low, close]).all() or low <= 0 or high < low or not low <= close <= high:
            raise RiskDataError("ATR 가격 범위가 유효하지 않습니다.")
        tr = high-low if previous_close is None else max(high-low, abs(high-previous_close), abs(low-previous_close))
        if atr is None:
            seed.append(tr)
            if len(seed) == period:
                atr = float(np.mean(seed))
        else:
            atr = (atr*(period-1)+tr)/period
        if atr is not None:
            output.iloc[position] = atr
        previous_time, previous_close = stamp, close
    return output


def calculate_risk_metrics(data, timeframe, *, now, thresholds=None):
    policy = RiskThresholds.model_validate(thresholds or RiskThresholds())
    if timeframe not in TIMEFRAME_MILLISECONDS or not isinstance(data, pd.DataFrame) or not set(OHLCV_COLUMNS).issubset(data.columns):
        raise RiskDataError("리스크 계산의 OHLCV 컬럼 또는 봉 설정을 확인하세요.")
    current = pd.Timestamp(now)
    if current.tz is None:
        raise RiskDataError("리스크 분석 시각은 UTC 시간대가 필요합니다.")
    step = pd.Timedelta(milliseconds=TIMEFRAME_MILLISECONDS[timeframe])
    try:
        times = data.timestamp
        if times.dt.tz is None or times.isna().any() or times.duplicated().any() or not times.is_monotonic_increasing:
            raise ValueError
        times = times.dt.tz_convert("UTC")
        if ((times+step) > current).any():
            raise ValueError
        if len(data) and any(not pd.api.types.is_numeric_dtype(data[column]) or pd.api.types.is_bool_dtype(data[column])
                             for column in OHLCV_COLUMNS[1:]):
            raise ValueError
        values = data[OHLCV_COLUMNS[1:]].astype(float)
        if (not np.isfinite(values.to_numpy()).all() or (values[["open", "high", "low", "close"]] <= 0).any().any()
                or (values.volume < 0).any() or (values.high < values[["open", "low", "close"]].max(axis=1)).any()
                or (values.low > values[["open", "high", "close"]].min(axis=1)).any()):
            raise ValueError
    except (AttributeError, TypeError, ValueError):
        raise RiskDataError("OHLCV의 값·시간순서·중복·확정 여부 검증에 실패했습니다.") from None
    count = len(data)
    differences = times.diff()
    contiguous = differences.eq(step)
    returns = values.close.pct_change(fill_method=None).where(contiguous)
    clean = returns.dropna()
    missing = int((differences.dropna()/step-1).clip(lower=0).sum())
    issues = []
    if not count:
        issues.append("확정 봉 없음")
    if len(clean) < policy.min_returns:
        issues.append(f"등급 평가 표본 부족: 유효 수익률 {len(clean)}개 / 최소 {policy.min_returns}개")
    if (differences.dropna() != step).any():
        issues.append("봉 간격 불일치/누락: 갭 수익률은 계산에서 제외")
    if data.attrs.get("is_partial") or data.attrs.get("missing_candles", 0):
        issues.append("수집 메타데이터의 일부 수집/누락 표시")
    factor = sqrt(ANNUAL_DAYS*86400/step.total_seconds())
    vol = float(clean.std(ddof=1)*100) if len(clean) >= 2 else None
    window = policy.volatility_window
    recent = returns.iloc[-window:]
    previous = returns.iloc[-2*window:-window]
    recent_vol = float(recent.std(ddof=1)*100) if len(recent) == window and recent.notna().all() else None
    previous_vol = float(previous.std(ddof=1)*100) if len(previous) == window and previous.notna().all() else None
    ratio = recent_vol/previous_vol if recent_vol is not None and previous_vol is not None and previous_vol > 0 else None
    expanding = (ratio >= policy.expansion_ratio if ratio is not None else
                 bool(recent_vol is not None and previous_vol == 0 and recent_vol > 0))
    close_mdd = maximum_drawdown(values.close) if count >= 2 else None
    trough = peak = None
    if close_mdd is not None and close_mdd > 0:
        drawdowns = values.close/values.close.cummax()-1
        trough_position = int(np.argmin(drawdowns.to_numpy()))
        peak_position = int(np.argmax(values.close.iloc[:trough_position+1].to_numpy()))
        trough, peak = (times.iloc[trough_position]+step).isoformat(), (times.iloc[peak_position]+step).isoformat()
    atr_series = calculate_atr(data, timeframe=timeframe)
    atr = float(atr_series.iloc[-1]) if count and pd.notna(atr_series.iloc[-1]) else None
    segment = 0
    for position in range(count-1, -1, -1):
        segment += 1
        if position == 0 or not contiguous.iloc[position]:
            break
    volume_mean = None
    prior_volume = values.volume.iloc[-policy.volume_window-1:-1]
    if len(prior_volume) == policy.volume_window and count > policy.volume_window and contiguous.iloc[-policy.volume_window:].all():
        volume_mean = float(prior_volume.mean())
    latest_volume = float(values.volume.iloc[-1]) if count else None
    volume_ratio = latest_volume/volume_mean if latest_volume is not None and volume_mean is not None and volume_mean > 0 else None
    volume_spike = volume_ratio >= policy.volume_spike_ratio if volume_ratio is not None else bool(volume_mean == 0 and latest_volume is not None and latest_volume > 0)
    if volume_mean == 0:
        issues.append("직전 거래량 평균이 0이어서 거래량 배수 해석 불가")
    close = float(values.close.iloc[-1]) if count else None
    as_of = times.iloc[-1]+step if count else None
    return QuantitativeMetrics(timeframe=timeframe, candle_count=count, return_count=len(clean), expected_interval_seconds=step.total_seconds(),
        observed_intervals_seconds=sorted(set(differences.dropna().dt.total_seconds().tolist())),
        window_start_utc=times.iloc[0].isoformat() if count else None, data_as_of_utc=as_of.isoformat() if count else None,
        latest_close=close, volatility_per_bar_pct=vol, annualization_factor=factor, annualized_volatility_pct=vol*factor if vol is not None else None,
        recent_volatility_pct=recent_vol, previous_volatility_pct=previous_vol, volatility_expansion_ratio=ratio, volatility_expanding=expanding,
        close_mdd_pct=close_mdd, mdd_peak_time_utc=peak, mdd_trough_time_utc=trough, atr_14=atr,
        atr_close_pct=atr/close*100 if atr is not None else None, atr_contiguous_candles=segment,
        latest_volume=latest_volume, previous_volume_sma=volume_mean, volume_ratio=volume_ratio, volume_spike=volume_spike,
        missing_candles=missing, quality_issues=issues, stale=bool(as_of is not None and current-as_of > step*2))
