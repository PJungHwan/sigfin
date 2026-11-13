# SIGFIN 리디자인 2단계 — 전체 UI 구현

기록일: **2026-11-12 (Asia/Seoul)**. 착수 HEAD `b4391e4`, R01 보호 기준 `ccf8d63`, 기능 기준 `4266787`. [확정 설계](DESIGN_STAGE_1.md), [보호 계약](R01_BASELINE.md)을 따른다.

**2단계 전체 UI 구현·자동 회귀 및 제한된 브라우저 확인 완료. 3단계 최종 사용자 검수·보완은 미착수다.** 아래 결과는 합성 자료·시험 DB에 한정하며 실제 Binance/FRED·유료 LLM 검수로 확대하지 않는다.

## 구현한 화면

| 메뉴 | 하위 작업·배치 |
| --- | --- |
| 시장 분석 | 확정 종가/봉 변화율/RSI/MACD 카드, 큰 기존 Plotly 캔들·거래량/지표 패널, 지표 설정, 신호/체결 마커, 수집 UTC와 품질 상세 |
| 전략·백테스트 | 전략 편집 / 단일 백테스트 / 전략 비교 / 저장 실험 재현. 공통 원금·비용·손익절 설정 한 곳. 비교는 2~6개 고정 전략, 과거 업로드와 현재 입력 분리 |
| 포트폴리오 | 보유·가상 기록 / 위험 분석 / 거래·평가 이력. 현금·총자산·실현/미실현 손익, 명시적 가상 기록, 공통 확정 가격 재검증 |
| 거시경제 | 경제지표 조회 / 암호화폐·거시경제 관계. 기존 다섯 지표·단위·주기·CPI YoY·월간 상관/시차와 시점 한계 유지 |
| AI 연구 | 기본 통합 리포트 / 에이전트 워크플로우 / 단일 AI 분석(고급). 각 모드·승인·결과 분리, 공유 비용 한도, Debate/Risk 근거·노드 결과와 저장 |
| 저장 이력 | 연구 결과 / 가상 계좌·평가 이력. 과거 배너·명시적 조회/복원·원장 JSON과 저장 스냅샷 다운로드, 개발자 JSON 기본 닫힘 |

진입점은 `st.navigation(position="sidebar")`와 파일 기반 `st.Page` 여섯 개다. 종목/봉/조회 기간은 진입점에서 한 번만 렌더링한다. 하위 작업은 세그먼트 선택 후 해당 어댑터만 실행한다. 접기 메뉴의 닫힘을 API/저장 차단 수단으로 사용하지 않는다.

## 공통 디자인·정리 범위

- UI 전용 SIGFIN SVG 로고·네이비/화이트/블루 테마·카드·간격·한국어 표시와 시스템 한국어 폰트를 적용했다. 외부 폰트/CDN·추가 라이브러리 설치는 없다.
- 기존 Plotly의 수치/시각/도형/기준선/범례 의미는 그대로 두고 글자·배경의 공통 표시 스타일만 적용한다. 상승 녹색·하락 빨간색, Risk OFF 점선/ON 실선은 유지한다.
- 기존 고정 영어 제품 헤더는 SIGFIN 로고/현재 화면 제목으로 대체했다. 일반 화면의 STEP 접미어와 이전 사이드바/화면 상하 위치를 전제로 한 안내만 정리했다. `config/settings.py`의 제품명/버전과 계산 지문은 변경하지 않는다.
- 원본 JSON·세부 로그·그래프는 접기 안에 둔다. 필수 오류/부분 실패·가격 부족·해시 일치/불일치·비용 승인과 공개 근거 진입은 유지한다. 핵심 기능이나 저장 데이터를 삭제하지 않는다.

## 세션·가격·실행 계약

1. 선택/숫자/텍스트 위젯은 기존 키와 `persist_state="session"`을 사용한다. 동적 전략 행/초안·등록 전략·계좌/revision·공유 `_ai_ledger`는 기존 애플리케이션 상태로 보존한다. 버튼 이벤트는 복원하지 않는다.
2. 업로더는 세션 영속 옵션이 없으므로 원본 바이트를 세션 내에서 별도 보관한다. 기존 파일별 크기/스키마/SHA 검증을 그대로 통과해야 한다. 페이지 왕복은 재실행하지 않으며 새 파일/파일 제거/‘보관 파일 해제’는 재현 결과를 무효화한다. 다시 방문했을 때 네이티브 업로더가 비어 있으면 보관 파일명과 해제 버튼을 보여 준다. 파일을 임의 경로에 저장하지 않는다.
3. `ui/workspace_state.py`는 화면 렌더링 없이 현재 전략·백테스트 타입/설정·기존 계좌와 평가 이력을 준비한다. 존재하는 DB만 읽으며 페이지 표시만으로 생성/거래/평가 저장하지 않는다.
4. 가격 문맥은 `PortfolioPriceContext(frames, quotes, valuation)`로 만든다. `_portfolio_price_context` 튜플은 봉/기간 식별자로 남긴다. 확정 봉/수집/평가 UTC 순서·최근 2봉·계좌 ID/revision 검증과 재평가 비교를 유지한다. 가격 만료 시 N/A/UNKNOWN이며 원장은 보존한다.
5. 백테스트·Workflow·통합 리포트·실험·포트폴리오 위험의 기존 지문을 합치지 않는다. 표시할 때 현재 문맥과 다시 대조한다. 동일 확정 값의 수집 시각만 바뀌는 통합 저장 안전장치도 유지한다.
6. 단일 AI/Workflow/통합의 실행·비용 승인은 별개이며 세션 원장만 공유한다. 실제 호출은 기존 서비스의 키/가격/승인/한도/중복 검사를 거친 버튼 동작뿐이다. Debate/Risk는 Mock, LLM 미실행을 실제 응답으로 표시하지 않는다.
7. 오프라인 진입점은 기존 `isolated_sources` 범위 안에서 선택 페이지까지 실행한다. 별도 UUID 시험 DB·HTTP/SDK 차단·전 화면 합성 배너·소스 잠금을 유지한다.

## 생성·수정 파일

- 새 화면: `app_pages/market.py`, `strategy.py`, `portfolio.py`, `macro.py`, `research.py`, `history.py`.
- 새 UI 공통: `ui/design.py`, `ui/assets/sigfin.svg`, `ui/workspaces.py`, `ui/workspace_state.py`, `ui/market_view.py`, `ui/uploads.py`, `.streamlit/config.toml`.
- 조립/기존 UI: `app.py`, `ui/styles.py`, `ai_analysis_view.py`, `backtest_view.py`, `backtest_charts.py`, `crypto_macro_view.py`, `crypto_macro_charts.py`, `debate_view.py`, `experiment_view.py`, `macro_view.py`, `macro_charts.py`, `portfolio_view.py`, `portfolio_risk_view.py`, `research_view.py`, `risk_manager_view.py`, `storage_view.py`, `strategy_builder.py`, `workflow_view.py`.
- 검증: `tests/test_redesign_ui.py`, `tests/fixtures/legacy_dashboard.py`, `tests/test_app.py`, `tests/test_offline_demo.py`, `tests/test_portfolio_risk_ui.py`.
- 문서: 본 문서·README·전체 계획·설계 상태·BACKLOG·CHANGELOG.

## 검증 구성과 실제 결과

기존 단일 페이지 테스트에는 ‘사이드바 체크박스 11개’, ‘동시에 차트 세 개’, ‘다른 영역 버튼이 같은 화면에 존재’ 등의 조립 전제가 있다. 이를 없애거나 새 화면 검증이라고 주장하지 않도록 **이전 조립만 고정한 테스트용 `legacy_dashboard.py`**를 사용한다. 이 파일은 실제 UI/계산 모듈을 호출하며 앱의 탐색에는 등록하지 않는다. 기존 엔진·수집·스키마·모의 HTTP/비용 검사는 그대로 유지한다.

새 `test_redesign_ui.py`는 **실제 `app.py`와 여섯 페이지**를 실행한다. 페이지/작업 전환, 초안/비용/Risk/업로드·공유 원장, 단일/복수 백테스트, 설정→스냅샷→손절 원장·해시, 포트폴리오 -10% 손실·만료, 리포트 저장/다운로드/새 세션 과거 조회, JSON 닫힘, Mock/무승인 Live·외부 통신 차단을 검증한다. 오프라인 앱의 기존 통합 시험도 실제 새 탐색 경로로 이관했다.

| 검사 | 실제 결과 |
| --- | --- |
| 문법·import | 컴파일 확인. 전체 import 시험은 아래 전체 pytest에 포함 |
| 초기 실험/새 화면/합성 시연 | **37 passed / 0 failed / 0 skipped** |
| AI·새 화면·합성 시연 | **79 passed / 0 failed / 0 skipped / 16 subtests passed** |
| 최종 전체 pytest | **669 passed / 0 failed / 14 skipped / 451 subtests passed · 348.75초**. 기존 646개와 새 화면 23개. 기존 외부 opt-in 14개는 미실행이며 skip 추가 없음 |
| 최종 새 화면/리포트/Debate UI 재확인 | **51 passed / 0 failed / 0 skipped / 9 subtests passed · 90.09초**. 개발자 JSON 닫힘의 추가 assertion 포함 |
| R01 보호 도구 | **11개 계산 소스·78개 백엔드 불변, 12회 MATCH, STOP_LOSS 8건, 가상 원장 1,026.9 USDT·SQLite 복원 PASS** |
| 격리 Edge | 최종 코드로 실제 6개 메뉴 이동·전략 이름 왕복·합성 매수 1건·보고서 저장·Markdown/JSON 파일 2개 다운로드·과거 조회·원본 JSON 닫힘 확인. 1440px/390px 캡처 관찰 |
| 원본 보존 | 착수 전 기록한 사용자 SQLite 1개 byte SHA 동일. R01 계약/fixture/검증 도구·보호 소스 불변. 삭제 파일 없음 |
| 외부 API | 실제 Binance/FRED/OpenAI 호출 미실행. 자동 시험은 합성/모의 HTTP이며 localhost 서버/Edge 제어 접속과 구분 |

고정 입력 SHA: `f9663443b4970660fe9ecbd1ed1dbce3b28bd1cde4d166cd54ba99e2a724b97c`.
고정 결과 SHA: `0f10cd796475bbc10c6e9b98126065d2a06e3db8b016d453340949cf6f68149d`.
R01 manifest·기준 JSON·검증 도구를 갱신하지 않았다.

### 발견·해결한 문제

- 업로드 어댑터 초기 시험에서 테스트 `BytesIO`에 `.size`가 없어 5건 실패했다. 기존 업로드 객체와 파일 바이트 입력을 모두 지원하도록 크기 확인을 수정한 뒤 재검증했다.
- 새 시험에서 RSI 패널 제거에 따른 후속 축 번호 변경까지 동일해야 한다는 잘못된 기대와, 보유량 없는 현금 계좌도 가격 부족이어야 한다는 기대를 수정했다. 수치/도형 비교와 **실제 보유 후 가격 누락** 상황으로 안전성을 검사한다.
- FRED 키 누락 모의 입력을 `None`으로 둔 2건은 기존 키 함수의 예외 계약과 달랐다. `FredKeyError`를 발생시키는 모의 응답으로 고쳤으며 수집 서비스를 변경하지 않았다.
- 첫 브라우저 메뉴 선택자는 아이콘 텍스트까지 완전 일치시키려 해 실패했다. 표시 메뉴명을 찾도록 드라이버를 수정했고 이후 여섯 화면 검사를 통과했다. 앱 오류로 집계하지 않는다.
- 최종 브라우저 재확인에서는 고정 대기 후 입력을 찾는 방식이 늦은 렌더링에서 실패해, 실제 입력 존재를 기다리도록 수정하고 새 합성 서버에서 다시 통과했다. 마지막 실행 전에 렌더링 보완이 있어 진행 중이던 전체 검사를 중단하고, 최종 소스로 전체 pytest를 새 프로세스에서 다시 실행한 결과가 위 669개다.
- 기존 가격/저장 오류를 재발 방지하기 위해 순서 의존 문맥 준비와 서로 다른 지문 규칙을 별도로 검증한다. 자료 부족 자체를 정상 가격으로 보정하지 않는다.

## 실행·3단계에 남긴 범위

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py --server.port 8532
.\.venv\Scripts\python.exe -m streamlit run demo/offline.py --server.port 8534
.\.venv\Scripts\python.exe -X utf8 -m pytest -q --tb=short -ra
.\.venv\Scripts\python.exe -X utf8 scripts/verify_redesign_baseline.py
```

이번 시험 서버: **http://127.0.0.1:8534** · 합성 자료/시험 계좌 전용. 사용 중인 다른 서버는 종료하지 않는다. 일반 앱은 실제 키/공개 자료 환경이 필요할 수 있다. 새 의존성 설치나 보안 정책 변경은 없다.

3단계는 여전히 미완료다. 전체 화면의 사용자 수용, 발표 장비·모든 해상도/확대·키보드/대비, Plotly 실제 hover/zoom·페이지 복귀 시 범위, 모든 파일/재시작/예외 경로의 최종 브라우저 대조를 수행해야 한다. 390px/1440px의 제한된 화면 관찰을 전체 반응형/접근성 통과로 기록하지 않는다. 페이지를 다시 열 때의 차트 줌 보존은 보장하지 않는다. 일부 원장 원본 열명·enum은 검산을 위해 영문으로 유지한다. 세션 임시 초안·업로드/비용 원장은 서버 재시작 영구 저장 대상이 아니다.

실제 외부 API·유료 LLM 검수와 전체 발표 리허설은 기존 백로그에 남긴다. 사용자 원본 SQLite/키·`.env` 수정, 실제 주문, 유료 호출, Git 커밋/Push는 하지 않는다.
