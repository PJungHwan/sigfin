"""STEP 18: 수작업 검산·독립 구간·원본 고정·재현성 (외부 연결 없음)."""

from dataclasses import asdict
import json
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from backtest.engine import run_backtest
from backtest.experiments import (
    ExperimentError, capture_experiment, comparison_table, execute_experiment,
    experiment_from_json, experiment_json, experiment_markdown, result_digest,
    risk_delta_table,
)
from backtest.models import BacktestConfig, BacktestError, RiskConfig
from indicators.technical import add_technical_indicators
from services.research_snapshots import restore_frame
from strategies.models import (
    Condition, ConditionGroup, Direction, Indicator, Logic, Operand, OperandKind, Operator, Strategy,
)

AS_OF = pd.Timestamp("2026-02-01T00:00:00Z")


def frame(prices=(100, 100, 120, 150), volume=100000.0):
    prices = np.array(prices, dtype=float)
    data = pd.DataFrame(dict(timestamp=pd.date_range("2026-01-01", periods=len(prices), freq="h", tz="UTC"),
                             open=prices, high=prices + 1, low=prices - 1, close=prices,
                             volume=volume))
    data.attrs = dict(symbol="BTC/USDT", timeframe="1h", fetched_at=AS_OF)
    return data


def strategy(name="A", long_value=110, short_value=110, indicator=Indicator.CLOSE,
             long_operator=Operator.LT):
    return Strategy(name, ConditionGroup(Direction.LONG, Logic.AND, (
        Condition("long", indicator, long_operator, Operand(OperandKind.NUMBER, long_value)),)),
        ConditionGroup(Direction.SHORT, Logic.AND, (
            Condition("short", indicator, Operator.GT, Operand(OperandKind.NUMBER, short_value)),)))


def capture(data=None, *, strategies=None, settings=None, risk=None, split_at=None, **kwargs):
    return capture_experiment(frame() if data is None else data,
                              [strategy(), strategy("B", 50, 1000)] if strategies is None else strategies,
                              settings or BacktestConfig(1000, 0, 0), risk or RiskConfig(),
                              symbol="BTC/USDT", timeframe="1h", period="시험", split_at=split_at,
                              as_of=AS_OF, **kwargs)


def stop_loss_fixture():
    """검토/검증마다 100 시가 진입 후 같은 봉 저가가 99 손절을 통과하는 합성 자료."""
    raw = frame([100, 102, 103, 104, 100, 102, 103, 104])
    raw.loc[[1, 5], "open"] = 100.
    raw.loc[[1, 5], "low"] = 98.5
    strategies = [strategy("손절 검산 A", 101, 1000), strategy("손절 검산 B", 100.5, 1000)]
    return raw, strategies


def test_stop_loss_one_percent_differs_in_all_segments_and_roundtrips_with_hash():
    from backtest.experiment_verification import (
        experiment_artifact_json, read_experiment_artifact, verify_result,
    )
    raw, strategies = stop_loss_fixture()
    saved = capture(raw, strategies=strategies, split_at=4,
                    risk=RiskConfig(stop_loss_enabled=True, stop_loss_rate=.01))
    result = execute_experiment(saved)
    assert result.status == "COMPLETED" and len(result.runs) == 12
    for off, on in zip(result.runs[::2], result.runs[1::2]):
        assert off.risk_mode == "OFF" and not off.result.risk.enabled
        assert on.risk_mode == "ON" and on.result.risk.stop_loss_rate == .01
        assert on.result.risk.stop_loss_enabled
        assert off.result.metrics.final_equity == pytest.approx(1040.)
        expected = 980.1 if on.segment == "전체" else 990.
        assert on.result.metrics.final_equity == pytest.approx(expected)
        assert (on.result.trades.exit_reason == "STOP_LOSS").all()
        assert off.result.trades.exit_reason.isna().all()
        fills = on.result.events.loc[on.result.events.status == "FILLED"]
        entries = fills.loc[fills.action == "ENTRY"]
        exits = fills.loc[fills.action == "EXIT"]
        assert (entries.execution_bar == entries.signal_bar + 1).all()
        assert entries.execution_timing.eq("NEXT_OPEN").all()
        assert exits.exit_reason.eq("STOP_LOSS").all()
        assert exits.price.eq(99.).all()
        assert exits.execution_timing.eq("INTRABAR_OHLC").all()
        assert exits.execution_timestamp.isna().all()  # 봉 내부의 정확한 시각은 만들지 않는다.
        curve = on.result.equity_curve
        assert np.allclose(curve.equity, 1000 + curve.realized_pnl + curve.unrealized_pnl)
    delta = risk_delta_table(result)
    assert delta.loc[delta.segment == "전체", "final_equity"].tolist() == pytest.approx([-59.9, -59.9])
    assert delta.loc[delta.segment == "검증", "final_equity"].tolist() == pytest.approx([-50., -50.])
    replay_input, reference = read_experiment_artifact(experiment_artifact_json(result))
    replay = execute_experiment(replay_input)
    assert verify_result(replay, reference).status == "MATCH"
    assert replay.snapshot.risk["stop_loss_enabled"] is True
    assert replay.snapshot.risk["stop_loss_rate"] == .01
    assert_frame_equal(comparison_table(result), comparison_table(replay))


def test_stop_loss_one_percent_uses_costs_and_future_validation_cannot_change_review():
    raw, strategies = stop_loss_fixture()
    config = BacktestConfig(1000, .001, .0005)
    risk = RiskConfig(stop_loss_enabled=True, stop_loss_rate=.01)
    original = execute_experiment(capture(raw, strategies=strategies, settings=config, risk=risk, split_at=4))
    review_on = next(run.result for run in original.runs if run.segment == "검토" and run.risk_mode == "ON")
    entry = 100 * (1 + config.slippage_rate)
    quantity = 1000 / (entry * (1 + config.fee_rate))
    stop_fill = entry * .99 * (1 - config.slippage_rate)
    fees = quantity * (entry + stop_fill) * config.fee_rate
    expected = quantity * stop_fill * (1 - config.fee_rate)
    assert review_on.metrics.final_equity == pytest.approx(expected)
    assert review_on.metrics.total_fees == pytest.approx(fees)
    assert review_on.trades.iloc[0].exit_price == pytest.approx(stop_fill)
    assert review_on.trades.iloc[0].net_pnl == pytest.approx(expected - 1000)
    changed = raw.copy(deep=True)
    changed.loc[4:, ["open", "high", "low", "close"]] *= 1.5
    changed.loc[4:, "volume"] *= 2
    future_changed = execute_experiment(capture(changed, strategies=strategies, settings=config, risk=risk, split_at=4))
    for before, after in zip(original.runs[4:8], future_changed.runs[4:8]):
        assert_frame_equal(before.result.events, after.result.events)
        assert_frame_equal(before.result.equity_curve, after.result.equity_curve)


def test_four_combinations_match_independent_engine_and_risk_delta():
    saved = capture(risk=RiskConfig(take_profit_enabled=True, take_profit_rate=.2))
    result = execute_experiment(saved)
    assert result.status == "COMPLETED" and len(result.runs) == 4
    calculated = add_technical_indicators(frame(), timeframe="1h")
    for run in result.runs:
        s = strategy() if run.strategy_name == "A" else strategy("B", 50, 1000)
        expected = run_backtest(calculated, s, BacktestConfig(1000, 0, 0), as_of=AS_OF,
                                risk=RiskConfig(**saved.risk) if run.risk_mode == "ON" else RiskConfig())
        assert asdict(expected.metrics) == asdict(run.result.metrics)
        assert_frame_equal(expected.events, run.result.events)
    assert result.runs[0].result.metrics.final_equity == 1500
    assert result.runs[1].result.metrics.final_equity == 1200
    delta = risk_delta_table(result).iloc[0]
    assert delta.final_equity == -300
    assert delta.cumulative_return_pct == pytest.approx(-30)


def test_manual_costs_mdd_winrate_and_accounting():
    result = execute_experiment(capture(settings=BacktestConfig(1000, .01, .01)))
    run = result.runs[0].result
    q = 1000 / (101 * 1.01)
    exit_price = 150 * .99
    entry_fee, exit_fee = q * 101 * .01, q * exit_price * .01
    pnl = q * (exit_price - 101) - entry_fee - exit_fee
    assert run.metrics.final_equity == pytest.approx(1000 + pnl)
    assert run.metrics.total_fees == pytest.approx(entry_fee + exit_fee)
    assert run.metrics.win_rate_pct == 100
    assert run.metrics.average_trade_pnl == pytest.approx(pnl)
    assert run.metrics.max_drawdown_pct == pytest.approx((1000 - q * 100) / 1000 * 100)
    curve = run.equity_curve
    assert np.allclose(curve.equity, 1000 + curve.realized_pnl + curve.unrealized_pnl)
    fills = run.events.loc[run.events.status == "FILLED"]
    assert fills.execution_bar.tolist() == [1, 3]
    assert (fills.execution_bar == fills.signal_bar + 1).all()


def test_risk_disabled_is_identical_and_no_trade_na_is_not_zero_winrate():
    result = execute_experiment(capture())
    assert_frame_equal(result.runs[0].result.equity_curve, result.runs[1].result.equity_curve)
    row = comparison_table(result).iloc[2]
    assert row.closed_trades == 0 and row.cumulative_return_pct == 0
    assert pd.isna(row.win_rate_pct) and pd.isna(row.average_trade_pnl)
    assert any("비활성화" in text for text in result.warnings)


def test_json_roundtrip_and_repeat_execution_and_order_independence():
    saved = capture(split_at=2)
    restored = experiment_from_json(experiment_json(saved))
    first, second = execute_experiment(saved), execute_experiment(restored)
    assert result_digest(first) == result_digest(second)
    assert_frame_equal(comparison_table(first), comparison_table(second))
    reverse = capture(strategies=[strategy("B", 50, 1000), strategy()], split_at=2)
    assert result_digest(first) == result_digest(execute_experiment(reverse))
    assert restored.input_digest == saved.input_digest
    assert "Risk ON − OFF" in experiment_markdown(first)


def test_snapshot_does_not_change_with_mutation_of_original():
    raw = frame()
    before = raw.copy(deep=True)
    saved = capture(raw)
    execute_experiment(saved)
    assert_frame_equal(raw, before)
    raw.loc[0, "close"] = 999
    assert restore_frame(saved.market).iloc[0].close == 100


def test_review_validation_start_flat_and_do_not_carry_orders_or_positions():
    result = execute_experiment(capture(split_at=2))
    assert len(result.runs) == 12
    review, validation = result.runs[4].result, result.runs[8].result
    assert review.final_position.value == "LONG"
    assert validation.equity_curve.iloc[0].position == "FLAT"
    assert validation.equity_curve.iloc[0].equity == 1000
    assert validation.trades.iloc[0].entry_timestamp == frame().iloc[3].timestamp
    assert validation.trades.iloc[0].direction == "SHORT"
    assert validation.trades.iloc[0].entry_signal_timestamp == frame().iloc[2].timestamp
    for run in result.runs:
        events = run.result.events
        fills = events.loc[(events.status == "FILLED") & (events.execution_timing == "NEXT_OPEN")]
        assert (fills.execution_bar == fills.signal_bar + 1).all()
        assert (fills.signal_bar >= 0).all()


def test_future_prices_cannot_change_review_indicators_or_fills():
    values = np.linspace(100, 160, 80)
    original = frame(values)
    changed = original.copy(deep=True)
    changed.loc[60:, ["open", "high", "low", "close"]] *= 1.8
    strategies = [strategy(indicator=Indicator.EMA20), strategy("B", 50, 1000)]
    first = execute_experiment(capture(original, strategies=strategies, split_at=60))
    second = execute_experiment(capture(changed, strategies=strategies, split_at=60))
    for a, b in zip(first.runs[4:8], second.runs[4:8]):
        assert_frame_equal(a.result.equity_curve, b.result.equity_curve)
        assert_frame_equal(a.result.events, b.result.events)


def test_uses_prior_warmup_but_no_cross_signal_from_before_validation():
    prices = [100.] * 60 + [120., 100., 120., 100.]
    s = strategy("cross", 110, 1000, long_operator=Operator.CROSS_UP)
    result = execute_experiment(capture(frame(prices), strategies=[s, strategy("B", 50, 1000)], split_at=60))
    validation = result.runs[8].result
    assert validation.signals.long.tolist() == [False, False, True, False]
    assert validation.trades.iloc[0].entry_timestamp == frame(prices).iloc[63].timestamp
    assert result.segments.iloc[2].prior_warmup_candles == 60


def test_recomputes_untrusted_indicator_columns_and_keeps_nan():
    raw = frame()
    raw["rsi_14"] = 10.0
    result = execute_experiment(capture(raw, strategies=[strategy(indicator=Indicator.RSI, short_value=90, long_value=30), strategy("B", 50, 1000)]))
    assert result.runs[0].result.trades.empty
    assert not result.runs[0].result.signals.validity.any().any()
    assert comparison_table(result).iloc[0].valid_condition_bars == 0


def test_volume_cap_last_signal_and_gap_handling():
    raw = frame(volume=1.)
    raw.loc[1:, "volume"] = 100000
    result = execute_experiment(capture(raw))
    assert result.runs[0].result.trades.iloc[0].quantity == .1
    only_last = frame([100, 100, 100, 90])
    last = execute_experiment(capture(only_last, strategies=[strategy(long_value=95, short_value=1000), strategy("B", 50, 1000)]))
    assert last.runs[0].result.trades.empty
    assert last.runs[0].result.events.iloc[-1].reason == "NO_NEXT_CANDLE"
    gap = frame().drop(index=1).reset_index(drop=True)
    output = execute_experiment(capture(gap))
    assert any("시간 간격 누락" in text for text in output.warnings)


@pytest.mark.parametrize("case", ["empty", "one", "missing", "duplicate", "unsorted", "naive", "future", "nan", "bad_ohlc", "mismatch", "unaligned", "collected_before_close"])
def test_bad_market_data_is_rejected(case):
    raw = frame()
    if case == "empty": raw = raw.iloc[:0]
    if case == "one": raw = raw.iloc[:1]
    if case == "missing": raw = raw.drop(columns="high")
    if case == "duplicate": raw.loc[1, "timestamp"] = raw.loc[0, "timestamp"]
    if case == "unsorted": raw = raw.iloc[::-1]
    if case == "naive": raw["timestamp"] = raw.timestamp.dt.tz_localize(None)
    if case == "future": raw["timestamp"] += pd.Timedelta(days=10000)
    if case == "nan": raw.loc[0, "close"] = np.nan
    if case == "bad_ohlc": raw.loc[0, "high"] = 1
    if case == "mismatch": raw.attrs["symbol"] = "ETH/USDT"
    if case == "unaligned": raw["timestamp"] += pd.Timedelta(minutes=1)
    if case == "collected_before_close": raw.attrs["fetched_at"] = raw.iloc[0].timestamp
    with pytest.raises((ValueError, TypeError)):
        capture(raw)


@pytest.mark.parametrize("strategies,split_at", [([strategy()], None), ([strategy(), strategy()], None),
                                                ([strategy(), strategy("B")], 1), ([strategy(), strategy("B")], 3)])
def test_bad_experiment_configuration_is_rejected(strategies, split_at):
    with pytest.raises(ValueError):
        capture(strategies=strategies, split_at=split_at)


def test_tampered_or_wrong_version_json_rejected():
    payload = json.loads(experiment_json(capture()))
    for path, value in [("input_digest", "0" * 64), ("schema_version", 2), ("code_digest", "bad")]:
        altered = {**payload, path: value}
        with pytest.raises(ExperimentError):
            experiment_from_json(json.dumps(altered))
    for text in ['{"schema_version":1,"schema_version":1}', '{bad', '{"bad":NaN}']:
        with pytest.raises(ExperimentError): experiment_from_json(text)


def test_partial_failure_is_not_success_or_zero_performance():
    real = run_backtest
    def failing(data, strategy, *args, **kwargs):
        if strategy.name == "A": raise BacktestError("의도한 시험 오류")
        return real(data, strategy, *args, **kwargs)
    with patch("backtest.experiments.run_backtest", side_effect=failing):
        output = execute_experiment(capture())
    assert output.status == "PARTIAL"
    table = comparison_table(output)
    assert table.iloc[0].status == "FAILED" and pd.isna(table.iloc[0].final_equity)
    assert pd.isna(risk_delta_table(output).iloc[0].final_equity)


def test_total_failure_is_failed_not_no_trade_success():
    real = run_backtest
    def failing(data, strategy, *args, **kwargs):
        if strategy.name != "입력 검증":
            raise BacktestError("시험용 회계 한도 초과")
        return real(data, strategy, *args, **kwargs)
    with patch("backtest.experiments.run_backtest", side_effect=failing):
        result = execute_experiment(capture())
    assert result.status == "FAILED"
    assert comparison_table(result).final_equity.isna().all()


def test_same_conditions_different_names_are_disclosed():
    output = execute_experiment(capture(strategies=[strategy(), strategy("B")]))
    assert any("이름만 다르고" in text for text in output.warnings)


def test_no_api_or_database_calls_and_partial_source_metadata():
    raw = frame()
    raw.attrs.update(is_partial=True, limit_reasons=["요청 한도"])
    with patch("openai.OpenAI", side_effect=AssertionError("no paid API")), patch("sqlite3.connect", side_effect=AssertionError("no user DB")), patch("ccxt.binance", side_effect=AssertionError("no fetch")):
        result = execute_experiment(capture(raw))
    assert result.status == "COMPLETED"
    assert any("요청 한도" in text for text in result.warnings)
