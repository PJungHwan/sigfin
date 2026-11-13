# STEP 09 — 단일 LLM 시장 분석 기반

## 근거와 단계 경계

- 대응 커밋: `379bd1d` — `feat: implement LLM analysis foundation and mock research reports`.
- Git 커밋 기록 시각: **2026-10-15 20:41:54 +09:00**. 실제 개발 시작/완료 시각은 **기록 확인 불가**.
- [단일 분석 문서](../ai_analysis.md), 신규 `llm/`·service/UI·테스트와 현재 SDK 전송 경로를 대조했다.
- 현재 `llm/client.py`의 역할별 공통 전송 함수와 예산 일괄 예약 등은 STEP 11 확장이 포함된다. 최초 단계는 단일 분석 경로다.

## 개발 목표

확정 시세/지표 요약으로 한국어 구조화 리포트를 만들 수 있는 단일 OpenAI 실행 경로, 무과금 Mock, 내용 검증과 비용 통제를 구축한다.

## 실제 구현 기능

- 공식 OpenAI Python SDK의 Responses API와 Pydantic 구조화 출력. `responses.with_raw_response.parse(text_format=...)`로 파싱 전 usage도 보존한다.
- `.env`/환경변수의 `OPENAI_API_KEY`·`OPENAI_MODEL`. 키가 없어도 앱/Mock은 실행된다.
- 기본 Mock Mode, 실제 호출은 live 선택·키·명시적 비용 승인·AI 버튼·지원 모델/가격·예산 검사를 모두 거친다.
- 마지막 확정 봉 시각/종가/변화율, RSI/MACD/EMA/SMA/Bollinger/거래량, 최근 추세와 누락/수집 시각을 Python에서 요약한다. 원시 시계열 전체를 보내지 않는다.
- 조회된 Macro만 재사용하고 관측일/단위/현재 개정 스냅샷을 보존한다. Macro를 과거 방향 근거나 백테스트 입력에 사용하지 않는다.
- `BULLISH/BEARISH/NEUTRAL/INSUFFICIENT_DATA`, 요약·상승/하락/위험·근거·한계·결론 스키마.
- 원본 ID·수치·시각·출처와 허용 한국어 문장 카탈로그를 대조한다. 새 숫자/사건·지원되지 않는 방향/문장·검증되지 않은 승률을 거부한다.
- 입력 본문 최대 24,000 UTF-8바이트, 출력 기본 3,000/최대 5,000토큰, 타임아웃·제한적 재시도.
- 기본 세션 5 HTTP 시도/$0.25 상한, 60초 중복 방지, 사용량/예약/추정 비용 기록. 실패의 과금을 무료로 단정하지 않는다.
- 모델/입력/설정 변경 시 결과·승인 무효화. AI SIGNAL과 단일 한국어 리포트/사용량/오류 UI 연결.

## 사용 기술

Python, 공식 OpenAI SDK, Responses API, Pydantic, httpx, python-dotenv, Streamlit. 기존 문서의 검증 버전은 OpenAI SDK 2.54.0/Pydantic 2.14.1이다.

저장소의 기본 모델은 `gpt-5.4-mini-2026-03-17`이며 별칭과 스냅샷 허용 목록/가격 확인일을 설정으로 관리한다. **이 문서는 해당 설정과 과거 기록을 설명하며 현재 모델 가용성·요금을 새로 검증한 문서가 아니다.**

## 주요 관련 파일

| 파일 | 구현 근거 |
| --- | --- |
| [llm/client.py](../../llm/client.py), [llm/config.py](../../llm/config.py) | 유일한 SDK 전송 경로·키/모델·안전한 오류 |
| [llm/schemas.py](../../llm/schemas.py), [llm/prompts.py](../../llm/prompts.py), [llm/validators.py](../../llm/validators.py) | 스키마·한국어 계약·근거/문장 검증 |
| [llm/budget.py](../../llm/budget.py), [config/llm_settings.py](../../config/llm_settings.py) | 예약·중복·요청/비용 상한·가격 정책 |
| [services/ai_inputs.py](../../services/ai_inputs.py), [services/ai_analysis_service.py](../../services/ai_analysis_service.py), [ui/ai_analysis_view.py](../../ui/ai_analysis_view.py) | 입력 요약·실행 게이트·결과/카드 |
| [tests/test_ai_analysis.py](../../tests/test_ai_analysis.py), [tests/test_ai_client.py](../../tests/test_ai_client.py), [tests/test_ai_ui.py](../../tests/test_ai_ui.py) | 계산·SDK MockTransport·형식/내용·비용·UI |

## 확인 가능한 테스트 결과

[기존 문서](../ai_analysis.md)의 **2026-10-15** 기록:

- 전체 **312 passed, 14 skipped**, 하위 검증 **314개 통과**. 신규 테스트 39개, 기존 273개 회귀 포함.
- 공식 SDK 정상/인증/한도/타임아웃/출력 오류는 `httpx.MockTransport`로 검증했고 **유료 OpenAI 호출 0회**다.
- Python 소스 83개 구문/컴파일, 별도 import, `pip check` 통과 기록.
- 8503 health 200/WebSocket 세션에서 실제 시장 차트와 버튼의 Mock 리포트/AI SIGNAL을 확인한 기록.
- 당시 원본 로그의 별도 보존 위치는 **기록 확인 불가**. 이번 문서 작업에서는 테스트/서버/API를 실행하지 않았다.

## 오류 및 해결 기록

- 인증·권한·Rate Limit·네트워크·타임아웃·거부/미완료·스키마·근거 오류를 분리하는 구현/모의 테스트는 확인된다.
- 이것이 실제 OpenAI 장애를 경험하고 해결했다는 증거는 아니다. 실제 계정 오류·수정 경위·해결 시각은 **기록 확인 불가**.
- 실제 유료 호출을 승인받지 않아 수행하지 않았다는 기존 문서의 제한을 유지한다. Mock 성공을 실 API 성공으로 기록하지 않는다.

## 확인이 필요한 사항과 한계

실제 계정 모델 접근·결제·청구액·LLM 응답 품질은 미검증이다. 세션 예산은 계정 전체 결제 한도가 아니며 가격 만료 시 요청을 차단한다. 검증은 입력 정합성 검사이고 예측 성능/사실성의 완전한 보장은 아니다.
