"""단위가 다른 원본을 같은 가격 축에 섞지 않는 통합 분석 차트."""

import plotly.graph_objects as go
from ui.design import style_figure

from analytics.models import TRANSFORMS


def _style(figure, title, x_title, y_title):
    figure.update_layout(
        title=title, template="plotly_white", paper_bgcolor="white", plot_bgcolor="white",
        height=360, margin=dict(l=25, r=25, t=55, b=35),
        font=dict(color="#334155"), legend=dict(orientation="h", y=1.15),
        xaxis_title=x_title, yaxis_title=y_title,
    )
    return style_figure(figure)


def normalized_trend_chart(result):
    data = result.normalized
    figure = go.Figure()
    for column, label, color in (("crypto_index", result.symbol, "#2563eb"),
                                  ("macro_index", result.config.series_id, "#9333ea")):
        if not data.empty and data[column].notna().any():
            figure.add_trace(go.Scatter(x=data.index.astype(str).tolist(), y=data[column].tolist(),
                                       name=label, mode="lines+markers", connectgaps=False,
                                       line=dict(color=color, width=2)))
    return _style(figure, f"수준 추이 · 기준월 {data.attrs.get('base_month', 'N/A')} = 100",
                  "관측 월 · 사후 비교", "기준화 지수 (원본 단위 아님)")


def correlation_scatter_chart(result):
    data = result.lag_pairs
    data = data.loc[(data.lag_months == 0) & data.used]
    label, unit = TRANSFORMS[result.config.series_id][result.config.transform]
    figure = go.Figure(go.Scatter(
        x=data.macro_change.tolist(), y=data.crypto_return_pct.tolist(), mode="markers",
        text=data.macro_month.tolist(), marker=dict(color="#2563eb", size=9, opacity=.8),
        hovertemplate="관측 월 %{text}<br>거시 변화 %{x:.4f}<br>수익률 %{y:.3f}%<extra></extra>",
        name="공통 유효 월",
    ))
    return _style(figure, "같은 월 변화와 수익률 · lag 0", f"{label} ({unit})",
                  f"{result.symbol} 월간 수익률 (%)")


def lag_correlation_chart(result):
    data = result.lags
    figure = go.Figure()
    custom = data[["n_used", "n_before", "status"]].values.tolist()
    for column, label, color in (("pearson", "Pearson", "#2563eb"), ("spearman", "Spearman", "#f59e0b")):
        figure.add_trace(go.Scatter(
            x=data.lag_months.tolist(), y=data[column].tolist(), name=label, mode="lines+markers",
            customdata=custom, connectgaps=False, line=dict(color=color, width=2),
            hovertemplate="lag %{x}개월<br>상관계수 %{y:.3f}<br>유효 월 %{customdata[0]} / 후보 %{customdata[1]}"
                          "<br>%{customdata[2]}<extra>%{fullData.name}</extra>",
        ))
    _style(figure, "탐색적 시차 · 거시 t월 → 가격 수익률 t+lag월", "시차 (개월)", "상관계수")
    figure.update_xaxes(tickmode="array", tickvals=[0, 1, 2, 3])
    figure.update_yaxes(range=[-1.05, 1.05])
    return style_figure(figure)
