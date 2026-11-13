"""FRED 원본 수준 시리즈의 단위/주기와 확장 가능한 관측 데이터 계약."""

from dataclasses import dataclass
from datetime import date
import re
from types import MappingProxyType


class FredError(RuntimeError):
    """원본 요청 URL/응답 본문/키가 들어 있지 않은 사용자 표시용 오류."""

    def __init__(self, message: str, *, category: str = "API_ERROR", http_status: int | None = None):
        super().__init__(message)
        self.category = category
        self.http_status = http_status


class FredKeyError(FredError):
    pass


def parse_macro_date(value, *, optional: bool = False) -> date | None:
    """관측 기간은 ISO 달력 날짜다. 시각/시간대 문자열을 날짜로 암묵 변환하지 않는다."""
    if value is None and optional:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise FredError("FRED 날짜 형식이 올바르지 않습니다. YYYY-MM-DD 달력 날짜를 사용해 주세요.",
                        category="DATA_VALIDATION")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise FredError("FRED 날짜를 해석할 수 없습니다.", category="DATA_VALIDATION") from None


@dataclass(frozen=True)
class MacroSeries:
    series_id: str
    name: str
    units: str
    unit_label: str
    frequency: str
    seasonal_adjustment: str
    description: str

    def response_date_bounds(self, start: date, end: date) -> tuple[date, date]:
        """월별 API는 시작 월의 1일 라벨을 포함할 수 있다. 일별은 정확한 날짜 경계다.

        이 구간은 응답 허용 범위이며, 반환/표시는 원래 [start, end] 라벨 구간으로 자른다.
        종료일 이후와 이전 달 라벨은 허용하지 않는다. 발표/가용 시각의 의미는 없다.
        """
        return (start.replace(day=1) if self.frequency == "M" else start), end

    @property
    def frequency_label(self) -> str:
        return "월별" if self.frequency == "M" else "일별"

    @property
    def source_url(self) -> str:
        return f"https://fred.stlouisfed.org/series/{self.series_id}"


SERIES = MappingProxyType({
    "CPIAUCSL": MacroSeries("CPIAUCSL", "미국 소비자물가지수", "Index 1982-1984=100",
                            "지수 (1982–1984=100)", "M", "SA",
                            "계절 조정된 CPI 원본 지수 수준입니다. 전년동월비는 별도로 계산합니다."),
    "FEDFUNDS": MacroSeries("FEDFUNDS", "미국 연방기금금리", "Percent", "%", "M", "NSA",
                            "실효 연방기금금리의 월별 평균입니다. 정책 목표금리 범위와 다릅니다."),
    "UNRATE": MacroSeries("UNRATE", "미국 실업률", "Percent", "%", "M", "SA",
                          "계절 조정된 U-3 실업률입니다."),
    "PAYEMS": MacroSeries("PAYEMS", "미국 비농업 고용자 수", "Thousands of Persons", "천 명", "M", "SA",
                          "계절 조정된 비농업 총 고용 수준입니다. 전월 고용 증감 인원과 구분합니다."),
    "DGS10": MacroSeries("DGS10", "미국 10년물 국채금리", "Percent", "%", "D", "NSA",
                         "10년 고정 만기 미국 국채 시장 수익률입니다. 휴일 등의 결측은 채우지 않습니다."),
})

MACRO_COLUMNS = ["series_id", "observation_date", "value", "units", "frequency",
                 "release_date", "available_at", "vintage_date", "realtime_start", "realtime_end",
                 "fetched_at"]


def get_series(series_id: str) -> MacroSeries:
    if series_id not in SERIES:
        raise FredError("지원하지 않는 FRED 시리즈입니다.", category="VALIDATION")
    return SERIES[series_id]
