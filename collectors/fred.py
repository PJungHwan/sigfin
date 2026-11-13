"""FRED API v1 원본 관측값. 공개 조회에도 사용자 키가 필요하며 임의 대체는 없다.

관측일은 기간의 라벨이다. realtime_start/end는 API가 반환한 실시간 구간일 뿐 최초
발표일/빈티지 날짜로 해석하지 않는다. 현재 개정값 스냅샷을 역사적 가용값으로 사용하지 않는다.
"""

import logging
from math import isfinite
import os
from pathlib import Path
import re
from threading import RLock
import time

from dotenv import dotenv_values
import pandas as pd
import requests

from config.settings import (
    FRED_API_BASE_URL, FRED_MAX_OBSERVATIONS, FRED_MAX_REQUESTS_PER_SERIES,
    FRED_MIN_REQUEST_INTERVAL_SECONDS, FRED_PAGE_LIMIT, FRED_TIMEOUT_SECONDS,
)
from models.macro import FredError, FredKeyError, MACRO_COLUMNS, get_series, parse_macro_date as _day

_REQUEST_LOCK = RLock()
_last_request_at = 0.0
_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


def get_api_key() -> str:
    """프로세스 환경을 우선하고 프로젝트 .env만 읽는다. 외부 경로는 탐색하지 않는다."""
    key = os.getenv("FRED_API_KEY")
    if not key and _ENV_PATH.is_file():
        try:
            key = dotenv_values(_ENV_PATH, interpolate=False).get("FRED_API_KEY")
        except (OSError, UnicodeError):
            raise FredKeyError("프로젝트 .env를 읽을 수 없습니다. 파일 권한과 UTF-8 인코딩을 확인해 주세요.",
                               category="KEY_CONFIGURATION") from None
    if not key:
        raise FredKeyError("FRED_API_KEY가 없습니다. 프로젝트 .env에 본인의 FRED API 키를 설정해 주세요.",
                           category="KEY_MISSING")
    return _validate_key(key)


def _validate_key(key) -> str:
    if not isinstance(key, str) or not re.fullmatch(r"[a-z0-9]{32}", key.strip()):
        raise FredKeyError("FRED API 키 형식이 올바르지 않습니다. 본인의 32자리 소문자 영숫자 키를 확인해 주세요.",
                           category="AUTHENTICATION")
    return key.strip()


class _RedactKey(logging.Filter):
    """requests의 urllib3 DEBUG 로그에도 api_key 쿼리 값이 남지 않도록 한다."""

    def filter(self, record):
        record.msg = re.sub(r"(?i)(api_key=)[^&\s\"']+", r"\1[REDACTED]", record.getMessage())
        record.args = ()
        return True


def _request_json(endpoint: str, params: dict, key: str) -> dict:
    global _last_request_at
    with _REQUEST_LOCK:
        delay = FRED_MIN_REQUEST_INTERVAL_SECONDS - (time.monotonic() - _last_request_at)
        if delay > 0:
            time.sleep(delay)
        # 고정 URL, TLS 검증 유지, 리다이렉트 금지. 자동 재시도로 요청 예산을 늘리지 않는다.
        logger = logging.getLogger("urllib3.connectionpool")
        redactor = _RedactKey()
        logger.addFilter(redactor)
        response = None
        try:
            _last_request_at = time.monotonic()
            response = requests.get(f"{FRED_API_BASE_URL}/{endpoint}",
                                    params={**params, "api_key": key, "file_type": "json"},
                                    timeout=(5, FRED_TIMEOUT_SECONDS), allow_redirects=False)
            status = response.status_code
            # FRED는 잘못된 api_key도 HTTP 400으로 반환할 수 있다. 본문은 분류에만 쓰고 보존하지 않는다.
            try:
                payload = response.json()
            except ValueError:
                payload = None
            api_message = str(payload.get("error_message", "")).lower() if isinstance(payload, dict) else ""
            if status in (401, 403) or (status == 400 and ("api_key" in api_message or "api key" in api_message)):
                raise FredKeyError("FRED 인증에 실패했습니다. 본인 API 키의 유효성을 확인해 주세요.",
                                   category="AUTHENTICATION", http_status=status)
            if status == 429:
                raise FredError("FRED 요청 한도에 도달했습니다. 잠시 기다린 뒤 다시 조회해 주세요.",
                                category="RATE_LIMIT", http_status=status)
            if status != 200:
                raise FredError(f"FRED API가 HTTP {status} 오류를 반환했습니다.", category="HTTP_ERROR", http_status=status)
            if not isinstance(payload, dict):
                raise FredError("FRED 응답을 JSON 객체로 읽을 수 없습니다.", category="RESPONSE_ERROR")
            if "error_code" in payload or "error_message" in payload:
                raise FredError("FRED API가 오류 응답을 반환했습니다.", category="API_ERROR")
            return payload
        except requests.Timeout:
            raise FredError("FRED 연결 또는 응답 대기 시간이 초과되었습니다.", category="TIMEOUT") from None
        except requests.RequestException:
            # 요청 예외에는 키가 포함된 URL이 있을 수 있으므로 원문/예외 체인을 출력하지 않는다.
            raise FredError("FRED 네트워크 연결에 실패했습니다. 실행 환경의 네트워크 권한을 확인해 주세요.",
                            category="NETWORK_ERROR") from None
        finally:
            if response is not None:
                response.close()
            logger.removeFilter(redactor)


def normalize_observations(series_id: str, observations: list, *, fetched_at=None) -> pd.DataFrame:
    spec = get_series(series_id)
    if not isinstance(observations, list) or not observations:
        raise FredError("선택 기간에 FRED 관측값이 없습니다.", category="EMPTY_RESPONSE")
    collected = pd.Timestamp.now(tz="UTC") if fetched_at is None else pd.Timestamp(fetched_at)
    if pd.isna(collected) or collected.tzinfo is None:
        raise FredError("수집 시각은 시간대가 있는 유효한 시각이어야 합니다.", category="VALIDATION")
    rows = []
    for item in observations:
        if not isinstance(item, dict) or "value" not in item:
            raise FredError("FRED 관측값 구조가 올바르지 않습니다.", category="DATA_VALIDATION")
        observed = _day(item.get("date"))
        if spec.frequency == "M" and observed.day != 1:
            raise FredError("월별 FRED 관측일이 월 시작일과 일치하지 않습니다.", category="DATA_VALIDATION")
        raw = item["value"]
        if raw is None or (isinstance(raw, str) and raw.strip() in (".", "")):
            value = float("nan")
        else:
            try:
                value = float(raw) if not isinstance(raw, bool) else float("inf")
            except (TypeError, ValueError, OverflowError):
                raise FredError("FRED 값이 숫자 또는 공식 결측 표기가 아닙니다.", category="DATA_VALIDATION") from None
            if not isfinite(value):
                raise FredError("FRED 값에 비유한 숫자가 있습니다.", category="DATA_VALIDATION")
        start, end = _day(item.get("realtime_start"), optional=True), _day(item.get("realtime_end"), optional=True)
        if start and end and start > end:
            raise FredError("FRED 실시간 구간의 순서가 올바르지 않습니다.", category="DATA_VALIDATION")
        rows.append({"series_id": series_id, "observation_date": observed, "value": value,
                     "units": spec.units, "frequency": spec.frequency, "release_date": pd.NaT,
                     "available_at": pd.NaT, "vintage_date": None,
                     "realtime_start": start, "realtime_end": end, "fetched_at": collected.tz_convert("UTC")})
    frame = pd.DataFrame(rows, columns=MACRO_COLUMNS)
    # 동일 날짜에 다른 버전/값이 섞이면 임의 버전을 선택하지 않는다.
    unique = frame.groupby("observation_date")[["value", "realtime_start", "realtime_end"]].nunique(dropna=False)
    if (unique > 1).any().any():
        raise FredError("같은 관측일에 서로 다른 값/버전이 있습니다.", category="DATA_VALIDATION")
    duplicates = int(frame.observation_date.duplicated().sum())
    frame = frame.drop_duplicates("observation_date").sort_values("observation_date", kind="stable").reset_index(drop=True)
    try:
        for column in ("observation_date", "release_date", "available_at"):
            frame[column] = pd.to_datetime(frame[column], utc=True)
    except (ValueError, OverflowError):
        raise FredError("FRED 관측일이 지원되는 날짜 범위를 벗어났습니다.", category="DATA_VALIDATION") from None
    frame.attrs = {"series_id": series_id, "units": spec.units, "frequency": spec.frequency,
                   "seasonal_adjustment": spec.seasonal_adjustment, "source_url": spec.source_url,
                   "fetched_at": collected.tz_convert("UTC"), "removed_duplicates": duplicates,
                   "missing_values": int(frame.value.isna().sum()), "is_point_in_time": False,
                   "version_policy": "current_revised_snapshot", "release_dates_available": False}
    return frame


def fetch_fred_series(series_id: str, start_date: str, end_date: str, *, api_key=None) -> pd.DataFrame:
    """원본 관측 라벨이 [start_date, end_date] 안인 행만 반환한다(양 끝 포함).

    월별 응답의 시작 월 1일 라벨만 정상 경계 문맥으로 인정한 후 표시 범위에서 제외한다.
    그보다 이전/종료일 이후 행은 계속 검증 오류이며, 발표일이나 빈티지는 생성하지 않는다.
    """
    spec = get_series(series_id)
    start, end = _day(start_date), _day(end_date)
    today = pd.Timestamp.now(tz="UTC").date()
    if start > end or end > today or (end - start).days > 22 * 366:
        raise FredError("FRED 조회 기간은 과거부터 오늘까지 최대 22년이어야 합니다.", category="VALIDATION")
    key = get_api_key() if api_key is None else _validate_key(api_key)
    metadata = _request_json("series", {"series_id": series_id}, key)
    items = metadata.get("seriess")
    if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
        raise FredError("FRED 시리즈 메타데이터 구조가 올바르지 않습니다.", category="RESPONSE_ERROR")
    source = items[0]
    if (source.get("id") != series_id or source.get("units") != spec.units
            or source.get("frequency_short") != spec.frequency
            or source.get("seasonal_adjustment_short") != spec.seasonal_adjustment):
        raise FredError("FRED 시리즈의 식별자·단위·관측 주기·계절 조정 정보가 예상과 다릅니다.", category="DATA_VALIDATION")
    # 오늘 기준 개정값 스냅샷만 요청한다. 과거 발표 시각/빈티지는 이 요청으로 얻지 않는다.
    params = {"series_id": series_id, "observation_start": start.isoformat(), "observation_end": end.isoformat(),
              "realtime_start": today.isoformat(), "realtime_end": today.isoformat(),
              "units": "lin", "output_type": 1, "sort_order": "asc", "limit": FRED_PAGE_LIMIT}
    rows, offset, total = [], 0, None
    for page_number in range(FRED_MAX_REQUESTS_PER_SERIES-1):
        payload = _request_json("series/observations", {**params, "offset": offset}, key)
        page, count = payload.get("observations"), payload.get("count")
        if (not isinstance(page, list) or type(count) is not int or count < 0
                or count > FRED_MAX_OBSERVATIONS or len(page) > FRED_PAGE_LIMIT
                or payload.get("offset") != offset or payload.get("units") != "lin"):
            raise FredError("FRED 응답 형식 또는 관측값 한도 검증에 실패했습니다.", category="RESPONSE_ERROR")
        if total is not None and total != count:
            raise FredError("페이지 수집 중 FRED 데이터 개수가 변경됐습니다. 다시 조회해 주세요.", category="RESPONSE_ERROR")
        total = count
        rows.extend(page)
        offset += len(page)
        if offset >= total:
            break
        if not page:
            raise FredError("FRED 페이지가 진행되지 않습니다.", category="RESPONSE_ERROR")
    if total is None or offset != total:
        raise FredError("FRED 최대 요청 횟수 내에 전체 데이터를 수집하지 못했습니다.", category="REQUEST_LIMIT")
    frame = normalize_observations(series_id, rows)
    dates = frame.observation_date.dt.date
    allowed_start, allowed_end = spec.response_date_bounds(start, end)
    if not dates.between(allowed_start, allowed_end).all():
        raise FredError("FRED 응답에 요청 기간 밖의 관측일이 있습니다.", category="DATA_VALIDATION")
    inside = dates.between(start, end)
    boundary_removed = int((~inside).sum())
    frame = frame.loc[inside].copy().reset_index(drop=True)
    if frame.empty:
        raise FredError("시작 월의 경계 관측값만 있고 요청한 날짜 구간의 관측 라벨은 없습니다.",
                        category="EMPTY_RESPONSE")
    frame.attrs.update(requested_start=start, requested_end=end, retrieved_as_of=today,
                       request_count=page_number + 2, allowed_response_start=allowed_start,
                       allowed_response_end=allowed_end, boundary_observations_removed=boundary_removed,
                       observation_window_policy="inclusive_observation_labels_v1",
                       missing_values=int(frame.value.isna().sum()))
    updated = source.get("last_updated")
    if updated is not None:
        try:
            updated = pd.Timestamp(updated)
            if pd.isna(updated) or updated.tzinfo is None:
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            raise FredError("FRED 메타데이터의 갱신 시각이 올바르지 않습니다.", category="DATA_VALIDATION") from None
        frame.attrs["source_last_updated"] = updated.tz_convert("UTC")
    return frame
