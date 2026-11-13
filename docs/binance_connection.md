# Binance 연결 오류 진단 및 실행

2026-10-15 확인한 실행 환경 차이:

| 검사 | 네트워크 제한 샌드박스 | 네트워크 권한 허용 프로세스 |
| --- | --- | --- |
| api.binance.com DNS | 성공 | 성공 |
| 공개 /api/v3/ping, /api/v3/time | PermissionError, WinError 10013 | HTTP 200 |
| BTC/USDT, ETH/USDT 1h 확정 캔들 | NetworkError → PermissionError | 실제 데이터 조회 성공 |

`.venv` Python 3.11.9, ccxt 4.5.88로 같은 코드와 같은 API 주소를 사용했다.
확인된 차이는 실행 프로세스의 외부 소켓 권한이다. HTTP 응답을 받기 전 차단됐으므로
DNS 실패, 응답 타임아웃, HTTP 403/451 지역 제한, OHLCV 검증 실패로 보고하지 않는다.
STEP 6에서 collectors 및 데이터 캐시 코드는 변경되지 않았고 API_TIMEOUT_MS는
15,000ms, 데이터 캐시 TTL은 60초로 유지된다. 백테스트/리스크 계산을 변경해 해결할
문제가 아니다. 다른 PC/터미널에서도 같은 원인이라고 단정하려면 해당 환경에서 재진단해야 한다.

기존 8501 서버에서는 실제 화면 세션의 DATA ERROR를 재현했다. 네트워크 권한이 있는
새 프로세스를 8502에 실행한 뒤 실제 WebSocket 세션에서 BTC/ETH 모두 DATA READY와
가격 차트를 확인했고 ETH Risk ON/OFF 비교의 성과 지표와 그래프 4개도 확인했다.
이는 브라우저 픽셀 검수가 아닌 서버의 실제 앱 세션/렌더링 메시지 검증이다.

## 같은 환경에서 직접 진단

오류가 발생한 서버와 **같은 VS Code 터미널·가상환경·네트워크 권한**으로 실행한다.

```powershell
.\.venv\Scripts\python.exe -m collectors.diagnostics
```

실제 Python 실행 경로, 버전, DNS, ping/time, BTC/ETH의 최근 확정 캔들 3개 구조를
검사한다. 모두 성공하면 종료 코드 0, 하나라도 실패하면 1이다. 오류는 예외 유형 체인,
HTTP 상태, OS 오류 번호로 출력하며 응답 본문·인증 정보·프록시 주소는 출력하지 않는다.
인증이나 다른 API 주소로 자동 우회하는 기능은 없다.

| category | 구분 및 대응 |
| --- | --- |
| NETWORK_PERMISSION | WinError 10013 / OS 권한 거부. 실행 환경의 네트워크 권한과 보안 정책 확인 |
| DNS_ERROR | 이름 해석 실패. 인터넷 및 DNS 확인 |
| TIMEOUT | 연결/읽기 시간 초과. 회선·거래소 상태 확인 |
| HTTP_ERROR | 응답 상태 코드 확인. 5xx 등 거래소 오류와 연결 실패 구분 |
| ACCESS_RESTRICTED | HTTP 403/451 또는 거래소 권한 거부. 공식 이용 가능 지역·접근 정책 확인 |
| RATE_LIMIT | HTTP 418/429 또는 ccxt 한도/보호 오류. 요청 중지 후 대기 |
| TLS_ERROR / PROXY_ERROR | 인증서·시스템 시간 또는 승인된 프록시 설정 확인 |
| NETWORK_ERROR | 그 밖의 연결 실패. 이 분류만으로 지역 제한을 단정하지 않음 |
| EMPTY_RESPONSE / DATA_VALIDATION | 빈 데이터/잘못된 OHLCV. 네트워크 오류와 별도로 조사 |
| EXCHANGE_ERROR | 기타 거래소 요청 처리 오류 |

앱의 DATA ERROR 아래 `데이터 조회 오류 진단`을 펼치면 같은 안전한 분류를 볼 수 있다.
정상 응답만 `st.cache_data`에 저장하며 예외는 캐시하지 않는다. 연결을 복구한 뒤
`새로고침`은 선택한 종목·타임프레임·기간의 캐시만 비우고 다시 수집한다.

## 서버 재실행

이미 네트워크 제한을 물려받아 실행된 서버의 권한은 새로고침으로 바뀌지 않는다.
네트워크가 허용된 일반 VS Code 터미널에서 진단 성공을 확인한 뒤 서버를 새로 시작한다.
8501에 기존 서버가 있으면 사용하지 않는 다른 포트를 선택한다.

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1 --server.port 8502
```

기존 8501을 재사용하려면 그 서버를 실행한 터미널에서 Ctrl+C로 종료한 다음 허용된
터미널에서 같은 명령의 포트를 8501로 바꿔 실행한다. 프로그램의 정상 HTTPS 443 접근에
필요한 정책은 관리자에게 확인하며 인증서 검증 해제, 거래소 제한 우회, 임의 데이터 대체는
해결책으로 사용하지 않는다. 실제 403/451인 환경에서는 거래소 공식 정책을 따라야 한다.

전체 오프라인 검증은 `.\.venv\Scripts\python.exe -m pytest -q`로 실행한다.
실제 API 테스트는 네트워크 허용 환경에서 `RUN_BINANCE_LIVE_TESTS=1`로 별도 활성화한다.
