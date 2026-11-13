"""정량 규칙 기반 연구용 위험 등급/행동. 방향 예측·승률·주문은 생성하지 않는다."""

from config.risk_settings import RISK_BOUNDS
from risk.models import RiskInput, RiskReport, RiskDataError
from risk.position import simulate_position

LEVELS = ("LOW", "MODERATE", "HIGH", "CRITICAL")
LIMITATIONS = [
    "규칙 기반 Mock Risk Manager이며 실제 LLM 판단·주문·포지션 실행이 아닙니다.",
    "임계값은 연구용 초기 가정이며 실증 검증된 최적 투자 기준이 아닙니다.",
    "수익률 표본 표준편차(ddof=1)를 사용합니다. 연율화는 365일·24시간 거래와 제곱근 시간 가정이며 예측값이 아닙니다.",
    "MDD는 조회 구간의 확정 종가만 사용합니다. 봉 내부 최대 손실을 재현하지 않습니다.",
    "ATR은 연속 구간 첫 14개 TR 평균으로 초기화한 Wilder 방식입니다. 갭 뒤에는 다시 초기화합니다.",
    "가상 손절 기준은 현재 확정 종가입니다. 기존 백테스트의 진입 체결가 기준 손절과 다른 정적 시나리오이며 백테스트를 변경하지 않습니다.",
    "왕복 수수료/불리한 슬리피지는 가정값입니다. 손절 체결을 보장하지 않으며 갭·유동성 부족으로 손실 한도를 넘을 수 있습니다.",
    "레버리지는 증거금 대비 명목 노출입니다. 펀딩/차입비·유지증거금·거래소 강제청산·최소 주문 단위는 모델링하지 않습니다.",
    "FRED 관측일은 발표일이 아닙니다. 발표 시각·당시 빈티지가 미검증인 현재 개정값과 사후 상관관계는 과거 당시의 거래 판단/백테스트 근거가 아닙니다.",
    "REVIEW/HOLD/ABSTAIN은 내부 연구 상태이며 실제 투자 실행 지시가 아닙니다.",
]


def classify_risk(metrics, thresholds, *, analysis_valid=True, technical_sufficient=True):
    issues = list(metrics.quality_issues)
    if not analysis_valid or not technical_sufficient:
        issues.append("필수 분석 결과 검증 실패 또는 핵심 기술지표 부족")
    if metrics.return_count < thresholds.min_returns:
        issues.append("등급 평가 최소 수익률 표본 미충족")
    if any(getattr(metrics, key) is None for key in RISK_BOUNDS):
        issues.append("변동성/MDD/ATR 필수 정량 지표 부족")
    if issues:
        return "UNKNOWN", list(dict.fromkeys(issues))
    grades, basis = [], []
    for name in RISK_BOUNDS:
        value, bounds = getattr(metrics, name), getattr(thresholds, name)
        if value < 0:
            raise RiskDataError("위험 지표는 음수일 수 없습니다.")
        rank = sum(value >= boundary for boundary in bounds)
        grades.append(rank)
        basis.append(f"{name}={value:.6g}% / 경계 {bounds}: {LEVELS[rank]}")
    if metrics.volatility_expanding:
        grades.append(2)
        basis.append(f"최근/직전 수익률 변동성 확대: 기준 {thresholds.expansion_ratio:g}배 이상 또는 직전 0에서 증가 → 최소 HIGH")
    if metrics.volume_spike:
        grades.append(1)
        basis.append(f"현재/직전 거래량 평균 배수: 기준 {thresholds.volume_spike_ratio:g}배 이상 → 최소 MODERATE")
    return LEVELS[max(grades)], basis


def evaluate_risk(value):
    value = RiskInput.model_validate(value)
    metrics, policy = value.quantitative_metrics, value.thresholds
    level, basis = classify_risk(metrics, policy, analysis_valid=value.required_analysis_valid,
                                 technical_sufficient=value.technical_sufficient)
    assessment = simulate_position(metrics.latest_close, value.position_settings) if metrics.latest_close is not None else None
    factors = [*metrics.quality_issues]
    if metrics.volatility_expanding:
        factors.append("최근 연속 구간의 변동성이 직전 구간보다 확대되었습니다.")
    if metrics.volume_spike:
        factors.append("거래량 급증이 관측되었습니다. 가격 방향이나 실제 체결 유동성을 보장하지 않습니다.")
    if metrics.stale:
        factors.append("자료 마감이 두 봉 이상 과거입니다. 이 보고서는 해당 과거 구간 평가이며 현재 시장과 다를 수 있습니다.")
    factors.extend("하락 관측(미래 지속 보장 아님): "+text for text in value.downward_observations)
    factors.extend(value.debate_conflicts)
    if value.macro_status != "CONTEXT_ONLY":
        factors.append("Macro 자료 부족" if value.macro_status == "INSUFFICIENT_DATA" else "Macro 입력/실행 검증 실패")
    exceeded = assessment.full_exposure_limit_exceeded if assessment is not None else False
    if exceeded:
        factors.append("원금×레버리지 전체 노출 가정의 손절+왕복비용 손실이 허용 손실을 초과합니다. 별도의 제한 규모와 구분합니다.")
    if level in ("UNKNOWN", "CRITICAL") or assessment is None or assessment.cost_aware_quantity == 0:
        action = "ABSTAIN"
    elif exceeded or level == "HIGH" or metrics.stale or value.debate_conflicts:
        action = "HOLD"
    else:
        action = "REVIEW"
    summary = f"Mock Risk Manager 위험 등급 {level}, 내부 연구 상태 {action}. 위험 등급과 상승/하락 방향은 다른 개념입니다."
    if level == "UNKNOWN":
        factors.append("필수 자료/품질 요건이 부족하여 정상 위험 등급을 부여하지 않습니다.")
    ids = [item.reference_id for item in value.evidence]+value.debate_claim_ids
    if len(ids) != len(set(ids)):
        raise RiskDataError("중복 위험 근거 ID가 있습니다.")
    return RiskReport(symbol=value.symbol, timeframe=value.timeframe, data_as_of_utc=value.data_as_of_utc,
        risk_level=level, quantitative_metrics=metrics, risk_factors=list(dict.fromkeys(factors)), supporting_evidence_ids=ids,
        evidence=value.evidence, debate_claim_ids=value.debate_claim_ids, position_settings=value.position_settings, thresholds=policy,
        grade_basis=basis, position_assessment=assessment, estimated_loss=assessment.adjusted_max_loss if assessment else None,
        estimated_cost=assessment.adjusted_max_cost if assessment else None, risk_limit_exceeded=exceeded, trading_action=action,
        limitations=LIMITATIONS, risk_summary=summary, validation_status="INSUFFICIENT_DATA" if level == "UNKNOWN" else "VALIDATED")


def validate_risk_report(report, original):
    report = RiskReport.model_validate(report)
    if report != evaluate_risk(original):
        raise RiskDataError("위험 등급·근거 ID·값·시각·비용·연구 상태 검증에 실패했습니다.")
    return report
