# SIGFIN 로컬 실행·저장소 운영

STEP 19 검증 환경은 **Windows / CPython 3.11.9**다. 기존 개발 환경을 덮어쓰지 않고 프로젝트 내부 별도 가상환경에서 공식 PyPI의 동일 버전을 설치해 확인했다. 사용자 DB나 `.env`를 시험용 자료로 사용하지 않는다.

## 설치와 실행

일반 설치는 기존 `requirements-dev.txt`를 사용한다. 검증 당시의 Windows/3.11 패키지 조합이 필요하면 신규 `requirements-lock.txt`를 사용한다. 후자는 90개 배포판의 정확한 버전이며 운영체제가 다른 환경의 잠금 파일이라고 가정하지 않는다. pip/setuptools 자체의 버전 고정이나 패키지 파일 해시 검증은 포함하지 않는다.

```powershell
python -m venv .pytest_cache/step19-clean-venv
.\.pytest_cache\step19-clean-venv\Scripts\python.exe -m pip install --index-url https://pypi.org/simple -r requirements-lock.txt
.\.pytest_cache\step19-clean-venv\Scripts\python.exe -m pip check
.\.pytest_cache\step19-clean-venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1 --server.port 8531
```

위 경로는 실제 STEP 19 검증에 사용한 Git 제외 경로다. 새 환경을 다른 이름으로 만들면 Git에 포함하지 않도록 해당 경로를 제외한다. 기존 `.venv`/설정/키는 재생성하지 않는다.

API 키가 없어도 화면과 Mock 실행 경로를 사용할 수 있다. 시장 수집 실패를 임의 가격으로 대신하지 않는다. 거시경제에는 프로젝트 `.env`의 `FRED_API_KEY`, 선택적 유료 LLM에는 `OPENAI_API_KEY`와 모델 선택용 `OPENAI_MODEL`을 사용한다. 실제 키를 문서·명령 인자·로그에 넣지 않는다. 키 발급/결제 설정은 사용자 작업이며 Live는 명시적 버튼과 비용 승인이 필요하다. 키가 있어도 이번 검증에서 유료 API를 호출하지 않았다.

## 포트·네트워크 오류

- 사용 중인 서버를 종료하거나 기존 포트를 빼앗지 말고 다른 사용 가능한 로컬 포트를 지정한다. `http://127.0.0.1:8531/_stcore/health`가 200이어도 시장 API 성공이나 전체 UI 검증을 의미하지 않는다.
- STEP 19 실행에서 8519/8520은 Streamlit의 `Port ... is not available`이 발생했고, 8520 직접 bind는 **WinError 10013**이었다. 정확한 OS 예약/보안 원인은 확정하지 않았고 보안 설정은 바꾸지 않았다. bind 가능한 8531에서 새 환경의 서버 시작·health 200을 확인했다.
- DNS, 타임아웃, HTTP/접근 제한, 로컬 소켓 거부는 구분한다. 기존 Binance 오류 진단 UI와 [연결 문서](binance_connection.md)를 따른다. 프록시/인증서/방화벽을 임의 변경하거나 제한을 우회하지 않는다.
- PyPI 설치도 최초 샌드박스 연결에서 10013으로 실패했다. 승인된 재시도에서 공식 PyPI 설치가 완료됐다. 연결 실패 시의 `No matching distribution`만으로 버전 부재라고 판단하지 않는다.

## SQLite 백업·복원

기본 원본은 프로젝트 내부 `data/sigfin.sqlite3`다. 로컬 단일 사용자용이며 OneDrive 동기화 중 다중 서버의 동시 쓰기는 피한다. 새 스키마/손상 오류가 발생해도 파일을 삭제하거나 빈 계좌로 자동 초기화하지 않는다.

1. 활성 서버를 정상 종료하고 다른 SIGFIN 프로세스가 쓰지 않는지 확인한다.
2. 원본과 다른 파일명/폴더에 백업한다. 닫힌 DB는 파일 복사가 가능하며, 열린 DB를 무작정 파일 복사하면 저널/WAL과 불일치할 수 있다. 활성 연결의 일관된 백업에는 Python 표준 SQLite `Connection.backup()`을 사용한다.
3. 우선 **별도 프로젝트 복사본의 임시 DB**에 복원하고 `LocalStore(복원 경로).load_account()`·`load_analysis()`로 스키마/해시/원장/근거를 검증한다. 새 연결/앱 세션에서 거래 수량·현금·저장 보고서를 대조한다.
4. 원본 교체가 필요하면 먼저 원본도 별도 보존하고, 서버 종료와 사용자 확인 후 처리한다. 오류를 숨기기 위해 원본을 덮어쓰거나 삭제하지 않는다.

STEP 19 자동 시험은 `tmp_path`의 연구 DB를 표준 `backup()`으로 복사하고 새 연결에서 원장·평가·리포트를 복원했다. **사용자 원본 DB 백업/교체 작업을 실행한 것은 아니다.** SHA-256은 손상 탐지용이며 저장소 인증/암호화가 아니다. 파일에 대한 OS 접근권한은 별도 운영 책임이다.

## 검증과 한도

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest -q --tb=short -ra
```

외부 API 테스트는 기본 opt-in이다. 미실행 14개를 통과로 합산하지 않는다. 유료 LLM은 자동 시험에서 실제 호출하지 않는다. 새 환경의 특정 시험도 `.pytest_cache/step19-clean-venv/Scripts/python.exe -m pytest ...`로 실행했다. 결과와 실제 범위는 [STEP 19](steps/STEP_19.md)에 기록한다.

시장 캐시는 60초/최대 50개, 기본 시장 수집은 최대 5,000봉·요청 7회이며 기존 설정을 재사용한다. 지표 워밍업 최대 300봉과 저장 표 최대 5,300행은 구분한다. 실험은 전략 2~6개, 시간순 구간 포함 최대 36회다. 입력/저장 JSON 검증 한도는 8,000,000바이트이며 원장 업로드는 별도 1,000,000바이트를 검사한다.

최대 조합의 **합산 결과**도 8MB 검증 한도를 넘을 수 있다. 이때 표·곡선·원장은 유지하되 결과 SHA-256/Markdown은 N/A이고 입력 JSON만 내려받을 수 있다. 이미 저장 기준 해시가 있으면 보존한다. 전략 수/조회 구간을 줄여 재실행하고, 검증할 수 없는 결과를 해시 일치로 표시하지 않는다. 기존 해시 형식과 계산 소스는 유지했다.

Mock는 실제 LLM 협업이나 미래 수익 검증이 아니다. 실제 브라우저 최종 수용·실제 Binance/FRED 통합·실 계정 LLM·타 운영체제/다중 사용자 배포는 자동 시험 통과와 구분한다.
