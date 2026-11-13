# STEP 03 — 기술적 분석 지표 엔진

## 근거와 단계 경계

- 대응 커밋: `0675bec` — `feat: implement technical indicators and chart overlays`.
- Git 커밋 기록 시각: **2026-10-14 23:21:40 +09:00**. 실제 개발 시작/완료 시각은 **기록 확인 불가**.
- [기술적 지표 계약](../technical_indicators.md), 당시 신규 계산/테스트 모듈, collector/cache/chart 변경을 대조했다.
- 사용자 회고상 Codex 사용 시작은 STEP 3~4 무렵이다. STEP 3부터였는지 STEP 4부터였는지와 정확한 기여 범위는 **기록 확인 불가**다.

## 개발 목표

확정 OHLCV에서 재사용 가능한 순수 지표 계산 모듈을 만들고 기존 차트·사이드바·Market Indicators 카드에 연결한다.

## 실제 구현 기능

| 지표 | 계산/초기화 정책 |
| --- | --- |
| RSI(14) | 첫 14개 변화량 평균으로 초기화한 Wilder 평활; 상승만 100, 하락만 0, 평탄 구간 50 |
| MACD(12,26,9) | SMA seed의 독립 EMA 차이, Signal EMA, Histogram; 기본 Histogram은 34봉부터 유효 |
| EMA/SMA 20·60 | 종가 기준; EMA는 최초 N개 SMA seed 후 `alpha=2/(N+1)` |
| Bollinger(20,2) | SMA20 ± 2×모집단 표준편차(`ddof=0`) |
| Volume SMA20/비율 | 현재 봉 포함 거래량 평균과 거래량/평균; 분모 0이면 NaN |

- `add_technical_indicators`는 원본 OHLCV·인덱스·attrs를 보존하는 새 DataFrame을 반환한다.
- 부족/비유한 입력은 NaN이며 미래 값·보간·backward fill을 사용하지 않는다. 봉 누락 뒤에는 지표를 다시 초기화한다.
- 분석 구간 앞 300봉을 워밍업으로 수집하고 계산 후 표시 구간만 반환한다. 표시 5,000봉, 워밍업 포함 5,300봉 제한.
- EMA/SMA/Bollinger 오버레이, RSI/MACD 패널, 거래량 평균선과 지표별 ON/OFF.
- RSI 값/과매수·중립·과매도, MACD Histogram 부호를 카드에 연결한다. AI SIGNAL은 아직 `-`다.
- 표시 체크박스는 시장 데이터 캐시 키에 포함하지 않는다.

## 사용 기술

Python, pandas, NumPy, Plotly, Streamlit. 별도 TA-Lib 계열 계산 의존성 없이 구현한다.

## 주요 관련 파일

| 파일 | 구현 근거 |
| --- | --- |
| [indicators/technical.py](../../indicators/technical.py) | `calculate_rsi/macd/ema/sma/bollinger_bands/volume_sma`, `add_technical_indicators` |
| [collectors/binance.py](../../collectors/binance.py), [services/market_data.py](../../services/market_data.py) | 워밍업 수집·계산·표시 구간 절단·캐시 |
| [ui/charts.py](../../ui/charts.py), [ui/components.py](../../ui/components.py), [app.py](../../app.py) | 보조 패널·오버레이·지표 카드/체크박스 |
| [tests/test_indicators.py](../../tests/test_indicators.py), [tests/test_market_data.py](../../tests/test_market_data.py) | 수치·불변성·미래 누수·캐시 검증 |
| [docs/technical_indicators.md](../technical_indicators.md) | 공식 계산 계약과 초기화/워밍업 한계 |

## 확인 가능한 테스트 결과

- 당시 신규 테스트에는 손계산 RSI/EMA/MACD 예제, 표준편차, 첫 유효 위치, NaN/갭, 원본 불변성과 미래 가격 변경의 영향 검사가 있다.
- 문서에는 `unittest discover` 실행 방법이 있지만 당시 전체 통과 수·실행일·원본 결과 로그는 **기록 확인 불가**.
- 현재 존재하는 테스트를 당시 통과 결과로 바꾸어 기록하지 않는다. 후속 누적 회귀 결과는 [전체 안내](README.md)를 참고한다.
- 이번 문서 정리에서는 테스트/실 API를 실행하지 않았다.

## 오류 및 해결 기록

- 실제 개발 중 발생한 오류와 해결 순서/날짜: **기록 확인 불가**.
- NaN 초기화, RSI 0 나눗셈, 갭 이후 재초기화는 소스·계약·테스트로 확인되는 처리 정책이다. 실제 장애를 겪었다는 기록과 구분한다.
- 무한 이력 기반 거래소 차트와 완전 일치를 보장하지 않는다. seed/평탄 RSI/표준편차 기준 차이는 문서화된 한계이며 무조건 오류가 아니다.

## 확인이 필요한 사항

Codex 최초 사용 단계, 당시 테스트 실행 결과, 외부 지표 구현과의 실측 비교 자료는 **기록 확인 불가**다.
