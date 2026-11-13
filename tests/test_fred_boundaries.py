"""실 FRED에서 확인한 월별 겹침 라벨과 엄격한 표시/일별 경계의 회귀 검증."""

from datetime import date
from unittest.mock import patch
import unittest

import pandas as pd

from collectors.fred import fetch_fred_series, normalize_observations
from models.macro import FredError, SERIES, parse_macro_date
from services.macro_service import _load_cached, load_macro_series
from test_fred import TEST_KEY, metadata, observation, observations_payload
from test_macro_service import cpi_frame


def fetch_mock(series_id, start, end, rows):
    with patch("collectors.fred._request_json", side_effect=[metadata(series_id), observations_payload(rows)]):
        return fetch_fred_series(series_id, start, end, api_key=TEST_KEY)


class FredBoundaryTests(unittest.TestCase):
    def test_start_month_overlap_allowed_but_excluded_for_all_monthly_series(self):
        for series_id in ("CPIAUCSL", "FEDFUNDS", "UNRATE", "PAYEMS"):
            with self.subTest(series_id=series_id):
                frame = fetch_mock(series_id, "2024-01-15", "2024-03-15", [
                    observation("2024-01-01", "."), observation("2024-02-01", "1"), observation("2024-03-01", "2"),
                ])
                self.assertEqual(frame.observation_date.dt.strftime("%Y-%m-%d").tolist(), ["2024-02-01", "2024-03-01"])
                self.assertEqual(frame.attrs["boundary_observations_removed"], 1)
                self.assertEqual(frame.attrs["missing_values"], 0)
                self.assertEqual(frame.attrs["requested_start"], date(2024, 1, 15))
                self.assertEqual(frame.attrs["allowed_response_start"], date(2024, 1, 1))
                self.assertTrue(frame[["release_date", "available_at", "vintage_date"]].isna().all().all())

    def test_start_and_end_labels_are_inclusive(self):
        frame = fetch_mock("FEDFUNDS", "2024-01-01", "2024-02-01", [
            observation("2024-01-01"), observation("2024-02-01"),
        ])
        self.assertEqual(len(frame), 2)
        self.assertEqual(frame.attrs["boundary_observations_removed"], 0)

    def test_previous_month_is_invalid_and_not_silently_discarded(self):
        with self.assertRaises(FredError) as context:
            fetch_mock("FEDFUNDS", "2024-01-15", "2024-03-15", [
                observation("2023-12-01"), observation("2024-01-01"), observation("2024-02-01"),
            ])
        self.assertEqual(context.exception.category, "DATA_VALIDATION")

    def test_month_after_end_is_invalid(self):
        with self.assertRaises(FredError) as context:
            fetch_mock("FEDFUNDS", "2024-01-15", "2024-03-15", [
                observation("2024-02-01"), observation("2024-04-01"),
            ])
        self.assertEqual(context.exception.category, "DATA_VALIDATION")

    def test_monthly_nonfirst_day_remains_invalid(self):
        with self.assertRaises(FredError) as context:
            fetch_mock("FEDFUNDS", "2024-01-15", "2024-03-15", [observation("2024-01-16")])
        self.assertEqual(context.exception.category, "DATA_VALIDATION")

    def test_daily_exact_boundaries_included_without_rounding(self):
        frame = fetch_mock("DGS10", "2024-01-15", "2024-03-15", [
            observation("2024-01-15", "."), observation("2024-03-15", "4.1"),
        ])
        self.assertEqual(frame.attrs["boundary_observations_removed"], 0)
        self.assertEqual(frame.attrs["allowed_response_start"], date(2024, 1, 15))
        self.assertEqual(frame.attrs["missing_values"], 1)
        self.assertEqual(len(frame), 2)

    def test_daily_outside_dates_on_either_side_are_invalid(self):
        for day in ("2024-01-14", "2024-03-16"):
            with self.subTest(day=day), self.assertRaises(FredError) as context:
                fetch_mock("DGS10", "2024-01-15", "2024-03-15", [observation(day)])
            self.assertEqual(context.exception.category, "DATA_VALIDATION")

    def test_leap_day_and_year_transition(self):
        for start, end, old, expected in (("2024-02-29", "2024-03-01", "2024-02-01", "2024-03-01"),
                                          ("2023-12-31", "2024-01-01", "2023-12-01", "2024-01-01")):
            with self.subTest(start=start):
                frame = fetch_mock("FEDFUNDS", start, end, [observation(old), observation(expected)])
                self.assertEqual(frame.observation_date.dt.strftime("%Y-%m-%d").tolist(), [expected])

    def test_only_overlap_is_empty_in_display_window_not_bad_api_response(self):
        with self.assertRaises(FredError) as context:
            fetch_mock("FEDFUNDS", "2024-01-15", "2024-01-20", [observation("2024-01-01")])
        self.assertEqual(context.exception.category, "EMPTY_RESPONSE")

    def test_iso_calendar_dates_reject_timezone_or_time_strings(self):
        self.assertEqual(parse_macro_date("2024-02-29"), date(2024, 2, 29))
        for value in ("2025-02-29", "2024-01-01T00:00:00Z", "2024-01-01T00:00:00+09:00",
                      "2024-01-01 00:00:00", "2024-1-1", "2024/01/01", pd.Timestamp("2024-01-01")):
            with self.subTest(value=value), self.assertRaises(FredError):
                parse_macro_date(value)

    def test_utc_collection_conversion_never_moves_observation_date(self):
        frame = normalize_observations("FEDFUNDS", [observation("2024-01-01")],
                                       fetched_at=pd.Timestamp("2024-05-01T00:30:00+09:00"))
        self.assertEqual(frame.observation_date.iloc[0], pd.Timestamp("2024-01-01T00:00Z"))
        self.assertEqual(frame.attrs["fetched_at"], pd.Timestamp("2024-04-30T15:30Z"))

    def test_model_response_window_distinguishes_monthly_from_daily(self):
        start, end = date(2024, 1, 15), date(2024, 3, 15)
        self.assertEqual(SERIES["FEDFUNDS"].response_date_bounds(start, end), (date(2024, 1, 1), end))
        self.assertEqual(SERIES["DGS10"].response_date_bounds(start, end), (start, end))


class MacroDisplayBoundaryTests(unittest.TestCase):
    def setUp(self):
        _load_cached.clear()

    def tearDown(self):
        _load_cached.clear()

    def test_cpi_warmup_kept_for_calculation_but_both_display_limits_enforced(self):
        with patch("services.macro_service.get_api_key", return_value=TEST_KEY), \
                patch("services.macro_service.fetch_fred_series", return_value=cpi_frame()) as fetch:
            result = load_macro_series("CPIAUCSL", "2024-01-15", "2024-03-15")
            self.assertEqual(result.observation_date.dt.strftime("%Y-%m-%d").tolist(), ["2024-02-01", "2024-03-01"])
            self.assertAlmostEqual(result.yoy_pct.iloc[0], (113/101-1)*100)
            fetch.assert_called_once_with("CPIAUCSL", "2023-01-01", "2024-03-15", api_key=TEST_KEY)

    def test_invalid_service_dates_rejected_before_key_or_network(self):
        for start, end in (("2024-01-15T00:00:00Z", "2024-03-15"),
                           ("2024-01-15", "2024-03-15T00:00:00+09:00"),
                           ("2024-04-15", "2024-03-15")):
            with self.subTest(start=start), patch("services.macro_service.get_api_key") as key, \
                    patch("services.macro_service.fetch_fred_series") as fetch:
                with self.assertRaises(FredError):
                    load_macro_series("FEDFUNDS", start, end)
                key.assert_not_called()
                fetch.assert_not_called()
