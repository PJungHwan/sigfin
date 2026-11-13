"""선택 실행/비용 승인·기존 예산 공유·과거 이력과 현재 통합 보고서 분리."""

from dataclasses import asdict
from hashlib import sha256

import pandas as pd
import streamlit as st

from config.llm_settings import AI_MAX_INPUT_BYTES
from llm.budget import AIOptions,UsageLedger,price_for,estimate_cost
from llm.config import AIError,read_openai_config
from models.research import ResearchContext,PortfolioResearchInput,BacktestResearchInput,ResearchReport
from risk.models import PositionSettings
from strategies.storage import strategy_to_json
from services.research_snapshots import pack_frame,capture_workflow,safe_json
from services.research_service import run_research,report_markdown
from services.local_store import LocalStore
from services.portfolio_service import quotes_from_frames
from ui.components import metric_card
from ui.workflow_view import input_signature
from ui.ai_analysis_view import _usage


def research_now():
    return pd.Timestamp.now(tz='UTC')


def render_research_raw_data(saved,*,scope):
    """원본 출력은 화면마다 이 한 곳만 사용한다. 저장/다운로드 payload는 변경하지 않는다."""
    generation=st.session_state.get('_restored_analysis_generation',0) if scope=='history' else 0
    with st.expander('원본 저장 데이터 보기 (개발자용)',expanded=False,
                     key=f'research_raw_{scope}_{saved.record_id}_{generation}'):
        st.json(safe_json(saved),expanded=False)


def display_research_report(report,*,historic=False):
    if historic: st.warning('과거 저장 통합 리포트 · 현재 입력의 결과가 아닙니다. API 재호출 없음')
    st.caption(f'{report.report_id} · {report.symbol} · {report.timeframe} · {report.period} · {report.execution_mode} · {report.status}')
    st.caption('현재 전략 설정: '+(report.strategy_name or '미첨부')+' · 전략 JSON은 LLM에 전송하지 않습니다.')
    st.caption(f'분석 UTC {report.analyzed_at_utc} · 가격 마감 UTC {report.data_as_of_utc} · 버전 {report.app_version}')
    metric_card('통합 연구 의견 / 별도 위험 상태',report.signal+' / '+report.research_action,report.interpretation_origin)
    risk_report=report.risk_source.get('report') or {}
    st.caption('위험 등급: '+risk_report.get('risk_level','미실행')+' · 방향 의견과 별개의 연구용 위험 평가입니다.')
    if report.missing_data:
        st.warning('자료 부족: '+' / '.join(report.missing_data[:3]))
    st.text('기술적 분석: '+report.technical_summary)
    st.text('거시경제 분석: '+report.macro_summary)
    with st.expander('호출 사실·모델·토큰·비용',expanded=False):
        rows=[]
        for role,value in report.agent_execution.items():
            usage=value.get('usage') or {}
            rows.append({'역할':role,'실행 모드':value.get('execution_mode','미실행'),
                '실제 API 호출':bool(value.get('api_called')),'모델':value.get('model') or '미사용',
                '검증':value.get('validation_status'),'입력 토큰':usage.get('input_tokens'),
                '출력 토큰':usage.get('output_tokens'),'추정 비용 (USD)':value.get('estimated_cost_usd'),
                '오류 유형':value.get('error_type')})
        st.dataframe(pd.DataFrame(rows),hide_index=True,width='stretch')
        st.caption('선택 모드와 실제 API 호출은 별개입니다. api_called=False는 로컬 검사/Mock/미실행입니다. 실패 과금은 미확인입니다.')
    with st.expander('Bull/Bear Debate · Risk Manager 출처',expanded=False):
        for title,source in (('Debate',report.debate_source),('Risk Manager',report.risk_source)):
            st.text(f"{title}: {source.get('mode','미실행')} · {source.get('status','미실행')}")
            st.caption(f"실행 ID {source.get('run_id') or '없음'} · 자료 UTC {source.get('data_as_of_utc') or '없음'}")
            risk=source.get('report') or {}
            if risk: st.text(f"위험 등급 {risk.get('risk_level','UNKNOWN')} · {risk.get('risk_summary','')}")
            evaluation=source.get('evaluation') or {}
            if evaluation:
                st.text('토론 검토: '+evaluation.get('debate_summary',''))
                st.caption('상승/하락은 검증된 공개 주장입니다. 개수로 승자를 판단하지 않습니다.')
                for label,field in (('상승 주장','valid_bull_claims'),('하락 주장','valid_bear_claims')):
                    for claim in evaluation.get(field,[]):
                        st.text(label+': '+claim['content'])
                        st.caption(claim['claim_id']+' · 근거 '+', '.join(claim['evidence_ids']))
    with st.expander('계산 결과 · 가상 원장/포트폴리오 위험/과거 백테스트',expanded=False):
        st.caption(report.calculation_origin+' · 포트폴리오 자료 시각과 시장 분석 시각은 별개로 확인하세요.')
        if report.valuation is not None:
            value=report.valuation
            st.text(f'계좌 평가: {value.status}')
            st.dataframe(pd.DataFrame([{'현금 (USDT)':value.cash,'총자산 (USDT)':value.total_equity,
                '실현 손익 (USDT)':value.realized_pnl,'미실현 손익 (USDT)':value.unrealized_pnl}]),hide_index=True,width='stretch')
            for issue in value.issues: st.text(issue)
        if report.portfolio_risk is not None:
            risk=report.portfolio_risk
            st.text(f'포트폴리오 위험: {risk.status} · {risk.trading_action}')
            for reason in risk.reasons: st.text(reason)
        if report.backtest is not None:
            metrics=report.backtest.get('metrics',{})
            st.caption('과거 백테스트 검산 성과 · 현재 보유 포지션과 별개')
            st.dataframe(pd.DataFrame([{'항목':key,'값':value} for key,value in metrics.items()
                if value is None or type(value) in (int,float)]),hide_index=True,width='stretch')
    with st.expander('검증된 근거 · 수치/단위/관측 시각'):
        st.dataframe(pd.DataFrame([item.model_dump() for item in report.evidence]),hide_index=True,width='stretch')
    for title,items in (('상충 근거',report.conflicts),('위험 요인',report.risk_factors),('자료 부족',report.missing_data),('분석 한계',report.limitations)):
        with st.expander('통합 리포트 · '+title):
            for item in items: st.text(item)
    if report.user_memo:
        st.caption('사용자 메모 · 검증된 사실/LLM 입력이 아닙니다.')
        st.text(report.user_memo)


def current_research_context(now,*,strategy=None,backtest_config=None,backtest_risk=None,risk_agent_settings=None):
    portfolio=None; backtest=None
    account=st.session_state.get('_portfolio_account')
    if account is not None:
        frames=st.session_state.get('_portfolio_frames',{})
        try: quotes=quotes_from_frames(frames,now=now)
        except (ValueError,TypeError,KeyError): quotes={}
        risk=st.session_state.get('_portfolio_risk')
        portfolio=PortfolioResearchInput(account=account,quotes=quotes,frames={k:pack_frame(v) for k,v in frames.items()},
            history=st.session_state.get('_portfolio_history',[]),risk_settings=risk.settings if risk is not None else None,
            evaluated_at_utc=now.isoformat())
    if st.session_state.get('_backtest_result') is not None and strategy is not None and backtest_config is not None and backtest_risk is not None:
        backtest=BacktestResearchInput(strategy_json=strategy_to_json(strategy),config=asdict(backtest_config),risk=asdict(backtest_risk))
    return ResearchContext(memo=st.session_state.get('research_memo',''),strategy_json=strategy_to_json(strategy) if strategy else None,
        risk_agent_settings=risk_agent_settings or PositionSettings(),portfolio=portfolio,backtest=backtest)


def render_research(data,symbol,timeframe,period,selected_macro,*,strategy=None,backtest_config=None,backtest_risk=None,risk_agent_settings=None):
    with st.container(border=True):
        st.subheader('통합 연구 리포트')
        mode=st.selectbox('통합 리포트 실행 모드',('Mock Workflow','LLM Workflow'),key='research_mode', persist_state="session")
        st.text_area('연구 메모 (분석 근거/LLM 입력과 분리)',max_chars=2000,key='research_memo', persist_state="session")
        st.caption('Mock: 실제 LangGraph + 규칙 기반 Debate/Risk. LLM: 기존 Technical/Macro만 선택 호출. 보고서 조립은 Python입니다.')
        st.caption('Mock Risk 설정은 AI 연구의 Risk Manager 설정을 재사용합니다. 비활성 상태면 기본 설정(원금 10,000/손실 한도 1%/손절 3%/1배)을 사용하며 포트폴리오 보유량과 별도 가정입니다.')
        config=options=None; approved=False; ledger=st.session_state.setdefault('_ai_ledger',UsageLedger())
        source_frames=st.session_state.get('_macro_results',{})
        macro={k:source_frames[k] for k in selected_macro if k in source_frames}
        # TTL 재조회가 갱신한 수집 시각만으로 저장 버튼 앞에서 결과를 삭제하지 않는다.
        # 봉/지표/품질·실제 설정은 계속 비교하고 원본 스냅샷/수집 시각은 수정하지 않는다.
        signature_parts=[input_signature(data,symbol,timeframe,period,selected_macro,source_frames,include_collection_time=False),mode,
            strategy_to_json(strategy) if strategy else None,asdict(backtest_config) if backtest_config else None,
            asdict(backtest_risk) if backtest_risk else None,(risk_agent_settings or PositionSettings()).model_dump(),
            st.session_state.get('research_memo',''),st.session_state.get('_backtest_key'),
            st.session_state.get('_portfolio_risk_signature'),
            st.session_state['_portfolio_account'].model_dump_json() if '_portfolio_account' in st.session_state else None]
        if mode=='LLM Workflow':
            try:
                config=read_openai_config()
                options=AIOptions('live',st.session_state.get('ai_request_limit',5),st.session_state.get('ai_cost_limit',.25),
                    st.session_state.get('ai_output_limit',3000),0)
            except AIError as error: st.error(f'{error.category}: {error}')
            st.warning('유료 OpenAI 요청 최대 2회 · 요약 기술/Macro 자료만 전송 · 사용자 메모/가상 계좌는 전송하지 않습니다. 실패 과금은 미확인입니다.')
            st.caption('기본 공유 세션 5회/$0.25 · 출력 한도/병렬 비용 사전 예약/자동 재시도 0회 · OpenAI 계정 결제 한도 대체 불가')
            price=price_for(config.model) if config else None
            st.caption('모델: '+(config.model if config else '설정 오류'))
            if not config or not config.api_key: st.info('OPENAI_API_KEY가 없습니다. 기본 Mock 모드로 실행할 수 있습니다.')
            if price is None: st.warning('모델 가격 미확인/만료 · 실제 호출 차단')
            elif options:
                from llm.agent_contracts import TECH_PROMPT,MACRO_PROMPT,TechnicalLLMOutput,MacroLLMOutput,token_reservation
                upper=sum(estimate_cost(config.model,AI_MAX_INPUT_BYTES+token_reservation(p,'',s),options.max_output_tokens)
                    for p,s in ((TECH_PROMPT,TechnicalLLMOutput),(MACRO_PROMPT,MacroLLMOutput)))
                st.caption(f'예약 추정 최대 약 ${upper:.5f} USD · 가격 확인일 {price["verified_on"]} · 실제 청구액 아님')
            signature_parts.extend([None if not config else (config.model,config.scope),None if not options else asdict(options)])
        try:
            signature=sha256(safe_json(signature_parts).encode()).hexdigest()
        except (ValueError,TypeError):
            st.session_state.pop('_research_snapshot',None)
            st.error('메모에 인증 정보로 의심되는 내용이 있거나 입력 크기 제한을 초과했습니다. 민감정보를 제거하세요.')
            return
        if st.session_state.get('_research_signature')!=signature:
            if '_research_snapshot' in st.session_state: st.info('분석 입력/모드/원장/가정/전략/메모가 바뀌어 이전 통합 결과를 무효화했습니다.')
            st.session_state.pop('_research_snapshot',None); st.session_state.pop('_research_execution',None)
            st.session_state['_research_signature']=signature
            if mode=='LLM Workflow': st.session_state['research_cost_approved']=False
        if mode=='LLM Workflow':
            approved=st.checkbox('현재 통합 리포트 Technical/Macro 유료 요청의 비용을 승인합니다.',key='research_cost_approved', persist_state="session")
            st.caption('동일 입력의 기존 Mock Workflow 결과가 있으면 별도 실행 ID의 Debate/Risk로 첨부합니다. 없으면 미실행 표시하며 자동 유료 Debate/Risk 호출은 없습니다.')
        if st.button('통합 연구 리포트 실행',key='run_research',type='primary'):
            st.session_state.pop('_research_snapshot',None)
            try:
                now=research_now(); context=current_research_context(now,strategy=strategy,backtest_config=backtest_config,backtest_risk=backtest_risk,risk_agent_settings=risk_agent_settings)
                supplement=None; prior=st.session_state.get('_workflow_execution')
                if mode=='LLM Workflow' and prior is not None and prior.state['execution_mode']=='Mock Workflow':
                    supplement=capture_workflow(prior,data,macro_frames=macro,settings={'period':period,'mode':'Mock Workflow'})
                with st.spinner('기존 LangGraph 실행 및 근거 연결 보고서를 검증합니다...'):
                    execution,snapshot=run_research(data,symbol,timeframe,period=period,macro_frames=macro,mode=mode,
                        requested=True,cost_approved=approved,config=config,options=options,ledger=ledger,context=context,supplement=supplement,now=now)
                st.session_state['_research_execution']=execution
                st.session_state['_research_snapshot']=snapshot
            except AIError as error: st.error(f'통합 리포트 · {error.category}: {error}')
            except (ValueError,TypeError,KeyError,AttributeError):
                st.error('통합 입력/근거/결과 검증에 실패했습니다. 정상 보고서를 만들지 않았습니다. 기존 원장/저장 이력은 유지합니다.')
        saved=st.session_state.get('_research_snapshot')
        if saved is None:
            st.caption('현재 입력의 통합 결과가 없습니다. 버튼을 누를 때만 실행합니다.')
        else:
            report=ResearchReport.model_validate_json(safe_json(saved.integrated['report']))
            display_research_report(report)
            st.caption('실행 당시 입력 스냅샷의 리포트입니다. 동일 관측의 재수집 시각만 바뀌면 유지하며 봉·값·설정 변경 시 무효화합니다.')
            if report.status=='FAILED': st.error('워크플로우 실패 · 정상 분석 결과가 아닙니다.')
            with st.expander('통합 실행 로그/오류',expanded=False):
                columns=['node','status','started_at_utc','ended_at_utc','duration_ms','error_type']
                st.dataframe(pd.DataFrame(saved.results['node_execution_logs']).reindex(columns=columns),hide_index=True,width='stretch')
                for error in saved.results['errors']:
                    st.text(f"{error.get('node','')}: {error.get('category','')} · {error.get('message','')}")
            render_research_raw_data(saved,scope='current')
            if st.button('통합 연구 리포트 로컬 저장',key='save_research'):
                try:
                    LocalStore().save_analysis(saved)
                    st.success('입력·가정·검증된 통합 결과를 저장했습니다. 저장 이력 메뉴에서 복원할 수 있습니다.')
                except (ValueError,TypeError): st.error('저장 검증에 실패했습니다. 기존 자료는 유지합니다.')
            st.download_button('통합 리포트 Markdown',report_markdown(report),file_name=f'sigfin-report-{report.report_id}.md',mime='text/markdown',key='research_md')
            st.download_button('통합 스냅샷 JSON',safe_json(saved),file_name=f'sigfin-report-{report.report_id}.json',mime='application/json',key='research_json')
        if mode=='LLM Workflow': _usage(ledger)
