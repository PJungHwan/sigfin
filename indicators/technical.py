"""현재 봉과 과거 데이터만 사용하는 기술적 분석 엔진.

계산 규칙
---------
* RSI: 첫 N개 변화량의 SMA로 평균 상승/하락을 초기화한 뒤 Wilder(α=1/N)
  갱신. N+1개 종가부터 유효하다. 상승만 있으면 100, 하락만 있으면 0,
  완전히 평탄한 구간은 중립값 50으로 정의한다.
* EMA: 첫 N개 연속 유효값의 SMA로 초기화하고 α=2/(N+1)로 갱신한다.
* MACD: 독립적으로 초기화한 fast/slow EMA의 차이. Signal은 MACD의 첫
  signal_period개 유효값의 SMA로 초기화한다. 기본 Histogram은 34봉부터 유효.
* SMA / Bollinger / Volume SMA: 현재 봉을 포함한 과거 N봉, center=False.
  Bollinger는 모집단 표준편차(ddof=0), 기본 SMA20 ± 2σ이다.
* NaN/비유한 값은 보간하지 않는다. 재귀 지표는 결측 이후 다시 초기화한다.
  add_technical_indicators는 timeframe을 알면 누락된 시간 구간에서도 모든
  지표를 다시 초기화한다. timestamp를 재정렬하거나 원본 값을 수정하지 않는다.

공식 참고: https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/relative-strength-index-rsi
EMA 초기화 참고: https://github.com/TA-Lib/ta-lib/blob/main/docs/studies/ema-seeding/README.md
"""

from copy import deepcopy

import numpy as np
import pandas as pd

from config.settings import OHLCV_COLUMNS, TIMEFRAME_MILLISECONDS

INDICATOR_COLUMNS = [
    "rsi_14", "macd", "macd_signal", "macd_histogram",
    "ema_20", "ema_60", "sma_20", "sma_60",
    "bb_upper", "bb_middle", "bb_lower", "volume_sma_20", "volume_ratio_20",
]


def _validate_period(period: int) -> None:
    if isinstance(period, bool) or not isinstance(period, (int, np.integer)) or period < 1:
        raise ValueError("기간은 1 이상의 정수여야 합니다.")


def _numeric(values: pd.Series) -> pd.Series:
    if not isinstance(values, pd.Series):
        raise TypeError("입력은 pandas Series여야 합니다.")
    numeric = pd.to_numeric(values, errors="coerce").astype(float)
    return numeric.where(np.isfinite(numeric))


def _seeded_average(values: pd.Series, period: int, alpha: float) -> pd.Series:
    """연속된 첫 N개 값으로 seed를 만들고 결측 이후 새 seed를 기다린다."""
    output = np.full(len(values), np.nan)
    count = 0
    total = 0.0
    average = np.nan
    for position, value in enumerate(values.to_numpy(dtype=float)):
        if not np.isfinite(value):
            count, total, average = 0, 0.0, np.nan
            continue
        if count < period:
            count += 1
            total += value
            if count == period:
                average = total / period
        else:
            average = alpha * value + (1.0 - alpha) * average
        output[position] = average
    return pd.Series(output, index=values.index)


def calculate_ema(close: pd.Series, period: int = 20) -> pd.Series:
    """SMA seed + α=2/(N+1). 첫 유효값은 위치 N-1이다."""
    _validate_period(period)
    return _seeded_average(_numeric(close), period, 2.0 / (period + 1)).rename(f"ema_{period}")


def calculate_sma(close: pd.Series, period: int = 20) -> pd.Series:
    _validate_period(period)
    return _numeric(close).rolling(period, min_periods=period, center=False).mean().rename(f"sma_{period}")


def calculate_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI. 0/0은 50, 양의 상승/0 하락은 100으로 처리한다."""
    _validate_period(period)
    delta = _numeric(close).diff()
    gain = _seeded_average(delta.clip(lower=0), period, 1.0 / period)
    loss = _seeded_average(-delta.clip(upper=0), period, 1.0 / period)
    total = gain + loss
    # gain/(gain+loss)는 RS 기반 식과 동치이며 loss=0에서도 안전하다.
    result = 100.0 * gain.div(total.where(total > 0))
    result = result.mask(total.eq(0), 50.0).clip(0.0, 100.0)
    return result.rename(f"rsi_{period}")


def calculate_macd(
    close: pd.Series, fast_period: int = 12, slow_period: int = 26,
    signal_period: int = 9,
) -> pd.DataFrame:
    for period in (fast_period, slow_period, signal_period):
        _validate_period(period)
    if fast_period >= slow_period:
        raise ValueError("MACD fast 기간은 slow 기간보다 작아야 합니다.")
    line = calculate_ema(close, fast_period) - calculate_ema(close, slow_period)
    signal = calculate_ema(line, signal_period)
    return pd.DataFrame({"macd": line, "macd_signal": signal, "macd_histogram": line - signal})


def calculate_bollinger_bands(
    close: pd.Series, period: int = 20, std_multiplier: float = 2.0,
) -> pd.DataFrame:
    """현재 봉을 포함한 N봉 SMA ± kσ. 모집단 표준편차(ddof=0)를 사용한다."""
    _validate_period(period)
    if not np.isfinite(std_multiplier) or std_multiplier <= 0:
        raise ValueError("표준편차 배수는 양의 유한한 수여야 합니다.")
    values = _numeric(close)
    middle = calculate_sma(values, period)
    deviation = values.rolling(period, min_periods=period, center=False).std(ddof=0)
    return pd.DataFrame({
        "bb_upper": middle + std_multiplier * deviation,
        "bb_middle": middle,
        "bb_lower": middle - std_multiplier * deviation,
    })


def calculate_volume_sma(volume: pd.Series, period: int = 20) -> pd.Series:
    """현재 확정 봉을 포함한 N봉 거래량 평균."""
    return calculate_sma(volume, period).rename(f"volume_sma_{period}")


def add_technical_indicators(
    data: pd.DataFrame, *, timeframe: str | None = None,
) -> pd.DataFrame:
    """원본 OHLCV/인덱스/attrs를 보존한 새 DataFrame에 모든 지표를 추가한다.

    timeframe은 인수 또는 data.attrs['timeframe']에서 읽는다. 지정되지 않으면
    행 간 시간 간격을 추정하지 않으며, 값의 결측만으로 재귀 계산을 초기화한다.
    정렬되지 않았거나 중복/결측 timestamp가 있으면 명시적 ValueError를 발생시킨다.
    """
    if not set(OHLCV_COLUMNS).issubset(data.columns):
        raise ValueError("기술적 지표 계산에 timestamp/open/high/low/close/volume이 필요합니다.")
    times = pd.to_datetime(data["timestamp"], utc=True, errors="coerce")
    if times.isna().any() or not times.is_monotonic_increasing or not times.is_unique:
        raise ValueError("timestamp는 결측·중복 없이 시간순으로 정렬되어야 합니다.")
    selected_timeframe = timeframe or data.attrs.get("timeframe")
    if selected_timeframe is not None and selected_timeframe not in TIMEFRAME_MILLISECONDS:
        raise ValueError(f"지원하지 않는 Timeframe입니다: {selected_timeframe}")

    boundaries = [0]
    if selected_timeframe is not None and len(data) > 1:
        expected = pd.Timedelta(milliseconds=TIMEFRAME_MILLISECONDS[selected_timeframe])
        boundaries.extend((np.flatnonzero(times.diff().iloc[1:].ne(expected).to_numpy()) + 1).tolist())
    gap_resets = len(boundaries) - 1
    boundaries.append(len(data))
    arrays = {column: np.full(len(data), np.nan) for column in INDICATOR_COLUMNS}
    for start, stop in zip(boundaries[:-1], boundaries[1:]):
        close = data["close"].iloc[start:stop]
        volume = data["volume"].iloc[start:stop]
        block = {
            "rsi_14": calculate_rsi(close),
            "ema_20": calculate_ema(close, 20), "ema_60": calculate_ema(close, 60),
            "sma_20": calculate_sma(close, 20), "sma_60": calculate_sma(close, 60),
            "volume_sma_20": calculate_volume_sma(volume),
        }
        block.update(calculate_macd(close).items())
        block.update(calculate_bollinger_bands(close).items())
        average_volume = block["volume_sma_20"]
        block["volume_ratio_20"] = _numeric(volume).div(average_volume.where(average_volume > 0))
        for column, values in block.items():
            arrays[column][start:stop] = values.to_numpy(dtype=float)
    result = data.copy(deep=True)
    for column, values in arrays.items():
        result[column] = values
    result.attrs = deepcopy(data.attrs)
    result.attrs["technical_indicators"] = {
        "ema_seed": "sma", "rsi_smoothing": "wilder", "flat_rsi": 50.0,
        "bollinger_ddof": 0, "bollinger_multiplier": 2.0,
        "timeframe": selected_timeframe, "gap_resets": gap_resets,
    }
    return result
