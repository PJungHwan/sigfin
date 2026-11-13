"""포트폴리오 노출·충격 가정과 단일 자산 Risk를 구분한다."""

import pandas as pd
import streamlit as st

from risk.portfolio import PortfolioRiskSettings
from config.settings import TIMEFRAME_MILLISECONDS
from portfolio.engine import evaluate
from services.portfolio_service import PortfolioPriceContext
from services.portfolio_risk_service import analyze_portfolio_risk,portfolio_risk_signature
from ui.components import metric_card


def render_portfolio_risk(*, price_context: PortfolioPriceContext | None = None):
    account=st.session_state.get('_portfolio_account')
    with st.container(border=True):
        st.subheader('포트폴리오 위험 분석 · 연구용')
        if account is None:
            st.caption('가상 계좌 생성 후 보유량 기준 위험을 분석할 수 있습니다.')
            st.session_state.pop('_portfolio_risk',None)
            return
        if (price_context is None or price_context.valuation.account_id!=account.account_id
                or price_context.valuation.account_revision!=len(account.trades)):
            st.session_state.pop('_portfolio_risk',None)
            st.session_state.pop('_portfolio_risk_logs',None)
            st.error('현재 계좌의 검증된 가격 입력이 전달되지 않았습니다. 가상 계좌의 확정 가격을 다시 조회하세요.')
            return
        # STEP 14의 표시 상태를 신뢰해 검사를 생략하지 않는다.
        # 동일 원본 가격/UTC 시각으로 순수 평가 엔진을 다시 실행한다.
        try:
            valuation=evaluate(account,price_context.quotes,now=price_context.valuation.evaluated_at_utc)
            if valuation!=price_context.valuation:
                raise ValueError('Portfolio valuation/source mismatch')
        except (ValueError,TypeError,KeyError):
            st.session_state.pop('_portfolio_risk',None)
            st.session_state.pop('_portfolio_risk_logs',None)
            st.error('전달된 가격·시각·계좌 평가의 재검증에 실패했습니다. 확정 가격을 다시 조회하세요.')
            return
        with st.expander('집중도·가격 하락 시나리오 설정',expanded=False):
            concentration=st.number_input('자산 집중 한도 (%)',min_value=1.,max_value=100.,value=60.,key='pr_concentration', persist_state="session")
            allowed=st.number_input('초기금 대비 허용 손실 (%)',min_value=0.,max_value=100.,value=1.,key='pr_allowed', persist_state="session")
            btc=st.number_input('BTC 가정 하락 (%)',min_value=0.,max_value=100.,value=20.,key='pr_btc_drop', persist_state="session")
            eth=st.number_input('ETH 가정 하락 (%)',min_value=0.,max_value=100.,value=20.,key='pr_eth_drop', persist_state="session")
            fee=st.number_input('가정 매도 수수료 (%)',min_value=0.,max_value=5.,value=.1,key='pr_fee', persist_state="session")
            slip=st.number_input('가정 매도 슬리피지 (%)',min_value=0.,max_value=10.,value=.05,key='pr_slippage', persist_state="session")
        settings=PortfolioRiskSettings(concentration_limit=concentration/100,allowed_loss_rate=allowed/100,
            btc_drop=btc/100,eth_drop=eth/100,exit_fee_rate=fee/100,exit_slippage_rate=slip/100)
        frames=price_context.frames; quotes=price_context.quotes
        history=st.session_state.get('_portfolio_history',[])
        evaluated_at=valuation.evaluated_at_utc
        signature=portfolio_risk_signature(account,quotes,settings,frames,history,valuation=valuation)
        if st.session_state.get('_portfolio_risk_signature')!=signature:
            if '_portfolio_risk' in st.session_state: st.info('원장·가격·가정·평가 이력이 변경되어 이전 위험 결과를 무효화했습니다.')
            st.session_state.pop('_portfolio_risk',None)
            st.session_state.pop('_portfolio_risk_logs',None)
            st.session_state['_portfolio_risk_signature']=signature
        cached=st.session_state.get('_portfolio_risk')
        if cached is not None and (
                cached.account_id!=account.account_id or cached.revision!=len(account.trades)
                or cached.valuation.model_dump(exclude={'evaluated_at_utc'})!=valuation.model_dump(exclude={'evaluated_at_utc'})):
            # 이전 서버/코드에서 남은 결과도 현재 가격 입력과 다르면 표시하지 않는다.
            st.session_state.pop('_portfolio_risk',None)
            st.session_state.pop('_portfolio_risk_logs',None)
            st.info('보관된 위험 결과의 가격·평가가 현재 검증 입력과 달라 무효화했습니다. 다시 실행해 주세요.')
        with st.expander('가격 입력·유효성 확인',expanded=False):
            st.caption(f'가상 계좌와 동일한 확정 가격·검증 시각 사용 · {evaluated_at} · 계좌 평가 {valuation.status}')
            st.caption('UTC 봉 마감 경계 · 봉 마감 ≤ 수집 ≤ 검증 시각 · 최근 2봉 이내 조건을 유지합니다.')
            rows=[]
            current=pd.Timestamp(evaluated_at)
            for position in valuation.positions:
                if not position['quantity']: continue
                quote=quotes.get(position['symbol'])
                row={'종목':position['symbol'],'가격 입력':'누락' if quote is None else '있음','평가 가격':position['price']}
                if quote is not None:
                    step=pd.Timedelta(milliseconds=TIMEFRAME_MILLISECONDS[quote.timeframe])
                    observed,fetched=map(pd.Timestamp,(quote.as_of_utc,quote.fetched_at_utc))
                    row.update({'봉':quote.timeframe,'봉 마감 UTC':quote.as_of_utc,'수집 UTC':quote.fetched_at_utc,
                        '봉 경계 일치':observed==observed.floor(step),'마감·수집·검증 순서':observed<=fetched<=current,
                        '최근 2봉 이내':pd.Timedelta(0)<=current-observed<=2*step})
                rows.append(row)
            st.dataframe(pd.DataFrame(rows),hide_index=True,width='stretch')
            for issue in valuation.issues: st.caption(issue)
        if st.button('포트폴리오 위험 분석 실행',key='run_portfolio_risk'):
            st.session_state.pop('_portfolio_risk',None)
            try:
                report,logs=analyze_portfolio_risk(account,quotes,settings,frames,now=evaluated_at,history=history)
                if report.valuation!=valuation:
                    raise ValueError('STEP 14/16 valuation mismatch')
                st.session_state['_portfolio_risk']=report
                st.session_state['_portfolio_risk_logs']=logs
            except (ValueError,TypeError,KeyError):
                st.error('포트폴리오 위험 계산 검증에 실패했습니다. 원장과 이전 저장 자료는 유지합니다.')
        report=st.session_state.get('_portfolio_risk')
        if report is None:
            st.caption('아직 실행하지 않았습니다. 추가 시장/API 조회 없이 현재 포트폴리오 가격 자료를 재사용합니다.')
            return
        metric_card('포트폴리오 연구 상태',report.trading_action,report.status)
        st.caption('평가 마감 UTC: '+str(report.valuation.data_as_of_utc)+' · 보유량 기준이며 실제 투자 추천이 아닙니다.')
        if report.weights:
            st.dataframe(pd.DataFrame([{'asset':k,'weight_pct':v*100} for k,v in report.weights.items()]),hide_index=True,width='stretch')
        else: st.warning('UNKNOWN · 유효한 공통 확정 가격이 없어 비중/충격 평가 N/A')
        if report.scenario:
            s=report.scenario
            metric_card('가정 손실 / 매도 비용',f"{s['loss_from_current']:,.4f} / {s['exit_cost']:,.4f} USDT",
                        f"초기금 대비 손실 {s['loss_pct_initial']:.4f}% · 허용 손실 {s['allowed_loss']:,.4f} USDT")
            with st.expander('충격 시나리오 원본 (개발자용)',expanded=False):
                st.json(s,expanded=False)
        for reason in report.reasons: st.warning(reason)
        h=report.history
        metric_card('실제 저장 평가 이력 MDD / 변동성',
            ('N/A' if h.mdd_pct is None else f'{h.mdd_pct:.4f}%')+' / '+
            ('N/A' if h.volatility_per_observation_pct is None else f'{h.volatility_per_observation_pct:.4f}%'),
            f'{h.observations}점 · 관측별 표본 표준편차 · 연율화 없음')
        for issue in h.issues: st.caption(issue)
        with st.expander('단일 자산 Mock Risk Manager · 계산 근거/로그'):
            st.caption('기존 원금 전체 투입·양방향 정적 시나리오는 현재 보유량의 충격 손실과 별개입니다.')
            for symbol,risk in report.asset_risk.items():
                st.text(f'{symbol}: {risk.risk_level} · {risk.validation_status}')
                st.json(risk.model_dump(mode='json'))
            st.json(st.session_state.get('_portfolio_risk_logs',[]))
        with st.expander('포트폴리오 위험 분석 한계'):
            for limitation in report.limitations: st.text(limitation)
