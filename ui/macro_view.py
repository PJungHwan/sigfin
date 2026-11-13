"""명시적 조회 버튼, 선택/자격 변경 시 무효화, 시리즈별 FRED 결과 표시."""

import pandas as pd
import streamlit as st

from config.settings import FRED_CACHE_TTL_SECONDS, MACRO_PERIOD_YEARS
from models.macro import FredError, FredKeyError, get_series
from services.macro_service import credential_scope, load_macro_series
from ui.macro_charts import macro_series_chart


def render_macro_controls(selected: tuple[str, ...]):
    period = st.selectbox("거시경제 조회 기간", list(MACRO_PERIOD_YEARS), index=2, key="macro_period", persist_state="session")
    cpi_yoy = st.selectbox("CPI 표시", ("원본 지수", "전년동월 대비 (%)"), key="macro_cpi_view", persist_state="session") == "전년동월 대비 (%)"
    scope = None
    try:
        scope = credential_scope()
    except FredKeyError as error:
        st.info(str(error))
        st.caption("프로젝트 .env에 FRED_API_KEY를 설정하세요. 키는 화면에 입력하거나 표시하지 않습니다.")
    if not selected:
        st.caption("사이드바의 거시경제 항목을 하나 이상 선택해 주세요.")
    requested = st.button("거시경제 데이터 조회", key="load_macro", width="stretch",
                          disabled=not selected or scope is None)
    st.caption("관측일은 발표일이 아닙니다. 현재 개정값 스냅샷이며 과거 해당 날짜에 알 수 있던 값으로 해석할 수 없습니다.")
    return period, cpi_yoy, scope, requested


def render_macro_data(selected, controls):
    period, cpi_yoy, scope, requested = controls
    end = pd.Timestamp.now(tz="UTC").normalize()
    start = end - pd.DateOffset(years=MACRO_PERIOD_YEARS[period])
    start_date, end_date = start.date().isoformat(), end.date().isoformat()
    signature = (selected, start_date, end_date, scope, "inclusive_observation_labels_v1")
    previous = st.session_state.get("_macro_signature")
    if previous is not None and previous != signature:
        st.session_state.pop("_macro_results", None)
        st.session_state.pop("_macro_signature", None)
        st.info("거시경제 선택·기간 또는 인증 설정이 변경되어 이전 조회 결과를 무효화했습니다. 다시 조회해 주세요.")
    if scope is None or not selected:
        return
    if requested:
        results = {}
        st.session_state.pop("_macro_results", None)
        with st.spinner("선택한 FRED 시리즈를 조회합니다..."):
            for index, series_id in enumerate(selected):
                try:
                    results[series_id] = load_macro_series(series_id, start_date, end_date)
                except FredError as error:
                    # 원본 예외/요청 URL/키를 Session State에 저장하지 않는다.
                    results[series_id] = {"error": str(error), "category": error.category}
                    if error.category in ("AUTHENTICATION", "RATE_LIMIT"):
                        for pending in selected[index+1:]:
                            results[pending] = {"error": "앞선 FRED 요청의 인증/한도 오류로 이 시리즈 조회를 보류했습니다.",
                                                "category": "DEFERRED"}
                        break
        st.session_state["_macro_results"] = results
        st.session_state["_macro_signature"] = signature
    results = st.session_state.get("_macro_results")
    if results is None:
        st.caption("조회 버튼을 누르면 선택한 실제 FRED 데이터를 표시합니다.")
        return
    for series_id, data in results.items():
        spec = get_series(series_id)
        st.markdown(f"**{spec.name} · {series_id}**")
        st.caption(f"{spec.description} · {spec.frequency_label} · 단위: {spec.unit_label} · "
                   + ("계절 조정" if spec.seasonal_adjustment == "SA" else "계절 조정 안 됨"))
        if isinstance(data, dict):
            st.error(f"{spec.name}: {data['error']} ({data['category']})")
            continue
        column = "yoy_pct" if series_id == "CPIAUCSL" and cpi_yoy else "value"
        valid = data.loc[data[column].notna()]
        unit = "% (전년동월비)" if column == "yoy_pct" else spec.unit_label
        if valid.empty:
            st.info("표시할 유효 관측값이 없습니다. 전년동월비는 정확히 1년 전 값이 필요합니다.")
        else:
            latest = valid.iloc[-1]
            a, b = st.columns(2)
            a.metric(f"최신 유효 관측값 · {series_id}", f"{latest[column]:,.3f} {unit}")
            b.metric(f"관측일 · {series_id}", latest.observation_date.strftime("%Y-%m-%d"))
            st.plotly_chart(macro_series_chart(data, series_id, cpi_yoy=column == "yoy_pct"),
                            width="stretch", theme=None, key=f"macro_chart_{series_id}")
        if data.attrs.get("missing_values") or data.attrs.get("missing_months"):
            st.warning(f"결측값 {data.attrs.get('missing_values', 0)}개 · 누락 월 {data.attrs.get('missing_months', 0)}개. 값을 채우지 않았습니다.")
        if data.attrs.get("boundary_observations_removed", 0):
            st.caption(f"월별 API의 시작 월 경계 관측 {data.attrs['boundary_observations_removed']}개는 요청 시작일 이전 라벨이므로 표시에서 제외했습니다.")
        st.caption(f"수집 시각: {data.attrs['fetched_at']:%Y-%m-%d %H:%M:%S} UTC · "
                   f"{len(data):,}개 관측값 · 캐시 {FRED_CACHE_TTL_SECONDS//60}분 · 발표일/빈티지 미확인")
        st.caption(f"실제 관측 구간: {data.observation_date.iloc[0]:%Y-%m-%d} ~ "
                   f"{data.observation_date.iloc[-1]:%Y-%m-%d}")
        st.markdown(f"[원본 시리즈 및 출처: FRED]({spec.source_url})")
        with st.expander(f"{series_id} 관측 데이터 및 버전 정보", expanded=False):
            table = data.copy()
            table.attrs = {}  # Arrow에는 데이터 컬럼만 전달하고 원본 시각 메타데이터는 보존한다.
            st.dataframe(table, hide_index=True, width="stretch")
            st.caption(f"스냅샷 조회 기준일: {data.attrs.get('retrieved_as_of', '미확인')} · "
                       f"원본 메타데이터 갱신 시각: {data.attrs.get('source_last_updated', '미확인')} (발표 시각 아님)")
            st.caption("realtime_start/end는 API 실시간 구간이며 최초 발표일이나 빈티지 날짜가 아닙니다. release_date/available_at/vintage_date는 미확인 상태입니다.")
