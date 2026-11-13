"""검증된 공개 주장/반박의 실행 후 타임라인. Mock 결과와 내부 추론을 구분한다."""

import pandas as pd
import streamlit as st


def render_debate(state):
    st.markdown("**Bull / Bear · Mock Debate**")
    status = state["debate_status"]
    st.caption(f"실제 LangGraph 노드 · 결정론적 Mock 규칙 · 상태 {status} · 수행 라운드 {state['debate_round']}/{state['debate_rounds']} · OpenAI API 미호출")
    st.info("공개 주장과 상대 근거에 대한 구조적 검토 기록입니다. 비공개 내부 추론이 아니며 환각 제거 또는 예측 정확도 향상을 입증하지 않습니다.")
    if status == "CANCELLED":
        st.warning("토론이 취소되었습니다. 실행한 노드의 부분 기록만 보존하며 완료 리포트로 표시하지 않습니다.")
    elif status in ("PARTIAL", "FAILED"):
        st.warning("토론 일부 또는 전체가 실패했습니다. 검증된 주장과 제외된 결과를 구분해 표시합니다.")
    elif status == "SKIPPED":
        st.info("핵심 기술 자료가 부족해 토론을 건너뛰었습니다.")
    history = state.get("debate_history", [])
    evaluation = state.get("debate_evaluation")
    allowed_ids = set(evaluation.validated_message_ids) if evaluation is not None else None
    if not history:
        st.caption("실행된 검증 주장/반박 기록이 없습니다.")
    else:
        tabs = st.tabs([f"Round {number}" for number in range(1, state["debate_rounds"]+1)])
        for number, tab in enumerate(tabs, 1):
            with tab:
                cols = st.columns(2)
                for column, side, color in zip(cols, ("BULL", "BEAR"), ("green", "red")):
                    with column:
                        st.markdown(f"**:{color}[{side}]**")
                        messages = [item for item in history if item.round == number and item.speaker == side]
                        if not messages:
                            st.caption("미실행 또는 검증 실패로 사용할 수 있는 메시지가 없습니다.")
                        for item in messages:
                            with st.container(border=True):
                                st.caption(f"{item.message_type} · {item.validation_status} · {item.claim_id}")
                                if item.validation_status != "VALID" or (allowed_ids is not None and item.claim_id not in allowed_ids):
                                    st.warning("미검증 메시지 내용은 표시하지 않습니다.")
                                    continue
                                st.text(item.content)
                                st.caption("상대 주장: "+(item.target_claim_id or "없음"))
                                st.caption("근거 ID: "+(", ".join(item.evidence_ids) or "없음 · 근거 부족 안내"))
                                if item.citations:
                                    with st.expander("이 메시지의 검증 근거"):
                                        st.dataframe(pd.DataFrame([fact.model_dump() for fact in item.citations]), hide_index=True, width="stretch")
    with st.expander("Debate Evaluator · 검증 결과", expanded=True):
        result = state.get("debate_evaluation")
        if result is None:
            st.caption("평가가 실행되지 않았습니다.")
        else:
            st.text(result.debate_summary)
            st.caption(f"평가 상태 {result.evaluation_status} · 보수적 방향 요약 {result.suggested_signal}")
            with st.expander("Debate 원본 검증 데이터 (개발자용)", expanded=False):
                st.json(result.model_dump(), expanded=False)
            st.caption("addressed_claim_ids는 반박/인정에서 참조한 주장입니다. 반박을 받았다는 이유로 원본 관측이 거짓이라고 처리하지 않습니다.")
    with st.expander("토론 근거 준비 · 단위/관측 시각"):
        pool = state.get("debate_evidence")
        if pool is not None:
            st.dataframe(pd.DataFrame([fact.model_dump() for fact in pool.facts]), hide_index=True, width="stretch")
            st.caption("Macro 자료는 현재 관측 문맥이며 방향 주장에 사용하지 않습니다. 관측일을 발표일로 대체하지 않습니다.")
