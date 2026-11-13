"""기존 그래프/검증/예산을 재사용하는 통합 보고. 조립·복원은 SDK를 호출하지 않는다."""

from dataclasses import asdict
from hashlib import sha256
import re

import pandas as pd

from graph.runner import run_workflow
from graph.state import (TechnicalResult,MacroResult,AggregationResult,WorkflowReport,NodeLog,WorkflowError,AgentExecution)
from graph.debate_state import DebateEvidence,BullResult,BearResult,DebateMessage,DebateEvaluation
from graph.risk_state import RiskContext
from graph.nodes import validate_input,aggregate
from graph.validation import validate_technical,validate_macro
from graph.debate_nodes import prepare_evidence,evaluate_debate,aggregate_debate
from graph.risk_nodes import build_risk_input,aggregate_risk
from llm.config import AIError
from models.research import ResearchContext,ResearchReport
from portfolio.engine import evaluate
from portfolio.models import utc
from risk.models import PositionSettings,RiskThresholds,RiskInput,RiskReport
from risk.evaluator import validate_risk_report
from services.ai_inputs import summarize_macro_frames
from services.portfolio_risk_service import analyze_portfolio_risk
from services.research_snapshots import (capture_workflow,restore_frame,restore_macro_frames,validate_snapshot,safe_json)


def parsed(schema,value):
    return schema.model_validate_json(safe_json(value))


def checked_source(saved):
    """스키마 다음에 원본 값/단위/시각·Mock 주장·Risk/집계를 다시 대조한다."""
    state=dict(saved.results)
    types={'technical_result':TechnicalResult,'macro_result':MacroResult,'aggregation_result':AggregationResult,
        'technical_execution':AgentExecution,'macro_execution':AgentExecution,'debate_evidence':DebateEvidence,
        'bull_result':BullResult,'bear_result':BearResult,'debate_evaluation':DebateEvaluation,'risk_input':RiskInput,'risk_result':RiskReport}
    for key,schema in types.items():
        if state.get(key) is not None: state[key]=parsed(schema,state[key])
    state['node_execution_logs']=[parsed(NodeLog,v) for v in state.get('node_execution_logs',[])]
    state['errors']=[parsed(WorkflowError,v) for v in state.get('errors',[])]
    state['debate_history']=[parsed(DebateMessage,v) for v in state.get('debate_history',[])]
    mode=state['execution_mode']
    if mode not in ('Mock Workflow','LLM Workflow'): raise ValueError('Invalid mode')
    if mode=='LLM Workflow' and (state.get('debate_enabled') or state.get('risk_manager_enabled')):
        raise ValueError('Forbidden Live graph combination')
    data=restore_frame(saved.market); frames=restore_macro_frames(saved)
    raw_input=state.get('risk_input')
    context=RiskContext(market_data=data,macro_frames=frames,requested_at_utc=saved.requested_at_utc or saved.captured_at_utc,
        started_monotonic=0.,rounds=state.get('debate_rounds',2),risk_settings=raw_input.position_settings if raw_input else
        parsed(PositionSettings,saved.settings.get('risk_agent_settings',PositionSettings().model_dump())),
        risk_thresholds=raw_input.thresholds if raw_input else RiskThresholds())
    if not state.get('input_fingerprint'):
        if state['execution_status']!='FAILED' or any(state.get(k) is not None for k in ('technical_result','macro_result','final_report','aggregation_result','risk_result','debate_evaluation','bull_result','bear_result')):
            raise ValueError('Failed input has analysis results')
        return state,[],context
    validated=validate_input(state,context)
    state['input_data']=validated['input_data']
    if validated['input_fingerprint']!=state['input_fingerprint'] or validated['data_as_of_utc']!=state['data_as_of_utc']:
        raise ValueError('Input identity/time mismatch')
    try:
        facts,metadata=summarize_macro_frames(frames,as_of=state['data_as_of_utc'],now=context.requested_at_utc,include_quality=True)
    except AIError:
        if state.get('macro_result') is not None: raise
        facts,metadata=[],[]
    state['macro_input'],state['macro_metadata']=facts,metadata
    if state.get('technical_result') is not None: validate_technical(state['technical_result'],state['input_data'],mode)
    if state.get('macro_result') is not None: validate_macro(state['macro_result'],facts,metadata,mode)
    if state.get('debate_enabled'):
        pool=prepare_evidence(state,context).get('debate_evidence')
        preparation_failed=any(e.node=='evidence_preparation' and e.status=='FAILED' for e in state['node_execution_logs'])
        if pool!=state.get('debate_evidence') and not (state.get('debate_evidence') is None and preparation_failed):
            raise ValueError('Debate pool changed')
        if state.get('debate_evaluation') is not None and evaluate_debate(state)!=state['debate_evaluation']:
            raise ValueError('Debate evaluation changed')
    if state.get('risk_result') is not None:
        inputs=build_risk_input(state,context)
        if state.get('risk_input')!=inputs: raise ValueError('Risk inputs changed')
        validate_risk_report(state['risk_result'],inputs)
    try:
        expected=(aggregate_risk if state.get('risk_manager_enabled') else aggregate_debate if state.get('debate_enabled') else aggregate)(state,context)['aggregation_result']
    except (ValueError,AIError):
        # 중복 차단/노드 실패로 실제 집계도 실패한 실행은 실패 감사 기록만 보존한다.
        if state['execution_status']=='FAILED' and state.get('aggregation_result') is None and state.get('final_report') is None:
            return state,[*state['input_data'].facts,*facts],context
        raise
    if state.get('aggregation_result') is not None and state['aggregation_result']!=expected:
        raise ValueError('Aggregator result changed')
    report=state.get('final_report')
    if report is not None:
        if report['run_id']!=saved.record_id or report['symbol']!=saved.symbol or report['timeframe']!=saved.timeframe or report['execution_mode']!=mode:
            raise ValueError('Final report identity mismatch')
        comparisons={'final_signal':expected.final_signal,'execution_status':state['execution_status'],
            'data_as_of_utc':state['data_as_of_utc'],'analyzed_at_utc':context.requested_at_utc,
            'technical_summary':state['technical_result'].technical_summary,
            'macro_summary':state['macro_result'].macro_summary if state.get('macro_result') else 'Macro Agent 실행 실패: 정상적인 거시 분석 결과가 없습니다.',
            'bullish_factors':state['technical_result'].bullish_factors,'bearish_factors':state['technical_result'].bearish_factors,
            'conflicts':expected.conflicts,'risk_factors':expected.risk_factors,'limitations':expected.limitations,
            'missing_data':expected.missing_data,'evidence':[v.model_dump() for v in expected.evidence]}
        if any(report.get(k)!=v for k,v in comparisons.items()): raise ValueError('Final report evidence/content mismatch')
        if state.get('risk_manager_enabled') and (report.get('risk_report')!=__import__('json').loads(safe_json(state.get('risk_result')))
                or report.get('research_action')!=state.get('research_action')): raise ValueError('Risk constraint changed')
    elif state['execution_status']!='FAILED': raise ValueError('Missing final report')
    return state,[*state['input_data'].facts,*facts],context


def source_scope(saved):
    return sha256(safe_json({'symbol':saved.symbol,'timeframe':saved.timeframe,'period':saved.settings.get('period'),
        'market':saved.market,'macro':saved.macro,'macro_order':saved.macro_order}).encode()).hexdigest()


def _assemble(saved,context,supplement=None):
    state,facts,_=checked_source(saved)
    final=state.get('final_report'); mode=state['execution_mode']; status=state['execution_status']
    technical=state.get('technical_result'); macro=state.get('macro_result')
    from strategies.storage import strategy_from_json
    strategy_json=context.strategy_json or (context.backtest.strategy_json if context.backtest else None)
    strategy_name=strategy_from_json(strategy_json).name if strategy_json else None
    if saved.settings.get('strategy_json') not in (None,strategy_json): raise ValueError('Strategy context mismatch')
    if context.backtest and strategy_json!=context.backtest.strategy_json: raise ValueError('Backtest strategy context mismatch')
    missing=list(final['missing_data']) if final else ['검증된 최종 분석 없음']
    limitations=list(final['limitations']) if final else []
    auxiliary=state; aux_id=saved.record_id
    if supplement is not None:
        if mode!='LLM Workflow' or supplement.results['execution_mode']!='Mock Workflow' or source_scope(saved)!=source_scope(supplement):
            raise ValueError('Supplemental Mock result has different context')
        auxiliary,_,_=checked_source(supplement); aux_id=supplement.record_id
    debate=auxiliary.get('debate_evaluation'); risk=auxiliary.get('risk_result')
    node_status={item.node:item.status for item in auxiliary.get('node_execution_logs',[])}
    debate_enabled=auxiliary.get('debate_enabled',False); risk_enabled=auxiliary.get('risk_manager_enabled',False)
    debate_source={'run_id':aux_id if debate_enabled else None,'mode':'Mock Debate' if debate_enabled else 'NOT_RUN',
        'data_as_of_utc':auxiliary.get('data_as_of_utc') if debate_enabled else None,
        'status':debate.evaluation_status if debate else node_status.get('debate_evaluator','NOT_RUN'),
        'evaluation':debate.model_dump(mode='json') if debate else None}
    risk_source={'run_id':aux_id if risk_enabled else None,'mode':'Mock Risk Manager' if risk_enabled else 'NOT_RUN',
        'data_as_of_utc':risk.data_as_of_utc if risk else auxiliary.get('data_as_of_utc') if risk_enabled else None,
        'status':risk.validation_status if risk else node_status.get('risk_manager','NOT_RUN'),
        'report':risk.model_dump(mode='json') if risk else None}
    if debate is None: missing.append('검증된 Mock Debate 결과 없음 · Live에서 자동 실행하지 않음')
    if risk is None: missing.append('검증된 Mock Risk Manager 결과 없음 · Live에서 자동 실행하지 않음')
    action=auxiliary.get('research_action') or ('ABSTAIN' if risk is None else risk.trading_action)
    valuation=portfolio_risk=backtest=None
    if context.portfolio is not None:
        p=context.portfolio
        if pd.Timestamp(utc(p.evaluated_at_utc))>pd.Timestamp(saved.requested_at_utc or saved.captured_at_utc): raise ValueError('Future portfolio attachment')
        valuation=evaluate(p.account,p.quotes,now=p.evaluated_at_utc)
        if p.risk_settings is not None:
            portfolio_risk,_=analyze_portfolio_risk(p.account,p.quotes,p.risk_settings,
                {k:restore_frame(v) for k,v in p.frames.items()},now=p.evaluated_at_utc,history=p.history)
            if portfolio_risk.trading_action=='ABSTAIN': action='ABSTAIN'
            elif portfolio_risk.trading_action=='HOLD' and action=='REVIEW': action='HOLD'
            if portfolio_risk.status!='COMPLETE': missing.append('포트폴리오 자산 Risk 자료 부족')
        else: missing.append('포트폴리오 위험 분석 미실행 · 원장 평가만 포함')
        if valuation.status!='COMPLETE': missing.append('포트폴리오 평가 가격 부족')
    else: missing.append('가상 포트폴리오 미첨부')
    if context.backtest is not None:
        from strategies.storage import strategy_from_json
        from backtest.engine import run_backtest
        from backtest.models import BacktestConfig,RiskConfig
        b=context.backtest; strategy=strategy_from_json(b.strategy_json)
        result=run_backtest(restore_frame(saved.market),strategy,BacktestConfig(**b.config),timeframe=saved.timeframe,risk=RiskConfig(**b.risk))
        backtest={'execution_mode':'Python Backtest','strategy':strategy.name,'config':b.config,'risk':b.risk,
                  'metrics':asdict(result.metrics),'source_input_digest':saved.input_digest}
    else: missing.append('현재 입력의 백테스트 미첨부')
    if status!='FAILED' and missing: status='PARTIAL'
    if status=='FAILED': action='ABSTAIN'
    if not technical or technical.technical_signal=='INSUFFICIENT_DATA': action='ABSTAIN'
    ids={e['reference_id'] for e in final['evidence']} if final else set()
    evidence=[fact for fact in facts if fact.reference_id in ids]
    limitations.extend(['통합 조립은 결정론적 Python이며 추가 LLM 호출이 없습니다.',
        'LLM Workflow의 Technical/Macro 해석과 Python 계산·Mock Debate/Risk·가상 원장/과거 백테스트를 구분합니다.',
        'Macro 관측일은 발표일이 아니며 현재 개정값/사후 관계를 과거 당시 예측 근거로 사용하지 않습니다.',
        '과거 성과·연구 상태는 투자 실행 지시나 수익 보장이 아닙니다. 실제 LLM 계정 호출은 별도 검증이 필요합니다.'])
    conflicts=list(final['conflicts']) if final else []
    if supplement is not None and technical and auxiliary.get('technical_result') and technical.technical_signal!=auxiliary['technical_result'].technical_signal:
        conflicts.append('별도 Mock 실행과 LLM Technical 의견 차이: '+auxiliary['technical_result'].technical_signal+' / '+technical.technical_signal+' · 표결로 해소하지 않음')
    valid_llm=any(state.get(role+'_execution') is not None and state[role+'_execution'].api_called
        and state[role+'_execution'].validation_status=='VALIDATED' for role in ('technical','macro'))
    origin=('LLM Technical/Macro 검증 응답 · 역할별 호출/로컬 생략 확인' if valid_llm else '로컬 검사/호출 실패 · 검증된 실제 LLM 해석 없음') if mode=='LLM Workflow' else '규칙 기반 Mock · 실제 LLM 응답 아님'
    return ResearchReport(report_id=saved.record_id,app_version=saved.app_version,symbol=saved.symbol,timeframe=saved.timeframe,
        period=saved.settings.get('period','기록 없음'),strategy_name=strategy_name,execution_mode=mode,status=status,
        analyzed_at_utc=saved.requested_at_utc or saved.captured_at_utc,data_as_of_utc=state.get('data_as_of_utc'),
        source_input_digest=saved.input_digest,signal=final['final_signal'] if final else 'INSUFFICIENT_DATA',
        interpretation_origin=origin,
        technical_summary=technical.technical_summary if technical else 'Technical 분석 실패 · 정상 요약 없음',
        macro_summary=macro.macro_summary if macro else 'Macro 분석 실패 · 정상 요약 없음',
        agent_execution={role:(state[role+'_execution'].model_dump(mode='json') if state.get(role+'_execution') else {'api_called':False,'validation_status':'NOT_RUN'}) for role in ('technical','macro')},
        debate_source=debate_source,risk_source=risk_source,conflicts=conflicts,
        risk_factors=final['risk_factors'] if final else ['분석 실패'],evidence=evidence,valuation=valuation,portfolio_risk=portfolio_risk,
        backtest=backtest,research_action=action,missing_data=list(dict.fromkeys(missing)),limitations=list(dict.fromkeys(limitations)),user_memo=context.memo)


def validate_integrated_snapshot(saved):
    body=saved.integrated
    if set(body)!={'context','supplement','report'}: raise ValueError('Invalid integrated fields')
    context=parsed(ResearchContext,body['context'])
    supplement=None
    if body['supplement'] is not None:
        from services.research_snapshots import ResearchSnapshot
        supplement=parsed(ResearchSnapshot,body['supplement'])
        if supplement.integrated is not None: raise ValueError('Nested report not allowed')
        validate_snapshot(supplement)
    report=parsed(ResearchReport,body['report'])
    if report!=_assemble(saved,context,supplement): raise ValueError('Integrated report content/evidence mismatch')
    return report


def integrate_snapshot(saved,context=None,*,supplement=None):
    if saved.integrated is not None: raise ValueError('Already integrated')
    saved=validate_snapshot(saved); context=ResearchContext.model_validate(context or ResearchContext())
    if supplement is not None: validate_snapshot(supplement)
    report=_assemble(saved,context,supplement)
    body=__import__('json').loads(safe_json({'context':context,'supplement':supplement,'report':report}))
    result=saved.model_copy(update={'integrated':body},deep=True)
    validate_snapshot(result)
    return result


def run_research(data,symbol,timeframe,*,period,macro_frames=None,mode='Mock Workflow',requested=False,cost_approved=False,
                 config=None,options=None,ledger=None,context=None,supplement=None,now=None):
    if requested is not True: raise AIError('통합 연구 리포트 실행 버튼 입력이 필요합니다.','NOT_REQUESTED')
    current=utc(now if now is not None else pd.Timestamp.now(tz='UTC'))
    # 과금 전에 첨부 구조/비밀정보와 대상 일치를 검사한다.
    context=ResearchContext.model_validate(context or ResearchContext()); safe_json(context)
    if context.portfolio is not None:
        p=context.portfolio
        if pd.Timestamp(utc(p.evaluated_at_utc))>pd.Timestamp(current): raise ValueError('Future portfolio attachment')
        evaluate(p.account,p.quotes,now=p.evaluated_at_utc)
    if supplement is not None:
        validate_snapshot(supplement)
        if supplement.symbol!=symbol or supplement.timeframe!=timeframe: raise ValueError('Supplement identity mismatch')
        if mode!='LLM Workflow' or supplement.results['execution_mode']!='Mock Workflow' or supplement.settings.get('period')!=period:
            raise ValueError('Invalid supplemental mode/period')
        # 과금 전에 시계열 내용·자료 시각·선택 Macro 일치를 검사한다.
        from services.research_snapshots import pack_frame
        expected_scope=sha256(safe_json({'symbol':symbol,'timeframe':timeframe,'period':period,
            'market':pack_frame(data),'macro':{k:pack_frame(v,macro=True) for k,v in (macro_frames or {}).items()},
            'macro_order':list(macro_frames or {})}).encode()).hexdigest()
        if source_scope(supplement)!=expected_scope: raise ValueError('Supplement inputs changed')
    execution=run_workflow(data,symbol,timeframe,macro_frames=macro_frames,now=current,mode=mode,requested=True,
        cost_approved=cost_approved,config=config,options=options,ledger=ledger,
        debate_enabled=mode=='Mock Workflow',risk_enabled=mode=='Mock Workflow',risk_settings=context.risk_agent_settings)
    settings={'period':period,'mode':mode,'risk_agent_settings':context.risk_agent_settings.model_dump()}
    if context.strategy_json is not None: settings['strategy_json']=context.strategy_json
    snapshot=capture_workflow(execution,data,macro_frames=macro_frames,settings=settings,requested_at=current)
    return execution,integrate_snapshot(snapshot,context,supplement=supplement)


def report_markdown(report):
    report=ResearchReport.model_validate(report)
    def literal(value):
        # HTML·외부 링크·코드 블록을 활성화하지 않는 내보내기.
        return re.sub(r'([\\`*_{}\[\]()<>#!|])',r'\\\1',str(value)).replace('\r','')
    lines=['# SIGFIN 통합 연구 리포트','',literal(f'{report.symbol} · {report.timeframe} · {report.period}'),
        '',literal(f'{report.execution_mode} / {report.status} / {report.signal} / {report.research_action}'),
        '',literal('자료 마감 UTC: '+str(report.data_as_of_utc)),literal('실행 ID: '+report.report_id),literal('전략 설정: '+str(report.strategy_name)),
        '',literal(report.interpretation_origin),'','## 기술적 요약','',literal(report.technical_summary),
        '','## 거시경제 요약','',literal(report.macro_summary),'','## 검증된 근거','']
    lines += ['- '+literal(f'{f.reference_id}: {f.metric_value} {f.unit} · 관측 {f.observation_time}') for f in report.evidence]
    lines += ['','## Python 계산 · Mock 출처','',literal(report.calculation_origin),
        literal(f"Debate {report.debate_source['mode']} / {report.debate_source['run_id']} / {report.debate_source['status']}"),
        literal(f"Risk {report.risk_source['mode']} / {report.risk_source['run_id']} / {report.risk_source['status']}")]
    for title,value in (('에이전트 호출/토큰/비용',report.agent_execution),('포트폴리오 평가',report.valuation),
        ('포트폴리오 위험',report.portfolio_risk),('백테스트',report.backtest)):
        lines+=['','## '+title,'',literal(safe_json(value))]
    for title,items in (('상충 근거',report.conflicts),('위험 요인',report.risk_factors),('자료 부족',report.missing_data),('한계',report.limitations)):
        lines+=['','## '+title,'']+['- '+literal(v) for v in items]
    lines+=['','## 사용자 메모 · 검증 근거 아님','',literal(report.user_memo)]
    return '\n'.join(lines)+'\n'
