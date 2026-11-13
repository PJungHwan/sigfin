"""손계산 가능한 합성 가격/가상 원장 검증. 외부 API 없음."""

import unittest
from copy import deepcopy

import pandas as pd
from pydantic import ValidationError

from portfolio.models import Quote, PortfolioError
from portfolio.engine import create_account, execute_trade, replay, evaluate
from services.portfolio_service import quotes_from_frames

NOW = pd.Timestamp("2026-11-05T12:05:00Z")


def quote(price=100., symbol="BTC/USDT", as_of="2026-11-05T12:00:00Z"):
    return Quote(symbol=symbol, price=price, as_of_utc=as_of, fetched_at_utc="2026-11-05T12:01:00Z", timeframe="1h")


def account(capital=1000.):
    return create_account(capital, now=NOW-pd.Timedelta(minutes=5))


def trade(book, side="BUY", quantity=2., price=100., symbol="BTC/USDT", **kwargs):
    return execute_trade(book,symbol,side,quantity,quote(price,symbol),now=NOW,fee_rate=kwargs.pop('fee_rate', .01),slippage_rate=kwargs.pop('slippage_rate',0.),**kwargs)


class PortfolioTests(unittest.TestCase):
    def test_hand_calculated_buy_partial_sell_and_mark(self):
        original=account(); before=deepcopy(original)
        result=trade(trade(original),"SELL",1.,110.)
        marked=evaluate(result,{"BTC/USDT":quote(120.)},now=NOW)
        self.assertAlmostEqual(marked.cash,906.9)
        self.assertAlmostEqual(marked.realized_pnl,6.9)
        self.assertAlmostEqual(marked.unrealized_pnl,20.)
        self.assertAlmostEqual(marked.total_equity,1026.9)
        self.assertAlmostEqual(marked.total_fees,3.1)
        self.assertEqual(original,before)

    def test_weighted_average_and_full_exit_reentry(self):
        result=trade(trade(account(),quantity=1.),quantity=3.,price=200.)
        self.assertEqual(replay(result).holdings[0].average_price,175.)
        closed=trade(result,"SELL",4.,200.)
        self.assertIsNone(replay(closed).holdings[0].average_price)
        self.assertEqual(replay(trade(closed,quantity=1.,price=90.)).holdings[0].average_price,90.)

    def test_cash_only_zero_unrealized_and_default_capital(self):
        a=create_account(now=NOW)
        v=evaluate(a,{},now=NOW)
        self.assertEqual((v.cash,v.total_equity,v.unrealized_pnl),(10000.,10000.,0.))

    def test_both_assets_accounting_and_adverse_cost(self):
        result=trade(account(),quantity=1.,slippage_rate=.01)
        result=trade(result,quantity=2.,symbol="ETH/USDT",price=50.)
        v=evaluate(result,{"BTC/USDT":quote(110.),"ETH/USDT":quote(55.,"ETH/USDT")},now=NOW)
        self.assertAlmostEqual(replay(result).cash,796.99)
        self.assertAlmostEqual(v.total_equity,1016.99)
        self.assertAlmostEqual(v.total_equity,1000+v.realized_pnl+v.unrealized_pnl)

    def test_insufficient_cash_and_excess_sell_leave_input_unchanged(self):
        a=trade(account()); before=deepcopy(a)
        for side,q in (("BUY",100.),("SELL",3.)):
            with self.subTest(side=side), self.assertRaises(PortfolioError): trade(a,side,q)
        self.assertEqual(a,before)

    def test_invalid_inputs(self):
        for q in (0.,-1.,True,float('nan'),float('inf'),"1"):
            with self.subTest(q=q),self.assertRaises((ValueError,ValidationError)): trade(account(),quantity=q)
        for kwargs in ({"fee_rate":-1.},{"slippage_rate":1.}):
            with self.assertRaises(ValueError): trade(account(),**kwargs)

    def test_duplicate_identical_is_idempotent_conflicting_is_rejected(self):
        a=trade(account(),event_id="same")
        self.assertEqual(trade(a,event_id="same"),a)
        with self.assertRaises(PortfolioError): trade(a,event_id="same",quantity=1.)

    def test_missing_price_never_fabricates_total(self):
        v=evaluate(trade(account()),{},now=NOW)
        self.assertIsNone(v.total_equity)
        self.assertIsNone(v.unrealized_pnl)
        self.assertEqual(v.status,"UNAVAILABLE")

    def test_mismatched_future_and_stale_prices(self):
        a=trade(trade(account(),quantity=1.),quantity=1.,symbol="ETH/USDT")
        qs={"BTC/USDT":quote(),"ETH/USDT":quote(symbol="ETH/USDT",as_of="2026-11-05T11:00:00Z")}
        self.assertEqual(evaluate(a,qs,now=NOW).status,"UNAVAILABLE")
        for stamp in ("2026-11-06T12:00:00Z","2026-11-04T12:00:00Z","2026-11-05T11:30:00Z"):
            with self.assertRaises(PortfolioError):
                execute_trade(account(),"BTC/USDT","BUY",1.,quote(as_of=stamp),now=NOW)

    def test_tampered_ledger_and_future_evaluation_are_rejected(self):
        a=trade(account())
        a.trades[0].symbol="ETH/USDT"
        with self.assertRaises(PortfolioError): replay(a)
        with self.assertRaises(PortfolioError): evaluate(trade(account()),{},now=NOW-pd.Timedelta(days=1))

    def test_quotes_align_common_confirmed_end_and_preserve_frames(self):
        frame=pd.DataFrame({'timestamp':pd.date_range('2026-11-05T10:00Z',periods=3,freq='h'),'close':[90.,100.,999.]})
        frame.attrs={'timeframe':'1h','fetched_at':NOW}
        before=frame.copy(deep=True)
        quotes=quotes_from_frames({'BTC/USDT':frame,'ETH/USDT':frame.iloc[:2]},now=NOW)
        self.assertEqual(quotes['BTC/USDT'].price,100.)
        self.assertEqual(quotes['BTC/USDT'].as_of_utc,quotes['ETH/USDT'].as_of_utc)
        pd.testing.assert_frame_equal(frame,before)
