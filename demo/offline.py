"""Run with streamlit run demo/offline.py in a SEPARATE server process.

Reuses app.py without changing its collectors, financial engines or hashes.
All prices/macro observations are synthetic; never a production fallback.
"""

from contextlib import ExitStack, contextmanager
from pathlib import Path
import runpy
import sys
from unittest.mock import patch
from uuid import uuid4

import numpy as np
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.binance import DataCollectionError
from collectors.fred import normalize_observations
from config.settings import TIMEFRAME_MILLISECONDS, SUPPORTED_SYMBOLS
from llm.config import OpenAIConfig
from models.macro import get_series
from services.market_data import load_market_data
from services.macro_service import _load_cached
from demo import SOURCE_LOCK


def synthetic_market(symbol, timeframe, period, *, anchor, case="ready", **options):
    """Fixed values and UTC closed-bar grid; actual covered range is explicit."""
    if symbol not in SUPPORTED_SYMBOLS or timeframe not in TIMEFRAME_MILLISECONDS:
        raise ValueError("Unsupported demo input")
    if case == "error":
        raise DataCollectionError("합성 시연: 외부 데이터 연결 실패 상황입니다. 실제 API 요청은 하지 않았습니다.")
    count = 2 if case == "short" else 360
    step = pd.Timedelta(milliseconds=TIMEFRAME_MILLISECONDS[timeframe])
    end = pd.Timestamp(anchor).floor(step)
    times = pd.date_range(end=end-step, periods=count, freq=step)
    t = np.arange(count, dtype=float)
    close = (100 + t*.04 + 4*np.sin(t/9)) * (1 if symbol == "BTC/USDT" else .1)
    opened = np.r_[close[0], close[:-1]]
    result = pd.DataFrame(dict(timestamp=times, open=opened,
        high=np.maximum(opened, close)*1.02, low=np.minimum(opened, close)*.98,
        close=close, volume=100000. + 1000*np.cos(t/5)))
    result.attrs.update(symbol=symbol, timeframe=timeframe, period=period,
        requested_start=pd.Timestamp(options.get("start_time", times[0])), requested_end=end,
        actual_start=times[0], actual_end=end, fetched_at=pd.Timestamp(anchor),
        is_partial=True, limit_reasons=("합성 시연 자료: 최대 360봉. 실제 요청 기간 전체를 수집한 결과가 아닙니다.",),
        demo_synthetic=True)
    return result


def synthetic_macro(series_id, start_date, end_date, *, anchor, **options):
    """No release/vintage values are invented, including in synthetic mode."""
    spec = get_series(series_id)
    dates = pd.date_range(start_date, end_date, freq="MS" if spec.frequency == "M" else "B")
    # Arbitrary teaching values only. Original units/frequencies remain visible.
    base = {"CPIAUCSL": 250., "FEDFUNDS": 3., "UNRATE": 4., "PAYEMS": 150000., "DGS10": 4.}[series_id]
    scale = {"CPIAUCSL": .3, "PAYEMS": 30.}.get(series_id, .005)
    values = base + scale*np.arange(len(dates))
    result = normalize_observations(series_id,
        [{"date": day.strftime("%Y-%m-%d"), "value": str(value)} for day, value in zip(dates, values)],
        fetched_at=anchor)
    result.attrs["demo_synthetic"] = True
    return result


def forbid_external(*args, **kwargs):
    raise RuntimeError("오프라인 합성 시연에서는 외부 HTTP/유료 LLM 호출이 차단됩니다.")


@contextmanager
def isolated_sources(database, anchor, case="ready"):
    """Scoped to this demo server; never run in a shared production process."""
    database = Path(database).resolve()
    demo_root = (ROOT / ".pytest_cache" / "sigfin-demo").resolve()
    if demo_root not in database.parents or database.suffix != ".sqlite3":
        raise ValueError("Demo database must stay inside .pytest_cache/sigfin-demo")
    with SOURCE_LOCK, ExitStack() as stack:
        stack.enter_context(patch("services.market_data.fetch_binance_ohlcv",
            side_effect=lambda s, t, p, **kw: synthetic_market(s, t, p, anchor=anchor, case=case, **kw)))
        stack.enter_context(patch("services.macro_service.get_api_key", return_value="0"*32))
        stack.enter_context(patch("services.macro_service.fetch_fred_series",
            side_effect=lambda s, start, end, **kw: synthetic_macro(s, start, end, anchor=anchor, **kw)))
        stack.enter_context(patch("services.local_store.default_store_path", return_value=database))
        for target in ("llm.config.read_openai_config", "ui.ai_analysis_view.read_openai_config",
                       "ui.workflow_view.read_openai_config", "ui.research_view.read_openai_config"):
            stack.enter_context(patch(target, return_value=OpenAIConfig()))
        for target in ("openai.OpenAI", "openai.AsyncOpenAI",
                       "requests.sessions.Session.request", "httpx.Client.send", "httpx.AsyncClient.send"):
            stack.enter_context(patch(target, side_effect=forbid_external))
        yield


def main():
    st.warning("SIGFIN 오프라인 합성 시연 · 모든 가격/거시 수치는 테스트용입니다. 실제 시장·사용자 계좌·LLM 결과가 아닙니다.")
    case = st.selectbox("합성 시연 상황", ("ready", "short", "error"),
        format_func=lambda value: {"ready": "정상 흐름 (합성 360봉)", "short": "자료 부족 (합성 2봉)", "error": "연결 실패 (요청 미실행)"}[value],
        key="demo_case")
    token = st.session_state.setdefault("_demo_token", uuid4().hex)
    # Only a generated UUID is accepted, never a user-controlled file path.
    if not isinstance(token, str) or len(token) != 32 or any(c not in "0123456789abcdef" for c in token):
        st.error("시연 저장소 식별자가 유효하지 않습니다.")
        st.stop()
    anchor = st.session_state.setdefault("_demo_anchor", pd.Timestamp.now(tz="UTC"))
    if st.session_state.get("_demo_case_previous") != case:
        load_market_data.clear()
        _load_cached.clear()
        st.session_state["_demo_case_previous"] = case
    st.caption("별도 시연 프로세스 · 세션별 시험 SQLite · 외부 HTTP/LLM 차단. 새 브라우저 세션은 새 시험 계좌를 사용합니다.")
    st.caption(f"합성 자료 기준 UTC: {anchor.isoformat()} · 긴 시연 후 가격이 오래되면 새 세션에서 다시 시작하세요.")
    with isolated_sources(ROOT / ".pytest_cache" / "sigfin-demo" / token / "demo.sqlite3", anchor, case):
        runpy.run_path(str(ROOT / "app.py"), run_name="__main__")


if __name__ == "__main__":
    main()
