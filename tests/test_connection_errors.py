"""실제 원인 래핑, HTTP 응답 및 비밀 정보 노출 방지를 오프라인에서 검증한다."""

import errno
import socket
import ssl
import unittest
from unittest.mock import Mock, patch

import ccxt
import requests

from collectors.binance import DataCollectionError, fetch_binance_ohlcv
from collectors.diagnostics import _probe
from collectors.errors import diagnose_api_error
from test_binance import NOW


def wrapped(cause):
    error = ccxt.NetworkError("raw message must not be displayed")
    error.__cause__ = cause
    return error


def http_error(status):
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError("raw HTTP body must not be displayed", response=response)


class ConnectionErrorTests(unittest.TestCase):
    def test_nested_socket_permission_is_not_misreported_as_dns_or_region_block(self):
        root = PermissionError(errno.EACCES, "sensitive original text")
        root.winerror = 10013
        error = wrapped(requests.ConnectionError("hidden proxy address"))
        error.__cause__.__cause__ = root
        details = diagnose_api_error(error)
        self.assertEqual(details.category, "NETWORK_PERMISSION")
        self.assertEqual(details.os_error, 10013)
        self.assertIsNone(details.http_status)
        self.assertIn("builtins.PermissionError", details.exception_types)
        self.assertNotIn("sensitive", str(details.safe_dict()))
        self.assertNotIn("hidden proxy", str(details.safe_dict()))

    def test_dns_and_connect_read_timeouts_are_distinct(self):
        self.assertEqual(diagnose_api_error(wrapped(socket.gaierror(-2, "host"))).category, "DNS_ERROR")
        for error in (ccxt.RequestTimeout("test"), requests.ConnectTimeout("test"),
                      requests.ReadTimeout("test"), TimeoutError("test")):
            with self.subTest(error=type(error).__name__):
                self.assertEqual(diagnose_api_error(wrapped(error)).category, "TIMEOUT")

    def test_http_access_rate_and_server_errors_are_distinct(self):
        for status, category in ((403, "ACCESS_RESTRICTED"), (451, "ACCESS_RESTRICTED"),
                                 (418, "RATE_LIMIT"), (429, "RATE_LIMIT"), (500, "HTTP_ERROR"),
                                 (503, "HTTP_ERROR")):
            with self.subTest(status=status):
                details = diagnose_api_error(wrapped(http_error(status)))
                self.assertEqual(details.category, category)
                self.assertEqual(details.http_status, status)
        for status, category in ((403, "ACCESS_RESTRICTED"), (503, "HTTP_ERROR")):
            with self.subTest(coarse_ccxt_error=status):
                error = ccxt.DDoSProtection("generic wrapper")
                error.__cause__ = http_error(status)
                self.assertEqual(diagnose_api_error(error).category, category)

    def test_tls_errno_one_is_not_operating_system_permission_error(self):
        error = ssl.SSLError(ssl.SSL_ERROR_SSL, "certificate test")
        self.assertEqual(diagnose_api_error(wrapped(error)).category, "TLS_ERROR")
        self.assertEqual(diagnose_api_error(wrapped(requests.exceptions.SSLError("test"))).category, "TLS_ERROR")

    def test_proxy_connection_rate_and_exchange_errors(self):
        for error, category in ((requests.exceptions.ProxyError("test"), "PROXY_ERROR"),
                                (ccxt.NetworkError("test"), "NETWORK_ERROR"),
                                (ccxt.RateLimitExceeded("test"), "RATE_LIMIT"),
                                (ccxt.DDoSProtection("test"), "RATE_LIMIT"),
                                (ccxt.PermissionDenied("test"), "ACCESS_RESTRICTED"),
                                (ccxt.ExchangeError("test"), "EXCHANGE_ERROR")):
            with self.subTest(category=category):
                self.assertEqual(diagnose_api_error(error).category, category)

    def test_cyclic_exception_chain_and_urllib3_reason_are_safe(self):
        error = requests.ConnectionError("test")
        nested = OSError("wrapper")
        nested.reason = socket.gaierror(-2, "test")
        error.__context__ = nested
        nested.__cause__ = error
        details = diagnose_api_error(error)
        self.assertEqual(details.category, "DNS_ERROR")
        self.assertEqual(len(details.exception_types), 3)

    def test_collector_preserves_original_cause_and_safe_details(self):
        original = wrapped(PermissionError(errno.EACCES, "test"))
        exchange = Mock()
        exchange.fetch_ohlcv.side_effect = original
        with patch("collectors.binance._get_exchange", return_value=exchange):
            with self.assertRaises(DataCollectionError) as context:
                fetch_binance_ohlcv("BTC/USDT", "1h", "1주", now=NOW)
        self.assertIs(context.exception.__cause__, original)
        self.assertEqual(context.exception.details.category, "NETWORK_PERMISSION")

    def test_empty_and_invalid_ohlcv_are_data_errors(self):
        for response, category in (([], "EMPTY_RESPONSE"), ([[None]*6], "DATA_VALIDATION"),
                                    ({"unexpected": "test"}, "DATA_VALIDATION")):
            with self.subTest(category=category):
                exchange = Mock()
                exchange.fetch_ohlcv.return_value = response
                with patch("collectors.binance._get_exchange", return_value=exchange):
                    with self.assertRaises(DataCollectionError) as context:
                        fetch_binance_ohlcv("BTC/USDT", "1h", "1주", now=NOW)
                self.assertEqual(context.exception.details.category, category)

    def test_diagnostic_probe_returns_no_raw_exception_message(self):
        report = _probe(lambda: (_ for _ in ()).throw(wrapped(http_error(451))))
        self.assertFalse(report["ok"])
        self.assertEqual(report["category"], "ACCESS_RESTRICTED")
        self.assertNotIn("raw HTTP body", str(report))
