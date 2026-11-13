"""Structured Outputs 스키마와 별도의 입력 계약. 모든 출력 키는 필수다."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat

Signal = Literal["BULLISH", "BEARISH", "NEUTRAL", "INSUFFICIENT_DATA"]
Source = Literal["TECHNICAL", "MACRO", "DATA_QUALITY"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True, revalidate_instances="always")


class Evidence(StrictModel):
    reference_id: str = Field(min_length=1, max_length=80)
    source_type: Source
    metric_name: str = Field(min_length=1, max_length=100)
    metric_value: FiniteFloat
    observation_time: str = Field(min_length=1, max_length=40)
    explanation: str = Field(min_length=1, max_length=400)


class Fact(Evidence):
    role: Literal["bullish", "bearish", "context"]
    unit: str


class MarketSnapshot(StrictModel):
    symbol: Literal["BTC/USDT", "ETH/USDT"]
    timeframe: Literal["5m", "15m", "1h", "4h", "1d"]
    candle_start_utc: str
    data_as_of_utc: str
    fetched_at_utc: str
    facts: list[Fact] = Field(max_length=40)
    missing_metrics: list[str]
    risk_notes: list[str]
    limitations: list[str]
    sufficient: bool
    macro_summary: str
    macro_metadata: list[dict[str, str]] = Field(max_length=5)
    input_version: str


class AnalysisReport(StrictModel):
    symbol: Literal["BTC/USDT", "ETH/USDT"]
    timeframe: Literal["5m", "15m", "1h", "4h", "1d"]
    analyzed_at_utc: str
    data_as_of_utc: str
    signal: Signal
    technical_summary: str = Field(min_length=1, max_length=600)
    macro_summary: str = Field(min_length=1, max_length=600)
    bullish_factors: list[str] = Field(max_length=6)
    bearish_factors: list[str] = Field(max_length=6)
    risk_factors: list[str] = Field(max_length=12)
    evidence: list[Evidence] = Field(max_length=24)
    limitations: list[str] = Field(max_length=12)
    conclusion: str = Field(min_length=1, max_length=600)


class TokenUsage(StrictModel):
    input_tokens: int = Field(ge=0, strict=True)
    output_tokens: int = Field(ge=0, strict=True)
    total_tokens: int = Field(ge=0, strict=True)
