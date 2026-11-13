"""수동 실행, 설정 변경 시 결과 무효화, 사후 통계와 데이터 품질 표시."""

import pandas as pd
import streamlit as st

from analytics.models import AnalysisConfig, AnalysisError, TRANSFORMS
from collectors.binance import DataCollectionError
from config.settings import APP_VERSION, DATA_CACHE_TTL_SECONDS, FRED_CACHE_TTL_SECONDS
from models.macro import FredError, FredKeyError, SERIES
from services.crypto_macro_service import ANALYSIS_PERIODS, load_crypto_macro_analysis
from services.macro_service import credential_scope
from ui.crypto_macro_charts import (normalized_trend_chart, correlation_scatter_chart, lag_correlation_chart)


def render_crypto_macro_controls(symbol):
    st.caption(f"선택 자산: {symbol} · 월별 표본 1개 · 전략 신호/백테스트와 독립적인 통계 탐색")
    a, b, c = st.columns(3)
    series = a.selectbox("통합 분석 거시경제 지표", list(SERIES), key="cm_series",
                         format_func=lambda key: f"{key} · {SERIES[key].name}", persist_state="session")
    period = b.selectbox("통합 분석 기간", list(ANALYSIS_PERIODS), index=1, key="cm_period", persist_state="session")
    timeframe = c.selectbox("통합 분석 원천 봉", ("1d", "4h", "1h", "15m", "5m"), key="cm_timeframe", persist_state="session")
    a, b = st.columns(2)
    transform = a.selectbox("거시경제 변화 기준", list(TRANSFORMS[series]), key=f"cm_transform_{series}",
                            format_func=lambda value: " · ".join(TRANSFORMS[series][value]), persist_state="session")
    aggregation = "last"
    if series == "DGS10":
        aggregation = b.selectbox("DGS10 월간 집계", ("last", "mean"), key="cm_dgs_aggregation",
                                  format_func=lambda value: "월말 마지막 유효 관측" if value == "last" else "월간 유효 관측 평균", persist_state="session")
    mode = st.selectbox("통합 분석 모드", ("exploratory", "point_in_time"), key="cm_mode",
                        format_func=lambda value: "A · 사후 탐색적 분석" if value == "exploratory" else "B · 시점 준수 분석 (데이터 부족)", persist_state="session")
    partial = st.checkbox("불완전한 월도 포함 (탐색용)", key="cm_partial", persist_state="session")
    st.caption("기본값은 현재 진행 월·누락 봉이 있는 월을 통계 표본에서 제외합니다. 월 수익률 기준 종가가 없으면 N/A입니다. "
               "장기 조회는 일봉을 권장하며 기존 5,000개 봉·최대 7회 요청 제한을 유지합니다.")
    scope = None
    try:
        scope = credential_scope()
    except FredKeyError as error:
        st.info(str(error))
    if mode == "point_in_time":
        st.info("시점 준수 분석을 위한 발표 시각/빈티지 데이터 부족. 관측일을 공개 시점으로 대체하지 않습니다.")
    requested = st.button("통합 분석 실행", key="load_crypto_macro", width="stretch",
                          disabled=scope is None or mode != "exploratory")
    return series, period, timeframe, transform, aggregation, mode, partial, scope, requested


def _table(data):
    table = data.copy(deep=True)
    table.attrs = {}
    if isinstance(table.index, pd.PeriodIndex):
        table.index = table.index.astype(str)
        table.index.name = "관측 월"
    st.dataframe(table, width="stretch")


def _render_result(result):
    config, quality, stats = result.config, result.quality, result.correlation
    spec = SERIES[config.series_id]
    transform, unit = TRANSFORMS[config.series_id][config.transform]
    st.caption(f"{result.symbol} · {config.series_id} · 원천 봉 {config.timeframe} · "
               f"원본 단위: {spec.unit_label} · {spec.frequency_label}")
    st.caption(f"가격: 월말 마지막 확정 종가의 전월 대비 수익률 (%) · 거시: {transform} ({unit}) · "
               + (f"DGS10 집계: {config.dgs_aggregation} · " if config.series_id == "DGS10" else "")
               + f"전 시차 동일 변환 · 최소 유효 월 {config.min_samples}개")
    st.warning("관측일은 발표일이 아닙니다. 현재 개정된 FRED 값과 같은 월 가격을 사후 비교합니다. "
               "상관관계는 인과관계나 미래 예측 성능을 의미하지 않습니다. 거래 신호나 백테스트 입력으로 전달하지 않습니다.")
    a, b, c = st.columns(3)
    a.metric("Pearson · lag 0", "N/A" if stats.pearson is None else f"{stats.pearson:+.3f}")
    b.metric("Spearman · lag 0", "N/A" if stats.spearman is None else f"{stats.spearman:+.3f}")
    c.metric("공통 유효 월 · lag 0", f"{stats.n_used}개")
    st.caption(f"분석 요청: {quality['requested_start']} ~ {quality['requested_end']} · "
               f"실제 lag 0 표본: {stats.first_month or 'N/A'} ~ {stats.last_month or 'N/A'} · "
               f"전체 달력 월 {quality['candidate_months']}개 → 부분 월 정책 통과 {stats.n_before}개 "
               f"→ NaN/무한값 제외 {stats.n_removed}개 → 유효 {stats.n_used}개")
    if stats.status != "OK":
        st.info("상관계수 N/A: " + ("최소 공통 표본 수가 부족합니다." if stats.status == "INSUFFICIENT_SAMPLES"
                                    else "한쪽 시계열이 상수입니다."))
    if stats.n_used < 36:
        st.warning("월간 표본이 적어 상관계수가 불안정할 수 있습니다. 36개월 미만 안내는 탐색용 기준이며 통계적 유의성 판정이 아닙니다.")
    for reason in quality["crypto_limits"]:
        st.warning(reason)
    st.caption(f"불완전한 가격 월 {quality['crypto_partial_months']}개 · 가격 누락 월 {quality['missing_crypto_months']}개 · "
               f"거시 누락/결측 월 {quality['missing_macro_months']}개 · 원본 거시 결측값 {quality['macro_missing_values']}개")
    if not result.normalized.empty:
        st.plotly_chart(normalized_trend_chart(result), width="stretch", theme=None, key="cm_normalized")
        st.caption("수준 차트는 첫 공통 유효 월을 100으로 기준화한 추이이며 상관계수 계산에는 변화값을 사용합니다. 선 사이의 누락은 연결하지 않습니다.")
        if result.normalized.attrs.get("excluded_reason"):
            st.info(result.normalized.attrs["excluded_reason"])
    if stats.n_used:
        st.plotly_chart(correlation_scatter_chart(result), width="stretch", theme=None, key="cm_scatter")
    st.plotly_chart(lag_correlation_chart(result), width="stretch", theme=None, key="cm_lags")
    st.caption("lag k: 거시 t월 변화와 가격 t+k월 수익률. 발표 시점·빈티지를 반영하지 않은 사후 탐색이며, "
               "여러 시차 중 높은 계수만으로 투자 전략이 검증되지는 않습니다.")
    _table(result.lags.rename(columns={"lag_months": "시차 (개월)", "n_before": "결측 제거 전",
                                      "n_used": "유효 표본", "n_removed": "결측 제거", "first_month": "거시 시작 월",
                                      "last_month": "거시 종료 월"}))
    with st.expander("통합 관측값 · 시차 표본 · 수집 품질", expanded=False):
        _table(result.monthly)
        _table(result.lag_pairs)
        st.caption("macro_observation_date는 관측 라벨입니다. DGS10 월평균에서는 마지막 유효 관측 라벨이며 발표일이 아닙니다. "
                   "candle_end는 가격 봉 마감 시각입니다.")
        st.json(quality)
        st.caption(f"기존 수집 캐시 재사용: Binance {DATA_CACHE_TTL_SECONDS}초 / FRED {FRED_CACHE_TTL_SECONDS//60}분. "
                   "버튼 실행 시 캐시가 유효하면 재사용하며 만료 후 다시 조회합니다. 수익률 계산용 이전 월/연도는 표시 기간 밖에서 수집합니다.")
    with st.expander("시간별·일별 수익률 및 DGS10 일별 변화", expanded=False):
        for label, data in (("시간별", result.hourly_returns), ("일별", result.daily_returns)):
            st.caption(f"{label} 가격 수익률 (%) · 확정된 연속 집계 구간만 계산")
            if data.empty:
                st.info(data.attrs.get("unavailable_reason", "해당 주기 데이터가 없습니다."))
            else:
                start = pd.Timestamp(quality["requested_start"])
                _table(data.loc[data.index >= start])
        if not result.macro_daily.empty:
            st.caption("DGS10 일별 수준 (%) / 직전 관측 행 대비 변화 (%p). 결측 행을 채우지 않습니다.")
            _table(result.macro_daily.loc[result.macro_daily.observation_date >= pd.Timestamp(quality["requested_start"])])
    st.markdown(f"[거시경제 원본 · FRED {config.series_id}]({spec.source_url})")


def render_crypto_macro_data(symbol, controls):
    series, period, timeframe, transform, aggregation, mode, partial, scope, requested = controls
    signature = (symbol, series, period, timeframe, transform, aggregation, mode, partial, scope,
                 pd.Timestamp.now(tz="UTC").strftime("%Y-%m"), APP_VERSION)
    previous = st.session_state.get("_cm_signature")
    if previous is not None and previous != signature:
        st.session_state.pop("_cm_result", None)
        st.session_state.pop("_cm_signature", None)
        st.info("통합 분석 설정·자산 또는 인증이 변경되어 이전 결과를 무효화했습니다. 다시 실행해 주세요.")
    if scope is None or mode != "exploratory":
        return
    if requested:
        st.session_state.pop("_cm_result", None)
        with st.spinner("확정 가격과 FRED 관측값을 수집하고 월별 통계를 계산합니다..."):
            try:
                config = AnalysisConfig(series, timeframe=timeframe, transform=transform,
                                        dgs_aggregation=aggregation, include_partial=partial)
                result = load_crypto_macro_analysis(symbol, period, config)
            except FredError as error:
                result = {"error": str(error), "category": error.category, "source": "FRED"}
            except DataCollectionError as error:
                category = error.details.category if error.details is not None else "DATA_ERROR"
                result = {"error": str(error), "category": category, "source": "Binance"}
            except AnalysisError as error:
                result = {"error": str(error), "category": "ANALYSIS_VALIDATION", "source": "분석 엔진"}
        st.session_state["_cm_result"] = result
        st.session_state["_cm_signature"] = signature
    result = st.session_state.get("_cm_result")
    if result is None:
        st.caption("통합 분석 실행 버튼을 누르면 실제 수집 데이터로 계산합니다.")
    elif isinstance(result, dict):
        st.error(f"{result['source']} · {result['category']}: {result['error']}")
    else:
        _render_result(result)
