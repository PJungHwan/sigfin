"""UTC·유한 수치·불변 이벤트 계약. 가상 거래만 표현한다."""

from math import isfinite
from typing import Literal

import pandas as pd
from pydantic import Field, FiniteFloat, field_validator

from llm.schemas import StrictModel

SYMBOLS = ("BTC/USDT", "ETH/USDT")


class PortfolioError(ValueError):
    pass


def utc(value):
    try:
        stamp = pd.Timestamp(value)
        if pd.isna(stamp) or stamp.tzinfo is None:
            raise ValueError
        return stamp.tz_convert("UTC").isoformat()
    except (ValueError, TypeError, OverflowError):
        raise PortfolioError("유효한 시간대 지정 시각이 필요합니다.") from None


class Numbers(StrictModel):
    @field_validator("*", mode="before")
    @classmethod
    def finite_numbers(cls, value):
        if isinstance(value, (int, float)) and (isinstance(value, bool) or not isfinite(value)):
            raise ValueError("유한한 숫자를 사용하세요.")
        return value


class Quote(Numbers):
    symbol: Literal["BTC/USDT", "ETH/USDT"]
    price: FiniteFloat = Field(gt=0)
    as_of_utc: str
    fetched_at_utc: str
    timeframe: Literal["5m", "15m", "1h", "4h", "1d"]

    @field_validator("as_of_utc", "fetched_at_utc")
    @classmethod
    def stamp(cls, value):
        return utc(value)


class Trade(Numbers):
    event_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    symbol: Literal["BTC/USDT", "ETH/USDT"]
    side: Literal["BUY", "SELL"]
    quantity: FiniteFloat = Field(gt=0, le=1e12)
    quote: Quote
    fee_rate: FiniteFloat = Field(ge=0, lt=1)
    slippage_rate: FiniteFloat = Field(ge=0, lt=1)
    recorded_at_utc: str

    @field_validator("recorded_at_utc")
    @classmethod
    def stamp(cls, value):
        return utc(value)


class Account(Numbers):
    schema_version: Literal[1] = 1
    account_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    name: str = Field(min_length=1, max_length=80)
    initial_capital: FiniteFloat = Field(gt=0, le=1e12)
    created_at_utc: str
    trades: list[Trade] = Field(default_factory=list, max_length=10000)

    @field_validator("created_at_utc")
    @classmethod
    def stamp(cls, value):
        return utc(value)


class Holding(Numbers):
    symbol: str
    quantity: FiniteFloat = Field(ge=0)
    average_price: FiniteFloat | None


class Book(Numbers):
    cash: FiniteFloat = Field(ge=0)
    realized_pnl: FiniteFloat
    total_fees: FiniteFloat = Field(ge=0)
    holdings: list[Holding]


class Valuation(Numbers):
    account_id: str
    account_revision: int
    evaluated_at_utc: str
    data_as_of_utc: str | None
    status: Literal["COMPLETE", "UNAVAILABLE"]
    cash: FiniteFloat
    realized_pnl: FiniteFloat
    unrealized_pnl: FiniteFloat | None
    total_equity: FiniteFloat | None
    total_fees: FiniteFloat
    positions: list[dict]
    issues: list[str]
