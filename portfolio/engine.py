"""FIFO/실제 체결을 추정하지 않는 이동평균 원가·계좌 회계. 모든 지급 수수료는 실현 손익."""

from math import isclose, isfinite
from uuid import uuid4

import pandas as pd

from backtest.risk import adverse_fill_price
from config.settings import TIMEFRAME_MILLISECONDS
from portfolio.models import Account, Trade, Quote, Book, Holding, Valuation, PortfolioError, SYMBOLS, utc


def create_account(capital=10000., *, name="SIGFIN 가상 계좌", now=None):
    return Account(account_id=uuid4().hex, name=name, initial_capital=capital,
                   created_at_utc=utc(now if now is not None else pd.Timestamp.now(tz="UTC")))


def validate_quote(quote, now):
    quote = Quote.model_validate(quote)
    current, observed, fetched = map(pd.Timestamp, (utc(now), quote.as_of_utc, quote.fetched_at_utc))
    interval = pd.Timedelta(milliseconds=TIMEFRAME_MILLISECONDS[quote.timeframe])
    if observed != observed.floor(interval) or observed > current or fetched > current or observed > fetched or current-observed > 2*interval:
        raise PortfolioError("가격이 미래·미확정·오래된 자료이거나 수집 시각이 유효하지 않습니다. 다시 조회하세요.")
    return quote


def replay(account):
    """이벤트가 유일한 원본. 캐시된 현금/수량을 신뢰하거나 입력을 직접 변경하지 않는다."""
    account = Account.model_validate(account)
    cash, realized, fees = account.initial_capital, 0., 0.
    amounts = {symbol: [0., 0.] for symbol in SYMBOLS}
    seen, previous = set(), pd.Timestamp(account.created_at_utc)
    for trade in account.trades:
        time = pd.Timestamp(trade.recorded_at_utc)
        if trade.event_id in seen or time < previous or trade.symbol != trade.quote.symbol:
            raise PortfolioError("거래 ID·순서·가격 종목 검증에 실패했습니다.")
        validate_quote(trade.quote, trade.recorded_at_utc)
        seen.add(trade.event_id)
        previous = time
        fill = adverse_fill_price(trade.quote.price, buying=trade.side == "BUY", slippage_rate=trade.slippage_rate)
        notional, fee = trade.quantity*fill, trade.quantity*fill*trade.fee_rate
        quantity, average = amounts[trade.symbol]
        if trade.side == "BUY":
            if notional+fee > cash:
                raise PortfolioError("수수료를 포함한 가상 현금이 부족합니다.")
            average = (quantity*average+notional)/(quantity+trade.quantity)
            quantity += trade.quantity
            cash -= notional+fee
            realized -= fee
        else:
            if trade.quantity > quantity:
                raise PortfolioError("보유 수량을 초과하여 매도할 수 없습니다.")
            quantity -= trade.quantity
            cash += notional-fee
            realized += trade.quantity*(fill-average)-fee
            if quantity == 0:
                average = 0.
        fees += fee
        amounts[trade.symbol] = [quantity, average]
        if not all(isfinite(value) for value in (cash, realized, fees, quantity, average)) or cash < 0:
            raise PortfolioError("원장 수치/회계의 유효 범위를 벗어났습니다.")
    return Book(cash=cash, realized_pnl=realized, total_fees=fees,
                holdings=[Holding(symbol=s, quantity=q, average_price=avg if q else None) for s, (q, avg) in amounts.items()])


def execute_trade(account, symbol, side, quantity, quote, *, fee_rate=.001, slippage_rate=.0005, event_id=None, now=None):
    account = Account.model_validate(account)
    stamp = utc(now if now is not None else pd.Timestamp.now(tz="UTC"))
    trade = Trade(event_id=event_id or uuid4().hex, symbol=symbol, side=side, quantity=quantity, quote=quote,
                  fee_rate=fee_rate, slippage_rate=slippage_rate, recorded_at_utc=stamp)
    if pd.Timestamp(stamp) < pd.Timestamp(account.created_at_utc):
        raise PortfolioError("계좌 생성 이전 거래는 기록할 수 없습니다.")
    for existing in account.trades:
        if existing.event_id == trade.event_id:
            if existing == trade:
                return account.model_copy(deep=True)
            raise PortfolioError("동일 거래 ID의 내용이 달라 중복 기록을 차단했습니다.")
    result = account.model_copy(update={"trades": [*account.trades, trade]}, deep=True)
    replay(result)
    return result


def evaluate(account, quotes, *, now=None):
    book = replay(account)
    current = utc(now if now is not None else pd.Timestamp.now(tz="UTC"))
    if pd.Timestamp(account.created_at_utc) > pd.Timestamp(current) or any(pd.Timestamp(t.recorded_at_utc)>pd.Timestamp(current) for t in account.trades):
        raise PortfolioError("미래 원장을 과거 시점에서 평가할 수 없습니다.")
    positions, issues, times = [], [], set()
    unrealized, equity = 0., book.cash
    for holding in book.holdings:
        row = holding.model_dump()
        row.update(market_value=0., unrealized_pnl=0., price=None)
        if holding.quantity:
            try:
                quote = validate_quote(quotes[holding.symbol], current)
                if quote.symbol != holding.symbol:
                    raise PortfolioError("평가 종목 불일치")
                times.add(quote.as_of_utc)
                value = holding.quantity*quote.price
                pnl = holding.quantity*(quote.price-holding.average_price)
                row.update(market_value=value, unrealized_pnl=pnl, price=quote.price)
                equity += value
                unrealized += pnl
            except (KeyError, ValueError, TypeError):
                row.update(market_value=None, unrealized_pnl=None)
                issues.append(holding.symbol+": 유효한 공통 확정 가격 부족")
        positions.append(row)
    if len(times)>1:
        issues.append("보유 자산의 평가 마감 시각이 다릅니다.")
    if not issues and not isclose(equity, account.initial_capital+book.realized_pnl+unrealized, abs_tol=1e-8, rel_tol=1e-10):
        raise PortfolioError("계좌 자산·실현/미실현 손익 회계가 일치하지 않습니다.")
    return Valuation(account_id=account.account_id, account_revision=len(account.trades), evaluated_at_utc=current,
        data_as_of_utc=next(iter(times)) if len(times)==1 else (current if not times and not issues else None),
        status="UNAVAILABLE" if issues else "COMPLETE", cash=book.cash, realized_pnl=book.realized_pnl,
        unrealized_pnl=None if issues else unrealized, total_equity=None if issues else equity,
        total_fees=book.total_fees, positions=positions, issues=issues)
