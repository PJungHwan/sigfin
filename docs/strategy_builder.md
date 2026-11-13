# STEP 4 전략 조건 조립기

사이드바의 **전략 조건 조립기**에서 이름과 LONG/SHORT 조건을 편집한다.
각 방향의 조건은 모두 AND 또는 모두 OR로 결합한다. 조건 행은 추가·삭제할 수
있으며 한 방향당 최대 20개다. 조건이 없는 방향은 비활성이고 신호를 만들지 않는다.

메인 화면의 **Strategy Signals → 신호 미리보기**를 켜면 현재 조회한 실제
확정 봉에 대해 조건을 평가한다. 개수, 최근 충족 시각(UTC 봉 마감), 최신 봉의
조건별 결과와 최근 100봉의 상세 결과를 표시한다. 차트에는 LONG/SHORT/충돌
마커를 구분해서 표시한다. 지표 표시를 꺼도 조건 계산에 필요한 지표는 유지된다.
편집이나 미리보기 ON/OFF는 기존 시장 데이터 캐시를 사용한다.

## 모델과 검증

`strategies/models.py`의 불변 dataclass와 Enum이 전략을 정의한다.

* `Operand`: 숫자 상수 또는 지표 피연산자.
* `Condition`: 고유 ID, 좌측 지표, 연산자, 우측 피연산자.
* `ConditionGroup`: LONG/SHORT 방향과 단일 AND/OR 조건 목록.
* `Strategy`: 이름, 스키마 버전, LONG/SHORT 그룹.

숫자 상수는 bool/문자열/NaN/Infinity를 허용하지 않는다. RSI 상수 범위는
0~100이며 가격·거래량·거래량 비율의 비교 상수는 0 이상이다. MACD 계열의
상수는 양수·음수 모두 가능하다.

| 비교 단위 | 허용 지표 |
| --- | --- |
| RSI | RSI14 (숫자 비교) |
| 가격 | Open, High, Low, Close, EMA20/60, SMA20/60, Bollinger Upper/Middle/Lower |
| 가격 차이 | MACD, MACD Signal, MACD Histogram |
| 기초자산 거래량 | Volume, Volume SMA20 |
| 거래량 비율 | Volume / SMA20 (숫자 비교) |

다른 지표와 비교할 때는 같은 단위만 허용하며 자기 자신과의 비교는 차단한다.
예를 들어 Close와 EMA20, MACD와 Signal은 비교할 수 있지만 RSI와 Close는
비교할 수 없다. Python 코드 문자열이나 임의 연산자는 지원하지 않는다.

## 조건 평가 계약

`strategies/evaluator.py`는 명시적인 pandas 비교 함수만 사용한다.
원본 DataFrame의 값, 순서, 인덱스와 메타데이터를 변경하지 않는다.

| 연산자 | 조건 |
| --- | --- |
| `>`, `<`, `>=`, `<=` | 현재 확정 봉의 좌측과 우측 값 비교 |
| `Cross Up` | 직전 좌측 ≤ 직전 우측 **그리고** 현재 좌측 > 현재 우측 |
| `Cross Down` | 직전 좌측 ≥ 직전 우측 **그리고** 현재 좌측 < 현재 우측 |

현재 값이 비교 대상과 같으면 교차로 판정하지 않는다. NaN/비유한 입력은
False다. 교차는 양쪽 피연산자의 직전/현재 값이 모두 유효하고, 두 봉의 시간이
선택 timeframe 간격으로 연속일 때만 판정한다. 첫 입력 봉은 직전 값이 없어
교차 False이며, 표시 구간 앞의 숨겨진 워밍업 봉을 교차 판정에 사용하지 않는다.

timestamp는 시간대가 지정된 datetime 컬럼이어야 하며 결측·중복 없이
시간순이어야 한다. timeframe 인수 또는 `data.attrs['timeframe']`을 기준으로
`timestamp + timeframe <= as_of`를 검증한다. `as_of` 기본값은 현재 UTC 시각이다.
미확정/미래 봉이 포함되면 명시적인 `StrategyEvaluationError`를 발생시킨다.

`evaluate_condition`은 원본 인덱스의 Boolean Series를 반환한다.
`evaluate_strategy`는 LONG/SHORT/충돌 Boolean Series, 상태(NONE/LONG/SHORT/CONFLICT),
조건별 Boolean DataFrame과 비교 데이터 유효성 DataFrame을 반환한다.
LONG/SHORT가 동시에 True이면 두 결과를 유지하고 충돌로 표시한다.

```python
from services.market_data import load_market_data
from strategies.models import default_strategy
from strategies.evaluator import evaluate_strategy

data = load_market_data("BTC/USDT", "1h", "1개월")
result = evaluate_strategy(data, default_strategy())
long_mask = result.long
short_mask = result.short
conflict_mask = result.conflict
```

조건이 여러 봉 동안 유지되면 각 봉이 충족 시점으로 표시된다. 신호 개수는
거래/체결 횟수가 아니다. LONG/SHORT 개수에는 충돌 봉도 포함되지만 차트의
충돌 봉은 방향 마커 대신 별도 X 마커를 사용한다. 마커의 가격 좌표는 시각화용이며
체결 가격을 뜻하지 않는다. 같은 봉 종가 체결이나 수익률을 가정하지 않는다.

## JSON 저장·불러오기

**전략 JSON 저장**은 브라우저로 파일을 다운로드한다. **전략 JSON 파일**에서
선택한 후 **전략 불러오기**를 누르면 검증 성공 후에만 편집 중인 전략을 교체한다.
잘못된 파일은 현재 편집 상태를 유지한 채 오류를 표시한다.

`strategies/storage.py`는 UTF-8 JSON 직렬화/역직렬화를 담당한다. 서버에 파일을
쓰거나 업로드 파일명으로 경로를 생성하지 않는다. 다운로드 파일명은 안전한
문자만 사용하고 UTC 시각과 임의 식별자를 포함해 기존 파일명과 구분한다.
최대 파일 크기는 64KB이며 중복 JSON 필드, 알 수 없는 필드/버전, NaN/Infinity,
잘못된 인코딩, 잘못된 조건을 차단한다.

스키마 버전 1은 루트의 `schema_version`, `name`, `long`, `short` 필드를 사용한다.
그룹은 `type="group"`, `direction`, `combine`, `conditions`이고, 조건은
`type="condition"`, `id`, `left`, `operator`, `right`이다. 숫자 right는
`{"kind":"number","value":30.0}`, 지표 right는
`{"kind":"indicator","indicator":"ema_20"}` 형식이다.
중첩 그룹은 현재 거부하며 추후 스키마 버전 확장으로 추가할 수 있다.

```python
from strategies.storage import strategy_to_json, strategy_from_json

encoded = strategy_to_json(default_strategy())
restored = strategy_from_json(encoded)
```

## 검증 실행

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

실제 API 검증은 별도로 활성화한다. 네트워크/지역 제한으로 실패하면 단위
테스트와 실제 API 테스트 결과를 구분해야 한다.

```powershell
$env:RUN_BINANCE_LIVE_TESTS = '1'
try {
    .\.venv\Scripts\python.exe -m unittest discover -s tests -p '*live.py' -v -f
} finally {
    Remove-Item Env:RUN_BINANCE_LIVE_TESTS
}
```

브라우저 파일 선택/다운로드 대화상자의 직접 조작은 AppTest 범위에 포함되지 않는다.
업로드 콜백, JSON 검증과 편집 상태 복원은 자동 테스트로 검증한다.
백테스트, 포지션, 주문, 자동매매, AI Agent와 FRED는 이번 단계에 포함하지 않는다.
