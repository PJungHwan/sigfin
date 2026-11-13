"""종료 거래 기준 승률과 초기 자산을 포함한 시간순 Equity Curve 성과."""

from math import isfinite

import pandas as pd

from backtest.models import BacktestError, BacktestMetrics


def maximum_drawdown(equity: pd.Series) -> float | None:
    """MDD = -min(E_t / max(E_0..E_t) - 1) × 100, 양의 낙폭 크기를 반환한다."""
    if equity.empty:
        return None
    if equity.isna().any() or not equity.map(isfinite).all() or (equity < 0).any():
        raise BacktestError("MDD 계산에는 유한한 비음수 자산 곡선이 필요합니다.")
    peak = equity.cummax()
    if (peak <= 0).any():
        raise BacktestError("MDD 분모의 초기 자산은 0보다 커야 합니다.")
    return max(0.0, float(-(equity / peak - 1).min() * 100))


def calculate_metrics(
    curve: pd.DataFrame, trades: pd.DataFrame, events: pd.DataFrame,
    initial_capital: float,
) -> BacktestMetrics:
    if not isfinite(initial_capital) or initial_capital <= 0:
        raise BacktestError("성과 계산의 초기 투자금은 유한한 양수여야 합니다.")
    closed = trades.loc[trades["status"] == "CLOSED"]
    count = len(closed)
    last = curve.iloc[-1] if not curve.empty else None
    final = float(last["equity"]) if last is not None else initial_capital
    return BacktestMetrics(
        final_equity=final,
        cumulative_return_pct=(final / initial_capital - 1) * 100 if last is not None else None,
        win_rate_pct=float(closed["net_pnl"].gt(0).sum() / count * 100) if count else None,
        max_drawdown_pct=maximum_drawdown(curve["equity"]),
        closed_trades=count,
        average_trade_pnl=float(closed["net_pnl"].mean()) if count else None,
        total_fees=float(last["total_fees"]) if last is not None else 0.0,
        realized_pnl=float(last["realized_pnl"]) if last is not None else 0.0,
        unrealized_pnl=float(last["unrealized_pnl"]) if last is not None else 0.0,
        rejected_orders=int(events["status"].eq("REJECTED").sum()),
    )
