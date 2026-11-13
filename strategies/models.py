"""검증된 불변 전략 객체. JSON 버전 1은 방향별 단일 AND/OR 그룹을 지원한다."""

from dataclasses import dataclass
from enum import Enum
from math import isfinite
import re

SCHEMA_VERSION = 1
MAX_CONDITIONS_PER_DIRECTION = 20


class StrategyValidationError(ValueError):
    """유효하지 않거나 의미가 맞지 않는 전략 조건."""


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class Logic(str, Enum):
    AND = "AND"
    OR = "OR"


class Operator(str, Enum):
    GT = ">"
    LT = "<"
    GE = ">="
    LE = "<="
    CROSS_UP = "Cross Up"
    CROSS_DOWN = "Cross Down"


class OperandKind(str, Enum):
    NUMBER = "number"
    INDICATOR = "indicator"


class Indicator(str, Enum):
    RSI = "rsi_14"
    MACD = "macd"
    MACD_SIGNAL = "macd_signal"
    MACD_HISTOGRAM = "macd_histogram"
    EMA20 = "ema_20"
    EMA60 = "ema_60"
    SMA20 = "sma_20"
    SMA60 = "sma_60"
    BB_UPPER = "bb_upper"
    BB_MIDDLE = "bb_middle"
    BB_LOWER = "bb_lower"
    CLOSE = "close"
    OPEN = "open"
    HIGH = "high"
    LOW = "low"
    VOLUME = "volume"
    VOLUME_SMA = "volume_sma_20"
    VOLUME_RATIO = "volume_ratio_20"


@dataclass(frozen=True)
class IndicatorSpec:
    label: str
    unit: str
    minimum: float | None = None
    maximum: float | None = None


INDICATOR_SPECS = {
    Indicator.RSI: IndicatorSpec("RSI (14)", "rsi", 0, 100),
    Indicator.MACD: IndicatorSpec("MACD", "price_difference"),
    Indicator.MACD_SIGNAL: IndicatorSpec("MACD Signal", "price_difference"),
    Indicator.MACD_HISTOGRAM: IndicatorSpec("MACD Histogram", "price_difference"),
    Indicator.EMA20: IndicatorSpec("EMA 20", "price", 0),
    Indicator.EMA60: IndicatorSpec("EMA 60", "price", 0),
    Indicator.SMA20: IndicatorSpec("SMA 20", "price", 0),
    Indicator.SMA60: IndicatorSpec("SMA 60", "price", 0),
    Indicator.BB_UPPER: IndicatorSpec("Bollinger Upper", "price", 0),
    Indicator.BB_MIDDLE: IndicatorSpec("Bollinger Middle", "price", 0),
    Indicator.BB_LOWER: IndicatorSpec("Bollinger Lower", "price", 0),
    Indicator.CLOSE: IndicatorSpec("종가 (Close)", "price", 0),
    Indicator.OPEN: IndicatorSpec("시가 (Open)", "price", 0),
    Indicator.HIGH: IndicatorSpec("고가 (High)", "price", 0),
    Indicator.LOW: IndicatorSpec("저가 (Low)", "price", 0),
    Indicator.VOLUME: IndicatorSpec("거래량 (Volume)", "volume", 0),
    Indicator.VOLUME_SMA: IndicatorSpec("거래량 SMA 20", "volume", 0),
    Indicator.VOLUME_RATIO: IndicatorSpec("거래량 / SMA 20", "ratio", 0),
}


def compatible_indicators(left: Indicator) -> tuple[Indicator, ...]:
    return tuple(item for item in Indicator
                 if item != left and INDICATOR_SPECS[item].unit == INDICATOR_SPECS[left].unit)


@dataclass(frozen=True)
class Operand:
    kind: OperandKind
    value: float | None = None
    indicator: Indicator | None = None

    def __post_init__(self):
        if not isinstance(self.kind, OperandKind):
            raise StrategyValidationError("지원하지 않는 우측 피연산자 유형입니다.")
        if self.kind is OperandKind.NUMBER:
            if type(self.value) not in (int, float) or self.indicator is not None:
                raise StrategyValidationError("숫자 피연산자는 숫자 값 하나만 가져야 합니다.")
            try:
                value = float(self.value)
            except (OverflowError, ValueError) as error:
                raise StrategyValidationError("상수는 유한한 숫자여야 합니다.") from error
            if not isfinite(value):
                raise StrategyValidationError("상수는 유한한 숫자여야 합니다.")
            object.__setattr__(self, "value", value)
        elif not isinstance(self.indicator, Indicator) or self.value is not None:
            raise StrategyValidationError("지표 피연산자는 지원 지표 하나만 가져야 합니다.")


@dataclass(frozen=True)
class Condition:
    id: str
    left: Indicator
    operator: Operator
    right: Operand

    def __post_init__(self):
        if not isinstance(self.id, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.id) is None:
            raise StrategyValidationError("조건 ID는 1~64자의 영문·숫자·밑줄·하이픈이어야 합니다.")
        if not isinstance(self.left, Indicator) or not isinstance(self.operator, Operator):
            raise StrategyValidationError("지원하지 않는 지표 또는 연산자입니다.")
        if not isinstance(self.right, Operand):
            raise StrategyValidationError("우측 피연산자 구조가 올바르지 않습니다.")
        spec = INDICATOR_SPECS[self.left]
        if self.right.kind is OperandKind.INDICATOR:
            if self.right.indicator not in compatible_indicators(self.left):
                raise StrategyValidationError("같은 지표 또는 단위가 다른 지표끼리는 비교할 수 없습니다.")
        else:
            value = self.right.value
            if spec.minimum is not None and value < spec.minimum:
                raise StrategyValidationError(f"{spec.label} 비교 상수는 {spec.minimum:g} 이상이어야 합니다.")
            if spec.maximum is not None and value > spec.maximum:
                raise StrategyValidationError(f"{spec.label} 비교 상수는 {spec.maximum:g} 이하여야 합니다.")


@dataclass(frozen=True)
class ConditionGroup:
    direction: Direction
    combine: Logic
    conditions: tuple[Condition, ...]

    def __post_init__(self):
        if not isinstance(self.direction, Direction) or not isinstance(self.combine, Logic):
            raise StrategyValidationError("방향은 LONG/SHORT, 결합은 AND/OR만 지원합니다.")
        if not isinstance(self.conditions, tuple) or not all(isinstance(item, Condition) for item in self.conditions):
            raise StrategyValidationError("그룹은 검증된 조건의 불변 tuple이어야 합니다.")
        if len(self.conditions) > MAX_CONDITIONS_PER_DIRECTION:
            raise StrategyValidationError(f"한 방향의 조건은 최대 {MAX_CONDITIONS_PER_DIRECTION}개입니다.")
        ids = [item.id for item in self.conditions]
        if len(ids) != len(set(ids)):
            raise StrategyValidationError("조건 ID가 중복되었습니다.")


@dataclass(frozen=True)
class Strategy:
    name: str
    long: ConditionGroup
    short: ConditionGroup
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != SCHEMA_VERSION:
            raise StrategyValidationError(f"지원 스키마 버전은 {SCHEMA_VERSION}입니다.")
        if not isinstance(self.name, str) or not 1 <= len(self.name.strip()) <= 80:
            raise StrategyValidationError("전략 이름은 공백을 제외하고 1~80자여야 합니다.")
        if any(ord(character) < 32 for character in self.name):
            raise StrategyValidationError("전략 이름에 제어 문자를 사용할 수 없습니다.")
        object.__setattr__(self, "name", self.name.strip())
        if not isinstance(self.long, ConditionGroup) or self.long.direction is not Direction.LONG:
            raise StrategyValidationError("LONG 그룹의 방향이 올바르지 않습니다.")
        if not isinstance(self.short, ConditionGroup) or self.short.direction is not Direction.SHORT:
            raise StrategyValidationError("SHORT 그룹의 방향이 올바르지 않습니다.")
        ids = [item.id for group in (self.long, self.short) for item in group.conditions]
        if len(ids) != len(set(ids)):
            raise StrategyValidationError("전략의 조건 ID는 방향 간에도 고유해야 합니다.")


def describe_condition(condition: Condition) -> str:
    left = INDICATOR_SPECS[condition.left].label
    right = (f"{condition.right.value:g}" if condition.right.kind is OperandKind.NUMBER
             else INDICATOR_SPECS[condition.right.indicator].label)
    descriptions = {
        Operator.GT: "보다 큼", Operator.LT: "보다 작음",
        Operator.GE: "이상", Operator.LE: "이하",
        Operator.CROSS_UP: "상향 돌파 (직전 ≤, 현재 >)",
        Operator.CROSS_DOWN: "하향 돌파 (직전 ≥, 현재 <)",
    }
    return f"{left} → {right} {descriptions[condition.operator]}"


def describe_group(group: ConditionGroup) -> str:
    if not group.conditions:
        return f"{group.direction.value}: 조건 없음 · 비활성"
    joiner = "\n그리고 (AND)\n" if group.combine is Logic.AND else "\n또는 (OR)\n"
    return f"{group.direction.value}\n" + joiner.join(describe_condition(item) for item in group.conditions)


def default_strategy() -> Strategy:
    return Strategy(
        "RSI 조건 예시",
        ConditionGroup(Direction.LONG, Logic.AND, (
            Condition("long_rsi", Indicator.RSI, Operator.LT, Operand(OperandKind.NUMBER, 30)),
        )),
        ConditionGroup(Direction.SHORT, Logic.AND, (
            Condition("short_rsi", Indicator.RSI, Operator.GT, Operand(OperandKind.NUMBER, 70)),
        )),
    )
