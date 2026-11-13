"""작은 합성 OHLCV와 수작업 손익으로 체결·원장·미래 데이터 미사용을 검증한다."""

import unittest

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from backtest.engine import run_backtest
from backtest.metrics import maximum_drawdown, calculate_metrics
from backtest.models import BacktestConfig, BacktestError, Position
from strategies.evaluator import StrategyEvaluationError
from strategies.models import (
    Condition, ConditionGroup, Direction, Indicator, Logic, Operand, OperandKind,
    Operator, Strategy,
)

AS_OF = pd.Timestamp("2026-02-01T00:00:00Z")


def strategy():
    return Strategy("합성 데이터 검산", ConditionGroup(Direction.LONG, Logic.AND, (
        Condition("long", Indicator.MACD, Operator.GT, Operand(OperandKind.NUMBER, 0)),
    )), ConditionGroup(Direction.SHORT, Logic.AND, (
        Condition("short", Indicator.MACD_SIGNAL, Operator.GT, Operand(OperandKind.NUMBER, 0)),
    )))


def data(opens, signals, closes=None, volume=None):
    closes = opens if closes is None else closes
    frame = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=len(opens), freq="h", tz="UTC"),
        "open": pd.Series(opens, dtype=float), "close": pd.Series(closes, dtype=float),
        "high": np.maximum(opens, closes) + 1, "low": np.minimum(opens, closes) - 1,
        "volume": [100000.0] * len(opens) if volume is None else volume,
        "macd": [1.0 if value in ("L", "X") else 0.0 for value in signals],
        "macd_signal": [1.0 if value in ("S", "X") else 0.0 for value in signals],
    })
    frame.attrs = {"timeframe": "1h", "symbol": "BTC/USDT", "source": "test only"}
    return frame


def run(frame, fee=0, slip=0, participation=0.1, capital=1000):
    return run_backtest(frame, strategy(), BacktestConfig(capital, fee, slip, participation), as_of=AS_OF)


class BacktestEngineTests(unittest.TestCase):
    def test_long_next_open_fill_and_known_profit(self):
        result = run(data([100, 100, 120, 150], ["L", "N", "S", "N"]))
        closed = result.trades.iloc[0]
        self.assertEqual(closed["entry_price"], 100)
        self.assertEqual(closed["quantity"], 10)
        self.assertEqual(closed["exit_price"], 150)
        self.assertEqual(closed["net_pnl"], 500)
        self.assertEqual(result.metrics.final_equity, 1500)
        self.assertEqual(result.metrics.cumulative_return_pct, 50)
        self.assertEqual(result.metrics.win_rate_pct, 100)
        self.assertEqual(result.final_position, Position.FLAT)
        fills = result.events.loc[result.events["status"] == "FILLED"]
        self.assertEqual(fills["signal_bar"].tolist(), [0, 2])
        self.assertEqual(fills["execution_bar"].tolist(), [1, 3])
        self.assertTrue((fills["execution_timestamp"] > fills["signal_timestamp"]).all())

    def test_short_ledger_known_profit_and_restricted_proceeds(self):
        result = run(data([100, 100, 80, 60], ["S", "N", "L", "N"]))
        self.assertEqual(result.metrics.final_equity, 1400)
        self.assertEqual(result.trades.iloc[0]["net_pnl"], 400)
        entry_mark = result.equity_curve.iloc[2]
        self.assertEqual(entry_mark["cash"], 2000)
        self.assertEqual(entry_mark["restricted_cash"], 2000)
        self.assertEqual(entry_mark["free_cash"], 0)
        self.assertEqual(entry_mark["quantity"], -10)
        self.assertEqual(entry_mark["equity"], 1000)
        self.assertEqual(result.metrics.realized_pnl, 400)
        self.assertEqual(result.metrics.unrealized_pnl, 0)

    def test_no_same_close_execution_even_if_next_open_is_different(self):
        result = run(data([100, 200], ["L", "N"], closes=[150, 210]))
        self.assertEqual(result.trades.iloc[0]["entry_price"], 200)
        self.assertEqual(result.trades.iloc[0]["quantity"], 5)
        self.assertEqual(result.metrics.final_equity, 1050)

    def test_last_bar_signal_cannot_fill(self):
        result = run(data([100], ["L"]))
        self.assertTrue(result.trades.empty)
        self.assertEqual(result.metrics.final_equity, 1000)
        self.assertEqual(result.events.iloc[0]["reason"], "NO_NEXT_CANDLE")

    def test_reversal_uses_a_separate_later_bar_signal(self):
        result = run(data([100] * 4, ["L", "S", "S", "N"]))
        fills = result.events.loc[result.events["status"] == "FILLED"]
        self.assertEqual(fills["action"].tolist(), ["ENTRY", "EXIT", "ENTRY"])
        self.assertEqual(fills["execution_bar"].tolist(), [1, 2, 3])
        self.assertEqual(fills["direction"].tolist(), ["LONG", "LONG", "SHORT"])
        self.assertEqual(result.equity_curve["position"].tolist(), ["FLAT", "FLAT", "LONG", "FLAT", "SHORT"])

    def test_flat_conflicts_do_not_enter(self):
        result = run(data([100] * 3, ["X", "X", "N"]))
        self.assertTrue(result.trades.empty)
        self.assertFalse(result.events["status"].eq("FILLED").any())
        self.assertEqual(result.final_position, Position.FLAT)

    def test_conflict_while_holding_exits_only(self):
        result = run(data([100] * 3, ["L", "X", "N"]))
        fills = result.events.loc[result.events["status"] == "FILLED"]
        self.assertEqual(fills["action"].tolist(), ["ENTRY", "EXIT"])
        self.assertEqual(fills.iloc[-1]["reason"], "OPPOSITE_SIGNAL_WITH_CONFLICT")
        self.assertEqual(result.final_position, Position.FLAT)

    def test_long_fees_and_adverse_slippage_hand_calculated(self):
        result = run(data([100] * 3, ["L", "S", "N"]), fee=0.01, slip=0.01)
        quantity = 1000 / (101 * 1.01)
        trade = result.trades.iloc[0]
        self.assertEqual(trade["entry_price"], 101)
        self.assertEqual(trade["exit_price"], 99)
        self.assertAlmostEqual(trade["quantity"], quantity)
        self.assertAlmostEqual(result.metrics.final_equity, quantity * 99 * 0.99)
        self.assertAlmostEqual(result.metrics.total_fees, quantity * (101 + 99) * 0.01)
        self.assertAlmostEqual(trade["net_pnl"], -4 * quantity)

    def test_short_fees_and_adverse_slippage_hand_calculated(self):
        result = run(data([100] * 3, ["S", "L", "N"]), fee=0.01, slip=0.01)
        quantity = 1000 / (100 + 99 * 0.01)
        trade = result.trades.iloc[0]
        self.assertEqual(trade["entry_price"], 99)
        self.assertEqual(trade["exit_price"], 101)
        self.assertAlmostEqual(trade["quantity"], quantity)
        self.assertAlmostEqual(result.metrics.final_equity, 1000 - 4 * quantity)
        self.assertLessEqual(quantity * 100, 1000)
        self.assertAlmostEqual(result.metrics.total_fees, 2 * quantity)

    def test_open_position_has_unrealized_pnl_and_only_entry_fee(self):
        result = run(data([100, 100], ["L", "N"], closes=[100, 120]), fee=0.001)
        quantity = 1000 / 100.1
        self.assertEqual(result.final_position, Position.LONG)
        self.assertEqual(result.metrics.closed_trades, 0)
        self.assertIsNone(result.metrics.win_rate_pct)
        self.assertIsNone(result.metrics.average_trade_pnl)
        self.assertEqual(result.trades.iloc[0]["status"], "OPEN")
        self.assertTrue(pd.isna(result.trades.iloc[0]["net_pnl"]))
        self.assertAlmostEqual(result.metrics.total_fees, quantity * 100 * 0.001)
        self.assertAlmostEqual(result.metrics.realized_pnl, -result.metrics.total_fees)
        self.assertAlmostEqual(result.metrics.unrealized_pnl, 20 * quantity)
        self.assertAlmostEqual(result.metrics.final_equity, 1000 + result.metrics.realized_pnl + result.metrics.unrealized_pnl)

    def test_drawdown_contains_initial_capital_and_mark_to_market(self):
        result = run(data([100] * 4, ["L", "N", "N", "N"], closes=[100, 120, 90, 100]))
        self.assertAlmostEqual(result.metrics.max_drawdown_pct, 25)
        self.assertEqual(result.equity_curve.iloc[0]["equity"], 1000)
        self.assertAlmostEqual(maximum_drawdown(pd.Series([1000, 950, 1200, 900])), 25)

    def test_win_rate_denominator_and_mean_include_only_closed_trades(self):
        result = run(data([100, 100, 110, 100, 90], ["L", "S", "L", "S", "N"]))
        self.assertEqual(result.trades["net_pnl"].tolist(), [100, -110])
        self.assertEqual(result.metrics.closed_trades, 2)
        self.assertEqual(result.metrics.win_rate_pct, 50)
        self.assertEqual(result.metrics.average_trade_pnl, -5)
        self.assertEqual(result.metrics.final_equity, 990)

    def test_no_trades_and_empty_data_distinguish_zero_from_na(self):
        flat = run(data([100] * 3, ["N"] * 3))
        self.assertTrue((flat.equity_curve["equity"] == 1000).all())
        self.assertEqual(flat.metrics.cumulative_return_pct, 0)
        self.assertEqual(flat.metrics.max_drawdown_pct, 0)
        self.assertEqual(flat.metrics.total_fees, 0)
        self.assertIsNone(flat.metrics.win_rate_pct)
        empty = run(data([], []))
        self.assertEqual(empty.metrics.final_equity, 1000)
        self.assertIsNone(empty.metrics.cumulative_return_pct)
        self.assertIsNone(empty.metrics.max_drawdown_pct)
        self.assertEqual(empty.metrics.closed_trades, 0)

    def test_entry_volume_cap_and_zero_volume(self):
        limited = run(data([100, 100], ["L", "N"], volume=[2, 10000]))
        self.assertEqual(limited.trades.iloc[0]["quantity"], 0.2)
        self.assertEqual(limited.equity_curve.iloc[-1]["cash"], 980)
        no_volume = run(data([100, 100], ["L", "N"], volume=[0, 10000]))
        self.assertTrue(no_volume.trades.empty)
        self.assertEqual(no_volume.events.iloc[0]["reason"], "NO_CASH_OR_VOLUME")

    def test_exit_volume_shortage_is_rejected_without_unlimited_fill(self):
        result = run(data([100] * 3, ["L", "S", "N"], volume=[100, 1, 100]))
        self.assertEqual(result.final_position, Position.LONG)
        self.assertEqual(result.metrics.closed_trades, 0)
        self.assertEqual(result.events.iloc[-1]["reason"], "INSUFFICIENT_VOLUME_FOR_FULL_EXIT")

    def test_execution_bar_volume_is_not_used_before_it_is_known(self):
        result = run(data([100, 100], ["L", "N"], volume=[100000, 0]))
        self.assertEqual(result.trades.iloc[0]["quantity"], 10)
        self.assertEqual(result.events.iloc[0]["status"], "FILLED")

    def test_missing_next_candle_does_not_fill_at_later_available_open(self):
        frame = data([100] * 3, ["L", "N", "N"])
        frame["timestamp"] = pd.to_datetime(["2026-01-01T00:00Z", "2026-01-01T02:00Z", "2026-01-01T03:00Z"])
        result = run(frame)
        self.assertTrue(result.trades.empty)
        self.assertEqual(result.events.iloc[0]["reason"], "NEXT_CANDLE_MISSING")

    def test_accounting_and_quantities_are_consistent(self):
        for signals in (["L", "S", "S", "L", "N"], ["S", "N", "L", "N", "N"]):
            result = run(data([100, 100, 105, 101, 104], signals), fee=0.001, slip=0.0005)
            curve = result.equity_curve
            np.testing.assert_allclose(curve["cash"] + curve["position_value"], curve["equity"])
            np.testing.assert_allclose(1000 + curve["realized_pnl"] + curve["unrealized_pnl"], curve["equity"])
            np.testing.assert_allclose(curve["cash"] - curve["restricted_cash"], curve["free_cash"], atol=1e-7)
            self.assertTrue((curve[["cash", "free_cash", "equity"]] >= 0).all().all())
            self.assertTrue((curve.loc[curve["position"] == "FLAT", "quantity"] == 0).all())

    def test_short_insolvency_raises_instead_of_clipping_loss(self):
        for frame in (
            data([100, 100], ["S", "N"], closes=[100, 300]),
            data([100, 100, 300], ["S", "L", "N"]),
        ):
            with self.assertRaisesRegex(BacktestError, "음수"):
                run(frame)

    def test_input_is_unchanged_and_runs_are_reproducible(self):
        frame = data([100, 100, 110, 100], ["L", "S", "S", "N"])
        original = frame.copy(deep=True)
        first, second = run(frame), run(frame)
        assert_frame_equal(frame, original)
        self.assertEqual(frame.attrs, original.attrs)
        assert_frame_equal(first.equity_curve, second.equity_curve)
        assert_frame_equal(first.events, second.events)
        assert_frame_equal(first.trades, second.trades)
        self.assertEqual(first.metrics, second.metrics)

    def test_future_prices_cannot_change_past_equity_or_fills(self):
        frame = data([100, 100, 110, 100, 90], ["L", "S", "S", "L", "N"])
        complete = run(frame)
        for end in (1, 2, 3, 4):
            prefix = run(frame.iloc[:end])
            assert_frame_equal(prefix.equity_curve, complete.equity_curve.iloc[:end+1])
        altered = frame.copy()
        altered.loc[4, ["open", "close", "high", "low"]] = [95, 95, 96, 94]
        changed = run(altered)
        assert_frame_equal(changed.equity_curve.iloc[:5], complete.equity_curve.iloc[:5])

    def test_bad_data_and_unconfirmed_candles_are_rejected(self):
        frame = data([100, 100], ["L", "N"])
        for invalid in (frame.drop(columns="open"), frame.assign(open=0),
                        frame.assign(volume=-1), frame.assign(close=np.nan), frame.assign(high=1)):
            with self.assertRaises(BacktestError):
                run(invalid)
        with self.assertRaises(StrategyEvaluationError):
            run_backtest(frame, strategy(), as_of=pd.Timestamp("2026-01-01T01:30Z"))

    def test_config_validation_and_defaults(self):
        config = BacktestConfig()
        self.assertEqual(config.initial_capital, 10000)
        self.assertEqual(config.fee_rate, 0.001)
        self.assertEqual(config.slippage_rate, 0.0005)
        for kwargs in ({"initial_capital": 0}, {"initial_capital": True}, {"fee_rate": -1},
                       {"slippage_rate": 1}, {"max_volume_participation": 0}, {"fee_rate": np.nan}):
            with self.assertRaises(BacktestError):
                BacktestConfig(**kwargs)

    def test_metrics_zero_denominator_and_invalid_equity_are_rejected(self):
        result = run(data([100], ["N"]))
        with self.assertRaises(BacktestError):
            calculate_metrics(result.equity_curve, result.trades, result.events, 0)
        for values in ([0, 100], [100, -1], [100, np.inf]):
            with self.assertRaises(BacktestError):
                maximum_drawdown(pd.Series(values))
