from dataclasses import dataclass

import pandas as pd

from config.settings import TIMEFRAME_MILLISECONDS
from models.macro import SERIES


class AnalysisError(ValueError):
    pass


class PointInTimeUnavailable(AnalysisError):
    pass


def require_exploratory(mode: str):
    if mode == "point_in_time":
        raise PointInTimeUnavailable("시점 준수 분석을 위한 발표 시각/빈티지 데이터 부족")
    if mode != "exploratory":
        raise AnalysisError("지원하지 않는 분석 모드입니다.")


TRANSFORMS = {
    "CPIAUCSL": {"mom_pct": ("CPI 전월 대비 변화율", "%"), "yoy_pct": ("CPI 전년동월 대비 변화율", "%")},
    "FEDFUNDS": {"change_pp": ("연방기금금리 전월 대비 변화", "%p")},
    "UNRATE": {"change_pp": ("실업률 전월 대비 변화", "%p")},
    "PAYEMS": {"change_thousands": ("비농업 고용 전월 대비 변화", "천 명")},
    "DGS10": {"change_pp": ("국채금리 월간 집계값 전월 대비 변화", "%p")},
}


@dataclass(frozen=True)
class AnalysisConfig:
    series_id: str
    timeframe: str = "1d"
    transform: str | None = None
    dgs_aggregation: str = "last"
    min_samples: int = 12
    include_partial: bool = False
    mode: str = "exploratory"

    def __post_init__(self):
        require_exploratory(self.mode)
        if self.series_id not in SERIES or self.timeframe not in TIMEFRAME_MILLISECONDS:
            raise AnalysisError("지원하지 않는 종목/주기 설정입니다.")
        transform = self.transform or next(iter(TRANSFORMS[self.series_id]))
        if transform not in TRANSFORMS[self.series_id] or self.dgs_aggregation not in ("last", "mean"):
            raise AnalysisError("지원하지 않는 거시경제 변환/집계 방식입니다.")
        object.__setattr__(self, "transform", transform)
        if type(self.min_samples) is not int or self.min_samples < 3 or type(self.include_partial) is not bool:
            raise AnalysisError("최소 표본은 3 이상 정수, 부분 월 포함 여부는 bool이어야 합니다.")


@dataclass(frozen=True)
class CorrelationResult:
    pearson: float | None
    spearman: float | None
    n_before: int
    n_used: int
    n_removed: int
    first_month: str | None
    last_month: str | None
    status: str


@dataclass(frozen=True)
class CryptoMacroResult:
    monthly: pd.DataFrame
    correlation: CorrelationResult
    lags: pd.DataFrame
    lag_pairs: pd.DataFrame
    normalized: pd.DataFrame
    hourly_returns: pd.DataFrame
    daily_returns: pd.DataFrame
    macro_daily: pd.DataFrame
    quality: dict
    config: AnalysisConfig
    symbol: str
