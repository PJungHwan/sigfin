# STEP 10 — LangGraph Technical/Macro Mock 워크플로우

## 근거와 단계 경계

- 대응 커밋: `db0d5b8` — `feat: implement LangGraph multi-agent mock workflow`.
- Git 커밋 기록 시각: **2026-10-15 21:20:59 +09:00**. 실제 개발 시작/완료 시각은 **기록 확인 불가**.
- [Mock 워크플로우 문서](../langgraph_workflow.md), 신규 graph/state/node/runner와 규칙 Agent·UI·테스트를 대조했다.
- 현재 그래프에는 STEP 11 LLM 경로와 STEP 13 선택적 Risk 노드도 있다. 여기서는 최초 Mock 경로를 기록한다.

## 개발 목표

단일 분석 기반을 재사용해 Technical/Macro를 독립 노드로 나누고, 실제 LangGraph의 병렬 상태 관리·합류·집계·리포트·로그를 구현한다.

## 실제 구현 기능

```text
START → Input Validator → [Technical Agent ∥ Macro Agent]
      → Research Aggregator → Report Formatter → END
```

- 공식 `StateGraph`, `START`, `END`, `compile()`과 `stream(stream_mode="values")`를 사용한다. 실제 그래프 실행이며 분석 내용은 규칙 기반 Mock이다.
- 검증 성공 시 Technical/Macro 병렬 분기, 최대 동시성 2. list-form barrier edge로 두 분기의 종료 후 한 번 집계한다.
- `WorkflowState(TypedDict)`에 실행 ID·대상·요약·자료 시각·분기 결과·집계·보고서·상태를 둔다.
- 로그/오류는 `Annotated[list, operator.add]`로 병합하고 원본 DataFrame은 실행별 Runtime Context의 참조로 전달한다.
- Input Validator는 종목/봉/핵심 지표/시간/유한값/메타데이터를 검사한다. 미래·미확정 봉 실패 시 후속 노드는 SKIPPED다.
- Technical은 실제 지표의 명시적인 AND 조건으로 방향을 요약한다. Macro는 유효 자료가 있으면 CONTEXT_ONLY, 없으면 INSUFFICIENT_DATA다.
- 발표 시각/당시 빈티지가 미검증인 Macro는 방향 판단에 사용하지 않는다. Macro 실패가 정상 Technical 결과를 지우지 않는다.
- Aggregator는 원본 근거와 결과를 재검증하며 상충을 명시한다. Technical 정상/Macro 실패는 PARTIAL, 필수 분석 실패는 FAILED다.
- 한국어 Formatter는 근거·한계·실행 ID/시각/모드를 포함하고 비공개 내부 추론은 출력하지 않는다.
- 노드 WAITING/RUNNING/COMPLETED/FAILED/SKIPPED와 소요 시간, Mermaid 구조, 분기 결과와 실행 후 로그 UI.
- 버튼 실행·입력 변경 시 무효화, 기존 데이터 캐시와 STEP 9 단일 분석 유지. LangSmith 원격 추적은 비활성화한다.

STEP 10 당시 Bull/Bear/Risk Manager는 미연결이었다. 실제 다중 LLM 호출이나 Debate를 구현한 단계로 기록하지 않는다.

## 사용 기술

Python 3.11, LangGraph StateGraph, TypedDict/Annotated reducer, Pydantic, Streamlit 네이티브 Mermaid. 기존 문서의 검증 버전은 LangGraph 1.2.15/langchain-core 1.6.10/Streamlit 1.64.0이다.

## 주요 관련 파일

| 파일 | 구현 근거 |
| --- | --- |
| [graph/workflow.py](../../graph/workflow.py), [graph/state.py](../../graph/state.py) | 그래프·barrier·State/Runtime/결과 타입 |
| [graph/nodes.py](../../graph/nodes.py), [graph/validation.py](../../graph/validation.py), [graph/runner.py](../../graph/runner.py) | 입력/분기/집계/리포트·검증·실행 추적 |
| [agents/research.py](../../agents/research.py), [services/ai_inputs.py](../../services/ai_inputs.py) | Mock Agent와 공용 시장/Macro 요약 |
| [ui/workflow_view.py](../../ui/workflow_view.py), [app.py](../../app.py) | 버튼·그림·상태·결과·로그 |
| [tests/test_workflow.py](../../tests/test_workflow.py), [tests/test_workflow_ui.py](../../tests/test_workflow_ui.py) | 실제 그래프·합류/격리·검증·무과금·UI |

## 확인 가능한 테스트 결과

[기존 문서](../langgraph_workflow.md)의 **2026-10-15** 기록:

- 신규 테스트 **27개**, 전체 **339 passed, 14 skipped**, 하위 검증 **332개 통과**.
- 실제 병렬 분기/barrier 합류, 로그 reducer, 동시 실행 격리, 입력/근거 변조, 부족/실패/상충, 원본 불변성·재현성, API/외부 추적 미호출, UI를 검증했다는 기록.
- 8503 서버 health 200, 실제 Binance 가격/FRED CPI 조회 후 Technical/Macro/집계 완료 기록.
- FRED 5종 노드 검증은 합성 입력이며 해당 서버의 실 FRED 연동 확인은 CPI다.
- **실제 LangGraph 실행 + 규칙 Mock 노드 + OpenAI 유료 호출 0회**를 구분한다.
- 원본 로그/CI 산출물 보존 위치는 **기록 확인 불가**. 이번 문서 작업에서는 테스트/서버/API를 실행하지 않았다.

## 오류 및 해결 기록

- 기존 문서는 실행 중 서버가 이전 모듈 캐시를 유지해 import 오류가 났으며 8503 서버 재시작으로 해소했다고 기록한다. 기존 8501은 종료하지 않았다.
- 정확한 예외 클래스·전체 traceback·오류 발생 시각·별도 수정 커밋은 **기록 확인 불가**다.
- 로그와 노드 실패 처리는 코드로 확인되지만 모든 실패 시나리오가 실제 개발 중 발생했다는 뜻은 아니다.

## 확인이 필요한 사항과 한계

UI는 실행 **후** 기록이며 실시간 진행 UI가 아니다. 실행 ID·시각·처리 시간은 재실행마다 다를 수 있다. 영속 체크포인트/장기 기록, 다중 LLM 협업·투자 성능 검증은 이 단계 범위 밖이다.
