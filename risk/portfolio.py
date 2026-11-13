"""가상 보유 원장의 확정 가격 평가. 방향 예측·자동 주문을 생성하지 않는다."""

from math import isclose
from typing import Literal

import pandas as pd
from pydantic import Field, FiniteFloat

from backtest.metrics import maximum_drawdown
from portfolio.engine import evaluate
from portfolio.models import Account, Numbers, Valuation, utc
from llm.schemas import StrictModel
from risk.models import RiskReport


class PortfolioRiskSettings(Numbers):
    concentration_limit: FiniteFloat = Field(default=.6, gt=0, le=1)
    allowed_loss_rate: FiniteFloat = Field(default=.01, ge=0, le=1)
    btc_drop: FiniteFloat = Field(default=.2, ge=0, le=1)
    eth_drop: FiniteFloat = Field(default=.2, ge=0, le=1)
    exit_fee_rate: FiniteFloat = Field(default=.001, ge=0, lt=1)
    exit_slippage_rate: FiniteFloat = Field(default=.0005, ge=0, lt=1)


class EquityHistoryRisk(Numbers):
    observations: int
    start_utc: str | None
    end_utc: str | None
    mdd_pct: FiniteFloat | None
    volatility_per_observation_pct: FiniteFloat | None
    interval_seconds: FiniteFloat | None
    issues: list[str]


class PortfolioRiskReport(StrictModel):
    schema_version: Literal[1] = 1
    execution_mode: Literal['Python Portfolio Risk'] = 'Python Portfolio Risk'
    account_id: str
    revision: int
    valuation: Valuation
    settings: PortfolioRiskSettings
    status: Literal['COMPLETE','PARTIAL','UNKNOWN']
    weights: dict[str,FiniteFloat]
    concentrated_assets: list[str]
    scenario: dict[str,FiniteFloat] | None
    risk_limit_exceeded: bool
    history: EquityHistoryRisk
    asset_risk: dict[str,RiskReport]
    trading_action: Literal['REVIEW','HOLD','ABSTAIN']
    reasons: list[str]
    limitations: list[str]


def equity_history_risk(account, history, *, now, interval_seconds):
    """실제로 저장한 평가만 사용. MDD는 불규칙 관측도 허용, 표준편차는 등간격만."""
    issues=[]; values=[]; times=[]
    try:
        for raw in history:
            value=Valuation.model_validate(raw)
            time=pd.Timestamp(utc(value.evaluated_at_utc))
            if (value.account_id!=account.account_id or value.status!='COMPLETE' or value.total_equity is None
                    or not 0<=value.account_revision<=len(account.trades)
                    or time<pd.Timestamp(account.created_at_utc) or time>pd.Timestamp(utc(now))
                    or any(pd.Timestamp(t.recorded_at_utc)>time for t in account.trades[:value.account_revision])
                    or pd.Timestamp(utc(value.data_as_of_utc))>time
                    or not isclose(value.total_equity,account.initial_capital+value.realized_pnl+value.unrealized_pnl,rel_tol=1e-10,abs_tol=1e-8)):
                raise ValueError
            values.append(value.total_equity); times.append(time)
        if times!=sorted(set(times)): raise ValueError
    except (ValueError,TypeError):
        return EquityHistoryRisk(observations=0,start_utc=None,end_utc=None,mdd_pct=None,
            volatility_per_observation_pct=None,interval_seconds=None,issues=['평가 이력의 계좌·확정 시각·회계 검증 실패'])
    mdd=maximum_drawdown(pd.Series(values,dtype=float)) if len(values)>=2 else None
    vol=None
    if len(values)<3: issues.append('실제 평가 이력 부족: MDD 2점, 표준편차 3점 이상 필요')
    steps=pd.Series(times).diff().dropna().dt.total_seconds() if times else pd.Series(dtype=float)
    regular=len(steps)>0 and bool((abs(steps-interval_seconds)<=.001).all())
    if len(values)>=3:
        if not regular: issues.append('평가 관측 간격 불규칙/누락으로 변동성 N/A')
        elif min(values)<=0: issues.append('자산 0의 분모가 있어 변동성 N/A')
        else: vol=float(pd.Series(values).pct_change(fill_method=None).dropna().std(ddof=1)*100)
    return EquityHistoryRisk(observations=len(values),start_utc=times[0].isoformat() if times else None,
        end_utc=times[-1].isoformat() if times else None,mdd_pct=mdd,volatility_per_observation_pct=vol,
        interval_seconds=float(interval_seconds) if regular else None,issues=issues)


def evaluate_portfolio_risk(account,quotes,settings=None,*,now,history=(),interval_seconds=3600.,asset_risk=None):
    account=Account.model_validate(account)
    settings=PortfolioRiskSettings.model_validate(settings or PortfolioRiskSettings())
    if type(interval_seconds) not in (int,float) or not 0<interval_seconds<1e9: raise ValueError('Invalid observation interval')
    valuation=evaluate(account,quotes,now=now)
    historic=equity_history_risk(account,history,now=now,interval_seconds=interval_seconds)
    reports=asset_risk or {}; weights={}; concentrated=[]; scenario=None; reasons=[]; exceeded=False
    if valuation.status!='COMPLETE' or valuation.total_equity is None or valuation.total_equity<=0:
        status='UNKNOWN'; action='ABSTAIN'; reasons.extend(valuation.issues or ['평가 자산 부족'])
    else:
        equity=valuation.total_equity
        weights={'USDT':valuation.cash/equity}
        shocked=valuation.cash; shocked_gross=valuation.cash
        for p in valuation.positions:
            weights[p['symbol'].split('/')[0]]=p['market_value']/equity
            drop=settings.btc_drop if p['symbol']=='BTC/USDT' else settings.eth_drop
            gross=p['market_value']*(1-drop)
            shocked_gross+=gross
            shocked+=gross*(1-settings.exit_slippage_rate)*(1-settings.exit_fee_rate)
        if not isclose(sum(weights.values()),1.,abs_tol=1e-10): raise ValueError('Weight accounting mismatch')
        concentrated=[asset for asset,weight in weights.items() if asset!='USDT' and weight>settings.concentration_limit]
        loss=max(0.,equity-shocked); allowed=account.initial_capital*settings.allowed_loss_rate
        scenario={'current_equity':equity,'shocked_equity':shocked,'loss_from_current':loss,
            'loss_pct_initial':loss/account.initial_capital*100,'loss_pct_current':loss/equity*100,
            'exit_cost':shocked_gross-shocked,'previous_paid_fees':valuation.total_fees,
            'net_change_from_initial':shocked-account.initial_capital,'allowed_loss':allowed}
        held={p['symbol'] for p in valuation.positions if p['quantity']>0}
        if not set(reports).issubset(held): raise ValueError('Unexpected asset risk report')
        for symbol,report in reports.items():
            if report.symbol!=symbol or pd.Timestamp(report.data_as_of_utc)!=pd.Timestamp(valuation.data_as_of_utc):
                raise ValueError('Asset risk identity/time mismatch')
        missing=held-set(reports)
        insufficient=missing or any(r.risk_level=='UNKNOWN' for r in reports.values())
        status='PARTIAL' if insufficient else 'COMPLETE'
        if missing: reasons.append('자산 Risk 자료 부족: '+', '.join(sorted(missing)))
        if insufficient: reasons.append('단일 자산 Risk 평가 부족. 포트폴리오 방향·위험 등급을 생성하지 않음')
        exceeded=loss>allowed+max(1e-8,allowed*1e-10)
        if exceeded: reasons.append('보유 수량 기준 가정 손실이 계좌 허용 손실 초과')
        if concentrated: reasons.append('연구용 집중 한도 초과: '+', '.join(concentrated))
        action='ABSTAIN' if exceeded or insufficient else ('HOLD' if concentrated or any(r.risk_level in ('HIGH','CRITICAL') for r in reports.values()) else 'REVIEW')
    return PortfolioRiskReport(account_id=account.account_id,revision=len(account.trades),valuation=valuation,settings=settings,
        status=status,weights=weights,concentrated_assets=concentrated,scenario=scenario,risk_limit_exceeded=exceeded,history=historic,asset_risk=reports,
        trading_action=action,reasons=reasons,limitations=[
            '교육·연구용 Python 계산. REVIEW/HOLD/ABSTAIN은 투자 주문·추천이 아닙니다.',
            '가정 가격 하락 후 보유 전량 매도 시나리오. 기존 매수 비용은 현금·실현 손익에 반영돼 다시 차감하지 않고 매도 비용만 추가합니다.',
            '체결·손절·유동성을 보장하지 않습니다. 레버리지/공매도/강제청산은 없습니다.',
            '집중 한도·하락률은 사용자 연구 가정이며 확률·검증된 투자 기준이 아닙니다.',
            '이력은 계좌 생성 후 저장한 평가만 사용. 자산 MDD/ATR 가중평균을 계좌 지표로 사용하지 않고 변동성은 연율화하지 않습니다.',
            '단일 자산 Risk의 원금 전체 투입·가상 양방향 수량은 별도 가정이며 현재 가상 보유량과 구분합니다.',
            'FRED 관측일은 발표일이 아니며 과거 당시 매매 판단의 근거가 아닙니다.'])
