# STEP 13 — 규칙 기반 Mock Risk Manager Agent

## 근거와 단계 경계

- 대응 커밋: `e4047e1` — `feat: implement LangGraph risk manager agent and position risk analysis`.
- Git 커밋 기록 시각: **2026-10-17 21:53:31 +09:00**. 실제 개발 시작/중단/재개/완료 시각은 **기록 확인 불가**.
- [Risk Manager 문서](../ai_risk_manager.md), 정량/가상 포지션/판단 코드, Risk State/노드와 두 그래프의 옵션, 신규 테스트를 대조했다.
- 기존 문서는 중단 당시 `backtest/risk.py`의 순수 슬리피지 함수와 `config/risk_settings.py`, `risk/`의 모델/계산 코드를 이어서 사용했다고 명시한다. 중간 작업 트리가 별도 커밋으로 남은 것은 아니다.

## 개발 목표

Technical/Macro·검증된 Debate와 확정 OHLCV, 사용자 가상 리스크 설정을 종합해 위험 등급·근거·포지션 적합성·보류 상태를 실제 LangGraph 노드로 제공한다. 실제 LLM Risk Manager나 주문은 구현하지 않는다.

## 실제 구현 기능

- `RiskManager.analyze`는 결정론적 Python 평가를 호출한다. OpenAI 클라이언트를 호출하지 않는다.
- 유효 수익률 표본 변동성(`ddof=1`)과 365일·24시간 제곱근 시간 가정의 연율 변동성.
- 확정 종가 MDD와 고점/저점 봉 마감 시각. 낙폭 0이면 낙폭 발생 시각은 N/A다.
- 연속 14개 TR 평균 seed 이후 Wilder ATR(14), ATR/종가 비율. 갭 뒤에는 재초기화하고 갭 수익률을 제외한다.
- 최근/직전 20개 수익률의 변동성 확대, 현재 거래량/직전 20봉 평균 급증, 실제 구간/봉 수/관측 간격/누락/지연 표시.
- LOW/MODERATE/HIGH/CRITICAL/UNKNOWN. 기본 최소 유효 수익률 40개, 핵심 자료/품질 부족은 UNKNOWN이다.
- 원금·허용 손실률·손절 거리·레버리지·편도 수수료/슬리피지의 가상 양방향 손절 시나리오.
- 허용 손실은 `원금×허용률`, 수량은 왕복 비용 포함 손실 한도와 증거금/진입비 한도 중 작은 값이다. 레버리지로 손실 예산을 늘리지 않는다.
- 전체 `원금×레버리지` 노출과 비용 포함 제한 규모를 구분한다. 최상위 `risk_limit_exceeded`는 전자, 예상 손실/비용은 후자의 값이다.
- 내부 연구 상태 REVIEW/HOLD/ABSTAIN과 위험/방향 구분. UNKNOWN/CRITICAL/수량 0/실패는 ABSTAIN, 한도 초과 등은 HOLD다.
- 원본 근거 ID·출처·값·단위·시각과 지표/비용/등급/행동을 재계산해 전체 결과를 대조한다. 변조는 보정하지 않고 거부한다.
- Risk 활성화/입력·카드·지표·가상 시나리오·한도 경고·근거/한계·실행 후 로그 UI. 설정 변경 시 결과 무효화.

### 연구용 등급 기준

| 지표 | MODERATE 경계 | HIGH 경계 | CRITICAL 경계 |
| --- | --- | --- | --- |
| 연율 변동성(%) | 40 | 80 | 150 |
| 종가 MDD(%) | 10 | 25 | 40 |
| ATR/종가(%) | 2 | 5 | 10 |

가장 높은 위험 단계를 적용하고 변동성 확대 1.5배는 최소 HIGH, 거래량 3배는 최소 MODERATE다. 이는 설정으로 분리된 **연구용 가정값**이며 시장에서 검증된 최적 기준이 아니다.

### 그래프 연결과 기존 기능

```text
Debate ON : Technical/Macro → 근거 준비 → Bull/Bear/반박 → Evaluator
          → [Risk Manager 선택] → Aggregator → Formatter
Debate OFF: Technical/Macro → [Risk Manager 선택] → Aggregator → Formatter
```

Debate/Risk ON·OFF 네 조합을 지원한다. Risk OFF는 기존 결과 계약을 유지한다. Risk ON은 Mock Workflow만 지원하며 기존 LLM Workflow는 Risk OFF로 사용할 수 있다.

`RiskState`에 위험 입력/결과·보류 상태·원래 방향·노드 상태를 추가하고 기존 로그 reducer/Runtime 참조를 재사용한다. 위험 실패는 가능한 기존 결과를 남겨 PARTIAL+ABSTAIN으로 표시하며 필수 Technical 실패는 전체 FAILED다. Aggregator는 위험 한도를 임의로 무시하지 않는다.

**`backtest/engine.py`는 이 단계에서 수정하지 않았다.** `backtest/risk.py`에는 기존 엔진과 같은 불리한 슬리피지의 순수 helper를 추가했다. 정적 손절 기준은 마지막 확정 종가이며 STEP 6의 진입 체결가 기준 손절/백테스트 성과를 변경하지 않는다.

## 사용 기술

Python 3.11, pandas, NumPy, Pydantic, LangGraph StateGraph, 기존 백테스트 `risk_levels`/MDD 계산, Streamlit. 새 외부 패키지를 추가하지 않았다.

## 주요 관련 파일

| 파일 | 구현 근거 |
| --- | --- |
| [agents/risk_manager.py](../../agents/risk_manager.py), [risk/evaluator.py](../../risk/evaluator.py) | Agent·등급/보류·보고서 재검증 |
| [risk/metrics.py](../../risk/metrics.py), [risk/position.py](../../risk/position.py), [risk/models.py](../../risk/models.py) | 정량·비용/증거금·입출력 계약 |
| [config/risk_settings.py](../../config/risk_settings.py), [backtest/risk.py](../../backtest/risk.py) | 연구 경계값·기존 위험/슬리피지 공식 재사용 |
| [graph/risk_state.py](../../graph/risk_state.py), [graph/risk_nodes.py](../../graph/risk_nodes.py) | 확장 State·입력 대조·Risk/집계/리포트 |
| [graph/workflow.py](../../graph/workflow.py), [graph/debate_workflow.py](../../graph/debate_workflow.py), [graph/debate_nodes.py](../../graph/debate_nodes.py), [graph/runner.py](../../graph/runner.py) | 선택 경로·중복/오류 범위·실행 |
| [ui/risk_manager_view.py](../../ui/risk_manager_view.py), [ui/workflow_view.py](../../ui/workflow_view.py), [app.py](../../app.py) | 가상 설정·카드·근거/로그·무효화 |
| [tests/test_risk_manager.py](../../tests/test_risk_manager.py), [tests/test_risk_manager_ui.py](../../tests/test_risk_manager_ui.py) | 손계산·경계·그래프/실패·무과금·UI |

## 확인 가능한 테스트 결과

[기존 문서](../ai_risk_manager.md)의 **2026-10-17** 기록:

- STEP 13 전용 **53 passed, 38 subtests passed**.
- 전체 **471 passed, 14 skipped, 417 subtests passed** (131.96초). 기존 STEP 1~12의 418개와 STEP 5 고정 결과 10개 비교 포함.
- Python 3.11.9/소스 120개 문법·Risk/graph/UI import·의존성·공백 검사 통과 기록.
- 8506 HTTP/health 200/WebSocket, 실제 Binance BTC/ETH 각 2,207개 확정 봉으로 Mock Debate+Risk 실행 기록.
- 실제 그래프에서 규칙 Mock 노드를 실행한 것이며 **OpenAI 유료 호출 0회**다. FRED 신규 실 API는 실행하지 않았고 재사용/시점 제약은 모의 검증이다.
- 원본 로그/CI 산출물 보존 위치는 **기록 확인 불가**. 이번 문서 작업에서는 테스트·서버·API를 실행하지 않았다.

## 오류 및 해결 기록

- 기존 문서와 현재 `evaluate_debate`는 **후속 Risk 오류가 이미 끝난 Debate 평가를 바꾸지 않도록** 후속 노드 오류를 분리한다고 명시한다. Risk 실패에도 검증된 기존 결과를 유지하는 회귀 테스트가 있다. 정확한 최초 실패 시각/원본 traceback은 **기록 확인 불가**다.
- 기존 문서는 최초 샌드박스 서버의 Binance 소켓 거부와 네트워크 허용 환경 재검증을 구분하며, 데이터 수집 코드를 바꾸지 않았다고 기록한다.
- 중단 후 재사용 사실은 문서로 확인되지만 학교 Wi-Fi·사용량 제한 등 구체적 중단 원인/일시는 저장소에서 **기록 확인 불가**다.

## 확인이 필요한 사항과 한계

가상 계산은 교육/연구용이다. 손절 체결·호가/부분 체결·최소 수량·펀딩/차입·실제 강제청산을 재현하지 않는다. 허용 손실 초과 가능성을 없앤다는 보장이 아니며 실제 승률/최적 레버리지/주문을 생성하지 않는다. 관측일과 발표일/당시 빈티지의 제약은 그대로다.
