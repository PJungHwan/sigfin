"""기존 Binance/FRED 캐시를 재사용하고 순수 통계 엔진에 스냅샷을 전달한다."""

import pandas as pd

from analytics.analysis import analyze_crypto_macro
from analytics.models import AnalysisConfig, AnalysisError, require_exploratory
from config.settings import SUPPORTED_SYMBOLS
from services.market_data import load_market_data
from services.macro_service import credential_scope, load_macro_series


ANALYSIS_PERIODS = {"1년": 1, "3년": 3, "5년": 5, "10년": 10}


def load_crypto_macro_analysis(symbol: str, period: str, config: AnalysisConfig):
    require_exploratory(config.mode)
    if symbol not in SUPPORTED_SYMBOLS or period not in ANALYSIS_PERIODS:
        raise AnalysisError("지원하지 않는 종목/분석 기간입니다.")
    credential_scope()  # 키가 없으면 Binance 장기 요청보다 먼저 친절한 오류를 반환한다.
    end = pd.Timestamp.now(tz="UTC")
    start = end.normalize().replace(day=1)-pd.DateOffset(years=ANALYSIS_PERIODS[period])
    price_start = start-pd.DateOffset(months=1)
    macro_start = start-pd.DateOffset(years=1)
    crypto = load_market_data(symbol, config.timeframe, "1년", start_time=price_start.isoformat())
    macro = load_macro_series(config.series_id, macro_start.date().isoformat(), end.date().isoformat())
    return analyze_crypto_macro(crypto, macro, config, start=start, end=end, symbol=symbol)
