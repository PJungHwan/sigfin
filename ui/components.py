from html import escape
from math import isfinite

import streamlit as st


def metric_card(title: str, value: str, detail: str = "", color: str = "#111827"):
    """RSI, MACD, AI Signal 등에 사용하는 지표 카드"""
    st.markdown(
        f"""
        <div class="dashboard-card">
            <div class="metric-title">{escape(title)}</div>
            <div class="metric-value" style="color:{escape(color, quote=True)};">{escape(value)}</div>
            <div class="metric-title">{escape(detail)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def rsi_metric_card(value: float | None):
    if value is None or not isfinite(value):
        metric_card("RSI", "-", "데이터 부족 · 연속 확정 봉 15개 이상 필요")
        return
    if value >= 70:
        state, color = "과매수", "#dc2626"
    elif value <= 30:
        state, color = "과매도", "#16a34a"
    else:
        state, color = "중립", "#111827"
    metric_card("RSI", f"{value:.2f}", f"RSI (14) · {state}", color)


def macd_metric_card(histogram: float | None):
    if histogram is None or not isfinite(histogram):
        metric_card("MACD", "-", "데이터 부족 · 연속 확정 봉 34개 이상 필요")
        return
    if histogram > 0:
        state, color = "양수", "#16a34a"
    elif histogram < 0:
        state, color = "음수", "#dc2626"
    else:
        state, color = "0 (중립)", "#111827"
    metric_card("MACD", f"{histogram:+.4f}", f"Histogram (12,26,9) · {state}", color)


def agent_status(name: str, status: str = "WAITING"):
    """AI Agent의 현재 상태를 표시하는 카드"""
    st.markdown(
        f"""
        <div class="dashboard-card">
            <div class="metric-title">{name}</div>
            <div class="agent-waiting">{status}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
