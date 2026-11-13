"""수치 예제, 초기화, 결측 복구와 미래 데이터 미사용을 검증한다."""

import unittest

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal, assert_series_equal

from indicators.technical import (
    INDICATOR_COLUMNS, add_technical_indicators, calculate_bollinger_bands,
    calculate_ema, calculate_macd, calculate_rsi, calculate_sma, calculate_volume_sma,
)


def ohlcv(count=120):
    # 단조 상승만 테스트하지 않도록 결정적인 등락을 포함한 테스트 전용 입력.
    close = 100 + np.arange(count) / 10 + 3 * np.sin(np.arange(count) / 4)
    data = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=count, freq="h", tz="UTC"),
        "open": close - 0.3, "high": close + 1, "low": close - 1,
        "close": close, "volume": 20 + np.arange(count) % 7,
    }, index=pd.Index(np.arange(count) * 2 + 10, name="original_index"))
    data.attrs = {"timeframe": "1h", "source": "test fixture"}
    return data


class IndicatorTests(unittest.TestCase):
    def test_ema_uses_sma_seed_and_recursion(self):
        result = calculate_ema(pd.Series([1.0, 2.0, 3.0, 4.0, 8.0]), 3)
        np.testing.assert_allclose(result, [np.nan, np.nan, 2.0, 3.0, 5.5], equal_nan=True)

    def test_wilder_rsi_hand_calculated_example(self):
        result = calculate_rsi(pd.Series([10, 11, 10, 12, 11, 13]), 3)
        np.testing.assert_allclose(result, [np.nan] * 3 + [75, 600 / 11, 75], equal_nan=True)

    def test_classic_rsi_fourteen_numeric_example(self):
        close = pd.Series([
            44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
            45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00,
        ])
        result = calculate_rsi(close)
        self.assertTrue(result.iloc[:14].isna().all())
        self.assertAlmostEqual(result.iloc[14], 70.46413502109705, places=10)
        self.assertAlmostEqual(result.iloc[15], 66.24961855355505, places=10)

    def test_rsi_zero_division_cases(self):
        cases = [(np.arange(30), 100), (-np.arange(30), 0), (np.ones(30), 50)]
        for values, expected in cases:
            with self.subTest(expected=expected):
                result = calculate_rsi(pd.Series(values))
                self.assertTrue((result.iloc[14:] == expected).all())
        result = calculate_rsi(ohlcv()["close"])
        self.assertTrue(result.dropna().between(0, 100).all())

    def test_macd_known_fractional_example(self):
        result = calculate_macd(pd.Series([1, 2, 3, 5, 8, 13]), 2, 3, 2)
        np.testing.assert_allclose(result["macd"], [np.nan, np.nan, 1/2, 2/3, 35/36, 331/216], equal_nan=True)
        np.testing.assert_allclose(result["macd_signal"], [np.nan] * 3 + [7/12, 91/108, 211/162], equal_nan=True)
        np.testing.assert_allclose(result["macd_histogram"], [np.nan] * 3 + [1/12, 7/54, 149/648], equal_nan=True)

    def test_sma_and_volume_sma_known_values(self):
        values = pd.Series([2, 4, 8, 16, 32])
        expected = [np.nan, np.nan, 14/3, 28/3, 56/3]
        np.testing.assert_allclose(calculate_sma(values, 3), expected, equal_nan=True)
        np.testing.assert_allclose(calculate_volume_sma(values, 3), expected, equal_nan=True)

    def test_bollinger_population_standard_deviation(self):
        result = calculate_bollinger_bands(pd.Series([1, 2, 3, 4]), 3)
        width = 2 * np.sqrt(2/3)
        self.assertTrue(result.iloc[:2].isna().all().all())
        np.testing.assert_allclose(result.iloc[2], [2 + width, 2, 2 - width])
        np.testing.assert_allclose(result.iloc[3], [3 + width, 3, 3 - width])

    def test_constant_bollinger_bands_collapse_to_middle(self):
        result = calculate_bollinger_bands(pd.Series([100.0] * 30))
        self.assertTrue((result.iloc[19:] == 100).all().all())

    def test_default_warmup_indices(self):
        result = add_technical_indicators(ohlcv())
        first_positions = {
            "rsi_14": 14, "macd": 25, "macd_signal": 33, "macd_histogram": 33,
            "ema_20": 19, "ema_60": 59, "sma_20": 19, "sma_60": 59,
            "bb_upper": 19, "bb_middle": 19, "bb_lower": 19,
            "volume_sma_20": 19, "volume_ratio_20": 19,
        }
        for column, position in first_positions.items():
            with self.subTest(column=column):
                self.assertTrue(result[column].iloc[:position].isna().all())
                self.assertFalse(result[column].iloc[position:].isna().any())

    def test_insufficient_and_empty_data_stay_nan(self):
        for count in (0, 1, 5, 14):
            with self.subTest(count=count):
                result = add_technical_indicators(ohlcv(count))
                self.assertEqual(len(result), count)
                self.assertTrue(result[INDICATOR_COLUMNS].isna().all().all())

    def test_missing_values_restart_ema_and_rsi(self):
        ema = calculate_ema(pd.Series([1, 2, 3, np.nan, 10, 20, 30, 40]), 3)
        np.testing.assert_allclose(ema, [np.nan, np.nan, 2, np.nan, np.nan, np.nan, 20, 30], equal_nan=True)
        rsi = calculate_rsi(pd.Series([1, 2, 3, 4, np.nan, 10, 11, 12, 13]), 3)
        np.testing.assert_allclose(rsi, [np.nan] * 3 + [100] + [np.nan] * 4 + [100], equal_nan=True)

    def test_missing_rolling_windows_are_not_filled(self):
        values = pd.Series([1, 2, np.nan, 4, 5, 6])
        self.assertTrue(calculate_sma(values, 3).iloc[:5].isna().all())
        self.assertEqual(calculate_sma(values, 3).iloc[5], 5)
        self.assertTrue(calculate_bollinger_bands(values, 3).iloc[:5].isna().all().all())

    def test_non_finite_and_nullable_values_are_safe(self):
        for values in (
            pd.Series([1, np.inf, 3, 4, 5]),
            pd.Series([1, pd.NA, 3, 4, 5], dtype="Float64"),
            pd.Series([1, "bad", 3, 4, 5]),
        ):
            with self.subTest(dtype=str(values.dtype)):
                result = calculate_ema(values, 3)
                self.assertTrue(result.iloc[:4].isna().all())
                self.assertEqual(result.iloc[4], 4)

    def test_original_data_index_and_metadata_are_preserved(self):
        data = ohlcv()
        original = data.copy(deep=True)
        result = add_technical_indicators(data)
        assert_frame_equal(data, original)
        assert_frame_equal(result[list(data.columns)], original)
        self.assertEqual(result.attrs["source"], original.attrs["source"])
        self.assertEqual(result.attrs["technical_indicators"]["bollinger_ddof"], 0)
        self.assertTrue(set(INDICATOR_COLUMNS).issubset(result.columns))
        result.iloc[0, result.columns.get_loc("close")] = -100
        assert_frame_equal(data, original)

    def test_no_future_data_use_for_every_prefix(self):
        data = ohlcv(100)
        complete = add_technical_indicators(data)
        for end in (1, 14, 15, 20, 26, 34, 60, 75, 99):
            with self.subTest(end=end):
                prefix = add_technical_indicators(data.iloc[:end])
                assert_frame_equal(prefix[INDICATOR_COLUMNS], complete[INDICATOR_COLUMNS].iloc[:end])

    def test_changing_future_prices_does_not_change_past_values(self):
        data = ohlcv()
        altered = data.copy()
        altered.loc[altered.index[80:], "close"] = 10000
        altered.loc[altered.index[80:], "volume"] = 0
        original = add_technical_indicators(data)
        changed = add_technical_indicators(altered)
        assert_frame_equal(original[INDICATOR_COLUMNS].iloc[:80], changed[INDICATOR_COLUMNS].iloc[:80])

    def test_timestamp_gap_resets_every_indicator(self):
        data = ohlcv(160).drop(index=ohlcv(160).index[80])
        result = add_technical_indicators(data)
        independent_suffix = add_technical_indicators(data.iloc[80:])
        assert_frame_equal(result[INDICATOR_COLUMNS].iloc[80:], independent_suffix[INDICATOR_COLUMNS])
        self.assertEqual(result.attrs["technical_indicators"]["gap_resets"], 1)
        self.assertTrue(result[INDICATOR_COLUMNS].iloc[80].isna().all())

    def test_zero_volume_average_has_no_infinite_ratio(self):
        data = ohlcv()
        data["volume"] = 0
        result = add_technical_indicators(data)
        self.assertEqual(result["volume_sma_20"].iloc[-1], 0)
        self.assertTrue(result["volume_ratio_20"].isna().all())

    def test_volume_ratio_includes_current_bar(self):
        data = ohlcv(20)
        data["volume"] = [1] * 19 + [21]
        result = add_technical_indicators(data)
        self.assertEqual(result["volume_sma_20"].iloc[-1], 2)
        self.assertEqual(result["volume_ratio_20"].iloc[-1], 10.5)

    def test_parameter_validation(self):
        for function in (calculate_ema, calculate_sma, calculate_rsi, calculate_volume_sma):
            for period in (0, -1, 1.5, True):
                with self.subTest(function=function.__name__, period=period):
                    with self.assertRaises(ValueError):
                        function(pd.Series([1, 2, 3]), period)
        with self.assertRaises(ValueError):
            calculate_macd(pd.Series([1, 2]), 26, 12)
        with self.assertRaises(ValueError):
            calculate_bollinger_bands(pd.Series([1, 2]), std_multiplier=0)

    def test_invalid_ohlcv_structure_is_rejected(self):
        data = ohlcv()
        for invalid in (
            data.drop(columns="close"), data.iloc[::-1],
            pd.concat([data, data.iloc[-1:]]), data.assign(timestamp=pd.NaT),
        ):
            with self.assertRaises(ValueError):
                add_technical_indicators(invalid)


if __name__ == "__main__":
    unittest.main()
