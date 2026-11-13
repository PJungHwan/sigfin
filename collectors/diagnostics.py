"""같은 Python 환경에서 공개 API만 진단: python -m collectors.diagnostics.

고정된 api.binance.com만 호출하며 인증/우회/인증서 검증 해제를 사용하지 않는다.
응답 본문, 요청 헤더, 프록시 주소, 예외 원문은 출력하지 않는다.
"""

import json
import socket
import sys

import ccxt
import requests

from collectors.binance import DataCollectionError, fetch_binance_ohlcv
from collectors.errors import diagnose_api_error
from config.settings import API_TIMEOUT_MS, SUPPORTED_SYMBOLS


def _probe(call):
    try:
        return {"ok": True, **call()}
    except Exception as error:
        details = (error.details if isinstance(error, DataCollectionError) and error.details
                   else diagnose_api_error(error))
        return {"ok": False, **details.safe_dict()}


def _dns():
    addresses = socket.getaddrinfo("api.binance.com", 443, type=socket.SOCK_STREAM)
    return {"host": "api.binance.com", "address_count": len({item[4][0] for item in addresses})}


def _http(endpoint):
    with requests.get("https://api.binance.com/api/v3/" + endpoint,
                      timeout=(5, API_TIMEOUT_MS / 1000)) as response:
        response.raise_for_status()
        return {"http_status": response.status_code}


def _ohlcv(symbol):
    frame = fetch_binance_ohlcv(symbol, "1h", "1주", max_candles=3)
    return {"candles": len(frame), "last_candle_utc": frame.timestamp.max().isoformat(),
            "duplicate_timestamps": int(frame.timestamp.duplicated().sum()),
            "missing_values": int(frame.isna().sum().sum()),
            "columns": frame.columns.tolist()}


def run_diagnostics() -> dict:
    """오류가 난 서버와 동일한 터미널/가상환경에서 실행해야 비교에 유효하다."""
    checks = {"dns": _probe(_dns), "ping": _probe(lambda: _http("ping")),
              "time": _probe(lambda: _http("time"))}
    for symbol in SUPPORTED_SYMBOLS:
        checks[symbol] = _probe(lambda symbol=symbol: _ohlcv(symbol))
    return {"python_executable": sys.executable, "python_version": sys.version.split()[0],
            "ccxt_version": ccxt.__version__, "timeout_ms": API_TIMEOUT_MS,
            "checks": checks}


def main() -> int:
    report = run_diagnostics()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all(check["ok"] for check in report["checks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
