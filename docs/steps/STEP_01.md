# STEP 01 — Streamlit 대시보드 기반 구축

## 근거와 단계 경계

- 대응 커밋: `9fc1555` — `feat: complete step 1 Streamlit dashboard setup`.
- Git 커밋 기록 시각: **2026-10-14 22:14:32 +09:00**. 실제 개발 시작/완료 시각은 **기록 확인 불가**.
- 확인 기준: `git show 9fc1555:app.py`와 이 커밋의 신규 파일. 현재 `app.py`에는 STEP 2~13 기능도 있으므로 최초 화면과 구분한다.
- 사용자 설명에 따르면 Codex는 STEP 3~4 무렵부터 사용했다. STEP 1을 Codex로 개발했다고 기록하지 않는다. 정확한 도구 사용 이력은 **기록 확인 불가**.

## 개발 목표

시장 설정과 분석 결과를 배치할 Streamlit 화면 및 모듈별 프로젝트 구조를 마련한다. 목표는 최초 커밋의 화면/주석으로 재구성했으며 원본 기획서 전문은 **기록 확인 불가**다.

## 실제 구현 기능

- wide 레이아웃과 펼쳐진 사이드바, 밝은 배경의 카드/CSS 적용.
- BTC/USDT·ETH/USDT, 5m·15m·1h·4h·1d, 1주·1개월·3개월·6개월·1년 선택 UI.
- RSI/MACD/EMA 및 CPI/FED Rate/NFP 체크박스.
- MARKET CHART 안내 영역, RSI/MACD/AI SIGNAL의 `-` 카드.
- Technical/Macro/Bull/Bear/Risk Manager 상태 카드.
- AI 분석 실행 버튼은 STEP 1 안내 메시지를 표시한다. 실제 AI 분석이 아니다.
- `collectors`, `indicators`, `models`, `services`, `agents`, `graph`, `backtest`, `config`, `ui`의 초기 패키지 구조와 `.gitignore`, `requirements.txt` 추가.

이 단계의 데이터 차트와 Agent 카드는 연결 전 UI다. 실제 시세 수집·지표 계산·전략·백테스트·LLM 실행은 후속 단계다.

## 사용 기술

Python, Streamlit, HTML/CSS, Python 패키지 구조, Git. Python 3.11 가상환경을 구축한 구체적인 명령/시각은 최초 커밋에서 **기록 확인 불가**다. 후속 검증 문서에는 Python 3.11 사용 기록이 있다.

## 주요 관련 파일

| 파일 | 확인 내용 |
| --- | --- |
| [app.py](../../app.py) | 최초 커밋 스냅샷의 화면·체크박스·버튼 안내 |
| [config/settings.py](../../config/settings.py) | 당시 버전 0.1.0, 종목/봉 기본값 |
| [ui/styles.py](../../ui/styles.py) | 공통 CSS 적용 |
| [ui/components.py](../../ui/components.py) | `metric_card`, `agent_status` |
| [requirements.txt](../../requirements.txt), [.gitignore](../../.gitignore) | 의존성 목록과 제외 규칙의 초기 형태 |

현재 파일 링크는 HEAD를 가리킨다. 최초 내용은 `git show 9fc1555:<파일>`로 확인한다.

## 확인 가능한 테스트 결과

- 최초 커밋에는 `tests/` 파일이 없다. 당시 자동 테스트 실행·통과 수는 **기록 확인 불가**.
- Streamlit 실행 화면/브라우저 점검의 당시 원본 로그·스크린샷·실행 주소는 **기록 확인 불가**.
- 후속 [tests/test_app.py](../../tests/test_app.py)에 화면 회귀 테스트가 있으나 이를 STEP 1 당시 통과 결과로 소급하지 않는다.
- 이번 문서 정리에서는 테스트와 서버를 실행하지 않았다.

## 오류 및 해결 기록

- 당시 실제 발생 오류, 재현 절차, 수정 전후 비교, 해결 날짜: **기록 확인 불가**.
- 안내 카드와 `-` 값은 최초 소스에 명시된 미연결 상태다. 데이터 수집 실패나 AI 실행 오류가 발생했다는 증거로 해석하지 않는다.

## 확인이 필요한 사항

초기 기획서, 환경 구축 로그, 수동 화면 확인 자료가 있으면 보완할 수 있다. 프로젝트 명칭 SIGFIN과 최초 UI의 `Crypto Multi-Agent Research System` 사이의 변경 이력은 **기록 확인 불가**다.
