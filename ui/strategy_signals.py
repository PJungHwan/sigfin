"""조건 충족 미리보기. 체결 가격이나 성과를 제시하지 않는다."""

import pandas as pd
import streamlit as st

from config.settings import TIMEFRAME_MILLISECONDS
from strategies.evaluator import EvaluationResult
from strategies.models import Strategy, describe_condition


def render_signal_preview(data: pd.DataFrame, strategy: Strategy, result: EvaluationResult):
    st.caption(f"전략: {strategy.name}")
    long_column, short_column, conflict_column = st.columns(3)
    long_column.metric("LONG 조건 충족", int(result.long.sum()))
    short_column.metric("SHORT 조건 충족", int(result.short.sum()))
    conflict_column.metric("동시 충족 (충돌)", int(result.conflict.sum()))
    interval = pd.Timedelta(milliseconds=TIMEFRAME_MILLISECONDS[data.attrs["timeframe"]])
    for label, matched in (("LONG", result.long), ("SHORT", result.short)):
        timestamps = data.loc[matched, "timestamp"]
        if not timestamps.empty:
            latest = timestamps.iloc[-1] + interval
            st.caption(f"최근 {label} 조건 충족: {latest:%Y-%m-%d %H:%M} UTC (봉 마감)")
    if result.conflict.any():
        st.warning("같은 봉에서 LONG/SHORT 조건이 동시에 충족됐습니다. 충돌로 표시하며 방향을 선택하지 않습니다.")
    if not (result.long | result.short).any():
        st.info("현재 데이터 구간에서 전략 조건을 충족한 확정 봉이 없습니다.")
    st.caption(
        "신호는 확정 봉에서 조건이 충족된 시점입니다. 조건이 유지되면 여러 봉에 표시됩니다. "
        "개수에는 충돌 봉도 포함됩니다. 체결·수익률 결과가 아니며 같은 봉 종가 체결을 가정하지 않습니다."
    )
    latest_rows = []
    for group in (strategy.long, strategy.short):
        for condition in group.conditions:
            matched = bool(result.conditions[condition.id].iloc[-1])
            valid = bool(result.validity[condition.id].iloc[-1])
            latest_rows.append({
                "방향": group.direction.value, "조건": describe_condition(condition),
                "최신 봉 충족": matched,
                "상태": "충족" if matched else ("미충족" if valid else "비교 데이터 부족 / 직전 연속 봉 없음"),
            })
    if latest_rows:
        st.dataframe(pd.DataFrame(latest_rows), hide_index=True, width="stretch")
    with st.expander("최근 100봉의 조건별 충족 여부", expanded=False):
        detail = pd.DataFrame({"봉 마감 (UTC)": data["timestamp"] + interval,
                               "LONG": result.long, "SHORT": result.short,
                               "충돌": result.conflict})
        for group in (strategy.long, strategy.short):
            for number, condition in enumerate(group.conditions, 1):
                detail[f"{group.direction.value} 조건 {number}"] = result.conditions[condition.id]
        st.dataframe(detail.tail(100).iloc[::-1], hide_index=True, width="stretch")
