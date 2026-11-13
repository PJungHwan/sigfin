"""과거 결과 조회/다운로드는 API나 새 거래를 발생시키지 않는다."""

import json
import pandas as pd
import streamlit as st

from portfolio.models import Account
from services.local_store import LocalStore,StorageError
import services.local_store as storage
from services.research_snapshots import safe_json,capture_workflow
from ui.uploads import session_upload


def render_account_history(account,quotes,*,now):
    with st.expander("로컬 계좌·평가 저장 이력",expanded=False):
        st.caption("SQLite v1 · 로컬 단일 사용자 · SHA-256은 손상 탐지용이며 접근 인증/암호화가 아닙니다. OneDrive 동기화 중 동시 실행은 피하세요.")
        try:
            store=LocalStore()
            accounts=store.list_accounts()
            selected=st.selectbox("저장 계좌",[item[0] for item in accounts],key="pf_saved_account", persist_state="session")
            if st.button("저장 계좌 불러오기",key="pf_restore"):
                restored=store.load_account(selected)
                if restored:
                    st.session_state["_portfolio_account"],st.session_state["_portfolio_revision"]=restored
                    st.rerun()
            if st.button("현재 평가 스냅샷 저장",key="pf_save_valuation"):
                store.save_valuation(account,quotes,now=now)
                st.success("가격/원장 버전을 검증한 평가를 저장했습니다.")
            values=store.load_valuations(account.account_id)
            st.dataframe(pd.DataFrame([value.model_dump(exclude={'positions'}) for value in values]),hide_index=True,width="stretch")
            st.session_state['_portfolio_history']=values
            st.download_button("가상 원장 JSON 다운로드",safe_json(account),file_name='sigfin-virtual-account.json',mime='application/json',key='pf_export')
            uploaded=session_upload("가상 원장 JSON 복원",type=['json'],key='pf_import_file',max_upload_size=1)
            if st.button("JSON 원장 검증·불러오기",key='pf_import'):
                from strategies.storage import _unique_keys
                from uuid import uuid4
                if uploaded is None or uploaded.size>1_000_000: raise ValueError('Invalid upload')
                body=json.loads(uploaded.getvalue().decode('utf-8'),object_pairs_hook=_unique_keys)
                restored=Account.model_validate(body)
                current=store.load_account(restored.account_id)
                # DB 버전은 기존 계좌에서 읽는다. 원장 과거 재작성/충돌은 저장소가 거부한다.
                revision=store.save_account(restored,expected_revision=current[1] if current else None)
                st.session_state['_portfolio_account']=restored
                st.session_state['_portfolio_revision']=revision
                st.session_state['_portfolio_nonce']=uuid4().hex
                st.rerun()
        except (ValueError,TypeError,OSError,RecursionError):
            st.error("로컬 이력 저장/복원 검증에 실패했습니다. 기존 원장과 파일은 자동 삭제하지 않습니다.")


def render_workflow_save(data,symbol,timeframe,period,selected_macro,*,strategy=None,backtest_config=None,backtest_risk=None):
    with st.container(border=True):
        st.subheader("분석 실행 이력 · 로컬 저장")
        if st.button("현재 Workflow 스냅샷 저장",key='save_analysis_snapshot'):
            try:
                execution=st.session_state.get('_workflow_execution')
                if execution is None: raise ValueError('No execution')
                frames=st.session_state.get('_macro_results',{})
                from dataclasses import asdict
                from strategies.storage import strategy_to_json
                settings={'period':period,'mode':execution.state['execution_mode'],
                          'debate_enabled':bool(execution.state.get('debate_enabled')),'risk_enabled':bool(execution.state.get('risk_manager_enabled'))}
                if strategy is not None: settings['strategy_json']=strategy_to_json(strategy)
                if backtest_config is not None: settings['backtest_config']=asdict(backtest_config)
                if backtest_risk is not None: settings['backtest_risk']=asdict(backtest_risk)
                snapshot=capture_workflow(execution,data,macro_frames={k:frames[k] for k in selected_macro if k in frames},settings=settings)
                if snapshot.symbol!=symbol or snapshot.timeframe!=timeframe: raise ValueError('Context mismatch')
                LocalStore().save_analysis(snapshot)
                st.success("현재 분석 입력/설정/결과를 로컬 저장했습니다.")
            except (ValueError,TypeError,KeyError):
                st.error("검증된 현재 분석/입력이 없거나 저장에 실패했습니다. 기존 이력은 유지합니다.")


def render_analysis_history(data,symbol,timeframe,period,selected_macro,*,strategy=None,backtest_config=None,backtest_risk=None,show_save=True):
    if show_save:
        render_workflow_save(data,symbol,timeframe,period,selected_macro,strategy=strategy,
                             backtest_config=backtest_config,backtest_risk=backtest_risk)
    with st.container(border=True):
        st.subheader("저장된 연구 결과")
        if not storage.default_store_path().is_file():
            st.caption("아직 저장 이력이 없습니다. 저장 버튼을 누른 경우에만 DB를 생성합니다.")
            return
        try:
            store=LocalStore(); entries=store.list_analyses()
            if not entries:
                st.caption("저장된 분석 스냅샷 없음")
                return
            selected=st.selectbox("분석 실행 ID",entries,key='saved_analysis_id', persist_state="session")
            if st.button("과거 분석 결과 조회",key='restore_analysis_snapshot'):
                st.session_state['_restored_analysis']=store.load_analysis(selected)
                st.session_state['_restored_analysis_generation']=st.session_state.get('_restored_analysis_generation',0)+1
            restored=st.session_state.get('_restored_analysis')
            if restored is not None:
                st.caption("과거 저장 자료 · 현재 입력 결과가 아닙니다. API 자동 재호출 없음 · "+restored.captured_at_utc)
                if restored.integrated is not None:
                    from ui.research_view import display_research_report
                    from models.research import ResearchReport
                    display_research_report(ResearchReport.model_validate_json(safe_json(restored.integrated['report'])),historic=True)
                from ui.research_view import render_research_raw_data
                render_research_raw_data(restored,scope='history')
                st.download_button('분석 스냅샷 JSON',safe_json(restored),file_name='sigfin-research-snapshot.json',mime='application/json',key='snapshot_export')
        except (ValueError,TypeError):
            st.error("분석 이력 손상/구조 오류입니다. 자동 초기화하지 않습니다.")
