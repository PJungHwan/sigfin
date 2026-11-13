"""기존 대시보드에 추가하는 수동 가상 계좌. 평가/화면 재실행은 거래를 만들지 않는다."""

from uuid import uuid4
from contextlib import nullcontext

import pandas as pd
import streamlit as st

from backtest.risk import adverse_fill_price
from portfolio.engine import create_account, execute_trade, replay, evaluate
from portfolio.models import PortfolioError
from services.portfolio_service import load_portfolio_prices, quotes_from_frames, PortfolioPriceContext
from ui.components import metric_card


def portfolio_now():
    return pd.Timestamp.now(tz="UTC")


def accept_account(account):
    """DB 저장 성공 후에만 화면 원장을 바꾼다. 실패하면 기존 세션/원장을 유지한다."""
    from services.local_store import LocalStore
    revision=LocalStore().save_account(account, expected_revision=st.session_state.get("_portfolio_revision")
        if st.session_state.get("_portfolio_account") is not None and st.session_state["_portfolio_account"].account_id==account.account_id else None)
    st.session_state["_portfolio_account"] = account
    st.session_state["_portfolio_revision"] = revision
    st.session_state["_portfolio_nonce"] = uuid4().hex


def autoload_account():
    from services.local_store import LocalStore,default_store_path,StorageError
    if not st.session_state.get("_portfolio_autoloaded"):
        st.session_state["_portfolio_autoloaded"]=True
        if default_store_path().is_file():
            try:
                saved=LocalStore().load_account()
                if saved is not None:
                    st.session_state["_portfolio_account"],st.session_state["_portfolio_revision"]=saved
            except StorageError:
                st.error("저장 계좌를 복원하지 못했습니다. 손상/저장 경로를 확인하세요. 자동 초기화하지 않습니다.")


def render_portfolio(timeframe, period, *, workspace=False):
    autoload_account()
    with st.container(border=True):
        st.subheader("가상 포트폴리오 · 수동 연구 원장")
        st.caption("USDT 현금 + BTC/ETH 양수 보유 · 무레버리지 · 실제 거래소 주문/자동매매 없음")
        overview = st.container() if workspace else None
        with (st.container() if workspace else st.expander("가상 계좌와 거래 관리", expanded=False)):
            capital = st.number_input("초기 가상 투자금 (USDT)", min_value=1., max_value=1e12, value=10000., key="pf_capital", persist_state="session")
            confirm = st.checkbox("새 계좌로 세션 원장을 초기화합니다.", key="pf_reset_confirm", persist_state="session")
            if st.button("가상 계좌 만들기 / 초기화", key="pf_create"):
                if st.session_state.get("_portfolio_account") is not None and not confirm:
                    st.warning("기존 원장을 바꾸려면 초기화 확인을 선택하세요.")
                else:
                    try:
                        accept_account(create_account(capital, now=portfolio_now()))
                    except (ValueError,TypeError):
                        st.error("계좌 생성/저장에 실패했습니다. 기존 원장을 보존합니다.")
            account = st.session_state.get("_portfolio_account")
            if account is None:
                st.caption("초기금 10,000 USDT가 기본값입니다. 계좌 생성 후 수동 가상 거래를 기록하세요.")
                return
            context = (timeframe, period)
            if st.session_state.get("_portfolio_price_context") != context:
                st.session_state["_portfolio_frames"] = {}
                st.session_state["_portfolio_price_context"] = context
            if st.button("BTC/ETH 확정 가격 조회", key="pf_load_prices"):
                try:
                    frames = load_portfolio_prices(timeframe, period)
                    quotes_from_frames(frames, now=portfolio_now())
                    st.session_state["_portfolio_frames"] = frames
                except Exception:
                    st.session_state["_portfolio_frames"] = {}
                    st.error("가격 조회/검증에 실패했습니다. 기존 원장을 보존하며 임의 가격으로 대체하지 않습니다.")
            frames = st.session_state.get("_portfolio_frames", {})
            try:
                quotes = quotes_from_frames(frames, now=portfolio_now())
            except (ValueError, TypeError, KeyError):
                quotes = {}
            with st.form("pf_trade_form"):
                symbol = st.selectbox("가상 거래 자산", ("BTC/USDT", "ETH/USDT"), key="pf_symbol", persist_state="session")
                side = st.selectbox("가상 기록 방향", ("BUY", "SELL"), key="pf_side", persist_state="session")
                quantity = st.number_input("가상 수량", min_value=0., value=.01, format="%.8f", key="pf_quantity", persist_state="session")
                fee = st.number_input("가상 편도 수수료 (%)", min_value=0., max_value=5., value=.1, key="pf_fee", persist_state="session")
                slip = st.number_input("가상 편도 슬리피지 (%)", min_value=0., max_value=10., value=.05, key="pf_slippage", persist_state="session")
                agreed = st.checkbox("가정 체결가의 수동 기록이며 실제 체결을 보장하지 않음을 확인합니다.", key="pf_trade_confirm", persist_state="session")
                submitted = st.form_submit_button("수동 가상 거래 기록", key="pf_trade", type="primary")
            if submitted:
                try:
                    if not agreed:
                        raise PortfolioError("수동 가상 기록 확인이 필요합니다.")
                    if symbol not in quotes:
                        raise PortfolioError("거래 자산의 유효한 확정 가격을 먼저 조회하세요.")
                    updated = execute_trade(account, symbol, side, quantity, quotes[symbol], fee_rate=fee/100,
                        slippage_rate=slip/100, event_id=st.session_state.setdefault("_portfolio_nonce", uuid4().hex), now=portfolio_now())
                    accept_account(updated)
                    account = updated
                    st.success("가상 원장에 기록했습니다. 실제 주문은 발생하지 않았습니다.")
                except (ValueError, TypeError):
                    st.error("가상 거래를 기록하지 못했습니다. 확인·가격 시각·수량·현금/보유 잔액을 확인하세요. 기존 원장은 유지됩니다.")
            # 거래 처리 이후의 동일 검증 시각/가격을 STEP 14와 STEP 16에 전달한다.
            evaluated_at = portfolio_now()
            try:
                quotes = quotes_from_frames(frames, now=evaluated_at)
            except (ValueError, TypeError, KeyError):
                quotes = {}
            valuation = evaluate(account, quotes, now=evaluated_at)
            st.session_state["_portfolio_valuation"] = valuation
            st.caption(f"계좌 {account.account_id} · 거래 {len(account.trades)}건 · 평가 {valuation.evaluated_at_utc} · 자료 마감 {valuation.data_as_of_utc or 'N/A'}")
            with overview if overview is not None else nullcontext():
                columns = st.columns(3)
                for col, title, value in zip(columns, ("가상 현금", "가상 총자산", "계좌 실현 손익"),
                                             (valuation.cash, valuation.total_equity, valuation.realized_pnl)):
                    with col:
                        metric_card(title, "N/A" if value is None else f"{value:,.4f} USDT")
                st.caption(f"미실현 손익: {valuation.unrealized_pnl if valuation.unrealized_pnl is not None else 'N/A'} · 총수수료: {valuation.total_fees:.6g} USDT")
            if valuation.issues:
                st.warning("평가 불가: "+" / ".join(valuation.issues))
            st.dataframe(pd.DataFrame(valuation.positions), hide_index=True, width="stretch")
            rows = []
            for event in account.trades:
                fill = adverse_fill_price(event.quote.price, buying=event.side == "BUY", slippage_rate=event.slippage_rate)
                rows.append({"id":event.event_id, "symbol":event.symbol, "side":event.side, "quantity":event.quantity,
                    "assumed_fill":fill, "fee":event.quantity*fill*event.fee_rate,
                    "recorded_at_utc":event.recorded_at_utc, "price_as_of_utc":event.quote.as_of_utc})
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
            st.caption("가상 원장은 로컬 SQLite에 저장됩니다. 가상 기록은 호가/거래량 기반 실제 체결이 아니며 동일 봉 종가 신호의 체결을 가정하지 않습니다.")
            if not workspace:
                from ui.storage_view import render_account_history
                render_account_history(account,quotes,now=evaluated_at)
            return PortfolioPriceContext(frames=frames,quotes=quotes,valuation=valuation)
