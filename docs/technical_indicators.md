# STEP 3 기술적 지표 계산 계약

`indicators/technical.py`는 pandas/numpy만 사용하는 계산 모듈이다. Streamlit,
Plotly, ccxt, 주문 기능에 의존하지 않으며 향후 Technical Agent와 백테스팅에서
같은 함수를 재사용할 수 있다.

## 입력과 반환

`add_technical_indicators(data, timeframe=None)`는 UTC 시간순 OHLCV를 입력받고
새 DataFrame을 반환한다. 기존 컬럼, 값, 인덱스와 순서를 보존하며 원본을
수정하지 않는다. 정렬되지 않은 시각, 중복 또는 결측 시각은 ValueError로
처리한다. `attrs`의 기존 메타데이터를 보존하고 계산 정책을 추가한다.

개별 함수의 가격/거래량 입력은 Series다. `calculate_ema`, `calculate_sma`,
`calculate_rsi`, `calculate_volume_sma`는 같은 인덱스의 Series를 반환한다.
`calculate_macd`와 `calculate_bollinger_bands`는 같은 인덱스의 DataFrame을
반환한다. 기간은 양의 정수이며 MACD fast는 slow보다 작아야 한다.

## 수식과 초기화

| 컬럼 | 기본 계산 | 첫 유효 결과에 필요한 연속 봉 수 |
| --- | --- | --- |
| `rsi_14` | 첫 14개 변화량의 평균 상승/하락을 seed로 사용, Wilder α=1/14 | 15 |
| `ema_20`, `ema_60` | 첫 N개 종가 SMA seed, EMA α=2/(N+1) | 20 / 60 |
| `sma_20`, `sma_60` | 현재 봉 포함 최근 N개 종가 산술평균 | 20 / 60 |
| `macd` | 독립적으로 초기화한 EMA12 − EMA26 | 26 |
| `macd_signal` | MACD 최초 9개 유효값 SMA seed, 이후 EMA9 | 34 |
| `macd_histogram` | MACD − Signal | 34 |
| `bb_middle` | SMA20 | 20 |
| `bb_upper`, `bb_lower` | SMA20 ± 2 × 모집단 표준편차 (`ddof=0`, 분모 N) | 20 |
| `volume_sma_20` | 현재 확정 봉 포함 최근 20봉 거래량 평균 | 20 |
| `volume_ratio_20` | 현재 거래량 / Volume SMA20, 평균 0이면 NaN | 20 |

RSI는 평균 하락이 0이고 평균 상승이 양수이면 100, 상승이 0이고 하락이
양수이면 0이다. 상승과 하락이 모두 0인 평탄한 구간은 중립값 50으로 정의한다.
지표 계산 전에 필요한 데이터가 부족하면 NaN을 유지한다.

모든 rolling window는 `center=False`이며 현재 확정 봉까지만 사용한다.
미래 값, 보간, backward fill을 사용하지 않는다. 결측/비유한 입력은 계산에서
NaN으로 처리하며 재귀 지표는 그 뒤에서 새 seed를 기다린다. OHLCV 원본 값은
그대로 보존한다. timeframe을 명시하거나 `attrs['timeframe']`이 있으면 시간
간격이 끊긴 위치에서도 모든 지표를 다시 초기화한다.

## 워밍업과 화면 표시

대시보드는 분석 구간 앞의 확정 봉 300개를 추가 수집한다. 표시 한도는 기존과
동일한 최근 5,000개이며, 워밍업을 포함한 총 수집 한도는 5,300개다. 요청당
최대 1,000개, 조회당 최대 7회 페이지 요청 제한과 ccxt Rate Limit을 유지한다.
`fetch_binance_ohlcv`의 `warmup_candles` 기본값은 0이므로 기존 호출도 유지된다.

```python
from collectors.binance import fetch_binance_ohlcv
from indicators.technical import add_technical_indicators

raw = fetch_binance_ohlcv("BTC/USDT", "1h", "1개월", warmup_candles=300)
calculated = add_technical_indicators(raw)
visible = calculated.loc[
    calculated["timestamp"] >= raw.attrs["analysis_start"]
].copy()
```

`services/market_data.py`는 이 순서를 실행하고 결과를 60초 캐시한다. 지표 표시
체크박스는 캐시 키에 포함하지 않는다. 종목/타임프레임/기간 변경은 해당 조합을
조회하며 새로고침은 선택한 조합만 다시 조회한다.

## 한계와 비교 기준

재귀 지표는 유한한 과거 구간에서 초기화된다. 300봉 워밍업으로 영향이
줄어들지만 거래소 차트의 전체 이력 기반 계산과 완전히 일치한다고 보장하지
않는다. 최초 가격 seed, MACD fast EMA 초기 위치, 평탄 구간 RSI 정책 또는
표준편차 ddof가 다른 구현과는 값이 다를 수 있다. TA-Lib과의 비트 단위 일치를
목표로 하지 않으며 위 계약의 수식을 사용한다.

워밍업 데이터가 없거나 누락이 있으면 이를 화면에 알리고, 충분한 연속 봉이
확보될 때까지 해당 지표를 NaN으로 유지한다. 캔들 자체를 임의 생성하지 않는다.

## 검증 실행

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

실제 API 검증은 별도로 활성화한다. 네트워크/지역 접근 제한이 있으면 실제
테스트 실패를 단위 테스트 성공과 구분해야 한다.

```powershell
$env:RUN_BINANCE_LIVE_TESTS = '1'
try {
    .\.venv\Scripts\python.exe -m unittest discover -s tests -p test_binance_live.py -v -f
} finally {
    Remove-Item Env:RUN_BINANCE_LIVE_TESTS
}
```

계산 참고: [Wilder RSI](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/relative-strength-index-rsi),
[EMA 초기화 방식](https://github.com/TA-Lib/ta-lib/blob/main/docs/studies/ema-seeding/README.md),
[pandas rolling 표준편차](https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.core.window.rolling.Rolling.std.html).
