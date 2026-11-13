"""확정 봉의 조건 충족 여부만 평가한다. 포지션·체결·수익률은 계산하지 않는다."""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from config.settings import TIMEFRAME_MILLISECONDS
from strategies.models import Condition, ConditionGroup, Logic, OperandKind, Operator, Strategy


class StrategyEvaluationError(ValueError):
    """미확정 봉, 시간 정렬 또는 필요한 지표 데이터 오류."""


@dataclass(frozen=True)
class EvaluationResult:
    long: pd.Series
    short: pd.Series
    conflict: pd.Series
    state: pd.Series
    conditions: pd.DataFrame
    validity: pd.DataFrame


def _validate_frame(data, timeframe, as_of):
    selected = timeframe or data.attrs.get("timeframe")
    if selected not in TIMEFRAME_MILLISECONDS:
        raise StrategyEvaluationError("확정 봉 검증을 위해 지원 Timeframe을 지정해야 합니다.")
    if "timestamp" not in data or not isinstance(data["timestamp"].dtype, pd.DatetimeTZDtype):
        raise StrategyEvaluationError("timestamp는 시간대가 지정된 pandas datetime 컬럼이어야 합니다.")
    times = data["timestamp"].dt.tz_convert("UTC")
    if times.isna().any() or not times.is_unique or not times.is_monotonic_increasing:
        raise StrategyEvaluationError("timestamp는 결측·중복 없이 시간순이어야 합니다.")
    cutoff = pd.Timestamp.now(tz="UTC") if as_of is None else pd.Timestamp(as_of)
    if pd.isna(cutoff) or cutoff.tzinfo is None:
        raise StrategyEvaluationError("평가 기준 시각은 시간대가 지정된 유효한 시각이어야 합니다.")
    interval = pd.Timedelta(milliseconds=TIMEFRAME_MILLISECONDS[selected])
    if ((times + interval) > cutoff.tz_convert("UTC")).any():
        raise StrategyEvaluationError("미확정 또는 미래 캔들은 전략 조건 평가에 사용할 수 없습니다.")
    consecutive = times.diff().eq(interval)
    return consecutive


def _values(data: pd.DataFrame, column: str) -> pd.Series:
    if column not in data:
        raise StrategyEvaluationError(f"필요한 지표 컬럼이 없습니다: {column}")
    values = pd.to_numeric(data[column], errors="coerce").astype(float)
    return values.where(np.isfinite(values))


def _condition(data: pd.DataFrame, condition: Condition, consecutive: pd.Series):
    left = _values(data, condition.left.value)
    right = (_values(data, condition.right.indicator.value)
             if condition.right.kind is OperandKind.INDICATOR
             else pd.Series(condition.right.value, index=data.index, dtype=float))
    valid = left.notna() & right.notna()
    if condition.operator in (Operator.CROSS_UP, Operator.CROSS_DOWN):
        previous_left, previous_right = left.shift(1), right.shift(1)
        valid = valid & previous_left.notna() & previous_right.notna() & consecutive
        if condition.operator is Operator.CROSS_UP:
            matched = previous_left.le(previous_right) & left.gt(right)
        else:
            matched = previous_left.ge(previous_right) & left.lt(right)
    else:
        operations = {Operator.GT: left.gt, Operator.LT: left.lt,
                      Operator.GE: left.ge, Operator.LE: left.le}
        matched = operations[condition.operator](right)
    return (matched & valid).astype(bool), valid.astype(bool)


def evaluate_condition(
    data: pd.DataFrame, condition: Condition, *, timeframe: str | None = None,
    as_of: pd.Timestamp | None = None,
) -> pd.Series:
    if not isinstance(condition, Condition):
        raise StrategyEvaluationError("검증된 Condition 객체가 필요합니다.")
    consecutive = _validate_frame(data, timeframe, as_of)
    matched, _ = _condition(data, condition, consecutive)
    return matched.rename(condition.id)


def _combine(group: ConditionGroup, results: list[pd.Series], index: pd.Index):
    if not results:
        return pd.Series(False, index=index, dtype=bool)
    combined = results[0].copy()
    for result in results[1:]:
        combined = combined & result if group.combine is Logic.AND else combined | result
    return combined.astype(bool)


def evaluate_strategy(
    data: pd.DataFrame, strategy: Strategy, *, timeframe: str | None = None,
    as_of: pd.Timestamp | None = None,
) -> EvaluationResult:
    if not isinstance(strategy, Strategy):
        raise StrategyEvaluationError("검증된 Strategy 객체가 필요합니다.")
    consecutive = _validate_frame(data, timeframe, as_of)
    matches, validity, groups = {}, {}, {}
    for group in (strategy.long, strategy.short):
        group_matches = []
        for condition in group.conditions:
            matched, valid = _condition(data, condition, consecutive)
            matches[condition.id], validity[condition.id] = matched, valid
            group_matches.append(matched)
        groups[group.direction.value] = _combine(group, group_matches, data.index)
    long = groups["LONG"].rename("LONG")
    short = groups["SHORT"].rename("SHORT")
    conflict = (long & short).rename("CONFLICT")
    state = pd.Series(
        np.select([conflict.to_numpy(), long.to_numpy(), short.to_numpy()],
                  ["CONFLICT", "LONG", "SHORT"], default="NONE"),
        index=data.index, name="state",
    )
    return EvaluationResult(long, short, conflict, state,
                            pd.DataFrame(matches, index=data.index, dtype=bool),
                            pd.DataFrame(validity, index=data.index, dtype=bool))
