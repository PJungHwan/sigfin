"""가상 자산 곡선과 종료 거래의 비용 반영 손익 시각화."""

import plotly.graph_objects as go
from ui.design import style_figure

from backtest.models import BacktestResult, RiskComparison


def equity_chart(result: BacktestResult) -> go.Figure:
    curve = result.equity_curve
    figure = go.Figure(go.Scatter(
        x=curve["timestamp"], y=curve["equity"], mode="lines", name="평가 자산",
        line={"color": "#2563eb", "width": 2},
        hovertemplate="%{x|%Y-%m-%d %H:%M} UTC<br>자산: %{y:,.2f} USDT<extra></extra>",
    ))
    figure.update_layout(template="plotly_white", height=340, margin={"l": 15, "r": 15, "t": 20, "b": 30},
                         xaxis_title="봉 마감 (UTC · 첫 점은 초기 자산)", yaxis_title="자산 (USDT)",
                         paper_bgcolor="#ffffff", plot_bgcolor="#ffffff", showlegend=False)
    return style_figure(figure)


def trade_pnl_chart(result: BacktestResult) -> go.Figure:
    closed = result.trades.loc[result.trades["status"] == "CLOSED"]
    figure = go.Figure(go.Bar(
        x=closed["trade_id"].astype(str), y=closed["net_pnl"], name="거래 순손익",
        marker_color=["#16a34a" if value > 0 else "#dc2626" if value < 0 else "#94a3b8"
                      for value in closed["net_pnl"]],
        hovertemplate="거래 %{x}<br>순손익: %{y:,.2f} USDT<extra></extra>",
    ))
    figure.add_hline(y=0, line_color="#94a3b8", line_width=1)
    figure.update_layout(template="plotly_white", height=300, margin={"l": 15, "r": 15, "t": 20, "b": 30},
                         xaxis_title="종료 거래 ID", yaxis_title="순손익 (USDT)",
                         paper_bgcolor="#ffffff", plot_bgcolor="#ffffff", showlegend=False)
    return style_figure(figure)


def comparison_equity_chart(comparison: RiskComparison) -> go.Figure:
    figure = go.Figure()
    for name, result, color in (("Risk OFF", comparison.risk_off, "#64748b"),
                                 ("Risk ON", comparison.risk_on, "#2563eb")):
        figure.add_trace(go.Scatter(
            x=result.equity_curve["timestamp"], y=result.equity_curve["equity"],
            mode="lines", name=name, line={"color": color, "width": 2},
            hovertemplate=name + "<br>%{x|%Y-%m-%d %H:%M} UTC<br>%{y:,.2f} USDT<extra></extra>",
        ))
    figure.update_layout(template="plotly_white", height=350,
                         margin={"l": 15, "r": 15, "t": 20, "b": 30},
                         xaxis_title="봉 마감 (UTC · 첫 점은 초기 자산)", yaxis_title="자산 (USDT)",
                         paper_bgcolor="#ffffff", plot_bgcolor="#ffffff", legend={"orientation": "h"})
    return style_figure(figure)
