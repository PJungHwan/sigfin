# SIGFIN — 암호화폐·거시경제 연구 시스템

시그핀(SIGFIN)은 BTC/ETH 시장 자료, 기술지표, 사용자 전략, 백테스트, 가상 포트폴리오와 AI 연구 결과를 검토하는 Python 졸업프로젝트다. 로컬 단일 사용자용이며 **실제 거래소 주문·계정 연결·자동매매 기능은 없다.**

STEP 1~20 기능 기준은 `4266787`, 리디자인 보호 기준은 `ccf8d63`이다. [2단계 UI 구현](docs/redesign/IMPLEMENTATION_STAGE_2.md)에서 좌측 6개 작업 화면과 SIGFIN 표시를 적용했다. 3단계 최종 사용자 검증은 별도다. 계산/저장·STEP 18 해시 호환성을 위해 `config/settings.py`와 `APP_VERSION=0.18.0`은 그대로 두고 브랜드는 UI 영역에서 처리한다. 기존 최종 기능 검수는 [STEP 20](docs/steps/STEP_20.md)·[검수표](docs/final_acceptance.md)에 보존한다.

## 주요 기능

| 화면/기능 | 실제 구현 범위 |
| --- | --- |
| 시장·기술 분석 | Binance 공개 현물 BTC/USDT·ETH/USDT 확정 OHLCV, 5m/15m/1h/4h/1d, UTC, 페이지 수집/캐시, Plotly 캔들·거래량, RSI/MACD/EMA/SMA/Bollinger/거래량 평균 |
| 전략 조립·백테스트 | 검증된 조건/교차·AND/OR, 전략 JSON, 신호 미리보기, 다음 연속 봉 시가 체결, 비용·거래량 참여 제한, 가상 LONG/SHORT, 성과·거래/체결 원장 |
| 위험관리 | 손절·익절·트레일링, Risk OFF/ON 비교, 결정론적 Mock Risk Manager의 변동성/MDD/ATR·가상 노출 평가 |
| 거시경제·통계 | FRED CPIAUCSL/FEDFUNDS/UNRATE/PAYEMS/DGS10, 단위·관측일·수집 시각, CPI YoY, 월간 Pearson/Spearman·0~3개월 사후 시차 탐색 |
| AI 연구 | 단일 LLM 분석, 실제 LangGraph의 Technical/Macro 병렬 분기, 규칙 기반 Bull/Bear Debate·Risk, 검증 근거와 한국어 통합 보고서 |
| 가상 포트폴리오 | USDT 현금·BTC/ETH 수동 가상 매수/매도, 수수료/평균 매입가·실현/미실현 손익, 공통 확정 가격 평가·비중/충격 시나리오 |
| 저장·복원 | 로컬 SQLite 원장/평가/분석 스냅샷, 명시적 저장·과거 조회, JSON/Markdown 다운로드, 무결성/중복/경합 검사 |
| 고정 실험 | 2~6전략 × Risk OFF/ON × 전체/검토/검증 구간, 동일 조건 비교·원장, JSON 재실행, SHA-256 일치/불일치/비교 불가 |

Python 3.11, pandas, NumPy, Streamlit, Plotly, ccxt, requests, SQLite(표준 라이브러리), Pydantic, OpenAI 공식 SDK, LangGraph를 사용한다. 복잡한 별도 백테스트 프레임워크 없이 기존 Python 엔진을 재사용한다.

## 설치·실행 — Windows / Python 3.11

PowerShell에서 프로젝트 폴더로 이동한다. 처음 설치할 때만 가상환경을 만들고, 기존 `.venv`나 `.env`는 덮어쓰지 않는다.

```powershell
python --version
if (-not (Test-Path .venv\Scripts\python.exe)) { python -m venv .venv }
.\.venv\Scripts\python.exe -m pip install --index-url https://pypi.org/simple -r requirements-lock.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1 --server.port 8532 --browser.gatherUsageStats false
```

첫 명령의 출력이 **Python 3.11.x**인지 확인한다. 다른 버전이면 설치된 Python 3.11 실행 파일의 경로로 `python`을 대신한다. 이번 PC는 `python`이 3.11.9였고 `py -3.11` 런처는 설치본을 찾지 못해 안내에 의존하지 않는다.

브라우저: `http://127.0.0.1:8532`. 사용 중인 포트라면 기존 서버를 종료하지 말고 빈 포트로 바꾼다. 서버 종료는 해당 터미널에서 `Ctrl+C`다. 가상환경 활성화 스크립트나 실행 정책 변경은 필요 없다.

`requirements-lock.txt`는 Windows/CPython 3.11의 검증된 90개 배포판 버전을 고정하고 개발 테스트 의존성도 포함한다. 최초 설치는 공식 PyPI 접근이 필요하다. OS별 잠금·wheel 해시·pip 자체 버전까지 보장하지 않는다. 일반 개발 범위 설치는 `requirements-dev.txt`, 실행 라이브러리만 필요하면 `requirements.txt`를 사용하지만 버전 조합이 달라질 수 있다. 기존 개발 환경을 업그레이드하기 전에 [별도 환경/운영 안내](docs/local_operation.md)를 확인한다.

### 선택적 환경변수

프로젝트 `.env`에 본인 키를 직접 설정한다. 기존 파일이 있으면 재생성하지 않는다.

- `FRED_API_KEY`: 실제 FRED 조회에 필요하다.
- `OPENAI_API_KEY`: 선택적 유료 LLM에만 필요하다.
- `OPENAI_MODEL`: 기존 지원/가격 검증 정책 안에서 모델을 선택한다.

키가 없어도 앱과 Mock 분석 경로는 실행된다. **Mock 분석에도 검증 가능한 시장 입력이 필요**하며 실제 시장 수집 실패를 임의 가격으로 채우지 않는다. 네트워크 없는 화면 시연에는 아래 별도 합성 경로를 사용한다. 실 모델 지원/요금은 설정의 확인일 기준이며 최신 상태/실계정 과금은 이번 단계에서 검증하지 않았다.

## 화면 사용 순서

1. **시장 분석**: 상단에서 종목·봉·기간을 정하고 확정 가격/UTC·실제 구간을 확인한다. 지표 설정과 전략 신호를 차트 가까이 조절한다. 표시 토글은 캐시를 재사용한다.
2. **전략·백테스트**: 전략 편집에서 LONG/SHORT 조건과 JSON을 관리한다. 공통 원금·편도 비용/거래량·손익절을 설정하고 단일 백테스트 또는 2~6전략 비교를 선택한다. 손절 `1.0`은 1%다. 2전략×Risk×3구간은 12회며 모두 OFF이면 차이 0이 정상이다. 저장 실험 재현은 현재 조회와 독립된 업로드·명시적 실행·해시 확인 경로다.
3. **포트폴리오**: 시험용 계좌·확정 가격 조회 후 확인 폼으로 수동 가상 거래를 기록한다. 위험 분석은 같은 가격을 재검증한다. -10% 손실은 보유액 기준이며 매도 비용은 별도다. 평가 이력이 적으면 MDD/변동성 N/A는 정상이다. 거래·평가 이력에서 저장·다운로드를 수행한다.
4. **거시경제**: 경제지표 선택 후 FRED 조회를 실행한다. 원본과 암호화폐·거시경제 관계를 구분하고 단위·발표 시점 부족/소표본 한계를 확인한다.
5. **AI 연구**: 통합 리포트가 기본이다. 에이전트 워크플로우와 단일 AI(고급)는 별도 실행·모드/승인을 사용하고 비용 원장만 공유한다. 기본 Mock의 근거·위험/부족·저장/다운로드를 확인한다.
6. **저장 이력**: 과거 연구 결과와 가상 계좌·평가를 조회한다. 과거 조회는 자동 재분석/원장 교체가 아니며 복원은 별도 명시 동작이다. 원본 JSON은 기본 닫힌 개발자 메뉴 안에 있다.

신호 마커는 체결 결과가 아니다. 가상 계좌는 무레버리지·공매도 없음이며 백테스트의 가상 SHORT와 구분한다. 세션 내 페이지 이동은 초안/설정을 보존하지만, 서버 재시작 후 복원은 기존 SQLite·명시적 저장 자료 기준이다.

## Mock와 실제 LLM의 차이

| 경로 | 수행/미수행 |
| --- | --- |
| 일반 앱 + Mock | 실제 확보한 입력을 사용한다. LangGraph는 실제 실행하고 에이전트는 결정론적 규칙/가상 응답을 사용한다. OpenAI 호출 없음 |
| 일반 앱 + 선택 LLM | 기존 Technical/Macro의 OpenAI Responses/Pydantic 경로. 명시적 버튼·키·비용 승인·유효 가격·동시성 예약/세션 상한 필요. 이번 개발에서 유료 호출 미실행 |
| Bull/Bear·Risk·Aggregator | Debate/Risk는 Mock 규칙, Aggregator/보고서 조립은 Python. 실제 LLM Debate/Risk 협업으로 설명하지 않는다 |
| 오프라인 합성 시연 | 아래 별도 프로세스. 가격/거시 값까지 모두 합성이다. 외부 HTTP/LLM 차단·사용자 DB 미사용 |

세션 기본 5회/$0.25 추정 비용 한도는 실제 계정 결제 한도가 아니다. 실패 요청의 과금 여부는 미확인이다. 모의 응답 시험의 통과를 실 API 응답 품질이나 투자 성공률로 소개하지 않는다.

## 네트워크 없는 시연

일반 앱과 **별도 서버 프로세스·포트**로 실행한다.

```powershell
.\.venv\Scripts\python.exe -m streamlit run demo/offline.py --server.address 127.0.0.1 --server.port 8533 --browser.gatherUsageStats false
```

`http://127.0.0.1:8533`에서 ‘SIGFIN 오프라인 합성 시연’ 경고를 확인한다. ‘정상 흐름 / 자료 부족 / 연결 실패’를 선택할 수 있다. 기존 `app.py`를 재사용하고 모든 가격/거시 값은 합성 360봉(부족 모드는 2봉)이다. 실제 요청 기간 전체의 자료라고 표시하지 않는다. 시연 DB는 `.pytest_cache/sigfin-demo/<세션 ID>/demo.sqlite3`이며 사용자 `data/sigfin.sqlite3`를 열지 않는다. 새 세션/새로고침 후 계좌가 유지되는 영구 서비스가 아니므로 필요한 결과는 다운로드한다. 실제 자료의 장기·월간 분석 성공을 이 경로로 주장할 수 없다.

[10~15분 시연 순서·발표 준비표](docs/demo_scenario.md)와 [실행/포트/DB 백업·복원](docs/local_operation.md)을 참고한다.

## 검증·제한

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest -q --tb=short -ra
```

2026-11-12 최종 전체 결과는 **646 passed / 0 failed / 14 skipped**다. 기존 STEP 1~19의 638개와 신규 시연 8개를 포함한다. 실행 과정/검증 범위는 [STEP 20](docs/steps/STEP_20.md)에 기록한다. 기본 비활성 외부 API 통합 시험 14개와 유료 LLM은 별도 미검증이며 통과에 합산하지 않는다. 최종 사용자 발표 장비의 수용 검수도 자동 통과와 구분한다.

- 백테스트는 확정 신호의 **다음 연속 봉 시가** 체결, 마지막 신호 미체결이다. OHLC만으로 봉 내부 순서를 알 수 없어 갭/손절 우선/직전 봉 극값 기반 트레일링의 명시적 가정을 사용한다. 수수료·슬리피지·유동성도 모델이며 실제 체결을 보장하지 않는다.
- 가상 SHORT는 Binance Spot 실제 주문이 아니다. 포트폴리오는 현금+BTC/ETH 양수 보유·무레버리지다. Risk Manager의 레버리지 계산은 별도 교육용 가정이다.
- FRED 관측일은 발표일이 아니며 현재 개정값은 과거 공개 빈티지가 아니다. 시점 준수 자료가 없으면 과거 거래 근거로 전달하지 않는다. 사후 상관관계는 인과·예측 성능이 아니다.
- 시장 기본 수집은 최대 5,000봉, 워밍업은 별도이며 실제 구간/제한을 표시한다. 작은 월별 표본과 결측은 N/A/불확실성으로 남긴다.
- 실험은 최대 36회다. 합산 결과가 8MB를 넘으면 표/곡선/원장·입력 JSON은 유지하고 결과 해시/Markdown은 N/A다. 입력/계산 코드가 다르면 기존 실험 재실행을 거부할 수 있다. SHA-256은 인증/암호화/성과 우수성 증명이 아니다.
- SQLite는 로컬 단일 사용자용이다. OneDrive/다중 서버 동시 쓰기를 피하고 원본을 보존한다. 외부 다중 사용자 배포·침투 테스트·다른 OS 검증은 완료 범위가 아니다.
- 과거 성과는 미래 수익을 보장하지 않는다. 과최적화·수집 한계·비용 가정을 리포트와 함께 설명한다.

## 코드·문서 위치

`collectors/` 수집 → `services/` 검증/캐시·통합 → `indicators/`, `strategies/`, `backtest/`, `analytics/`, `portfolio/`, `risk/` 계산 → `agents/`, `graph/`, `llm/` 연구 → `ui/`, `app.py` 표시. `services/local_store.py`는 명시적 로컬 저장, `demo/`는 별도 합성 시연, `tests/`는 자동 검증이다.

- [확정 로드맵](docs/ROADMAP_STEP_14_20.md) · [남은 할 일](docs/BACKLOG.md) · [변경 기록](docs/CHANGELOG.md)
- [STEP 1~20 개발 기록](docs/steps/README.md) · [최종 추적/검수](docs/final_acceptance.md)
- [백테스트 계약](docs/backtesting.md) · [위험 체결](docs/risk_management.md) · [FRED](docs/macro_data.md) · [LLM 워크플로우](docs/llm_workflow.md)
