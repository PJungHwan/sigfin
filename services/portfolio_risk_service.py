"""이미 조회한 확정 봉과 기존 Mock Risk Manager만 재사용한다. 네트워크 없음."""

from hashlib import sha256

import pandas as pd

from config.settings import TIMEFRAME_MILLISECONDS
from graph.runner import run_workflow
from indicators.technical import add_technical_indicators,INDICATOR_COLUMNS
from portfolio.engine import replay
from risk.portfolio import evaluate_portfolio_risk
from services.research_snapshots import pack_frame,safe_json


def portfolio_risk_signature(account,quotes,settings,frames,history,*,valuation=None):
    payload={'account':account,'quotes':quotes,'settings':settings,'history':history,
             'frames':{k:pack_frame(v) for k,v in frames.items()},
             # 가격 수치/봉이 같아도 미래 수집 시각 회복·만료로 유효성은 바뀔 수 있다.
             # 매 재실행 시각 자체는 넣지 않아 정상 결과를 불필요하게 지우지 않는다.
             'price_validity':None if valuation is None else {'status':valuation.status,'issues':valuation.issues}}
    return sha256(safe_json(payload).encode()).hexdigest()


def analyze_portfolio_risk(account,quotes,settings,frames,*,now,history=()):
    reports={}; logs=[]
    for holding in replay(account).holdings:
        if not holding.quantity or holding.symbol not in frames or holding.symbol not in quotes: continue
        frame=frames[holding.symbol]; quote=quotes[holding.symbol]
        step=pd.Timedelta(milliseconds=TIMEFRAME_MILLISECONDS[quote.timeframe])
        visible=frame.loc[frame.timestamp+step<=pd.Timestamp(quote.as_of_utc)].copy()
        if visible.empty: continue
        if float(visible.close.iloc[-1])!=quote.price: raise ValueError('Portfolio quote/source price mismatch')
        if not set(INDICATOR_COLUMNS).issubset(visible.columns):
            visible=add_technical_indicators(visible,timeframe=quote.timeframe)
        visible.attrs['actual_end']=pd.Timestamp(quote.as_of_utc)
        result=run_workflow(visible,holding.symbol,quote.timeframe,mode='Mock Workflow',requested=True,
                            risk_enabled=True,now=now)
        logs.append({'symbol':holding.symbol,'run_id':result.state['run_id'],'status':result.state['execution_status'],
                     'nodes':[item.model_dump() for item in result.state['node_execution_logs']]})
        report=result.state.get('risk_result')
        if report is not None: reports[holding.symbol]=report
    interval=TIMEFRAME_MILLISECONDS[next(iter(quotes.values())).timeframe]/1000 if quotes else 3600.
    report=evaluate_portfolio_risk(account,quotes,settings,now=now,history=history,
                                   interval_seconds=interval,asset_risk=reports)
    return report,logs
