# STEP 06 — 손절·익절·트레일링 및 Risk ON/OFF 비교

## 근거와 단계 경계

- 대응 커밋: `0ce580e` — `feat: implement risk management and backtest comparison`.
- Git 커밋 기록 시각: **2026-10-15 18:14:40 +09:00**. 실제 개발 시작/완료 시각은 **기록 확인 불가**.
- [리스크 백테스트](../risk_management.md), [Binance 연결 진단](../binance_connection.md), 당시 엔진/collector diff와 테스트를 대조했다.
- 신호 백테스트에 추가된 가격 기반 위험 청산이다. STEP 13의 Agent 위험 등급과 가상 포지션 평가와 구분한다.

## 개발 목표

기존 백테스트에 재현 가능한 손절·익절·트레일링, 청산 사유, 유동성 부족 처리 및 동일 조건 Risk OFF/ON 비교를 추가한다.

## 실제 구현 기능

- 독립 `RiskConfig`와 활성 플래그/유한 비율 검증. 기본은 모두 OFF다.
- LONG/SHORT Stop Loss·Take Profit은 슬리피지 적용 진입가 기준이며 방향에 따라 반대로 계산한다.
- Trailing Stop은 **직전 봉까지 확정된** 최고/최저가를 사용한다. 현재 봉 극값은 판단 뒤 갱신해 다음 봉부터 반영한다.
- 기존 포지션의 시가 갭은 원시 시가에 청산한다. 같은 봉 보호선/익절 동시 접촉은 손절/트레일링을 우선한다.
- 일반 손절과 트레일링 동시 접촉은 더 불리한 기준가를 택하고 동률은 일반 손절이다.
- 처리 순서는 미체결 리스크 재시도 → 시가 갭 리스크 → 신호 주문 → 봉 내부 리스크다. 같은 시가 중복 체결을 막는다.
- 진입 봉도 진입 직후 리스크를 적용하는 가정을 문서화했다. 봉 내부 고저 순서를 알았다고 처리하지 않는다.
- 봉별 직전 거래량 한도를 진입/청산이 공유한다. **부분 청산은 없다.** 전량 부족이면 전량 유지 후 다음 관측 봉 시가마다 재시도한다.
- 청산 사유 `SIGNAL_EXIT/STOP_LOSS/TAKE_PROFIT/TRAILING_STOP`, 최초 트리거·재시도·비용·실제 가상 체결가를 기록한다.
- 정확한 봉 내부 체결 시각은 NaT로 두고 봉 시작 위치를 별도 필드/마커에 표시한다.
- 동일 설정/데이터로 OFF/ON 독립 실행, 6개 성과 지표와 두 Equity Curve 비교.
- 비활성 비율 변경은 결과에 영향을 주지 않는다. Risk OFF의 STEP 5 호환 기준을 유지한다.
- Binance 오류를 네트워크 권한·DNS·타임아웃·HTTP/제한·검증 오류로 분류하는 진단 도구/화면을 추가했다.

## 사용 기술

Python, pandas, 기존 백테스트 회계/전략 평가, dataclasses, Streamlit, Plotly, ccxt/requests/socket 기반 공개 연결 진단, pytest.

## 주요 관련 파일

| 파일 | 구현 근거 |
| --- | --- |
| [backtest/risk.py](../../backtest/risk.py), [backtest/models.py](../../backtest/models.py), [backtest/engine.py](../../backtest/engine.py) | 위험 기준·우선순위·미체결 재시도 |
| [ui/backtest_view.py](../../ui/backtest_view.py), [ui/backtest_charts.py](../../ui/backtest_charts.py), [ui/charts.py](../../ui/charts.py) | Risk 설정·비교·청산 마커 |
| [collectors/errors.py](../../collectors/errors.py), [collectors/diagnostics.py](../../collectors/diagnostics.py), [collectors/binance.py](../../collectors/binance.py) | 오류 분류·안전한 진단 |
| [tests/test_risk.py](../../tests/test_risk.py), [tests/test_risk_ui.py](../../tests/test_risk_ui.py), [tests/test_risk_live.py](../../tests/test_risk_live.py), [tests/test_connection_errors.py](../../tests/test_connection_errors.py) | 가격/유동성·UI·실 API 선택·분류 검증 |
| [tests/fixtures/step5_risk_off.json](../../tests/fixtures/step5_risk_off.json), [requirements-dev.txt](../../requirements-dev.txt) | STEP 5 고정 결과 10개와 개발 테스트 의존성 |

## 확인 가능한 테스트 결과

- 소스에 LONG/SHORT 손절·익절·트레일링, 갭/동시 접촉, 진입 봉, 신호 충돌, 거래량 거절/재시도, 비용/회계, 인과적 계산 및 OFF 기준 비교 테스트가 있다.
- 당시 전체 pytest 통과 수·원본 실행 로그는 **기록 확인 불가**. 테스트 파일·실행 명령과 후속 누적 회귀 기록은 확인된다.
- `binance_connection.md`는 **2026-10-15** Python 3.11.9/ccxt 4.5.88에서 같은 코드/공식 주소를 비교한 기록을 담고 있다.
- 해당 문서에는 허용된 프로세스의 BTC/ETH 실제 데이터와 8502 WebSocket 세션, ETH Risk OFF/ON 성과/차트 확인이 기록돼 있다. 브라우저 픽셀 검수 기록은 아니다.
- 이번 문서 정리에서는 테스트·진단·서버·실 API를 실행하지 않았다.

## 오류 및 해결 기록

| 실제 기록 | 원인/해결 및 근거 |
| --- | --- |
| Binance `DATA ERROR` | 문서상 DNS는 성공했고 HTTP 응답 전 `PermissionError/WinError 10013`으로 소켓이 차단됐다. DNS/403·451/데이터 검증 오류와 구분했다. |
| 실행 환경 차이 | 네트워크 허용 프로세스에서는 ping/time HTTP 200과 BTC/ETH 수집이 성공했다. 제한된 서버 새로고침 대신 허용된 환경의 새 서버로 검증했다. |
| 소스 변경 범위 | 단계 커밋은 collector에 `ErrorDetails`·`diagnose_api_error` 연결을 추가했다. 캐시의 `services/market_data.py`는 이 커밋에서 바뀌지 않았다. |

기존 진단 문서의 “collectors 및 캐시 변경 없음” 문구를 단계 커밋 전체에 그대로 적용하지 않는다. Git diff에는 위 collector 진단 변경이 있으므로 **리스크 구현 커밋의 변경**과 **실행 환경 문제 해결**을 구분한다. 회계 규칙을 바꾸어 네트워크 오류를 해결한 것으로 기록하지 않는다.

## 확인이 필요한 사항과 한계

증상별 최초 발생 시각·원본 터미널 로그는 **기록 확인 불가**다. OHLC 내부 경로/실제 호가·부분 청산은 구현하지 않았고, 위험 관리 적용이 수익률이나 MDD 개선을 보장하지 않는다.
