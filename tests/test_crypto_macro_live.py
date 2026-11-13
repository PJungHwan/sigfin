"""명시적으로 활성화할 때만 실제 사용자 키와 공개 API를 사용한다. 대체 데이터 없음."""

import os
import unittest

from analytics.models import AnalysisConfig
from collectors.fred import get_api_key
from models.macro import FredKeyError, SERIES
from services.crypto_macro_service import load_crypto_macro_analysis


@unittest.skipUnless(os.getenv("RUN_CRYPTO_MACRO_LIVE_TESTS") == "1", "실 API 통합 검증은 별도 활성화가 필요합니다.")
class CryptoMacroLiveTests(unittest.TestCase):
    def setUp(self):
        try:
            get_api_key()
        except FredKeyError:
            self.skipTest("프로젝트에 유효한 사용자 FRED 키가 없습니다.")

    def test_actual_btc_eth_all_five_macro_series(self):
        for symbol in ("BTC/USDT", "ETH/USDT"):
            for series_id in SERIES:
                with self.subTest(symbol=symbol, series=series_id):
                    result = load_crypto_macro_analysis(symbol, "3년", AnalysisConfig(series_id))
                    self.assertGreaterEqual(result.correlation.n_used, 24)
                    self.assertEqual(result.correlation.status, "OK")
                    self.assertTrue(-1 <= result.correlation.pearson <= 1)
                    self.assertTrue(result.monthly.index.is_unique)
                    self.assertFalse(result.quality["is_point_in_time"])
                    self.assertEqual(result.lags.lag_months.tolist(), [0, 1, 2, 3])
                    self.assertTrue(result.lag_pairs.loc[result.lag_pairs.used, "eligible"].all())
                    print(f"LIVE CRYPTO MACRO OK {symbol} {series_id}: n={result.correlation.n_used}, "
                          f"range={result.correlation.first_month}..{result.correlation.last_month}, "
                          f"lag_samples={result.lags.n_used.tolist()}", flush=True)
                    missing = result.monthly.loc[result.monthly.macro_level.isna()].index.astype(str).tolist()
                    print(f"LIVE QUALITY {series_id}: macro_missing_months={missing}, "
                          f"crypto_partial={result.quality['crypto_partial_months']}, "
                          f"source_candles={result.quality['source_crypto_candles']}", flush=True)

    def test_actual_streamlit_integrated_analysis(self):
        from streamlit.testing.v1 import AppTest
        from pathlib import Path

        path = Path(__file__).resolve().parents[1]/"app.py"
        app = AppTest.from_file(str(path), default_timeout=90).run()
        self.assertFalse(app.exception)
        app.button(key="load_crypto_macro").click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error, [item.value for item in app.error])
        self.assertEqual(len(app.get("plotly_chart")), 4)
        self.assertTrue(any(metric.label == "Pearson · lag 0" and metric.value != "N/A" for metric in app.metric))
        print("LIVE CRYPTO MACRO UI OK: market chart plus normalized/scatter/lag charts", flush=True)
