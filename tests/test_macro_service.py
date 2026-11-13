"""CPI 정확한 전년동월 비교, 표시 구간 워밍업, 자격별 캐시 분리 검증."""

from unittest.mock import patch
import unittest

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from collectors.fred import normalize_observations
from models.macro import FredError, FredKeyError
from services.macro_service import _load_cached, calculate_cpi_yoy, load_macro_series
from test_fred import TEST_KEY, observation


def cpi_frame():
    dates = pd.date_range("2023-01-01", periods=36, freq="MS")
    return normalize_observations("CPIAUCSL", [observation(d.strftime("%Y-%m-%d"), str(100+i)) for i, d in enumerate(dates)])


class MacroServiceTests(unittest.TestCase):
    def setUp(self):
        _load_cached.clear()

    def tearDown(self):
        _load_cached.clear()

    def test_cpi_known_values_no_initial_lookahead_and_immutability(self):
        source = cpi_frame()
        original = source.copy(deep=True)
        result = calculate_cpi_yoy(source)
        self.assertTrue(result.yoy_pct.iloc[:12].isna().all())
        self.assertAlmostEqual(result.yoy_pct.iloc[12], 12)
        self.assertAlmostEqual(result.yoy_pct.iloc[13], (113/101-1)*100)
        assert_frame_equal(source, original)
        assert_frame_equal(result.iloc[:20], calculate_cpi_yoy(source.iloc[:20]))

    def test_missing_month_cannot_shift_reference_to_wrong_month(self):
        frame = cpi_frame().drop(index=1).reset_index(drop=True)
        result = calculate_cpi_yoy(frame)
        feb = result.loc[result.observation_date == pd.Timestamp("2024-02-01T00:00Z")]
        self.assertTrue(feb.yoy_pct.isna().all())
        jan = result.loc[result.observation_date == pd.Timestamp("2024-01-01T00:00Z")]
        self.assertAlmostEqual(jan.yoy_pct.iloc[0], 12)

    def test_missing_and_zero_denominators_return_nan(self):
        source = cpi_frame()
        source.loc[0, "value"] = 0
        source.loc[1, "value"] = np.nan
        source.loc[14, "value"] = np.nan
        result = calculate_cpi_yoy(source)
        self.assertTrue(result.yoy_pct.iloc[[12, 13, 14]].isna().all())

    def test_wrong_series_and_duplicate_dates_rejected_for_cpi_calculation(self):
        for frame in (cpi_frame().assign(series_id="UNRATE"),
                      pd.concat([cpi_frame(), cpi_frame().iloc[[0]]])):
            with self.assertRaises(FredError):
                calculate_cpi_yoy(frame)

    def test_warmup_hidden_first_visible_yoy_valid_and_cache_copy_safe(self):
        with patch("services.macro_service.get_api_key", return_value=TEST_KEY), \
                patch("services.macro_service.fetch_fred_series", return_value=cpi_frame()) as fetch:
            first = load_macro_series("CPIAUCSL", "2024-01-01", "2025-12-31")
            self.assertEqual(first.observation_date.min(), pd.Timestamp("2024-01-01T00:00Z"))
            self.assertAlmostEqual(first.yoy_pct.iloc[0], 12)
            fetch.assert_called_once_with("CPIAUCSL", "2023-01-01", "2025-12-31", api_key=TEST_KEY)
            first.loc[first.index[0], "value"] = 9999
            second = load_macro_series("CPIAUCSL", "2024-01-01", "2025-12-31")
            self.assertEqual(second.value.iloc[0], 112)
            self.assertEqual(fetch.call_count, 1)

    def test_key_change_does_not_reuse_another_credentials_cache(self):
        with patch("services.macro_service.get_api_key", side_effect=[TEST_KEY, "1"*32]), \
                patch("services.macro_service.fetch_fred_series", return_value=cpi_frame()) as fetch:
            load_macro_series("CPIAUCSL", "2024-01-01", "2025-12-31")
            load_macro_series("CPIAUCSL", "2024-01-01", "2025-12-31")
            self.assertEqual(fetch.call_count, 2)

    def test_key_missing_cannot_serve_old_cached_result(self):
        with patch("services.macro_service.get_api_key", side_effect=[TEST_KEY, FredKeyError("missing")]), \
                patch("services.macro_service.fetch_fred_series", return_value=cpi_frame()):
            load_macro_series("CPIAUCSL", "2024-01-01", "2025-12-31")
            with self.assertRaises(FredKeyError):
                load_macro_series("CPIAUCSL", "2024-01-01", "2025-12-31")

    def test_refresh_clears_only_selected_query_and_errors_are_not_cached(self):
        with patch("services.macro_service.get_api_key", return_value=TEST_KEY), \
                patch("services.macro_service.fetch_fred_series", side_effect=[FredError("test network"), cpi_frame(), cpi_frame()]) as fetch:
            with self.assertRaises(FredError):
                load_macro_series("CPIAUCSL", "2024-01-01", "2025-12-31")
            load_macro_series("CPIAUCSL", "2024-01-01", "2025-12-31")
            load_macro_series("CPIAUCSL", "2024-01-01", "2025-12-31", refresh=True)
            self.assertEqual(fetch.call_count, 3)

    def test_no_daily_frequency_conversion_or_monthly_interpolation(self):
        frame = normalize_observations("DGS10", [observation("2024-01-02", "4.0"), observation("2024-01-04", ".")])
        with patch("services.macro_service.get_api_key", return_value=TEST_KEY), \
                patch("services.macro_service.fetch_fred_series", return_value=frame):
            result = load_macro_series("DGS10", "2024-01-01", "2024-12-31")
        self.assertEqual(len(result), 2)
        self.assertEqual(result.frequency.tolist(), ["D", "D"])
        self.assertTrue(pd.isna(result.value.iloc[-1]))
