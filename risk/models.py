"""유한 수치/명확한 단위와 연구 상태만 허용한다. 매매 실행 스키마가 아니다."""

from typing import Literal
from math import isfinite

from pydantic import Field, FiniteFloat, field_validator, model_validator

from config.risk_settings import RISK_BOUNDS, RISK_POLICY_VERSION
from llm.schemas import StrictModel, Signal

RiskLevel = Literal["LOW", "MODERATE", "HIGH", "CRITICAL", "UNKNOWN"]
ResearchAction = Literal["REVIEW", "HOLD", "ABSTAIN"]


class RiskDataError(ValueError):
    pass


class PositionSettings(StrictModel):
    capital: FiniteFloat = Field(default=10000., gt=0, le=1e12)
    account_loss_rate: FiniteFloat = Field(default=.01, ge=0, le=1)
    stop_distance: FiniteFloat = Field(default=.03, gt=0, lt=1)
    leverage: FiniteFloat = Field(default=1., ge=1, le=100)
    fee_rate: FiniteFloat = Field(default=.001, ge=0, lt=1)
    slippage_rate: FiniteFloat = Field(default=.0005, ge=0, lt=1)

    @field_validator("capital", "account_loss_rate", "stop_distance", "leverage", "fee_rate", "slippage_rate", mode="before")
    @classmethod
    def numeric(cls, value):
        if type(value) not in (int, float) or not isfinite(value):
            raise ValueError("설정값은 유한한 숫자여야 합니다.")
        return value


class RiskThresholds(StrictModel):
    version: str = RISK_POLICY_VERSION
    annualized_volatility_pct: tuple[float, float, float] = RISK_BOUNDS["annualized_volatility_pct"]
    close_mdd_pct: tuple[float, float, float] = RISK_BOUNDS["close_mdd_pct"]
    atr_close_pct: tuple[float, float, float] = RISK_BOUNDS["atr_close_pct"]
    min_returns: int = Field(default=40, ge=2, le=5000)
    volatility_window: int = Field(default=20, ge=2, le=1000)
    volume_window: int = Field(default=20, ge=2, le=1000)
    expansion_ratio: FiniteFloat = Field(default=1.5, gt=1)
    volume_spike_ratio: FiniteFloat = Field(default=3., gt=1)

    @model_validator(mode="after")
    def ordered(self):
        for name in RISK_BOUNDS:
            values = getattr(self, name)
            if not all(isfinite(v) and v > 0 for v in values) or not values[0] < values[1] < values[2]:
                raise ValueError("위험 임계값은 유한한 양수의 엄격한 오름차순이어야 합니다.")
        return self


class QuantitativeMetrics(StrictModel):
    timeframe: str
    candle_count: int
    return_count: int
    expected_interval_seconds: FiniteFloat
    observed_intervals_seconds: list[FiniteFloat]
    window_start_utc: str | None
    data_as_of_utc: str | None
    latest_close: FiniteFloat | None
    volatility_per_bar_pct: FiniteFloat | None
    annualization_factor: FiniteFloat
    annualized_volatility_pct: FiniteFloat | None
    recent_volatility_pct: FiniteFloat | None
    previous_volatility_pct: FiniteFloat | None
    volatility_expansion_ratio: FiniteFloat | None
    volatility_expanding: bool
    close_mdd_pct: FiniteFloat | None
    mdd_peak_time_utc: str | None
    mdd_trough_time_utc: str | None
    atr_14: FiniteFloat | None
    atr_close_pct: FiniteFloat | None
    atr_contiguous_candles: int
    latest_volume: FiniteFloat | None
    previous_volume_sma: FiniteFloat | None
    volume_ratio: FiniteFloat | None
    volume_spike: bool
    missing_candles: int
    quality_issues: list[str]
    stale: bool


class PositionScenario(StrictModel):
    direction: Literal["LONG", "SHORT"]
    quantity: FiniteFloat = Field(ge=0)
    entry_fill: FiniteFloat = Field(gt=0)
    stop_reference_price: FiniteFloat = Field(gt=0)
    exit_fill: FiniteFloat = Field(gt=0)
    entry_notional: FiniteFloat = Field(ge=0)
    margin_required: FiniteFloat = Field(ge=0)
    entry_fee: FiniteFloat = Field(ge=0)
    total_fees: FiniteFloat = Field(ge=0)
    total_slippage_cost: FiniteFloat = Field(ge=0)
    estimated_cost: FiniteFloat = Field(ge=0)
    estimated_loss: FiniteFloat = Field(ge=0)


class PositionAssessment(StrictModel):
    reference_price: FiniteFloat = Field(gt=0)
    allowed_loss: FiniteFloat = Field(ge=0)
    requested_full_notional: FiniteFloat = Field(ge=0)
    requested_full_loss: FiniteFloat = Field(ge=0)
    stop_only_quantity: FiniteFloat = Field(ge=0)
    cost_aware_quantity: FiniteFloat = Field(ge=0)
    adjusted_notional: FiniteFloat = Field(ge=0)
    adjusted_max_loss: FiniteFloat = Field(ge=0)
    adjusted_max_cost: FiniteFloat = Field(ge=0)
    full_exposure_limit_exceeded: bool
    adjusted_limit_exceeded: bool
    scenarios: list[PositionScenario]


class RiskEvidence(StrictModel):
    reference_id: str
    source_type: Literal["RISK_QUANTITATIVE", "TECHNICAL", "MACRO", "SETTING"]
    metric_name: str
    metric_value: FiniteFloat
    unit: str
    observation_time: str


class RiskInput(StrictModel):
    symbol: str
    timeframe: str
    data_as_of_utc: str
    quantitative_metrics: QuantitativeMetrics
    position_settings: PositionSettings
    thresholds: RiskThresholds
    technical_sufficient: bool
    technical_signal: Signal
    technical_summary: str
    required_analysis_valid: bool
    downward_observations: list[str]
    debate_enabled: bool
    debate_status: str | None
    debate_conflicts: list[str]
    debate_summary: str | None
    debate_claim_ids: list[str]
    macro_status: str
    macro_summary: str
    evidence: list[RiskEvidence]


class RiskReport(StrictModel):
    execution_mode: Literal["Mock Risk Manager"] = "Mock Risk Manager"
    symbol: str
    timeframe: str
    data_as_of_utc: str
    risk_level: RiskLevel
    quantitative_metrics: QuantitativeMetrics
    risk_factors: list[str]
    supporting_evidence_ids: list[str]
    evidence: list[RiskEvidence]
    debate_claim_ids: list[str]
    position_settings: PositionSettings
    thresholds: RiskThresholds
    grade_basis: list[str]
    position_assessment: PositionAssessment | None
    estimated_loss: FiniteFloat | None
    estimated_cost: FiniteFloat | None
    risk_limit_exceeded: bool
    trading_action: ResearchAction
    limitations: list[str]
    risk_summary: str
    validation_status: Literal["VALIDATED", "INSUFFICIENT_DATA"]
