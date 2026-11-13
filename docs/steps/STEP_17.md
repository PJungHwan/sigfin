# STEP 17 — 선택적 LLM 연동 및 통합 연구 리포트

## 개발 목표

기존 Technical/Macro·LangGraph·Mock Debate/Risk와 가상 원장/포트폴리오 위험·현재 입력의 백테스트를 출처가 연결된 한국어 연구 리포트로 조립한다. 실제 LLM 해석과 Python 계산을 구분한다.

## 구현한 기능

- 기본 Mock는 기존 실제 StateGraph에서 Technical/Macro 병렬 → Mock Debate/Evaluator → Mock Risk Manager → Aggregator/Formatter를 실행한다. 유료 SDK를 호출하지 않는다.
- 선택 `LLM Workflow`는 기존 STEP 11 Technical/Macro Responses API 경로만 사용한다. Mock Debate/Risk를 Live 그래프에 새로 연결하지 않는다. 동일 입력·기간·종목·봉·수집 시각의 기존 Mock 결과가 있으면 별도 실행 ID/모드로 첨부하고, 없으면 NOT_RUN/자료 부족을 표시한다.
- 기존 키/지원 모델/유효 가격/명시적 비용 승인·버튼·세션 5회/$0.25·출력 한도·병렬 전체 예약·중복 방지·자동 재시도 0회 정책과 `_ai_ledger`를 그대로 재사용한다. Macro/기술 자료 부족으로 로컬 결과만 만든 경우 `api_called=False`와 실제 LLM 해석 없음으로 표시한다.
- 현재 전략 JSON과 사용자 Risk Manager 설정도 기록한다. 기존 Risk Manager가 비활성 상태면 기본 설정을 명시해 사용하며, 이 정적 원금/레버리지 가정은 현재 가상 보유 원장과 구분한다.
- 역할별 실제 호출/모델/토큰/추정 비용/오류를 보존한다. 실패 과금은 미확인이다. FAILED/PARTIAL을 정상 전체 완료로 바꾸지 않는다.
- 원본 실행 ID·입력 해시/버전·시각·신호·요약·상충/위험/부족·근거 ID/값/단위/관측일·Macro 제약을 묶는다. 위험 연구 상태는 시장 방향과 분리하며 위험 보류/실패를 해제하지 않는다.
- 계좌/가격/가정/실제 평가 이력으로 STEP 16 계산을 다시 검산하고, 현재 백테스트가 있으면 같은 입력/전략/비용으로 기존 엔진을 재실행해 검산한 성과를 첨부한다. 현재 가상 원장을 과거 백테스트에 넣지 않는다.
- 입력 스냅샷에서 Technical/Macro 내용 검증, Mock 주장/반박/Evaluator 및 Risk·Aggregator 결과 대조를 수행한다. Pydantic 스키마 통과만으로 사실이라고 인정하지 않는다. 통합 결과 변조도 재조립 비교로 거부한다.
- 사용자 메모는 검증 근거·LLM 입력과 분리한다. 원장/메모는 OpenAI에 보내지 않는다. 입력/설정/전략/원장/가정/메모 변경 시 결과와 비용 승인을 무효화한다.
- 로컬 저장/과거 복원·Markdown/JSON 내보내기. 이력 조회/다운로드는 유료 요청·거래를 생성하지 않는다. 과거 결과는 현재 결과와 구분한다.

## 사용 기술·공식 근거

Python 3.11, 기존 OpenAI Python SDK/Pydantic/Responses API, LangGraph, pandas, Streamlit, SQLite. 새 패키지/모델·별도 SDK 클라이언트/비용 원장을 추가하지 않았다.

- 기존 모델 설정 `gpt-5.4-mini-2026-03-17` / `OPENAI_MODEL` 경로를 유지했다. 지원 목록·가격 유효기간 검사를 통과해야만 호출 가능하다. 계정 접근/결제/실제 모델 응답은 이번 작업에서 미검증이다.
- [공식 Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)에서 Python Pydantic `responses.parse` 경로를 확인했다.
- [공식 Responses API](https://developers.openai.com/api/reference/python/resources/responses/methods/create)의 출력 제한/사용량/저장 설정을 대조했다. 기존 SDK는 `store=False`, `max_output_tokens`, 제한된 timeout·재시도 0회 정책을 유지한다. SDK의 문법 검증과 프로젝트의 내용 검증은 별개다.

## 생성 및 수정 파일

신규: `models/research.py`, `services/research_service.py`, `ui/research_view.py`, `tests/test_research_report.py`, `tests/test_research_ui.py`, 이 문서.

수정: `services/research_snapshots.py`(허용된 Debate/Risk 상태·통합 첨부/버전), `services/local_store.py`(저장 내용 오류 분류), `ui/storage_view.py`(과거 통합 보고 표시), `app.py`, `config/settings.py`, `docs/BACKLOG.md`, `docs/ROADMAP_STEP_14_20.md`, `docs/steps/README.md`.

기존 `llm/`, `agents/`, `graph/`, `collectors/`, `indicators/`, `strategies/`, `backtest/`의 분석/거래 계산 코드는 변경하지 않았다.

## 테스트 결과

- 초기 통합 보고/화면 검사 **23 passed / 1 failed**. Macro fixture를 두 번 수집하면서 행별 수집 시각이 달라져 동일 입력 검사에 거부된 것이었다. 같은 시점의 fixture를 재사용하도록 테스트를 수정했다. 실제 입력 일치 검사는 완화하지 않았다.
- 저장/UI 선택 회귀 보완 후 **42 passed, 0 failed, 0 skipped, 6 subtests passed** (45.46초).
- 이후 중복 실행·Live 로컬 생략·민감 메모·실패 상태 검사를 추가했다. 최종 전체 시험 결과와 서버 검증은 아래 후속 기록을 따른다.
- 최종 STEP 17 신규 검사: **29 passed, 0 failed, 0 skipped, 6 subtests passed** (41.55초). 단위/모의 HTTP 22개, AppTest 7개다.
- 최종 전체 `pytest -q --tb=short --show-capture=no`: **551 passed, 0 failed, 14 skipped, 440 subtests passed** (192.02초). 총 565개 시험이며 건너뛴 14개는 기존 opt-in 외부 API 검사다. 실패/xfail/제외(deselected)는 0개다.
- 기존 STEP 1~13 시험 **471 passed / 14 skipped**를 유지했다. 신규 80개(14:16, 15:18, 16:17, 17:29)가 추가됐고 기존 테스트를 삭제/비활성화하지 않았다. STEP 5 Risk OFF 고정 회귀와 STEP 12/13 그래프 조합도 포함한다.
- 변경 Python 24개 문법 검사, 신규/연결 모듈 13개 import, `pip check`와 `git diff --check` 통과. 문서 로컬 링크와 기존 STEP 1~13 백로그 본문 보존을 확인했다.
- Streamlit **http://127.0.0.1:8507** 실행 및 `/_stcore/health` **200 / ok** 확인. AppTest로 실제 렌더링/버튼 동작을 검사했다. 실 브라우저 픽셀·실 데이터 조회 성공을 의미하지 않는다.
- 검증 환경: Python 3.11 가상환경, Streamlit 1.64.0 / pandas 3.0.6 / NumPy 2.4.6 / OpenAI SDK 2.54.0 / Pydantic 2.14.1 / LangGraph 1.2.15. 새 라이브러리 설치 없음.
- 실제 SDK의 Responses 파싱은 `httpx.MockTransport`와 합성 키/응답으로만 시험했다. 개발 작업의 실제 유료 OpenAI 호출은 **0회**다. 실 Binance/FRED 연결도 재시험하지 않았다.

## 오류와 해결 과정

동일 Mock/Live 첨부의 입력 범위를 과금 전에 검사하도록 보완했다. 민감 메모가 입력 해시에 들어가기 전 사용자 오류로 안내해 페이지 중단을 막았다. 선택 Live라도 실제 LLM 응답이 없는 로컬 생략/실패를 명시하고, 미실행·실패·건너뛰기를 출처 상태로 구분했다. 별도 Mock/LLM 의견 차이는 표결로 지우지 않고 상충 기록에 남긴다.

첫 전체 실행은 **548 passed / 1 failed / 14 skipped / 440 subtests passed**(184.19초)였다. 새 중복 실행 감사 테스트에서, 실제로 실패해 생성되지 않은 근거 풀/집계와 정상 재계산 결과를 비교하는 문제가 드러났다. 기록된 노드 실패가 확인되고 원래 정상 집계/리포트가 없는 경우에만 FAILED 감사 자료로 보존하도록 수정했다. 이후 UI 보완 검사에서 함수 내부 조건부 import로 발생한 `UnboundLocalError` 3개를 import 범위 정리로 수정했다. 최종 재검증 결과는 별도로 아래에 기록한다.

## 실행 방법

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py --server.port 8507
.\.venv\Scripts\python.exe -m pytest -q tests/test_research_report.py tests/test_research_ui.py
.\.venv\Scripts\python.exe -m pytest -q
```

화면 아래 ‘통합 연구 리포트’에서 기본 Mock 실행 → 자료/출처/위험/로그 확인 → 필요하면 로컬 저장/다운로드를 사용한다. 계좌/포트폴리오 위험/백테스트를 먼저 준비하면 계산 결과도 첨부한다. Live는 키/지원 가격/상한 및 화면의 별도 명시적 비용 승인이 필요하며 이번 개발 요청은 실행 비용 승인이 아니다.

## 제약사항 및 미검증 사항

실 계정 OpenAI 호출·실제 비용/응답 품질은 미검증이다. 실제 LLM Debate/Risk/추가 Aggregator LLM은 없다. FRED 발표 시각/당시 빈티지는 생성하지 않고 현재 개정 관측값을 과거 당시 매매 근거로 전달하지 않는다. 표본/첨부가 없으면 PARTIAL/N/A이며 인과관계·예측 승률·수익 보장을 생성하지 않는다. 스냅샷은 허용 컬럼/크기 8MB·각 프레임 5,300행 제한이다. 버전이 다른 미래 코드에서 과거 Mock 정책을 재현하는 마이그레이션은 별도 작업이며 검증 불일치 시 복원을 거부한다. 포트폴리오는 단일 사용자 로컬 가상 원장이고 SQLite 해시는 암호화/인증을 대신하지 않는다. 전체 UI/UX 리디자인, STEP 18~20 기능 개발 및 Git 커밋/Push는 수행하지 않았다.

## 후속 수정 — 저장 버튼 재실행 시 통합 리포트 무효화 (2026-11-06)

### 증상과 재현한 원인

사용자는 Mock 리포트가 생성되지만 ‘통합 연구 리포트 로컬 저장’을 누르면 입력 변경 안내와 함께 사라지고 분석 이력이 비어 있다고 보고했다. BEARISH/ABSTAIN은 분석 결과이며 저장 실패의 원인이 아니다.

`app.py`는 모든 위젯 재실행에서 `load_market_data()`를 호출한다. 60초 TTL이 만료되면 같은 확정 봉/가격을 다시 받더라도 `fetched_at` 수집 시각은 새로워진다. 기존 `ui.research_view`는 공용 `input_signature()`로 이 메타데이터까지 비교했다. 지문이 다르면 저장 버튼 분기에 도달하기 전에 `_research_snapshot`을 삭제하므로 `LocalStore.save_analysis()`가 실행되지 않았다.

동일 OHLCV/지표·원장/가정을 유지하고 시장 캐시를 비운 뒤 수집 시각만 갱신하는 AppTest로 이 경로를 재현했다. 수정 전 새 테스트는 **1 failed / 1 passed / 7 deselected**였고 실패 지점은 저장 클릭 후 스냅샷 소실이었다. 사용자 당시 세션의 TTL 경과/실제 수신 응답 원본은 남아 있지 않아 그 실행 순서까지 확인했다고 주장하지 않는다. 재현된 코드 결함과 SQLite 자체 쓰기 오류를 구분한다.

### 수정과 유지한 안전장치

- `ui/workflow_view.input_signature()`에 키워드 옵션 `include_collection_time`을 추가했다. 기본값은 `True`로 기존 STEP 10~13 Workflow의 수집 버전 구분을 유지한다.
- STEP 17만 `False`를 사용해 같은 관측의 DataFrame 속성 `fetched_at` 변경을 무효화 사유에서 제외한다. 수집 시각을 제거/갱신한 가짜 스냅샷을 만들지 않으며 실행 당시 입력/근거/수집 시각은 그대로 저장한다.
- 봉 시각·OHLCV·계산 지표·컬럼/인덱스·품질 메타데이터, 선택 Macro 값/구조·종목/봉/기간·모드·원장·가격/리스크 서명·전략·백테스트 설정·메모 비교는 유지한다. 실제 가격/봉·설정이나 보유 가격 유효성이 바뀌면 계속 무효화한다.
- 리포트에 실행 당시 스냅샷이라는 안내를 추가했다. 저장 후 현재 리포트와 Markdown/JSON 버튼을 유지하며 동일 ID 재저장은 중복을 만들지 않는다. 저장 실패도 현재 결과/다운로드를 보존해 재시도할 수 있다.
- 기존 SQLite 스키마/저장소·수집기·STEP 14~16 회계/가격 검증·백테스트·LLM 비용 정책은 변경하지 않았다.

수정 파일: `ui/research_view.py`, `ui/workflow_view.py`, `tests/test_research_ui.py`, 이 문서, `docs/CHANGELOG.md`, `docs/BACKLOG.md`.

### 검증 범위

- 수정 직후 재현/실제 가격 변경 대조: **2 passed / 0 failed / 7 deselected** (15.88초). 선택하지 않은 항목을 통과로 합산하지 않는다.
- STEP 14~17 저장/리포트/위험 UI 선택 회귀: **69 passed / 0 failed / 0 skipped / 15 subtests passed** (153.20초). 7개 새 회귀는 TTL 재수집 후 저장·실제 가격 변경·새 세션 복원·저장 실패/재시도·가격 만료·기존 Workflow 기본 계약·OHLCV/품질 변경을 검사한다.
- 변경 Python 3개 문법 검사 통과.
- 기존 8507 서버와 사용자 SQLite를 사용하지 않는 **별도 실제 Streamlit 서버**를 임시 포트에서 실행했다. 합성 fixture와 임시 SQLite만 사용하고, 매 재실행 수집 시각을 갱신해 캐시 재수집 상황을 재현했다. OpenAI SDK 생성은 실패하도록 차단했다.
- 실제 WebSocket 화면에서 리포트 실행→로컬 저장→일반 재실행까지 리포트/Markdown/JSON 버튼과 저장 이력이 유지됐다. HTTP로 Markdown/JSON 다운로드 본문과 동일 실행 ID를 확인했다.
- 연결을 끊고 새 세션을 만들어 과거 보고서를 조회했고, 해당 테스트 서버 프로세스를 종료한 뒤 같은 DB로 다시 시작해 동일 스냅샷 JSON을 복원했다. 각 단계 미처리 화면 예외 0. 보안 설정을 끄거나 실제 서버/사용자 원장을 재시작·변경하지 않았다.
- 격리 서버 검사 도구의 초기 실행에서는 XSRF 쿠키 취득 및 도구 스크립트 구문 오류로 검증이 시작되지 못했다. 검사 도구를 수정한 뒤 위 저장/재시작 검증을 완료했으며 앱 저장 로직의 추가 실패로 집계하지 않는다.
- 이 서버 검증은 실제 브라우저의 픽셀·파일 저장 대화상자 수동 검수나 실제 Binance/FRED/유료 LLM 검증이 아니다. SF-17-06~08의 남은 실환경/수동 검수는 백로그에 유지한다.

### 사용자 브라우저 재확인 절차

1. 기존 `http://localhost:8507`을 새로고침하고 Mock Workflow로 통합 리포트를 실행한다.
2. 같은 봉/값을 유지하는 상황에서 로컬 저장을 누르고 성공 안내, 계속 표시되는 리포트·Markdown/JSON 버튼, 아래 분석 실행 이력의 실행 ID를 확인한다. 그 사이 실제 봉/가격/설정이 바뀌면 무효화는 정상이다.
3. 다운로드한 두 파일의 실행 ID/모드/근거와 화면을 비교한다. 한 번 더 저장해도 같은 실행 이력이 중복되지 않아야 한다.
4. 브라우저 새로고침 후 아래 분석 이력에서 과거 결과를 조회한다. 현재 입력의 신규 분석으로 표시되거나 유료 호출이 발생해서는 안 된다.
5. 필요하면 현재 서버를 직접 종료하고 동일 프로젝트에서 `python -m streamlit run app.py --server.port 8507`로 재시작한 뒤 같은 과거 결과를 조회한다.
6. 메모·종목·가격 등 실제 입력을 변경했을 때 현재 결과가 무효화되는지 확인한다. 저장된 과거 보고서는 별도로 남아 있어야 한다.

### 최종 전체 회귀 결과

```powershell
.\.venv\Scripts\python.exe -m pytest -q --tb=short --show-capture=no
```

- **566 passed / 0 failed / 14 skipped / 451 subtests passed** (451.84초). deselected/xfail 0개. 기존 559개에 새 회귀 7개를 추가했고 테스트 삭제/비활성화는 없다. STEP 17 시험은 기존 29개에서 36개(서비스 22 / UI·입력 지문 14)로 늘었다.
- 기존 STEP 1~13의 471개와 STEP 14~17의 95개가 통과했다. 외부 API opt-in 14개는 미실행이며 통과로 합산하지 않는다. 실제 Binance/FRED 및 실계정 LLM 응답/청구 검증은 수행하지 않았다.
- 문서 로컬 링크·수정 Python 3개 문법·`git diff --check` 통과. CRLF 변환 안내는 있었으나 공백 오류는 없었다.
- 사용자 SQLite 해시는 수정 전과 동일했고, 변경 대상으로 정한 코드/테스트 3개·문서 3개 외의 기존 작업물은 보존했다. 격리 서버 도구는 `.pytest_cache/`의 테스트 산출물이며 사용자 계좌/분석 DB를 사용하지 않았다.
- 기존 `http://localhost:8507`의 health 200을 확인했다. 해당 서버 프로세스는 종료하지 않았으며, 이번 실제 저장/재시작 시나리오는 별도 임시 서버에서 검증했다.

실제 유료 API·실제 거래·키 수정·Git 커밋/Push는 수행하지 않았다. 사용자 브라우저의 최종 수동 확인은 위 절차로 진행한다.

## 화면·문서 마무리 — 원본 JSON 기본 노출 개선 (2026-11-06)

### 변경 내용

과거 통합 리포트를 복원하면 `ui/storage_view.py`가 원본 설정/노드 결과 JSON을 접기 메뉴 없이 출력해 기본 화면이 길어졌다. 해당 `st.json()`을 **‘원본 저장 데이터 보기 (개발자용)’**, `expanded=False`인 Streamlit 기본 메뉴 안으로 옮겼다. 기존 JSON의 필드/값은 그대로다.

분석 요약·근거·현재 리포트 저장/Markdown·JSON 다운로드·과거 결과 복원·전체 스냅샷 JSON 다운로드는 유지한다. 다운로드 버튼은 개발자용 메뉴 밖에 있고, SQLite 저장 형식/원본 스냅샷·회계/리스크 엔진은 변경하지 않는다. 접기 메뉴는 기본 클라이언트 동작을 사용하며 여닫기를 위한 서버 재실행/새 API 요청을 추가하지 않는다.

수정 파일은 `ui/storage_view.py`, `tests/test_storage_ui.py`, `tests/test_research_ui.py`, `docs/steps/STEP_14.md`~`STEP_17.md`, `docs/CHANGELOG.md`, `docs/BACKLOG.md`다. UI/UX 전면 리디자인·새 패키지 설치는 없다.

### 확인한 내용과 남은 검수

- 새 AppTest 2개에서 기존 Workflow/통합 리포트 각각의 기본 닫힘 설정, 메뉴 내부 JSON 배치, 기존 payload/복원 결과와의 일치, 메뉴 밖 요약·다운로드를 확인했다. 조회 전후 임시 DB 바이트도 동일했다.
- AppTest는 실제 브라우저에서 메뉴를 누르는 검사가 아니다. 접기/펼치기 클릭과 파일 저장 대화상자 수동 검수는 SF-17-08에 남긴다. 이 한계를 숨기기 위해 서버 재실행 방식으로 메뉴를 바꾸지 않았다.
- 백로그에서 완료 수정/검증의 설명은 CHANGELOG/이 기록으로 이동했다. 활성 목록은 STEP 16 검증 2개·STEP 17 검증 3개·STEP 18~20 개발 12개만 남기고, STEP 20 이후 리디자인 1개를 별도 유지한다. 실제 LLM과 실제 Binance/FRED 통합 검증은 완료로 바꾸지 않는다.
- 개발일지 14~17과 확정 로드맵을 대조했다. 과거 통과 수·오류 해결 기록을 이번 실행 결과로 덮어쓰지 않고 최신 공통 검증은 이 절에 추가한다.

### 브라우저 확인 방법

1. 기존 앱에서 과거 통합 리포트를 조회한다.
2. 한국어 요약·근거·스냅샷 다운로드는 보이고, ‘원본 저장 데이터 보기 (개발자용)’는 처음에 닫혀 있는지 확인한다.
3. 메뉴를 열면 기존 원본 JSON이 보이고 닫으면 가려지는지 확인한다. 원본 내용과 저장 이력은 그대로여야 한다.
4. 현재 리포트의 로컬 저장·Markdown/JSON 다운로드와 과거 결과 복원을 함께 확인한다. 유료 LLM은 실행하지 않는다.

### 이번 검증 결과

- JSON 메뉴 신규 AppTest: **2 passed / 0 failed / 19 deselected** (15.24초). 선택하지 않은 기존 시험을 통과로 합산하지 않는다.
- STEP 14~17 관련 8개 테스트 파일: **97 passed / 0 failed / 0 skipped / 34 subtests passed** (128.58초). 가상 매매/회계·저장/복원·위험 시나리오·리포트/입력 무효화·다운로드 회귀를 포함한다.

- 전체 `python -m pytest -q --tb=short --show-capture=no`: **568 passed / 0 failed / 14 skipped / 451 subtests passed** (325.59초). 기존 566개에 새 UI 회귀 2개를 추가했으며 삭제/비활성화한 시험은 없다. 전체 실행의 deselected/xfail 0개다.
- 기존 STEP 1~13의 471개와 STEP 14~17 관련 97개가 통과했다. 14개 skip은 기존 opt-in 외부 API 검사다. 실제 LLM·실 데이터 API 성공으로 합산하지 않는다.
- 변경 Python 3개 문법, 문서 로컬 링크/앵커, 활성 백로그의 중복 없는 미완료 항목 18개(단계 내 17 + 리디자인 1), `git diff --check`를 확인했다.
- 사용자 SQLite는 작업 전후 해시가 동일하다. 변경 대상 9개 파일 외의 기존 미커밋 작업물·로드맵·STEP 1~13 기록도 보존했다. 기존 8507 서버 health 200을 확인했으며 서버 프로세스는 종료하지 않았다.

실제 거래·유료 API 호출·키 수정·Git 커밋/Push는 수행하지 않았다. 이번 자동 검증은 브라우저의 여닫기 클릭·파일 저장 대화상자 수동 검수나 실 API 검증을 대체하지 않는다.

## 후속 수정 — 실제 8507의 구 화면 코드와 중복 원본 출력 정리 (2026-11-06)

### 실제 서버에서 확인한 원인과 코드 위치

사용자가 본 `record_id/settings/strategy_json/results` 구조는 `ui/storage_view.py:render_analysis_history()`의 과거 스냅샷 출력이었다. 이번 작업 전 디스크에는 펼침 메뉴가 있었지만, **실제 8507 서버의 새 화면 세션에서 과거 조회를 실행한 응답에는 해당 JSON의 상위 펼침 메뉴가 없었다.** 실행 경로도 현재 프로젝트 `app.py`임을 서버 메타데이터로 대조했다. 저장 데이터의 손상/변경이나 단순 JSON 트리의 펼침 깊이 문제가 아니라 실행 화면 코드가 디스크 변경을 반영하지 못한 상태였다.

소스 수정 후에도 같은 프로세스가 이전 형태(JSON 4개, 스냅샷 원본은 펼침 메뉴 밖)를 계속 반환하는 것을 다시 확인했다. 포트/경로를 검증한 8507 서버만 재시작하자 새 코드(과거 원본 1개, 지정 메뉴의 `expanded=False`)가 반환됐다. 앞선 AppTest/새 테스트 서버 검사로 실제 기존 서버 적용까지 확인했다고 볼 수 없었다는 점을 보완한다. 프로세스 메모리의 코드 객체 자체를 덤프한 것은 아니며, 디스크·실행 응답·재시작 전후의 차이로 이전 모듈 실행 상태를 확인했다.

별도로 `ui/research_view.py`에는 호출 정보·Debate/Risk 출처·계좌/위험/백테스트·로그를 JSON으로 출력하는 경로가 남아 있었다. 이 경로도 이번에 정리했다. 다른 단계의 수집 오류 진단·백테스트/단일 분석 개발 정보는 통합 스냅샷 출력과 구분했고 STEP 14~16 엔진은 변경하지 않았다.

### 수정 내용

- `render_research_raw_data()`를 만들어 STEP 17 원본 JSON 출력 경로를 한 곳으로 모았다. 현재/과거 화면마다 **‘원본 저장 데이터 보기 (개발자용)’** 메뉴 하나만 사용하고 바깥에는 원본을 출력하지 않는다. 메뉴와 내부 JSON 트리 모두 기본 닫힘이다.
- 과거 조회 버튼을 누르면 세션의 조회 세대 번호를 증가시킨다. 원본 메뉴 키에 현재/과거 범위·실행 ID·조회 세대를 넣어, 다른 결과 또는 같은 결과를 다시 조회할 때 이전에 열었던 상태를 이어받지 않도록 했다. 단순 여닫기는 기존처럼 클라이언트 동작이며 별도 API 호출을 추가하지 않는다.
- 일반 리포트의 원본 JSON 패널은 읽기 쉬운 실행/토큰/비용 표, 출처/위험 요약, 계좌 평가와 백테스트 검산 표, 공개 실행 로그로 바꿨다. 분석 의견·위험 상태·근거·부족·한계와 메모를 유지한다. 임의의 값/의견을 계산해 추가하지 않았다.
- 원본 메뉴는 저장 스냅샷 전체를 그대로 직렬화해 보여준다. SQLite payload·해시·스키마·Markdown/JSON 다운로드·검증/복원 경로는 변경하지 않았다. 기존 일부 필드만 보이던 원본 뷰를 전체 스냅샷과 일치시킨 것이며 저장 데이터를 편집한 것이 아니다.
- 변경: `ui/research_view.py`, `ui/storage_view.py`, `tests/test_research_ui.py`, `tests/test_storage_ui.py`, 이 문서, `docs/CHANGELOG.md`, `docs/BACKLOG.md`.

### 실제 Edge·Streamlit 검증

프로젝트 내부의 **별도 임시 Edge 프로필**을 사용해 실제 `http://127.0.0.1:8507`의 저장된 과거 통합 리포트를 조회했다. AppTest/합성 화면으로 대체하지 않았고, 저장·매매·유료 분석 실행 버튼은 누르지 않았다.

실제 DOM에서 다음을 확인했다.

1. 과거 조회 직후 지정 메뉴가 닫혀 있음.
2. 메뉴를 열고 닫을 수 있음. 닫기 애니메이션 완료 후 상태를 확인함.
3. 닫힌 상태에서 기술지표 표시 토글로 Streamlit을 재실행해도 자동으로 열리지 않음.
4. 메뉴를 열어 둔 채 같은 과거 결과를 재조회하면 새 메뉴가 닫혀 있음.
5. 과거 화면의 원본 JSON은 지정 메뉴 아래에 1개만 존재하고 한국어 분석 요약은 유지됨. 미처리 UI 예외 0.
6. 실제 서버 응답에서도 과거 원본의 상위 메뉴 이름/`expanded=False`와 중복 없음 확인. 데스크톱 크기 1600×1000의 화면도 캡처해 확인함.

초기 브라우저 검사 도구는 JSON 노드의 로딩 완료/닫기 애니메이션을 기다리지 않아 잘못된 0개 결과와 시간 초과/즉시 닫힘 단언 실패가 있었다. 그 값을 정상 검증으로 사용하지 않고 도구를 수정해 위 흐름을 끝까지 재검증했다. 실제 사용자의 기존 브라우저 세션 자체를 원격 조작한 것이 아니라 같은 실제 서버에 연결한 새 Edge 세션의 검증이다. 파일 저장 대화상자/승인된 Live 흐름의 최종 사용자 검수는 별도다.

### 실행 환경 복구와 테스트 결과

- 기존 PID 4524/8507의 종료는 일반 권한에서 접근 거부되어 승인된 실행 권한으로 해당 서버만 재시작했다. 제한 환경에서 시작한 중간 서버는 `NETWORK_PERMISSION / os_error=10013`을 반환했다. 기존처럼 허용된 실행 환경으로 다시 실행해 이 진단이 사라진 것을 확인했다. 거래소 접근 제한 우회·네트워크 보안 설정·API 키 변경은 하지 않았다.
- 초기 관련 UI 검사 **20 passed / 3 failed / 9 subtests passed**의 실패는 상태를 서버에 전송하지 않는 expander의 `proto.id`가 비어 있는데 이를 위젯 ID로 선택한 테스트 가정 때문이었다. 조회 세대/실제 컨테이너 키 계약과 패널 구조를 검사하도록 고쳤다.
- 최종 관련 UI: **23 passed / 0 failed / 0 skipped / 9 subtests passed** (63.91초). 기존 저장·중복/실패 재시도·다운로드·원장 보존과 단일 원본 위치/재조회 닫힘을 포함한다.
- 전체 `python -m pytest -q --tb=short --show-capture=no`: **570 passed / 0 failed / 14 skipped / 451 subtests passed** (366.61초). 기존 568개에 회귀 2개를 추가했으며 테스트 삭제/비활성화 없음. 기존 STEP 1~16도 포함한다. 외부 API opt-in 14개는 미실행이다.
- 실제 Edge 검증과 서버 출력 검증은 위 전체 자동 회귀와 별도로 수행했다. 실계정 LLM 호출·실제 거래·API 키 수정·Git 커밋/Push는 없다. 새 라이브러리를 설치하지 않았다.
- 최종 사용자 SQLite SHA-256은 작업 전과 동일하며 변경 대상 7개 파일 외의 기존 미커밋 작업물은 보존했다. 변경 Python 4개 문법·문서 링크·`git diff --check`를 확인했고, 현재 `http://localhost:8507` health 200이다.

### 사용자 재확인

기존 `http://localhost:8507`을 새로고침한 뒤 아래 ‘분석 실행 이력 · 로컬 저장’에서 과거 결과를 다시 조회한다. 일반 분석 내용 아래 원본 메뉴는 닫혀 있어야 한다. 원본이 필요할 때만 메뉴/JSON 트리를 열고, 다시 과거 조회를 누르면 닫히는지 확인한다. 현재 리포트와 과거 결과는 실행 ID/모드를 구분하며 다운로드/저장 데이터는 유지한다.

## STEP 18 착수 전 최종 상태 확인 (2026-11-06)

- Git의 `f28a8f3`(`feat: implement steps 14-17 portfolio and research platform`)에 STEP 14~17 구현·후속 오류 수정·테스트/문서가 포함돼 있음을 확인했다. STEP 16 가격 연결/렌더러, STEP 17 저장 무효화·JSON 표시 수정과 회귀 근거는 위 기록 및 해당 테스트에 이미 있어 중복 작성하지 않는다.
- **STEP 14~17 구현·수동 검증 완료는 사용자가 이번 점검 요청에서 확인했다.** 이 확인을 근거로 SF-16-07~08과 SF-17-08의 완료된 Mock 수동 범위를 활성 백로그에서 제외한다. 새 수동 관측값/원본 로그를 제공받거나 이번에 직접 재검사한 것은 아니다. SF-17-08에 섞여 있던 미검증 Live 범위는 SF-17-06으로 모았다.
- 최신 전체 결과는 기존 기록과 사용자 확인이 일치하는 **570 passed / 0 failed / 14 skipped / 451 subtests passed**다. 이번 문서 점검에서 pytest/서버/API를 재실행하지 않았고 과거 결과를 새 실행 결과로 표시하지 않는다.
- **실계정 유료 LLM 호출·응답 품질·실제 청구는 미검증**이다. 실제 Binance/FRED 자료를 함께 넣은 통합 리포트 대조도 별도 성공 기록이 없어 SF-17-07에 유지한다. 일반 기능의 수동 완료 확인을 이 외부 연동 성공으로 확대하지 않는다. 14개 opt-in 외부 API skip 역시 통과가 아니다.
- 확정 로드맵과 백로그의 STEP 18~20 작업 ID/범위가 일치한다. 기존 엔진·전략·스냅샷·보고 형식을 사용하는 **STEP 18 개발 착수는 가능**하며 유료 LLM 검증은 선행 필수 조건이 아니다. 이번에는 STEP 18 구현·코드/테스트 수정·DB/환경 파일 접근·Git 커밋/Push를 수행하지 않는다.
