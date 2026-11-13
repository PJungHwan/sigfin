"""공개 API 예외의 안전한 원인 분류. 원본 메시지/헤더/프록시 주소는 노출하지 않는다."""

from dataclasses import asdict, dataclass
import errno
import socket
import ssl

import ccxt
import requests


@dataclass(frozen=True)
class ErrorDetails:
    category: str
    message: str
    hint: str
    exception_types: tuple[str, ...] = ()
    http_status: int | None = None
    os_error: int | None = None

    def safe_dict(self) -> dict:
        return asdict(self)


def _exception_chain(error: BaseException) -> list[BaseException]:
    """requests/urllib3/ccxt 래핑과 reason도 검사한다. 순환 참조는 한 번만 방문한다."""
    found, queue, seen = [], [error], set()
    while queue and len(found) < 32:
        current = queue.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        found.append(current)
        candidates = [current.__cause__, current.__context__, getattr(current, "reason", None)]
        candidates.extend(current.args)
        queue.extend(item for item in candidates if isinstance(item, BaseException))
    return found


def diagnose_api_error(error: BaseException) -> ErrorDetails:
    chain = _exception_chain(error)
    types = tuple(f"{type(item).__module__}.{type(item).__name__}" for item in chain)
    status = next((getattr(getattr(item, "response", None), "status_code", None)
                   for item in chain
                   if isinstance(getattr(getattr(item, "response", None), "status_code", None), int)), None)
    os_code = next((getattr(item, "winerror", None) or getattr(item, "errno", None)
                    for item in reversed(chain) if isinstance(item, OSError) and not isinstance(item, ssl.SSLError)
                    and (getattr(item, "winerror", None) or getattr(item, "errno", None))), None)

    def result(category, message, hint):
        return ErrorDetails(category, message, hint, types, status, os_code)

    if status in (418, 429) or (status is None and any(
            isinstance(item, (ccxt.RateLimitExceeded, ccxt.DDoSProtection)) for item in chain)):
        return result("RATE_LIMIT", "Binance 요청 한도에 도달했습니다.",
                      "잠시 기다린 뒤 새로고침해 주세요. 반복 호출이나 다른 주소로의 우회를 시도하지 않습니다.")
    if status in (403, 451) or (status is None and any(isinstance(item, ccxt.PermissionDenied) for item in chain)):
        return result("ACCESS_RESTRICTED", "Binance API가 접근을 거부했습니다.",
                      "HTTP 403/451 또는 거래소 권한 거부입니다. 지역·네트워크 정책과 Binance 공식 안내를 확인해 주세요.")
    if status is not None:
        return result("HTTP_ERROR", f"Binance 공개 API가 HTTP {status} 오류를 반환했습니다.",
                      "거래소 상태를 확인하고 잠시 후 다시 조회해 주세요.")
    if any(isinstance(item, PermissionError) or (isinstance(item, OSError) and not isinstance(item, ssl.SSLError) and
           (getattr(item, "winerror", None) == 10013 or getattr(item, "errno", None) in (errno.EACCES, errno.EPERM)))
           for item in chain):
        return result("NETWORK_PERMISSION", "Binance 공개 API에 연결하지 못했습니다. 실행 환경이 소켓 연결을 거부했습니다.",
                      "실행 환경의 네트워크 권한·보안 정책을 확인하고, 권한이 허용된 VS Code 터미널에서 같은 .venv로 실행해 주세요.")
    if any(isinstance(item, socket.gaierror) or type(item).__name__ == "NameResolutionError" for item in chain):
        return result("DNS_ERROR", "Binance API 주소의 DNS 조회에 실패했습니다.",
                      "인터넷 연결과 api.binance.com의 DNS 해석 상태를 확인해 주세요.")
    if any(isinstance(item, (ccxt.RequestTimeout, requests.exceptions.Timeout, TimeoutError)) for item in chain):
        return result("TIMEOUT", "Binance API 연결 또는 응답 대기 시간이 초과되었습니다.",
                      "네트워크 지연과 거래소 상태를 확인한 뒤 다시 조회해 주세요.")
    if any(isinstance(item, (requests.exceptions.SSLError, ssl.SSLError)) for item in chain):
        return result("TLS_ERROR", "Binance API의 TLS 인증서 또는 보안 연결 확인에 실패했습니다.",
                      "시스템 시간·인증서·승인된 네트워크 설정을 확인해 주세요. 인증서 검증은 해제하지 않습니다.")
    if any(isinstance(item, requests.exceptions.ProxyError) for item in chain):
        return result("PROXY_ERROR", "설정된 프록시를 통한 Binance 연결에 실패했습니다.",
                      "조직에서 승인한 프록시 설정과 연결 상태를 확인해 주세요.")
    if any(isinstance(item, (ccxt.NetworkError, requests.exceptions.ConnectionError, OSError)) for item in chain):
        return result("NETWORK_ERROR", "Binance 공개 API에 연결하지 못했습니다.",
                      "네트워크 상태를 확인해 주세요. HTTP 응답 없이 발생한 연결 오류만으로 지역 제한을 확정할 수 없습니다.")
    return result("EXCHANGE_ERROR", "Binance 공개 API가 요청을 처리하지 못했습니다.",
                  "거래소 상태 및 요청 설정을 확인한 뒤 새로고침해 주세요.")
