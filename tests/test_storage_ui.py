"""실제 AppTest + 임시 SQLite. 영구 복원/실패의 세션 원장 보존."""

from unittest.mock import patch
import json
import unittest

from services.local_store import LocalStore,StorageError
from services.research_snapshots import safe_json
import test_portfolio_ui as fixtures


class StorageUITests(unittest.TestCase):
    setUp=fixtures.PortfolioUITests.setUp
    tearDown=fixtures.PortfolioUITests.tearDown
    app=fixtures.PortfolioUITests.app
    ready=fixtures.PortfolioUITests.ready
    buy=fixtures.PortfolioUITests.buy

    def test_new_app_session_restores_saved_cash_and_trades(self):
        first=self.ready(); self.buy(first)
        saved=first.session_state['_portfolio_account']
        restored=self.app()
        self.assertEqual(restored.session_state['_portfolio_account'],saved)
        self.assertFalse(restored.exception)

    def test_failed_database_trade_does_not_change_ui_ledger(self):
        app=self.ready(); original=app.session_state['_portfolio_account']
        with patch.object(LocalStore,'save_account',side_effect=StorageError('private')):
            self.buy(app)
        self.assertEqual(app.session_state['_portfolio_account'],original)
        self.assertTrue(any('기록하지 못' in e.value for e in app.error))
        self.assertEqual(LocalStore(self.db_path).load_account()[0],original)

    def test_valuation_saved_once_and_history_displayed(self):
        app=self.ready(); self.buy(app)
        app.button(key='pf_save_valuation').click().run()
        app.button(key='pf_save_valuation').click().run()
        values=LocalStore(self.db_path).load_valuations(app.session_state['_portfolio_account'].account_id)
        self.assertEqual(len(values),1)
        self.assertFalse(app.exception)

    def test_workflow_snapshot_restored_without_extra_network_or_paid_call(self):
        app=self.app()
        app.button(key='run_multi_agent').click().run()
        app.button(key='save_analysis_snapshot').click().run()
        self.assertFalse(app.exception)
        self.assertEqual(len(LocalStore(self.db_path).list_analyses()),1)
        calls=self.fetch.call_count
        app.button(key='restore_analysis_snapshot').click().run()
        self.assertIn('_restored_analysis',app.session_state)
        self.assertEqual(self.fetch.call_count,calls)

    def test_corrupt_storage_is_reported_without_reset(self):
        self.db_path.write_bytes(b'not a database')
        app=self.app()
        self.assertFalse(app.exception)
        self.assertEqual(self.db_path.read_bytes(),b'not a database')
        self.assertTrue(any('복원하지 못' in e.value for e in app.error))

    def test_workflow_raw_json_is_collapsed_and_read_only(self):
        app=self.app()
        app.button(key='run_multi_agent').click().run()
        app.button(key='save_analysis_snapshot').click().run()
        before=self.db_path.read_bytes()
        app.button(key='restore_analysis_snapshot').click().run()
        self.assertFalse(app.exception)
        panel=next(item for item in app.expander if item.label=='원본 저장 데이터 보기 (개발자용)')
        self.assertFalse(panel.proto.expanded)
        saved=app.session_state['_restored_analysis']
        self.assertEqual(json.loads(panel.json[0].value),json.loads(safe_json(saved)))
        self.assertFalse(panel.json[0].proto.expanded)
        self.assertFalse(panel.get('download_button'))
        self.assertTrue(any(item.proto.id.endswith('-snapshot_export') for item in app.get('download_button')))
        self.assertEqual(self.db_path.read_bytes(),before)
