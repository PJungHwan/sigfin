"""사용자가 명시적으로 활성화하고 본인의 키가 있을 때만 수행하는 실 API 검증."""

import os
import unittest

from collectors.fred import get_api_key, fetch_fred_series
from models.macro import FredKeyError, SERIES


@unittest.skipUnless(os.getenv("RUN_FRED_LIVE_TESTS") == "1", "실제 FRED 키 및 명시적 활성화가 필요합니다.")
class FredLiveTests(unittest.TestCase):
    def setUp(self):
        try:
            get_api_key()
        except FredKeyError:
            self.skipTest("프로젝트에 유효한 사용자 FRED 키가 없습니다.")

    def test_real_series_units_frequency_and_observation_dates(self):
        try:
            key = get_api_key()
        except FredKeyError:
            self.skipTest("프로젝트에 유효한 사용자 FRED 키가 없습니다.")
        import pandas as pd

        end = pd.Timestamp.now(tz="UTC").date().isoformat()
        start = (pd.Timestamp(end)-pd.DateOffset(years=1)).date().isoformat()
        for series_id, spec in SERIES.items():
            with self.subTest(series_id=series_id):
                frame = fetch_fred_series(series_id, start, end, api_key=key)
                self.assertFalse(frame.empty)
                self.assertTrue(frame.observation_date.is_monotonic_increasing)
                self.assertTrue(frame.observation_date.is_unique)
                self.assertEqual(frame.attrs["units"], spec.units)
                self.assertEqual(frame.attrs["frequency"], spec.frequency)
                self.assertTrue(frame.release_date.isna().all())
                self.assertTrue(frame.observation_date.dt.date.between(pd.Timestamp(start).date(), pd.Timestamp(end).date()).all())
                print(f"LIVE FRED OK {series_id}: rows={len(frame)}, "
                      f"first={frame.observation_date.min().date()}, last={frame.observation_date.max().date()}, "
                      f"boundary_removed={frame.attrs['boundary_observations_removed']}", flush=True)

    def test_real_monthly_overlap_and_daily_exact_boundaries(self):
        import pandas as pd

        for series_id in SERIES:
            with self.subTest(series_id=series_id):
                frame = fetch_fred_series(series_id, "2024-01-15", "2024-03-15")
                if SERIES[series_id].frequency == "M":
                    self.assertEqual(frame.observation_date.dt.strftime("%Y-%m-%d").tolist(), ["2024-02-01", "2024-03-01"])
                    self.assertEqual(frame.attrs["boundary_observations_removed"], 1)
                else:
                    self.assertEqual(frame.observation_date.min(), pd.Timestamp("2024-01-15T00:00Z"))
                    self.assertEqual(frame.observation_date.max(), pd.Timestamp("2024-03-15T00:00Z"))
                    self.assertEqual(frame.attrs["boundary_observations_removed"], 0)

    def test_real_service_cpi_yoy_and_all_series_display_window(self):
        import pandas as pd
        from services.macro_service import load_macro_series

        end = pd.Timestamp.now(tz="UTC").date().isoformat()
        start = (pd.Timestamp(end)-pd.DateOffset(years=1)).date().isoformat()
        for series_id in SERIES:
            with self.subTest(series_id=series_id):
                frame = load_macro_series(series_id, start, end, refresh=True)
                self.assertFalse(frame.empty)
                self.assertTrue(frame.observation_date.dt.date.between(pd.Timestamp(start).date(), pd.Timestamp(end).date()).all())
                if series_id == "CPIAUCSL":
                    self.assertTrue(frame.yoy_pct.notna().all())
                self.assertTrue(frame.available_at.isna().all())
