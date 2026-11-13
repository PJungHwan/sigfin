# STEP 07 — FRED 거시경제 데이터 수집

## 근거와 단계 경계

- 대응 커밋: `dd4eb6e` — `feat: integrate FRED macroeconomic data collection and dashboard`.
- Git 커밋 기록 시각: **2026-10-15 19:32:44 +09:00**. 실제 개발 시작/완료 시각은 **기록 확인 불가**.
- [FRED 기능/경계 오류 문서](../macro_data.md), collector/service/model 및 날짜 경계 테스트가 이 커밋에 함께 추가된 것을 확인했다.
- 월별 경계 수정의 독립 커밋은 없다. 하나의 단계 커밋에 개발/수정 결과가 포함돼 있으므로 내부 작업 시각을 세분해 추정하지 않는다.

## 개발 목표

공식 FRED API에서 과거 미국 거시 지표를 독립적으로 수집·검증·시각화하고, 관측일과 실제 발표일/빈티지를 구분하는 데이터 계약을 만든다.

## 실제 구현 기능

| 시리즈 | 원본 의미/단위 | 관측 주기 |
| --- | --- | --- |
| CPIAUCSL | 소비자물가지수, Index 1982–1984=100 | 월별 |
| FEDFUNDS | 실효 연방기금금리 월평균, % | 월별 |
| UNRATE | 실업률, % | 월별 |
| PAYEMS | 비농업 총고용, 천 명 | 월별 |
| DGS10 | 10년물 국채 수익률, % | 일별 |

- 프로젝트 `.env`/환경변수의 사용자 `FRED_API_KEY`를 읽고 누락·인증·네트워크·HTTP·응답·검증 오류를 구분한다. 키를 자동 발급하지 않는다.
- 공식 메타데이터와 단위·주기·계절 조정을 대조하고 관측 페이지를 수집한다. 시리즈당 최대 3요청/총 10,000관측, 프로세스 내 최소 1초 간격.
- 정렬/완전 중복 제거, 상충 중복 오류, 공식 `.` 결측을 NaN으로 보존한다. 임의 보간은 없다.
- 관측일·수집 시각·realtime 구간을 구분한다. 미확인 `release_date/available_at/vintage_date`는 비워 두고 `current_revised_snapshot`, `is_point_in_time=False`를 기록한다.
- CPI 전년동월비는 실제 같은 달 1년 전 값과 비교한다. 계산용 12개월을 확보한 후 표시 구간을 자른다.
- 시리즈/기간/자격 범위별 60분 캐시, 선택 캐시 새로고침, 수동 조회와 설정 변경 시 무효화.
- 기존 Macro 선택 UI를 확장하고 서로 다른 단위/주기를 별도 Plotly 차트에 표시한다.

이 단계는 거시 수집/조회다. 상관관계·투자 신호·LLM 해석이나 과거 백테스트 입력 연결은 하지 않는다.

## 사용 기술

Python, requests, python-dotenv, pandas, dataclass 메타데이터, Streamlit 캐시, Plotly, pytest/합성 HTTP 응답.

## 주요 관련 파일

| 파일 | 구현 근거 |
| --- | --- |
| [collectors/fred.py](../../collectors/fred.py) | `fetch_fred_series`, `normalize_observations`, 요청/보안/경계 검사 |
| [models/macro.py](../../models/macro.py) | 시리즈 명세·단위/주기·월별/일별 응답 범위 |
| [services/macro_service.py](../../services/macro_service.py) | CPI YoY·표시 범위·자격 분리 캐시 |
| [ui/macro_view.py](../../ui/macro_view.py), [ui/macro_charts.py](../../ui/macro_charts.py) | 수동 조회·최신 관측·차트 |
| [tests/test_fred.py](../../tests/test_fred.py), [tests/test_fred_boundaries.py](../../tests/test_fred_boundaries.py), [tests/test_fred_live.py](../../tests/test_fred_live.py) | 응답·월/일 경계·선택적 실제 API |
| [tests/test_macro_service.py](../../tests/test_macro_service.py), [tests/test_macro_ui.py](../../tests/test_macro_ui.py), [.env.example](../../.env.example) | 계산/캐시/UI와 키 이름 예시 |

## 확인 가능한 테스트 결과

- 기존 문서는 최초 개발 당시 키가 없어 모의 검증했고, **2026-10-15** 사용자 키로 5개 시리즈를 실제 조회해 경계 처리를 고쳤다고 기록한다.
- 기록된 실 API 결과는 테스트 **3개**, 시리즈별 하위 검증 **15개**다. 고정 기간 월별/일별 양 끝, 서비스 표시 구간, CPI YoY를 포함한다.
- 당시 전체 pytest 통과 수·완전한 원본 실행 로그는 **기록 확인 불가**.
- 모의 응답 테스트에는 키 누락/오류, 네트워크/응답 오류, 중복·결측·단위/주기·YoY·UI/캐시가 있다. 테스트 존재와 실행 결과를 구분한다.
- 이번 문서 정리에서는 `.env` 내용이나 키를 읽지 않았고 FRED/테스트를 실행하지 않았다.

## 오류 및 해결 기록

**문서에 실제 기록된 오류: FEDFUNDS `DATA_VALIDATION` — 요청 기간 밖 관측일.**

1. `2025-10-15~2026-10-15` 요청에서 첫 월별 관측 라벨이 `2025-10-01`로 반환됐다는 조사 기록이 있다. 타임존 이동이 아니라 월 관측 라벨과 요청 경계의 차이다.
2. 시작 월의 1일 라벨만 정상 경계 문맥으로 허용한 뒤 요청 시작일보다 이른 행은 최종 반환에서 제외한다. 관측일을 다른 날짜로 이동하지 않는다.
3. 이전 달·월초가 아닌 월별 날짜·종료일 이후 행은 계속 오류다. DGS10 일별 자료는 요청일 경계를 그대로 적용한다.
4. 경계 문맥만 있고 반환 구간에 값이 없으면 `EMPTY_RESPONSE`다. 검증 전체를 비활성화하지 않는다.
5. 서비스의 양 끝 표시 경계와 캐시/UI 결과 식별을 함께 적용했다. 회귀 테스트에 윤일·연말·UTC 수집 시각·엄격 ISO 날짜·CPI 워밍업 검증이 있다.

최초 오류 발생 시각과 수정 작업별 시각/원본 HTTP 전체 로그는 **기록 확인 불가**다. 인증 정보가 섞일 수 있는 원문을 복사하지 않는다.

## 확인이 필요한 사항과 한계

관측일은 발표일이 아니다. 현재 개정값을 과거 당시의 공개값으로 사용할 수 없으며 발표/빈티지 수집은 미구현이다. 원본 테스트 실행 산출물이 있으면 당시 전체 결과를 보완할 수 있다.
