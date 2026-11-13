"""통합 보고서의 실제 그래프/모의 HTTP/근거 재대조. 유료 API 없음."""

from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch

import httpx
import pandas as pd

from backtest.models import BacktestConfig,RiskConfig
from models.research import ResearchContext,PortfolioResearchInput,BacktestResearchInput,ResearchReport
from risk.portfolio import PortfolioRiskSettings
from risk.models import PositionSettings
from services.local_store import LocalStore
from services.research_service import run_research,integrate_snapshot,validate_integrated_snapshot,report_markdown
from services.research_snapshots import capture_workflow,pack_frame,validate_snapshot,safe_json
from graph.runner import run_workflow
from llm.budget import AIOptions,UsageLedger
from llm.config import AIError,OpenAIConfig
from indicators.technical import add_technical_indicators
from strategies.storage import strategy_to_json
from test_workflow import market,macro_frames
from test_ai_analysis import NOW,TEST_OPENAI_KEY
from test_portfolio import NOW as PORTFOLIO_NOW,account,trade,quote
from test_portfolio_ui import fresh_frame
from test_backtest import strategy
import test_llm_workflow as http_fixtures


class ResearchReportTests(unittest.TestCase):
    def run_mock(self,**kwargs):
        return run_research(kwargs.pop('data',market()),'BTC/USDT','1h',period='test',macro_frames=kwargs.pop('macro_frames',macro_frames()),
                            requested=True,now=kwargs.pop('now',NOW),**kwargs)

    def test_actual_mock_debate_risk_graph_and_report_provenance(self):
        with patch('openai.OpenAI') as sdk:
            result,saved=self.run_mock()
        sdk.assert_not_called()
        report=validate_integrated_snapshot(saved)
        self.assertEqual(report.debate_source['run_id'],result.state['run_id'])
        self.assertEqual(report.risk_source['mode'],'Mock Risk Manager')
        self.assertEqual(report.calculation_origin,'Python 검산 · LLM 생성 수치 아님')
        self.assertTrue(all(not v['api_called'] for v in report.agent_execution.values()))
        self.assertIn('risk_manager',{e.node for e in result.state['node_execution_logs']})

    def test_exact_evidence_value_unit_time_and_source_preserved(self):
        _,saved=self.run_mock(); report=validate_integrated_snapshot(saved)
        close=next(f for f in report.evidence if f.metric_name=='close')
        self.assertEqual(close.metric_value,float(market().close.iloc[-1]))
        self.assertEqual(close.unit,'USDT')
        self.assertEqual(close.observation_time,report.data_as_of_utc)
        self.assertTrue(all(f.role=='context' for f in report.evidence if f.source_type=='MACRO'))

    def test_formatted_markdown_and_memo_are_separate_safe_text(self):
        _,saved=self.run_mock(context=ResearchContext(memo='<script> [link](https://example.org)'))
        report=validate_integrated_snapshot(saved); md=report_markdown(report)
        for title in ('기술적 요약','거시경제 요약','검증된 근거','Python 계산','사용자 메모'): self.assertIn(title,md)
        self.assertNotIn('<script>',md); self.assertNotIn('[link](https://example.org)',md)
        self.assertEqual(report.user_memo,'<script> [link](https://example.org)')

    def test_integrated_report_tampering_rejected(self):
        _,saved=self.run_mock()
        for key,value in (('signal','BULLISH'),('technical_summary','확인되지 않은 사건'),('data_as_of_utc','2020-01-01T00:00:00Z')):
            changed=saved.model_copy(deep=True); changed.integrated['report'][key]=value
            if changed.integrated['report']==saved.integrated['report']: continue
            with self.subTest(key=key),self.assertRaises(ValueError): validate_snapshot(changed)
        changed=saved.model_copy(deep=True)
        changed.integrated['report']['evidence'][0]['metric_value']+=1
        with self.assertRaises(ValueError): validate_snapshot(changed)

    def test_source_unsupported_evidence_and_time_are_rejected(self):
        _,saved=self.run_mock()
        for updates in ({'reference_id':'technical:invented'},{'metric_value':1234567.}, {'observation_time':'2030-01-01T00:00:00Z'}):
            changed=saved.model_copy(deep=True)
            changed.results['technical_result']['evidence'][0].update(updates)
            with self.subTest(updates=updates),self.assertRaises(ValueError): validate_snapshot(changed)

    def test_debate_claim_and_risk_constraint_tampering_rejected(self):
        _,saved=self.run_mock()
        changed=saved.model_copy(deep=True); changed.results['debate_evaluation']['debate_summary']='fake'
        with self.assertRaises(ValueError): validate_snapshot(changed)
        changed=saved.model_copy(deep=True); changed.results['risk_result']['estimated_loss']=99999.
        with self.assertRaises(ValueError): validate_snapshot(changed)

    def test_no_macro_and_insufficient_data_are_explicit(self):
        _,saved=self.run_mock(data=market(short=True),macro_frames={})
        report=validate_integrated_snapshot(saved)
        self.assertEqual(report.signal,'INSUFFICIENT_DATA')
        self.assertEqual(report.research_action,'ABSTAIN')
        self.assertEqual(report.status,'PARTIAL')
        self.assertTrue(report.missing_data)

    def test_failed_technical_does_not_become_normal_report(self):
        with patch('agents.research.RuleTechnicalAgent.analyze',side_effect=ValueError('private')):
            execution,saved=self.run_mock()
        report=validate_integrated_snapshot(saved)
        self.assertEqual(execution.state['execution_status'],'FAILED')
        self.assertEqual(report.status,'FAILED'); self.assertEqual(report.research_action,'ABSTAIN')
        self.assertNotIn('private',safe_json(saved))

    def test_portfolio_and_backtest_recomputed_without_changing_ledger(self):
        data=add_technical_indicators(fresh_frame())
        a=trade(account()); original=a.model_dump_json()
        p=PortfolioResearchInput(account=a,quotes={'BTC/USDT':quote(float(data.close.iloc[-1]))},
            frames={'BTC/USDT':pack_frame(data)},history=[],risk_settings=PortfolioRiskSettings(),evaluated_at_utc=PORTFOLIO_NOW.isoformat())
        b=BacktestResearchInput(strategy_json=strategy_to_json(strategy()),config=asdict(BacktestConfig()),risk=asdict(RiskConfig()))
        _,saved=self.run_mock(data=data,now=PORTFOLIO_NOW,macro_frames={},context=ResearchContext(portfolio=p,backtest=b))
        report=validate_integrated_snapshot(saved)
        self.assertIsNotNone(report.valuation); self.assertIsNotNone(report.portfolio_risk)
        self.assertEqual(report.backtest['execution_mode'],'Python Backtest')
        self.assertEqual(a.model_dump_json(),original)

    def test_saved_report_restores_same_snapshot_without_sdk(self):
        _,saved=self.run_mock()
        with TemporaryDirectory() as folder,patch('openai.OpenAI') as sdk:
            path=Path(folder)/'research.sqlite3'; LocalStore(path).save_analysis(saved)
            restored=LocalStore(path).load_analysis(saved.record_id)
        sdk.assert_not_called()
        self.assertEqual(restored,saved)

    def test_mock_reproducibility_excludes_execution_identity_and_timings(self):
        _,a=self.run_mock(); _,b=self.run_mock()
        first=validate_integrated_snapshot(a); second=validate_integrated_snapshot(b)
        for key in ('signal','technical_summary','macro_summary','evidence','risk_factors','research_action'):
            self.assertEqual(getattr(first,key),getattr(second,key))

    def test_button_and_live_cost_gates_never_call_sdk(self):
        with patch('openai.OpenAI') as sdk:
            with self.assertRaises(AIError): run_research(market(),'BTC/USDT','1h',period='test')
            with self.assertRaises(AIError) as failure:
                self.run_mock(mode='LLM Workflow',config=OpenAIConfig(api_key=TEST_OPENAI_KEY),options=AIOptions('live'),ledger=UsageLedger())
            self.assertEqual(failure.exception.category,'COST_APPROVAL_REQUIRED')
        sdk.assert_not_called()

    def live(self,*,supplement=None,handler=None,**kwargs):
        harness=http_fixtures.LLMWorkflowTests(); harness.setUp()
        with patch('llm.client.openai.OpenAI',side_effect=harness.factory(handler or harness.handler)):
            execution,saved=self.run_mock(mode='LLM Workflow',cost_approved=True,config=harness.config,
                options=kwargs.pop('options',harness.options),ledger=harness.ledger,supplement=supplement,**kwargs)
        return execution,saved,harness

    def test_live_real_sdk_mock_http_no_paid_debate_or_risk(self):
        execution,saved,harness=self.live()
        report=validate_integrated_snapshot(saved)
        self.assertEqual(len(harness.requests),2)
        self.assertEqual(report.execution_mode,'LLM Workflow')
        self.assertTrue(all(v['api_called'] for v in report.agent_execution.values()))
        self.assertEqual(report.debate_source['status'],'NOT_RUN')
        self.assertEqual(report.risk_source['status'],'NOT_RUN')
        self.assertEqual(sum(v['usage']['total_tokens'] for v in report.agent_execution.values()),4000)
        self.assertNotIn(TEST_OPENAI_KEY,safe_json(saved))

    def test_live_with_prior_matching_mock_labels_independent_runs(self):
        frames=macro_frames()
        original=run_workflow(market(),'BTC/USDT','1h',macro_frames=frames,requested=True,now=NOW,debate_enabled=True,risk_enabled=True)
        supplement=capture_workflow(original,market(),macro_frames=frames,settings={'period':'test'})
        _,saved,harness=self.live(supplement=supplement,macro_frames=frames)
        report=validate_integrated_snapshot(saved)
        self.assertEqual(report.debate_source['run_id'],supplement.record_id)
        self.assertNotEqual(report.debate_source['run_id'],report.report_id)
        self.assertEqual(report.risk_source['mode'],'Mock Risk Manager')
        self.assertEqual(len(harness.requests),2)

    def test_supplement_wrong_period_blocked_before_any_paid_path(self):
        source=run_workflow(market(),'BTC/USDT','1h',now=NOW)
        supplement=capture_workflow(source,market(),settings={'period':'different'})
        with patch('services.research_service.run_workflow') as run,self.assertRaises(ValueError):
            self.run_mock(mode='LLM Workflow',supplement=supplement)
        run.assert_not_called()

    def test_mock_http_rate_limit_stays_failed_and_records_unknown_cost(self):
        def handler(request): return httpx.Response(429,json={'error':{'message':'test','type':'rate_limit_error','code':'rate_limit'}})
        result,saved,harness=self.live(handler=handler)
        self.assertEqual(result.state['execution_status'],'FAILED')
        self.assertEqual(saved.integrated['report']['status'],'FAILED')
        self.assertTrue(any(row['status']=='RATE_LIMIT' for row in harness.ledger.records))
        self.assertGreater(harness.ledger.committed_usd,0)

    def test_total_cost_limit_prevents_both_calls(self):
        _,saved,harness=self.live(options=AIOptions('live',cost_limit_usd=.000001))
        self.assertEqual(saved.integrated['report']['status'],'FAILED')
        self.assertEqual(harness.requests,[])

    def test_portfolio_future_attachment_rejected(self):
        data=add_technical_indicators(fresh_frame())
        p=PortfolioResearchInput(account=account(),quotes={},frames={},history=[],risk_settings=None,
            evaluated_at_utc=(PORTFOLIO_NOW+pd.Timedelta('1h')).isoformat())
        with self.assertRaises(ValueError): self.run_mock(data=data,now=PORTFOLIO_NOW,macro_frames={},context=ResearchContext(portfolio=p))

    def test_integrated_secret_and_unapproved_schema_fields_rejected(self):
        _,saved=self.run_mock()
        changed=saved.model_copy(deep=True); changed.integrated['context']['api_key']='do-not-store'
        with self.assertRaises(ValueError): validate_snapshot(changed)
        with self.assertRaises(ValueError): self.run_mock(context=ResearchContext(memo='sk-fake-secret-not-for-storage'))

    def test_duplicate_mock_request_is_not_shown_as_full_success(self):
        ledger=UsageLedger()
        self.run_mock(ledger=ledger)
        result,saved=self.run_mock(ledger=ledger)
        self.assertTrue(any(e.category=='DUPLICATE_REQUEST' for e in result.state['errors']))
        self.assertNotEqual(saved.integrated['report']['status'],'COMPLETED')

    def test_live_local_insufficient_result_not_labelled_llm_output(self):
        _,saved,harness=self.live(data=market(short=True),macro_frames={})
        report=validate_integrated_snapshot(saved)
        self.assertEqual(harness.requests,[])
        self.assertTrue(all(not r['api_called'] for r in report.agent_execution.values()))
        self.assertIn('실제 LLM 해석 없음',report.interpretation_origin)

    def test_strategy_and_user_risk_parameters_are_preserved(self):
        context=ResearchContext(strategy_json=strategy_to_json(strategy()),risk_agent_settings=PositionSettings(capital=4321.,stop_distance=.07))
        _,saved=self.run_mock(context=context)
        report=validate_integrated_snapshot(saved)
        self.assertEqual(report.strategy_name,strategy().name)
        self.assertEqual(report.risk_source['report']['position_settings']['capital'],4321.)
        self.assertEqual(report.risk_source['report']['position_settings']['stop_distance'],.07)
