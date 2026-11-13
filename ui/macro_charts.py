"""FRED 시리즈를 원래 관측 주기/단위로 각각 시각화한다."""

import plotly.graph_objects as go
from ui.design import style_figure

from models.macro import FredError, get_series


def macro_series_chart(data, series_id: str, *, cpi_yoy: bool = False) -> go.Figure:
    spec = get_series(series_id)
    if cpi_yoy and series_id != "CPIAUCSL":
        raise FredError("전년동월비 표시는 CPI에만 적용합니다.", category="VALIDATION")
    column = "yoy_pct" if cpi_yoy else "value"
    unit = "% (전년동월 대비)" if cpi_yoy else spec.unit_label
    label = spec.name + (" · 전년동월비" if cpi_yoy else " · 원본 수준")
    figure = go.Figure(go.Scatter(
        x=data.observation_date, y=data[column], mode="lines+markers" if spec.frequency == "M" else "lines",
        connectgaps=False, name=label, line={"color": "#2563eb", "width": 2}, marker={"size": 4},
        hovertemplate="관측일: %{x|%Y-%m-%d}<br>%{y:,.3f} " + unit + "<extra></extra>",
    ))
    figure.update_layout(template="plotly_white", height=310,
                         margin={"l": 15, "r": 15, "t": 30, "b": 30}, title={"text": label, "font": {"size": 15}},
                         xaxis_title="관측일 (발표일 아님)", yaxis_title=unit,
                         paper_bgcolor="#ffffff", plot_bgcolor="#ffffff", showlegend=False,
                         uirevision=f"{series_id}:{column}")
    return style_figure(figure)
