"""유한한 공통 표본만 사용하는 Pearson과 평균 순위 기반 Spearman. p-value는 계산하지 않는다."""

import numpy as np
import pandas as pd

from analytics.models import AnalysisError, CorrelationResult


def _pearson(x, y):
    # 큰 원본 단위의 제곱에서 overflow가 생기지 않도록 먼저 크기를 정규화한다.
    x, y = x / np.max(np.abs(x)), y / np.max(np.abs(y))
    x, y = x-x.mean(), y-y.mean()
    result = float(np.dot(x, y)/(np.linalg.norm(x)*np.linalg.norm(y)))
    if not np.isfinite(result) or not -1-1e-10 <= result <= 1+1e-10:
        raise AnalysisError("상관계수 계산이 유효한 범위를 벗어났습니다.")
    return float(np.clip(result, -1, 1))


def calculate_correlation(x: pd.Series, y: pd.Series, *, min_samples: int = 12) -> CorrelationResult:
    if type(min_samples) is not int or min_samples < 3:
        raise AnalysisError("최소 표본 수는 3 이상 정수여야 합니다.")
    if not x.index.is_unique or not y.index.is_unique:
        raise AnalysisError("상관관계 표본 인덱스는 고유해야 합니다.")
    pair = pd.concat([x.rename("x"), y.rename("y")], axis=1, join="inner").sort_index()
    try:
        values = pair.to_numpy(dtype=float)
    except (TypeError, ValueError):
        raise AnalysisError("상관관계 입력은 숫자여야 합니다.") from None
    valid = pd.DataFrame(values, index=pair.index, columns=pair.columns).loc[np.isfinite(values).all(axis=1)]
    before, count = len(pair), len(valid)
    first, last = (str(valid.index.min()), str(valid.index.max())) if count else (None, None)
    def outcome(status, pearson=None, spearman=None):
        return CorrelationResult(pearson, spearman, before, count, before-count, first, last, status)
    if count < min_samples:
        return outcome("INSUFFICIENT_SAMPLES")
    if valid.x.nunique() < 2 or valid.y.nunique() < 2:
        return outcome("CONSTANT_SERIES")
    pearson = _pearson(valid.x.to_numpy(dtype=float), valid.y.to_numpy(dtype=float))
    spearman = _pearson(valid.x.rank(method="average").to_numpy(), valid.y.rank(method="average").to_numpy())
    return outcome("OK", pearson, spearman)
