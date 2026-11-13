"""FRED API 모의 응답만 사용한다. TEST_KEY는 네트워크 인증에 사용하지 않는 테스트 상수다."""

from copy import deepcopy
from datetime import date
import logging
from unittest.mock import Mock, patch
import traceback
import unittest

import pandas as pd
import requests

from collectors.fred import (_RedactKey, _request_json, fetch_fred_series, get_api_key,
                             normalize_observations)
from models.macro import FredError, FredKeyError, MACRO_COLUMNS, SERIES

TEST_KEY = "0" * 32
FETCHED = pd.Timestamp("2026-10-15T10:00Z")


def observation(day="2024-01-01", value="100", **extra):
    return {"date": day, "value": value, "realtime_start": "2026-10-15",
            "realtime_end": "9999-12-31", **extra}


def metadata(series_id="CPIAUCSL"):
    spec = SERIES[series_id]
    return {"seriess": [{"id": series_id, "units": spec.units, "frequency_short": spec.frequency,
                         "seasonal_adjustment_short": spec.seasonal_adjustment,
                         "last_updated": "2026-10-14 08:00:00-05:00"}]}


def observations_payload(rows, *, count=None, offset=0):
    return {"observations": rows, "count": len(rows) if count is None else count,
            "offset": offset, "units": "lin"}


def response(payload, status=200):
    result = Mock()
    result.status_code = status
    result.json.return_value = payload
    return result


class FredCollectorTests(unittest.TestCase):
    def setUp(self):
        limiter = patch("collectors.fred.FRED_MIN_REQUEST_INTERVAL_SECONDS", 0)
        limiter.start()
        self.addCleanup(limiter.stop)

    def fetch(self, rows=None, series_id="CPIAUCSL", meta=None):
        replies = [response(metadata(series_id) if meta is None else meta),
                   response(observations_payload(rows if rows is not None else [observation()]))]
        with patch("collectors.fred.requests.get", side_effect=replies) as get:
            frame = fetch_fred_series(series_id, "2024-01-01", "2024-12-31", api_key=TEST_KEY)
        return frame, get

    def test_missing_key_never_calls_network(self):
        with patch.dict("os.environ", {"FRED_API_KEY": ""}), \
                patch("collectors.fred._ENV_PATH") as path, patch("collectors.fred.requests.get") as get:
            path.is_file.return_value = False
            with self.assertRaises(FredKeyError) as context:
                fetch_fred_series("CPIAUCSL", "2024-01-01", "2024-12-31")
        self.assertEqual(context.exception.category, "KEY_MISSING")
        get.assert_not_called()

    def test_key_reads_only_project_env_and_process_takes_precedence(self):
        with patch.dict("os.environ", {"FRED_API_KEY": ""}), \
                patch("collectors.fred._ENV_PATH") as path, patch("collectors.fred.dotenv_values", return_value={"FRED_API_KEY": TEST_KEY}) as values:
            path.is_file.return_value = True
            self.assertEqual(get_api_key(), TEST_KEY)
            values.assert_called_once_with(path, interpolate=False)
        with patch.dict("os.environ", {"FRED_API_KEY": TEST_KEY}), patch("collectors.fred.dotenv_values") as values:
            self.assertEqual(get_api_key(), TEST_KEY)
            values.assert_not_called()

    def test_malformed_key_rejected_without_echo(self):
        for key in ("secret-value", "A"*32, 123, "", "x"*33):
            with self.subTest(kind=type(key).__name__), patch("collectors.fred.requests.get") as get:
                with self.assertRaises(FredKeyError) as context:
                    fetch_fred_series("CPIAUCSL", "2024-01-01", "2024-12-31", api_key=key)
                self.assertNotIn("secret-value", str(context.exception))
                get.assert_not_called()

    def test_valid_response_dataframe_metadata_and_fixed_endpoint(self):
        frame, get = self.fetch()
        self.assertEqual(frame.columns.tolist(), MACRO_COLUMNS)
        self.assertEqual(frame.value.tolist(), [100.0])
        self.assertEqual(frame.attrs["request_count"], 2)
        self.assertEqual(str(frame.observation_date.dt.tz), "UTC")
        self.assertEqual(frame.attrs["source_last_updated"], pd.Timestamp("2026-10-14T13:00Z"))
        urls = [call.args[0] for call in get.call_args_list]
        self.assertEqual(urls, ["https://api.stlouisfed.org/fred/series", "https://api.stlouisfed.org/fred/series/observations"])
        params = get.call_args.kwargs["params"]
        self.assertEqual(params["units"], "lin")
        self.assertEqual(params["api_key"], TEST_KEY)
        self.assertFalse(get.call_args.kwargs["allow_redirects"])
        self.assertNotIn(TEST_KEY, str(frame.attrs))

    def test_invalid_registered_key_http_400_is_authentication_error(self):
        payload = {"error_code": 400, "error_message": "The value for variable api_key is not registered: " + TEST_KEY}
        with patch("collectors.fred.requests.get", return_value=response(payload, 400)):
            with self.assertRaises(FredKeyError) as context:
                self.fetch_direct()
        self.assertEqual(context.exception.category, "AUTHENTICATION")
        self.assertNotIn(TEST_KEY, str(context.exception))

    def fetch_direct(self):
        return fetch_fred_series("CPIAUCSL", "2024-01-01", "2024-12-31", api_key=TEST_KEY)

    def test_http_status_errors_and_no_automatic_retry(self):
        for status, category in ((401, "AUTHENTICATION"), (403, "AUTHENTICATION"),
                                 (400, "HTTP_ERROR"), (429, "RATE_LIMIT"), (503, "HTTP_ERROR"),
                                 (302, "HTTP_ERROR")):
            with self.subTest(status=status), patch("collectors.fred.requests.get", return_value=response({}, status)) as get:
                with self.assertRaises(FredError) as context:
                    self.fetch_direct()
                self.assertEqual(context.exception.category, category)
                self.assertEqual(context.exception.http_status, status)
                self.assertEqual(get.call_count, 1)

    def test_network_and_timeout_exceptions_do_not_leak_key_or_traceback_url(self):
        for error, category in ((requests.Timeout("api_key="+TEST_KEY), "TIMEOUT"),
                                (requests.ConnectionError("api_key="+TEST_KEY), "NETWORK_ERROR")):
            with self.subTest(category=category), patch("collectors.fred.requests.get", side_effect=error):
                try:
                    self.fetch_direct()
                except FredError as caught:
                    self.assertEqual(caught.category, category)
                    self.assertNotIn(TEST_KEY, "".join(traceback.format_exception(caught)))
                else:
                    self.fail("Network failure must raise FredError")

    def test_response_errors_json_and_api_payload(self):
        for payload in (["not a dictionary"], {"error_code": 500, "error_message": TEST_KEY}):
            with self.subTest(payload_type=type(payload).__name__), patch("collectors.fred.requests.get", return_value=response(payload)):
                with self.assertRaises(FredError):
                    self.fetch_direct()
        broken = response(None)
        broken.json.side_effect = ValueError(TEST_KEY)
        with patch("collectors.fred.requests.get", return_value=broken):
            with self.assertRaises(FredError) as context:
                self.fetch_direct()
        self.assertNotIn(TEST_KEY, str(context.exception))

    def test_all_five_series_units_and_frequency_are_verified(self):
        for series_id, units, frequency in (("CPIAUCSL", "Index 1982-1984=100", "M"),
                                            ("FEDFUNDS", "Percent", "M"), ("UNRATE", "Percent", "M"),
                                            ("PAYEMS", "Thousands of Persons", "M"), ("DGS10", "Percent", "D")):
            with self.subTest(series_id=series_id):
                frame, _ = self.fetch(series_id=series_id)
                self.assertEqual(frame.units.iloc[0], units)
                self.assertEqual(frame.frequency.iloc[0], frequency)

    def test_metadata_mismatch_rejected_before_observations(self):
        for field, bad in (("units", "Dollars"), ("frequency_short", "Q"), ("id", "OTHER"),
                           ("seasonal_adjustment_short", "wrong")):
            with self.subTest(field=field):
                meta = metadata()
                meta["seriess"][0][field] = bad
                with self.assertRaises(FredError):
                    self.fetch(meta=meta)

    def test_sort_exact_duplicates_and_missing_values_without_fill(self):
        rows = [observation("2024-03-01", "."), observation("2024-01-01", "100"),
                observation("2024-01-01", "100"), observation("2024-02-01", "101")]
        original = deepcopy(rows)
        frame = normalize_observations("CPIAUCSL", rows, fetched_at=FETCHED)
        self.assertTrue(frame.observation_date.is_monotonic_increasing)
        self.assertTrue(frame.observation_date.is_unique)
        self.assertEqual(frame.attrs["removed_duplicates"], 1)
        self.assertEqual(frame.attrs["missing_values"], 1)
        self.assertTrue(pd.isna(frame.value.iloc[-1]))
        self.assertEqual(rows, original)

    def test_conflicting_duplicate_versions_are_not_arbitrarily_selected(self):
        for second in (observation(value="101"), observation(realtime_start="2026-10-14")):
            with self.subTest(value=second["value"]), self.assertRaises(FredError):
                normalize_observations("CPIAUCSL", [observation(), second])

    def test_dates_are_not_release_dates_or_inferred_vintages(self):
        frame, _ = self.fetch()
        self.assertEqual(frame.observation_date.iloc[0], pd.Timestamp("2024-01-01T00:00Z"))
        self.assertTrue(frame[["release_date", "available_at", "vintage_date"]].isna().all().all())
        self.assertEqual(frame.realtime_end.iloc[0], date(9999, 12, 31))
        self.assertFalse(frame.attrs["is_point_in_time"])
        self.assertEqual(frame.attrs["version_policy"], "current_revised_snapshot")

    def test_missing_realtime_dates_are_left_unknown(self):
        frame = normalize_observations("CPIAUCSL", [{"date": "2024-01-01", "value": "1"}])
        self.assertTrue(frame[["realtime_start", "realtime_end", "vintage_date"]].isna().all().all())

    def test_invalid_values_dates_and_monthly_frequency(self):
        rows = [[observation(value="invalid")], [observation(value="NaN")],
                [observation(value="inf")], [observation(value=True)], [observation("2024-02-30")],
                [observation("2024-01-02")], [{"date": "2024-01-01"}],
                [observation(realtime_start="2026-10-16", realtime_end="2026-10-15")]]
        for invalid in rows:
            with self.subTest(invalid=invalid), self.assertRaises(FredError):
                normalize_observations("CPIAUCSL", invalid)
        frame = normalize_observations("DGS10", [observation("2024-01-02", "4.1"), observation("2024-01-03", ".")])
        self.assertEqual(frame.attrs["missing_values"], 1)

    def test_empty_response_does_not_generate_fake_data(self):
        with self.assertRaises(FredError) as context:
            self.fetch(rows=[])
        self.assertEqual(context.exception.category, "EMPTY_RESPONSE")

    def test_pagination_and_request_budget(self):
        replies = [metadata(), observations_payload([observation()], count=2),
                   observations_payload([observation("2024-02-01")], count=2, offset=1)]
        with patch("collectors.fred._request_json", side_effect=replies) as request:
            result = self.fetch_direct()
        self.assertEqual(len(result), 2)
        self.assertEqual(request.call_count, 3)
        self.assertEqual(result.attrs["request_count"], 3)

    def test_incomplete_nonadvancing_changed_counts_and_oversize_rejected(self):
        cases = ([observations_payload([observation()], count=3), observations_payload([observation("2024-02-01")], count=3, offset=1)],
                 [observations_payload([], count=2)],
                 [observations_payload([observation()], count=2), observations_payload([observation("2024-02-01")], count=3, offset=1)],
                 [observations_payload([], count=10001)])
        for pages in cases:
            with self.subTest(pages=len(pages)), patch("collectors.fred._request_json", side_effect=[metadata(), *pages]):
                with self.assertRaises(FredError):
                    self.fetch_direct()

    def test_invalid_request_parameters_never_send_key(self):
        for series_id, start, end in (("OTHER", "2024-01-01", "2024-02-01"),
                                      ("CPIAUCSL", "2024-02-01", "2024-01-01"),
                                      ("CPIAUCSL", "1900-01-01", "2024-01-01"),
                                      ("CPIAUCSL", "2024-01-01", "9999-01-01")):
            with self.subTest(series=series_id), patch("collectors.fred.requests.get") as get:
                with self.assertRaises(FredError):
                    fetch_fred_series(series_id, start, end, api_key=TEST_KEY)
                get.assert_not_called()

    def test_debug_log_query_redaction(self):
        record = logging.LogRecord("urllib3.connectionpool", logging.DEBUG, "test", 1,
                                   "GET %s", ("/fred/series?api_key="+TEST_KEY+"&series_id=CPIAUCSL",), None)
        _RedactKey().filter(record)
        self.assertNotIn(TEST_KEY, record.getMessage())
        self.assertIn("api_key=[REDACTED]", record.getMessage())

    def test_request_interval_is_serialized_and_bounded(self):
        with patch("collectors.fred.FRED_MIN_REQUEST_INTERVAL_SECONDS", 1), \
                patch("collectors.fred._last_request_at", 9.5), patch("collectors.fred.time.monotonic", return_value=10), \
                patch("collectors.fred.time.sleep") as sleep, patch("collectors.fred.requests.get", return_value=response({})):
            _request_json("series", {}, TEST_KEY)
            sleep.assert_called_once_with(.5)
