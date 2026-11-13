"""STEP 20: 배포한 시연 진입점의 원본 보존/실 API 차단/실제 화면 재사용."""

import pandas as pd
from pandas.testing import assert_frame_equal
import pytest
from streamlit.testing.v1 import AppTest

from demo.offline import ROOT, synthetic_market, synthetic_macro, isolated_sources
from config.settings import TIMEFRAME_MILLISECONDS
from services.market_data import load_market_data
from services.macro_service import _load_cached


@pytest.mark.parametrize("timeframe", TIMEFRAME_MILLISECONDS)
def test_demo_values_are_deterministic_confirmed_and_explicitly_synthetic(timeframe):
    now = pd.Timestamp("2026-11-12T03:12:00Z")
    first = synthetic_market("BTC/USDT", timeframe, "3개월", anchor=now)
    assert_frame_equal(first, synthetic_market("BTC/USDT", timeframe, "3개월", anchor=now))
    assert (first.timestamp + pd.Timedelta(milliseconds=TIMEFRAME_MILLISECONDS[timeframe]) <= now).all()
    assert first.attrs["demo_synthetic"] and first.attrs["is_partial"]


def test_demo_macro_preserves_missing_release_and_vintage_and_units():
    from models.macro import SERIES
    for series, spec in SERIES.items():
        frame = synthetic_macro(series, "2024-01-01", "2026-10-31", anchor=pd.Timestamp("2026-11-12T00:00Z"))
        assert frame.attrs["demo_synthetic"] and not frame.attrs["is_point_in_time"]
        assert frame.units.eq(spec.units).all() and frame.frequency.eq(spec.frequency).all()
        assert frame[["release_date", "available_at", "vintage_date"]].isna().all().all()


def test_demo_refuses_original_store_and_blocks_http_and_paid_client():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    import requests
    import httpx
    import openai
    from services import local_store
    original = local_store.default_store_path()
    now = pd.Timestamp("2026-11-12T00:00Z")
    with pytest.raises(ValueError):
        with isolated_sources(original, now):
            pytest.fail("Original store must never be opened")
    path = ROOT / ".pytest_cache" / "sigfin-demo" / "unit" / "demo.sqlite3"
    with isolated_sources(path, now):
        assert local_store.default_store_path() == path.resolve()
        # Import after patch: all actual UI aliases are patched as well.
        from ui.ai_analysis_view import read_openai_config as ui_config
        assert ui_config().api_key is None
        with pytest.raises(RuntimeError): requests.get("https://example.invalid")
        with pytest.raises(RuntimeError): httpx.get("https://example.invalid")
        with pytest.raises(RuntimeError): openai.OpenAI()
    assert local_store.default_store_path() == original
    attempted, entered = Event(), Event()
    other = path.with_name("other.sqlite3")
    def second_session():
        attempted.set()
        with isolated_sources(other, now):
            entered.set()
            assert local_store.default_store_path() == other.resolve()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with isolated_sources(path, now):
            pending = pool.submit(second_session)
            assert attempted.wait(5)
            assert not entered.wait(.05)
            assert local_store.default_store_path() == path.resolve()
        pending.result(timeout=5)
    assert entered.is_set() and local_store.default_store_path() == original


def test_demo_full_app_modes_report_save_restore_and_input_changes():
    from test_redesign_ui import go
    load_market_data.clear()
    _load_cached.clear()
    try:
        app = AppTest.from_file(str(ROOT / "demo" / "offline.py"), default_timeout=40).run()
        assert not app.exception
        assert any("모든 가격/거시" in row.value for row in app.warning)
        go(app, 'research', '단일 AI 분석(고급)')
        assert app.session_state["ai_mode"] == "mock"
        go(app, 'research', '에이전트 워크플로우')
        assert app.session_state["workflow_mode"] == "Mock Workflow"
        go(app, 'strategy', '전략 비교')
        app.button(key="exp_add_strategy").click().run()
        assert len(app.session_state["_experiment_strategies"]) == 1
        go(app, 'strategy', '전략 편집')
        app.text_input(key="strategy_name").set_value("시연 비교 B").run()
        go(app, 'strategy', '전략 비교')
        app.button(key="exp_add_strategy").click().run()
        assert not app.exception and len(app.session_state["_experiment_strategies"]) == 2
        go(app, 'portfolio')
        app.button(key="pf_create").click().run()
        assert not app.exception
        token = app.session_state["_demo_token"]
        db = ROOT / ".pytest_cache" / "sigfin-demo" / token / "demo.sqlite3"
        assert db.is_file()
        app.button(key="pf_load_prices").click().run()
        assert not app.exception
        go(app, 'research', '통합 리포트')
        app.button(key="run_research").click().run()
        assert not app.exception and len(app.get("download_button")) >= 2
        app.button(key="save_research").click().run()
        assert not app.exception
        assert any("저장" in row.value for row in app.success)
        go(app, 'history')
        app.button(key="restore_analysis_snapshot").click().run()
        assert not app.exception
        raw = [row for row in app.expander if row.label == "원본 저장 데이터 보기 (개발자용)"]
        assert raw and all(not row.proto.expanded for row in raw)
        go(app, 'market')
        app.selectbox(key="demo_case").select("short").run()
        assert not app.exception and any("데이터 부족" in row.value for row in app.markdown)
        app.selectbox(key="demo_case").select("error").run()
        assert not app.exception and any("합성 시연" in row.value for row in app.error)
    finally:
        load_market_data.clear()
        _load_cached.clear()
