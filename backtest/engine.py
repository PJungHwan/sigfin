"""확정 신호 → 다음 연속 봉 시가 체결, 단일 포지션의 결정적 기초 백테스트.

수량은 진입 시 최대 1배 노출 및 비용 예산, 직전 봉 거래량 한도 중 작은 값이다.
SHORT는 매도 대금이 장부 현금에 들어가지만 원래 노출과 매도 대금을 잠가 재사용을
막는 가상 회계다. equity = cash + signed_quantity * close.
실현 손익 = 종료 거래 가격 손익 - 지금까지 지급한 모든 수수료(열린 거래 진입비 포함).
미실현 손익 = signed_quantity * (현재 종가 - 진입 체결가).
따라서 equity = initial_capital + realized_pnl + unrealized_pnl이다.
종료 시 강제 청산하지 않는다. 수량은 보유 중 고정하며 거래소 강제 청산/펀딩은 모사하지 않는다.
선택적 리스크 주문의 봉별 우선순위와 OHLC 가정은 docs/risk_management.md에 명시한다.
"""

from math import isfinite

import numpy as np
import pandas as pd

from backtest.metrics import calculate_metrics
from backtest.models import (BacktestConfig, BacktestError, BacktestResult, Position,
                             RiskConfig, RiskComparison)
from backtest.risk import RiskTrigger, check_risk, risk_levels, update_favorable_price
from config.settings import OHLCV_COLUMNS, TIMEFRAME_MILLISECONDS
from strategies.evaluator import evaluate_strategy
from strategies.models import Strategy
from strategies.storage import strategy_to_json

CURVE_COLUMNS = ["timestamp", "candle_timestamp", "position", "cash", "restricted_cash",
                 "free_cash", "quantity", "position_value", "equity", "realized_pnl",
                 "unrealized_pnl", "total_fees"]
EVENT_COLUMNS = ["signal_bar", "execution_bar", "signal_timestamp", "signal_confirmed_at",
                 "execution_timestamp", "action", "direction", "status", "reason",
                 "trade_id", "price", "quantity", "fee", "cash_after", "position_after",
                 "exit_reason", "execution_candle_timestamp", "execution_timing", "trigger_price",
                 "risk_candidates", "risk_trigger_bar", "trigger_candle_timestamp",
                 "requested_quantity", "liquidity_available"]
TRADE_COLUMNS = ["trade_id", "direction", "status", "entry_signal_timestamp", "entry_timestamp",
                 "entry_price", "quantity", "entry_fee", "exit_signal_timestamp", "exit_timestamp",
                 "exit_price", "exit_fee", "gross_pnl", "net_pnl", "unrealized_pnl", "fees_paid",
                 "exit_reason", "exit_candle_timestamp", "exit_timing"]


def _nonnegative(value: float, label: str, tolerance: float) -> float:
    if not isfinite(value) or value < -tolerance:
        raise BacktestError(f"{label}이 음수 또는 비유한 값이 되는 경로입니다. 가상 숏/비용 설정을 확인해 주세요.")
    return max(0.0, value)  # 허용 오차 범위의 부동소수점 음수만 0으로 정리한다.


def run_backtest(
    data: pd.DataFrame, strategy: Strategy, settings: BacktestConfig | None = None,
    *, timeframe: str | None = None, as_of: pd.Timestamp | None = None,
    risk: RiskConfig | None = None,
) -> BacktestResult:
    settings = BacktestConfig() if settings is None else settings
    if not isinstance(settings, BacktestConfig):
        raise BacktestError("검증된 BacktestConfig가 필요합니다.")
    risk = RiskConfig() if risk is None else risk
    if not isinstance(risk, RiskConfig):
        raise BacktestError("검증된 RiskConfig가 필요합니다.")
    if not set(OHLCV_COLUMNS).issubset(data.columns):
        raise BacktestError("백테스트에는 전체 OHLCV 컬럼이 필요합니다.")
    selected = timeframe or data.attrs.get("timeframe")
    if selected not in TIMEFRAME_MILLISECONDS:
        raise BacktestError("지원 Timeframe을 지정해야 합니다.")
    prices = data[OHLCV_COLUMNS[1:]].apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(prices.to_numpy(dtype=float)).all():
        raise BacktestError("OHLCV에 결측값 또는 비유한 값이 있습니다.")
    if (prices[["open", "high", "low", "close"]] <= 0).any().any() or (prices["volume"] < 0).any():
        raise BacktestError("가격은 양수이고 거래량은 비음수여야 합니다.")
    if ((prices["high"] < prices[["open", "low", "close"]].max(axis=1)).any()
            or (prices["low"] > prices[["open", "high", "close"]].min(axis=1)).any()):
        raise BacktestError("OHLC 가격 범위가 올바르지 않습니다.")
    signals = evaluate_strategy(data, strategy, timeframe=selected, as_of=as_of)
    times = data["timestamp"].dt.tz_convert("UTC").tolist()
    interval = pd.Timedelta(milliseconds=TIMEFRAME_MILLISECONDS[selected])
    cash = settings.initial_capital
    total_fees = realized_gross = 0.0
    position = Position.FLAT
    active = None
    favorable_price = None
    pending_risk = None
    restricted = 0.0
    trade_id = 0
    curve, events, trades = [], [], []
    tolerance = max(1e-8, float(np.spacing(settings.initial_capital)) * 32)

    def snapshot(timestamp, candle_timestamp, mark):
        sign = 1 if position is Position.LONG else -1
        quantity = sign * active["quantity"] if active else 0.0
        unrealized = quantity * (mark - active["entry_price"]) if active else 0.0
        equity = _nonnegative(cash + quantity * mark, "평가 자산", tolerance)
        free = _nonnegative(cash - restricted, "사용 가능 현금", tolerance)
        realized = realized_gross - total_fees
        if not np.isclose(equity, settings.initial_capital + realized + unrealized,
                          rtol=1e-9, atol=tolerance):
            raise BacktestError("현금·포지션·실현/미실현 손익 회계가 일치하지 않습니다.")
        curve.append({"timestamp": timestamp, "candle_timestamp": candle_timestamp,
                      "position": position.value, "cash": cash, "restricted_cash": restricted,
                      "free_cash": free, "quantity": quantity, "position_value": quantity * mark,
                      "equity": equity, "realized_pnl": realized, "unrealized_pnl": unrealized,
                      "total_fees": total_fees})

    def action_for(signal_bar):
        long, short = bool(signals.long.iloc[signal_bar]), bool(signals.short.iloc[signal_bar])
        if position is Position.FLAT:
            if long and short:
                return "CONFLICT", Position.FLAT
            if long or short:
                return "ENTRY", Position.LONG if long else Position.SHORT
        elif (position is Position.LONG and short) or (position is Position.SHORT and long):
            return "EXIT", position
        return None, position

    def event(signal_bar, execution_bar, action, direction, status, reason, **details):
        record = {"signal_bar": signal_bar, "execution_bar": execution_bar,
                  "signal_timestamp": times[signal_bar] if signal_bar is not None else pd.NaT,
                  "signal_confirmed_at": times[signal_bar] + interval if signal_bar is not None else pd.NaT,
                  "execution_timestamp": times[execution_bar] if execution_bar is not None else pd.NaT,
                  "action": action, "direction": direction.value, "status": status, "reason": reason,
                  "trade_id": active["trade_id"] if active else None,
                  "price": np.nan, "quantity": 0.0, "fee": 0.0,
                  "cash_after": cash, "position_after": position.value,
                  "exit_reason": "SIGNAL_EXIT" if action == "EXIT" else None,
                  "execution_candle_timestamp": times[execution_bar] if execution_bar is not None else pd.NaT,
                  "execution_timing": "NEXT_OPEN" if execution_bar is not None else None,
                  "trigger_price": np.nan, "risk_candidates": None, "risk_trigger_bar": None,
                  "trigger_candle_timestamp": pd.NaT, "requested_quantity": 0.0,
                  "liquidity_available": np.nan}
        record.update(details)
        events.append(record)

    def close_position(bar, raw_price, liquidity, *, signal_bar=None, trigger=None, trigger_bar=None):
        """신호/리스크가 같은 수량·비용·현금 원장을 공유한다. 부분 청산은 하지 않는다."""
        nonlocal cash, total_fees, realized_gross, position, restricted, active
        direction = position
        reason = (trigger.reason if trigger else "OPPOSITE_SIGNAL_WITH_CONFLICT"
                  if bool(signals.conflict.iloc[signal_bar]) else "OPPOSITE_SIGNAL")
        timing = trigger.timing if trigger else "NEXT_OPEN"
        execution_time = pd.NaT if timing == "INTRABAR_OHLC" else times[bar]
        details = dict(exit_reason=trigger.reason if trigger else "SIGNAL_EXIT",
                       execution_timestamp=execution_time, execution_timing=timing,
                       requested_quantity=active["quantity"], liquidity_available=liquidity)
        if trigger:
            details.update(trigger_price=trigger.trigger_price,
                           risk_candidates=" | ".join(trigger.candidates), risk_trigger_bar=trigger_bar,
                           trigger_candle_timestamp=times[trigger_bar])
        if active["quantity"] > liquidity + max(1e-12, float(np.spacing(active["quantity"])) * 16):
            event(signal_bar, bar, "EXIT", direction, "REJECTED",
                  "INSUFFICIENT_VOLUME_FOR_FULL_EXIT", **details)
            return False
        buying = direction is Position.SHORT
        fill = raw_price * (1 + settings.slippage_rate if buying else 1 - settings.slippage_rate)
        quantity = active["quantity"]
        fee = quantity * fill * settings.fee_rate
        new_cash = cash - quantity * fill - fee if buying else cash + quantity * fill - fee
        cash = _nonnegative(new_cash, "청산 후 현금", tolerance)
        sign = 1 if direction is Position.LONG else -1
        gross = sign * quantity * (fill - active["entry_price"])
        realized_gross += gross
        total_fees += fee
        active.update(status="CLOSED", exit_signal_timestamp=times[signal_bar] if signal_bar is not None else pd.NaT,
                      exit_timestamp=execution_time, exit_price=fill, exit_fee=fee,
                      gross_pnl=gross, net_pnl=gross-active["entry_fee"]-fee,
                      unrealized_pnl=0.0, fees_paid=active["entry_fee"]+fee,
                      exit_reason=details["exit_reason"], exit_candle_timestamp=times[bar], exit_timing=timing)
        trades.append(active.copy())
        closed_id = active["trade_id"]
        position = Position.FLAT
        restricted = 0.0
        active = None
        event(signal_bar, bar, "EXIT", direction, "FILLED", reason,
              price=fill, quantity=quantity, fee=fee, trade_id=closed_id, **details)
        return True

    if times:
        snapshot(times[0], pd.NaT, float(prices["open"].iloc[0]))
    for bar in range(len(data)):
        row = prices.iloc[bar]
        # 봉 전체의 진입+청산 합산 물량 한도. 체결 봉 거래량은 미리 읽지 않는다.
        liquidity = float(prices["volume"].iloc[bar-1]) * settings.max_volume_participation if bar else 0.0
        risk_attempted = False
        if active and risk.enabled:
            levels = risk_levels(position, active["entry_price"], favorable_price, risk)
            opening_trigger = (RiskTrigger(pending_risk[0].reason, float(row["open"]),
                                           pending_risk[0].trigger_price, "RETRY_OPEN", pending_risk[0].candidates)
                               if pending_risk else check_risk(position, levels, float(row["open"]),
                                                              float(row["high"]), float(row["low"]), opening_only=True))
            if opening_trigger:
                risk_attempted = True
                origin = pending_risk[1] if pending_risk else bar
                closed = close_position(bar, opening_trigger.raw_price, liquidity,
                                        trigger=opening_trigger, trigger_bar=origin)
                pending_risk = None if closed else (opening_trigger, origin)
        # 우선순위: 기존 미체결 리스크 → 시가 갭 리스크 → 신호 청산/진입 → 봉 내부 리스크.
        # 리스크 시도(미체결 포함)가 있으면 같은 시가의 신호 주문을 실행하지 않는다.
        if bar > 0 and not risk_attempted:
            signal_bar = bar - 1
            action, direction = action_for(signal_bar)
            if action is not None:
                if action == "CONFLICT":
                    event(signal_bar, bar, action, direction, "SKIPPED", "SIMULTANEOUS_SIGNALS")
                elif times[bar] != times[signal_bar] + interval:
                    event(signal_bar, bar, action, direction, "REJECTED", "NEXT_CANDLE_MISSING")
                else:
                    # 체결 봉 전체 거래량은 아직 알 수 없으므로 직전 확정 봉 거래량만 사용한다.
                    buying = (action == "ENTRY" and direction is Position.LONG) or (action == "EXIT" and direction is Position.SHORT)
                    raw_open = float(row["open"])
                    fill = raw_open * (1 + settings.slippage_rate if buying else 1 - settings.slippage_rate)
                    if action == "ENTRY":
                        # LONG: q*(fill+fee*fill) <= cash. SHORT: q*(open+fee*fill) <= cash.
                        budget_quantity = cash / (max(raw_open, fill) + settings.fee_rate * fill)
                        quantity = min(budget_quantity, liquidity)
                        if quantity <= 0 or not isfinite(quantity):
                            event(signal_bar, bar, action, direction, "REJECTED", "NO_CASH_OR_VOLUME")
                        else:
                            fee = quantity * fill * settings.fee_rate
                            new_cash = cash - quantity * fill - fee if buying else cash + quantity * fill - fee
                            cash = _nonnegative(new_cash, "장부 현금", tolerance)
                            restricted = 0.0 if buying else quantity * (raw_open + fill)
                            _nonnegative(cash - restricted, "사용 가능 현금", tolerance)
                            total_fees += fee
                            position = direction
                            trade_id += 1
                            active = {"trade_id": trade_id, "direction": direction.value, "status": "OPEN",
                                      "entry_signal_timestamp": times[signal_bar], "entry_timestamp": times[bar],
                                      "entry_price": fill, "quantity": quantity, "entry_fee": fee,
                                      "exit_signal_timestamp": pd.NaT, "exit_timestamp": pd.NaT,
                                      "exit_price": np.nan, "exit_fee": 0.0, "gross_pnl": np.nan,
                                      "net_pnl": np.nan, "unrealized_pnl": 0.0, "fees_paid": fee,
                                      "exit_reason": None, "exit_candle_timestamp": pd.NaT, "exit_timing": None}
                            favorable_price = fill
                            liquidity = max(0.0, liquidity-quantity)
                            event(signal_bar, bar, action, direction, "FILLED", "SIGNAL",
                                  price=fill, quantity=quantity, fee=fee)
                    else:
                        close_position(bar, raw_open, liquidity, signal_bar=signal_bar)
                        # 이 청산에 사용한 신호로 같은 시가에 반대 포지션을 열지 않는다.
        if active and risk.enabled:
            if not risk_attempted:
                levels = risk_levels(position, active["entry_price"], favorable_price, risk)
                trigger = check_risk(position, levels, float(row["open"]), float(row["high"]), float(row["low"]))
                if trigger:
                    closed = close_position(bar, trigger.raw_price, liquidity, trigger=trigger, trigger_bar=bar)
                    pending_risk = None if closed else (trigger, bar)
            if active:
                # 현재 봉 고저는 청산 판단을 끝낸 뒤에만 다음 봉 trailing 기준으로 갱신한다.
                favorable_price = update_favorable_price(position, favorable_price, float(row["high"]), float(row["low"]))
        snapshot(times[bar] + interval, times[bar], float(row["close"]))

    if times:
        action, direction = action_for(len(data)-1)
        if action is not None:
            event(len(data)-1, None, action, direction,
                  "SKIPPED" if action == "CONFLICT" else "REJECTED",
                  "SIMULTANEOUS_SIGNALS" if action == "CONFLICT" else "NO_NEXT_CANDLE")
    if active:
        sign = 1 if position is Position.LONG else -1
        active["unrealized_pnl"] = sign * active["quantity"] * (float(prices["close"].iloc[-1])-active["entry_price"])
        trades.append(active.copy())
    equity_curve = pd.DataFrame(curve, columns=CURVE_COLUMNS)
    trades_frame = pd.DataFrame(trades, columns=TRADE_COLUMNS)
    events_frame = pd.DataFrame(events, columns=EVENT_COLUMNS)
    metrics = calculate_metrics(equity_curve, trades_frame, events_frame, settings.initial_capital)
    return BacktestResult(equity_curve, trades_frame, events_frame, signals, metrics, settings,
                          strategy_to_json(strategy), selected, data.attrs.get("symbol"), position, risk)


def compare_risk(data: pd.DataFrame, strategy: Strategy, settings: BacktestConfig | None = None,
                 *, risk: RiskConfig, timeframe: str | None = None,
                 as_of: pd.Timestamp | None = None) -> RiskComparison:
    """동일 입력/비용으로 두 번 독립 실행. 어느 한쪽 실패 시 비교 결과를 만들지 않는다."""
    as_of = pd.Timestamp.now(tz="UTC") if as_of is None else as_of
    off = run_backtest(data, strategy, settings, timeframe=timeframe, as_of=as_of, risk=RiskConfig())
    on = run_backtest(data, strategy, settings, timeframe=timeframe, as_of=as_of, risk=risk)
    return RiskComparison(off, on)
