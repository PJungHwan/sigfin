# STEP 15 — 로컬 저장 및 분석 실행 이력

## 개발 목표

STEP 14의 원장과 검증된 연구 입력/결과를 앱 재실행 후 복원한다. 시세 캐시·현재 세션·과거 저장 자료를 구분한다.

## 구현한 기능

- Python 표준 `sqlite3`로 프로젝트 `data/sigfin.sqlite3`에 계좌/이벤트/평가/분석을 저장한다. 외부 DB 서버나 추가 패키지가 필요 없고 트랜잭션으로 원장과 이벤트를 함께 반영할 수 있어 선택했다.
- 스키마 v1, 계좌 revision, 이벤트 ID, 낙관적 동시성 검사, BEGIN IMMEDIATE/rollback, 매개변수 SQL. 동일 내용 재저장은 멱등이며 과거 거래 재작성/경합은 거부한다.
- 가상 거래는 DB 저장 성공 후에만 세션에 반영한다. 재시작 시 최신 계좌를 자동 복원하며 다른 저장 계좌는 명시적으로 선택한다. 손상 DB는 자동 초기화하지 않는다.
- 평가 시점/원장 revision별 스냅샷을 저장한다. 이후 거래가 추가돼도 당시 원장 prefix로 재검산한다. 동일 평가 마감/revision은 최초 한 건만 보존한다.
- Workflow의 실제 입력 OHLCV/기술지표·선택 Macro·수집 메타데이터, 전략/백테스트 설정, 실행 시각/모드/노드 결과/정리된 보고서와 코드/계산 버전을 저장한다. SHA-256 입력/저장 해시를 검사한다.
- UTC 시간 정밀도와 부동소수점 값을 보존해 결정론적 Mock 분석을 재현한다. 과거 결과 조회/JSON 다운로드는 API를 호출하지 않는다. 조회한 과거 자료는 현재 분석과 분리한다.
- 계좌 JSON은 크기/중복 키/스키마/원장 검증 후 복원한다. 파일명/사용자 입력을 DB 경로로 사용하지 않는다.
- 허용 컬럼/설정/결과만 저장하고 키·인증 헤더·승인·비용 세션·예외 원문·비공개 추론은 저장하지 않는다. DB/저널 파일은 Git 제외한다.

## 사용 기술 및 파일

Python 3.11 sqlite3/json/hashlib, 기존 Pydantic/Streamlit/pandas/LangGraph.

신규: `services/local_store.py`, `services/research_snapshots.py`, `ui/storage_view.py`, `tests/test_local_store.py`, `tests/test_storage_ui.py`, 이 문서.

수정: `ui/portfolio_view.py`, `tests/test_portfolio_ui.py`, `app.py`, `config/settings.py`, `.gitignore`, `docs/BACKLOG.md`. 기존 백테스트/수집/전략/Agent 계산은 변경하지 않았다.

## 테스트 결과

- STEP 15 신규 단위 13개/UI 5개, STEP 14 및 기존 Workflow/Debate/Risk Manager/앱 선택 회귀: **145 passed, 0 failed, 0 skipped, 80 subtests passed** (39.24초).
- Macro 순서 해시 보완 후 저장 단위 재검증: **13 passed, 0 failed, 0 skipped** (9.14초).
- 모든 저장 시험은 임시 DB와 합성 시장/Macro 응답을 사용하며 유료 API는 호출하지 않는다. 이 단계의 전체 프로젝트 최종 회귀는 STEP 17 뒤 별도로 기록한다.

## 오류와 해결 과정

초기 선택 실행의 실패를 분석해 Structured Outputs의 엄격한 tuple 검증을 JSON 검증 경로로 수정했다. 재검증의 2개 실패는 UTC us/ns dtype 보존과 UI 저장 경로 조회를 수정해 해결했다. 저장 입력 재실행 비교의 실패 1개씩은 pandas JSON 기본 10자리 반올림 및 Macro 순서 변경이 원인이었다. 숫자의 정확한 JSON 왕복/`precise_float=True`와 원본 Macro 순서·해시를 보존해 해결했고 최종 실패는 0개다.

## 실행 방법

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_local_store.py tests/test_storage_ui.py
```

가상 계좌의 거래는 자동 로컬 저장한다. ‘로컬 계좌·평가 저장 이력’에서 평가 저장/계좌 복원을 사용하고, ‘분석 실행 이력’에서는 현재 Workflow를 실행한 후 명시적으로 스냅샷을 저장한다. 복원은 저장된 결과 조회이며 유료 모델을 재호출하지 않는다.

## 제약사항 및 미검증 사항

로컬 단일 사용자용이다. 현재 OneDrive 프로젝트 경로의 활성 DB는 앱 종료 후 백업하고 동기화 중 동시 실행은 피한다. SHA-256은 우발 손상 탐지이며 인증/암호화가 아니다. 임의 경로 자동 이동/외부 클라우드 저장은 없다. 저장 가능한 분석 결과는 Workflow 결과이며 기존 독립 백테스트 전체 거래표/단일 LLM 결과의 전용 저장 UI는 제공하지 않는다(전략·백테스트 설정은 스냅샷에 포함). 실제 LLM 출력 재호출의 동일성이나 실 API 연결을 검증하지 않았다.

후속 STEP 17에서는 통합 보고 첨부를 통해 동일 시장 입력의 백테스트 설정/검산 성과도 함께 보존한다. STEP 17 완료 후 전체 회귀는 **551 passed / 0 failed / 14 skipped / 440 subtests passed**이며 STEP 15 신규 시험은 중복 fixture 수집 없이 18개다.

## STEP 14~17 마무리 점검 (2026-11-06)

과거 Workflow/통합 리포트의 긴 원본 JSON 표시를 기본 닫힘인 ‘원본 저장 데이터 보기 (개발자용)’으로 이동했다. 분석 요약·과거 복원·스냅샷 다운로드는 메뉴 밖에 유지하고 DB 스키마/저장 payload는 변경하지 않았다. 조회 전후 임시 DB 바이트와 복원 내용의 일치를 새 UI 회귀로 확인했다.

위 수치는 STEP 15 당시 결과다. 이번 관련 회귀·전체 검사와 남은 수동/실 API 검수는 [STEP 17 마무리 기록](STEP_17.md#화면문서-마무리--원본-json-기본-노출-개선-2026-11-06)을 따른다.
