"""모든 가격/거시 값은 수작업 검산용 합성 데이터이며 실제 API 데이터가 아니다."""

import unittest

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from analytics.alignment import (closed_prices, crypto_monthly, periodic_crypto_returns, macro_monthly,
                                 align_monthly, normalized_levels)
from analytics.analysis import analyze_crypto_macro
from analytics.correlation import calculate_correlation
from analytics.lag_analysis import analyze_lags
from analytics.models import AnalysisConfig, AnalysisError, PointInTimeUnavailable
from models.macro import SERIES


def prices(start="2022-12-01", end="2025-01-01", *, freq="D"):
    dates = pd.date_range(start, end, freq=freq, inclusive="left", tz="UTC")
    # 각 월 내 동일한 종가, 월간 +2%: 월말 선택을 쉽게 검산할 수 있다.
    months = dates.tz_localize(None).to_period("M")
    values = 100 * 1.02 ** (months.astype("int64")-months[0].ordinal) if len(dates) else []
    return pd.DataFrame({"timestamp": dates, "open": values, "high": np.asarray(values)*1.01,
                         "low": np.asarray(values)*.99, "close": values, "volume": 1000.})


def economics(series_id="CPIAUCSL", start="2021-12-01", end="2025-01-01", values=None):
    spec = SERIES[series_id]
    dates = pd.date_range(start, end, freq="MS" if spec.frequency == "M" else "B", inclusive="left", tz="UTC")
    if values is None:
        values = 100 + np.arange(len(dates), dtype=float)
    return pd.DataFrame({"observation_date": dates, "value": values, "series_id": series_id,
                         "units": spec.units, "frequency": spec.frequency,
                         "release_date": pd.NaT, "available_at": pd.NaT, "vintage_date": None})


class AlignmentTests(unittest.TestCase):
    def test_supported_source_timeframes_have_same_monthly_returns(self):
        for timeframe, frequency in (("5m", "5min"), ("15m", "15min"), ("1h", "h"), ("4h", "4h"), ("1d", "D")):
            with self.subTest(timeframe=timeframe):
                data = prices("2023-12-01", "2024-03-01", freq=frequency)
                result = crypto_monthly(data, timeframe, as_of="2024-03-01T00:00Z")
                self.assertAlmostEqual(result.loc["2024-02", "crypto_return_pct"], 2)
                self.assertFalse(result.loc["2024-02", "crypto_partial"])

    def test_month_end_candle_belongs_to_month_it_closes(self):
        data = prices("2023-12-01", "2024-03-01")
        data.loc[data.timestamp == pd.Timestamp("2024-01-31", tz="UTC"), "close"] = 110
        data.loc[data.timestamp == pd.Timestamp("2024-02-29", tz="UTC"), "close"] = 121
        result = crypto_monthly(data, "1d", as_of="2024-03-01T00:00Z")
        self.assertEqual(result.loc["2024-01", "crypto_close"], 110)
        self.assertEqual(result.loc["2024-02", "candle_end"], pd.Timestamp("2024-03-01T00:00Z"))
        self.assertAlmostEqual(result.loc["2024-02", "crypto_return_pct"], 10)
        self.assertFalse(result.loc["2024-02", "crypto_partial"])
        self.assertEqual(result.loc["2024-02", "expected_candles"], 29)

    def test_first_month_without_baseline_is_nan(self):
        result = crypto_monthly(prices(), "1d", as_of="2025-01-01T00:00Z")
        self.assertTrue(pd.isna(result.iloc[0].crypto_return_pct))
        self.assertFalse(result.iloc[0].baseline_available)
        self.assertAlmostEqual(result.iloc[1].crypto_return_pct, 2)

    def test_missing_month_is_not_compressed_or_filled(self):
        data = prices()
        data = data.loc[data.timestamp.dt.month != 2]
        result = crypto_monthly(data, "1d", as_of="2025-01-01T00:00Z")
        self.assertTrue(pd.isna(result.loc["2024-02", "crypto_close"]))
        self.assertTrue(pd.isna(result.loc["2024-03", "crypto_return_pct"]))
        self.assertFalse(result.loc["2024-03", "baseline_available"])

    def test_incomplete_month_and_gap_are_flagged(self):
        data = prices("2023-12-01", "2024-03-15")
        data = data.loc[data.timestamp != pd.Timestamp("2024-01-05", tz="UTC")]
        result = crypto_monthly(data, "1d", as_of="2024-03-15T12:00Z")
        self.assertTrue(result.loc["2024-01", "crypto_partial"])
        self.assertFalse(result.loc["2024-02", "crypto_partial"])
        self.assertTrue(result.loc["2024-03", "crypto_partial"])

    def test_current_unconfirmed_and_future_prices_are_excluded(self):
        data = prices("2024-01-01", "2024-01-04")
        data.loc[2, "close"] = 9999
        result = closed_prices(data, "1d", "2024-01-03T12:00Z")
        self.assertEqual(len(result), 2)
        self.assertLess(result.close.max(), 9999)

    def test_timezone_conversion_and_bad_times(self):
        data = prices("2024-01-01", "2024-01-03")
        data.timestamp = data.timestamp.dt.tz_convert("Asia/Seoul")
        result = closed_prices(data, "1d", "2024-01-03T09:00+09:00")
        self.assertEqual(str(result.timestamp.dt.tz), "UTC")
        for value in (None, "2024-01-03", "bad"):
            with self.subTest(value=value), self.assertRaises(AnalysisError):
                closed_prices(data, "1d", value)
        data.timestamp += pd.Timedelta(minutes=1)
        with self.assertRaises(AnalysisError):
            closed_prices(data, "1d", "2024-01-03T00:00Z")

    def test_duplicate_unsorted_and_invalid_prices_rejected(self):
        data = prices("2024-01-01", "2024-01-04")
        bad = [data.iloc[::-1], pd.concat([data, data.iloc[:1]])]
        for value in (0, -1, np.inf, np.nan):
            sample = data.copy()
            sample.loc[0, "close"] = value
            bad.append(sample)
        for sample in bad:
            with self.subTest(), self.assertRaises(AnalysisError):
                closed_prices(sample, "1d", "2024-01-04T00:00Z")

    def test_hourly_daily_returns_and_no_upsampling(self):
        data = prices("2024-01-01", "2024-01-04", freq="15min")
        data.close = 100 + np.arange(len(data), dtype=float)
        result = periodic_crypto_returns(data, "15m", rule="1h", as_of="2024-01-04T00:00Z")
        self.assertEqual(result.iloc[0].close, 103)
        self.assertTrue(pd.isna(result.iloc[0].return_pct))
        self.assertAlmostEqual(result.iloc[1].return_pct, (107/103-1)*100)
        daily = periodic_crypto_returns(data, "15m", rule="1d", as_of="2024-01-04T00:00Z")
        self.assertAlmostEqual(daily.iloc[1].return_pct, (291/195-1)*100)
        self.assertTrue(periodic_crypto_returns(prices(), "1d", rule="1h", as_of="2025-01-01T00:00Z").empty)

    def test_hourly_gaps_do_not_create_returns_across_missing_bin(self):
        data = prices("2024-01-01", "2024-01-02", freq="15min")
        data = data.loc[~data.timestamp.dt.hour.eq(2)]
        result = periodic_crypto_returns(data, "15m", rule="1h", as_of="2024-01-02T00:00Z")
        self.assertTrue(result.loc["2024-01-01T02:00Z":"2024-01-01T03:00Z", "return_pct"].isna().all())

    def test_common_alignment_boundaries_and_partial_policy(self):
        crypto = crypto_monthly(prices(), "1d", as_of="2024-12-15T00:00Z")
        macro, _ = macro_monthly(economics(), AnalysisConfig("CPIAUCSL"), as_of="2024-12-15T00:00Z")
        result = align_monthly(crypto, macro, start="2024-01-15T00:00Z", end="2024-12-15T00:00Z")
        self.assertEqual(len(result), 12)
        self.assertFalse(result.iloc[0].crypto_eligible)
        self.assertFalse(result.iloc[-1].macro_eligible)
        included = align_monthly(crypto, macro, start="2024-01-15T00:00Z", end="2024-12-15T00:00Z", include_partial=True)
        self.assertTrue(included.crypto_eligible.all())

    def test_normalized_levels_use_common_positive_base_and_keep_gaps(self):
        data = pd.DataFrame({"crypto_close": [100, 110, np.nan, 120], "macro_level": [np.nan, 5, 6, 0]},
                            index=pd.period_range("2024-01", periods=4, freq="M"))
        normalized = normalized_levels(data)
        self.assertEqual(normalized.attrs["base_month"], "2024-02")
        self.assertEqual(normalized.iloc[0].crypto_index, 100)
        self.assertEqual(normalized.iloc[1].macro_index, 120)
        self.assertTrue(pd.isna(normalized.iloc[1].crypto_index))
        data.loc["2024-02", "macro_level"] = 0
        normalized = normalized_levels(data)
        self.assertTrue(normalized.macro_index.isna().all())
        self.assertIn("excluded_reason", normalized.attrs)


class MacroTransformationTests(unittest.TestCase):
    def test_cpi_level_mom_and_yoy_exact_calendar(self):
        data = economics(values=np.arange(100., 137.))
        result, _ = macro_monthly(data, AnalysisConfig("CPIAUCSL"), as_of="2025-01-01T00:00Z")
        self.assertEqual(result.loc["2022-12", "macro_level"], 112)
        self.assertAlmostEqual(result.loc["2022-12", "macro_yoy_pct"], 12)
        self.assertAlmostEqual(result.iloc[1].macro_mom_pct, 1)
        self.assertTrue(result.macro_yoy_pct.iloc[:12].isna().all())
        annual, _ = macro_monthly(data, AnalysisConfig("CPIAUCSL", transform="yoy_pct"), as_of="2025-01-01T00:00Z")
        self.assertEqual(annual.loc["2022-12", "macro_change"], result.loc["2022-12", "macro_yoy_pct"])

    def test_cpi_missing_month_does_not_become_previous_year(self):
        data = economics().drop(index=12)
        result, _ = macro_monthly(data, AnalysisConfig("CPIAUCSL", transform="yoy_pct"), as_of="2025-01-01T00:00Z")
        self.assertTrue(pd.isna(result.loc["2023-12", "macro_yoy_pct"]))
        self.assertTrue(pd.isna(result.loc["2023-01", "macro_mom_pct"]))

    def test_rate_changes_are_percentage_points_and_payroll_preserves_thousands(self):
        for series, values, expected in (("FEDFUNDS", [5., 5.25, 5.], .25),
                                          ("UNRATE", [4., 4.1, 3.9], .1),
                                          ("PAYEMS", [150000., 150200., 150100.], 200.)):
            with self.subTest(series=series):
                data = economics(series, "2024-01-01", "2024-04-01", values)
                result, _ = macro_monthly(data, AnalysisConfig(series), as_of="2024-04-01T00:00Z")
                self.assertAlmostEqual(result.loc["2024-02", "macro_change"], expected)

    def test_dgs10_last_valid_vs_mean_and_daily_changes(self):
        data = economics("DGS10", "2024-01-29", "2024-02-03", [4., 6., np.nan, 5., 7.])
        last, daily = macro_monthly(data, AnalysisConfig("DGS10"), as_of="2024-03-01T00:00Z")
        mean, _ = macro_monthly(data, AnalysisConfig("DGS10", dgs_aggregation="mean"), as_of="2024-03-01T00:00Z")
        self.assertEqual(last.loc["2024-01", "macro_level"], 6)
        self.assertEqual(last.loc["2024-02", "macro_change"], 1)
        self.assertEqual(mean.loc["2024-02", "macro_change"], 1)
        self.assertEqual(mean.loc["2024-01", "macro_level"], 5)
        self.assertEqual(last.loc["2024-01", "macro_observation_date"], pd.Timestamp("2024-01-30T00:00Z"))
        self.assertEqual(daily.daily_change_pp.iloc[1], 2)
        self.assertTrue(pd.isna(daily.daily_change_pp.iloc[3]))

    def test_metadata_month_labels_missing_values_and_no_release_invention(self):
        data = economics("FEDFUNDS", "2024-01-01", "2024-04-01", [5., np.nan, 5.5])
        result, _ = macro_monthly(data, AnalysisConfig("FEDFUNDS"), as_of="2024-04-01T00:00Z")
        self.assertTrue(pd.isna(result.loc["2024-03", "macro_change"]))
        self.assertTrue(data.release_date.isna().all())
        for column, value in (("units", "wrong"), ("frequency", "D"), ("series_id", "CPIAUCSL"),
                              ("observation_date", pd.Timestamp("2024-01-02T00:00Z")), ("value", np.inf)):
            sample = data.copy()
            sample.loc[0, column] = value
            with self.subTest(column=column), self.assertRaises(AnalysisError):
                macro_monthly(sample, AnalysisConfig("FEDFUNDS"), as_of="2024-04-01T00:00Z")


class CorrelationAndLagTests(unittest.TestCase):
    def test_known_pearson_and_spearman(self):
        result = calculate_correlation(pd.Series([1., 2., 3., 4.]), pd.Series([4., 1., 3., 2.]), min_samples=3)
        self.assertAlmostEqual(result.pearson, -.4)
        self.assertAlmostEqual(result.spearman, -.4)
        self.assertEqual(result.status, "OK")
        for sign in (-1, 1):
            actual = calculate_correlation(pd.Series([1, 2, 3]), pd.Series([1, 2, 3])*sign, min_samples=3)
            self.assertAlmostEqual(actual.pearson, sign)
            self.assertAlmostEqual(actual.spearman, sign)

    def test_spearman_average_ties_and_numeric_strings(self):
        actual = calculate_correlation(pd.Series(["1", "1", "10", "20"]), pd.Series([1, 2, 3, 4]), min_samples=3)
        # ranks [1.5,1.5,3,4], [1,2,3,4]: 4.5/sqrt(4.5*5)
        self.assertAlmostEqual(actual.spearman, 4.5/np.sqrt(22.5))

    def test_nan_inf_counts_and_minimum_and_constant_series(self):
        actual = calculate_correlation(pd.Series([1., np.nan, 3., np.inf, 5.]), pd.Series([1., 2., 3., 4., 5.]), min_samples=3)
        self.assertEqual((actual.n_before, actual.n_used, actual.n_removed), (5, 3, 2))
        self.assertAlmostEqual(actual.pearson, 1)
        short = calculate_correlation(pd.Series([1, 2]), pd.Series([2, 3]))
        self.assertIsNone(short.pearson)
        self.assertEqual(short.status, "INSUFFICIENT_SAMPLES")
        for constant in (0., 5.):
            actual = calculate_correlation(pd.Series([constant]*12), pd.Series(range(12)))
            self.assertEqual(actual.status, "CONSTANT_SERIES")
            self.assertIsNone(actual.spearman)

    def test_common_index_only_and_duplicate_rejection(self):
        result = calculate_correlation(pd.Series([1, 2, 3, 4], index=[1, 2, 3, 4]),
                                       pd.Series([10, 20, 30], index=[2, 3, 4]), min_samples=3)
        self.assertEqual(result.n_before, 3)
        self.assertAlmostEqual(result.pearson, 1)
        with self.assertRaises(AnalysisError):
            calculate_correlation(pd.Series([1, 2], index=[0, 0]), pd.Series([1, 2]))

    def lag_frame(self):
        return pd.DataFrame({"macro_change": [2., -1., 4., 1., -3., 8.],
                             "crypto_return_pct": [50., 2., -1., 4., 1., -3.],
                             "macro_eligible": True, "crypto_eligible": True},
                            index=pd.period_range("2024-01", periods=6, freq="M"))

    def test_lag_direction_uses_later_return_and_per_lag_sample_counts(self):
        stats, pairs = analyze_lags(self.lag_frame(), min_samples=3)
        self.assertAlmostEqual(stats.iloc[1].pearson, 1)
        self.assertEqual(stats.n_used.tolist(), [6, 5, 4, 3])
        pair = pairs.loc[(pairs.lag_months == 1) & pairs.used].iloc[0]
        self.assertEqual((pair.macro_month, pair.crypto_month), ("2024-01", "2024-02"))
        self.assertEqual(pair.crypto_return_pct, 2)

    def test_lag_keeps_calendar_gaps_and_never_wraps(self):
        data = self.lag_frame().drop(index=pd.Period("2024-02", freq="M"))
        stats, pairs = analyze_lags(data, min_samples=3)
        january = pairs.loc[(pairs.lag_months == 1) & (pairs.macro_month == "2024-01")].iloc[0]
        self.assertFalse(january.used)
        self.assertEqual(january.crypto_month, "2024-02")
        self.assertFalse(pairs.loc[pairs.macro_month == "2024-06"].query("lag_months > 0").used.any())
        self.assertEqual(stats.iloc[1].n_used, 3)

    def test_partial_eligibility_applies_to_each_lag_target(self):
        data = self.lag_frame()
        data.loc["2024-06", "crypto_eligible"] = False
        stats, pairs = analyze_lags(data, min_samples=3)
        self.assertEqual(stats.n_used.tolist(), [5, 4, 3, 2])
        self.assertFalse(pairs.loc[pairs.crypto_month == "2024-06"].used.any())


class AnalysisSafetyTests(unittest.TestCase):
    def test_point_in_time_is_blocked_even_with_forged_metadata(self):
        with self.assertRaisesRegex(PointInTimeUnavailable, "발표 시각/빈티지"):
            AnalysisConfig("CPIAUCSL", mode="point_in_time")
        config = AnalysisConfig("CPIAUCSL")
        object.__setattr__(config, "mode", "point_in_time")
        data = economics()
        data["available_at"] = data.observation_date
        data["vintage_date"] = data.observation_date
        with self.assertRaises(PointInTimeUnavailable):
            analyze_crypto_macro(prices(), data, config, start="2023-01-01T00:00Z", end="2025-01-01T00:00Z")

    def test_config_validation(self):
        for kwargs in ({"series_id": "BAD"}, {"series_id": "UNRATE", "transform": "mom_pct"},
                       {"series_id": "DGS10", "dgs_aggregation": "median"},
                       {"series_id": "CPIAUCSL", "min_samples": 2},
                       {"series_id": "CPIAUCSL", "timeframe": "bad"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(AnalysisError):
                AnalysisConfig(**kwargs)

    def test_full_analysis_is_immutable_and_future_rows_do_not_change_past(self):
        crypto, macro = prices(), economics()
        crypto_before, macro_before = crypto.copy(deep=True), macro.copy(deep=True)
        config = AnalysisConfig("CPIAUCSL")
        full = analyze_crypto_macro(crypto, macro, config, start="2023-01-01T00:00Z", end="2024-07-01T00:00Z")
        prefix = analyze_crypto_macro(crypto.loc[crypto.timestamp < "2024-07-01"],
                                      macro.loc[macro.observation_date < "2024-07-01"], config,
                                      start="2023-01-01T00:00Z", end="2024-07-01T00:00Z")
        assert_frame_equal(full.monthly, prefix.monthly)
        assert_frame_equal(full.lags, prefix.lags)
        assert_frame_equal(crypto, crypto_before)
        assert_frame_equal(macro, macro_before)
        self.assertEqual(full.correlation.n_used, 18)
        self.assertFalse(full.quality["is_point_in_time"])

    def test_empty_valid_frames_return_na_without_fake_values(self):
        result = analyze_crypto_macro(prices().iloc[:0], economics().iloc[:0], AnalysisConfig("CPIAUCSL"),
                                      start="2024-01-01T00:00Z", end="2024-03-01T00:00Z")
        self.assertEqual(result.correlation.n_used, 0)
        self.assertIsNone(result.correlation.pearson)
        self.assertTrue(result.normalized.empty)
