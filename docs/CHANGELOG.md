# SIGFIN Changelog

현재 소스·개발일지·로컬 Git 기록에서 확인한 구현과 수정을 STEP별로 요약한다. 정리일은 **2026-11-06 (Asia/Seoul)**이며 실제 개발 완료일을 뜻하지 않는다. 남은 작업은 [BACKLOG](BACKLOG.md), 상세 과정은 [개발일지](steps/README.md), 확정 계획은 [로드맵](ROADMAP_STEP_14_20.md)을 따른다.

STEP 14~17 기능 기준은 `f28a8f3`(`feat: implement steps 14-17 portfolio and research platform`), 문서 기준은 `610d44b`, STEP 18 기능/문서는 `1e8f474`다. 사용자께서 STEP 14~18의 수동 검증 완료를 확인했으며, 실제 유료 LLM 호출은 미검증이다. 아래 STEP 19는 2026-11-11의 신규 미커밋 변경이다. 구현 기록을 외부 API·실계정 호출·투자 성능 검증으로 확대하지 않는다. 확인되지 않은 과거 작업일·오류·시험 결과는 **기록 확인 불가**다.

## STEP 01 — 대시보드 기반

- Streamlit 화면, 종목/기간/봉·기술/거시 선택 사이드바, 차트·시장 지표·Agent 카드와 공통 CSS 구성.
- 근거: [개발일지](steps/STEP_01.md), [앱](../app.py), [스타일](../ui/styles.py), `9fc1555`. 당시 테스트 실행 결과는 기록 확인 불가.

## STEP 02 — Binance 시장 데이터

- ccxt 공개 OHLCV 수집·페이지네이션/한도·UTC/확정 봉 검증·캐시/새로고침과 Plotly 캔들/거래량·가격 요약 연결.
- 근거: [개발일지](steps/STEP_02.md), [수집기](../collectors/binance.py), [시장 서비스](../services/market_data.py), `0d1f7c4`. 과거 `API_TIMEOUT_MS` import 오류의 독립된 수정 경위는 기록 확인 불가.

## STEP 03 — 기술적 지표

- Wilder RSI, MACD, EMA/SMA, Bollinger Bands, 거래량 평균과 워밍업·NaN 처리, 차트/카드 ON/OFF 연결.
- 근거: [개발일지](steps/STEP_03.md), [지표 엔진](../indicators/technical.py), [차트](../ui/charts.py), `0675bec`.

## STEP 04 — 전략 빌더

- 타입/단위 검증을 갖춘 LONG/SHORT 조건, AND/OR·비교·교차 평가, 충돌 표시·신호 미리보기와 JSON 저장/복원 구현.
- 근거: [개발일지](steps/STEP_04.md), [모델](../strategies/models.py), [평가](../strategies/evaluator.py), [저장](../strategies/storage.py), `8ba8f18`.

## STEP 05 — 기초 백테스트

- 다음 연속 봉 시가 체결, 가상 단일 LONG/SHORT, 수수료/슬리피지·거래량 한도·현금 회계와 실현/미실현 손익 구현.
- Equity·수익률·승률·MDD·거래 로그와 설정 변경 시 결과 무효화 연결. 근거: [개발일지](steps/STEP_05.md), [엔진](../backtest/engine.py), [성과](../backtest/metrics.py), `fe05300`.

## STEP 06 — 리스크 백테스트·연결 진단

- 손절/익절·직전 봉 극값 기반 트레일링, 갭/동시 접촉 우선순위·유동성 부족 재시도·청산 사유와 Risk OFF/ON 비교 구현.
- Binance 오류 분류/진단을 추가하고 네트워크 권한 문제와 코드 실패를 구분한 기록을 남김. 근거: [개발일지](steps/STEP_06.md), [리스크 규칙](../backtest/risk.py), [연결 진단 기록](binance_connection.md), `0ce580e`.

## STEP 07 — FRED 거시경제 데이터

- CPIAUCSL/FEDFUNDS/UNRATE/PAYEMS/DGS10 수집, 단위·관측 주기·시각/결측 검증, CPI YoY·캐시·거시 차트 구현.
- FEDFUNDS의 시작 월 관측 라벨 경계 오류를 제한적으로 처리하고 최종 요청 구간 밖 행은 제외함. 일별 경계·잘못된 날짜 검증은 유지. 근거: [개발일지](steps/STEP_07.md), [수집기](../collectors/fred.py), [경계 회귀](../tests/test_fred_boundaries.py), `dd4eb6e`.

## STEP 08 — Crypto × Macro 탐색 분석

- 월말 확정 가격/월간 수익률·지표 변화량 정렬, Pearson/Spearman·0~3개월 시차·품질/표본 표시와 차트 구현.
- 발표 시각/빈티지 없는 시점 준수 분석을 차단. 근거: [개발일지](steps/STEP_08.md), [정렬](../analytics/alignment.py), [상관](../analytics/correlation.py), [시차](../analytics/lag_analysis.py), `664274c`.

## STEP 09 — 단일 LLM 분석 기반

- OpenAI SDK/Responses·Pydantic 출력, 입력 요약/근거 검증, Mock·버튼/비용 승인·중복/예산/오류 처리와 한국어 분석 UI 구현.
- 근거: [개발일지](steps/STEP_09.md), [클라이언트](../llm/client.py), [검증](../llm/validators.py), [비용 원장](../llm/budget.py), `379bd1d`. 모의 응답 시험을 실계정 성공으로 해석하지 않는다.

## STEP 10 — LangGraph Mock 워크플로우

- 실제 StateGraph의 입력 검증→Technical/Macro 병렬→Aggregator→Formatter, 공유 상태·reducer 로그·노드 상태/실행 시간 UI 구현.
- 근거: [개발일지](steps/STEP_10.md), [워크플로우](../graph/workflow.py), [상태](../graph/state.py), `db0d5b8`. 분석 노드는 규칙 기반 Mock이다.

## STEP 11 — 역할별 LLM 선택 경로

- Technical/Macro 전용 프롬프트·스키마/내용 검증과 Mock/LLM 주입, 동시성 비용 예약·자료 부족 호출 생략·PARTIAL/FAILED 표시 구현.
- 근거: [개발일지](steps/STEP_11.md), [어댑터](../agents/llm_agents.py), [출력 계약](../llm/agent_contracts.py), `23533c5`. 실계정 유료 호출 성공은 미검증이다.

## STEP 12 — Bull/Bear Mock Debate

- 실제 LangGraph에서 근거 준비·Bull/Bear 병렬 주장·상대 주장 참조 반박·1~2라운드·Evaluator·집계를 실행하고 타임라인/로그 표시.
- 근거: [개발일지](steps/STEP_12.md), [Debate 그래프](../graph/debate_workflow.py), [검증](../graph/debate_validation.py), `e07272e`. 규칙 기반 토론이며 실제 유료 LLM Debate가 아니다.

## STEP 13 — Mock Risk Manager

- 변동성·종가 MDD·Wilder ATR14·품질 기반 연구용 등급, 왕복 비용/손실 한도 기반 가상 포지션과 REVIEW/HOLD/ABSTAIN 구현.
- Debate/Risk ON/OFF 네 조합, 근거 재계산·실패 보류·카드/로그 연결. 근거: [개발일지](steps/STEP_13.md), [정량 평가](../risk/evaluator.py), [포지션 계산](../risk/position.py), [노드](../graph/risk_nodes.py), `e4047e1`. 실제 LLM Risk Manager가 아니다.

## STEP 14 — 가상 포트폴리오

- 기본 10,000 USDT, BTC/ETH 수동 매수/매도·수수료/슬리피지·가중평균 매입가·실현/미실현 손익·총자산과 중복/잔고/가격 검증 구현.
- 확정 가격의 공통 마감 평가와 수동 확인 UI 제공. 실제 주문·레버리지·공매도 없음.
- 근거: [개발일지](steps/STEP_14.md), [회계 엔진](../portfolio/engine.py), [가격 서비스](../services/portfolio_service.py), [UI](../ui/portfolio_view.py). 기존 구현 항목 `SF-14-01~05`는 활성 백로그에서 제외.

## STEP 15 — 로컬 저장·실행 이력

- SQLite 트랜잭션·revision/중복 검사로 원장/거래·평가/연구 스냅샷 저장·재시작 복원·이력 조회/JSON 제공. 키/비용 승인 원장은 저장하지 않음.
- JSON 숫자 정밀도·UTC us/ns dtype·Macro 순서/해시 보존과 저장 오류/검증 경로 보완.
- 근거: [개발일지](steps/STEP_15.md), [저장소](../services/local_store.py), [스냅샷](../services/research_snapshots.py), [이력 UI](../ui/storage_view.py). 기존 구현 항목 `SF-15-01~04`는 활성 백로그에서 제외.

## STEP 16 — 포트폴리오 위험·가격 전달 수정

- 보유량 기준 비중/집중도·하락/매도 비용/손실 한도, 실제 저장 평가 이력의 MDD/변동성 및 기존 Mock Risk StateGraph 연계 구현.
- STEP 14/16에 공통 가격 문맥·검증 시각 전달, 가격 유효성 회복/만료 시 캐시 무효화와 입력 진단 추가. 키워드 전용 렌더러 호출·독립 가격/원장 재검증으로 호출 계약과 안전장치 보완.
- 실제 8507 서버 세션의 가격 조회→위험 분석에서 미처리 예외 0, 수수료/슬리피지 0의 -10% 손실 검산 기록이 있음. 사용자 최초 세션의 모든 원인을 소급 확정한 것은 아니다. 후속 STEP 14~17 수동 검증 완료는 사용자가 확인했다.
- 근거: [개발일지·후속 수정](steps/STEP_16.md), [위험 계산](../risk/portfolio.py), [서비스](../services/portfolio_risk_service.py), [렌더러](../ui/portfolio_risk_view.py), [회귀](../tests/test_portfolio_risk_ui.py). `SF-16-01~06` 구현·수정과 사용자 확인된 `SF-16-07~08` 검증은 완료 기록으로 보존한다.

## STEP 17 — 통합 연구 리포트

- 기존 LangGraph Technical/Macro·Mock Debate/Risk, 계좌/위험·현재 입력 백테스트 검산을 출처/시각/근거가 연결된 한국어 보고서로 조립.
- 선택 LLM은 기존 Technical/Macro 경로·공유 비용 원장/승인/상한을 재사용. 동일 입력의 Mock 첨부는 별도 실행으로 표시하며, 근거 재검증·실패/부족·호출 사실·메모 분리·저장/복원/Markdown·JSON 제공.
- 동일 입력 fixture, 실패 감사 자료 보존과 조건부 import 범위를 수정하고 Mock/모의 HTTP/AppTest로 검사. 후속 수동 검증은 사용자 완료 확인을 반영하되, 실 OpenAI 호출·청구/응답 품질과 실제 Binance/FRED 통합 자료 대조는 별도 미검증으로 남긴다.
- 근거: [개발일지](steps/STEP_17.md), [모델](../models/research.py), [서비스](../services/research_service.py), [UI](../ui/research_view.py). `SF-17-01~05`와 사용자 확인된 `SF-17-08`의 Mock 수동 검수는 완료 기록으로 보존한다. 미검증 Live 검수는 `SF-17-06`에 통합하며 `SF-17-07` 외부 자료 대조도 유지한다.
- 2026-11-06 수정: 시장 캐시 재조회로 동일 봉/값의 수집 시각만 바뀔 때 저장 버튼 처리 전에 리포트가 삭제되던 입력 지문 비교를 수정했다. 기존 Workflow의 기본 비교·실제 입력 변경 무효화는 유지하며 실행 당시 스냅샷을 저장한다.
- SQLite 저장·중복/실패 재시도·리포트/다운로드 유지·새 세션 복원 회귀를 추가했다. 합성 자료의 별도 실제 Streamlit 서버에서 Markdown/JSON HTTP 다운로드와 서버 프로세스 재시작 후 복원도 검증했다. 실제 API/사용자 브라우저 수동 검수는 별도이며 [STEP 17 후속 기록](steps/STEP_17.md)에 범위를 명시한다.
- 2026-11-06 화면 마무리: 과거 결과의 긴 원본 JSON을 ‘원본 저장 데이터 보기 (개발자용)’ 메뉴로 이동하고 기본 닫힘으로 설정했다. 원본 JSON/DB와 기존 요약·근거·저장/다운로드·복원은 유지한다. Workflow/통합 리포트의 기본 닫힘·JSON 배치/내용·DB 불변성과 메뉴 밖 다운로드를 새 AppTest 2개로 검증했다. 이 시점에 남았던 실제 브라우저 여닫기는 아래 후속 검증으로 보완했다.
- STEP 14~17 개발일지의 과거 기록은 보존하고 최신 공통 검사 근거를 연결했다. BACKLOG는 완료 기록을 제거한 미완료 체크리스트 17개와 별도 리디자인 1개로 간소화했다. 실 LLM 검증 등은 계속 미완료다([마무리 기록](steps/STEP_17.md#화면문서-마무리--원본-json-기본-노출-개선-2026-11-06)).
- 2026-11-06 후속 수정: 실제 기존 8507 서버가 디스크와 다른 구 화면 코드를 반환해 과거 원본 JSON이 메뉴 밖에 있었음을 확인하고 해당 서버를 재시작해 적용했다. 통합 리포트의 다른 원본 출력도 공통 개발자용 메뉴 하나로 모으고 메뉴/JSON 트리 기본 닫힘·과거 재조회 시 닫힘 초기화를 추가했다. 일반 화면은 요약·근거·읽기 쉬운 계산/실행 표로 유지하며 저장 데이터/다운로드는 변경하지 않았다.
- 실제 Edge의 기존 8507 과거 화면에서 기본 닫힘→열기→닫기→일반 재실행→재조회 닫힘, 원본 1곳·분석 요약 유지·예외 0을 확인했다. 자동 UI 23 passed, 전체 570 passed / 실패 0 / skip 14 / 451 subtests passed. 제한 환경의 중간 서버 소켓 오류 10013도 원래 허용된 실행 환경으로 복구했으며 보안 설정/키/유료 LLM은 변경·호출하지 않았다([STEP 17 후속 기록](steps/STEP_17.md)).

## STEP 18 — 백테스팅 전략 비교 (2026-11-11)

- 전략 2~6개 × Risk OFF/ON을 동일 OHLCV·초기금·비용·거래량 참여율로 독립 실행하고 성과 표·자산 곡선·ON−OFF 차이·조합별 원장을 제공한다. 기존 STEP 5~6 엔진/리스크 계산은 수정하지 않았다.
- 고정 원본 OHLCV·전략·설정·기준 UTC·계산 버전/소스 해시를 JSON으로 보존하고, 추가 API/DB 조회 없이 업로드 재실행·결과 지문 비교 및 Markdown 내보내기를 지원한다.
- 시간순 검토/검증을 각각 동일 초기금/FLAT으로 시작한다. 앞 구간은 지표 워밍업에만 사용하며 외부 지표 재계산·구간 첫 Cross/마지막 신호·next-open·미래 가격 변경 불변성을 검증했다. 구간 이전 외부 워밍업은 추가 수집하지 않아 초기 NaN을 유지한다.
- 무거래 N/A, 부족 표본, 부분/전체 실패, 같은 조건 경고와 입력 변경 무효화를 구분한다. 앱 버전은 0.18.0이며 새 라이브러리 설치는 없다.
- 신규 **38 passed / 0 failed / 0 skipped**, 기존 **570 passed / 0 failed / 14 skipped**, 전체 **608 passed / 0 failed / 14 skipped / 451 subtests passed** (549.51초). Streamlit 8518 서버 시작·health 200과 AppTest 화면/버튼을 확인했다. 실제 브라우저·실 API 검수는 별도다.
- SF-18-01~04는 완료하여 활성 백로그에서 제거했다. 사용자 SQLite/키·기존 저장 결과는 보존하고 유료 API·실거래·Git 커밋/Push는 수행하지 않았다. 근거: [STEP 18](steps/STEP_18.md), [실험 모듈](../backtest/experiments.py), [화면](../ui/experiment_view.py), [단위 테스트](../tests/test_experiments.py), [UI 테스트](../tests/test_experiment_ui.py).
- 결과 해시 후속 보완: 초기에는 UI/Markdown에 계산값만 표시하고 단위 테스트에서만 비교하던 부분을 수정했다. 새 JSON에 기준 결과 해시를 저장하고 재실행 시 **일치/불일치/비교 불가**와 기준/재실행 해시를 표시한다. 기존 JSON은 같은 입력의 세션 원본이 있을 때 비교하고 없으면 비교 불가다. 기존 계산 소스/입력 해시를 유지하며, 재다운로드 기준 보존·업로드 변경 무효화를 추가했다. 관련 회귀 **130 passed / 0 failed / 0 skipped / 27 subtests passed** (55.72초), 전체 테스트 재실행/실 브라우저 새 상태 검수는 이번에 수행하지 않았다. [계약/확인 방법](steps/STEP_18.md#결과-해시-자동-비교-보완--2026-11-11), [검증 모듈](../backtest/experiment_verification.py), [검증 테스트](../tests/test_experiment_verification.py).
- Risk ON/OFF 최종 검증: 위쪽 **백테스팅 → Risk Management · 손절·익절·트레일링**의 기존 설정이 STEP 18로 전달됨을 확인했다. 모두 false이면 차이 0은 정상이다. 앱/UI/엔진 수정 없이 신규 손절 1% 테스트 3개를 추가해 12조합의 `STOP_LOSS` 원장, next-open 진입, 봉 내부 체결/비용·회계, 미래 데이터 미참조, 활성화/무효화/재비활성화와 JSON/결과 해시를 확인했다. 합성 검토/검증은 OFF 1,040 / ON 990, 전체는 OFF 1,040 / ON 980.1 USDT다. 관련 **133 passed / 0 failed / 0 skipped / 27 subtests passed** (58.96초), 전체 **629 passed / 0 failed / 14 skipped / 451 subtests passed** (267.90초). 사용자 SQLite·설정·기존 미커밋 변경은 보존했다. [기존 UI 위치/재현 절차/한계](steps/STEP_18.md#risk-onoff-최종-검증--2026-11-11).
- STEP 19 착수 전 사용자 수동 검증 완료: 실제 BTC/USDT의 2전략·3구간·Risk OFF/ON 총 12회, 성과표/자산 곡선, JSON 저장 입력 재실행·SHA-256 일치, 손절 1% 활성화·ON/OFF 성과/거래 차이·`STOP_LOSS` 원장을 확인했다. 최신 자동 결과는 629/0/14이며 외부 API 통합 자동 시험 14개·유료 LLM 검증은 미실행으로 유지한다. 이번 정리는 문서만 갱신했고 STEP 19 개발·테스트/API 재실행·Git 커밋/Push는 하지 않았다. [사용자 확인 범위](steps/STEP_18.md#step-19-착수-전-최종-확인--2026-11-11).

## STEP 19 — 시스템 통합 및 안정화 (2026-11-11)

- 기존 수집/지표·원장/포트폴리오 위험·Mock LangGraph Debate/Risk·통합 보고·SQLite 백업/복원·고정 실험 재실행을 하나의 합성 통합 시험으로 연결했다. 실제 병렬 원장 경합·세션 분리·API/주문 미실행 검증을 추가했다.
- 미지원/다른 SQLite에 DDL 전에 오류를 반환하고 원본을 보존하도록 수정했다. BLOB·깊은 저장 JSON은 안전한 StorageError로 처리하고, 깊은 원장 업로드의 RecursionError와 uploader 크기 제한을 보완했다.
- 36회 최대 실험에서 합산 결과가 8MB를 넘을 때 화면이 중단되던 경로를 수정했다. 표/곡선/원장·입력 다운로드는 유지하고 해시/Markdown은 N/A/비교 불가로 안내한다. 기존 계산 소스·입력/결과 해시와 저장 기준을 유지한다.
- 별도 Windows/Python 3.11 venv에 공식 PyPI의 동일 90개 버전을 설치해 import·pip check·선택 시험·서버 health를 확인했다. 기존 `.venv`는 유지하며 [검증 버전](../requirements-lock.txt)과 [로컬 실행/포트/DB 운영 안내](local_operation.md)를 추가했다.
- 기준 전체 629/0/14에서 수정 후 **638 passed / 0 failed / 14 skipped / 451 subtests passed** (474.42초). 관련 선택 39 passed, 새 환경 선택 21 passed/2 subtests passed. 초기 실패 재현과 실제 브라우저/외부 미검증 범위는 [STEP 19](steps/STEP_19.md)에 구분한다. 사용자 DB/키 보존, 유료 LLM·실제 주문·Git 커밋/Push 없음. STEP 20은 미착수다.
- 격리 Edge의 합성 시세/임시 DB에서 계좌·수동 거래·위험 분석·Mock 리포트 저장/과거 조회·12회 실험 JSON 다운로드/업로드/해시 일치를 실제 화면으로 확인했다. SF-19-01~04를 활성 백로그에서 제거했다. 실제 시장/유료 LLM 검수와 STEP 20 최종 수용은 별도로 유지한다.
- STEP 20 착수 전 문서 확인: 사용자가 VS Code 터미널의 **8532 서버 실행·주요 기능 정상 동작**을 보고했다. 기존 638/0/14와 격리 Edge 합성 검증을 보존하고, 상세 기록이 없는 개별 기능/오류 사례의 수동 통과로 확대하지 않았다. 올바른 백로그·로드맵·단계 목록은 유지했으며 새 테스트/서버/API 실행·STEP 20 개발·Git 커밋/Push는 없다. [사용자 확인 범위](steps/STEP_19.md#step-20-착수-전-사용자-확인--2026-11-11).

## STEP 20 — 최종 검증 및 졸업 시연 준비 (2026-11-12)

- STEP 19 커밋 `7060ebd`와 실제 소스/문서/잠금 환경을 대조하고 [최종 요구 추적표](final_acceptance.md)를 작성했다. 기존 계산 엔진·그래프·일반 앱 코드를 변경하지 않고 STEP 18 계산 소스/입력·결과 SHA-256 규약을 유지했다.
- 없던 [메인 README](../README.md)에 설치·화면/사용·모드/단위·SQLite/해시·제한을 정리했다. 이 PC에서 동작하지 않는 `py -3.11` 대신 확인된 `python` 3.11.9 명령을 안내하며 기존 키/환경은 바꾸지 않았다.
- [10~15분 시연 계획](demo_scenario.md)과 별도 `demo/offline.py`를 추가했다. 기존 전체 화면을 합성 360봉/2봉/연결 실패·키 없는 Mock·세션별 시험 DB로 실행하며 HTTP/유료 LLM을 차단하고 세션 주입은 직렬화한다. 일반 앱 수집 실패의 가짜 대체를 추가한 것은 아니다.
- 신규 시연 시험 8개와 실제 격리 Edge에서 가상 매수/위험·보고서 저장/과거 조회·12회 실험 JSON 다운로드/업로드/재실행·결과 해시 일치를 확인했다. 초기 신규 코드/시험·브라우저 검사 실패와 최종 전체 결과는 [STEP 20](steps/STEP_20.md)에 구분한다. 새 라이브러리·유료/실 API 호출·사용자 DB 수정·Git 커밋/Push는 없다.
- 최종 전체 **646 passed / 0 failed / 14 skipped / 451 subtests passed** (672.50초), 별도 설치 환경의 시연/import **9 passed** (41.71초). 일반/합성 서버 8532/8533 health 200, 원본 DB/기존 코드 보존·문서 링크/키 패턴 검사 통과. 실 API 14개는 미실행으로 유지한다.
- SF-20-01~03 완료 항목을 활성 백로그에서 제거했다. SF-20-04의 사용자 발표 장비 최종 리허설/수용, SF-17-06/07 외부 검수와 별도 전면 리디자인은 유지한다. 자동/합성 브라우저 성공을 사용자 전체 수동 수용이나 투자 예측 성능으로 확장하지 않는다.
- 사용자 후속 확인: STEP 1~20 기능 구현 완료, 오프라인 8533 접속·합성 차트/기술지표 표시와 오프라인 기능 정상 동작을 보고받았다. 기존 최종 테스트 수치는 유지하고 개별 미관측 항목의 수동 통과로 확대하지 않는다. 전체 발표 리허설·제출 형식/최종 수용은 미완료이며, 이번에는 문서만 정리했다. [사용자 확인 범위](steps/STEP_20.md#사용자-오프라인-시연-확인--2026-11-12)

## UI/UX 리디자인 — R01 기준선 확정 (2026-11-12)

- 기능 커밋 `4266787`과 기존 미커밋 계획/백로그를 보존하고, `app.py`·20개 UI 모듈·호출/상태/저장/비용 계약을 조사했다. [R01 기준선](redesign/R01_BASELINE.md), [상태 목록](redesign/R01_UI_STATE.md), [기능 정리 후보](redesign/R01_FEATURE_REVIEW.md)를 작성했다. UI 디자인·기능 이동/삭제·엔진 변경은 없다.
- 11개 계산 파일과 78개 백엔드 소스의 보호 지문, 표준 STEP 18 합성 12회 실험 JSON/기준 SHA·손계산 원장을 고정했다. 전용 검증 도구는 기준을 덮어쓰지 않고 재실행 MATCH/반복 동일·STOP_LOSS 8건·계좌 자산 1,026.9 USDT·시험 SQLite 스키마/중복/백업 복원을 검증한다. 변조된 시험용 사본도 거부했다.
- 기존 전체 회귀를 실제 재실행해 **646 passed / 0 failed / 14 skipped / 451 subtests passed** (314.13초)를 확인했다. 새 pytest 항목/skip·라이브러리를 추가하지 않았다. 외부 14개/실계정 유료 LLM은 미검증이며 실제 주문·사용자 원본 DB 수정·Git 커밋/Push는 없다.
- R01은 완료하고 R02~R08을 SF-POST-01의 남은 범위로 연결했다. 단일 AI/Workflow/통합 보고와 세 종류 Risk의 의미를 구분했으며 핵심 기능 삭제 권고는 없다. 삭제 검토는 대체되는 정적 헤더/개발 단계 문구에 한정한 제안이다.

## UI/UX 리디자인 — 1단계 설계 확정·3단계 진행 전환 (2026-11-12)

- R01 커밋 `ccf8d63`을 기준으로 진행 단위를 **1단계 설계 확정 → 2단계 전체 UI 구현 → 3단계 검증·보완**으로 압축했다. 기존 R02~R07 검증은 2단계 내부 점검으로, R08은 3단계로 대응하며 R01 계약/fixture/해시를 유지한다.
- [설계 확정본](redesign/DESIGN_STAGE_1.md)에 SIGFIN 브랜드·화이트/네이비/블루·좌측 6개 메뉴/상단 조건·핵심 카드/차트와 화면별 하위 작업을 정했다. 핵심 기능 유지·화면 통합·고급 JSON/로그·표시 중복 정리 후보를 구분한다. UI 적용이나 기능 삭제는 없다.
- 전체 계획과 미완료 백로그를 새 진행 방식에 맞췄다. **1단계 문서 설계만 완료**, 2·3단계는 미착수다. 이번에는 pytest/기준 도구·서버/브라우저·실 API를 실행하지 않았고 646/0/14는 R01의 기존 결과로 유지한다. 코드/테스트/사용자 데이터·키 변경·유료 호출·Git 커밋/Push는 없다.

## UI/UX 리디자인 — 2단계 전체 UI 구현 (2026-11-12)

- SIGFIN 로고·화이트/네이비/블루 공통 테마와 좌측 **시장 분석 / 전략·백테스트 / 포트폴리오 / 거시경제 / AI 연구 / 저장 이력**을 구현했다. 기존 실행 기능은 유지하고 선택한 화면/작업만 조립한다. 단일/복수 백테스트와 세 AI 경로는 각각의 실행·결과 계약을 유지한다.
- 페이지/하위 작업 이동 시 초안·비용/Risk·등록 전략·업로드와 공유 예산을 보존하고, 화면 방문 순서에 의존하던 계좌/확정 가격·평가 이력 문맥을 UI 계층에서 준비한다. 가격 재검증·각 결과 지문·기본 닫힌 JSON/로그·저장/다운로드/과거 복원을 유지했다. 중복 정적 헤더/STEP 표시만 정리했다.
- 전체 **669 passed / 0 failed / 14 skipped / 451 subtests passed**. 기존 렌더러 조립 회귀는 테스트 하네스로 보존하고, 실제 새 진입점/페이지 검사 23개를 추가했다. 최종 새 화면/리포트/Debate UI 별도 **51 passed / 0 failed**. R01의 11/78개 보호 소스·12회 결과 SHA MATCH·SQLite 복원/1,026.9 USDT 원장 결과와 사용자 원본 DB 지문은 동일하다.
- 격리 Edge·합성 서버에서 여섯 화면, 전략 왕복, 가상 매수·리포트 저장/두 파일 다운로드·과거 JSON 닫힘을 확인했다. 실제 외부 API/유료 LLM 호출·실제 주문·키/원본 데이터 변경·Git 커밋/Push는 없다. **3단계 최종 사용자/반응형·접근성 검증은 미완료**로 유지한다. [수정 파일·오류 해결·검증 한계](redesign/IMPLEMENTATION_STAGE_2.md).

## 검증 기록의 범위

- STEP 1~13의 과거 결과/확인 불가 항목은 [기존 기록 안내](steps/README.md)에 보존한다. 당시 테스트 코드의 존재를 실행 성공으로 바꾸지 않는다.
- STEP 17 최초 전체 회귀 기록: **551 passed / 0 failed / 14 skipped / 440 subtests passed**. 이후 STEP 16 가격 캐시 수정은 556 passed, 호출 계약/재검증 보완 시 기록은 **559 passed / 0 failed / 14 skipped / 442 subtests passed**다([STEP 16 후속 검증](steps/STEP_16.md)). 저장 오류 수정의 후속 결과는 [STEP 17](steps/STEP_17.md)에 별도로 기록한다.
- 559개 통과 기록에는 기존 STEP 1~13의 471개와 STEP 14~17의 88개가 포함된다. 14개 skip은 opt-in 외부 API 시험이며 통과로 합산하지 않는다. 실제 서버의 별도 Binance 경로 검증과도 구분한다.
- 2026-11-06 저장 오류 수정 후 전체 회귀: **566 passed / 0 failed / 14 skipped / 451 subtests passed** (451.84초). 신규 회귀 7개와 기존 STEP 1~16 기능을 포함한다. 별도 합성 자료/임시 DB의 실제 서버 저장·다운로드·재시작 복원은 통과했으며 사용자 브라우저 수동/실 API 검증과 구분한다([STEP 17](steps/STEP_17.md)).
- 2026-11-06 원본 JSON 표시·문서 마무리 후: STEP 14~17 관련 **97 passed / 0 failed / 0 skipped / 34 subtests passed** (128.58초), 전체 **568 passed / 0 failed / 14 skipped / 451 subtests passed** (325.59초). 새 UI 회귀 2개를 추가했고 사용자 DB/기존 미커밋 작업물은 보존했다. 브라우저 수동·실 LLM 검수는 여전히 미완료다([상세](steps/STEP_17.md#화면문서-마무리--원본-json-기본-노출-개선-2026-11-06)).
- 앞선 백로그/체인지로그 정리에서는 pytest·서버·외부 API를 실행하지 않고 기존 기록만 인용했다. 이후 저장 오류 수정에서는 자동 회귀와 격리 서버 검증을 수행했다. 과거 수치를 이번 결과나 현재 실 API 가동으로 바꾸어 표시하지 않는다.

## 문서 정리 기록 — 2026-11-06

- 완료 작업을 활성 BACKLOG에서 제거하고, STEP 16~17 미검증 5개·STEP 18~20 기존 12개와 STEP 20 이후 별도 리디자인을 체크리스트로 정리했다.
- 기존 STEP 14~20 계획 확정 작업(`SF-14-P01`~`SF-20-P01`)은 [확정 로드맵](ROADMAP_STEP_14_20.md)과 문서 커밋 `5df9bff`를 근거로 완료 기록에 포함한다. STEP 18~20 기능 구현은 완료로 기록하지 않는다.
- `docs/steps/`와 로드맵·소스·기존 미커밋 작업물은 보존한다. 이 변경은 문서 정리이며 기능 구현·유료 호출·Git 커밋/Push가 아니다.
