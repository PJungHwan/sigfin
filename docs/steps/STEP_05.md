# STEP 05 — 기초 전략 백테스팅 엔진

## 근거와 단계 경계

- 대응 커밋: `fe05300` — `feat: implement strategy backtesting engine and performance dashboard`.
- Git 커밋 기록 시각: **2026-10-15 00:32:00 +09:00**. 실제 개발 시작/완료 시각은 **기록 확인 불가**.
- 당시 신규 `backtest/engine.py`, `models.py`, `metrics.py`와 UI/테스트, [백테스트 계약](../backtesting.md)을 대조했다.
- 현재 엔진에는 STEP 6 위험 청산이 추가되어 있다. STEP 5 범위는 신호 기반 Risk OFF 경로다.

## 개발 목표

STEP 4 조건 평가를 재사용해 단일 종목·단일 가상 포지션의 거래를 재현하고, 비용·회계·성과를 검증 가능한 형태로 출력한다.

## 실제 구현 기능

- `run_backtest`는 Streamlit과 독립적으로 DataFrame·전략·설정을 입력받는다.
- 초기금 10,000 USDT, 편도 수수료 0.1%, 편도 슬리피지 0.05%, 거래량 참여율 10% 기본값.
- FLAT/LONG/SHORT 상태. 확정 신호 다음 **연속 봉 시가**에 체결하며 같은 봉 종가 체결은 하지 않는다.
- FLAT 동시 신호는 진입하지 않는다. 보유 중 반대 신호는 청산만 하고 같은 시가에 즉시 반전하지 않는다.
- 다음 봉이 없거나 누락되면 체결하지 않는다. 종료 시 열린 포지션은 마지막 확정 종가로 평가한다.
- 매수는 `가격×(1+slippage)`, 매도는 `가격×(1-slippage)`이며 진입/청산 수수료를 각각 반영한다.
- **직전 확정 봉 거래량×참여율**로 수량을 제한한다. 신호 청산은 전량 조건을 충족하지 못하면 거절하고 포지션을 유지한다. 부분 청산·미체결 주문 자동 이월은 없다.
- 가상 SHORT의 매도 대금을 잠금 현금으로 표시하고 재사용을 막는다. 실제 Binance Spot SHORT 주문이 아니다.
- 현금/수량/실현·미실현 손익/Equity 일관성을 검사하고 유효 범위를 벗어난 음수 자산 경로는 오류로 중단한다.
- Equity Curve, 거래표, 체결/거절/생략 이벤트, 최종 자산·수익률·승률·MDD·평균 종료 손익·총 수수료.
- 거래 없는 경우 0과 N/A를 구분한다. 승률은 종료 거래만 분모에 포함한다.
- 수동 실행, 결과 카드·곡선·거래별 손익·이벤트 표, 신호와 체결 마커 구분, 입력 변경 시 결과 무효화.

## 사용 기술

Python, pandas, NumPy, dataclasses/Enum, Plotly, Streamlit. 별도 백테스트 프레임워크 없이 STEP 4 evaluator를 재사용한다.

## 주요 관련 파일

| 파일 | 구현 근거 |
| --- | --- |
| [backtest/engine.py](../../backtest/engine.py) | `run_backtest`, 거래·이벤트·평가 원장 |
| [backtest/models.py](../../backtest/models.py), [backtest/metrics.py](../../backtest/metrics.py) | 설정/결과 타입, 성과 공식 |
| [strategies/evaluator.py](../../strategies/evaluator.py) | 원본 전략 신호 생성 |
| [ui/backtest_view.py](../../ui/backtest_view.py), [ui/backtest_charts.py](../../ui/backtest_charts.py), [ui/charts.py](../../ui/charts.py) | 실행·무효화·성과/체결 시각화 |
| [tests/test_backtest.py](../../tests/test_backtest.py), [tests/test_backtest_ui.py](../../tests/test_backtest_ui.py), [tests/test_backtest_live.py](../../tests/test_backtest_live.py) | 손계산·UI·선택적 실제 데이터 검증 |

## 확인 가능한 테스트 결과

- 당시 커밋의 테스트는 다음 봉 체결, 마지막 봉/누락 봉, 상태 전이, LONG/SHORT 비용, 거래량 제한, 회계, 미래 가격 변경과 불변성을 검사한다.
- 당시 전체 통과 수·실행일·원본 실행 로그는 **기록 확인 불가**. 기존 문서에는 테스트 방법만 제시되어 있다.
- 후속 STEP 6의 [tests/fixtures/step5_risk_off.json](../../tests/fixtures/step5_risk_off.json)은 기존 문서상 STEP 6 변경 전 엔진 결과 10개를 보존한 회귀 기준이다. fixture 파일의 Git 추가 시점은 STEP 6이며 STEP 5에 이미 파일이 있었다고 기록하지 않는다.
- 해당 기준을 비교하는 `test_risk_off_matches_ten_frozen_step5_results`와 후속 누적 통과 기록은 이전 결과 호환성의 후속 근거다.
- 이번 문서 정리에서는 테스트/실 API를 실행하지 않았다.

## 오류 및 해결 기록

- 개발 중 실제 발생 오류·해결 순서/시각은 **기록 확인 불가**.
- 음수 자산, 분모 0, 거래량 부족, 잘못된 OHLCV, 미확정 봉을 명시적으로 처리하는 코드는 확인된다. 방어 테스트를 실제 장애 이력으로 기록하지 않는다.

## 확인이 필요한 사항과 한계

당시 수동 거래 원장 검산 로그는 **기록 확인 불가**다. 비용/유동성은 가정이며 호가·펀딩·차입·강제청산·지연을 재현하지 않는다. Sharpe Ratio와 투자 성능 보장은 구현 범위에 없다.
