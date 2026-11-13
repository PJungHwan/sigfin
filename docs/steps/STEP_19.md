# STEP 19 — 시스템 통합 및 안정화

작업 기록일: **2026-11-11 (Asia/Seoul)**. 기준 커밋은 STEP 18의 `1e8f474`다. 기존 629 passed/0 failed/14 skipped와 사용자 BTC/USDT 수동 검증 기록을 확인한 뒤 확정 로드맵 **SF-19-01~04**를 진행했다. STEP 20과 전면 UI 리디자인은 구현하지 않는다. Git 커밋/Push는 수행하지 않는다.

## 범위와 기존 모듈 재사용

기존 수집기·지표·전략·백테스트·가상 원장·위험·LangGraph·LLM 비용/근거 검증·SQLite·통합 리포트·고정 실험을 재사용한다. 계산 엔진이나 그래프를 새로 만들지 않았다. 안정성 수정은 저장소와 기존 UI의 실패 처리에 한정하며 **STEP 18 계산 소스/입력 스키마/코드 해시와 기존 결과 해시 알고리즘은 변경하지 않았다.** 앱 버전/환경 설정도 유지했다.

| 작업 | 실제 수행 |
| --- | --- |
| SF-19-01 통합 | 합성 수집→기술지표→두 자산 가상 원장/평가→기존 위험/Mock Debate/Risk 그래프→통합 보고→SQLite 저장/새 연결/백업 복원→2전략×Risk×3구간 재실행/해시 대조 |
| SF-19-02 예외/안전 | 손상/다른 버전 DB·BLOB/깊은 JSON·실제 병렬 쓰기 경합·세션별 미저장 상태 분리·실 API/주문 미실행 가드·기존 인증/비용/부분 실패/입력 보안 회귀 |
| SF-19-03 성능/한도 | 캐시 재사용·표시 변경 미호출·한도/중복/기존 결과 무효화 회귀, 최대 5,000봉×6전략×3구간의 36회 실험 측정 및 결과 한도 실패 처리 |
| SF-19-04 로컬 운영 | 별도 Python 3.11 환경 설치·90개 동일 버전/import/pip check·새 환경 합성/UI 시험·실제 서버 health·포트 오류 구분·임시 DB 백업/복원·[실행/운영 문서](../local_operation.md) |

## 재현한 오류와 최소 수정

### 1. 미지원 SQLite에 테이블을 먼저 생성

기존 `LocalStore.__init__()`은 `CREATE TABLE IF NOT EXISTS` 후 버전을 검사했다. `metadata=2`와 사용자 테이블만 있는 임시 DB로 실행하면 오류는 발생하지만 새 SIGFIN 테이블이 생겨 파일이 바뀌었다.

**수정:** DDL보다 먼저 기존 metadata 버전을 검사하고, metadata 없이 다른 테이블이 있으면 SIGFIN 저장소로 열지 않는다. 미지원 파일을 변경하지 않는 바이트/테이블 대조 시험을 추가했다. v1 데이터 마이그레이션·원본 파일 초기화는 하지 않는다.

### 2. 손상된 저장 payload의 예외 누락

정상 해시를 가진 BLOB payload는 `.encode()`의 AttributeError, 깊은 JSON은 RecursionError가 발생해 기존 저장 오류 처리에서 빠졌다.

**수정:** 저장 payload의 문자열/8MB 검사와 기존 중복 키·NaN/Infinity 거부 파서를 적용하고, 구조 오류를 고정된 안전한 `StorageError`로 변환한다. 오류 원문/비공개 자료를 메시지에 넣지 않는다. 손상 파일은 그대로 보존한다.

### 3. 깊은 가상 원장 업로드가 화면 중단

2,000단계 중첩의 합성 JSON을 기존 UI로 불러오면 파서 RecursionError로 Streamlit 예외가 표시됐다.

**수정:** 기존 오류 처리에 RecursionError를 포함하고 uploader에도 1MB 상한을 지정했다. 기존 1,000,000바이트 본문 검사도 유지한다. 실패 후 세션 원장/DB의 바이트가 유지되는지 확인했다.

### 4. 큰 실험 결과의 해시/보고서 한도 오류

5,000봉·6전략·3구간, 총 36회는 계산 성공 후 합산 결과를 `safe_json`으로 해시할 때 8MB 한도 오류가 발생했다. 기존 화면은 닫힌 expander/다운로드 자료를 만들면서도 이 함수를 호출해 중단됐다.

**수정:** 기존 UI에서 이 실패를 **해시 N/A/비교 불가**로 처리한다. 성과표·곡선·원장과 유효한 입력 JSON 다운로드는 유지하고, 검증하지 못한 Markdown/기준 해시를 만들지 않는다. 기존 저장 기준이 있으면 재다운로드에서도 유지한다. 한도를 완화하거나 과거 해시 형식을 바꾸지 않았다. 일반 크기의 기존 JSON·기준 해시 대조도 회귀 시험에 포함한다.

## 통합·안전·성능 확인

- 동일 합성 BTC/ETH 입력의 지표·캐시 재조회는 수집 mock 호출 **2회**를 유지했다. 보고서 복원·백업·실험 재실행은 추가 시장/LLM API 호출을 만들지 않았다.
- 원장 저장 중복은 동일 revision으로 유지하고, Barrier를 이용한 두 실제 스레드의 경합에서는 1회 저장/1회 충돌로 원장 손실을 막았다.
- 실제 LangGraph의 Mock Technical/Macro·Bull/Bear·Risk 노드를 실행하고, 원장·평가·백테스트가 붙은 통합 보고서의 근거와 복원 결과를 대조했다. 발표일/빈티지 없는 Macro를 과거 매매 입력으로 바꾸지 않았다.
- 두 Streamlit 세션은 영구 저장 계좌만 복원하며 수량 선택·미저장 연구/실험 상태는 공유하지 않았다.
- 신규 통합 시험은 SDK/실 HTTP 호출을 금지하고, 런타임 AST에서 실제 거래 주문/출금/이체·`eval`/`exec` 호출 경로가 추가되지 않았는지 검사했다. 이는 침투 테스트나 전체 보안성 보장이 아니다. 기존 인증/Rate Limit/타임아웃·비용 승인/예산·키 비노출·부분 실패 시험은 전체 회귀에서 재사용했다.
- 최대 36회 계산의 독립 자원 측정은 **33.86초**였다. 하드웨어/캐시/동시 작업에 따라 달라지는 관측값이며 성능 보장값이나 테스트 임계값이 아니다. 이 크기에서는 결과 해시 한도가 초과돼 안내/부분 내보내기 경로를 별도 AppTest로 검증했다.

## 새 환경과 서버

기존 `.venv`를 변경하지 않고 `.pytest_cache/step19-clean-venv`를 `venv`로 생성했다. 공식 `https://pypi.org/simple`에서 기존 90개 배포판의 동일 버전을 설치했으며 새 런타임 라이브러리를 요구사항에 추가하지 않았다. 선택 설치용 [requirements-lock.txt](../../requirements-lock.txt)는 Windows/CPython 3.11 검증 버전을 기록한다.

- 기존/새 환경 `pip check`: **No broken requirements found**.
- 새 환경 Python **3.11.9**, system site-packages를 공유하지 않는 별도 venv, 90개 버전 일치, 핵심 모듈 import 통과.
- `requirements-lock.txt`의 `pip install --dry-run --no-index`로 기존 직접 요구사항도 만족하는지 확인했다.
- 최초 설치의 WinError 10013과 이후 PyPI 승인 재시도 성공을 구분했다. 키/OS 보안 설정을 변경하지 않았다.
- 포트 8519/8520 실행 거부와 8520 bind 10013을 기록하고, bind 가능한 **127.0.0.1:8531**에서 새 환경의 실제 `app.py` 서버 시작·health **HTTP 200 / ok**를 확인했다. 기존 서버를 종료하지 않았다.
- 사용자 DB 대신 임시 DB의 표준 SQLite `backup()`과 새 `LocalStore` 연결로 원장·분석 스냅샷 복원을 검증했다. 원본 DB에 백업/복원/마이그레이션을 실행하지 않았다.

## 테스트 결과

| 실행 | 결과 | 시간 |
| --- | --- | --- |
| 수정 전 기존 전체 | 629 passed / 0 failed / 14 skipped / 451 subtests passed | 302.43초 |
| 최초 신규 실패 재현 | 4 failed / 8 passed | 29.74초 |
| 수정 후 저장/신규 통합/UI 선택 | 26 passed / 0 failed / 0 skipped | 58.11초 |
| 최대 크기 UI 보완 포함 관련 선택 | 39 passed / 0 failed / 0 skipped | 98.00초 |
| 새 가상환경 통합·UI·기존 app/import | 21 passed / 0 failed / 0 skipped / 2 subtests passed | 117.91초 |
| **수정 후 전체** | **638 passed / 0 failed / 14 skipped / 451 subtests passed** | **474.42초** |

최초 신규 선택 실행에는 fixture용 TestCase를 직접 import해 기존 5개가 중복 수집됐다. 모듈 import로 바로잡아 최종 **신규 9개**만 추가됐으며 기존 시험을 삭제하거나 실패를 숨기지 않았다. 최초 4개 실패는 위 DB/JSON 문제를 실제로 재현했다. 별도 자원 측정에서 확인한 한도 오류는 이후 UI 회귀에 포함했다.

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest -q --tb=short -ra
.\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_system_integration.py tests/test_system_ui.py tests/test_experiment_ui.py tests/test_storage_ui.py tests/test_local_store.py -q --tb=short -ra
.\.pytest_cache\step19-clean-venv\Scripts\python.exe -X utf8 -m pytest tests/test_system_integration.py tests/test_system_ui.py tests/test_app.py tests/test_imports.py -q --tb=short -ra
```

14개 skip은 opt-in Binance/FRED/실 통합 API 시험이며 통과로 합산하지 않는다. 실제 유료 LLM은 이번에도 호출하지 않았다.

## 생성·수정 파일

| 구분 | 파일 |
| --- | --- |
| 생성 | [tests/test_system_integration.py](../../tests/test_system_integration.py), [tests/test_system_ui.py](../../tests/test_system_ui.py), [requirements-lock.txt](../../requirements-lock.txt), [로컬 운영 문서](../local_operation.md), 본 기록 |
| 수정 | [services/local_store.py](../../services/local_store.py), [ui/storage_view.py](../../ui/storage_view.py), [ui/experiment_view.py](../../ui/experiment_view.py) |
| 기록 갱신 | [목록](README.md), [CHANGELOG](../CHANGELOG.md), [BACKLOG](../BACKLOG.md), [로드맵](../ROADMAP_STEP_14_20.md), [STEP 18 후속 커밋 확인](STEP_18.md) |

실험 계산 파일/스키마/해시·백테스트·수집기·지표·전략·LLM/그래프 코드는 유지했다. 사용자 DB의 작업 전후 해시가 같고, STEP 18 계산 코드 지문도 `1e8f474`의 원본과 동일함을 확인했다. 사용자 데이터·`.env`·API 키 변경, 실제 주문, Git 커밋/Push는 없다.

## 실제 화면 확인과 한계

별도 8532 검증 서버/새 Edge 프로필에서 **실제 브라우저 자동화**를 완료했다. 원래 앱을 실행하되 합성 시세·실행별 임시 SQLite·Mock만 주입했고 외부 HTTP와 OpenAI SDK 호출을 차단했다. 기존 브라우저/사용자 DB를 조작하지 않았다.

- 화면에서 계좌 생성 → BTC/ETH 가격 조회 → 확인 체크 후 수동 가상 거래 1건 → 포트폴리오 위험 분석을 실행했다.
- Mock 통합 리포트 실행 → 로컬 저장 → 과거 조회를 확인했다.
- 전략 2개 × Risk OFF/ON × 3구간 **12회** 비교 → 브라우저 JSON 다운로드 → 실제 파일 입력 업로드 → 저장 입력 재실행 → **결과 해시 일치**를 확인했다. UI 예외는 0개였으며 실제 화면 캡처도 확인했다.
- 앞선 드라이버 3회는 접힌/지연 렌더링 버튼과 파일 입력의 DOM 노드 식별에서 중단됐다. 페이지 스크롤/펼치기·backendNodeId 사용과 실행별 새 임시 DB로 검증 도구를 고친 뒤 위 경로가 통과했다. 이를 앱 오류나 성공한 검증으로 숨겨 기록하지 않는다.
- 테스트용 8532 서버/Edge는 종료하고 일반 앱 서버 8531은 유지한다. 증빙/검증 도구와 별도 venv는 Git 제외 `.pytest_cache` 내부에 있다. 이 브라우저 결과는 실제 BTC/ETH 시장 자료의 사용자 수동 검수와 구분한다.

Codex가 앞서 확인한 일반 앱 서버는 **127.0.0.1:8531**이다. 이후 사용자는 **VS Code 터미널에서 포트 8532로 서버를 실행**하고 주요 기능의 정상 동작을 확인했다고 보고했다. 이 사용자 실행은 위의 종료된 8532 합성 검증 서버와 별개다. 이번 문서 정리에서 서버 접속/health를 다시 검사한 것은 아니다. 시장 API가 제한되면 오류 안내가 정상이며 수집 성공으로 주장하지 않는다. 계좌 거래/DB 복원 등 원본이 바뀌는 확인은 별도 복사본/시험 계좌에서 수행하고 데이터 손실 상황을 실제 사용자 원본에 주입하지 않는다.

STEP 20의 개별 수용 확인 대상은 실제 BTC/ETH 데이터의 새로고침/표시 토글과 오류 안내, 시험 계좌의 재시작 복원, 실제 조회 자료를 붙인 Mock 리포트 저장/과거 조회, STEP 18 JSON 재실행/해시 및 `STOP_LOSS` 표시다. 사용자의 **주요 기능 정상 동작** 확인을 이 모든 개별 항목의 STEP 19 수동 통과로 확대하지 않는다. STEP 18에서 이미 확인한 상세 결과는 그대로 보존하며, STEP 19 이후 항목별 확인 기록이 없는 부분을 자동/격리 검증과 구분해 최종 수용/시연에서 대조한다.

남는 한계는 실제 외부 API 14개와 실계정 유료 LLM 미검증, 8MB 결과 검증 한도, Windows/로컬 단일 사용자 범위, 타 OS/배포·다중 사용자 미검증이다. STEP 20 최종 수용/시연 및 전체 UI 리디자인은 별도다. 실제 투자 성과나 체결 가능성을 보장하지 않는다.

SF-19-01~04의 구현·자동/격리 화면 검증을 완료했다. 사용자 환경의 주요 기능 정상 동작 확인도 아래에 별도 반영하고, 항목별 상세 수용 검수는 STEP 20에서 이어간다. 실제 외부 API·유료 LLM 미검증 항목은 활성 백로그에 유지한다.

## STEP 20 착수 전 사용자 확인 — 2026-11-11

사용자의 이번 문서 정리 요청에서 **VS Code 터미널의 8532 서버 실행 및 주요 기능 정상 동작**을 확인했다. 이는 사용자 보고이며 Codex의 새 브라우저/자동 테스트 실행 결과가 아니다. 개별 기능 목록·조작 순서·사용 자료·실행 시각·수치/로그가 추가로 제공되지 않았으므로 각 기능이나 오류 사례를 별도 수동 통과로 기록하지 않는다.

기존 전체 **638 passed / 0 failed / 14 skipped**와 격리 Edge의 합성 자료/임시 DB 통합 검증은 위 기록을 유지한다. SQLite 버전 검사·손상 JSON·8MB 초과 처리는 자동/격리 검증 근거와 사용자 일반 확인을 구분한다. 실제 외부 API 시험 14개와 실계정 유료 LLM은 미검증이다.

[백로그](../BACKLOG.md)는 이미 완료 STEP 19가 제외된 상태여서 변경하지 않았다. STEP 20 미완료 4개·외부 연동 검증 2개·STEP 20 이후 UI 리디자인 1개를 유지한다. 로드맵의 STEP 19 완료/STEP 20 미착수 상태와 [단계 목록](README.md)의 개발일지 링크도 확인했다. 이번 작업은 필요한 문서만 갱신하며 코드·테스트·SQLite·`.env` 및 기존 미커밋 변경을 보존하고, 테스트/서버/API 재실행·STEP 20 개발·Git 커밋/Push는 수행하지 않았다.
