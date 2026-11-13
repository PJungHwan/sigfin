"""작은 합성 봉으로 리스크 가격·체결 우선순위·비용·이전 엔진 호환성을 검산한다."""

from dataclasses import asdict
from io import StringIO
import json
from pathlib import Path
import unittest

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from backtest.engine import compare_risk, run_backtest
from backtest.models import BacktestConfig, BacktestError, Position, RiskConfig
from backtest.risk import risk_levels
from test_backtest import AS_OF, data, strategy
from ui.backtest_view import result_fingerprint


CONFIG = BacktestConfig(1000, 0, 0, 0.1)


def simulate(frame, risk, config=CONFIG):
    return run_backtest(frame, strategy(), config, risk=risk, as_of=AS_OF)


def candles(direction="L", *, highs=None, lows=None, opens=None, closes=None, signals=None, volume=None):
    length = len(opens or highs or lows or [100] * 3)
    frame = data(opens or [100]*length, signals or [direction]+["N"]*(length-1), closes, volume)
    if highs is not None:
        frame["high"] = highs
    if lows is not None:
        frame["low"] = lows
    return frame


class RiskTests(unittest.TestCase):
    def test_long_stop_loss_price_and_net_pnl(self):
        result = simulate(candles(lows=[99, 96, 99]), RiskConfig(stop_loss_enabled=True))
        trade = result.trades.iloc[0]
        self.assertEqual(trade["exit_price"], 97)
        self.assertEqual(trade["net_pnl"], -30)
        self.assertEqual(trade["exit_reason"], "STOP_LOSS")
        self.assertEqual(result.metrics.final_equity, 970)

    def test_long_take_profit(self):
        result = simulate(candles(highs=[101, 107, 101]), RiskConfig(take_profit_enabled=True))
        self.assertEqual(result.trades.iloc[0]["exit_price"], 106)
        self.assertEqual(result.trades.iloc[0]["net_pnl"], 60)

    def test_short_stop_loss(self):
        result = simulate(candles("S", highs=[101, 104, 101]), RiskConfig(stop_loss_enabled=True))
        self.assertEqual(result.trades.iloc[0]["exit_price"], 103)
        self.assertEqual(result.metrics.final_equity, 970)

    def test_short_take_profit(self):
        result = simulate(candles("S", lows=[99, 93, 99]), RiskConfig(take_profit_enabled=True))
        self.assertEqual(result.trades.iloc[0]["exit_price"], 94)
        self.assertEqual(result.metrics.final_equity, 1060)

    def test_touch_equal_to_boundary_triggers(self):
        for direction, column, value, risk in (
            ("L", "low", 97, RiskConfig(stop_loss_enabled=True)),
            ("L", "high", 106, RiskConfig(take_profit_enabled=True)),
            ("S", "high", 103, RiskConfig(stop_loss_enabled=True)),
            ("S", "low", 94, RiskConfig(take_profit_enabled=True)),
        ):
            with self.subTest(direction=direction, column=column):
                frame = candles(direction)
                frame.loc[1, column] = value
                self.assertEqual(simulate(frame, risk).metrics.closed_trades, 1)

    def test_long_trailing_uses_previous_completed_high(self):
        frame = candles(highs=[101, 110, 109], lows=[99, 99, 103],
                        opens=[100, 100, 108], closes=[100, 108, 104])
        result = simulate(frame, RiskConfig(trailing_stop_enabled=True, trailing_stop_rate=.05))
        self.assertAlmostEqual(result.trades.iloc[0]["exit_price"], 104.5)
        self.assertEqual(result.trades.iloc[0]["exit_candle_timestamp"], frame.iloc[2]["timestamp"])

    def test_short_trailing_uses_previous_completed_low(self):
        frame = candles("S", highs=[101, 101, 96], lows=[99, 90, 91],
                        opens=[100, 100, 92], closes=[100, 92, 95])
        result = simulate(frame, RiskConfig(trailing_stop_enabled=True, trailing_stop_rate=.05))
        self.assertAlmostEqual(result.trades.iloc[0]["exit_price"], 94.5)
        self.assertAlmostEqual(result.metrics.final_equity, 1055)

    def test_same_bar_new_extreme_cannot_retroactively_trigger_trailing(self):
        for direction, frame in (
            ("L", candles(highs=[101, 120], lows=[99, 99], closes=[100, 115])),
            ("S", candles("S", highs=[101, 101], lows=[99, 80], closes=[100, 85])),
        ):
            with self.subTest(direction=direction):
                result = simulate(frame, RiskConfig(trailing_stop_enabled=True, trailing_stop_rate=.05))
                self.assertEqual(result.metrics.closed_trades, 0)
                self.assertEqual(result.final_position.value, "LONG" if direction == "L" else "SHORT")

    def test_stop_loss_gap_fills_open_in_both_directions(self):
        for direction, opening, expected in (("L", 90, 900), ("S", 110, 900)):
            with self.subTest(direction=direction):
                frame = candles(direction, opens=[100, 100, opening])
                result = simulate(frame, RiskConfig(stop_loss_enabled=True))
                self.assertEqual(result.trades.iloc[0]["exit_price"], opening)
                self.assertEqual(result.metrics.final_equity, expected)
                self.assertEqual(result.trades.iloc[0]["exit_timing"], "OPEN_GAP")

    def test_take_profit_gap_is_known_before_later_stop_touch(self):
        for direction, opening, high, low in (("L", 110, 111, 90), ("S", 90, 110, 89)):
            frame = candles(direction, opens=[100, 100, opening])
            frame.loc[2, ["high", "low"]] = [high, low]
            result = simulate(frame, RiskConfig(stop_loss_enabled=True, take_profit_enabled=True))
            self.assertEqual(result.trades.iloc[0]["exit_price"], opening)
            self.assertEqual(result.trades.iloc[0]["exit_reason"], "TAKE_PROFIT")

    def test_intrabar_both_stop_and_target_choose_stop(self):
        for direction in ("L", "S"):
            result = simulate(candles(direction, highs=[101, 110], lows=[99, 90]),
                              RiskConfig(stop_loss_enabled=True, take_profit_enabled=True))
            self.assertEqual(result.trades.iloc[0]["exit_reason"], "STOP_LOSS")
            event = result.events.iloc[-1]
            self.assertIn("TAKE_PROFIT", event["risk_candidates"])
            self.assertEqual(event["execution_timing"], "INTRABAR_OHLC")
            self.assertTrue(pd.isna(event["execution_timestamp"]))

    def test_stop_and_trailing_choose_worse_price_for_each_direction(self):
        examples = (
            candles(highs=[101, 110, 109], lows=[99, 99, 90], opens=[100, 100, 108], closes=[100, 108, 100]),
            candles("S", highs=[101, 101, 110], lows=[99, 90, 91], opens=[100, 100, 92], closes=[100, 92, 100]),
        )
        for frame, price in zip(examples, (97, 103)):
            result = simulate(frame, RiskConfig(stop_loss_enabled=True, trailing_stop_enabled=True,
                                               trailing_stop_rate=.05))
            self.assertEqual(result.trades.iloc[0]["exit_price"], price)
            self.assertEqual(result.trades.iloc[0]["exit_reason"], "STOP_LOSS")
            self.assertIn("TRAILING_STOP", result.events.iloc[-1]["risk_candidates"])

    def test_wider_trailing_can_be_worse_than_fixed_stop(self):
        for direction, high, low, expected in (("L", 101, 89, 90), ("S", 111, 99, 110)):
            with self.subTest(direction=direction):
                result = simulate(candles(direction, highs=[101, high], lows=[99, low]),
                                  RiskConfig(stop_loss_enabled=True, trailing_stop_enabled=True,
                                             trailing_stop_rate=.1))
                self.assertAlmostEqual(result.trades.iloc[0]["exit_price"], expected)
                self.assertEqual(result.trades.iloc[0]["exit_reason"], "TRAILING_STOP")

    def test_trailing_gap_uses_open_instead_of_old_threshold(self):
        frame = candles(opens=[100, 100, 100], closes=[100, 108, 100],
                        highs=[101, 110, 101], lows=[99, 99, 99])
        result = simulate(frame, RiskConfig(trailing_stop_enabled=True, trailing_stop_rate=.05))
        self.assertEqual(result.trades.iloc[0]["exit_price"], 100)
        self.assertEqual(result.trades.iloc[0]["exit_reason"], "TRAILING_STOP")
        self.assertEqual(result.trades.iloc[0]["exit_timing"], "OPEN_GAP")

    def test_entry_bar_risk_is_active_after_open_fill(self):
        result = simulate(candles(highs=[101, 110], lows=[99, 90]),
                          RiskConfig(stop_loss_enabled=True, take_profit_enabled=True))
        fills = result.events.loc[result.events.status == "FILLED"]
        self.assertEqual(fills.execution_bar.tolist(), [1, 1])
        self.assertEqual(fills.action.tolist(), ["ENTRY", "EXIT"])
        self.assertEqual(result.metrics.final_equity, 970)

    def test_risk_does_not_exit_before_entry_and_last_bar_can_exit_intrabar(self):
        frame = candles(opens=[100, 100], lows=[1, 96])
        result = simulate(frame, RiskConfig(stop_loss_enabled=True))
        self.assertEqual(result.events.execution_bar.tolist(), [1, 1])
        self.assertEqual(result.metrics.closed_trades, 1)
        no_next = simulate(candles(signals=["N", "N", "L"]), RiskConfig(stop_loss_enabled=True))
        self.assertTrue(no_next.trades.empty)

    def test_gap_risk_precedes_signal_exit_without_duplicate_fill_or_reversal(self):
        frame = candles(opens=[100, 100, 90], signals=["L", "S", "N"])
        result = simulate(frame, RiskConfig(stop_loss_enabled=True))
        self.assertEqual(result.events.action.tolist(), ["ENTRY", "EXIT"])
        self.assertEqual(result.trades.iloc[0]["exit_reason"], "STOP_LOSS")
        self.assertEqual(result.final_position, Position.FLAT)

    def test_signal_exit_at_open_precedes_later_intrabar_risk(self):
        frame = candles(signals=["L", "S", "N"], lows=[99, 99, 90])
        result = simulate(frame, RiskConfig(stop_loss_enabled=True))
        self.assertEqual(result.trades.iloc[0]["exit_reason"], "SIGNAL_EXIT")
        self.assertEqual(result.trades.iloc[0]["exit_price"], 100)
        self.assertEqual(len(result.events.loc[result.events.action == "EXIT"]), 1)

    def test_risk_exit_liquidity_shortage_latches_and_retries_even_after_recovery(self):
        frame = candles(opens=[100, 100, 100, 110], lows=[99, 99, 90, 109],
                        volume=[10000, 1, 10000, 10000])
        result = simulate(frame, RiskConfig(stop_loss_enabled=True))
        rejected = result.events.loc[result.events.status == "REJECTED"].iloc[0]
        self.assertEqual(rejected.exit_reason, "STOP_LOSS")
        self.assertEqual(rejected.quantity, 0)
        self.assertEqual(rejected.requested_quantity, 10)
        self.assertEqual(rejected.liquidity_available, .1)
        self.assertEqual(result.trades.iloc[0]["exit_price"], 110)
        self.assertEqual(result.trades.iloc[0]["exit_timing"], "RETRY_OPEN")
        self.assertEqual(result.events.iloc[-1].risk_trigger_bar, 2)

    def test_pending_risk_retries_once_per_bar_and_preempts_signal(self):
        frame = candles(opens=[100]*5, lows=[99, 99, 90, 99, 99],
                        signals=["L", "N", "S", "S", "N"], volume=[10000, 1, 1, 10000, 10000])
        result = simulate(frame, RiskConfig(stop_loss_enabled=True))
        exits = result.events.loc[result.events.action == "EXIT"]
        self.assertEqual(exits.execution_bar.tolist(), [2, 3, 4])
        self.assertEqual(exits.status.tolist(), ["REJECTED", "REJECTED", "FILLED"])
        self.assertEqual(exits.exit_reason.tolist(), ["STOP_LOSS"]*3)
        self.assertEqual(result.final_position, Position.FLAT)

    def test_reentry_requires_new_close_signal_after_risk_exit(self):
        frame = candles(opens=[100]*4, lows=[99, 96, 99, 99], signals=["L", "L", "N", "N"])
        result = simulate(frame, RiskConfig(stop_loss_enabled=True))
        fills = result.events.loc[result.events.status == "FILLED"]
        self.assertEqual(fills.action.tolist(), ["ENTRY", "EXIT", "ENTRY"])
        self.assertEqual(fills.execution_bar.tolist(), [1, 1, 2])
        self.assertEqual(fills.signal_bar.iloc[-1], 1)

    def test_insufficient_risk_volume_retains_entire_position_without_partial_exit(self):
        frame = candles(lows=[99, 99, 90], volume=[10000, 1, 10000])
        result = simulate(frame, RiskConfig(stop_loss_enabled=True))
        self.assertEqual(result.final_position, Position.LONG)
        self.assertEqual(result.metrics.closed_trades, 0)
        self.assertEqual(result.trades.iloc[0]["quantity"], 10)
        self.assertEqual(result.metrics.total_fees, 0)

    def test_entry_and_risk_exit_share_one_bar_volume_budget(self):
        frame = candles(opens=[100, 100], lows=[99, 90], volume=[2, 10000])
        result = simulate(frame, RiskConfig(stop_loss_enabled=True))
        self.assertEqual(result.trades.iloc[0]["quantity"], .2)
        self.assertEqual(result.events.iloc[-1]["status"], "REJECTED")
        self.assertEqual(result.events.iloc[-1]["liquidity_available"], 0)

    def test_risk_exit_uses_previous_volume_not_current_or_future_volume(self):
        frame = candles(lows=[99, 99, 90], volume=[10000, 10000, 0])
        result = simulate(frame, RiskConfig(stop_loss_enabled=True))
        self.assertEqual(result.metrics.closed_trades, 1)
        changed = frame.copy()
        changed.loc[2, "volume"] = 1e9
        assert_frame_equal(result.events, simulate(changed, result.risk).events)

    def test_adverse_slippage_and_both_fees_on_risk_exit(self):
        settings = BacktestConfig(1000, .01, .01, .1)
        for direction, entry_fill, exit_multiplier in (("L", 101, .99), ("S", 99, 1.01)):
            frame = candles(direction, highs=[101, 110], lows=[99, 90])
            result = simulate(frame, RiskConfig(stop_loss_enabled=True), settings)
            trade = result.trades.iloc[0]
            quantity = 1000/(max(100, entry_fill)+.01*entry_fill)
            raw_exit = entry_fill*(.97 if direction == "L" else 1.03)
            fill = raw_exit*exit_multiplier
            gross = (1 if direction == "L" else -1)*quantity*(fill-entry_fill)
            fees = quantity*(entry_fill+fill)*.01
            self.assertAlmostEqual(trade.exit_price, fill)
            self.assertAlmostEqual(trade.net_pnl, gross-fees)
            self.assertAlmostEqual(result.metrics.final_equity, 1000+gross-fees)
            self.assertAlmostEqual(result.metrics.total_fees, fees)

    def test_accounting_original_immutability_reproducibility(self):
        risk = RiskConfig(stop_loss_enabled=True, take_profit_enabled=True, trailing_stop_enabled=True)
        for direction in ("L", "S"):
            frame = candles(direction, highs=[101, 110, 101], lows=[99, 90, 99])
            original = frame.copy(deep=True)
            result = simulate(frame, risk, BacktestConfig(1000, .001, .0005, .1))
            curve = result.equity_curve
            np.testing.assert_allclose(curve.cash+curve.position_value, curve.equity)
            np.testing.assert_allclose(1000+curve.realized_pnl+curve.unrealized_pnl, curve.equity)
            self.assertTrue((curve[["cash", "equity", "free_cash"]] >= 0).all().all())
            assert_frame_equal(frame, original)
            assert_frame_equal(result.events, simulate(frame, risk, result.settings).events)

    def test_future_extremes_cannot_change_past_equity_or_risk_events(self):
        frame = candles(opens=[100]*5, highs=[101, 102, 103, 104, 105], lows=[99]*5)
        risk = RiskConfig(trailing_stop_enabled=True, trailing_stop_rate=.1)
        complete = simulate(frame, risk)
        for end in (2, 3, 4):
            prefix = simulate(frame.iloc[:end], risk)
            assert_frame_equal(prefix.equity_curve, complete.equity_curve.iloc[:end+1])
            actual = prefix.events.loc[prefix.events.status == "FILLED"].reset_index(drop=True)
            expected = complete.events.loc[(complete.events.status == "FILLED") &
                                           (complete.events.execution_bar < end)].reset_index(drop=True)
            assert_frame_equal(actual, expected, check_dtype=False)
        altered = frame.copy()
        altered.loc[4, ["high", "low"]] = [1000, 1]
        changed = simulate(altered, risk)
        assert_frame_equal(complete.equity_curve.iloc[:5], changed.equity_curve.iloc[:5])

    def test_virtual_short_insolvent_gap_still_raises(self):
        with self.assertRaisesRegex(BacktestError, "음수"):
            simulate(candles("S", opens=[100, 100, 300]), RiskConfig(stop_loss_enabled=True))

    def test_validation_rejects_invalid_flags_and_rates(self):
        for kwargs in ({"stop_loss_enabled": 1}, {"stop_loss_rate": 0}, {"take_profit_rate": 1},
                       {"trailing_stop_rate": np.nan}, {"stop_loss_rate": True},
                       {"take_profit_rate": "0.1"}, {"stop_loss_rate": float("inf")}):
            with self.subTest(kwargs=kwargs), self.assertRaises(BacktestError):
                RiskConfig(**kwargs)
        with self.assertRaises(BacktestError):
            simulate(candles(), {"stop_loss_enabled": True})

    def test_levels_long_short_and_disabled_rates_have_no_effect(self):
        config = RiskConfig(True, .03, True, .06, True, .05)
        self.assertEqual(risk_levels(Position.LONG, 100, 110, config),
                         {"STOP_LOSS": 97, "TAKE_PROFIT": 106, "TRAILING_STOP": 104.5})
        self.assertEqual(risk_levels(Position.SHORT, 100, 90, config),
                         {"STOP_LOSS": 103, "TAKE_PROFIT": 94, "TRAILING_STOP": 94.5})
        first = simulate(candles(lows=[99, 1, 99]), RiskConfig())
        second = simulate(candles(lows=[99, 1, 99]), RiskConfig(stop_loss_rate=.9, take_profit_rate=.8))
        assert_frame_equal(first.events, second.events)
        assert_frame_equal(first.equity_curve, second.equity_curve)
        self.assertEqual(RiskConfig().effective_settings(), second.risk.effective_settings())

    def test_comparison_matches_independent_runs_and_can_worsen_return(self):
        frame = candles(opens=[100, 100, 100, 120], lows=[99, 99, 90, 119])
        risk = RiskConfig(stop_loss_enabled=True)
        comparison = compare_risk(frame, strategy(), CONFIG, risk=risk, as_of=AS_OF)
        self.assertEqual(comparison.risk_off.metrics.final_equity, 1200)
        self.assertEqual(comparison.risk_on.metrics.final_equity, 970)
        assert_frame_equal(comparison.risk_off.equity_curve, simulate(frame, RiskConfig()).equity_curve)
        assert_frame_equal(comparison.risk_on.equity_curve, simulate(frame, risk).equity_curve)

    def test_comparison_all_disabled_is_identical(self):
        comparison = compare_risk(candles(), strategy(), CONFIG, risk=RiskConfig(), as_of=AS_OF)
        assert_frame_equal(comparison.risk_off.equity_curve, comparison.risk_on.equity_curve)
        assert_frame_equal(comparison.risk_off.events, comparison.risk_on.events)
        self.assertEqual(comparison.risk_off.metrics, comparison.risk_on.metrics)

    def test_disabled_rates_do_not_change_result_fingerprint(self):
        frame = candles()
        args = (frame, strategy(), CONFIG, "BTC/USDT", "1h", "1개월")
        self.assertEqual(result_fingerprint(*args, RiskConfig()),
                         result_fingerprint(*args, RiskConfig(stop_loss_rate=.9, take_profit_rate=.8)))
        self.assertNotEqual(result_fingerprint(*args, RiskConfig()),
                            result_fingerprint(*args, RiskConfig(stop_loss_enabled=True)))

    def test_risk_off_matches_ten_frozen_step5_results(self):
        path = Path(__file__).parent / "fixtures" / "step5_risk_off.json"
        for case in json.loads(path.read_text(encoding="utf-8"))["cases"]:
            with self.subTest(case=case["name"]):
                frame = pd.read_json(StringIO(json.dumps(case["input"])), orient="table")
                frame.attrs = {"timeframe": "1h", "symbol": "BTC/USDT"}
                config = BacktestConfig(1000, case["kwargs"].get("fee", 0), case["kwargs"].get("slip", 0), .1)
                result = simulate(frame, RiskConfig(), config)
                for name in ("equity_curve", "trades", "events"):
                    # 확장 컬럼 외 STEP 5의 모든 기존 컬럼/수치/행/시각을 비교한다.
                    frozen = case[name]
                    actual = json.loads(getattr(result, name)[frozen["columns"]].to_json(
                        orient="split", date_format="iso", double_precision=15))
                    self.assertEqual(actual, frozen)
                self.assertEqual(asdict(result.metrics), case["metrics"])
                self.assertEqual(result.final_position.value, case["final_position"])
