"""STEP 18 명시 실행, 무효화, 다운로드·재현 AppTest. 시장/유료 API 없음."""

from io import BytesIO
from unittest.mock import patch

import pandas as pd
from streamlit.testing.v1 import AppTest

from backtest.experiments import execute_experiment, experiment_json, result_digest
from backtest.experiment_verification import experiment_artifact_json
from services.market_data import load_market_data
from test_app import APP_PATH, full_fixture_frame
from test_experiments import frame, strategy


def script():
    import streamlit as st
    from backtest.models import BacktestConfig, RiskConfig
    from ui.experiment_view import render_experiments
    from test_experiments import frame, strategy
    capital = st.number_input("test capital", value=1000., key="test_capital")
    data = st.session_state.get("test_data", frame([100., 100., 120., 150., 120., 100., 100., 90.]))
    current = st.session_state.get("test_strategy", strategy())
    st.checkbox("표시만 변경", key="test_display")
    render_experiments(data, current, BacktestConfig(capital, 0, 0), RiskConfig(), "BTC/USDT", "1h", "시험")


def ready():
    at = AppTest.from_function(script, default_timeout=45).run()
    at.button(key="exp_add_strategy").click().run()
    at.session_state["test_strategy"] = strategy("B", 50, 1000)
    at.button(key="exp_add_strategy").click().run()
    assert not at.exception
    return at


def test_no_auto_execution_duplicate_names_and_explicit_comparison():
    with patch("ui.experiment_view.execute_experiment", wraps=execute_experiment) as engine:
        at = ready()
        assert "_experiment_current" not in at.session_state
        assert engine.call_count == 0
        at.button(key="exp_add_strategy").click().run()
        assert at.error and len(at.session_state["_experiment_strategies"]) == 2
        at.button(key="exp_run").click().run()
        assert not at.exception and not at.error
        assert len(at.session_state["_experiment_current"][1].runs) == 12
        assert len(at.get("plotly_chart")) == 1
        assert len(at.get("download_button")) == 2
        assert engine.call_count == 1
        at.checkbox(key="test_display").check().run()
        at.selectbox(key="exp_result_segment_current").select("검증").run()
        assert engine.call_count == 1 and not at.exception


def test_actual_input_change_invalidates_but_collection_time_alone_does_not():
    at = ready()
    at.button(key="exp_run").click().run()
    original = result_digest(at.session_state["_experiment_current"][1])
    raw = frame([100., 100., 120., 150., 120., 100., 100., 90.])
    raw.attrs["fetched_at"] += pd.Timedelta(minutes=1)
    at.session_state["test_data"] = raw
    at.run()
    assert result_digest(at.session_state["_experiment_current"][1]) == original
    raw.loc[0, "volume"] += 1
    at.session_state["test_data"] = raw
    at.run()
    assert "_experiment_current" not in at.session_state
    assert any("무효화" in item.value for item in at.info)
    at.button(key="exp_run").click().run()
    at.number_input(key="test_capital").set_value(2000.).run()
    assert "_experiment_current" not in at.session_state
    assert not at.exception


def test_strategy_removal_and_split_changes_invalidate():
    at = ready()
    at.button(key="exp_run").click().run()
    at.number_input(key="exp_split_percent").set_value(50).run()
    assert "_experiment_current" not in at.session_state
    at.button(key="exp_run").click().run()
    at.button(key="exp_remove_strategy").click().run()
    assert at.button(key="exp_run").disabled
    assert "_experiment_current" not in at.session_state and not at.exception


def test_replay_does_not_need_current_data_and_has_independent_widget_keys():
    at = ready()
    at.button(key="exp_run").click().run()
    saved = at.session_state["_experiment_current"][1]
    payload = experiment_json(saved.snapshot).encode()
    with patch("ui.experiment_view.st.file_uploader", return_value=BytesIO(payload)):
        at.run()
        at.button(key="exp_replay").click().run()
        assert not at.exception
        assert len(at.get("plotly_chart")) == 2
        assert len(at.get("download_button")) == 4
        assert result_digest(at.session_state["_experiment_replay"]) == result_digest(saved)
        assert any("결과 해시 일치" in item.value for item in at.success)
        at.session_state["test_data"] = None
        at.run()
        assert not at.exception
        assert "_experiment_current" not in at.session_state
        assert len(at.get("download_button")) == 2


def test_bad_replay_and_insufficient_split_are_explicit_errors():
    at = ready()
    at.session_state["test_data"] = frame([100, 100, 120])
    at.button(key="exp_run").click().run()
    assert not at.exception and at.error
    assert "_experiment_current" not in at.session_state
    with patch("ui.experiment_view.st.file_uploader", return_value=BytesIO(b'{bad')):
        at.run()
        at.button(key="exp_replay").click().run()
        assert at.error and "_experiment_replay" not in at.session_state


def test_invalid_collection_metadata_invalidates_current_result():
    at = ready()
    at.button(key="exp_run").click().run()
    raw = frame([100., 100., 120., 150., 120., 100., 100., 90.])
    raw.attrs["fetched_at"] = pd.Timestamp.now(tz="UTC") + pd.Timedelta(days=1)
    at.session_state["test_data"] = raw
    at.run()
    assert not at.exception and at.error
    assert "_experiment_current" not in at.session_state


def test_full_app_preserves_existing_functions_and_no_extra_fetch_or_paid_api():
    load_market_data.clear()
    try:
        market = full_fixture_frame()
        market.attrs["symbol"] = "BTC/USDT"
        with patch("services.market_data.fetch_binance_ohlcv", return_value=market) as fetch, \
             patch("openai.OpenAI", side_effect=AssertionError("no paid API")):
            at = AppTest.from_file(str(APP_PATH), default_timeout=45).run()
            assert not at.exception
            assert any(item.value == "백테스트 전략 비교 · 고정 실험" for item in at.subheader)
            assert at.button(key="run_backtest") and at.button(key="compare_risk")
            at.button(key="exp_add_strategy").click().run()
            at.text_input(key="strategy_name").set_value("두 번째 RSI 전략").run()
            at.button(key="exp_add_strategy").click().run()
            at.button(key="exp_run").click().run()
            assert not at.exception
            assert not at.error, [item.value for item in at.error]
            assert "_experiment_current" in at.session_state
            assert fetch.call_count == 1
    finally:
        load_market_data.clear()


def test_artifact_match_is_visible_without_original_session_and_survives_rerun():
    at = ready()
    at.button(key="exp_run").click().run()
    original = at.session_state["_experiment_current"][1]
    payload = experiment_artifact_json(original).encode()
    fresh = AppTest.from_function(script, default_timeout=45)
    fresh.session_state["test_data"] = None
    with patch("ui.experiment_view.st.file_uploader", return_value=BytesIO(payload)):
        fresh.run()
        fresh.button(key="exp_replay").click().run()
        assert not fresh.exception
        assert any("결과 해시 일치" in item.value for item in fresh.success)
        assert any("기준 출처: 저장 JSON" in item.value for item in fresh.caption)
        fresh.checkbox(key="test_display").check().run()
        assert any("결과 해시 일치" in item.value for item in fresh.success)
        assert len(fresh.get("download_button")) == 2


def test_legacy_missing_reference_and_changed_upload_are_not_false_matches():
    at = ready()
    at.button(key="exp_run").click().run()
    original = at.session_state["_experiment_current"][1]
    payload = experiment_json(original.snapshot).encode()
    fresh = AppTest.from_function(script, default_timeout=45)
    fresh.session_state["test_data"] = None
    with patch("ui.experiment_view.st.file_uploader", return_value=BytesIO(payload)):
        fresh.run()
        fresh.button(key="exp_replay").click().run()
        assert not fresh.exception
        assert any("결과 해시 비교 불가" in item.value for item in fresh.info)
        assert not any("결과 해시 일치" in item.value for item in fresh.success)
    with patch("ui.experiment_view.st.file_uploader", return_value=BytesIO(b'{bad')):
        fresh.run()
        assert "_experiment_replay" not in fresh.session_state
        assert any("업로드 파일이 바뀌어" in item.value for item in fresh.info)


def test_mismatch_is_shown_with_both_hashes_and_downloads_remain():
    import json
    at = ready()
    at.button(key="exp_run").click().run()
    original = at.session_state["_experiment_current"][1]
    payload = json.loads(experiment_artifact_json(original))
    payload["reference"]["result_digest"] = "0" * 64
    with patch("ui.experiment_view.st.file_uploader", return_value=BytesIO(json.dumps(payload).encode())):
        at.run()
        at.button(key="exp_replay").click().run()
        assert not at.exception
        assert any("결과 해시 불일치" in item.value for item in at.error)
        assert any("0" * 64 in item.value for item in at.text)
        assert any(result_digest(original) in item.value for item in at.text)
        assert len(at.get("download_button")) == 4


def test_existing_backtest_risk_controls_feed_step18_and_render_stop_loss_ledger():
    from backtest.experiment_verification import experiment_artifact_json, read_experiment_artifact, verify_result
    from test_experiments import stop_loss_fixture
    from strategies.storage import strategy_to_json
    raw, strategies = stop_loss_fixture()
    metadata = full_fixture_frame().attrs.copy()
    metadata.update(raw.attrs)
    metadata.update(actual_start=raw.iloc[0].timestamp,
                    actual_end=raw.iloc[-1].timestamp + pd.Timedelta(hours=1),
                    requested_start=raw.iloc[0].timestamp,
                    requested_end=raw.iloc[-1].timestamp + pd.Timedelta(hours=1))
    raw.attrs = metadata
    load_market_data.clear()
    try:
        with patch("services.market_data.fetch_binance_ohlcv", return_value=raw) as fetch, \
             patch("openai.OpenAI", side_effect=AssertionError("no paid API")):
            at = AppTest.from_file(str(APP_PATH), default_timeout=45)
            at.session_state["_experiment_strategies"] = [strategy_to_json(s) for s in strategies]
            at.run()
            assert not at.exception
            assert any(item.label == "Risk Management · 손절·익절·트레일링" for item in at.expander)
            for prefix in ("stop_loss", "take_profit", "trailing_stop"):
                assert not at.checkbox(key=f"risk_{prefix}_enabled").value
                assert at.number_input(key=f"risk_{prefix}_rate").disabled
            at.number_input(key="bt_capital").set_value(1000.)
            at.number_input(key="bt_fee").set_value(0.)
            at.number_input(key="bt_slippage").set_value(0.)
            at.number_input(key="exp_split_percent").set_value(50).run()
            at.button(key="exp_run").click().run()
            disabled = at.session_state["_experiment_current"][1]
            for off, on in zip(disabled.runs[::2], disabled.runs[1::2]):
                assert off.result.metrics == on.result.metrics
            at.checkbox(key="risk_stop_loss_enabled").check().run()
            assert not at.number_input(key="risk_stop_loss_rate").disabled
            assert "_experiment_current" not in at.session_state
            at.number_input(key="risk_stop_loss_rate").set_value(1.).run()
            at.button(key="exp_run").click().run()
            assert not at.exception and not at.error
            experiment = at.session_state["_experiment_current"][1]
            assert len(experiment.runs) == 12 and experiment.status == "COMPLETED"
            assert experiment.snapshot.risk["stop_loss_enabled"] is True
            assert experiment.snapshot.risk["stop_loss_rate"] == .01
            assert experiment.runs[0].result.metrics.final_equity == 1040
            assert experiment.runs[1].result.metrics.final_equity == 980.1
            ledgers = [item.value for item in at.dataframe if "exit_reason" in item.value.columns]
            assert any(table.exit_reason.eq("STOP_LOSS").any() for table in ledgers)
            assert any(item.label == "조합별 거래·체결 원장" for item in at.expander)
            saved, reference = read_experiment_artifact(experiment_artifact_json(experiment))
            assert verify_result(execute_experiment(saved), reference).status == "MATCH"
            at.checkbox(key="risk_stop_loss_enabled").uncheck().run()
            assert "_experiment_current" not in at.session_state
            at.button(key="exp_run").click().run()
            disabled_again = at.session_state["_experiment_current"][1]
            for off, on in zip(disabled_again.runs[::2], disabled_again.runs[1::2]):
                assert off.result.metrics == on.result.metrics
            assert fetch.call_count == 1
    finally:
        load_market_data.clear()
