"""테스트 전용 입력으로 실제 Streamlit 화면과 캐시 동작을 검증한다."""

from pathlib import Path
import json
import unittest
from unittest.mock import patch

import pandas as pd
import numpy as np
from streamlit.testing.v1 import AppTest

from collectors.binance import DataCollectionError, OHLCV_COLUMNS
from services.market_data import load_market_data
from config.settings import INDICATOR_WARMUP_CANDLES

# Original composition keeps the existing cross-renderer regression assertions.
# The actual six-page entrypoint is covered by test_redesign_ui.py.
APP_PATH = Path(__file__).resolve().parent / "fixtures" / "legacy_dashboard.py"


def fixture_frame(close=103.0):
    data = pd.DataFrame(
        [
            [pd.Timestamp("2026-10-14T10:00:00Z"), 100.0, 106.0, 98.0, 101.0, 10.0],
            [pd.Timestamp("2026-10-14T11:00:00Z"), 100.0, 106.0, 98.0, close, 12.0],
        ],
        columns=OHLCV_COLUMNS,
    )
    data.attrs = {
        "timeframe": "1h",
        "period": "3개월",
        "requested_start": pd.Timestamp("2026-07-14T12:00:00Z"),
        "requested_end": pd.Timestamp("2026-10-14T12:00:00Z"),
        "actual_start": data.iloc[0]["timestamp"],
        "actual_end": pd.Timestamp("2026-10-14T12:00:00Z"),
        "fetched_at": pd.Timestamp("2026-10-14T12:03:00Z"),
        "is_partial": False,
        "limit_reasons": (),
    }
    return data


def full_fixture_frame(drop=False):
    data = fixture_frame()
    close = 100.0 + np.arange(120)
    close[-1] += -150 if drop else 20
    result = pd.DataFrame({
        "timestamp": pd.date_range(end="2026-10-14T11:00:00Z", periods=120, freq="h"),
        "open": close - 1, "high": close + 2, "low": close - 2,
        "close": close, "volume": np.arange(120) + 20,
    })
    result.attrs = data.attrs.copy()
    result.attrs["actual_start"] = result.iloc[0]["timestamp"]
    return result


def chart_names(app):
    specification = json.loads(app.get("plotly_chart")[0].proto.spec)
    return {trace["name"] for trace in specification["data"]}


class StreamlitAppTests(unittest.TestCase):
    def setUp(self):
        load_market_data.clear()

    def tearDown(self):
        load_market_data.clear()

    def run_app(self):
        app = AppTest.from_file(str(APP_PATH), default_timeout=20).run()
        self.assertFalse(app.exception)
        return app

    def test_default_screen_and_existing_controls(self):
        with patch("services.market_data.fetch_binance_ohlcv", return_value=fixture_frame()) as fetch:
            app = self.run_app()
        fetch.assert_called_once_with("BTC/USDT", "1h", "3개월", warmup_candles=INDICATOR_WARMUP_CANDLES)
        self.assertEqual(len(app.sidebar.checkbox), 11)
        self.assertEqual([item.label for item in app.sidebar.checkbox], [
            "RSI", "MACD", "EMA", "SMA", "Bollinger Bands", "거래량 평균선", "CPI", "FED Rate", "NFP",
            "실업률 (UNRATE)", "10년물 국채금리 (DGS10)",
        ])
        self.assertEqual(len(app.get("plotly_chart")), 1)
        self.assertEqual(app.metric[0].value, "103.00 USDT")
        self.assertEqual(app.metric[1].value, "+3.00%")
        self.assertEqual(app.metric[2].value, "2026-10-14 11:00")
        self.assertTrue(any(item.value == "DATA READY" for item in app.success))
        rendered = " ".join(item.value for item in app.markdown)
        for title in ("Technical Agent", "Macro Agent", "Bull Agent", "Bear Agent", "Risk Manager", "AI SIGNAL"):
            self.assertIn(title, rendered)

    def test_checkbox_uses_cache_and_refresh_gets_new_data(self):
        with patch("services.market_data.fetch_binance_ohlcv", side_effect=[fixture_frame(), fixture_frame(105)]) as fetch:
            app = self.run_app()
            app.checkbox(key="show_rsi").uncheck().run()
            self.assertFalse(app.exception)
            self.assertEqual(fetch.call_count, 1)
            app.button(key="refresh_market").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(app.metric[0].value, "105.00 USDT")

    def test_sidebar_changes_query_parameters(self):
        with patch("services.market_data.fetch_binance_ohlcv", return_value=fixture_frame()) as fetch:
            app = self.run_app()
            app.selectbox(key="symbol").select("ETH/USDT").run()
            self.assertFalse(app.exception)
            fetch.assert_called_with("ETH/USDT", "1h", "3개월", warmup_candles=INDICATOR_WARMUP_CANDLES)
            app.selectbox(key="timeframe").select("4h").run()
            self.assertFalse(app.exception)
            fetch.assert_called_with("ETH/USDT", "4h", "3개월", warmup_candles=INDICATOR_WARMUP_CANDLES)
            app.selectbox(key="period").select("1년").run()
            self.assertFalse(app.exception)
            fetch.assert_called_with("ETH/USDT", "4h", "1년", warmup_candles=INDICATOR_WARMUP_CANDLES)
            self.assertEqual(fetch.call_count, 4)

    def test_refresh_only_clears_selected_query(self):
        with patch("services.market_data.fetch_binance_ohlcv", return_value=fixture_frame()) as fetch:
            app = self.run_app()
            app.selectbox(key="symbol").select("ETH/USDT").run()
            app.button(key="refresh_market").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(fetch.call_count, 3)
            app.selectbox(key="symbol").select("BTC/USDT").run()
            self.assertFalse(app.exception)
            self.assertEqual(fetch.call_count, 3)

    def test_partial_collection_explains_range_and_reason(self):
        data = fixture_frame()
        data.attrs.update(is_partial=True, limit_reasons=("최대 5,000개 제한",))
        with patch("services.market_data.fetch_binance_ohlcv", return_value=data):
            app = self.run_app()
        self.assertTrue(any("최대 5,000개 제한" in item.value for item in app.warning))
        self.assertTrue(any("실제 수집:" in item.value for item in app.caption))
        self.assertEqual(len(app.get("plotly_chart")), 1)

    def test_api_failure_displays_error_and_no_chart(self):
        with patch("services.market_data.fetch_binance_ohlcv", side_effect=DataCollectionError("공개 API 연결 실패")):
            app = self.run_app()
        self.assertTrue(any("공개 API 연결 실패" in item.value for item in app.error))
        self.assertEqual(len(app.get("plotly_chart")), 0)
        self.assertEqual(len(app.metric), 0)
        self.assertEqual(len(app.sidebar.checkbox), 11)

    def test_network_permission_details_and_refresh_recovery(self):
        from collectors.errors import diagnose_api_error

        root = PermissionError(13, "not for display")
        root.winerror = 10013
        details = diagnose_api_error(root)
        error = DataCollectionError(details.message, details=details)
        with patch("services.market_data.fetch_binance_ohlcv", side_effect=[error, fixture_frame()]) as fetch:
            app = self.run_app()
            self.assertTrue(any(item.label == "데이터 조회 오류 진단" for item in app.expander))
            self.assertTrue(any("네트워크 권한" in item.value for item in app.caption))
            self.assertEqual(len(app.get("plotly_chart")), 0)
            app.button(key="refresh_market").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            self.assertEqual(len(app.get("plotly_chart")), 1)
            self.assertEqual(fetch.call_count, 2)

    def test_ai_button_with_short_data_reports_insufficiency_without_api(self):
        with patch("services.market_data.fetch_binance_ohlcv", return_value=fixture_frame()) as fetch:
            app = self.run_app()
            app.button(key="analyze_ai").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any("판단 근거 부족" in item.value for item in app.info))
            self.assertEqual(fetch.call_count, 1)

    def test_each_indicator_toggle_only_changes_its_traces_and_uses_cache(self):
        with patch("services.market_data.fetch_binance_ohlcv", return_value=full_fixture_frame()) as fetch:
            app = self.run_app()
            groups = {
                "show_rsi": {"RSI 14"},
                "show_macd": {"MACD Histogram", "MACD (12,26)", "Signal (9)"},
                "show_ema": {"EMA 20", "EMA 60"},
                "show_volume_sma": {"Volume SMA 20"},
            }
            for key, removed in groups.items():
                before = chart_names(app)
                app.checkbox(key=key).uncheck().run()
                self.assertFalse(app.exception)
                self.assertEqual(chart_names(app), before - removed)
            self.assertEqual(len(chart_names(app)), 2)
            for key, added in {
                "show_sma": {"SMA 20", "SMA 60"},
                "show_bollinger": {"BB Upper", "BB Lower", "BB Middle (SMA 20)"},
            }.items():
                before = chart_names(app)
                app.checkbox(key=key).check().run()
                self.assertFalse(app.exception)
                self.assertEqual(chart_names(app), before | added)
            self.assertEqual(fetch.call_count, 1)

    def test_indicator_cards_show_values_and_states(self):
        for drop, states in ((False, ("과매수", "양수")), (True, ("과매도", "음수"))):
            with self.subTest(drop=drop):
                load_market_data.clear()
                with patch("services.market_data.fetch_binance_ohlcv", return_value=full_fixture_frame(drop)):
                    app = self.run_app()
                rendered = " ".join(item.value for item in app.markdown)
                self.assertIn(states[0], rendered)
                self.assertIn(states[1], rendered)
                self.assertIn("AI SIGNAL", rendered)
                self.assertNotIn("데이터 부족", rendered)

    def test_short_data_displays_indicator_insufficiency(self):
        with patch("services.market_data.fetch_binance_ohlcv", return_value=fixture_frame()):
            app = self.run_app()
        rendered = " ".join(item.value for item in app.markdown)
        self.assertIn("데이터 부족", rendered)
        self.assertTrue(any("연속 데이터가 부족" in item.value for item in app.info))
        self.assertEqual(len(app.get("plotly_chart")), 1)
