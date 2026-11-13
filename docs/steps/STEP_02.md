# STEP 02 — Binance OHLCV 수집 및 캔들차트

## 근거와 단계 경계

- 대응 커밋: `0d1f7c4` — `feat: implement Binance OHLCV data and candlestick dashboard`.
- Git 커밋 기록 시각: **2026-10-14 22:51:15 +09:00**. 실제 개발 시작/완료 시각은 **기록 확인 불가**.
- 당시 `collectors/binance.py`, `services/market_data.py`, `ui/charts.py` 및 테스트 신규 추가와 `app.py` 연결을 확인했다.
- STEP 1~2를 Codex 개발로 기록하지 않는다. 사용자 설명상 사용 시작은 STEP 3~4 무렵이며 정확한 시점은 **기록 확인 불가**다.

## 개발 목표

안내 문구였던 차트 영역을 실제 Binance 공개 현물 확정 캔들 데이터와 Plotly 차트로 연결한다. 주문·계정 인증은 포함하지 않는다.

## 실제 구현 기능

- ccxt `fetch_ohlcv`로 BTC/USDT·ETH/USDT를 조회한다. 지원 봉은 5m·15m·1h·4h·1d다.
- 달력 기준 분석 기간과 UTC를 사용한다. `timestamp`는 봉 시작이고 진행 봉은 제외한다.
- 페이지당 최대 1,000개, 표시 캔들 최대 5,000개, 조회당 페이지 요청 최대 7회 제한.
- ccxt Rate Limit, 재사용 클라이언트 및 요청 락, 15초 타임아웃.
- 중복 제거·시간 정렬·OHLC 범위/수치 검증. 누락 봉을 보간하지 않는다.
- 반환 DataFrame은 `timestamp/open/high/low/close/volume`. attrs에 실제 기간·요청 횟수·누락·부분 수집 사유를 기록한다.
- 일부 페이지 실패 시 이미 받은 실제 데이터와 경고를 제공하고, 유효한 데이터가 없으면 `DataCollectionError`다. 임의 시세를 생성하지 않는다.
- Plotly 캔들/거래량, 상승 녹색·하락 빨간색, hover·확대/축소.
- 마지막 확정 종가·해당 봉 시가 대비 변화율·UTC 시각, 로딩/오류/새로고침 UI.
- `st.cache_data` 60초 캐시. 새로고침은 선택 조합의 캐시를 비운다.

지표 워밍업 300봉은 STEP 3의 확장이고, 상세 네트워크 오류 분류는 STEP 6의 확장이다. 최초 STEP 2 구현으로 소급하지 않는다.

## 사용 기술

Python, pandas, ccxt, Plotly Candlestick/Subplots, Streamlit `st.cache_data`, 락과 재사용 클라이언트.

## 주요 관련 파일

| 파일 | 구현 근거 |
| --- | --- |
| [collectors/binance.py](../../collectors/binance.py) | `fetch_binance_ohlcv`, `_clean_page`, 페이지네이션/확정 봉 |
| [services/market_data.py](../../services/market_data.py) | 캐시 래퍼; 최초 버전은 지표 계산 없이 수집 결과 반환 |
| [ui/charts.py](../../ui/charts.py) | 캔들·거래량 차트 |
| [app.py](../../app.py), [config/settings.py](../../config/settings.py) | UI 연결과 요청/캐시 상수 |
| [tests/test_binance.py](../../tests/test_binance.py), [tests/test_charts.py](../../tests/test_charts.py) | 수집 계약·차트 테스트 |
| [tests/test_app.py](../../tests/test_app.py), [tests/test_imports.py](../../tests/test_imports.py), [tests/test_binance_live.py](../../tests/test_binance_live.py) | 화면·import·선택적 실 API 검증 |

## 확인 가능한 테스트 결과

- `0d1f7c4`의 수집 테스트에는 페이지네이션, 캔들 수 제한, UTC, 진행 봉 제외, 부분 실패/빈 응답 검증 코드가 있다.
- 당시 테스트 실행 로그·전체 통과 수·실 API 성공 여부/실행일은 **기록 확인 불가**.
- 후속 STEP 6 [연결 진단 문서](../binance_connection.md)와 STEP 8 [통합 분석 문서](../crypto_macro_analysis.md)에 Binance 실제 연동 결과가 있다. 이는 후속 검증이며 STEP 2 당시의 실행 결과와 구분한다.
- 이번 문서 정리에서는 테스트/실 API를 실행하지 않았다.

## 오류 및 해결 기록

| 구분 | 확인 내용 |
| --- | --- |
| 확인된 방어 구현 | 네트워크·요청 한도·빈 응답·잘못된 OHLCV를 처리한다. 테스트가 있다는 이유만으로 당시 실제 발생했다고 기록하지 않는다. |
| 설정 상수 | `git show 0d1f7c4:config/settings.py`에는 `API_TIMEOUT_MS=15000`이 있고 당시 collector import와 이름이 일치한다. |
| 과거 import 오류 경위 | `API_TIMEOUT_MS` 관련 오류의 원본 로그·잘못된 중간 파일·독립된 해결 커밋·해결 시각은 **기록 확인 불가**. 현재 정상 정의만으로 과거 오류 원인을 확정하지 않는다. |

## 확인이 필요한 사항

STEP 2 당시 서버/실 API 점검 자료와 import 오류 원본이 있으면 추가한다. 이후의 네트워크 권한 문제를 STEP 2 코드 오류로 소급해서 단정하지 않는다.
