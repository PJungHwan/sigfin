"""원본 분석/시장 데이터를 다시 검증하고 리스크 제약을 최종 보고서까지 유지한다."""

from langgraph.runtime import Runtime

from graph.risk_state import RiskState, RiskContext, RiskWorkflowReport, RiskDebateReport
from graph.nodes import tracked, aggregate, format_report
from graph.validation import WorkflowValidationError
from risk.metrics import calculate_risk_metrics
from risk.models import RiskInput, RiskEvidence, RiskDataError
from risk.evaluator import validate_risk_report


def risk_tracked(name, function):
    recorded = tracked(name, function)
    def node(state: RiskState, runtime: Runtime[RiskContext]):
        return recorded(state, runtime)
    return node


def build_risk_input(state, context):
    aggregate(state, context)  # Technical/Macro의 완료 및 원본 근거를 재검증한다.
    metrics = calculate_risk_metrics(context.market_data, state["timeframe"], now=context.requested_at_utc,
                                     thresholds=context.risk_thresholds)
    if metrics.data_as_of_utc != state["data_as_of_utc"]:
        raise RiskDataError("가격 자료와 위험 지표의 기준 시각이 다릅니다.")
    snapshot = state["input_data"]
    close_fact = next(item for item in snapshot.facts if item.metric_name == "close" and item.source_type == "TECHNICAL")
    if metrics.latest_close != close_fact.metric_value:
        raise RiskDataError("기술 분석과 위험 지표의 확정 종가가 다릅니다.")
    technical, macro = state.get("technical_result"), state.get("macro_result")
    source_facts = {fact.reference_id: fact for fact in [*snapshot.facts, *state["macro_input"]]}
    citations = [*(technical.evidence if technical else []), *(macro.macro_evidence if macro else [])]
    evidence = [RiskEvidence(**item.model_dump(exclude={"explanation"}), unit=source_facts[item.reference_id].unit) for item in citations]
    for name, unit in (("volatility_per_bar_pct", "%"), ("annualized_volatility_pct", "%"), ("close_mdd_pct", "%"),
                        ("atr_14", "USDT"), ("atr_close_pct", "%"), ("volume_ratio", "배"), ("volatility_expansion_ratio", "배")):
        value = getattr(metrics, name)
        if value is not None:
            evidence.append(RiskEvidence(reference_id="risk:"+name, source_type="RISK_QUANTITATIVE", metric_name=name,
                metric_value=value, unit=unit, observation_time=state["data_as_of_utc"]))
    for name, value in context.risk_settings.model_dump().items():
        evidence.append(RiskEvidence(reference_id="setting:"+name, source_type="SETTING", metric_name=name,
            metric_value=value, unit="USDT" if name == "capital" else "배" if name == "leverage" else "비율",
            observation_time=state["data_as_of_utc"]))
    valid = technical is not None and macro is not None
    debate_enabled = state.get("debate_enabled", False)
    evaluation = state.get("debate_evaluation")
    if debate_enabled:
        from graph.debate_nodes import evaluate_debate
        if evaluation is not None and evaluation != evaluate_debate(state):
            raise RiskDataError("Debate Evaluator 결과가 검증된 기록과 다릅니다.")
        valid = valid and evaluation is not None and evaluation.evaluation_status == "COMPLETED"
    return RiskInput(symbol=state["symbol"], timeframe=state["timeframe"], data_as_of_utc=state["data_as_of_utc"],
        quantitative_metrics=metrics, position_settings=context.risk_settings, thresholds=context.risk_thresholds,
        technical_sufficient=bool(snapshot.sufficient and technical is not None), required_analysis_valid=bool(valid),
        technical_signal=technical.technical_signal if technical else "INSUFFICIENT_DATA",
        technical_summary=technical.technical_summary if technical else "Technical 분석 실패",
        downward_observations=technical.bearish_factors if technical else [], debate_enabled=debate_enabled,
        debate_status=evaluation.evaluation_status if evaluation else None, debate_conflicts=evaluation.unresolved_conflicts if evaluation else [],
        debate_summary=evaluation.debate_summary if evaluation else None, debate_claim_ids=evaluation.validated_message_ids if evaluation else [],
        macro_status=macro.macro_status if macro else "FAILED", macro_summary=macro.macro_summary if macro else "Macro 분석 실패", evidence=evidence)


def risk_node(agent):
    def analyze(state, context):
        try:
            inputs = build_risk_input(state, context)
            report = validate_risk_report(agent.analyze(inputs.model_copy(deep=True)), inputs)
        except RiskDataError:
            raise WorkflowValidationError("Risk Manager 내용 검증 실패") from None
        return {"risk_input": inputs, "risk_result": report, "risk_manager_status": "COMPLETED"}
    return risk_tracked("risk_manager", analyze)


def aggregate_risk(state, context):
    if state.get("debate_enabled"):
        from graph.debate_nodes import aggregate_debate
        base = aggregate_debate(state, context)["aggregation_result"]
    else:
        base = aggregate(state, context)["aggregation_result"]
    report = state.get("risk_result")
    status = "COMPLETED" if report is not None else "FAILED"
    if report is not None:
        try:
            report = validate_risk_report(report, build_risk_input(state, context))
        except RiskDataError:
            raise WorkflowValidationError("위험 평가 재검증 실패") from None
    action = report.trading_action if report is not None else "ABSTAIN"
    changes = {"summary": base.summary+" "+(report.risk_summary if report else "Risk Manager 실패로 내부 연구 상태 ABSTAIN을 유지합니다.")}
    changes["risk_factors"] = list(dict.fromkeys([*base.risk_factors, *(report.risk_factors if report else ["Risk Manager 실행/검증 실패 · 정상 위험 평가 없음"])]))
    conflicts = list(base.conflicts)
    if action != "REVIEW":
        conflicts.append(f"시장 방향 의견 {base.final_signal}와 위험 검토 상태 {action}를 구분합니다. 위험 제약을 해제하거나 실제 진입으로 연결하지 않습니다.")
    changes["conflicts"] = conflicts
    if report is not None:
        changes["limitations"] = list(dict.fromkeys([*base.limitations, *report.limitations]))
    if (report is None or report.validation_status != "VALIDATED") and base.status != "FAILED":
        changes["status"] = "PARTIAL"
    return {"aggregation_result": base.model_copy(update=changes), "research_action": action,
            "risk_manager_status": status, "pre_risk_signal": base.final_signal}


def format_risk_report(state, context):
    if state.get("debate_enabled"):
        from graph.debate_nodes import format_debate_report
        result = format_debate_report(state, context)
        schema = RiskDebateReport
    else:
        result, schema = format_report(state, context), RiskWorkflowReport
    report = result["final_report"]
    if report is not None:
        # 시장 방향과 연구 보류 상태를 별도 필드로 유지한다.
        result["final_report"] = schema(**report.model_dump(), risk_report=state.get("risk_result"),
            research_action=state["research_action"], risk_manager_status=state["risk_manager_status"],
            pre_risk_signal=state["pre_risk_signal"])
    return result
