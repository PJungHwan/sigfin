# STEP 12 — LangGraph Bull/Bear Mock Debate

## 근거와 단계 경계

- 대응 커밋: `e07272e` — `feat: implement LangGraph bull bear mock debate workflow`.
- Git 커밋 기록 시각: **2026-10-15 22:53:51 +09:00**. 실제 개발 시작/완료 시각은 **기록 확인 불가**.
- [Debate 문서](../bull_bear_debate.md), 신규 Agent/State/그래프/검증/UI/테스트와 현재 토론 노드를 대조했다.
- 현재 Debate 그래프의 선택적 Risk Manager는 STEP 13 확장이다. STEP 12 당시에는 Risk Manager 미연결이다.

## 개발 목표

검증된 Technical/Macro 결과를 실제 StateGraph 노드 간 전달해 Bull/Bear 주장과 상호 반박, 구조적 평가·통합·공개 기록을 구현한다. 유료 LLM Debate는 실행하지 않는다.

## 실제 구현 기능

```text
START → Validator → [Technical ∥ Macro] → Evidence Preparation
      → [Bull ∥ Bear] → Debate Round → Debate Evaluator
      → Research Aggregator → Report Formatter → END
```

- 기본 Debate OFF, Mock Workflow에서만 ON 가능. 기존 STEP 11 Mock/LLM 경로와 STEP 9 단일 분석을 유지한다.
- 두 차례 병렬 분기와 barrier 합류를 갖는 실제 StateGraph다. 반복 edge가 없는 DAG이고 지원 라운드는 1~2다.
- Evidence Preparation은 원본 근거의 ID·출처·수치·단위·관측 시각·역할·fingerprint를 검증한다.
- Round 1: Bull은 상승, Bear는 하락 역할 근거로 주장한다. 반대 근거도 인정하며 자기 근거가 없으면 INSUFFICIENT_EVIDENCE다.
- Round 2: 실제 상대의 검증된 Round 1 결과를 `respond(evidence, opponent)`에 전달한다. 상대 주장 ID와 근거를 인용해 반박/인정한다.
- 반박 근거가 있으면 REBUTTAL, 없으면 CONCESSION, 상대 주장이 없으면 INSUFFICIENT_EVIDENCE다. 최초 주장 복사를 반박으로 표시하지 않는다.
- 메시지는 라운드·발언자·자기/상대 주장 ID·근거/인용·유형·내용·검증 상태를 보존한다.
- Evaluator는 유효 Bull/Bear 주장·거부·응답 대상·미해결 상충·부족 자료·요약/상태를 반환한다. 주장 개수로 승자를 정하지 않는다.
- 근거/시각/단위/참조·정해진 문장·원본 fingerprint를 생성 후/Evaluator/Aggregator에서 검증한다. 시점 미검증 Macro는 방향 인용에서 제외한다.
- 실패/취소/중복과 부분 결과를 구분한다. 기존 UsageLedger의 락/중복 기능만 재사용해 60초 중복 차단하며 유료 사용량은 늘리지 않는다.
- State history/errors/logs reducer, 정렬된 공개 기록, Bull/Bear 상태·타임라인·근거·평가·최종 보고서·실행 후 로그 UI.

## 사용 기술

Python 3.11, LangGraph StateGraph 1.2.15, Pydantic, 결정론적 Agent 인터페이스, 해시 ID/fingerprint, Streamlit 1.64.0. 새 패키지는 추가하지 않았다.

## 주요 관련 파일

| 파일 | 구현 근거 |
| --- | --- |
| [agents/bull_agent.py](../../agents/bull_agent.py), [agents/bear_agent.py](../../agents/bear_agent.py), [agents/debate.py](../../agents/debate.py) | 주장/상대 응답·메시지/평가 모델 |
| [graph/debate_workflow.py](../../graph/debate_workflow.py), [graph/debate_state.py](../../graph/debate_state.py) | 실제 노드 경로·확장 State/Runtime |
| [graph/debate_nodes.py](../../graph/debate_nodes.py), [graph/debate_validation.py](../../graph/debate_validation.py), [graph/runner.py](../../graph/runner.py) | 준비·주장/반박·평가/집계·검증·실행 |
| [ui/debate_view.py](../../ui/debate_view.py), [ui/workflow_view.py](../../ui/workflow_view.py), [config/settings.py](../../config/settings.py) | 공개 기록 UI·토글/라운드·설정 |
| [tests/test_debate.py](../../tests/test_debate.py), [tests/test_debate_ui.py](../../tests/test_debate_ui.py) | 그래프·근거/응답·실패/취소·무과금·호환성/UI |

## 확인 가능한 테스트 결과

[기존 문서](../bull_bear_debate.md)의 기록:

- 신규 **45개**, STEP 1~11 포함 전체 **418 passed, 14 skipped, 379 subtests passed** (134.24초).
- 실제 그래프 컴파일/병렬 합류·상대 참조·1~2라운드·시각/근거 변조·자료 부족/부분 실패·중복/취소·불변성·재현성·API 미호출·Debate OFF 호환성·UI를 검증했다는 기록.
- Python 소스 108개 문법, 별도 import, `pip check`, `git diff --check` 통과 기록.
- 8505 HTTP/health 200/WebSocket에서 실제 시장 차트, 토글만으로 실행되지 않음, 버튼 이후 Mock Debate COMPLETED와 Round 2 메시지 6개 표시 기록.
- **유료 OpenAI 호출 0회**이며 기존 LLM 경로 회귀는 모의 HTTP다.
- 결과를 담은 커밋은 2026-10-15다. 원본 터미널/CI 산출물과 정확한 검사 시각은 **기록 확인 불가**.
- 이번 문서 작업에서는 테스트/서버/API를 실행하지 않았다.

## 오류 및 해결 기록

| 확인된 기록 | 처리와 범위 |
| --- | --- |
| 서버 점검 클라이언트의 selectbox 전송 형식 오류 | 기존 문서는 Streamlit 문자열 직렬화 형식으로 **점검 클라이언트**를 고쳐 해결했다고 기록한다. 앱 코드를 바꾼 오류로 기록하지 않는다. |
| 확장 State 입력 필드 | 기존 문서/소스는 `WorkflowState`만 노드 입력 타입에 지정하면 토론 필드가 제외될 수 있어 `DebateState`를 명시한다고 설명한다. 원본 traceback·발생 시각은 **기록 확인 불가**다. |
| 개발 중단 | 사용량/네트워크로 작업이 멈춘 상세 일시·재개 횟수는 저장소에서 **기록 확인 불가**. 대화 경위만으로 커밋에 없는 작업일을 추가하지 않는다. |

## 확인이 필요한 사항과 한계

실제 LLM Debate·환각 제거·예측 정확도 향상을 검증한 것이 아니다. 기록은 공개 주장/반박이며 내부 Chain-of-Thought가 아니다. 취소 Event는 엔진 경계에서 지원하지만 실행 중 취소 버튼/실시간 진행 UI는 없다. Macro 공개 시점과 당시 빈티지는 미검증이다.
