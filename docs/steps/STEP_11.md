# STEP 11 — Technical/Macro 역할별 LLM 실행 옵션

## 근거와 단계 경계

- 대응 커밋: `23533c5` — `feat: add LLM technical and macro agents with validated workflows`.
- Git 커밋 기록 시각: **2026-10-15 21:52:10 +09:00**. 실제 개발 시작/완료 시각은 **기록 확인 불가**.
- [LLM 워크플로우 문서](../llm_workflow.md), 신규 역할 어댑터/출력 계약과 기존 client/budget/graph 변경을 대조했다.

## 개발 목표

기존 Mock 그래프를 유지하면서 Technical/Macro에 독립 프롬프트·Pydantic 출력을 사용하는 실제 LLM 선택 경로를 추가하고, 병렬 예산·검증·부분 실패를 관리한다.

## 실제 구현 기능

- 기존 `Mock Workflow`를 기본값으로 유지하고 `LLM Workflow`를 선택 경로로 추가한다.
- StateGraph의 Validator → 병렬 Technical/Macro → Python Aggregator → Python Formatter 구조와 barrier를 유지한다.
- Technical은 확정 지표 요약과 품질을, Macro는 조회된 FRED 값·단위·관측/수집 시각·미검증 발표/빈티지 정보를 받는다.
- 전용 `TechnicalLLMOutput`, `MacroLLMOutput`과 역할별 프롬프트를 사용한다.
- 필요한 핵심 자료/유효 Macro가 없으면 해당 유료 호출을 생략하고 로컬 INSUFFICIENT_DATA를 `api_called=False`로 기록한다.
- 허용 근거 ID·출처·값·시각·문장을 대조하고 실제 출력 규칙과 내용 검증을 분리한다. Macro 방향 판단이나 새 사건/수치·승률을 허용하지 않는다.
- STEP 9의 client와 UsageLedger를 공유한다. 새 별도 결제 체계를 만들지 않는다.
- 호출할 두 분기의 전체 횟수/예산을 `RLock` 아래 일괄 예약한다. 총예산이 부족하면 둘 다 전송하지 않는다.
- 기본 5 HTTP 시도/$0.25, 입력 24KB/출력 제한, 60초 중복 방지, Workflow 자동 재시도 0회, 가격 미확인/만료 차단.
- API 키·live 모드·실행 버튼·명시적 비용 승인이 모두 필요하다. 페이지 로딩/모드 변경만으로 호출하지 않는다.
- State에 모드와 technical/macro 실행 상세를 추가하고 모델·실제 전송 여부·검증·토큰·예약/추정 비용·오류·처리 시간을 표시한다.
- Macro 실패/Technical 정상은 PARTIAL, 필수 분석/집계 실패는 FAILED이며 정상 분기 결과는 유지한다.

Aggregator에는 추가 LLM 호출이 없다. STEP 11 당시 Bull/Bear/Risk Manager는 미연결이다.

## 사용 기술

Python 3.11, LangGraph 1.2.15, 공식 OpenAI SDK Responses API, Pydantic Structured Outputs, httpx, RLock, Streamlit. 기존 검증 문서의 SDK/Pydantic 버전은 2.54.0/2.14.1이다.

저장소에 기록된 모델/가격 확인 기준은 STEP 9와 같다. 현재 계정 가용성·현재 요금을 이번 문서 정리에서 검증한 것은 아니다.

## 주요 관련 파일

| 파일 | 구현 근거 |
| --- | --- |
| [agents/llm_agents.py](../../agents/llm_agents.py) | `validate_llm_access`, `WorkflowLLMSession`, 분기별 호출/로컬 생략 |
| [llm/agent_contracts.py](../../llm/agent_contracts.py) | 독립 프롬프트·스키마·역할별 내용 검증 |
| [llm/client.py](../../llm/client.py), [llm/budget.py](../../llm/budget.py) | 공용 전송·일괄 예약·정산/미확인 비용 |
| [graph/nodes.py](../../graph/nodes.py), [graph/state.py](../../graph/state.py), [graph/validation.py](../../graph/validation.py), [graph/runner.py](../../graph/runner.py) | 모드 주입·실행 상세·재검증·실패 처리 |
| [ui/workflow_view.py](../../ui/workflow_view.py), [ui/ai_analysis_view.py](../../ui/ai_analysis_view.py) | 모드/승인/공유 상한·결과/로그 |
| [tests/test_llm_workflow.py](../../tests/test_llm_workflow.py), [tests/test_llm_workflow_ui.py](../../tests/test_llm_workflow_ui.py) | 모의 SDK·동시성/예산·무승인 차단·UI |

## 확인 가능한 테스트 결과

[기존 문서](../llm_workflow.md)의 검증 기록:

- 신규 **34개**, 전체 **373 passed, 14 skipped, 361 subtests passed** (106.14초).
- 합성 입력과 모의 HTTP로 공식 SDK 파싱·그래프 병렬/합류·근거 오류·PARTIAL/FAILED·예산 경합·중복·인증/한도/타임아웃·승인·UI 및 기존 Mock/단일 LLM을 검증했다는 기록.
- Python 소스 98개 문법, 별도 로컬 import, 의존성/공백 검사 통과 기록.
- 8505 HTTP/health 200/WebSocket, 실제 시세 차트와 Mock Workflow COMPLETED 기록.
- **유료 OpenAI 호출은 수행하지 않았다.** LLM 응답 테스트는 MockTransport이며 실제 계정 권한·청구·응답 품질 검증과 다르다.
- 결과가 포함된 커밋의 날짜는 2026-10-15다. 별도로 명시된 테스트 실행일·시작/종료 시각과 원본 로그 보존 위치는 **기록 확인 불가**.
- 이번 문서 작업에서는 테스트·서버·API를 실행하지 않았다.

## 오류 및 해결 기록

- 기존 문서에는 최초 격리 서버의 Binance 소켓 연결 거부와, 허용된 환경에서 같은 8505 서버 재시작 후 실제 시세/Mock 그래프 성공이 기록돼 있다.
- 해당 문제를 해결하기 위해 수집/백테스트 규칙을 바꾸지 않았다는 기록이 있다.
- 실제 OpenAI 인증/Rate Limit/타임아웃 장애의 발생·해결 이력은 **기록 확인 불가**다. 해당 오류는 모의 응답으로 검증한 것이다.

## 확인이 필요한 사항과 한계

문장 카탈로그 제약과 Macro 발표/빈티지 부족이 남아 있다. 실제 LLM 실행 코드의 존재와 실제 호출 성공은 다르다. 실제 사용자 계정의 응답 품질·비용 검증은 별도 승인 후 확인할 항목이다.
