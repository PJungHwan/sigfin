"""Binance 공개 현물 OHLCV 수집. 인증, 주문, 임의 데이터 대체는 사용하지 않는다."""

from datetime import datetime
from functools import lru_cache
from math import isfinite
from threading import RLock

import ccxt
import pandas as pd

from collectors.errors import ErrorDetails, diagnose_api_error
from config.settings import (
    API_TIMEOUT_MS,
    BINANCE_PAGE_LIMIT,
    MAX_CANDLES,
    MAX_FETCH_REQUESTS,
    MAX_WARMUP_CANDLES,
    OHLCV_COLUMNS,
    PERIOD_OFFSETS,
    SUPPORTED_SYMBOLS,
    TIMEFRAME_MILLISECONDS,
)

_EXCHANGE_LOCK = RLock()


class DataCollectionError(RuntimeError):
    """화면에 표시할 수 있는 공개 시장 데이터 조회 오류."""

    def __init__(self, message: str, *, details: ErrorDetails | None = None):
        super().__init__(message)
        self.details = details


@lru_cache(maxsize=1)
def _get_exchange() -> ccxt.binance:
    # 클라이언트를 재사용하여 ccxt의 Rate Limit 상태와 시장 정보도 유지한다.
    # 현물 시장 정보만 읽어 불필요한 선물 API 호출을 방지한다.
    return ccxt.binance(
        {
            "enableRateLimit": True,
            "timeout": API_TIMEOUT_MS,
            "options": {
                "defaultType": "spot",
                "fetchMarkets": {"types": ["spot"]},
                "fetchCurrencies": False,
            },
        }
    )


def _api_error_message(error: ccxt.BaseError) -> str:
    return diagnose_api_error(error).message


def _clean_page(page: list, interval_ms: int) -> tuple[list, int, int | None]:
    """잘못된 행을 제외한다. 누락된 가격이나 캔들은 채워 넣지 않는다."""
    valid_rows = []
    invalid_rows = 0
    latest_timestamp = None
    for row in page:
        try:
            if not isinstance(row, (list, tuple)) or len(row) != 6:
                raise ValueError("OHLCV row must contain six values")
            timestamp = float(row[0])
            if (
                not isfinite(timestamp)
                or timestamp < 0
                or not timestamp.is_integer()
                or int(timestamp) % interval_ms != 0
            ):
                raise ValueError("Invalid candle opening time")
            timestamp = int(timestamp)
            latest_timestamp = (
                timestamp if latest_timestamp is None else max(latest_timestamp, timestamp)
            )
            open_, high, low, close, volume = map(float, row[1:])
            if not all(isfinite(value) for value in (open_, high, low, close, volume)):
                raise ValueError("Missing or non-finite OHLCV")
            if (
                min(open_, high, low, close) <= 0
                or volume < 0
                or high < max(open_, low, close)
                or low > min(open_, high, close)
            ):
                raise ValueError("Invalid OHLCV range")
            valid_rows.append([timestamp, open_, high, low, close, volume])
        except (TypeError, ValueError, OverflowError):
            invalid_rows += 1
    return valid_rows, invalid_rows, latest_timestamp


def fetch_binance_ohlcv(
    symbol: str,
    timeframe: str,
    period: str,
    *,
    max_candles: int = MAX_CANDLES,
    warmup_candles: int = 0,
    now: datetime | pd.Timestamp | None = None,
    start_time: datetime | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """선택 기간의 확정 캔들을 UTC DataFrame으로 반환한다.

    timestamp는 캔들 시작 시각이다. 월/년은 달력 기준으로 계산한다.
    5,000개 한도를 넘는 기간은 가장 최근 구간부터 수집한다.
    warmup_candles > 0이면 표시 구간 앞의 확정 봉을 추가 반환한다(최대 300개).
    attrs['analysis_start']로 계산 후 표시 구간을 구분할 수 있다.
    start_time은 명시적 UTC 기간 조회용이며 기존 캔들/요청 수 제한을 그대로 적용한다.
    DataFrame.attrs에 실제 기간, 수집 시각, 누락 개수와 제한 사유를 기록한다.
    일부 페이지 실패 시 확보한 실제 데이터와 경고를 반환하며, 유효한 데이터가
    전혀 없으면 DataCollectionError를 발생시킨다. now는 재현 가능한 검증용이다.
    """
    if symbol not in SUPPORTED_SYMBOLS:
        raise ValueError(f"지원하지 않는 종목입니다: {symbol}")
    if timeframe not in TIMEFRAME_MILLISECONDS:
        raise ValueError(f"지원하지 않는 Timeframe입니다: {timeframe}")
    if period not in PERIOD_OFFSETS:
        raise ValueError(f"지원하지 않는 분석 기간입니다: {period}")
    if type(max_candles) is not int or not 1 <= max_candles <= MAX_CANDLES:
        raise ValueError(f"max_candles는 1~{MAX_CANDLES:,} 사이의 정수여야 합니다.")
    if type(warmup_candles) is not int or not 0 <= warmup_candles <= MAX_WARMUP_CANDLES:
        raise ValueError(f"warmup_candles는 0~{MAX_WARMUP_CANDLES} 사이의 정수여야 합니다.")

    as_of = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    if pd.isna(as_of) or as_of.tzinfo is None:
        raise ValueError("now는 시간대가 지정된 유효한 시각이어야 합니다.")
    as_of = as_of.tz_convert("UTC")
    interval_ms = TIMEFRAME_MILLISECONDS[timeframe]
    interval = pd.Timedelta(milliseconds=interval_ms)
    requested_start = as_of - pd.DateOffset(**PERIOD_OFFSETS[period]) if start_time is None else pd.Timestamp(start_time)
    if pd.isna(requested_start) or requested_start.tzinfo is None or requested_start >= as_of:
        raise ValueError("start_time은 now보다 이전인 시간대가 지정된 유효한 시각이어야 합니다.")
    requested_start = requested_start.tz_convert("UTC")
    requested_start_ms = (requested_start.value + 999_999) // 1_000_000
    first_open_ms = ((requested_start_ms + interval_ms - 1) // interval_ms) * interval_ms
    # 현재 진행 중인 캔들의 시작 시각이 확정 캔들 구간의 배타적 끝이다.
    closed_end_ms = (int(as_of.timestamp() * 1000) // interval_ms) * interval_ms
    expected_candles = max(0, (closed_end_ms - first_open_ms) // interval_ms)
    if expected_candles == 0:
        raise DataCollectionError("선택한 기간에 확정된 캔들이 없습니다.", details=ErrorDetails(
            "EMPTY_RESPONSE", "선택 기간에 확정 캔들이 없습니다.", "기간 또는 Timeframe을 확인해 주세요."))
    analysis_start_ms = max(first_open_ms, closed_end_ms - max_candles * interval_ms)
    collection_start_ms = analysis_start_ms - warmup_candles * interval_ms
    planned_candles = (closed_end_ms - analysis_start_ms) // interval_ms

    reasons = []
    if expected_candles > max_candles:
        reasons.append(
            f"요청 기간의 확정 캔들 {expected_candles:,}개가 최대 {max_candles:,}개 "
            "제한을 초과하여 최근 구간만 수집했습니다."
        )
    rows = []
    invalid_rows = 0
    cursor = collection_start_ms
    request_count = 0

    # 공유 클라이언트의 페이지 요청을 직렬화해 여러 세션에서도 limiter를 보호한다.
    with _EXCHANGE_LOCK:
        exchange = _get_exchange()
        while cursor < closed_end_ms and request_count < MAX_FETCH_REQUESTS:
            remaining = (closed_end_ms - cursor) // interval_ms
            request_count += 1
            try:
                page = exchange.fetch_ohlcv(
                    symbol,
                    timeframe=timeframe,
                    since=cursor,
                    limit=min(BINANCE_PAGE_LIMIT, remaining),
                    params={"endTime": closed_end_ms - 1},
                )
            except ccxt.BaseError as error:
                details = diagnose_api_error(error)
                message = details.message
                if not rows:
                    raise DataCollectionError(message, details=details) from error
                reasons.append(f"페이지 수집이 중단되었습니다. {message}")
                break

            if not page:
                reasons.append("Binance가 빈 응답을 반환하여 남은 구간 수집을 중단했습니다.")
                break
            if not isinstance(page, list) or len(page) > BINANCE_PAGE_LIMIT:
                reasons.append("Binance 응답 형식이 올바르지 않아 수집을 중단했습니다.")
                break
            cleaned, rejected, page_last_ms = _clean_page(page, interval_ms)
            invalid_rows += rejected
            rows.extend(
                row for row in cleaned if collection_start_ms <= row[0] < closed_end_ms
            )
            if page_last_ms is None or page_last_ms < cursor:
                reasons.append("API 페이지의 시각이 진행되지 않아 반복 호출을 중단했습니다.")
                break
            cursor = page_last_ms + interval_ms

    if cursor < closed_end_ms and request_count >= MAX_FETCH_REQUESTS:
        reasons.append(f"조회당 최대 {MAX_FETCH_REQUESTS}회 페이지 요청 제한에 도달했습니다.")
    if not rows:
        raise DataCollectionError(
            "Binance에서 유효한 확정 캔들을 받지 못했습니다. "
            + " ".join(reasons), details=ErrorDetails(
                "DATA_VALIDATION" if invalid_rows or page else "EMPTY_RESPONSE",
                "유효한 OHLCV 데이터가 없습니다.",
                "응답이 비었거나 OHLCV 검증을 통과하지 못했습니다. 네트워크 오류와 별개로 확인해 주세요.")
        )

    data = pd.DataFrame(rows, columns=OHLCV_COLUMNS)
    duplicates = int(data.duplicated("timestamp").sum())
    data = data.drop_duplicates("timestamp", keep="last").sort_values("timestamp")
    data["timestamp"] = pd.to_datetime(data["timestamp"], unit="ms", utc=True)
    data = data.reset_index(drop=True)
    analysis_start = pd.to_datetime(analysis_start_ms, unit="ms", utc=True)
    visible = data.loc[data["timestamp"] >= analysis_start]
    if visible.empty:
        raise DataCollectionError(
            "계산용 과거 데이터만 수집되어 요청한 분석 구간의 확정 캔들이 없습니다. "
            + " ".join(reasons), details=ErrorDetails(
                "DATA_VALIDATION", "요청 분석 구간의 데이터가 없습니다.",
                "부분 수집 경고를 확인하고 새로고침해 주세요.")
        )
    warmup_collected = len(data) - len(visible)
    warmup_missing = warmup_candles - warmup_collected
    warmup_reasons = ()
    if warmup_missing:
        warmup_reasons = (
            f"계산용 과거 봉 {warmup_candles}개 중 {warmup_collected}개만 수집했습니다. "
            "분석 구간 앞부분의 지표가 NaN이거나 초기화 영향을 받을 수 있습니다.",
        )
    missing_candles = planned_candles - len(visible)
    gaps = visible["timestamp"].diff().dropna() / interval - 1
    gap_count = int(gaps.clip(lower=0).sum())
    if invalid_rows:
        reasons.append(f"결측값 또는 잘못된 OHLCV가 있는 {invalid_rows:,}개 행을 제외했습니다.")
    if missing_candles:
        reasons.append(
            f"수집 대상 구간에 {missing_candles:,}개 캔들이 누락되어 있습니다. "
            "누락 구간은 보간하지 않습니다."
        )
    data.attrs = {
        "symbol": symbol,
        "timeframe": timeframe,
        "period": period if start_time is None else "사용자 지정 기간",
        "requested_start": requested_start,
        "requested_end": as_of,
        "closed_end": pd.to_datetime(closed_end_ms, unit="ms", utc=True),
        "collection_start": pd.to_datetime(collection_start_ms, unit="ms", utc=True),
        "analysis_start": analysis_start,
        "actual_start": data.iloc[0]["timestamp"],
        "actual_end": data.iloc[-1]["timestamp"] + interval,
        "fetched_at": pd.Timestamp.now(tz="UTC"),
        "expected_candles": expected_candles,
        "missing_candles": missing_candles,
        "gap_count": gap_count,
        "max_candles": max_candles,
        "request_count": request_count,
        "removed_duplicates": duplicates,
        "invalid_rows": invalid_rows,
        "is_partial": bool(reasons),
        "limit_reasons": tuple(reasons),
        "warmup_requested": warmup_candles,
        "warmup_collected": warmup_collected,
        "warmup_missing_candles": warmup_missing,
        "warmup_reasons": warmup_reasons,
    }
    return data
