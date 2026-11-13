"""OHLCV와 이미 계산된 지표를 표시한다. 차트 모듈에서 지표를 계산하지 않는다."""

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from config.settings import OHLCV_COLUMNS
from strategies.evaluator import EvaluationResult

UP_COLOR = "#16a34a"
DOWN_COLOR = "#dc2626"


def create_candlestick_chart(
    data: pd.DataFrame, symbol: str, timeframe: str,
    *, show_rsi: bool = False, show_macd: bool = False, show_ema: bool = False,
    show_sma: bool = False, show_bollinger: bool = False, show_volume_sma: bool = False,
    signals: EvaluationResult | None = None,
    executions: pd.DataFrame | None = None,
) -> go.Figure:
    """캔들/거래량은 항상 유지하며 선택된 지표만 공유 날짜축에 표시한다."""
    if data.empty or not set(OHLCV_COLUMNS).issubset(data.columns):
        raise ValueError("차트 생성에 필요한 OHLCV 데이터가 없습니다.")

    selected = {
        "rsi": show_rsi, "macd": show_macd, "ema": show_ema,
        "sma": show_sma, "bollinger": show_bollinger, "volume_sma": show_volume_sma,
    }
    required = {
        "rsi": ["rsi_14"], "macd": ["macd", "macd_signal", "macd_histogram"],
        "ema": ["ema_20", "ema_60"], "sma": ["sma_20", "sma_60"],
        "bollinger": ["bb_upper", "bb_middle", "bb_lower"],
        "volume_sma": ["volume_sma_20"],
    }
    for indicator, enabled in selected.items():
        if enabled and not set(required[indicator]).issubset(data.columns):
            raise ValueError(f"선택한 {indicator} 지표를 먼저 계산해야 합니다.")
    panel_count = int(show_rsi) + int(show_macd)
    rows = 2 + panel_count
    row_heights = {0: [0.77, 0.23], 1: [0.60, 0.18, 0.22], 2: [0.48, 0.16, 0.17, 0.19]}
    figure = make_subplots(
        rows=rows,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.035,
        row_heights=row_heights[panel_count],
    )
    hovertext = [
        f"{row.timestamp:%Y-%m-%d %H:%M} UTC<br>"
        f"시가: {row.open:,.2f} USDT<br>고가: {row.high:,.2f} USDT<br>"
        f"저가: {row.low:,.2f} USDT<br>종가: {row.close:,.2f} USDT"
        for row in data.itertuples(index=False)
    ]
    figure.add_trace(
        go.Candlestick(
            x=data["timestamp"],
            open=data["open"],
            high=data["high"],
            low=data["low"],
            close=data["close"],
            increasing_line_color=UP_COLOR,
            increasing_fillcolor=UP_COLOR,
            decreasing_line_color=DOWN_COLOR,
            decreasing_fillcolor=DOWN_COLOR,
            hovertext=hovertext,
            hoverinfo="text",
            name=symbol,
            showlegend=False,
        ),
        row=1,
        col=1,
    )
    base_asset = symbol.split("/")[0]
    figure.add_trace(
        go.Bar(
            x=data["timestamp"],
            y=data["volume"],
            marker_color=[
                UP_COLOR if close >= open_ else DOWN_COLOR
                for open_, close in zip(data["open"], data["close"])
            ],
            opacity=0.65,
            name=f"거래량 ({base_asset})",
            showlegend=False,
            hovertemplate=(
                "%{x|%Y-%m-%d %H:%M} UTC<br>거래량: %{y:,.4f} "
                + base_asset
                + "<extra></extra>"
            ),
        ),
        row=2,
        col=1,
    )

    def add_line(column, name, color, row=1, width=1.5, dash="solid", **kwargs):
        figure.add_trace(
            go.Scatter(
                x=data["timestamp"], y=data[column], mode="lines", name=name,
                line={"color": color, "width": width, "dash": dash},
                connectgaps=False,
                hovertemplate=f"{name}: %{{y:,.4f}}<extra></extra>",
                **kwargs,
            ), row=row, col=1,
        )

    if show_ema:
        add_line("ema_20", "EMA 20", "#2563eb", width=1.8)
        add_line("ema_60", "EMA 60", "#7c3aed", width=1.8)
    if show_sma:
        add_line("sma_20", "SMA 20", "#d97706", dash="dash")
        add_line("sma_60", "SMA 60", "#0f766e", dash="dash")
    if show_bollinger:
        add_line("bb_upper", "BB Upper", "#94a3b8", width=1)
        add_line("bb_lower", "BB Lower", "#94a3b8", width=1,
                 fill="tonexty", fillcolor="rgba(148,163,184,0.10)")
        add_line("bb_middle", "BB Middle (SMA 20)", "#64748b", width=1, dash="dot")
    if show_volume_sma:
        add_line("volume_sma_20", "Volume SMA 20", "#d97706", row=2, width=1.8)

    next_row = 3
    if show_rsi:
        add_line("rsi_14", "RSI 14", "#7c3aed", row=next_row)
        for level, color in ((30, UP_COLOR), (70, DOWN_COLOR)):
            figure.add_hline(y=level, line_dash="dash", line_color=color,
                             line_width=1, row=next_row, col=1)
        figure.update_yaxes(title_text="RSI (14)", range=[0, 100],
                            tickvals=[0, 30, 50, 70, 100], row=next_row, col=1)
        next_row += 1
    if show_macd:
        figure.add_trace(
            go.Bar(
                x=data["timestamp"], y=data["macd_histogram"], name="MACD Histogram",
                marker_color=[UP_COLOR if value >= 0 else DOWN_COLOR
                              for value in data["macd_histogram"]],
                opacity=0.55, hovertemplate="Histogram: %{y:,.4f}<extra></extra>",
            ), row=next_row, col=1,
        )
        add_line("macd", "MACD (12,26)", "#2563eb", row=next_row)
        add_line("macd_signal", "Signal (9)", "#d97706", row=next_row)
        figure.add_hline(y=0, line_color="#94a3b8", line_width=1, row=next_row, col=1)
        figure.update_yaxes(title_text="MACD", row=next_row, col=1)

    if signals is not None:
        if not isinstance(signals, EvaluationResult) or not signals.long.index.equals(data.index):
            raise ValueError("신호 결과의 인덱스가 차트 데이터와 일치해야 합니다.")
        markers = (
            ("LONG 조건 충족", signals.long & ~signals.conflict, "low", "triangle-up", UP_COLOR),
            ("SHORT 조건 충족", signals.short & ~signals.conflict, "high", "triangle-down", DOWN_COLOR),
            ("LONG/SHORT 충돌", signals.conflict, "close", "x", "#7c3aed"),
        )
        for name, matched, column, marker, color in markers:
            points = data.loc[matched]
            if points.empty:
                continue
            figure.add_trace(
                go.Scatter(
                    x=points["timestamp"], y=points[column], mode="markers", name=name,
                    marker={"symbol": marker, "color": color, "size": 10,
                            "line": {"color": "#ffffff", "width": 1}},
                    hovertemplate=(name + "<br>봉 시작: %{x|%Y-%m-%d %H:%M} UTC"
                                   "<br>조건 충족 표시 · 체결 가격 아님<extra></extra>"),
                ), row=1, col=1,
            )
    if executions is not None:
        required_events = {"status", "action", "direction", "execution_timestamp", "price", "signal_timestamp"}
        if not required_events.issubset(executions.columns):
            raise ValueError("가상 체결 이벤트 컬럼이 올바르지 않습니다.")
        filled = executions.loc[executions["status"] == "FILLED"]
        risk_reasons = ("STOP_LOSS", "TAKE_PROFIT", "TRAILING_STOP")
        risk_points = (filled["exit_reason"].isin(risk_reasons) if "exit_reason" in filled
                       else pd.Series(False, index=filled.index))
        for action, label, marker in (("ENTRY", "진입", "diamond"), ("EXIT", "청산", "circle-open")):
            for direction, color in (("LONG", "#2563eb"), ("SHORT", "#d97706")):
                points = filled.loc[(filled["action"] == action) & (filled["direction"] == direction) & ~risk_points]
                if points.empty:
                    continue
                name = f"가상 {direction} {label}"
                figure.add_trace(go.Scatter(
                    x=points["execution_timestamp"], y=points["price"], mode="markers", name=name,
                    marker={"symbol": marker, "color": color, "size": 12, "line": {"width": 2}},
                    customdata=points["signal_timestamp"].dt.strftime("%Y-%m-%d %H:%M UTC"),
                    hovertemplate=(name + "<br>가상 체결: %{x|%Y-%m-%d %H:%M} UTC"
                                   "<br>신호 봉 시작: %{customdata}<br>비용 적용 가격: %{y:,.2f} USDT"
                                   "<extra></extra>"),
                ), row=1, col=1)
        for reason, label, color in (("STOP_LOSS", "손절", "#dc2626"),
                                      ("TAKE_PROFIT", "익절", "#16a34a"),
                                      ("TRAILING_STOP", "트레일링", "#9333ea")):
            if "exit_reason" not in filled:
                break
            points = filled.loc[risk_points & filled["exit_reason"].eq(reason)]
            if points.empty:
                continue
            name = f"리스크 청산 · {label}"
            figure.add_trace(go.Scatter(
                x=points["execution_candle_timestamp"], y=points["price"], mode="markers", name=name,
                marker={"symbol": "x", "color": color, "size": 13, "line": {"width": 2}},
                customdata=[timing + (" · 정확한 봉 내부 시각은 알 수 없음" if timing == "INTRABAR_OHLC"
                                       else " · 시가 기준") for timing in points["execution_timing"]],
                hovertemplate=(name + "<br>해당 봉 시작: %{x|%Y-%m-%d %H:%M} UTC"
                               "<br>체결 가정: %{customdata}<br>비용 적용 가격: %{y:,.2f} USDT"
                               "<extra></extra>"),
            ), row=1, col=1)
    figure.update_layout(
        template="plotly_white",
        paper_bgcolor="#ffffff",
        plot_bgcolor="#ffffff",
        font={"color": "#111827", "family": "Arial, sans-serif"},
        height=560 + 170 * panel_count,
        margin={"l": 12, "r": 12, "t": 65 if any(selected.values()) or signals is not None or executions is not None else 20, "b": 20},
        showlegend=any(selected.values()) or signals is not None or executions is not None,
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.01,
                "xanchor": "left", "x": 0, "font": {"size": 11}},
        hovermode="x",
        dragmode="zoom",
        # 같은 시장에서 새로고침할 때 사용자가 선택한 확대 범위를 유지한다.
        uirevision=f"{symbol}:{timeframe}:{data.attrs.get('period', '')}:{show_rsi}:{show_macd}",
        bargap=0.05,
    )
    figure.update_xaxes(type="date", rangeslider_visible=False, gridcolor="#eef2f7")
    figure.update_xaxes(title_text="날짜 (UTC)", row=rows, col=1)
    figure.update_yaxes(title_text="가격 (USDT)", tickformat=",.2f", row=1, col=1)
    figure.update_yaxes(title_text=f"거래량 ({base_asset})", rangemode="tozero", row=2, col=1)
    figure.update_yaxes(gridcolor="#eef2f7", fixedrange=False)
    return figure
