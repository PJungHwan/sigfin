"""기존 백테스트와 분리된 명시적 비교 실행·고정 실험 재현 화면."""

from dataclasses import asdict
from hashlib import sha256

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from ui.design import style_figure

from backtest.experiments import (
    MAX_STRATEGIES, capture_experiment, code_digest, comparison_table,
    execute_experiment, experiment_json, experiment_markdown,
    result_digest, risk_delta_table,
)
from backtest.experiment_verification import (
    RESULT_DIGEST_ALGORITHM, experiment_artifact_json, read_experiment_artifact,
    reference_from_result, verify_result,
)
from config.settings import OHLCV_COLUMNS, TIMEFRAME_MILLISECONDS
from services.research_snapshots import pack_frame, safe_json
from strategies.models import describe_group
from strategies.storage import strategy_from_json, strategy_to_json
from ui.uploads import session_upload

LABELS = {
    "segment": "평가 구간", "strategy": "전략", "risk": "Risk", "status": "실행 상태",
    "cumulative_return_pct": "수익률 (%)", "max_drawdown_pct": "MDD (%)", "win_rate_pct": "승률 (%)",
    "final_equity": "최종 자산 (USDT)", "closed_trades": "종료 거래 수",
    "average_trade_pnl": "평균 종료 손익 (USDT)", "total_fees": "총 수수료 (USDT)",
    "realized_pnl": "실현 손익 (USDT)", "unrealized_pnl": "미실현 손익 (USDT)",
    "rejected_orders": "미체결 수", "valid_condition_bars": "모든 조건 유효 봉 수",
    "data_status": "조건 자료 상태",
    "total_candles": "평가 봉 수", "error": "오류", "start_utc": "시작 UTC",
    "end_utc": "마지막 봉 마감 UTC", "candles": "봉 수",
    "prior_warmup_candles": "구간 이전 지표용 봉 수", "initial_position": "초기 상태",
}


def current_input_key(data, strategies, settings, risk, symbol, timeframe, period, split_at):
    """같은 OHLCV의 수집 시각 갱신만으로 고정 결과를 지우지 않는다."""
    raw = data[OHLCV_COLUMNS].copy().reset_index(drop=True)
    market = pack_frame(raw).model_dump(mode="json")
    fetched = raw.attrs.get("fetched_at")
    if fetched is not None:
        fetched = pd.Timestamp(fetched)
        interval = pd.Timedelta(milliseconds=TIMEFRAME_MILLISECONDS[timeframe])
        if (pd.isna(fetched) or fetched.tzinfo is None or fetched > pd.Timestamp.now(tz="UTC")
                or raw.empty or raw["timestamp"].iloc[-1] + interval > fetched):
            raise ValueError("수집 시각/확정 봉 메타데이터가 유효하지 않습니다.")
    market["attrs"].pop("fetched_at", None)
    payload = dict(market=market, strategies=strategies, settings=asdict(settings),
                   risk=risk.effective_settings(), symbol=symbol, timeframe=timeframe,
                   period=period, split_at=split_at, code=code_digest(),
                   quality={k: data.attrs[k] for k in ("requested_start", "requested_end", "is_partial",
                            "missing_candles", "limit_reasons") if k in data.attrs})
    return sha256(safe_json(payload).encode()).hexdigest()


def experiment_equity_chart(experiment, segment):
    figure = go.Figure()
    colors = ("#2563eb", "#16a34a", "#9333ea", "#d97706", "#0891b2", "#db2777")
    names = [strategy_from_json(item).name for item in experiment.snapshot.strategies]
    for run in experiment.runs:
        if run.segment != segment or run.result is None:
            continue
        curve = run.result.equity_curve
        figure.add_trace(go.Scatter(
            x=curve["timestamp"], y=curve["equity"], mode="lines",
            name=f"{run.strategy_name} · Risk {run.risk_mode}",
            line=dict(color=colors[names.index(run.strategy_name)], width=2,
                      dash="dash" if run.risk_mode == "OFF" else "solid"),
            hovertemplate="%{x|%Y-%m-%d %H:%M} UTC<br>%{y:,.2f} USDT<extra>%{fullData.name}</extra>",
        ))
    figure.update_layout(template="plotly_white", height=370, paper_bgcolor="#ffffff",
                         margin=dict(l=15, r=15, t=20, b=25),
                         xaxis_title="봉 마감 UTC (첫 점은 초기 자산)", yaxis_title="자산 (USDT)",
                         legend=dict(orientation="h"))
    return style_figure(figure)


def display_result_verification(experiment, reference=None, *, source="기준 없음"):
    try:
        verification = verify_result(experiment, reference, source=source)
    except (ValueError, TypeError):
        st.warning("결과 해시 비교 불가 · 결과 직렬화 한도(8MB)/내용 검증에 실패했습니다. 전략 수나 조회 구간을 줄여 다시 실행하세요. 성과표·원장은 유지합니다.")
        return None
    labels = {"MATCH": "결과 해시 일치", "MISMATCH": "결과 해시 불일치", "UNAVAILABLE": "결과 해시 비교 불가"}
    message = labels[verification.status] + " · " + verification.reason
    {"MATCH": st.success, "MISMATCH": st.error, "UNAVAILABLE": st.info}[verification.status](message)
    with st.expander("결과 해시 비교 기준", expanded=False):
        st.caption(f"기준 출처: {verification.source}")
        st.text(f"기준 결과 SHA-256: {verification.expected_digest or '없음'}")
        st.text(f"재실행 결과 SHA-256: {verification.actual_digest}")
        st.caption(f"계산 방식: {RESULT_DIGEST_ALGORITHM}")
        st.caption(f"입력 SHA-256: {experiment.snapshot.input_digest} · 선택한 화면 구간과 관계없이 전체 {len(experiment.runs)}개 조합을 비교합니다.")
        st.caption("성과 지표·종료 포지션·자산 곡선·거래·체결 이벤트·오류를 조합별로 정렬하여 SHA-256으로 계산합니다. 실행 시간/임의 ID는 제외하고, 숫자 표는 소수점 정밀도 15자리의 pandas JSON으로 정규화합니다.")
        st.caption("동일 입력/계산 코드·알고리즘과 완전 성공 결과끼리 비교합니다. 입력 해시 검증과 결과 해시 비교는 별개이며, 파일의 기준 해시는 원본 인증이나 투자 성과 검증을 뜻하지 않습니다.")
    return verification


def display_experiment(experiment, *, replay=False, reference=None, reference_source="기준 없음"):
    suffix = "replay" if replay else "current"
    st.markdown("**저장 입력 재현 결과**" if replay else "**고정 입력 비교 결과**")
    if experiment.status != "COMPLETED":
        st.error(f"비교 상태: {experiment.status} · 실패한 조합의 성과는 N/A입니다.")
    else:
        st.success(f"비교 상태: COMPLETED · 독립 실행 {len(experiment.runs)}개")
    if replay:
        display_result_verification(experiment, reference, source=reference_source)
    try:
        calculated_digest = result_digest(experiment)
    except (ValueError, TypeError):
        calculated_digest = None
        if not replay:
            st.warning("결과 해시 생성 불가 · 결과 직렬화 한도(8MB)/내용 검증에 실패했습니다. 전략 수나 조회 구간을 줄이면 해시·보고서를 생성할 수 있습니다. 성과표·원장은 유지합니다.")
    saved = experiment.snapshot
    st.caption(f"{saved.symbol} · {saved.timeframe} · 요청 기간 {saved.period} · 실행 기준 {saved.as_of_utc}")
    st.caption("동일 구간·초기금·수수료·슬리피지·거래량 참여율을 사용합니다. 구간이 다른 성과는 직접 비교하지 마세요.")
    st.caption(f"초기금 {saved.settings['initial_capital']:,.2f} USDT · 편도 수수료 {saved.settings['fee_rate']*100:g}% · "
               f"편도 슬리피지 {saved.settings['slippage_rate']*100:g}% · 직전 봉 거래량 참여율 {saved.settings['max_volume_participation']*100:g}%")
    if saved.source_quality.get("is_partial"):
        st.warning("요청 기간의 일부 데이터로 평가했습니다. 아래 실제 구간과 실험 조건의 수집 제한 사유를 확인하세요.")
    st.dataframe(experiment.segments.rename(columns=LABELS), hide_index=True, width="stretch")
    segments = experiment.segments["segment"].tolist()
    segment = st.selectbox("비교할 평가 구간", segments, key=f"exp_result_segment_{suffix}", persist_state="session")
    table = comparison_table(experiment)
    table = table.loc[table["segment"] == segment]
    if "data_status" in table and table["data_status"].eq("INSUFFICIENT_DATA").any():
        st.warning("초기 지표 NaN/조건 없음 등으로 모든 조건이 유효한 봉이 없는 전략이 있습니다. 무거래를 유효한 성과 증거로 해석하지 마세요.")
    st.caption("빈 성과는 N/A입니다. 종료 거래가 없으면 승률/평균 거래 손익은 N/A이며 거래 수는 0입니다.")
    st.dataframe(table.rename(columns=LABELS), hide_index=True, width="stretch")
    st.plotly_chart(experiment_equity_chart(experiment, segment), width="stretch", theme=None, key=f"experiment_equity_{suffix}")
    st.markdown("**Risk ON − OFF 차이**")
    st.caption("수익률·승률·MDD 차이는 %p, 자산·손익·수수료 차이는 USDT입니다. MDD 감소는 음수로 표시됩니다.")
    delta = risk_delta_table(experiment)
    st.dataframe(delta.loc[delta["segment"] == segment].rename(columns=LABELS), hide_index=True, width="stretch")
    with st.expander("실험 조건·전략·한계", expanded=False):
        st.caption(f"입력 SHA-256: {saved.input_digest}")
        st.caption(f"결과 SHA-256: {calculated_digest or 'N/A · 검증 한도/내용 확인 필요'} · 계산 코드: {saved.code_digest}")
        st.write("동일 초기금/비용/직전 봉 거래량 참여율", saved.settings)
        st.write("Risk ON 설정", saved.risk)
        for text in saved.strategies:
            strategy = strategy_from_json(text)
            st.text(strategy.name + "\n" + describe_group(strategy.long) + "\n" + describe_group(strategy.short))
        for warning in experiment.warnings:
            st.warning(warning)
        st.caption("각 구간은 FLAT/동일 원금으로 시작합니다. 첫 봉 Cross는 False, 마지막 신호는 미체결이며 열린 포지션은 종가 평가합니다.")
    with st.expander("조합별 거래·체결 원장", expanded=False):
        for run in experiment.runs:
            if run.segment == segment and run.result is not None:
                st.markdown(f"**{run.strategy_name} · Risk {run.risk_mode}**")
                st.dataframe(run.result.trades, hide_index=True, width="stretch")
                st.dataframe(run.result.events, hide_index=True, width="stretch")
    if replay and reference is None and calculated_digest is not None:
        st.caption("기존 JSON에 기준이 없는 경우 새 다운로드 파일은 이번 재실행을 향후 비교 기준으로 저장합니다. 과거 원본 결과와의 일치를 소급 증명하지 않습니다.")
    # 큰 결과의 해시 실패가 유효한 입력·표·곡선 전체를 중단하지 않게 한다.
    # 기존 기준이 있다면 재다운로드에서도 그대로 보존한다.
    input_json = (experiment_json(saved) if calculated_digest is None and reference is None
                  else experiment_artifact_json(experiment, reference=reference))
    st.download_button("실험 입력 JSON 다운로드", input_json, file_name=f"sigfin-experiment-{saved.input_digest[:12]}.json",
                       mime="application/json", key=f"exp_json_{suffix}", on_click="ignore")
    if calculated_digest is None:
        st.caption("입력 JSON은 다운로드할 수 있습니다. 기준 해시가 없으면 입력 전용으로 저장하며 결과 해시/Markdown은 N/A입니다.")
        return
    st.download_button("비교 보고서 Markdown 다운로드", experiment_markdown(experiment),
                       file_name=f"sigfin-experiment-{saved.input_digest[:12]}.md", mime="text/markdown",
                       key=f"exp_md_{suffix}", on_click="ignore")


def render_experiments(data, strategy, settings, risk, symbol, timeframe, period, *, panel="all"):
    if panel == "replay":
        render_experiment_replay(st.session_state.get("_experiment_current"))
        return
    with st.container(border=True):
        st.subheader("백테스트 전략 비교 · 고정 실험")
        st.caption("다음 연속 봉 시가 체결 · 동일 비용/거래량 한도 · 실제 주문/API 호출 없음")
        with st.expander("비교 전략 등록·실험 설정", expanded=False):
            st.info("전략 편집에서 작성하거나 불러온 뒤 비교 목록에 추가하세요. 이름이 다른 전략 2~6개를 고정합니다. 현재 편집은 등록 전략을 바꾸지 않습니다.")
            candidates = st.session_state.setdefault("_experiment_strategies", [])
            if st.button("현재 전략을 비교 목록에 추가", key="exp_add_strategy", disabled=strategy is None):
                names = [strategy_from_json(item).name for item in candidates]
                if strategy.name in names or len(candidates) >= MAX_STRATEGIES:
                    st.error("전략 이름이 중복되었거나 최대 6개입니다. 다른 이름으로 작성하거나 기존 항목을 삭제하세요.")
                else:
                    candidates = [*candidates, strategy_to_json(strategy)]
                    st.session_state["_experiment_strategies"] = candidates
            if candidates:
                names = [strategy_from_json(item).name for item in candidates]
                selected = st.selectbox("등록 전략", names, key="exp_remove_selection", persist_state="session")
                if st.button("선택 전략 삭제", key="exp_remove_strategy"):
                    candidates = [item for item in candidates if strategy_from_json(item).name != selected]
                    st.session_state["_experiment_strategies"] = candidates
                st.caption("등록 전략: " + ", ".join(strategy_from_json(item).name for item in candidates))
            chronological = st.checkbox("시간순 검토/검증 구간도 평가", value=True, key="exp_split_enabled", persist_state="session")
            fraction = st.number_input("앞쪽 검토 구간 비율 (%)", min_value=10, max_value=90, value=70, step=5,
                                       disabled=not chronological, key="exp_split_percent", persist_state="session")
            st.caption("위 백테스팅 영역의 초기금·비용·거래량 참여율·Risk Management 설정을 공통 사용합니다. 지표는 현재 조회 구간 OHLCV로 재계산하며 초기 NaN을 유지합니다.")
            requested = st.button("전략 × Risk 비교 실행", key="exp_run", type="primary",
                                  disabled=data is None or settings is None or risk is None or len(candidates) < 2)
        split_at = int(len(data) * fraction / 100) if chronological and data is not None else None
        key = None
        if data is not None and settings is not None and risk is not None:
            try:
                key = current_input_key(data, candidates, settings, risk, symbol, timeframe, period, split_at)
            except (ValueError, TypeError, KeyError):
                st.error("실험 입력을 확인할 수 없습니다. 유효한 시장 데이터/설정이 필요합니다.")
        previous = st.session_state.get("_experiment_current")
        if previous is not None and previous[0] != key:
            st.session_state.pop("_experiment_current", None)
            st.info("시장 입력·등록 전략·비용·리스크·구간 설정 변경으로 이전 비교 결과를 무효화했습니다.")
        if requested and key is not None:
            try:
                with st.spinner("고정 OHLCV로 전략 × Risk 독립 실험을 실행합니다..."):
                    snapshot = capture_experiment(data, [strategy_from_json(s) for s in candidates], settings, risk,
                                                  symbol=symbol, timeframe=timeframe, period=period, split_at=split_at)
                    result = execute_experiment(snapshot)
                st.session_state["_experiment_current"] = (key, result)
            except (ValueError, TypeError, KeyError) as error:
                st.session_state.pop("_experiment_current", None)
                st.error(f"실험 실행 실패: {error}")
        current = st.session_state.get("_experiment_current")
        if current is not None:
            display_experiment(current[1])
        else:
            st.caption("아직 현재 입력의 비교 결과가 없습니다. 전략을 2개 이상 등록하고 명시적으로 실행하세요.")
        if panel == "all":
            render_experiment_replay(current)


def render_experiment_replay(current=None):
    with st.expander("저장 실험 재현 · 현재 조회와 독립", expanded=False):
        uploaded = session_upload("고정 실험 JSON", type=["json"], key="exp_upload", max_upload_size=8)
        uploaded_data = uploaded.getvalue() if uploaded is not None else None
        uploaded_digest = sha256(uploaded_data).hexdigest() if uploaded_data is not None else None
        previous_upload = st.session_state.get("_experiment_replay_upload_digest")
        if previous_upload is not None and previous_upload != uploaded_digest:
            for name in ("_experiment_replay", "_experiment_replay_reference", "_experiment_replay_reference_source", "_experiment_replay_upload_digest"):
                st.session_state.pop(name, None)
            st.info("업로드 파일이 바뀌어 이전 재실행/해시 비교 결과를 무효화했습니다. 다시 실행하세요.")
        if st.button("저장 입력으로 재실행", key="exp_replay", disabled=uploaded is None):
            st.session_state.pop("_experiment_replay", None)
            st.session_state.pop("_experiment_replay_reference", None)
            st.session_state.pop("_experiment_replay_reference_source", None)
            try:
                snapshot, reference = read_experiment_artifact(uploaded_data)
                source = "저장 JSON의 기준 결과 해시" if reference is not None else "기준 없음"
                if reference is None and current is not None and current[1].snapshot.input_digest == snapshot.input_digest:
                    reference = reference_from_result(current[1])
                    source = "같은 입력의 현재 세션 원본 실행"
                st.session_state["_experiment_replay"] = execute_experiment(snapshot)
                st.session_state["_experiment_replay_reference"] = reference
                st.session_state["_experiment_replay_reference_source"] = source
                st.session_state["_experiment_replay_upload_digest"] = uploaded_digest
            except (ValueError, TypeError, KeyError):
                st.error("저장 실험의 형식/해시/코드 버전/확정 데이터를 검증하지 못했습니다.")
        replay = st.session_state.get("_experiment_replay")
        if replay is not None:
            st.caption("과거 고정 입력 재실행입니다. 현재 차트·원장·설정의 결과가 아닙니다. 별도 API/DB 조회를 수행하지 않습니다.")
            # 현재 결과와 동시에 표시해도 위젯 키가 충돌하지 않는다.
            with st.container(key="experiment_replay_result"):
                display_experiment(replay, replay=True,
                                   reference=st.session_state.get("_experiment_replay_reference"),
                                   reference_source=st.session_state.get("_experiment_replay_reference_source", "기준 없음"))
