import unittest

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal, assert_series_equal

from strategies.evaluator import StrategyEvaluationError, evaluate_condition, evaluate_strategy
from strategies.models import (
    Condition, ConditionGroup, Direction, Indicator, Logic, Operand, OperandKind,
    Operator, Strategy, StrategyValidationError,
)

AS_OF = pd.Timestamp("2026-02-01T00:00:00Z")


def frame(values):
    data = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=len(values), freq="h", tz="UTC"),
        "close": pd.Series(values, dtype=float),
    }, index=pd.RangeIndex(len(values), name="original"))
    data.attrs = {"timeframe": "1h", "source": "explicit test fixture"}
    return data


def condition(id="c1", operator=Operator.GT, value=10, left=Indicator.CLOSE):
    return Condition(id, left, operator, Operand(OperandKind.NUMBER, value))


def strategy(long=(), short=(), logic=Logic.AND):
    return Strategy("테스트 전략", ConditionGroup(Direction.LONG, logic, tuple(long)),
                    ConditionGroup(Direction.SHORT, logic, tuple(short)))


class StrategyEvaluatorTests(unittest.TestCase):
    def test_numeric_comparisons_and_equality_boundaries(self):
        data = frame([9, 10, 11, np.nan])
        expected = {Operator.GT: [False, False, True, False],
                    Operator.LT: [True, False, False, False],
                    Operator.GE: [False, True, True, False],
                    Operator.LE: [True, True, False, False]}
        for operator, mask in expected.items():
            with self.subTest(operator=operator):
                result = evaluate_condition(data, condition(operator=operator), as_of=AS_OF)
                self.assertEqual(result.tolist(), mask)
                self.assertEqual(result.dtype, bool)
                self.assertTrue(result.index.equals(data.index))

    def test_cross_up_and_down_use_previous_inclusive_current_strict(self):
        data = frame([9, 10, 11, 10, 9, 10, 11])
        up = evaluate_condition(data, condition(operator=Operator.CROSS_UP), as_of=AS_OF)
        down = evaluate_condition(data, condition(operator=Operator.CROSS_DOWN), as_of=AS_OF)
        self.assertEqual(up.tolist(), [False, False, True, False, False, False, True])
        self.assertEqual(down.tolist(), [False, False, False, False, True, False, False])

    def test_cross_between_two_moving_indicators(self):
        data = frame([1, 3, 2, 5])
        data["ema_20"] = [2, 2, 2, 4]
        rule = Condition("price_cross", Indicator.CLOSE, Operator.CROSS_UP,
                         Operand(OperandKind.INDICATOR, indicator=Indicator.EMA20))
        self.assertEqual(evaluate_condition(data, rule, as_of=AS_OF).tolist(), [False, True, False, True])

    def test_macd_signal_comparison(self):
        data = frame([10, 11, 12])
        data["macd"], data["macd_signal"] = [-1, 1, 2], [0, 0, 3]
        rule = Condition("macd_cross", Indicator.MACD, Operator.CROSS_UP,
                         Operand(OperandKind.INDICATOR, indicator=Indicator.MACD_SIGNAL))
        self.assertEqual(evaluate_condition(data, rule, as_of=AS_OF).tolist(), [False, True, False])

    def test_nan_in_either_current_or_previous_operand_is_false(self):
        for values in ([9, np.nan, 11], [9, np.inf, 11], [9, 10, np.nan]):
            with self.subTest(values=values):
                result = evaluate_condition(frame(values), condition(operator=Operator.CROSS_UP), as_of=AS_OF)
                self.assertFalse(result.any())
        data = frame([9, 11, 12])
        data["ema_20"] = [10, np.nan, 10]
        rule = Condition("ema_cross", Indicator.CLOSE, Operator.CROSS_UP,
                         Operand(OperandKind.INDICATOR, indicator=Indicator.EMA20))
        self.assertFalse(evaluate_condition(data, rule, as_of=AS_OF).any())

    def test_missing_time_interval_does_not_create_cross(self):
        data = frame([9, 9, 11, 11])
        data["timestamp"] = pd.to_datetime([
            "2026-01-01T00:00Z", "2026-01-01T01:00Z",
            "2026-01-01T04:00Z", "2026-01-01T05:00Z",
        ])
        result = evaluate_condition(data, condition(operator=Operator.CROSS_UP), as_of=AS_OF)
        self.assertFalse(result.any())

    def test_and_and_or_combine_all_conditions(self):
        data = frame([9, 10, 11, np.nan])
        conditions = [condition("at_least", Operator.GE), condition("at_most", Operator.LE)]
        and_result = evaluate_strategy(data, strategy(conditions), as_of=AS_OF)
        or_result = evaluate_strategy(data, strategy(conditions, logic=Logic.OR), as_of=AS_OF)
        self.assertEqual(and_result.long.tolist(), [False, True, False, False])
        self.assertEqual(or_result.long.tolist(), [True, True, True, False])

    def test_nan_condition_is_false_but_other_or_condition_can_match(self):
        data = frame([11, 12])
        data["rsi_14"] = np.nan
        rules = [condition("rsi", Operator.LT, 30, Indicator.RSI), condition("price")]
        result = evaluate_strategy(data, strategy(rules, logic=Logic.OR), as_of=AS_OF)
        self.assertFalse(result.conditions["rsi"].any())
        self.assertTrue(result.long.all())
        self.assertFalse(result.validity["rsi"].any())

    def test_long_short_conflict_keeps_both_and_marks_conflict(self):
        result = evaluate_strategy(frame([9, 10, 11]),
                                   strategy([condition("long")], [condition("short", Operator.GE)]),
                                   as_of=AS_OF)
        self.assertEqual(result.long.tolist(), [False, False, True])
        self.assertEqual(result.short.tolist(), [False, True, True])
        self.assertEqual(result.conflict.tolist(), [False, False, True])
        self.assertEqual(result.state.tolist(), ["NONE", "SHORT", "CONFLICT"])

    def test_empty_groups_and_empty_dataframe_return_false_or_empty(self):
        result = evaluate_strategy(frame([11, 12]), strategy(), as_of=AS_OF)
        self.assertFalse(result.long.any())
        self.assertFalse(result.short.any())
        empty = evaluate_strategy(frame([]), strategy([condition()]), as_of=AS_OF)
        self.assertTrue(empty.long.empty)
        self.assertTrue(empty.conditions.empty)

    def test_one_bar_cannot_cross(self):
        self.assertFalse(evaluate_condition(frame([11]), condition(operator=Operator.CROSS_UP), as_of=AS_OF).any())

    def test_current_unconfirmed_and_future_candles_are_rejected(self):
        data = frame([9, 11])
        for cutoff in (pd.Timestamp("2026-01-01T01:30Z"), pd.Timestamp("2025-12-31T00:00Z")):
            with self.subTest(cutoff=cutoff):
                with self.assertRaisesRegex(StrategyEvaluationError, "미확정"):
                    evaluate_strategy(data, strategy([condition()]), as_of=cutoff)
        # 정확히 마감 시각이면 확정된 봉으로 평가 가능하다.
        result = evaluate_strategy(data, strategy([condition()]), as_of=pd.Timestamp("2026-01-01T02:00Z"))
        self.assertEqual(result.long.tolist(), [False, True])

    def test_missing_columns_timeframe_and_invalid_times_are_rejected(self):
        data = frame([9, 11])
        variants = [data.drop(columns="close"), data.iloc[::-1],
                    pd.concat([data, data.iloc[-1:]]),
                    data.assign(timestamp=data["timestamp"].dt.tz_localize(None))]
        for invalid in variants:
            with self.assertRaises(StrategyEvaluationError):
                evaluate_condition(invalid, condition(), as_of=AS_OF)
        data.attrs = {}
        with self.assertRaises(StrategyEvaluationError):
            evaluate_condition(data, condition(), as_of=AS_OF)
        self.assertTrue(evaluate_condition(data, condition(), timeframe="1h", as_of=AS_OF).iloc[-1])

    def test_original_dataframe_and_metadata_are_unchanged(self):
        data = frame([9, 10, 11])
        original = data.copy(deep=True)
        evaluate_strategy(data, strategy([condition()]), as_of=AS_OF)
        assert_frame_equal(data, original)
        self.assertEqual(data.attrs, original.attrs)

    def test_look_ahead_bias_prefix_and_future_mutation(self):
        data = frame([9, 11, 12, 8, 10, 13, 7, 15])
        model = strategy([condition("cross", Operator.CROSS_UP)], [condition("down", Operator.CROSS_DOWN)])
        complete = evaluate_strategy(data, model, as_of=AS_OF)
        for end in range(1, len(data)):
            prefix = evaluate_strategy(data.iloc[:end], model, as_of=AS_OF)
            assert_series_equal(prefix.long, complete.long.iloc[:end])
            assert_series_equal(prefix.short, complete.short.iloc[:end])
            assert_frame_equal(prefix.conditions, complete.conditions.iloc[:end])
        changed = data.copy()
        changed.loc[changed.index[5:], "close"] = 99999
        changed_result = evaluate_strategy(changed, model, as_of=AS_OF)
        assert_series_equal(changed_result.long.iloc[:5], complete.long.iloc[:5])


class StrategyModelTests(unittest.TestCase):
    def test_rsi_bounds_and_non_finite_constants_are_rejected(self):
        for value in (-1, 101, np.nan, np.inf, True, "30"):
            with self.subTest(value=value):
                with self.assertRaises(StrategyValidationError):
                    condition(value=value, left=Indicator.RSI)

    def test_incompatible_and_identical_indicators_are_rejected(self):
        for right in (Indicator.CLOSE, Indicator.VOLUME, Indicator.RSI):
            with self.assertRaises(StrategyValidationError):
                Condition("bad", Indicator.RSI, Operator.GT, Operand(OperandKind.INDICATOR, indicator=right))
        with self.assertRaises(StrategyValidationError):
            condition(value=-1, left=Indicator.VOLUME)

    def test_unknown_types_duplicate_ids_and_direction_mismatch_are_rejected(self):
        with self.assertRaises(StrategyValidationError):
            Condition("bad", "close", Operator.GT, Operand(OperandKind.NUMBER, 10))
        with self.assertRaises(StrategyValidationError):
            Condition("bad", Indicator.CLOSE, "==", Operand(OperandKind.NUMBER, 10))
        with self.assertRaises(StrategyValidationError):
            strategy([condition()], [condition()])
        with self.assertRaises(StrategyValidationError):
            Strategy("bad", ConditionGroup(Direction.SHORT, Logic.AND, ()), ConditionGroup(Direction.SHORT, Logic.AND, ()))

    def test_name_and_condition_count_are_limited(self):
        for name in ("", " ", "a" * 81, "bad\nname"):
            with self.assertRaises(StrategyValidationError):
                Strategy(name, ConditionGroup(Direction.LONG, Logic.AND, ()), ConditionGroup(Direction.SHORT, Logic.AND, ()))
        with self.assertRaises(StrategyValidationError):
            ConditionGroup(Direction.LONG, Logic.AND, tuple(condition(f"id_{i}") for i in range(21)))
