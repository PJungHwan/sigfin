"""Production six-page entrypoint; synthetic data/temporary DB, no HTTP/LLM."""

from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from backtest.experiment_verification import experiment_artifact_json, verify_result
from demo.offline import synthetic_market
from llm.config import OpenAIConfig
from services.local_store import LocalStore
from services.market_data import load_market_data
from services.macro_service import _load_cached

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def workspace(tmp_path):
    from contextlib import ExitStack
    now = pd.Timestamp.now(tz='UTC')
    database = tmp_path / 'redesign.sqlite3'
    load_market_data.clear(); _load_cached.clear()
    with ExitStack() as stack:
        stack.enter_context(patch('services.local_store.default_store_path', return_value=database))
        fetch = stack.enter_context(patch('services.market_data.fetch_binance_ohlcv',
            side_effect=lambda s,t,p,**kw: synthetic_market(s,t,p,anchor=now,**kw)))
        stack.enter_context(patch('ui.portfolio_view.portfolio_now', return_value=now))
        stack.enter_context(patch('ui.research_view.research_now', return_value=now))
        from models.macro import FredKeyError
        stack.enter_context(patch('services.macro_service.get_api_key', side_effect=FredKeyError('테스트: API 키 미설정')))
        for target in ('ui.ai_analysis_view.read_openai_config','ui.workflow_view.read_openai_config','ui.research_view.read_openai_config'):
            stack.enter_context(patch(target, return_value=OpenAIConfig()))
        for target in ('openai.OpenAI','openai.AsyncOpenAI','requests.sessions.Session.request','httpx.Client.send','httpx.AsyncClient.send'):
            stack.enter_context(patch(target, side_effect=AssertionError('External call forbidden')))
        at = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=50).run()
        assert not at.exception
        yield at, fetch, database, now
    load_market_data.clear(); _load_cached.clear()


def go(at, page, task=None):
    at.switch_page(str(ROOT / 'app_pages' / (page+'.py'))).run()
    assert not at.exception
    if task is not None:
        key = {'strategy':'strategy_task','portfolio':'portfolio_task','macro':'macro_task',
               'research':'ai_task','history':'history_task'}[page]
        at.segmented_control(key=key).set_value(task).run()
        assert not at.exception
    return at


def create_and_buy(at):
    go(at, 'portfolio', '보유·가상 기록')
    at.button(key='pf_create').click().run()
    at.button(key='pf_load_prices').click().run()
    at.checkbox(key='pf_trade_confirm').check()
    at.button(key='pf_trade').click().run()
    assert not at.exception


@pytest.mark.parametrize('page', ['market','strategy','portfolio','macro','research','history'])
def test_selected_workspace_only_and_no_automatic_actions(workspace, page):
    at, _, db, _ = workspace
    go(at, page)
    assert len(at.title) == 1
    assert '_workflow_execution' not in at.session_state
    assert '_research_snapshot' not in at.session_state
    assert '_backtest_result' not in at.session_state
    assert not db.exists()
    keys = {w.key for w in at.button}
    if page != 'portfolio': assert 'pf_trade' not in keys
    if page != 'research': assert 'run_research' not in keys


def test_history_does_not_fetch_market_even_after_filter_changes(workspace):
    at, fetch, _, _ = workspace
    go(at, 'history')
    before = fetch.call_count
    at.selectbox(key='symbol').select('ETH/USDT').run()
    at.selectbox(key='timeframe').select('4h').run()
    assert fetch.call_count == before
    assert not at.get('plotly_chart')


def test_strategy_cost_risk_and_registered_list_survive_navigation(workspace):
    at, _, _, _ = workspace
    go(at, 'strategy')
    at.text_input(key='strategy_name').set_value('보존할 전략').run()
    at.number_input(key='bt_capital').set_value(2345.).run()
    at.number_input(key='bt_fee').set_value(.2).run()
    at.checkbox(key='risk_stop_loss_enabled').check().run()
    at.number_input(key='risk_stop_loss_rate').set_value(1.).run()
    draft = at.session_state['_strategy_draft']
    go(at, 'strategy', '전략 비교')
    at.button(key='exp_add_strategy').click().run()
    go(at, 'history'); go(at, 'research'); go(at, 'strategy', '전략 편집')
    assert at.session_state['_strategy_draft'] == draft
    assert at.text_input(key='strategy_name').value == '보존할 전략'
    assert at.number_input(key='bt_capital').value == 2345.
    assert at.number_input(key='bt_fee').value == .2
    assert at.checkbox(key='risk_stop_loss_enabled').value
    assert at.number_input(key='risk_stop_loss_rate').value == 1.
    assert len(at.session_state['_experiment_strategies']) == 1


def test_indicator_toggles_cache_and_trace_data_survive_route(workspace):
    import json
    at, fetch, _, _ = workspace
    initial = json.loads(at.get('plotly_chart')[0].proto.spec)
    at.checkbox(key='show_rsi').uncheck().run()
    go(at,'history'); go(at,'market')
    assert not at.checkbox(key='show_rsi').value
    current = json.loads(at.get('plotly_chart')[0].proto.spec)
    # Removing a panel intentionally renumbers later axes; numerical data stays.
    def numerical(traces):
        return [{k:v for k,v in d.items() if k not in ('xaxis','yaxis')}
                for d in traces if d['name'] != 'RSI 14']
    assert numerical(initial['data']) == numerical(current['data'])
    assert fetch.call_count == 1


def test_portfolio_price_context_and_minus_ten_percent_scenario(workspace):
    at, fetch, db, _ = workspace
    create_and_buy(at)
    account = at.session_state['_portfolio_account']
    valuation = at.session_state['_portfolio_valuation']
    before = fetch.call_count
    go(at, 'portfolio', '위험 분석')
    at.number_input(key='pr_btc_drop').set_value(10.).run()
    at.number_input(key='pr_fee').set_value(0.).run()
    at.number_input(key='pr_slippage').set_value(0.).run()
    at.button(key='run_portfolio_risk').click().run()
    report = at.session_state['_portfolio_risk']
    assert report.weights and report.scenario
    assert report.scenario['loss_from_current'] == pytest.approx(valuation.positions[0]['market_value'] * .1)
    assert report.valuation.positions == valuation.positions
    assert LocalStore(db).load_account()[0] == account
    assert fetch.call_count == before
    go(at,'research'); go(at,'portfolio','보유·가상 기록')
    assert at.session_state['_portfolio_account'] == account


def test_risk_tab_without_price_does_not_bypass_validation(workspace):
    at, _, _, _ = workspace
    create_and_buy(at)
    at.session_state['_portfolio_frames'] = {}
    go(at,'portfolio','위험 분석')
    at.button(key='run_portfolio_risk').click().run()
    assert at.session_state['_portfolio_risk'].status == 'UNKNOWN'
    assert any('유효한 공통' in w.value for w in at.warning)


def test_report_navigation_save_download_restore_with_closed_raw_json(workspace):
    at, fetch, db, _ = workspace
    create_and_buy(at)
    go(at,'research')
    at.button(key='run_research').click().run()
    assert not at.exception
    saved = at.session_state['_research_snapshot']
    ledger = at.session_state['_ai_ledger']
    go(at,'history'); go(at,'research')
    assert at.session_state['_research_snapshot'] == saved
    assert at.session_state['_ai_ledger'] is ledger
    at.button(key='save_research').click().run()
    assert at.session_state['_research_snapshot'] == saved
    assert LocalStore(db).load_analysis(saved.record_id) == saved
    assert {'research_md','research_json'} <= {x.key for x in at.get('download_button')}
    go(at,'history')
    calls = fetch.call_count
    at.button(key='restore_analysis_snapshot').click().run()
    assert at.session_state['_restored_analysis'] == saved
    raw = [x for x in at.expander if x.label == '원본 저장 데이터 보기 (개발자용)']
    assert len(raw) == 1 and not raw[0].proto.expanded
    assert fetch.call_count == calls
    # New process/session path: database still provides the same saved snapshot.
    fresh = AppTest.from_file(str(ROOT/'app.py'),default_timeout=50).run()
    go(fresh,'history')
    fresh.button(key='restore_analysis_snapshot').click().run()
    assert fresh.session_state['_restored_analysis'] == saved


def test_actual_input_change_invalidates_research_but_navigation_does_not(workspace):
    at, _, _, _ = workspace
    go(at,'research'); at.button(key='run_research').click().run()
    go(at,'strategy')
    at.number_input(key='bt_fee').set_value(.3).run()
    go(at,'research')
    assert '_research_snapshot' not in at.session_state
    assert any('무효화' in row.value for row in at.info)


@pytest.mark.parametrize('task,mode_key,mode,button,approval', [
    ('통합 리포트','research_mode','LLM Workflow','run_research','research_cost_approved'),
    ('에이전트 워크플로우','workflow_mode','LLM Workflow','run_multi_agent','workflow_cost_approved'),
    ('단일 AI 분석(고급)','ai_mode','live','analyze_ai','ai_cost_approved'),
])
def test_live_without_approval_never_calls_paid_api(workspace, task, mode_key, mode, button, approval):
    at, _, _, _ = workspace
    go(at,'research',task)
    at.selectbox(key=mode_key).select(mode).run()
    assert not at.checkbox(key=approval).value
    at.button(key=button).click().run()
    assert not at.exception
    assert at.error or at.info
    assert at.session_state['_ai_ledger'].request_count == 0


def test_experiment_actual_controls_next_open_stop_and_replay_hash(workspace):
    from strategies.storage import strategy_to_dict
    from test_experiments import strategy
    at, _, _, _ = workspace
    at.session_state['_strategy_draft'] = strategy_to_dict(strategy('A', 113, 115))
    at.session_state['_strategy_revision'] = 0
    go(at,'strategy','전략 비교')
    at.checkbox(key='risk_stop_loss_enabled').check().run()
    at.number_input(key='risk_stop_loss_rate').set_value(1.).run()
    at.number_input(key='bt_fee').set_value(.2).run()
    at.button(key='exp_add_strategy').click().run()
    at.session_state['_strategy_draft'] = strategy_to_dict(strategy('B',112,116))
    at.button(key='exp_add_strategy').click().run()
    at.button(key='exp_run').click().run()
    assert not at.exception
    original = at.session_state['_experiment_current'][1]
    assert len(original.runs) == 12
    assert original.snapshot.risk['stop_loss_rate'] == .01
    assert original.snapshot.settings['fee_rate'] == .002
    assert any('STOP_LOSS' in r.result.events.to_string() for r in original.runs if r.result is not None)
    payload = experiment_artifact_json(original).encode()
    go(at,'strategy','저장 실험 재현')
    with patch('ui.uploads.st.file_uploader', return_value=BytesIO(payload)):
        at.run(); at.button(key='exp_replay').click().run()
    assert any('결과 해시 일치' in x.value for x in at.success)
    replay = at.session_state['_experiment_replay']
    assert verify_result(replay,at.session_state['_experiment_replay_reference']).status == 'MATCH'
    go(at,'history'); go(at,'strategy','저장 실험 재현')
    assert at.session_state['_experiment_replay'] is replay
    assert any('결과 해시 일치' in x.value for x in at.success)


def test_short_and_error_do_not_create_fake_chart(workspace):
    from collectors.binance import DataCollectionError
    at, fetch, _, now = workspace
    fetch.side_effect = lambda s,t,p,**kw: synthetic_market(s,t,p,anchor=now,case='short',**kw)
    at.button(key='refresh_market').click().run()
    assert not at.exception and len(at.get('plotly_chart')) == 1
    fetch.side_effect = DataCollectionError('테스트 연결 실패')
    at.button(key='refresh_market').click().run()
    assert not at.exception and not at.get('plotly_chart')
    assert any('테스트 연결 실패' in x.value for x in at.error)


def test_single_backtest_survives_navigation_without_second_execution(workspace):
    from backtest.engine import run_backtest
    from strategies.storage import strategy_to_dict
    from test_experiments import strategy
    at, _, _, _ = workspace
    at.session_state['_strategy_draft'] = strategy_to_dict(strategy('단일 검산',113,115))
    at.session_state['_strategy_revision'] = 0
    with patch('ui.backtest_view.run_backtest', wraps=run_backtest) as engine:
        go(at,'strategy','단일 백테스트')
        at.button(key='run_backtest').click().run()
        result = at.session_state['_backtest_result']
        signature = at.session_state['_backtest_key']
        go(at,'history'); go(at,'market'); go(at,'strategy')
        assert at.session_state['_backtest_result'] is result
        assert at.session_state['_backtest_key'] == signature
        assert engine.call_count == 1
        at.number_input(key='bt_fee').set_value(.4).run()
        assert '_backtest_result' not in at.session_state
        assert any('무효화' in x.value for x in at.info)


def test_mock_workflow_debate_risk_and_single_analysis_are_independent(workspace):
    at, _, _, _ = workspace
    go(at,'research','에이전트 워크플로우')
    at.checkbox(key='debate_enabled').check().run()
    at.checkbox(key='risk_manager_enabled').check().run()
    at.button(key='run_multi_agent').click().run()
    execution = at.session_state['_workflow_execution']
    assert execution.state['debate_status'] == 'COMPLETED'
    assert execution.state['risk_result'] is not None
    ledger = at.session_state['_ai_ledger']
    go(at,'research','단일 AI 분석(고급)')
    at.button(key='analyze_ai').click().run()
    assert '_ai_result' in at.session_state
    go(at,'history'); go(at,'research','에이전트 워크플로우')
    assert at.session_state['_workflow_execution'] is execution
    assert at.session_state['_ai_ledger'] is ledger
    assert at.session_state['_ai_ledger'].request_count == 0
    closed_json = {id(value) for panel in at.expander if not panel.proto.expanded for value in panel.json}
    assert {id(value) for value in at.json} <= closed_json


def test_budget_and_macro_selection_survive_pages_without_fetch(workspace):
    at, fetch, _, _ = workspace
    go(at,'macro')
    at.checkbox(key='macro_cpi').check().run()
    go(at,'research')
    at.number_input(key='ai_request_limit').set_value(3).run()
    at.number_input(key='ai_cost_limit').set_value(.15).run()
    calls = fetch.call_count
    go(at,'history'); go(at,'macro')
    assert at.checkbox(key='macro_cpi').value
    go(at,'research','에이전트 워크플로우')
    assert at.number_input(key='ai_request_limit').value == 3
    assert at.number_input(key='ai_cost_limit').value == .15
    assert fetch.call_count == calls


def test_corrupt_replay_is_explicit_error_and_cannot_replace_current_result(workspace):
    at, _, _, _ = workspace
    go(at,'strategy','저장 실험 재현')
    with patch('ui.uploads.st.file_uploader', return_value=BytesIO(b'{bad json')):
        at.run(); at.button(key='exp_replay').click().run()
    assert not at.exception
    assert '_experiment_replay' not in at.session_state
    assert any('검증하지 못' in x.value for x in at.error)


def test_expired_portfolio_quotes_become_unavailable_without_ledger_changes(workspace):
    at, _, db, now = workspace
    create_and_buy(at)
    account = at.session_state['_portfolio_account']
    stored = db.read_bytes()
    with patch('ui.portfolio_view.portfolio_now', return_value=now+pd.Timedelta(hours=4)):
        go(at,'portfolio','위험 분석')
        at.button(key='run_portfolio_risk').click().run()
    assert at.session_state['_portfolio_risk'].status == 'UNKNOWN'
    assert at.session_state['_portfolio_valuation'].total_equity is None
    assert at.session_state['_portfolio_account'] == account
    assert db.read_bytes() == stored
