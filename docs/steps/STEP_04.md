# STEP 04 — 사용자 정의 전략 빌더와 조건 평가

## 근거와 단계 경계

- 대응 커밋: `8ba8f18` — `feat: implement strategy builder and signal evaluation`.
- Git 커밋 기록 시각: **2026-10-14 23:50:54 +09:00**. 실제 개발 시작/완료 시각은 **기록 확인 불가**.
- [전략 빌더 문서](../strategy_builder.md), 당시 신규 `strategies/`와 UI/테스트, 현재 평가 함수를 대조했다.
- Codex는 사용자 설명상 STEP 3~4 무렵부터 사용했다. 정확한 시작 단계·시각과 단계별 기여도는 **기록 확인 불가**다.

## 개발 목표

사용자가 LONG/SHORT 조건을 구조화해 조립·저장하고 실제 확정 지표 DataFrame에서 조건 충족 여부를 계산한다. 신호 미리보기와 실제 체결/수익률을 구분한다.

## 실제 구현 기능

- 불변 dataclass/Enum의 `Operand`, `Condition`, `ConditionGroup`, `Strategy`와 스키마 버전 1.
- `>`, `<`, `>=`, `<=`, Cross Up, Cross Down; 숫자 또는 같은 단위의 호환 지표 비교.
- RSI 0~100, 유한 숫자, 가격/거래량 범위, 중복 ID, 비교 단위·자기 비교 등의 명시적 검증. `eval`을 사용하지 않는다.
- 한 방향의 조건은 모두 AND 또는 모두 OR, 최대 20행. 빈 그룹은 신호 False다.
- Cross Up은 직전 좌측≤우측 및 현재 좌측>우측, Cross Down은 반대다. 직전/현재 값과 봉 연속성만 사용한다.
- NaN/비유한 비교는 False. 미확정·미래 봉은 오류다. 원본 DataFrame을 변경하지 않는다.
- LONG/SHORT Boolean Series, 조건별 충족/유효성 표, 동시 충족 `CONFLICT`를 반환한다.
- 조건 추가/삭제, 한국어 미리보기, 검증 결과, JSON 업로드/다운로드.
- 64KB 한도, 중복 JSON 키·알 수 없는 필드/버전 차단. 서버 파일 경로로 업로드 이름을 사용하지 않는다.
- 신호 개수·최근 UTC 마감 시각·최근 상세 표와 Plotly LONG/SHORT/충돌 마커.

이 단계의 마커는 조건 충족 시점이다. 같은 봉 종가 체결·실제 주문·백테스트 수익률을 계산하지 않는다. 중첩 조건 그룹은 아직 지원하지 않는다.

## 사용 기술

Python dataclasses/Enum/JSON, pandas, NumPy, Streamlit, Plotly. 저장은 브라우저 다운로드/업로드 방식이다.

## 주요 관련 파일

| 파일 | 구현 근거 |
| --- | --- |
| [strategies/models.py](../../strategies/models.py) | 타입·단위·조건/그룹/전략 검증 |
| [strategies/evaluator.py](../../strategies/evaluator.py) | `_condition`, `evaluate_condition`, `evaluate_strategy` |
| [strategies/storage.py](../../strategies/storage.py) | JSON 변환·검증·안전한 다운로드 이름 |
| [ui/strategy_builder.py](../../ui/strategy_builder.py), [ui/strategy_signals.py](../../ui/strategy_signals.py) | 편집·복원·조건별 신호 미리보기 |
| [ui/charts.py](../../ui/charts.py), [app.py](../../app.py) | 신호 차트 연결 |
| [tests/test_strategies.py](../../tests/test_strategies.py), [tests/test_strategy_storage.py](../../tests/test_strategy_storage.py), [tests/test_strategy_builder.py](../../tests/test_strategy_builder.py), [tests/test_strategy_live.py](../../tests/test_strategy_live.py) | 계산·JSON·UI·선택적 실 API 검사 |

## 확인 가능한 테스트 결과

- 숫자/교차 경계, NaN, AND/OR, 충돌, 미확정/미래 봉, 불변성, JSON 차단/복원, UI 상태 검증 코드가 커밋에 있다.
- 당시 전체 통과 수·실행일·원본 로그는 **기록 확인 불가**. 기존 문서는 실행 방법과 테스트 범위를 설명하며 통과 수를 제시하지 않는다.
- 브라우저의 파일 선택/다운로드 대화상자 직접 조작은 기존 문서가 AppTest 범위 밖으로 명시한다.
- 이번 문서 정리에서는 테스트/실 API를 실행하지 않았다.

## 오류 및 해결 기록

- 실제 발생한 개발 오류와 해결 시각: **기록 확인 불가**.
- 잘못된 JSON은 현재 전략을 보존한 채 오류를 표시하고, 알 수 없는 비교/비호환 지표는 차단한다. 이는 소스로 확인한 오류 처리 기능이며 과거 장애 발생 기록과 다르다.

## 확인이 필요한 사항

당시 테스트 실행 자료와 브라우저 파일 입출력 수동 점검 기록은 **기록 확인 불가**다. Downloads 폴더의 개인 전략 파일은 이번 조사 범위에 포함하지 않았다.
