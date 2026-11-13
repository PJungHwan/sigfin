"""STEP 19 기존 UI의 손상 입력·세션 분리. 임시 DB/합성 시세만 사용한다."""

from types import SimpleNamespace
from unittest.mock import patch
import unittest

from services.local_store import LocalStore
import test_portfolio_ui as portfolio_fixtures


def resource_script():
    import streamlit as st
    from ui.experiment_view import display_experiment
    display_experiment(st.session_state["resource_result"],
                       replay=st.session_state.get("resource_replay", False),
                       reference=st.session_state.get("resource_reference"))


def test_maximum_comparison_keeps_tables_and_input_download_when_result_exceeds_limit():
    from streamlit.testing.v1 import AppTest
    from backtest.experiments import capture_experiment, execute_experiment
    from backtest.experiment_verification import ResultReference, RESULT_DIGEST_ALGORITHM
    from backtest.models import BacktestConfig, RiskConfig
    from test_experiments import frame, strategy
    raw = frame([100.] * 5000)
    raw.attrs["fetched_at"] = portfolio_fixtures.NOW
    spec = capture_experiment(raw, [strategy(f"S{i}", i + 1, 1000) for i in range(6)],
                              BacktestConfig(), RiskConfig(), symbol="BTC/USDT", timeframe="1h",
                              period="resource test", split_at=3500, as_of=portfolio_fixtures.NOW)
    result = execute_experiment(spec)
    assert len(result.runs) == 36 and result.status == "COMPLETED"
    at = AppTest.from_function(resource_script, default_timeout=60)
    at.session_state["resource_result"] = result
    at.run()
    assert not at.exception
    assert any("결과 해시 생성 불가" in row.value for row in at.warning)
    assert len(at.dataframe) > 2 and len(at.get("plotly_chart")) == 1
    assert len(at.get("download_button")) == 1  # 입력은 보존, 검증 못한 Markdown/해시는 표시하지 않는다.
    at.session_state["resource_replay"] = True
    at.session_state["resource_reference"] = ResultReference(input_digest=spec.input_digest,
        result_digest="0" * 64, algorithm=RESULT_DIGEST_ALGORITHM, execution_status="COMPLETED")
    at.run()
    assert not at.exception
    assert any("결과 해시 비교 불가" in row.value for row in at.warning)
    assert not any("결과 해시 일치" in row.value for row in at.success)
    assert len(at.get("download_button")) == 1


class SystemUITests(unittest.TestCase):
    setUp = portfolio_fixtures.PortfolioUITests.setUp
    tearDown = portfolio_fixtures.PortfolioUITests.tearDown
    app = portfolio_fixtures.PortfolioUITests.app
    ready = portfolio_fixtures.PortfolioUITests.ready
    buy = portfolio_fixtures.PortfolioUITests.buy

    def test_deep_account_upload_is_safe_and_preserves_session_and_database(self):
        app = self.ready()
        self.buy(app)
        account = app.session_state["_portfolio_account"]
        before = self.db_path.read_bytes()
        payload = ("[" * 2000 + "0" + "]" * 2000).encode()
        upload = SimpleNamespace(size=len(payload), getvalue=lambda: payload)
        with patch("ui.storage_view.st.file_uploader", return_value=upload):
            app.button(key="pf_import").click().run()
        self.assertFalse(app.exception)
        self.assertTrue(app.error)
        self.assertEqual(app.session_state["_portfolio_account"], account)
        self.assertEqual(self.db_path.read_bytes(), before)
        self.assertEqual(LocalStore(self.db_path).load_account()[0], account)

    def test_two_sessions_do_not_share_unsaved_selections_or_research_state(self):
        first = self.ready()
        first.session_state["_experiment_strategies"] = []
        first.number_input(key="pf_quantity").set_value(.123).run()
        second = self.app()
        self.assertEqual(second.number_input(key="pf_quantity").value, .01)
        self.assertNotIn("_research_snapshot", second.session_state)
        self.assertNotIn("_experiment_current", second.session_state)
        self.assertEqual(first.session_state["_portfolio_account"], second.session_state["_portfolio_account"])
        self.assertFalse(first.exception or second.exception)
