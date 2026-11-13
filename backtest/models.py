"""Streamlit에 의존하지 않는 백테스트 설정 및 결과 계약."""

from dataclasses import dataclass, field
from enum import Enum
from math import isfinite

import pandas as pd

from strategies.evaluator import EvaluationResult


class BacktestError(ValueError):
    """데이터/설정 또는 유효한 비음수 회계로 처리할 수 없는 경로 오류."""


class Position(str, Enum):
    FLAT = "FLAT"
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass(frozen=True)
class RiskConfig:
    """가격 비율(0.03 = 3%). 기본은 모두 OFF이며 비활성 비율은 계산에 쓰지 않는다."""

    stop_loss_enabled: bool = False
    stop_loss_rate: float = 0.03
    take_profit_enabled: bool = False
    take_profit_rate: float = 0.06
    trailing_stop_enabled: bool = False
    trailing_stop_rate: float = 0.03

    def __post_init__(self):
        for prefix in ("stop_loss", "take_profit", "trailing_stop"):
            if type(getattr(self, f"{prefix}_enabled")) is not bool:
                raise BacktestError("리스크 활성화 여부는 bool이어야 합니다.")
            rate = getattr(self, f"{prefix}_rate")
            if type(rate) not in (int, float):
                raise BacktestError("리스크 비율은 유한한 숫자여야 합니다.")
            try:
                rate = float(rate)
            except (ValueError, OverflowError) as error:
                raise BacktestError("리스크 비율은 유한한 숫자여야 합니다.") from error
            if not isfinite(rate) or not 0 < rate < 1:
                raise BacktestError("리스크 비율은 0 초과 100% 미만이어야 합니다.")
            object.__setattr__(self, f"{prefix}_rate", rate)

    @property
    def enabled(self) -> bool:
        return self.stop_loss_enabled or self.take_profit_enabled or self.trailing_stop_enabled

    def effective_settings(self) -> dict:
        """UI 결과 식별에도 사용한다. OFF인 입력값 변경은 결과를 무효화하지 않는다."""
        return {prefix: getattr(self, f"{prefix}_rate") if getattr(self, f"{prefix}_enabled") else None
                for prefix in ("stop_loss", "take_profit", "trailing_stop")}


@dataclass(frozen=True)
class BacktestConfig:
    initial_capital: float = 10000.0
    fee_rate: float = 0.001
    slippage_rate: float = 0.0005
    max_volume_participation: float = 0.10

    def __post_init__(self):
        for name in ("initial_capital", "fee_rate", "slippage_rate", "max_volume_participation"):
            value = getattr(self, name)
            if type(value) not in (int, float):
                raise BacktestError(f"{name}은 유한한 숫자여야 합니다.")
            try:
                value = float(value)
            except (ValueError, OverflowError) as error:
                raise BacktestError(f"{name}은 유한한 숫자여야 합니다.") from error
            if not isfinite(value):
                raise BacktestError(f"{name}은 유한한 숫자여야 합니다.")
            object.__setattr__(self, name, value)
        if self.initial_capital <= 0:
            raise BacktestError("초기 투자금은 0보다 커야 합니다.")
        if not 0 <= self.fee_rate < 1 or not 0 <= self.slippage_rate < 1:
            raise BacktestError("수수료와 슬리피지 비율은 0 이상 1 미만이어야 합니다.")
        if not 0 < self.max_volume_participation <= 1:
            raise BacktestError("거래량 참여율은 0 초과 1 이하여야 합니다.")


@dataclass(frozen=True)
class BacktestMetrics:
    final_equity: float
    cumulative_return_pct: float | None
    win_rate_pct: float | None
    max_drawdown_pct: float | None
    closed_trades: int
    average_trade_pnl: float | None
    total_fees: float
    realized_pnl: float
    unrealized_pnl: float
    rejected_orders: int


@dataclass(frozen=True)
class BacktestResult:
    equity_curve: pd.DataFrame
    trades: pd.DataFrame
    events: pd.DataFrame
    signals: EvaluationResult
    metrics: BacktestMetrics
    settings: BacktestConfig
    strategy_json: str
    timeframe: str
    symbol: str | None
    final_position: Position
    risk: RiskConfig = field(default_factory=RiskConfig)


@dataclass(frozen=True)
class RiskComparison:
    risk_off: BacktestResult
    risk_on: BacktestResult
