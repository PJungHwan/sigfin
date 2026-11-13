"""합성 데이터 기반 통합 분석 UI와 기존 캐시 재사용 검증. 외부 API를 호출하지 않는다."""

from unittest.mock import patch, Mock
import unittest

import pandas as pd
from streamlit.testing.v1 import AppTest

from analytics.analysis import analyze_crypto_macro
from analytics.models import AnalysisConfig, PointInTimeUnavailable
from collectors.binance import DataCollectionError, fetch_binance_ohlcv
from models.macro import FredError, FredKeyError
from services.crypto_macro_service import load_crypto_macro_analysis
from services.macro_service import _load_cached
from services.market_data import load_market_data
from test_app import APP_PATH, full_fixture_frame
from test_crypto_macro import prices, economics
from test_fred import TEST_KEY
from test_macro_ui import mock_series
from test_market_data import fake_ohlcv_page
from ui.crypto_macro_charts import normalized_trend_chart, correlation_scatter_chart, lag_correlation_chart


def analysis_fixture(symbol="BTC/USDT", period="3년", config=None):
    config = config or AnalysisConfig("CPIAUCSL")
    return analyze_crypto_macro(prices(), economics(config.series_id), config,
                                start="2023-01-01T00:00Z", end="2025-01-01T00:00Z", symbol=symbol)


class CryptoMacroUITests(unittest.TestCase):
    def setUp(self):
        load_market_data.clear()
        _load_cached.clear()
        key = patch("services.macro_service.get_api_key", return_value=TEST_KEY)
        key.start()
        self.addCleanup(key.stop)
        market = patch("services.market_data.fetch_binance_ohlcv", return_value=full_fixture_frame())
        self.market = market.start()
        self.addCleanup(market.stop)

    def tearDown(self):
        load_market_data.clear()
        _load_cached.clear()

    def app(self):
        app = AppTest.from_file(str(APP_PATH), default_timeout=30).run()
        self.assertFalse(app.exception)
        return app

    def test_manual_run_displays_statistics_and_three_charts(self):
        with patch("ui.crypto_macro_view.load_crypto_macro_analysis", side_effect=analysis_fixture) as fetch:
            app = self.app()
            fetch.assert_not_called()
            app.button(key="load_crypto_macro").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(len(app.get("plotly_chart")), 4)
            self.assertTrue(any(metric.label == "Pearson · lag 0" for metric in app.metric))
            self.assertEqual(app.metric[-1].value, "24개")
            captions = " ".join(item.value for item in app.caption)
            self.assertIn("NaN/무한값", captions)
            self.assertIn("시차", captions)
            self.assertNotIn(TEST_KEY, str(app.session_state["_cm_result"]))

    def test_indicator_and_other_sidebar_selection_do_not_repeat_fetch(self):
        with patch("ui.crypto_macro_view.load_crypto_macro_analysis", side_effect=analysis_fixture) as fetch:
            app = self.app()
            app.button(key="load_crypto_macro").click().run()
            app.checkbox(key="show_rsi").uncheck().run()
            app.checkbox(key="macro_cpi").check().run()
            self.assertFalse(app.exception)
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(self.market.call_count, 1)
            self.assertEqual(len(app.get("plotly_chart")), 4)

    def test_changed_symbol_period_transform_partial_and_timeframe_invalidate(self):
        with patch("ui.crypto_macro_view.load_crypto_macro_analysis", side_effect=analysis_fixture) as fetch:
            app = self.app()
            changes = [("symbol", "ETH/USDT"), ("cm_period", "5년"),
                       ("cm_transform_CPIAUCSL", "yoy_pct"), ("cm_timeframe", "4h")]
            for key, value in changes:
                app.button(key="load_crypto_macro").click().run()
                app.selectbox(key=key).select(value).run()
                self.assertFalse(app.exception)
                self.assertFalse(any(metric.label.startswith("Pearson") for metric in app.metric))
                self.assertTrue(any("무효화" in item.value for item in app.info))
            app.button(key="load_crypto_macro").click().run()
            app.checkbox(key="cm_partial").check().run()
            self.assertFalse(any(metric.label.startswith("Pearson") for metric in app.metric))
            self.assertEqual(fetch.call_count, 5)

    def test_missing_key_and_point_in_time_disable_requests(self):
        with patch("ui.crypto_macro_view.load_crypto_macro_analysis") as fetch:
            with patch("services.macro_service.get_api_key", side_effect=FredKeyError("FRED_API_KEY가 없습니다.")):
                app = self.app()
                self.assertTrue(app.button(key="load_crypto_macro").disabled)
            app = self.app()
            app.selectbox(key="cm_mode").select("point_in_time").run()
            self.assertTrue(app.button(key="load_crypto_macro").disabled)
            self.assertTrue(any("발표 시각/빈티지" in info.value for info in app.info))
            fetch.assert_not_called()

    def test_removed_key_clears_old_statistics(self):
        with patch("ui.crypto_macro_view.load_crypto_macro_analysis", side_effect=analysis_fixture):
            app = self.app()
            app.button(key="load_crypto_macro").click().run()
            with patch("services.macro_service.get_api_key", side_effect=FredKeyError("FRED_API_KEY가 없습니다.")):
                app.run()
            self.assertFalse(app.exception)
            self.assertFalse(any(metric.label.startswith("Pearson") for metric in app.metric))

    def test_fred_and_binance_errors_are_visible_without_fabricated_statistics(self):
        for error in (FredError("모의 네트워크 오류", category="TIMEOUT"), DataCollectionError("모의 가격 조회 실패")):
            with self.subTest(error=type(error).__name__), patch("ui.crypto_macro_view.load_crypto_macro_analysis", side_effect=error):
                app = self.app()
                app.button(key="load_crypto_macro").click().run()
                self.assertFalse(app.exception)
                self.assertTrue(app.error)
                self.assertFalse(any(metric.label.startswith("Pearson") for metric in app.metric))
                self.assertEqual(len(app.get("plotly_chart")), 1)

    def test_dgs_aggregation_changes_invalidate_and_na_is_explicit(self):
        with patch("ui.crypto_macro_view.load_crypto_macro_analysis", side_effect=analysis_fixture):
            app = self.app()
            app.selectbox(key="cm_series").select("DGS10").run()
            app.button(key="load_crypto_macro").click().run()
            self.assertFalse(app.exception)
            app.selectbox(key="cm_dgs_aggregation").select("mean").run()
            self.assertFalse(any(metric.label.startswith("Pearson") for metric in app.metric))
        result = analysis_fixture(config=AnalysisConfig("CPIAUCSL", min_samples=36))
        with patch("ui.crypto_macro_view.load_crypto_macro_analysis", return_value=result):
            app = self.app()
            app.button(key="load_crypto_macro").click().run()
            self.assertFalse(app.exception)
            self.assertEqual([m.value for m in app.metric if m.label.startswith(("Pearson", "Spearman"))], ["N/A", "N/A"])

    def test_charts_preserve_units_sample_counts_and_gaps(self):
        result = analysis_fixture()
        trend = normalized_trend_chart(result)
        scatter = correlation_scatter_chart(result)
        lags = lag_correlation_chart(result)
        self.assertEqual(len(trend.data), 2)
        self.assertFalse(trend.data[0].connectgaps)
        self.assertEqual(trend.data[0].y[0], 100)
        self.assertIn("전월 대비", scatter.layout.xaxis.title.text)
        self.assertIn("수익률 (%)", scatter.layout.yaxis.title.text)
        self.assertEqual(len(scatter.data[0].x), result.correlation.n_used)
        self.assertEqual(list(lags.data[0].x), [0, 1, 2, 3])
        self.assertEqual(list(lags.data[0].customdata[1])[:2], [23, 23])
        for chart in (trend, scatter, lags):
            self.assertNotIn(TEST_KEY, chart.to_json())


class CryptoMacroServiceTests(unittest.TestCase):
    def tearDown(self):
        load_market_data.clear()
        _load_cached.clear()

    def test_source_caches_reused_across_transform_changes_with_no_extra_warmup_candles(self):
        load_market_data.clear()
        _load_cached.clear()
        exchange = Mock()
        exchange.fetch_ohlcv.side_effect = fake_ohlcv_page
        def collector(*args, **kwargs):
            return fetch_binance_ohlcv(*args, **kwargs)
        with patch("collectors.binance._get_exchange", return_value=exchange), \
                patch("services.market_data.fetch_binance_ohlcv", side_effect=collector) as fetch_prices, \
                patch("services.macro_service.get_api_key", return_value=TEST_KEY), \
                patch("services.macro_service.fetch_fred_series", side_effect=mock_series) as fetch_macro:
            first = load_crypto_macro_analysis("BTC/USDT", "3년", AnalysisConfig("CPIAUCSL"))
            second = load_crypto_macro_analysis("BTC/USDT", "3년", AnalysisConfig("CPIAUCSL", transform="yoy_pct"))
            self.assertEqual(fetch_prices.call_count, 1)
            self.assertEqual(fetch_macro.call_count, 1)
            self.assertEqual(fetch_prices.call_args.kwargs["warmup_candles"], 0)
            self.assertGreaterEqual(first.correlation.n_used, 35)
            self.assertEqual(second.correlation.n_used, first.correlation.n_used)
            self.assertLessEqual(exchange.fetch_ohlcv.call_count, 7)

    def test_explicit_start_keeps_original_cap_and_validation(self):
        exchange = Mock()
        exchange.fetch_ohlcv.side_effect = fake_ohlcv_page
        with patch("collectors.binance._get_exchange", return_value=exchange):
            frame = fetch_binance_ohlcv("ETH/USDT", "1d", "1년", now=pd.Timestamp("2026-10-15T12:00Z"),
                                        start_time=pd.Timestamp("2000-01-01T00:00Z"))
            self.assertEqual(len(frame), 5000)
            self.assertTrue(frame.attrs["is_partial"])
            self.assertEqual(frame.attrs["request_count"], 5)
            for start in (pd.Timestamp("2026-10-16T00:00Z"), pd.Timestamp("2020-01-01"), pd.NaT):
                with self.subTest(start=start), self.assertRaises(ValueError):
                    fetch_binance_ohlcv("BTC/USDT", "1d", "1년", start_time=start,
                                        now=pd.Timestamp("2026-10-15T12:00Z"))

    def test_missing_key_and_pit_fail_before_any_network_request(self):
        with patch("services.crypto_macro_service.load_market_data") as crypto, \
                patch("services.crypto_macro_service.load_macro_series") as macro:
            with patch("services.macro_service.get_api_key", side_effect=FredKeyError("FRED_API_KEY가 없습니다.")):
                with self.assertRaises(FredKeyError):
                    load_crypto_macro_analysis("BTC/USDT", "3년", AnalysisConfig("CPIAUCSL"))
            config = AnalysisConfig("CPIAUCSL")
            object.__setattr__(config, "mode", "point_in_time")
            with self.assertRaises(PointInTimeUnavailable):
                load_crypto_macro_analysis("BTC/USDT", "3년", config)
            crypto.assert_not_called()
            macro.assert_not_called()
