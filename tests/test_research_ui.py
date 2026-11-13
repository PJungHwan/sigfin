"""버튼·설정 변경·모드·저장 복원 AppTest. 합성 시세, SDK 미호출 감시."""

import unittest
from unittest.mock import patch

from llm.config import OpenAIConfig
from services.local_store import LocalStore,StorageError
from services.market_data import load_market_data
from services.research_snapshots import safe_json
import json
import streamlit as st
from ui.workflow_view import input_signature
from test_portfolio import NOW
import test_portfolio_ui as fixtures


class ResearchUITests(unittest.TestCase):
    def setUp(self):
        fixtures.PortfolioUITests.setUp(self)
        patch('ui.research_view.research_now',return_value=NOW).start()
        patch('ui.research_view.read_openai_config',return_value=OpenAIConfig()).start()
    tearDown=fixtures.PortfolioUITests.tearDown
    app=fixtures.PortfolioUITests.app
    ready=fixtures.PortfolioUITests.ready
    buy=fixtures.PortfolioUITests.buy

    def test_default_mode_and_rerun_do_not_execute(self):
        with patch('ui.research_view.run_research') as run:
            app=self.app(); app.text_area(key='research_memo').set_value('메모').run(); app.run()
        run.assert_not_called()
        self.assertEqual(app.selectbox(key='research_mode').value,'Mock Workflow')
        self.assertNotIn('_research_snapshot',app.session_state)

    def test_mock_report_real_graph_no_extra_market_calls(self):
        app=self.app(); count=self.fetch.call_count
        app.button(key='run_research').click().run()
        self.assertFalse(app.exception)
        self.assertIn('_research_snapshot',app.session_state)
        self.assertEqual(app.session_state['_research_snapshot'].integrated['report']['debate_source']['mode'],'Mock Debate')
        self.assertEqual(self.fetch.call_count,count)

    def test_settings_memo_and_symbol_invalidate(self):
        app=self.app(); app.button(key='run_research').click().run()
        app.text_area(key='research_memo').set_value('changed').run()
        self.assertNotIn('_research_snapshot',app.session_state)
        app.button(key='run_research').click().run()
        app.selectbox(key='symbol').select('ETH/USDT').run()
        self.assertNotIn('_research_snapshot',app.session_state)

    def test_live_unapproved_no_sdk_or_report(self):
        app=self.app(); app.selectbox(key='research_mode').select('LLM Workflow').run()
        self.assertFalse(app.checkbox(key='research_cost_approved').value)
        app.button(key='run_research').click().run()
        self.assertFalse(app.exception)
        self.assertNotIn('_research_snapshot',app.session_state)
        self.assertTrue(any('COST_APPROVAL_REQUIRED' in e.value for e in app.error))

    def test_sensitive_memo_is_reported_without_page_crash(self):
        app=self.app()
        app.text_area(key='research_memo').set_value('sk-test-secret-not-for-storage-1234').run()
        self.assertFalse(app.exception)
        self.assertNotIn('_research_snapshot',app.session_state)
        self.assertTrue(any('민감정보' in e.value for e in app.error))

    def test_changed_backtest_configuration_invalidates_report(self):
        app=self.app(); app.button(key='run_research').click().run()
        self.assertIn('_research_snapshot',app.session_state)
        app.number_input(key='bt_capital').set_value(2000.).run()
        self.assertNotIn('_research_snapshot',app.session_state)

    def test_portfolio_and_saved_history_remain_separate(self):
        app=self.ready(); self.buy(app)
        original=app.session_state['_portfolio_account']
        app.button(key='run_portfolio_risk').click().run()
        app.button(key='run_research').click().run()
        self.assertIn('_research_snapshot',app.session_state)
        self.assertIsNotNone(app.session_state['_research_snapshot'].integrated['report']['portfolio_risk'])
        app.button(key='save_research').click().run()
        self.assertFalse(app.exception)
        app.button(key='restore_analysis_snapshot').click().run()
        self.assertIn('_restored_analysis',app.session_state)
        self.assertEqual(app.session_state['_portfolio_account'],original)
        self.assertTrue(any('과거 저장 통합' in w.value for w in app.warning))

    def test_save_after_market_cache_refresh_preserves_report_and_downloads(self):
        app=self.ready(); self.buy(app)
        app.button(key='run_portfolio_risk').click().run()
        app.button(key='run_research').click().run()
        saved=app.session_state['_research_snapshot']
        account=app.session_state['_portfolio_account']
        # 실제 TTL 갱신처럼 종가/봉은 같고 수집 시각만 새로워진다.
        refreshed=fixtures.fresh_frame()
        refreshed.attrs['fetched_at']=NOW+fixtures.pd.Timedelta(minutes=2)
        self.fetch.return_value=refreshed
        load_market_data.clear()
        with patch('ui.portfolio_view.portfolio_now',return_value=NOW+fixtures.pd.Timedelta(minutes=2)):
            app.button(key='save_research').click().run()
        self.assertFalse(app.exception)
        self.assertIn('_research_snapshot',app.session_state)
        self.assertEqual(app.session_state['_research_snapshot'],saved)
        self.assertEqual(LocalStore(self.db_path).load_analysis(saved.record_id),saved)
        self.assertEqual(app.session_state['_portfolio_account'],account)
        labels={item.proto.label for item in app.get('download_button')}
        self.assertTrue({'통합 리포트 Markdown','통합 스냅샷 JSON'}.issubset(labels))
        app.button(key='save_research').click().run()
        self.assertEqual(LocalStore(self.db_path).list_analyses(),[saved.record_id])

    def test_changed_market_price_still_invalidates_before_save(self):
        app=self.app(); app.button(key='run_research').click().run()
        changed=fixtures.fresh_frame()
        changed.loc[changed.index[-1],['open','high','low','close']]+=1.
        self.fetch.return_value=changed
        load_market_data.clear()
        app.button(key='save_research').click().run()
        self.assertFalse(app.exception)
        self.assertNotIn('_research_snapshot',app.session_state)
        self.assertTrue(any('무효화' in value.value for value in app.info))

    def test_report_save_survives_rerun_and_new_session_restore(self):
        app=self.app(); app.button(key='run_research').click().run()
        saved=app.session_state['_research_snapshot']
        app.button(key='save_research').click().run()
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state['_research_snapshot'],saved)
        restored=self.app()  # 새 WebSocket/브라우저 세션처럼 session_state를 비운다.
        self.assertNotIn('_research_snapshot',restored.session_state)
        count=self.fetch.call_count
        restored.button(key='restore_analysis_snapshot').click().run()
        self.assertFalse(restored.exception)
        self.assertEqual(restored.session_state['_restored_analysis'],saved)
        self.assertTrue(any('과거 저장 통합' in value.value for value in restored.warning))
        self.assertEqual(self.fetch.call_count,count)

    def test_failed_store_keeps_report_and_downloads_for_retry(self):
        app=self.app(); app.button(key='run_research').click().run()
        saved=app.session_state['_research_snapshot']
        with patch.object(LocalStore,'save_analysis',side_effect=StorageError('test storage failure')):
            app.button(key='save_research').click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state['_research_snapshot'],saved)
        self.assertTrue(any('저장 검증' in value.value for value in app.error))
        self.assertEqual(LocalStore(self.db_path).list_analyses(),[])
        labels={item.proto.label for item in app.get('download_button')}
        self.assertTrue({'통합 리포트 Markdown','통합 스냅샷 JSON'}.issubset(labels))
        app.button(key='save_research').click().run()
        self.assertEqual(LocalStore(self.db_path).load_analysis(saved.record_id),saved)

    def test_expired_portfolio_prices_still_invalidate_report(self):
        app=self.ready(); self.buy(app)
        app.button(key='run_research').click().run()
        self.assertIn('_research_snapshot',app.session_state)
        with patch('ui.portfolio_view.portfolio_now',return_value=NOW+fixtures.pd.Timedelta(hours=3)):
            app.run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state['_portfolio_valuation'].status,'UNAVAILABLE')
        self.assertNotIn('_research_snapshot',app.session_state)

    def test_historic_integrated_report_keeps_summary_and_downloads_outside_raw_json(self):
        app=self.app(); app.button(key='run_research').click().run()
        saved=app.session_state['_research_snapshot']
        app.button(key='save_research').click().run()
        before=self.db_path.read_bytes()
        app.button(key='restore_analysis_snapshot').click().run()
        self.assertFalse(app.exception)
        panel=[item for item in app.expander if item.label=='원본 저장 데이터 보기 (개발자용)'][-1]
        self.assertFalse(panel.proto.expanded)
        self.assertEqual(len(panel.json),1)
        self.assertFalse(panel.get('download_button'))
        self.assertEqual(app.session_state['_restored_analysis'],saved)
        self.assertEqual(LocalStore(self.db_path).load_analysis(saved.record_id),saved)
        self.assertEqual(self.db_path.read_bytes(),before)
        self.assertTrue(any('과거 저장 통합' in item.value for item in app.warning))
        self.assertTrue(any('기술적 분석:' in item.value for item in app.text))
        labels={item.proto.label for item in app.get('download_button')}
        self.assertTrue({'통합 리포트 Markdown','통합 스냅샷 JSON','분석 스냅샷 JSON'}.issubset(labels))

    def test_report_json_has_only_one_explicit_debug_location_per_view(self):
        app=self.app(); app.button(key='run_research').click().run()
        app.button(key='save_research').click().run()
        app.button(key='restore_analysis_snapshot').click().run()
        panels=[item for item in app.expander if item.label=='원본 저장 데이터 보기 (개발자용)']
        self.assertEqual(len(panels),2)  # 현재/과거 화면 각각 한 번. 다른 상세 패널의 원본 중복 금지.
        self.assertEqual({id(item) for item in app.json},{id(item) for panel in panels for item in panel.json})
        for panel in panels:
            self.assertFalse(panel.proto.expanded)
            self.assertEqual(len(panel.json),1)
            self.assertFalse(panel.json[0].proto.expanded)
            self.assertFalse(panel.text)
        historic=panels[-1]
        self.assertEqual(json.loads(historic.json[0].value),json.loads(safe_json(app.session_state['_restored_analysis'])))

    def test_historical_reload_resets_debug_panel_and_rerun_does_not_open_it(self):
        with patch('ui.research_view.st.expander',wraps=st.expander) as expand:
            def history_key():
                return [call.kwargs['key'] for call in expand.call_args_list
                    if call.kwargs.get('key','').startswith('research_raw_history_')][-1]
            app=self.app(); app.button(key='run_research').click().run()
            app.button(key='save_research').click().run()
            app.button(key='restore_analysis_snapshot').click().run()
            original=app.session_state['_restored_analysis'];first_key=history_key()
            app.run()
            panel=[item for item in app.expander if item.label=='원본 저장 데이터 보기 (개발자용)'][-1]
            self.assertFalse(panel.proto.expanded)
            self.assertEqual(history_key(),first_key)
            app.button(key='restore_analysis_snapshot').click().run()
            panel=[item for item in app.expander if item.label=='원본 저장 데이터 보기 (개발자용)'][-1]
            self.assertFalse(panel.proto.expanded)
            self.assertNotEqual(history_key(),first_key)
            self.assertEqual(app.session_state['_restored_analysis'],original)


class ResearchInputSignatureTests(unittest.TestCase):
    def signature(self,data,**kwargs):
        return input_signature(data,'BTC/USDT','1h','3months',[],{},**kwargs)

    def test_default_workflow_still_tracks_collection_time(self):
        data=fixtures.fresh_frame(); refreshed=data.copy(deep=True)
        refreshed.attrs['fetched_at']=NOW+fixtures.pd.Timedelta(minutes=2)
        self.assertNotEqual(self.signature(data),self.signature(refreshed))
        self.assertEqual(self.signature(data,include_collection_time=False),self.signature(refreshed,include_collection_time=False))
        self.assertEqual(data.attrs['fetched_at'],NOW)

    def test_research_still_tracks_observation_values_and_quality(self):
        data=fixtures.fresh_frame()
        original=self.signature(data,include_collection_time=False)
        for column in ('timestamp','open','high','low','close','volume'):
            changed=data.copy(deep=True)
            changed.loc[changed.index[-1],column]+=(fixtures.pd.Timedelta(hours=1) if column=='timestamp' else 1.)
            with self.subTest(column=column):
                self.assertNotEqual(original,self.signature(changed,include_collection_time=False))
        for key,value in (('is_partial',True),('missing_candles',1),('version_policy','changed')):
            changed=data.copy(deep=True); changed.attrs[key]=value
            with self.subTest(metadata=key):
                self.assertNotEqual(original,self.signature(changed,include_collection_time=False))
