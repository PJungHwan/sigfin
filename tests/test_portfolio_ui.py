"""합성 확정 시세 + 실제 AppTest. 세션 원장과 기존 UI/캐시 보존."""

import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from streamlit.testing.v1 import AppTest

from llm.config import OpenAIConfig
from services.market_data import load_market_data
from test_app import APP_PATH, full_fixture_frame
from test_portfolio import NOW


def fresh_frame():
    data=full_fixture_frame()
    delta=NOW.floor('h')-data.attrs['actual_end']
    data['timestamp']+=delta
    data.attrs={key:value+delta if isinstance(value,pd.Timestamp) else value for key,value in data.attrs.items()}
    data.attrs['fetched_at']=NOW
    return data


class PortfolioUITests(unittest.TestCase):
    def setUp(self):
        load_market_data.clear()
        self.folder=TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.db_path=Path(self.folder.name)/'test.sqlite3'
        patch('services.local_store.default_store_path',return_value=self.db_path).start()
        self.fetch=patch('services.market_data.fetch_binance_ohlcv',return_value=fresh_frame()).start()
        patch('ui.portfolio_view.portfolio_now',return_value=NOW).start()
        patch('ui.ai_analysis_view.read_openai_config',return_value=OpenAIConfig()).start()
        self.sdk=patch('openai.OpenAI').start()
        self.addCleanup(patch.stopall)

    def tearDown(self):
        self.sdk.assert_not_called()
        load_market_data.clear()

    def app(self):
        app=AppTest.from_file(str(APP_PATH),default_timeout=40).run()
        self.assertFalse(app.exception)
        return app

    def ready(self):
        app=self.app()
        app.button(key='pf_create').click().run()
        app.button(key='pf_load_prices').click().run()
        self.assertFalse(app.exception)
        return app

    def buy(self,app):
        app.checkbox(key='pf_trade_confirm').check()
        app.button(key='pf_trade').click().run()
        self.assertFalse(app.exception)

    def test_no_account_no_extra_fetch_or_price_chart(self):
        app=self.app()
        self.assertNotIn('_portfolio_account',app.session_state)
        self.assertEqual(self.fetch.call_count,1)
        self.assertEqual(len(app.get('plotly_chart')),1)

    def test_manual_buy_rerun_and_display_toggle_never_duplicate(self):
        app=self.ready(); self.buy(app)
        account=app.session_state['_portfolio_account']
        self.assertEqual(len(account.trades),1)
        app.checkbox(key='show_sma').check().run()
        app.run()
        self.assertEqual(app.session_state['_portfolio_account'],account)
        self.assertEqual(self.fetch.call_count,2)
        self.assertFalse(app.error)

    def test_sell_and_bad_quantity_preserve_cash(self):
        app=self.ready(); self.buy(app)
        app.selectbox(key='pf_side').select('SELL')
        app.number_input(key='pf_quantity').set_value(999.)
        app.button(key='pf_trade').click().run()
        self.assertEqual(len(app.session_state['_portfolio_account'].trades),1)
        self.assertTrue(any('가상 거래' in e.value for e in app.error))
        app.number_input(key='pf_quantity').set_value(.005)
        app.button(key='pf_trade').click().run()
        self.assertEqual(len(app.session_state['_portfolio_account'].trades),2)

    def test_missing_price_and_reset_require_explicit_user_action(self):
        app=self.app(); app.button(key='pf_create').click().run()
        original=app.session_state['_portfolio_account']
        self.buy(app)
        self.assertEqual(app.session_state['_portfolio_account'],original)
        app.number_input(key='pf_capital').set_value(2000.).run()
        app.button(key='pf_create').click().run()
        self.assertEqual(app.session_state['_portfolio_account'],original)
        app.checkbox(key='pf_reset_confirm').check().run()
        app.button(key='pf_create').click().run()
        self.assertEqual(app.session_state['_portfolio_account'].initial_capital,2000.)

    def test_price_failure_retains_ledger_and_marks_total_unavailable(self):
        app=self.ready(); self.buy(app)
        old=app.session_state['_portfolio_account']
        with patch('ui.portfolio_view.load_portfolio_prices',side_effect=RuntimeError('private')):
            app.button(key='pf_load_prices').click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state['_portfolio_account'],old)
        self.assertEqual(app.session_state['_portfolio_valuation'].status,'UNAVAILABLE')
        self.assertFalse(any('private' in e.value for e in app.error))
