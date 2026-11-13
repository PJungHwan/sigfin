"""포트폴리오 위험 버튼/무효화/기존 화면과 API 미호출."""

import unittest
from unittest.mock import patch
import pandas as pd
from test_portfolio import NOW
import test_portfolio_ui as fixtures
import ui.portfolio_risk_view as risk_view
from risk.portfolio import evaluate_portfolio_risk


class PortfolioRiskUITests(unittest.TestCase):
    setUp=fixtures.PortfolioUITests.setUp
    tearDown=fixtures.PortfolioUITests.tearDown
    app=fixtures.PortfolioUITests.app
    ready=fixtures.PortfolioUITests.ready
    buy=fixtures.PortfolioUITests.buy

    def test_no_account_no_result(self):
        app=self.app()
        self.assertNotIn('_portfolio_risk',app.session_state)
        self.assertEqual(len(app.get('plotly_chart')),1)

    def test_run_with_holdings_and_no_extra_api(self):
        app=self.ready(); self.buy(app); count=self.fetch.call_count
        app.button(key='run_portfolio_risk').click().run()
        self.assertFalse(app.exception)
        self.assertIn('BTC/USDT',app.session_state['_portfolio_risk'].asset_risk)
        self.assertEqual(self.fetch.call_count,count)

    def test_settings_and_trade_invalidate(self):
        app=self.ready(); self.buy(app)
        app.button(key='run_portfolio_risk').click().run()
        app.number_input(key='pr_btc_drop').set_value(30.).run()
        self.assertNotIn('_portfolio_risk',app.session_state)
        app.button(key='run_portfolio_risk').click().run()
        self.buy(app)
        self.assertNotIn('_portfolio_risk',app.session_state)

    def test_no_quotes_unknown_no_fabricated_risk(self):
        app=self.ready(); self.buy(app)
        app.session_state['_portfolio_frames']={}
        app.button(key='run_portfolio_risk').click().run()
        result=app.session_state['_portfolio_risk']
        self.assertEqual(result.status,'UNKNOWN'); self.assertIsNone(result.scenario)

    def test_price_validity_recovery_invalidates_previous_unknown(self):
        app=self.ready(); self.buy(app)
        frames={symbol:frame.copy(deep=True) for symbol,frame in app.session_state['_portfolio_frames'].items()}
        for frame in frames.values(): frame.attrs['fetched_at']=NOW+pd.Timedelta(minutes=1)
        app.session_state['_portfolio_frames']=frames
        app.button(key='run_portfolio_risk').click().run()
        self.assertEqual(app.session_state['_portfolio_risk'].status,'UNKNOWN')
        with patch('ui.portfolio_view.portfolio_now',return_value=NOW+pd.Timedelta(minutes=2)):
            app.run()
            self.assertEqual(app.session_state['_portfolio_valuation'].status,'COMPLETE')
            self.assertFalse('_portfolio_risk' in app.session_state)
            app.button(key='run_portfolio_risk').click().run()
            self.assertIsNotNone(app.session_state['_portfolio_risk'].scenario)

    def test_expired_prices_invalidate_previous_success(self):
        app=self.ready(); self.buy(app)
        app.button(key='run_portfolio_risk').click().run()
        self.assertEqual(app.session_state['_portfolio_risk'].status,'COMPLETE')
        with patch('ui.portfolio_view.portfolio_now',return_value=NOW+pd.Timedelta(hours=3)):
            app.run()
            self.assertEqual(app.session_state['_portfolio_valuation'].status,'UNAVAILABLE')
            self.assertFalse('_portfolio_risk' in app.session_state)
            app.button(key='run_portfolio_risk').click().run()
            self.assertEqual(app.session_state['_portfolio_risk'].status,'UNKNOWN')
            self.assertIsNone(app.session_state['_portfolio_risk'].scenario)

    def test_same_price_and_timestamp_with_ten_percent_shock(self):
        app=self.ready(); self.buy(app)
        app.number_input(key='pr_btc_drop').set_value(10.)
        app.number_input(key='pr_fee').set_value(0.)
        app.number_input(key='pr_slippage').set_value(0.)
        count=self.fetch.call_count
        app.button(key='run_portfolio_risk').click().run()
        self.assertFalse(app.exception)
        current=app.session_state['_portfolio_valuation']
        report=app.session_state['_portfolio_risk']
        self.assertEqual(report.valuation,current)
        self.assertEqual(report.valuation.evaluated_at_utc,current.evaluated_at_utc)
        self.assertEqual(report.status,'COMPLETE')
        btc=next(p for p in current.positions if p['symbol']=='BTC/USDT')
        self.assertAlmostEqual(btc['quantity'],.01)
        self.assertAlmostEqual(report.scenario['loss_from_current'],btc['quantity']*btc['price']*.1)
        self.assertEqual(self.fetch.call_count,count)
        self.assertTrue(any('가상 계좌와 동일한 확정 가격' in c.value for c in app.caption))
        app.run()
        self.assertEqual(app.session_state['_portfolio_risk'],report)

    def test_app_uses_explicit_keyword_context_contract(self):
        with patch.object(risk_view,'render_portfolio_risk',wraps=risk_view.render_portfolio_risk) as render:
            app=self.ready(); self.buy(app)
        self.assertFalse(app.exception)
        self.assertEqual(render.call_args.args,())
        context=render.call_args.kwargs['price_context']
        self.assertEqual(context.valuation,app.session_state['_portfolio_valuation'])
        self.assertIn('BTC/USDT',context.quotes)

    def test_current_price_cannot_reuse_inconsistent_cached_valuation(self):
        app=self.ready(); self.buy(app)
        app.button(key='run_portfolio_risk').click().run()
        current=app.session_state['_portfolio_risk']
        unavailable=evaluate_portfolio_risk(app.session_state['_portfolio_account'],{},current.settings,now=NOW)
        # 기존 서버/세션에 남은 결과의 가격 입력과 현재 입력이 다른 경우.
        app.session_state['_portfolio_risk']=unavailable
        app.run()
        self.assertEqual(app.session_state['_portfolio_valuation'].status,'COMPLETE')
        self.assertFalse('_portfolio_risk' in app.session_state)

    def test_displayed_complete_flag_cannot_bypass_quote_revalidation(self):
        app=self.ready(); self.buy(app)
        displayed=app.session_state['_portfolio_valuation']
        frames={key:frame.copy(deep=True) for key,frame in app.session_state['_portfolio_frames'].items()}
        for frame in frames.values(): frame.attrs['fetched_at']=NOW+pd.Timedelta(hours=1)
        app.session_state['_portfolio_frames']=frames
        with patch('ui.portfolio_view.evaluate',return_value=displayed),patch.object(risk_view,'analyze_portfolio_risk') as run:
            app.button(key='run_portfolio_risk').click().run()
        self.assertFalse(app.exception)
        run.assert_not_called()
        self.assertFalse('_portfolio_risk' in app.session_state)
        self.assertTrue(any('재검증' in error.value for error in app.error))
