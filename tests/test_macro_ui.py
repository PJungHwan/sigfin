"""합성 응답임을 명시한 입력으로 FRED UI, 캐시, Binance 독립성을 검증한다."""

import json
import unittest
from unittest.mock import patch

import pandas as pd
from streamlit.testing.v1 import AppTest

from collectors.binance import DataCollectionError
from collectors.fred import normalize_observations
from models.macro import FredError, FredKeyError, SERIES
from services.macro_service import _load_cached
from test_app import APP_PATH, full_fixture_frame
from test_fred import TEST_KEY, observation
from ui.macro_charts import macro_series_chart


def mock_series(series_id, start, end, *, api_key):
    spec = SERIES[series_id]
    dates = pd.date_range(start, end, freq="MS" if spec.frequency == "M" else "B")
    return normalize_observations(series_id, [observation(d.strftime("%Y-%m-%d"), str(100+i))
                                             for i, d in enumerate(dates)])


class MacroUITests(unittest.TestCase):
    def setUp(self):
        from services.market_data import load_market_data
        load_market_data.clear()
        _load_cached.clear()
        key = patch("services.macro_service.get_api_key", return_value=TEST_KEY)
        key.start()
        self.addCleanup(key.stop)
        self.market = patch("services.market_data.fetch_binance_ohlcv", return_value=full_fixture_frame()).start()
        self.addCleanup(patch.stopall)

    def tearDown(self):
        from services.market_data import load_market_data
        load_market_data.clear()
        _load_cached.clear()

    def app(self):
        app = AppTest.from_file(str(APP_PATH), default_timeout=30).run()
        self.assertFalse(app.exception)
        return app

    def test_no_automatic_fred_requests_and_existing_sidebar_reused(self):
        with patch("services.macro_service.fetch_fred_series") as fetch:
            app = self.app()
            self.assertEqual(len(app.sidebar.checkbox), 11)
            self.assertTrue(app.button(key="load_macro").disabled)
            app.checkbox(key="macro_cpi").check().run()
            self.assertFalse(app.button(key="load_macro").disabled)
            fetch.assert_not_called()
            self.assertEqual(len(app.get("plotly_chart")), 1)

    def test_missing_key_shows_setup_and_disables_request(self):
        with patch("services.macro_service.get_api_key", side_effect=FredKeyError("FRED_API_KEY가 없습니다.")), \
                patch("services.macro_service.fetch_fred_series") as fetch:
            app = self.app()
            app.checkbox(key="macro_cpi").check().run()
            self.assertTrue(app.button(key="load_macro").disabled)
            self.assertTrue(any("FRED_API_KEY" in info.value for info in app.info))
            fetch.assert_not_called()

    def test_all_series_render_separate_units_frequency_and_no_key_in_output(self):
        with patch("services.macro_service.fetch_fred_series", side_effect=mock_series) as fetch:
            app = self.app()
            for key in ("macro_cpi", "macro_fed", "macro_nfp", "macro_unrate", "macro_dgs10"):
                app.checkbox(key=key).check().run()
            app.button(key="load_macro").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            self.assertEqual(fetch.call_count, 5)
            self.assertEqual(len(app.get("plotly_chart")), 6)
            self.assertTrue(any("천 명" in metric.value for metric in app.metric))
            captions = " ".join(item.value for item in app.caption)
            self.assertIn("일별", captions)
            self.assertIn("월별", captions)
            self.assertIn("발표일", captions)
            rendered = " ".join(item.value for item in [*app.caption, *app.info, *app.error])
            self.assertNotIn(TEST_KEY, rendered)
            self.assertNotIn(TEST_KEY, str(app.session_state["_macro_results"]))
            self.assertEqual(self.market.call_count, 1)

    def test_cpi_view_and_technical_display_change_do_not_refetch(self):
        with patch("services.macro_service.fetch_fred_series", side_effect=mock_series) as fetch:
            app = self.app()
            app.checkbox(key="macro_cpi").check().run()
            app.button(key="load_macro").click().run()
            app.selectbox(key="macro_cpi_view").select("전년동월 대비 (%)").run()
            self.assertFalse(app.exception)
            self.assertTrue(any("전년동월비" in metric.value for metric in app.metric))
            app.checkbox(key="show_rsi").uncheck().run()
            app.button(key="load_macro").click().run()
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(self.market.call_count, 1)

    def test_selection_and_period_changes_invalidate_previous_result(self):
        with patch("services.macro_service.fetch_fred_series", side_effect=mock_series):
            app = self.app()
            app.checkbox(key="macro_cpi").check().run()
            app.button(key="load_macro").click().run()
            app.selectbox(key="macro_period").select("1년").run()
            self.assertFalse(any("최신 유효 관측값" in metric.label for metric in app.metric))
            self.assertTrue(any("무효화" in info.value for info in app.info))
            app.button(key="load_macro").click().run()
            app.checkbox(key="macro_nfp").check().run()
            self.assertFalse(any("최신 유효 관측값" in metric.label for metric in app.metric))

    def test_removed_key_invalidates_result_without_showing_old_data(self):
        with patch("services.macro_service.fetch_fred_series", side_effect=mock_series):
            app = self.app()
            app.checkbox(key="macro_cpi").check().run()
            app.button(key="load_macro").click().run()
            with patch("services.macro_service.get_api_key", side_effect=FredKeyError("FRED_API_KEY가 없습니다.")):
                app.run()
            self.assertFalse(app.exception)
            self.assertFalse(any("최신 유효 관측값" in metric.label for metric in app.metric))

    def test_per_series_api_error_shows_category_and_other_series_still_renders(self):
        def fetch(series_id, *args, **kwargs):
            if series_id == "CPIAUCSL":
                raise FredError("FRED 응답 구조를 확인해 주세요.", category="RESPONSE_ERROR")
            return mock_series(series_id, *args, **kwargs)

        with patch("services.macro_service.fetch_fred_series", side_effect=fetch):
            app = self.app()
            app.checkbox(key="macro_cpi").check().run()
            app.checkbox(key="macro_fed").check().run()
            app.button(key="load_macro").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any("RESPONSE_ERROR" in error.value for error in app.error))
            self.assertTrue(any("FEDFUNDS" in metric.label for metric in app.metric))

    def test_authentication_or_rate_limit_defers_remaining_requests(self):
        for category in ("AUTHENTICATION", "RATE_LIMIT"):
            with self.subTest(category=category), patch("services.macro_service.fetch_fred_series", side_effect=FredError("test failure", category=category)) as fetch:
                app = self.app()
                app.checkbox(key="macro_cpi").check().run()
                app.checkbox(key="macro_fed").check().run()
                app.button(key="load_macro").click().run()
                self.assertFalse(app.exception)
                self.assertEqual(fetch.call_count, 1)
                self.assertTrue(any("DEFERRED" in error.value for error in app.error))

    def test_binance_error_does_not_block_fred_query(self):
        with patch("services.market_data.fetch_binance_ohlcv", side_effect=DataCollectionError("Binance network test")), \
                patch("services.macro_service.fetch_fred_series", side_effect=mock_series):
            app = self.app()
            app.checkbox(key="macro_cpi").check().run()
            app.button(key="load_macro").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any("CPIAUCSL" in metric.label for metric in app.metric))
            self.assertEqual(len(app.get("plotly_chart")), 1)

    def test_monthly_chart_yoy_axis_and_missing_values(self):
        data = mock_series("CPIAUCSL", "2023-01-01", "2025-01-01", api_key=TEST_KEY)
        from services.macro_service import calculate_cpi_yoy
        data = calculate_cpi_yoy(data)
        figure = macro_series_chart(data, "CPIAUCSL", cpi_yoy=True)
        self.assertIn("전년동월 대비", figure.layout.yaxis.title.text)
        self.assertIn("발표일 아님", figure.layout.xaxis.title.text)
        self.assertFalse(figure.data[0].connectgaps)
        self.assertEqual(figure.data[0].mode, "lines+markers")
        specification = json.loads(figure.to_json())
        self.assertNotIn(TEST_KEY, str(specification))
