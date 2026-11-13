"""STEP 13 연구용 초기 기준. 실증 검증된 최적 기준/투자 추천이 아니다."""

RISK_POLICY_VERSION = "step13-research-v1"
ANNUAL_DAYS = 365
ATR_PERIOD = 14
RISK_BOUNDS = {
    "annualized_volatility_pct": (40., 80., 150.),
    "close_mdd_pct": (10., 25., 40.),
    "atr_close_pct": (2., 5., 10.),
}
