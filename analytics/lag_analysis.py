"""macro[t]와 crypto_return[t+lag]의 사후 상관. 미래 수익률을 과거 특징으로 반환하지 않는다."""

from dataclasses import asdict

import numpy as np
import pandas as pd

from analytics.correlation import calculate_correlation
from analytics.models import AnalysisError


def analyze_lags(monthly: pd.DataFrame, *, min_samples=12):
    if not isinstance(monthly.index, pd.PeriodIndex) or monthly.index.freqstr != "M" or not monthly.index.is_unique:
        raise AnalysisError("시차 분석에는 고유한 월별 PeriodIndex가 필요합니다.")
    frame = monthly.reindex(pd.period_range(monthly.index.min(), monthly.index.max(), freq="M")) if len(monthly) else monthly
    stats, pairs = [], []
    for lag in range(4):
        target = frame.index + lag
        target_values = frame.crypto_return_pct.reindex(target).to_numpy()
        allowed = (frame.macro_eligible.fillna(False).astype(bool).to_numpy() &
                   frame.crypto_eligible.reindex(target).fillna(False).astype(bool).to_numpy())
        x = frame.macro_change.loc[allowed]
        y = pd.Series(target_values, index=frame.index).loc[allowed]
        result = calculate_correlation(x, y, min_samples=min_samples)
        stats.append({"lag_months": lag, **asdict(result)})
        for origin, dest, macro, price, eligible in zip(frame.index, target, frame.macro_change, target_values, allowed):
            pairs.append({"lag_months": lag, "macro_month": str(origin), "crypto_month": str(dest),
                          "macro_change": macro, "crypto_return_pct": price, "eligible": bool(eligible),
                          "used": bool(eligible and np.isfinite(macro) and np.isfinite(price))})
    return pd.DataFrame(stats), pd.DataFrame(pairs)
