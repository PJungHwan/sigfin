"""기존 캐시를 사용하는 명시적 가격 조회. 가상 거래와 독립적이다."""

from dataclasses import dataclass

import pandas as pd

from config.settings import TIMEFRAME_MILLISECONDS
from portfolio.models import Quote, Valuation, PortfolioError, SYMBOLS, utc
from services.market_data import load_market_data


@dataclass(frozen=True)
class PortfolioPriceContext:
    """한 화면 실행에서 계좌/위험 평가가 공유하는 가격·검증 시각. 원본 봉은 복사하지 않는다."""

    frames: dict[str, pd.DataFrame]
    quotes: dict[str, Quote]
    valuation: Valuation


def quotes_from_frames(frames, *, now=None):
    current = pd.Timestamp(utc(now if now is not None else pd.Timestamp.now(tz="UTC")))
    candidates = {}
    for symbol, frame in frames.items():
        if symbol not in SYMBOLS or frame is None or frame.empty:
            continue
        timeframe = frame.attrs.get("timeframe")
        if timeframe not in TIMEFRAME_MILLISECONDS or frame.attrs.get("symbol", symbol) != symbol:
            raise PortfolioError("가격 자료의 종목/봉 정보가 일치하지 않습니다.")
        end = frame.timestamp+pd.Timedelta(milliseconds=TIMEFRAME_MILLISECONDS[timeframe])
        valid = end.le(current)
        if valid.any():
            candidates[symbol] = (frame, end, timeframe)
    if not candidates:
        return {}
    common = set.intersection(*(set(end[end.le(current)]) for _, end, _ in candidates.values()))
    if not common:
        return {}
    cutoff = max(common)
    result = {}
    for symbol, (frame, end, timeframe) in candidates.items():
        selected = frame.loc[end.eq(cutoff)]
        if len(selected)!=1:
            raise PortfolioError("가격 자료의 중복 시각을 확인하세요.")
        result[symbol] = Quote(symbol=symbol, price=float(selected.iloc[0].close), as_of_utc=cutoff.isoformat(),
            fetched_at_utc=utc(frame.attrs["fetched_at"]), timeframe=timeframe)
    return result


def load_portfolio_prices(timeframe, period):
    return {symbol: load_market_data(symbol, timeframe, period) for symbol in SYMBOLS}
