# STEP 08 — Crypto × Macro 통합 통계 분석

## 근거와 단계 경계

- 대응 커밋: `664274c` — `feat: implement crypto macro correlation and lag analysis`.
- Git 커밋 기록 시각: **2026-10-15 20:05:16 +09:00**. 실제 개발 시작/완료 시각은 **기록 확인 불가**.
- [통합 분석 계약/검증 기록](../crypto_macro_analysis.md), 신규 `analytics/`, 전용 service/UI 및 collector/cache 확장을 대조했다.

## 개발 목표

암호화폐와 거시 관측값을 월 단위로 정렬하고 변화량·상관관계·탐색적 시차를 분석한다. 사후 관계 분석과 시점 준수 분석을 분리한다.

## 실제 구현 기능

- 기존 BTC/ETH 선택·Binance/FRED 수집과 캐시 재사용. 전용 버튼에서 1/3/5/10년을 조회하며 기본 원천은 일봉이다.
- 시작/종료 범위 확장 조회를 지원하되 기존 5,000봉/페이지 요청 한도를 유지한다.
- UTC 봉 시작과 마감을 구분하고 `candle_end-1ns`의 달에 월말 마지막 확정 종가를 배정한다.
- 월간 수익률은 직전 달 종가가 있을 때만 계산한다. 전체 월 달력을 복원해 누락 월을 건너뛴 수익률을 만들지 않는다.
- 부분 월·봉 누락·이전 기준가 부족을 표시하고 기본 상관계수 표본에서 제외한다. 시간/일 수익률도 불완전 구간을 구분하며 작은 주기로 가격을 복제하지 않는다.
- CPI 수준/MoM/YoY, FEDFUNDS·UNRATE의 전월 차이(%p), PAYEMS 차이(천 명), DGS10 월말/월평균 전월 차이(%p).
- 유한 공통 월 표본만 Pearson과 평균 동률 순위의 Spearman 계산. 제거 전후 개수·실제 기간을 기록한다.
- 최소 표본 기본 12개월, 상수/부족 표본 N/A. 36개월 미만 경고는 연구용 안내 기준이다.
- lag 0~3은 **거시 t월과 가격 t+k월 수익률**의 사후 관계다. 달력 인덱스와 각 lag 표본 수를 보존한다.
- 공통 기준월=100 수준 비교, 변화량 산점도·계수 카드·시차 차트·품질/표본 표.
- 기본 A는 사후 탐색이다. B 시점 준수는 `PointInTimeUnavailable`로 차단하며 임의 메타데이터로도 실행하지 않는다.

## 사용 기술

Python, pandas PeriodIndex/UTC 시계열, NumPy 내적·노름·순위 기반 통계, dataclasses, Streamlit, Plotly. SciPy 등 별도 통계 패키지는 요구하지 않는다.

## 주요 관련 파일

| 파일 | 구현 근거 |
| --- | --- |
| [analytics/alignment.py](../../analytics/alignment.py) | 확정 가격·월말 집계·변환·공통 정렬/기준화 |
| [analytics/correlation.py](../../analytics/correlation.py), [analytics/lag_analysis.py](../../analytics/lag_analysis.py) | Pearson/Spearman, 달력 시차 |
| [analytics/analysis.py](../../analytics/analysis.py), [analytics/models.py](../../analytics/models.py) | 순수 분석 실행·설정/결과/시점 제한 |
| [services/crypto_macro_service.py](../../services/crypto_macro_service.py), [services/market_data.py](../../services/market_data.py), [collectors/binance.py](../../collectors/binance.py) | 기존 수집/캐시와 장기 범위 연결 |
| [ui/crypto_macro_view.py](../../ui/crypto_macro_view.py), [ui/crypto_macro_charts.py](../../ui/crypto_macro_charts.py) | 수동 실행·통계/품질 시각화 |
| [tests/test_crypto_macro.py](../../tests/test_crypto_macro.py), [tests/test_crypto_macro_ui.py](../../tests/test_crypto_macro_ui.py), [tests/test_crypto_macro_live.py](../../tests/test_crypto_macro_live.py) | 수치·시차·시점 차단·UI·선택적 실 API |

## 확인 가능한 테스트 결과

[기존 문서](../crypto_macro_analysis.md)의 **2026-10-15 검증 기록**을 인용한다. 이번 정리에서 재실행한 결과가 아니다.

- 전체 오프라인: **273 passed, 14 skipped**, 하위 검증 **253개 통과**.
- 별도 승인된 실 API: **14 passed**, 하위 검증 **41개 통과**. Binance/FRED와 통합 분석 UI를 포함한다.
- BTC/ETH×5종의 3년 일봉·부분 월 제외 분석 기록이 있으며, CPI/UNRATE 결측을 보간하지 않고 해당 변화 표본에서 제외했다고 명시한다.
- 당시 Python 소스 69개 구문/컴파일, 별도 프로세스 import, Streamlit health 200/실행 세션 차트 확인 기록이 있다.
- 원본 CI/터미널 산출물의 별도 보존 위치는 **기록 확인 불가**.
- 이번 문서 작업에서는 테스트/서버/실 API를 실행하지 않았다.

## 오류 및 해결 기록

- 기존 문서는 제한 환경의 `WinError 10013`과 허용된 공식 API 접속의 성공을 구분한다. 지역 차단이나 계산 오류로 단정하지 않았다.
- CPI/UNRATE의 특정 월 결측은 데이터 품질 관측으로 기록돼 있다. 원인이나 발표일을 추정하지 않았고 보간으로 숨기지 않았다.
- 그 외 개발 중 오류의 상세 원문/수정 순서/시각은 **기록 확인 불가**.

## 확인이 필요한 사항과 한계

실제 발표 시각/당시 빈티지 확보가 없어 시점 준수 분석·거시 forward-fill·과거 백테스트 입력은 제공하지 않는다. 상관관계는 인과/예측력이나 투자 전략 검증이 아니며 p-value도 계산하지 않는다. 소표본·다중 시차 선택 편향을 표시한다.
