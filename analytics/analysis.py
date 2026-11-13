"""데이터만 받는 순수 분석 진입점. 수집/UI/매매 엔진을 호출하지 않는다."""

import pandas as pd

from analytics.alignment import (align_monthly, crypto_monthly, macro_monthly, normalized_levels,
                                 periodic_crypto_returns, utc_time)
from analytics.lag_analysis import analyze_lags
from analytics.correlation import calculate_correlation
from analytics.models import AnalysisConfig, CryptoMacroResult, require_exploratory


def analyze_crypto_macro(crypto, macro, config: AnalysisConfig, *, start, end, symbol="BTC/USDT"):
    require_exploratory(config.mode)
    prices = crypto_monthly(crypto, config.timeframe, as_of=end)
    economics, daily_macro = macro_monthly(macro, config, as_of=end)
    monthly = align_monthly(prices, economics, start=start, end=end, include_partial=config.include_partial)
    lags, pairs = analyze_lags(monthly, min_samples=config.min_samples)
    eligible = monthly.crypto_eligible & monthly.macro_eligible
    correlation = calculate_correlation(monthly.loc[eligible, "macro_change"],
                                        monthly.loc[eligible, "crypto_return_pct"], min_samples=config.min_samples)
    quality = {"mode": "사후 탐색적 분석", "requested_start": str(utc_time(start)), "requested_end": str(utc_time(end)),
               "candidate_months": len(monthly), "crypto_partial_months": int(monthly.crypto_partial.fillna(True).sum()),
               "missing_crypto_months": int(monthly.crypto_close.isna().sum()),
               "missing_macro_months": int(monthly.macro_level.isna().sum()),
               "source_crypto_candles": len(crypto), "source_macro_observations": len(macro),
               "macro_missing_values": int(macro.value.isna().sum()),
               "crypto_limits": list(crypto.attrs.get("limit_reasons", ())),
               "crypto_actual_start": str(crypto.timestamp.min()) if len(crypto) else None,
               "crypto_actual_last_open": str(crypto.timestamp.max()) if len(crypto) else None,
               "crypto_actual_end": str(prices.candle_end.max()) if len(prices) else None,
               "macro_actual_start": str(macro.observation_date.min()) if len(macro) else None,
               "macro_actual_end": str(macro.observation_date.max()) if len(macro) else None,
               "crypto_fetched_at": str(crypto.attrs.get("fetched_at", "미확인")),
               "macro_fetched_at": str(macro.attrs.get("fetched_at", "미확인")),
               "macro_version_policy": macro.attrs.get("version_policy", "current_revised_snapshot"),
               "is_point_in_time": False}
    return CryptoMacroResult(monthly, correlation, lags, pairs, normalized_levels(monthly),
                             periodic_crypto_returns(crypto, config.timeframe, rule="1h", as_of=end),
                             periodic_crypto_returns(crypto, config.timeframe, rule="1d", as_of=end),
                             daily_macro, quality, config, symbol)
