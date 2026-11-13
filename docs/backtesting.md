# STEP 5 기초 백테스트 계약

STEP 6은 이 계약을 Risk OFF 기준으로 유지한다. 리스크 활성화 시 추가되는 체결 우선순위,
유동성 재시도 및 로그 컬럼은 [리스크 관리 문서](risk_management.md)를 참고한다.

`backtest/engine.py`, `backtest/models.py`, `backtest/metrics.py`는 Streamlit과
독립적이다. STEP 4의 `evaluate_strategy`를 재사용하며 주문 API를 호출하지 않는다.
초기 투자금 10,000 USDT, 편도 수수료 0.1%, 편도 슬리피지 0.05%, 직전 확정 봉
거래량 대비 참여율 10%가 기본값이다. UI 또는 `BacktestConfig`에서 조정할 수 있다.

```python
from backtest.engine import run_backtest
from backtest.models import BacktestConfig
from services.market_data import load_market_data
from strategies.models import default_strategy

data = load_market_data("BTC/USDT", "1h", "1개월")
result = run_backtest(data, default_strategy(), BacktestConfig())
equity = result.equity_curve
trades = result.trades
events = result.events
metrics = result.metrics
```

## 데이터와 시점

OHLCV 원본과 지표, 인덱스, 메타데이터를 수정하지 않는다. 유한한 양의 가격,
비음수 거래량, 정상 OHLC 범위를 요구한다. 전략 평가기가 시간순·중복 없는
시간대 지정 timestamp와 확정 봉 여부를 검증한다. `as_of`를 지정하면 동일한
평가 기준 시각으로 재현할 수 있다. 기본값은 현재 UTC 시각이다.

timestamp는 봉 **시작 시각**이다. 봉 i의 확정 신호를 다음 연속 봉 i+1의
시가에 체결한 것으로 가정한다. 같은 봉 종가 체결은 하지 않는다. 다음 봉이
누락되면 뒤의 이용 가능한 봉으로 대체하지 않는다. 마지막 봉 신호는 다음 봉이
없어 미체결이다. 마지막 포지션을 강제로 청산하거나 가상의 다음 봉을 만들지 않는다.

이벤트 원장은 `signal_bar`, `execution_bar`, 신호 봉 시작 `signal_timestamp`,
봉 마감 `signal_confirmed_at`, 다음 봉 시작 `execution_timestamp`를 모두 저장한다.
연속 봉에서는 신호 확인 경계와 다음 봉 시작 시각이 동일하지만 **신호 봉과 체결
봉은 다르다**. 봉 경계에서 즉시 다음 시가를 이용할 수 있다는 단순화된 가정이며,
실제 신호 계산 지연과 주문 전송 지연을 재현하지 않는다.

## 포지션 전이

단일 종목에 FLAT/LONG/SHORT 중 하나만 유지한다. 피라미딩이나 추가 진입은 없다.

| 현재 상태 | 직전 확정 신호 | 다음 봉 처리 |
| --- | --- | --- |
| FLAT | LONG만 | LONG 진입 |
| FLAT | SHORT만 | 가상 SHORT 진입 |
| FLAT | LONG/SHORT 동시 | 충돌 기록, 진입 없음 |
| LONG | SHORT 포함 | LONG 청산만 |
| SHORT | LONG 포함 | SHORT 청산만 |
| 보유 중 | 같은 방향만 / 신호 없음 | 보유 유지 |

보유 중 동시 신호에는 반대 신호가 포함되므로 청산만 한다. 청산에 사용한 신호로
같은 시가에 반대 방향에 진입하지 않는다. 청산한 봉이 마감된 후 별도 신호가
발생해야 그다음 봉 시가에 신규 진입할 수 있다. 비교 조건이 계속 충족되면 각
확정 봉은 별도 신호로 평가된다.

## 체결·거래량 가정

매수(LONG 진입/SHORT 청산)는 `시가 × (1 + slippage)`에, 매도(SHORT 진입/LONG
청산)는 `시가 × (1 - slippage)`에 체결한다. 수수료는 진입·청산 각각
`수량 × 체결가 × fee_rate`로 차감한다.

체결 봉 전체 거래량은 시가 시점에 알려지지 않았다. 따라서 수량 한도는
**직전 확정 봉 거래량 × participation**만 사용한다. 현재 봉이나 미래 봉의
거래량을 미리 읽어 주문 크기나 청산 가능 여부를 결정하지 않는다.

진입 수량은 자금 예산과 거래량 한도 중 작은 값이다. 청산은 전량만 지원하며
그 전량이 거래량 한도를 넘으면 미체결로 기록하고 포지션을 유지한다. 부분 청산,
주문 잔량의 자동 이월은 없다. 이후 별도 반대 신호가 발생하면 다시 청산을 시도한다.
거래량 0, 사용할 자금 없음, 누락된 다음 봉, 마지막 신호도 원장에 사유를 남긴다.

이전 봉 거래량 한도는 유동성의 근사값이며 실제 시가 호가량을 보장하지 않는다.
현재 봉의 거래량이 사후에 0으로 관측되더라도 이전 봉 한도만으로 가상 체결할 수
있다는 한계가 있다. 실제 시장 체결을 보장하거나 캔들 총량을 호가로 해석하지 않는다.

## 1배 진입과 가상 숏 회계

진입 직전 현금을 C, 시가를 O, 슬리피지 적용 체결가를 P, 수수료율을 f라고 하면:

* LONG 자금 한도 수량: `q = C / (P + P*f)`.
* SHORT 자금 한도 수량: `q = C / (O + P*f)` (SHORT 매도 P ≤ O).
* 실제 진입 수량: 위 수량과 직전 거래량 한도 중 작은 값.

LONG 진입 현금은 `C - q*P - fee`다. SHORT는 가상의 자산을 매도했다고 보아
장부 현금을 `C + q*P - fee`로 기록한다. SHORT 매도 대금과 원래 시가 노출
`q*(O+P)`를 잠금 현금으로 표시해 재사용을 막는다. 장부 현금 증가가 투자 수익이나
추가 구매력은 아니다. `free_cash = cash - restricted_cash`는 진입 예산 기준의
미사용 현금이며 실제 거래소의 출금 가능 금액이나 변동 증거금 계산은 아니다.

진입 시가 기준 노출은 진입 전 자산 1배 이하이며, 보유 중 수량은 고정한다.
가격 변화 이후의 노출/자산 비율을 항상 1로 유지하는 리밸런싱은 구현하지 않는다.
SHORT는 실제 Binance Spot 거래가 아닌 가격 하락에 대한 가상 포지션이다.

종가 M에서 signed_quantity는 LONG +q, SHORT -q, FLAT 0이다.

```text
equity = cash + signed_quantity * M
gross_closed_pnl = Σ side_sign * q * (exit_fill - entry_fill)
realized_pnl = gross_closed_pnl - 모든 지급 수수료
unrealized_pnl = signed_quantity * (M - entry_fill)  # 포지션 없으면 0
equity = initial_capital + realized_pnl + unrealized_pnl
```

열린 거래의 진입 수수료는 이미 지급한 비용이므로 실현 손익에 포함한다. 미실현
가격 손익은 이를 중복 차감하지 않는다. 종료 시 열린 포지션은 마지막 확정 종가로
평가하고, 미체결 청산의 슬리피지나 수수료는 부과하지 않는다.

가상 숏 가격 급등 등으로 현금·평가 자산이 음수가 되는 경로는 `BacktestError`로
중단한다. 손실을 큰 값에서 0으로 잘라내어 성공 결과를 만들지 않는다. IEEE 부동소수점
반올림 범위의 작은 음수만 정리한다. 자동 청산·증거금 모델을 도입하는 대신 기본
회계의 유효 범위를 벗어났음을 명시한다.

## 결과와 성과 공식

`BacktestResult`는 Equity Curve, 진입/청산 거래표, FILLED/REJECTED/SKIPPED 이벤트,
전략 신호, 성과, 사용 설정, 전략 JSON, timeframe, 종목, 종료 포지션을 포함한다.
OPEN 거래의 exit_price와 net_pnl은 NaN이며 종료 거래 수와 승률에 포함하지 않는다.

| 지표 | 정의 |
| --- | --- |
| 누적 수익률 | `(최종 평가 자산 / 초기 투자금 - 1) × 100` |
| 승률 | `순손익 > 0인 종료 거래 수 / 종료 거래 수 × 100` |
| 평균 거래 손익 | 종료 거래의 비용 반영 순손익 평균(USDT) |
| 거래 순손익 | 방향별 가격 손익 − 진입 수수료 − 청산 수수료 |
| 총 수수료 | 실행된 모든 진입·청산 수수료 합계 |
| MDD | `-min(E_t / cummax(E)_t - 1) × 100` |

Equity Curve에는 첫 봉 시가 시각의 초기 자산 점과 각 확정 봉 마감 평가점을
포함한다. MDD는 이 시간순 곡선으로 계산하며 봉 내부 낙폭을 뜻하지 않는다.
거래가 없으면 거래 수·수수료·유효 평가기간의 수익률은 0, 승률·평균 종료 손익은
N/A다. 빈 데이터는 최종 자산을 초기 투자금으로 유지하고 수익률·MDD도 N/A다.
Sharpe Ratio와 연율화는 구현하지 않는다.

## 화면과 재실행

백테스팅 영역에서 설정과 전략 요약을 확인한 뒤 **백테스팅 실행**을 눌러야
결과가 생긴다. 전략, 종목, timeframe, 기간, 비용/거래량 한도 또는 데이터 내용이
변경되면 기존 결과를 무효화하고 재실행을 안내한다. 동일한 데이터의 지표 표시
ON/OFF는 결과를 유지하며 엔진을 다시 실행하지 않는다.

가격 차트의 조건 신호는 삼각형/X, 가상 진입은 다이아몬드, 가상 청산은 빈 원이다.
실행하지 않거나 결과가 무효화되면 가상 체결 마커와 성과를 표시하지 않는다.
자산 곡선, 종료 거래 순손익 차트, 손익 구분 거래표, 이벤트·회계표를 표시한다.
수집 제한이 있으면 선택 기간 전체가 아닌 실제 수집된 구간을 평가하며 화면에
실제 평가 구간을 표시한다.

## STEP 18 — 고정 입력 전략 비교

기존 단일 실행/Risk 비교를 유지하고 [실험 모듈](../backtest/experiments.py)과
[화면](../ui/experiment_view.py)을 추가했다. 전략 2~6개 × Risk OFF/ON을 같은
시장 구간·초기금·수수료·슬리피지·직전 봉 거래량 참여율로 각각 독립 실행한다.
시간순 검토/검증 구간도 선택할 수 있으며 각 구간은 동일 초기금/FLAT으로 시작한다.
앞 구간의 포지션·신호 주문은 넘기지 않는다. 구간 첫 Cross는 False, 마지막 신호는
다음 봉이 없어 미체결이다. 성과 차이는 ON−OFF이며 수익률/승률/MDD 차이 단위는 %p다.

실험 입력 JSON에는 OHLCV 원본과 전략·공통 비용/Risk·기준 UTC·버전/해시를 보존한다.
STEP 15의 표 직렬화를 재사용하며 업로드 재실행은 API/SQLite/현재 원장에 접근하지 않는다.
입력 해시/계산 코드 변경은 검출하고, 코드가 다른 버전에서 동일 재현을 주장하지 않는다.

외부 지표 열은 사용하지 않고 저장 OHLCV로 지표를 인과적으로 재계산한다. 검증 구간은
앞 구간을 지표 워밍업으로만 사용할 수 있다. 조회 구간 이전 외부 워밍업은 새로 수집하지
않으므로 초기 NaN이 유지된다. 기존 단일 실행과 초기 지표/신호가 다를 수 있다.
구간/설정이 다른 성과를 동일 조건의 성과처럼 비교하지 않는다. 일부 실패는 PARTIAL,
전체 실패는 FAILED이며 해당 성과는 N/A다. 상세 계약·테스트·재현 절차는
[STEP 18 기록](steps/STEP_18.md)을 따른다.

## 한계와 검증

과거 성과는 미래 수익을 보장하지 않는다. 동일 데이터로 전략을 개발·검증하면
과최적화 위험이 있다. OHLCV와 고정 비용 모델은 실제 체결·호가 유동성·계산/전송
지연을 완전히 재현하지 못한다. 거래소 최소 수량·호가 단위, 대차 비용, 무기한 선물
펀딩비, 실제 거래소 청산·증거금 제약은 이번 단계에서 제외한다.

작은 합성 데이터의 수작업 검산을 `tests/test_backtest.py`, 화면 실행·무효화를
`tests/test_backtest_ui.py`, 실제 API 연동을 `tests/test_backtest_live.py`에서 검증한다.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

실제 API 테스트는 별도로 활성화하며 네트워크 오류를 계산 오류와 구분한다.

```powershell
$env:RUN_BINANCE_LIVE_TESTS = '1'
try {
    .\.venv\Scripts\python.exe -m unittest discover -s tests -p '*live.py' -v -f
} finally {
    Remove-Item Env:RUN_BINANCE_LIVE_TESTS
}
```

참고: [pandas 누적 최댓값](https://pandas.pydata.org/docs/reference/api/pandas.Series.cummax.html),
[가격 상승 시 숏 손실의 원리](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins-51).
