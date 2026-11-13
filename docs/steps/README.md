# SIGFIN STEP 1~20 개발 기록

현재 STEP 1~20 기능 구현과 오프라인 시연 사용자 확인은 완료됐다([STEP 20](STEP_20.md), 2026-11-12). 발표 장비 전체 리허설·제출 형식/최종 수용과 외부 검수는 미완료다. [최종 요구 추적/검수](../final_acceptance.md)와 [시연 순서](../demo_scenario.md)를 함께 본다. 아래 STEP 1~13 기록 기준은 최초 조사 당시의 기록이며 현재 실행 결과와 구분한다.

## 기록 기준

- 문서 정리 기준일: **2026-10-29 (Asia/Seoul)**.
- 확인한 소스 기준: HEAD `e4047e1` (`feat: implement LangGraph risk manager agent and position risk analysis`).
- 작업 시작 시 Git 작업 트리는 깨끗했다. 이번 정리는 문서만 추가하며 기존 소스와 기존 docs는 수정하지 않는다.
- 단계는 커밋 제목만으로 확정하지 않고 해당 커밋의 추가/수정 파일, 현재 소스, 기존 기능 문서를 대조해 구분했다.
- 아래 시각은 Git의 **커밋 기록 시각(committer date, +09:00)**이다. 실제 개발 시작일·작업 시간·기능별 완료일을 뜻하지 않는다. 별도 근거가 없는 일정은 **기록 확인 불가**다.
- 개발 목표는 해당 단계의 소스와 기존 문서에서 재구성했다. 원래 기획서/요청서의 모든 문구가 저장소에 남아 있는 것은 아니다.
- 사용자는 Codex를 **STEP 3~4 무렵부터 사용**했다고 설명했다. 정확한 최초 사용 단계·시각과 단계별 도구 기여도는 **기록 확인 불가**다. STEP 1~2를 Codex 개발 실적으로 기록하지 않는다.
- 사용자가 부르는 프로젝트명은 **시그핀(SIGFIN)**이다. 현재 코드의 `APP_NAME`은 `Crypto Multi-Agent Research System`이다. 명칭 변경 시점은 **기록 확인 불가**이며 이번 작업에서는 표시명을 수정하지 않는다.

## 확인한 자료와 한계

1. 현재 소스: `app.py`, `config/`, `collectors/`, `indicators/`, `strategies/`, `backtest/`, `analytics/`, `services/`, `llm/`, `agents/`, `graph/`, `risk/`, `ui/`, `tests/`.
2. 기존 문서: `docs/technical_indicators.md`부터 `docs/ai_risk_manager.md`까지의 기능 문서 12개.
3. Git: `git log --reverse --name-status`, 해당 단계의 `git show <commit>:<path>`, 주요 변경 diff.
4. 기존 루트 `README.md`: **기록 확인 불가 — 파일이 없다.** 루트의 대소문자 변형 README 파일 및 로컬 `git log --all -- ':(icase)*readme*'`에서도 기존 기록을 찾지 못했다. 이 파일은 이번에 추가한 단계별 기록의 안내 문서다.
5. 기존 문서에 적힌 테스트 수치는 **당시 결과를 기록한 문서의 인용**이다. 원본 터미널 로그나 CI 실행 산출물이 별도로 보존되었다는 뜻은 아니다.
6. 테스트 코드의 존재는 실행 성공의 증거가 아니다. 테스트 명령만 있고 결과가 없는 경우 통과 수·실행일을 만들지 않는다.
7. 현재 파일에는 후속 단계의 확장이 포함된다. 단계 경계는 커밋 스냅샷을 기준으로 삼고 후속 변경은 따로 설명한다.
8. 대화 기록은 저장소 근거를 대신하지 않는다. 구체적인 시행착오·도구 사용·중단 원인이 문서/커밋으로 확인되지 않으면 **기록 확인 불가**로 남긴다.

## 단계별 구현과 근거

| 단계 | 확인 가능한 핵심 구현 | 대응 커밋 | 커밋 기록 시각 (+09:00) | 주요 기존 근거 |
| --- | --- | --- | --- | --- |
| [01](STEP_01.md) | Streamlit 기본 대시보드·사이드바·카드·CSS | `9fc1555` | 2026-10-14 22:14:32 | 당시 `app.py`, `ui/components.py`, `ui/styles.py` |
| [02](STEP_02.md) | Binance 확정 OHLCV·캐시·Plotly 캔들/거래량 | `0d1f7c4` | 2026-10-14 22:51:15 | `collectors/binance.py`, `services/market_data.py`, 테스트 |
| [03](STEP_03.md) | RSI/MACD/EMA/SMA/Bollinger/거래량 평균 | `0675bec` | 2026-10-14 23:21:40 | [지표 계약](../technical_indicators.md), `indicators/technical.py` |
| [04](STEP_04.md) | 구조화 전략·AND/OR·교차 평가·JSON·신호 | `8ba8f18` | 2026-10-14 23:50:54 | [전략 빌더](../strategy_builder.md), `strategies/` |
| [05](STEP_05.md) | 다음 봉 시가 백테스트·비용·회계·성과 | `fe05300` | 2026-10-15 00:32:00 | [백테스트 계약](../backtesting.md), `backtest/` |
| [06](STEP_06.md) | 손절/익절/트레일링·Risk 비교·연결 오류 진단 | `0ce580e` | 2026-10-15 18:14:40 | [리스크 관리](../risk_management.md), [연결 진단](../binance_connection.md) |
| [07](STEP_07.md) | FRED 5종·단위/시점 메타데이터·CPI YoY | `dd4eb6e` | 2026-10-15 19:32:44 | [FRED 수집](../macro_data.md), 날짜 경계 테스트 |
| [08](STEP_08.md) | 월별 결합·Pearson/Spearman·0~3개월 시차 | `664274c` | 2026-10-15 20:05:16 | [통합 분석](../crypto_macro_analysis.md), `analytics/` |
| [09](STEP_09.md) | 단일 LLM 실행 경로·Mock·구조화 출력·비용 | `379bd1d` | 2026-10-15 20:41:54 | [단일 분석](../ai_analysis.md), `llm/` |
| [10](STEP_10.md) | 실제 StateGraph·Technical/Macro Mock 병렬 노드 | `db0d5b8` | 2026-10-15 21:20:59 | [Mock 워크플로우](../langgraph_workflow.md), `graph/` |
| [11](STEP_11.md) | 역할별 LLM 선택 경로·동시성 비용 예약 | `23533c5` | 2026-10-15 21:52:10 | [LLM 워크플로우](../llm_workflow.md), `agents/llm_agents.py` |
| [12](STEP_12.md) | 실제 LangGraph의 Mock Bull/Bear 토론·Evaluator | `e07272e` | 2026-10-15 22:53:51 | [Debate](../bull_bear_debate.md), 토론 그래프/검증 |
| [13](STEP_13.md) | Mock Risk Manager·정량 위험·가상 포지션 | `e4047e1` | 2026-10-17 21:53:31 | [Risk Manager](../ai_risk_manager.md), `risk/` |

## 확인 가능한 테스트 기록

아래 수치는 각 단계 완료 시점의 **누적 전체 테스트**이며 단계별 신규 테스트 수와 다르다. `subtests`도 별도 집계이며 `passed`에 다시 더해 전체 테스트 수라고 부르지 않는다.

| 단계 | 저장소에서 확인한 결과 | 근거와 해석 |
| --- | --- | --- |
| 01 | 당시 실행 결과: **기록 확인 불가** | 최초 커밋에는 자동 테스트 파일이 없음 |
| 02~06 | 당시 전체 통과 수/실행일: **기록 확인 불가** | 테스트 구현·명령은 확인되지만 단계별 실행 결과표는 없음 |
| 07 | 실 FRED 테스트 3개, 하위 검증 15개 기록 | `macro_data.md`; 당시 전체 pytest 통과 수는 **기록 확인 불가** |
| 08 | 273 passed / 14 skipped / 하위 검증 253개 | `crypto_macro_analysis.md`; 별도 실 API 14 passed / 하위 검증 41개 기록 |
| 09 | 312 passed / 14 skipped / 하위 검증 314개 | `ai_analysis.md`; 신규 39개 기록 |
| 10 | 339 passed / 14 skipped / 하위 검증 332개 | `langgraph_workflow.md`; 신규 27개 기록 |
| 11 | 373 passed / 14 skipped / 361 subtests passed | `llm_workflow.md`; 신규 34개 기록 |
| 12 | 418 passed / 14 skipped / 379 subtests passed | `bull_bear_debate.md`; 신규 45개 기록 |
| 13 | 471 passed / 14 skipped / 417 subtests passed | `ai_risk_manager.md`; STEP 13 전용 53 passed / 38 subtests passed 기록 |

STEP 8~10 문서는 검증 기록일을 2026-10-15로, STEP 13은 2026-10-17로 명시한다. STEP 7의 실 API 재검증 기록일도 2026-10-15다. STEP 11~12 결과는 2026-10-15 커밋에 포함되어 있지만 테스트 실행일을 따로 확정할 기록은 **기록 확인 불가**다. 커밋 날짜로 검사 날짜를 대체하지 않는다. 위 결과는 이번 문서 정리에서 재실행한 결과가 아니다.

**이번 문서 작업에서는 pytest·Streamlit 서버·Binance/FRED/OpenAI API를 실행하지 않았다.** 파일·함수·커밋·문서 링크와 변경 범위를 정적으로 확인한다. 과거 서버 주소와 시장 관측값은 당시 기록이며 현재 서버 가동이나 현재 시세를 뜻하지 않는다.

## 추가 확인이 필요한 내용

- 초기 단계의 실제 개발 시작일·완료일, 수동 브라우저 점검 로그, STEP 1~7 누적 테스트 실행 결과.
- Codex를 처음 사용한 정확한 단계/시각과 단계별 도구·사람의 기여 구분.
- `API_TIMEOUT_MS` 관련 과거 import 오류의 원본 로그·수정 전 상태·독립된 해결 커밋.
- STEP 13 중단의 정확한 시각/원인: 중단 후 재사용 사실은 기존 문서에 있으나 학교 Wi-Fi나 사용량 제한 등 구체적 원인은 저장소만으로 확정할 수 없다.
- SIGFIN 명칭을 UI/설정에 적용한 이력 및 별도 기획서/발표 자료의 존재.
- OpenAI 실제 계정 호출 성공·응답 품질·실제 청구액. 기존 문서의 모의 HTTP 테스트를 유료 실 API 검증으로 바꾸어 해석하지 않는다.

## 기록을 재확인하는 명령

```powershell
git log --reverse --date=iso-strict --format="%h %cI %s"
git show --format= --name-status 0d1f7c4
git show 9fc1555:app.py
git show 0675bec:indicators/technical.py
git show --format= 0ce580e -- collectors/binance.py
git log --all --oneline -- ":(icase)*readme*"
```

공식 문서 URL이 기존 기능 문서에 있어도, 이번 정리에서 외부 지원 모델·요금·API 정책을 새로 검증했다는 뜻은 아니다. API 키 값이나 `.env` 내용은 기록 자료로 읽거나 복사하지 않았다.

## 후속 구현 기록 — STEP 14~20

아래는 위 STEP 1~13의 과거 기록 정리와 구분된다. STEP 14~17은 **2026-11-05 후속 요청에 따른 실제 순차 개발/시험**, STEP 18은 **2026-11-11 별도 개발 요청**의 기록이다. 각 단계의 선택 시험/해결한 실패와 최종 전체 회귀를 구분한다.

| 단계 | 구현 | 파일/검증 기록 |
| --- | --- | --- |
| STEP 14 | 무레버리지 가상 현금·BTC/ETH 수동 원장/평가·수수료 회계 | [STEP_14.md](STEP_14.md), 구현/수동 검증 완료 확인 |
| STEP 15 | SQLite 원자 저장/복원·평가/분석 입력과 결과 스냅샷 | [STEP_15.md](STEP_15.md), 구현/수동 검증 완료 확인 |
| STEP 16 | 실제 보유량 비중·집중·하락/비용·관측 이력 위험 | [STEP_16.md](STEP_16.md), 가격 연결 회귀 수정·수동 검증 확인 |
| STEP 17 | Mock/선택 LLM 연결·검증 근거 통합·저장/내보내기 | [STEP_17.md](STEP_17.md), 저장/JSON 회귀 수정·수동 검증 확인. 실 LLM 호출은 미검증 |
| STEP 18 | 동일 조건의 복수 전략 × Risk OFF/ON·시간순 구간·고정 입력 재현 | [STEP_18.md](STEP_18.md), 구현·자동 검증 및 사용자 BTC/USDT 브라우저 검증 완료 |
| STEP 19 | 기존 전체 모듈 통합·저장/JSON/결과 한도 안정화·격리 환경/로컬 운영 | [STEP_19.md](STEP_19.md), 새 통합/UI 9개·전체 638 passed/0 failed/14 skipped, 별도 환경 검증 |
| STEP 20 | 최종 요구 대조·README/운영/시연·합성 진입점·기존 전체 회귀 | [STEP_20.md](STEP_20.md), 전체 646 passed/0 failed/14 skipped·오프라인 기능 사용자 확인 완료. 전체 리허설·제출 확인 대기 |

STEP 14~17 기능/오류 수정은 `f28a8f3`, STEP 18은 `1e8f474`, STEP 19는 `7060ebd`에 커밋돼 있다. STEP 14~17 수동 검증 완료는 사용자의 문서 점검 요청에서 확인했다. 세부 수동 관측값/원본 로그가 새로 제공된 것은 아니며 이를 재실행 결과로 만들지 않는다. STEP 20 변경은 미커밋이며 자동 커밋/Push는 하지 않는다.

최초 순차 구현 당시 전체 결과는 **551 passed / 14 skipped / 440 subtests passed**였고, 후속 오류 수정 후 최신 기록은 **570 passed / 0 failed / 14 skipped / 451 subtests passed**다([STEP 17 최종 기록](STEP_17.md#step-18-착수-전-최종-상태-확인-2026-11-06)). 이번 문서 점검에서는 테스트/서버/API를 새로 실행하지 않았다.

가상 주문과 Mock Debate/Risk를 실거래·실제 LLM 협업으로 소개하지 않는다. 실계정 유료 LLM 호출/응답 품질·청구와 실제 Binance/FRED 통합 자료 대조는 별도 미검증이다. 14개 외부 API skip을 통과로 합산하지 않는다. 위 570개는 STEP 18 이전 기록이며, 후속 신규/전체 시험은 해당 개발일지에 별도 기록한다. STEP 20의 검증/이월은 최신 기록을 따르고 전체 UI/UX 리디자인은 별도 미착수다. Git 커밋/Push는 수행하지 않는다.

STEP 18 최신 전체 회귀: **629 passed / 0 failed / 14 skipped / 451 subtests passed** (267.90초). 최초 신규 38개·해시 비교 18개·손절 검증 3개와 기존 570개를 포함한다. 사용자가 실제 BTC/USDT의 12회 비교·성과표/곡선·JSON 재실행/해시 일치·손절 1% 및 `STOP_LOSS`를 확인했다([최종 확인](STEP_18.md#step-19-착수-전-최종-확인--2026-11-11)). 외부 API 통합 자동 시험 14개·유료 LLM 검증은 미실행이며, 이번 문서 정리에서 테스트/브라우저/API를 재실행하지 않았다.

STEP 19에서는 기존 회귀와 새 통합·저장 실패·경합·세션·최대 결과 한도를 포함해 **638 passed / 0 failed / 14 skipped / 451 subtests passed** (474.42초)를 확인했다. 별도 Python 3.11 환경의 import/의존성 검사와 선택 시험 **21 passed / 2 subtests passed**, 8531 서버 health 200을 확인했다. 격리 브라우저/실 API/수동 재검수의 실제 범위는 [STEP 19](STEP_19.md)를 따른다. 실제 유료 API·실제 주문·Git 커밋/Push는 하지 않았다.

STEP 20의 2026-11-12 최종 전체는 **646 passed / 0 failed / 14 skipped / 451 subtests passed** (672.50초)다. 별도 기존 검증 환경에서 신규 시연/import 9 passed, 두 서버 health와 격리 Edge 합성 거래/위험·리포트 저장/조회·12회 실험 JSON 재실행/해시 일치를 확인했다. 초기 실패와 해결·원본 보존은 [상세 기록](STEP_20.md)을 따른다. 후속 사용자 확인은 8533 접속·합성 차트/기술지표 정상 표시와 오프라인 기능 정상 동작이며, 개별 미관측 항목의 수동 통과로 확대하지 않는다([확인 범위](STEP_20.md#사용자-오프라인-시연-확인--2026-11-12)). SF-20-01~03은 완료이고 SF-20-04 전체 리허설·제출 형식/최종 수용과 외부 검수는 미완료로 유지한다. 이번 후속 문서 정리에서 테스트/서버/API는 재실행하지 않았다.
