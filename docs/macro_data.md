# STEP 7 FRED 거시경제 데이터

기존 Binance 수집·전략·리스크 백테스트는 유지한다. FRED는 독립적으로 조회하고,
암호화폐 가격과의 상관관계·투자 신호·발표 이벤트 수익률·LLM 해석에 연결하지 않는다.

## 키 설정과 실행

사용자 키가 없어도 앱은 실행되며 FRED 설정 안내를 표시한다. 계정/키 자동 생성은 없다.
프로젝트 루트에 `.env`를 직접 만들고 본인의 키를 설정한다.

```dotenv
FRED_API_KEY=여기에_본인의_32자리_소문자_영숫자_키
```

형식만 보여 주는 예시이며 유효한 키가 아니다. 프로세스의 `FRED_API_KEY`가 우선하고,
없으면 프로젝트의 `.env`만 읽는다. 상위 폴더나 다른 자격 저장소를 탐색하지 않는다.
값 보간을 비활성화했으며 키는 코드/로그/화면/캐시 데이터/Session State에 넣지 않는다.
`.env`와 `.env.*`는 Git에서 제외하고, 비어 있는 `.env.example`만 추적 가능하게 한다.
공식 키 안내: https://fred.stlouisfed.org/docs/api/api_key.html

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py --server.port 8503
.\.venv\Scripts\python.exe -m pytest -q
```

다른 서버가 해당 포트를 사용하면 사용하지 않는 포트를 선택한다. 실행 권한/인증이
부족하면 해당 검증을 보류하며 권한 확장, 인증서 검증 해제, API 제한 우회를 수행하지 않는다.
`requests`는 기존 ccxt 환경에 설치된 것을 재사용하고 직접 의존성으로 명시했다.
기존 장기 실행 서버가 이전 config.settings 모듈을 유지하면 새 설정 상수 import가 실패할 수
있으므로 코드 갱신 후 서버를 새 프로세스로 재시작한다. 네트워크 제한을 가진 프로세스는
Binance 연결도 실패할 수 있으며, 이 경우 허용된 일반 터미널에서 실행해야 한다.

## 지원 시리즈

| 시리즈 | 값의 의미 | 원본 단위 | 주기 | 계절 조정 |
| --- | --- | --- | --- | --- |
| CPIAUCSL | 소비자물가지수 수준 | Index 1982-1984=100 | 월별 | SA |
| FEDFUNDS | 실효 연방기금금리 월별 평균 | Percent | 월별 | NSA |
| UNRATE | U-3 실업률 | Percent | 월별 | SA |
| PAYEMS | 비농업 총 고용 수준 | Thousands of Persons (천 명) | 월별 | SA |
| DGS10 | 10년 고정 만기 미국 국채 수익률 | Percent | 일별 | NSA |

FEDFUNDS는 정책 목표금리 범위가 아니다. PAYEMS는 전월 대비 고용 증감이 아니다.
CPI YoY는 지정된 CPIAUCSL 계절 조정 지수의 전년동월 대비 변화율로 원본 지수와 구분한다.
공식 시리즈 설명/단위: https://fred.stlouisfed.org/series/CPIAUCSL ,
https://fred.stlouisfed.org/series/FEDFUNDS , https://fred.stlouisfed.org/series/UNRATE ,
https://fred.stlouisfed.org/series/PAYEMS , https://fred.stlouisfed.org/series/DGS10

## 수집 및 데이터 계약

`collectors/fred.py`의 `fetch_fred_series(series_id, start_date, end_date, api_key=None)`는
Streamlit 없이 사용한다. 날짜 인자는 ISO `YYYY-MM-DD`이며 오늘까지 최대 22년을 허용한다.
`models/macro.py`는 허용된 시리즈와 오류·메타데이터 계약을 정의한다.

먼저 `/fred/series`로 식별자·단위·관측 주기·계절 조정을 검증하고,
`/fred/series/observations`에 `units=lin`, `output_type=1`, `sort_order=asc`로 원본 수준을
요청한다. 빈도 변환이나 단위 변환을 API에 요청하지 않는다. 현재 날짜의 realtime 구간을
명시하여 현재 개정값 스냅샷을 요청한다.

한 시리즈당 최대 3회(메타데이터 1회 + 관측 페이지 2회), 페이지당 최대 10,000개,
총 10,000개 한도다. 응답 offset/count, 페이지 진행, 전체 개수 안정성을 검사한다.
한도 내 전체 수집이 불가능하면 조용히 잘라 정상처럼 표시하지 않고 명시적 오류를 낸다.
동일 프로세스의 FRED 요청은 락으로 직렬화하고 최소 1초 간격을 둔다. 자동 재시도와
리다이렉트는 사용하지 않는다. 여러 별도 프로세스 간 공통 제한기는 구현하지 않았다.

반환 컬럼:

- `series_id`, `observation_date`, `value`, `units`, `frequency`
- `release_date`, `available_at`, `vintage_date`
- `realtime_start`, `realtime_end`, `fetched_at`

관측일은 정렬하고 동일 관측일의 완전히 같은 값/실시간 구간은 중복 제거한다. 서로 다른
값이나 버전이 같은 관측일에 섞이면 임의로 하나를 선택하지 않고 오류 처리한다.
공식 결측 표기 `.`/빈 값은 NaN으로 보존하며 채우지 않는다. 잘못된 날짜, 월별 시리즈의
월 시작일 불일치, 잘못된 숫자/비유한 값, 아래 허용 경계를 벗어난 관측일은 검증 오류다.
단위/주기, 결측·중복 개수, UTC 수집 시각, 실제 API 메타데이터 갱신 시각을 attrs에 기록한다.

### 월별 경계 정책 (FEDFUNDS 오류 수정)

FRED 월별 관측값은 요청 시작일이 월 중간이어도 해당 월의 1일 라벨을 반환할 수 있다.
실제 `2025-10-15 ~ 2026-10-15` FEDFUNDS 요청에서 응답 헤더는 요청 날짜 그대로였지만
첫 관측일은 `2025-10-01`이었다. 이는 타임존 변환으로 날짜가 바뀐 현상이 아니다.
CPI는 이미 계산용 조회 시작일을 월초로 정렬했기 때문에 이 검증 오류가 드러나지 않았다.

일관된 정책은 **원본 관측 라벨 기준 [요청 시작일, 종료일] 양 끝 포함**이다.

- 월별 응답에서 시작 월의 1일 라벨만 정상적인 겹침 문맥으로 인정한다. 그 라벨이
  요청 시작일보다 이르면 반환/표시에서 제외하며 값을 바꾸거나 날짜를 이동하지 않는다.
- 시작 월보다 이전 달, 월초가 아닌 월별 날짜, 종료일 이후의 데이터는 계속 오류 처리한다.
- 일별 DGS10은 시작일·종료일을 그대로 검증하며 같은 달의 앞 날짜도 허용하지 않는다.
- 예: `2024-01-15 ~ 2024-03-15`의 월별 API 라벨 1/1, 2/1, 3/1 중 2/1과 3/1만 반환한다.
  시작 월 1/1을 보려면 조회 시작일을 1/1로 지정한다. 같은 달 중간 날짜끼리 조회해
  월초 라벨밖에 없다면 DATA_VALIDATION이 아닌 EMPTY_RESPONSE로 보고한다.
- attrs의 `allowed_response_start/end`, `boundary_observations_removed`,
  `observation_window_policy`로 허용 응답 구간과 제외 개수를 기록한다.
- 날짜 인자는 엄격한 YYYY-MM-DD다. 시각·타임존 문자열을 날짜로 암묵 변환하지 않는다.
  UTC 변환은 수집/갱신 시각에 적용하고, 관측 월의 라벨은 이동시키지 않는다.

서비스도 양 끝 표시 경계를 적용한다. CPI 계산용 12개월은 그대로 유지하고 계산 후
동일한 라벨 정책으로 표시 구간을 선택한다. 새 정책은 서비스 캐시와 UI 결과 식별을
갱신하여 이전 DATA_VALIDATION 표시를 그대로 재사용하지 않는다.

## 관측일, 발표일, 빈티지와 미래 정보

월별 `2024-01-01`은 해당 관측 월의 라벨이다. 그날 실제 발표됐거나 그날부터 알려진
값이라는 뜻이 아니다. UTC 자정으로 정규화한 관측일도 실제 발표 시각을 뜻하지 않는다.

- `release_date`, `available_at`, `vintage_date`: 확인하지 못했으므로 NaT/None.
- `realtime_start/end`: API가 반환한 실시간 구간. Python date로 보존하여 `9999-12-31`도
  손실 없이 처리한다. 최초 발표일/최초 빈티지로 추정하지 않는다.
- `fetched_at`: 데이터를 수집한 UTC 시각. 발표 시각과 구분한다.
- attrs `source_last_updated`: 실제 메타데이터 갱신 시각. 관측값별 최초 발표 시각이 아니다.
- attrs `version_policy=current_revised_snapshot`, `is_point_in_time=False`.

이 데이터는 과거 특정 시점에 알 수 있었던 값이라는 보장이 없다. 향후 백테스트에
연결하려면 실제 발표 시각과 당시 빈티지를 별도로 확보하는 단계가 필요하다.
STEP 7에서는 거시 데이터를 기존 백테스트/신호 평가기로 전달하지 않는다.
FRED 실시간 구간 안내: https://fred.stlouisfed.org/docs/api/fred/realtime_period.html

## CPI 계산과 캐시

`services/macro_service.py`는 CPI의 계산용 과거 12개월을 추가 수집하고 계산 후 표시
기간으로 자른다. `yoy_pct = (CPI_t / CPI_동일월_1년전 − 1) × 100`이다. 단순 12행 shift를
쓰지 않아 누락 월이 있어도 다른 달과 비교하지 않는다. 분모가 없거나 0 이하, 현재 값이
결측이면 NaN이다. 미래 행은 이전 결과를 바꾸지 않는다. 원본 DataFrame을 변경하지 않는다.

캐시는 시리즈/기간/자격 범위별 60분, 최대 128개다. 원문 키 인자는 `_api_key`로 캐시
해시에서 제외하고 SHA-256 자격 범위를 별도 키로 사용한다. 호출 시마다 자격을 확인하므로
키가 없거나 다른 키이면 이전 자격의 데이터를 그대로 제공하지 않는다. 예외는 캐시하지
않으며, 서비스 `refresh=True`는 선택한 조회 캐시만 비운다.

UI는 조회 버튼을 눌렀을 때만 수집한다. 선택/기간/자격 변경은 기존 표시를 무효화하고
CPI 원본/YoY 표시 및 기술지표 표시 변경은 재호출하지 않는다. 일별·월별과 단위가 다른
시리즈는 각각 별도 Plotly 차트로 표시한다. 최신 유효 관측값에는 그 값의 관측일을 붙이고
결측·누락 경고와 출처를 표시한다. Binance 오류가 FRED 조회를 막지 않는다.

## 오류, 보안 및 테스트

키 누락/형식/서버 인증 오류, 타임아웃, 네트워크, HTTP, 요청 한도, JSON/API 응답,
데이터 검증 오류를 구분한다. FRED가 인증 오류를 HTTP 400으로 반환하는 경우도 처리한다.
인증 또는 요청 한도 오류가 발생하면 같은 배치의 나머지 시리즈는 추가 요청 없이 보류한다.
응답 오류 원문이나 키가 포함될 수 있는 요청 예외 체인을 UI/로그로 전달하지 않으며,
urllib3 DEBUG 로그의 api_key 쿼리 값도 가린다. TLS 검증과 고정 공식 API 주소를 유지한다.

수집·서비스·UI 테스트는 명시적인 합성 응답으로 실행한다. 합성 입력은 테스트 전용이며
앱의 실제 데이터 실패 시 대체 데이터로 사용하지 않는다. 실 API 검증은 별도 선택 항목이다.

```powershell
# 사용자 키와 허용된 네트워크가 모두 있을 때만 직접 실행
$env:RUN_FRED_LIVE_TESTS='1'
.\.venv\Scripts\python.exe -m pytest -q tests/test_fred_live.py
Remove-Item Env:RUN_FRED_LIVE_TESTS
```

최초 개발 시점에는 사용자 FRED 키가 없어 모의 검증만 수행했다. 이후 2026-10-15 사용자
키로 5개 시리즈의 실제 응답을 확인하여 월별 경계 처리를 수정했다. 최근 1년 조회에서
월별 4개 시리즈는 시작 월 경계 1개를 제외한 11개 관측값을, DGS10은 260개 관측값을
반환했다. 고정 기간의 월별/일별 양 끝 경계, 서비스 표시 구간 및 CPI 전년동월비도
실 API 테스트 3개(시리즈별 하위 검증 15개)로 확인했다. 개수/최신일은 조회 시점에 따라
달라질 수 있으며 현재 개정값 스냅샷이라는 제약은 그대로다.
API 요청/응답 명세: https://fred.stlouisfed.org/docs/api/fred/series_observations.html
오류 명세: https://fred.stlouisfed.org/docs/api/fred/errors.html
