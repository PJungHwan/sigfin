"""실 보유 원장 충격·집중도·이력 손계산. 외부 API 없음."""

import unittest
from unittest.mock import patch
import math
import pandas as pd

from portfolio.engine import create_account,execute_trade,evaluate
from portfolio.models import Quote
from risk.portfolio import PortfolioRiskSettings,evaluate_portfolio_risk,equity_history_risk
from services.portfolio_risk_service import analyze_portfolio_risk,portfolio_risk_signature
from test_portfolio import NOW,quote,account,trade
from test_portfolio_ui import fresh_frame


class PortfolioRiskTests(unittest.TestCase):
    def test_cash_only_has_no_scenario_loss_and_weights_sum_one(self):
        result=evaluate_portfolio_risk(account(),{},now=NOW)
        self.assertEqual(result.weights,{'USDT':1.,'BTC':0.,'ETH':0.})
        self.assertEqual(result.scenario['loss_from_current'],0.)
        self.assertEqual(result.trading_action,'REVIEW')

    def test_two_assets_weights_and_different_shocks(self):
        a=trade(account(),quantity=2.,price=100.)
        a=execute_trade(a,'ETH/USDT','BUY',1.,quote(200.).model_copy(update={'symbol':'ETH/USDT'}),fee_rate=0.,slippage_rate=0.,now=NOW)
        settings=PortfolioRiskSettings(btc_drop=.1,eth_drop=.2,exit_fee_rate=0.,exit_slippage_rate=0.)
        result=evaluate_portfolio_risk(a,{'BTC/USDT':quote(),'ETH/USDT':quote(200.).model_copy(update={'symbol':'ETH/USDT'})},settings,now=NOW)
        self.assertAlmostEqual(sum(result.weights.values()),1.)
        self.assertAlmostEqual(result.scenario['loss_from_current'],60.)
        self.assertAlmostEqual(result.scenario['loss_pct_initial'],6.)

    def test_fees_not_charged_twice_and_initial_pnl_distinct(self):
        a=trade(account())
        settings=PortfolioRiskSettings(btc_drop=.1,exit_fee_rate=.01,exit_slippage_rate=.02)
        result=evaluate_portfolio_risk(a,{'BTC/USDT':quote()},settings,now=NOW)
        # 매수 2*100, 수수료 2 => 현금798, 현재998.
        # 하락 후180, 매도순액180*.98*.99=174.636.
        self.assertAlmostEqual(result.scenario['shocked_equity'],972.636)
        self.assertAlmostEqual(result.scenario['exit_cost'],5.364)
        self.assertAlmostEqual(result.scenario['loss_from_current'],25.364)
        self.assertAlmostEqual(result.scenario['net_change_from_initial'],-27.364)
        self.assertAlmostEqual(result.scenario['previous_paid_fees'],2.)

    def test_concentration_boundary_and_cash_not_risky_asset(self):
        a=execute_trade(account(),'BTC/USDT','BUY',6.,quote(),fee_rate=0.,slippage_rate=0.,now=NOW)
        result=evaluate_portfolio_risk(a,{'BTC/USDT':quote()},PortfolioRiskSettings(concentration_limit=.6),now=NOW)
        self.assertEqual(result.concentrated_assets,[])
        result=evaluate_portfolio_risk(a,{'BTC/USDT':quote()},PortfolioRiskSettings(concentration_limit=.59),now=NOW)
        self.assertEqual(result.concentrated_assets,['BTC'])

    def test_loss_limit_boundary(self):
        a=execute_trade(account(),'BTC/USDT','BUY',10.,quote(),fee_rate=0.,slippage_rate=0.,now=NOW)
        settings=PortfolioRiskSettings(allowed_loss_rate=.1,btc_drop=.1,exit_fee_rate=0.,exit_slippage_rate=0.)
        exact=evaluate_portfolio_risk(a,{'BTC/USDT':quote()},settings,now=NOW)
        self.assertFalse(exact.risk_limit_exceeded)
        changed=evaluate_portfolio_risk(a,{'BTC/USDT':quote()},settings.model_copy(update={'btc_drop':.11}),now=NOW)
        self.assertTrue(changed.risk_limit_exceeded)
        self.assertEqual(changed.trading_action,'ABSTAIN')

    def test_missing_future_and_mismatched_prices_are_unknown(self):
        a=trade(account())
        for quotes in ({},{'BTC/USDT':quote().model_copy(update={'as_of_utc':(NOW+pd.Timedelta('1h')).isoformat()})}):
            with self.subTest(quotes=quotes):
                result=evaluate_portfolio_risk(a,quotes,now=NOW)
                self.assertEqual(result.status,'UNKNOWN'); self.assertIsNone(result.scenario)
                self.assertEqual(result.weights,{})

    def test_invalid_settings_rejected(self):
        for key,value in (('btc_drop',-1.),('eth_drop',1.1),('allowed_loss_rate',True),('exit_fee_rate',float('nan'))):
            with self.subTest(key=key),self.assertRaises(ValueError): PortfolioRiskSettings(**{key:value})

    def historical(self):
        a=create_account(1000.,now=NOW-pd.Timedelta('1D'))
        def q(price,time):
            return Quote(symbol='BTC/USDT',price=float(price),as_of_utc=time.floor('h').isoformat(),fetched_at_utc=time.isoformat(),timeframe='1h')
        entry=NOW-pd.Timedelta('3h')
        a=execute_trade(a,'BTC/USDT','BUY',10.,q(100.,entry),fee_rate=0.,slippage_rate=0.,now=entry)
        history=[evaluate(a,{'BTC/USDT':q(p,NOW-pd.Timedelta(hours=2-i))},now=NOW-pd.Timedelta(hours=2-i)) for i,p in enumerate((100.,110.,99.))]
        return a,history

    def test_real_equity_mdd_and_sample_volatility(self):
        a,history=self.historical()
        result=equity_history_risk(a,history,now=NOW,interval_seconds=3600.)
        self.assertAlmostEqual(result.mdd_pct,10.)
        self.assertAlmostEqual(result.volatility_per_observation_pct,math.sqrt(.02)*100)
        self.assertEqual(result.interval_seconds,3600.)

    def test_insufficient_and_irregular_history_na(self):
        a,history=self.historical()
        self.assertIsNone(equity_history_risk(a,history[:1],now=NOW,interval_seconds=3600.).mdd_pct)
        history[1]=history[1].model_copy(update={'evaluated_at_utc':(NOW-pd.Timedelta('30m')).isoformat()})
        result=equity_history_risk(a,history,now=NOW,interval_seconds=3600.)
        self.assertIsNone(result.volatility_per_observation_pct)
        self.assertAlmostEqual(result.mdd_pct,10.)

    def test_pre_account_future_and_cross_account_history_rejected(self):
        a,history=self.historical()
        for updates in ({'account_id':'another'},{'evaluated_at_utc':(NOW+pd.Timedelta('1h')).isoformat()},
                        {'evaluated_at_utc':(NOW-pd.Timedelta('2D')).isoformat()}):
            with self.subTest(updates=updates):
                result=equity_history_risk(a,[history[0].model_copy(update=updates)],now=NOW,interval_seconds=3600.)
                self.assertEqual(result.observations,0); self.assertIsNone(result.mdd_pct)

    def test_reuses_actual_mock_risk_node_and_does_not_mutate(self):
        frame=fresh_frame(); a=trade(account()); original=a.model_dump_json()
        with patch('openai.OpenAI') as sdk:
            result,logs=analyze_portfolio_risk(a,{'BTC/USDT':quote(float(frame.close.iloc[-1]))},PortfolioRiskSettings(),{'BTC/USDT':frame},now=NOW)
        sdk.assert_not_called()
        self.assertIn('BTC/USDT',result.asset_risk)
        self.assertTrue(any(e['node']=='risk_manager' and e['status']=='COMPLETED' for e in logs[0]['nodes']))
        self.assertEqual(a.model_dump_json(),original)

    def test_failed_asset_risk_is_partial_not_success(self):
        frame=fresh_frame(); a=trade(account())
        with patch('agents.risk_manager.evaluate_risk',side_effect=ValueError('private')):
            result,_=analyze_portfolio_risk(a,{'BTC/USDT':quote(float(frame.close.iloc[-1]))},PortfolioRiskSettings(),{'BTC/USDT':frame},now=NOW)
        self.assertEqual(result.status,'PARTIAL'); self.assertEqual(result.trading_action,'ABSTAIN')

    def test_signature_covers_account_quotes_settings_and_history(self):
        a=account(); settings=PortfolioRiskSettings()
        original=portfolio_risk_signature(a,{},settings,{},[])
        self.assertNotEqual(original,portfolio_risk_signature(trade(a),{},settings,{},[]))
        self.assertNotEqual(original,portfolio_risk_signature(a,{},settings.model_copy(update={'btc_drop':.3}),{},[]))

    def test_point_zero_one_btc_ten_percent_shock_and_exit_cost(self):
        # 합성 기준가100,000, 0.01 BTC, 현금9,000. 실제 시세가 아니다.
        a=create_account(10000.,now=NOW-pd.Timedelta(minutes=5))
        a=execute_trade(a,'BTC/USDT','BUY',.01,quote(100000.),fee_rate=0.,slippage_rate=0.,now=NOW)
        quotes={'BTC/USDT':quote(100000.)}
        before=evaluate(a,quotes,now=NOW)
        for fee,slip,expected_loss in ((0.,0.,100.),(.001,.0005,101.34955)):
            with self.subTest(fee=fee,slippage=slip):
                settings=PortfolioRiskSettings(btc_drop=.1,exit_fee_rate=fee,exit_slippage_rate=slip)
                result=evaluate_portfolio_risk(a,quotes,settings,now=NOW)
                self.assertEqual(result.valuation,before)
                self.assertAlmostEqual(result.scenario['loss_from_current'],expected_loss)
                self.assertAlmostEqual(result.scenario['shocked_equity'],10000.-expected_loss)
                self.assertAlmostEqual(result.scenario['exit_cost'],expected_loss-100.)
                # 계좌 이력이 없어도 현재 가격의 충격 계산을 없애지 않는다.
                self.assertIsNone(result.history.mdd_pct)
                self.assertIsNone(result.history.volatility_per_observation_pct)

    def test_signature_tracks_validity_without_invalidating_each_timestamp(self):
        a=trade(account()); quotes={'BTC/USDT':quote()}; settings=PortfolioRiskSettings()
        valid=evaluate(a,quotes,now=NOW)
        still_valid=evaluate(a,quotes,now=NOW+pd.Timedelta(minutes=1))
        expired=evaluate(a,quotes,now=NOW+pd.Timedelta(hours=3))
        first=portfolio_risk_signature(a,quotes,settings,{},[],valuation=valid)
        self.assertEqual(first,portfolio_risk_signature(a,quotes,settings,{},[],valuation=still_valid))
        self.assertNotEqual(first,portfolio_risk_signature(a,quotes,settings,{},[],valuation=expired))
