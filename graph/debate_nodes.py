"""STEP 11 노드/검증을 재사용하는 Mock 토론 노드와 결정론적 평가/집계."""

import hashlib

from pydantic import ValidationError
from langgraph.runtime import Runtime

from agents.debate import DEBATE_LIMITATIONS
from graph.debate_state import DebateEvidence, DebateEvaluation, DebateReport, RejectedClaim, DebateState, DebateContext, DebateMessage
from graph.debate_validation import (DebateValidationError, evidence_fingerprint, validate_evidence,
                                     validate_position, validate_responses)
from graph.nodes import tracked, aggregate, format_report, validate_input
from llm.config import AIError


def check_cancel(context):
    if context.cancel_event is not None and context.cancel_event.is_set():
        raise AIError("Mock Debate 실행이 취소되었습니다.", "CANCELLED")


def debate_tracked(name, function):
    def checked(state, context):
        check_cancel(context)
        return function(state, context)
    recorded = tracked(name, checked)
    # LangGraph는 노드 입력 타입으로 읽기 채널을 제한한다. 확장 State를 명시해야 토론 필드가 전달된다.
    def node(state: DebateState, runtime: Runtime[DebateContext]):
        return recorded(state, runtime)
    return node


def prepare_evidence(state, context):
    base = aggregate(state, context)["aggregation_result"]  # 원본 Technical/Macro와 합류 완료를 재검증한다.
    snapshot = state["input_data"]
    technical, macro = state.get("technical_result"), state.get("macro_result")
    if technical is None:
        return {"debate_status": "FAILED", "_skipped": True}
    cited = {item.reference_id for item in base.evidence}
    facts = [fact for fact in [*snapshot.facts, *state["macro_input"]] if fact.reference_id in cited]
    pool = validate_evidence(DebateEvidence(symbol=state["symbol"], timeframe=state["timeframe"],
        data_as_of_utc=state["data_as_of_utc"], facts=facts, technical_signal=technical.technical_signal,
        technical_sufficient=snapshot.sufficient, technical_summary=technical.technical_summary,
        macro_summary=macro.macro_summary if macro else "Macro Agent 실행 실패: 정상 결과가 없습니다.",
        macro_status=macro.macro_status if macro else "FAILED", limitations=base.limitations))
    fingerprint = evidence_fingerprint(pool)
    if context.guard is not None:
        risk_scope = (context.risk_settings.model_dump_json()+context.risk_thresholds.model_dump_json()
                      if hasattr(context, "risk_settings") else "")
        key = hashlib.sha256(f"step12-mock:{state['input_fingerprint']}:{fingerprint}:{context.rounds}:{risk_scope}".encode()).hexdigest()
        with context.guard.lock:
            context.guard.check_duplicate(key)
    return {"debate_evidence": pool, "evidence_fingerprint": fingerprint,
            "debate_status": "RUNNING" if pool.technical_sufficient else "SKIPPED", "_skipped": not pool.technical_sufficient}


def checked_pool(state):
    pool = validate_evidence(state["debate_evidence"])
    if evidence_fingerprint(pool) != state["evidence_fingerprint"]:
        raise DebateValidationError("준비 이후 근거 변경")
    return pool


def position_node(side, agent):
    def propose(state, context):
        pool = checked_pool(state)
        result = validate_position(agent.propose(pool.model_copy(deep=True)), pool, side)
        key = "bull_result" if side == "BULL" else "bear_result"
        return {key: result, "debate_history": result.messages}
    return debate_tracked("bull_agent" if side == "BULL" else "bear_agent", propose)


def round_node(bull, bear):
    def respond(state, context):
        if context.rounds == 1:
            return {"debate_round": 1, "_skipped": True}
        pool = checked_pool(state)
        responses, errors = [], []
        # 둘 다 동일한 Round 1 스냅샷을 읽는다. 먼저 생성한 반박을 나중 응답의 입력으로 소급하지 않는다.
        for side, agent, opponent_key in (("BULL", bull, "bear_result"), ("BEAR", bear, "bull_result")):
            if context.cancel_event is not None and context.cancel_event.is_set():
                from graph.state import WorkflowError
                errors.append(WorkflowError(node="debate_round", category="CANCELLED",
                    message="토론 취소 요청으로 남은 반박을 생략하고 이미 검증한 메시지를 보존합니다."))
                break
            own_key = "bull_result" if side == "BULL" else "bear_result"
            if state.get(own_key) is None or state.get(opponent_key) is None:
                continue  # 상대/자기 노드 실패는 없는 주장을 만들어 보완하지 않는다.
            try:
                opponent = validate_position(state[opponent_key], pool, "BEAR" if side == "BULL" else "BULL")
                values = agent.respond(pool.model_copy(deep=True), opponent.model_copy(deep=True))
                responses.extend(validate_responses(values, pool, side, opponent))
            except Exception as error:
                from graph.state import WorkflowError
                category = "EVIDENCE_VALIDATION" if isinstance(error, (DebateValidationError, ValidationError)) else "NODE_ERROR"
                errors.append(WorkflowError(node="debate_round", category=category,
                    message=f"{side}의 반박 생성/검증에 실패했습니다. 해당 반박을 제외하고 다른 정상 결과를 보존합니다."))
        return {"debate_history": responses, "debate_round": 2 if responses else 1, "errors": errors,
                "_skipped": not responses and not errors, "_failed": bool(errors), "_error_type": errors[0].category if errors else None}
    return debate_tracked("debate_round", respond)


def evaluate_debate(state):
    rejected, missing, valid, addressed, verified_ids = [], [], {}, [], []
    try:
        for item in state["debate_history"]:
            DebateMessage.model_validate(item)
    except ValidationError:
        raise DebateValidationError("토론 기록 스키마 오류") from None
    pool = state.get("debate_evidence")
    # 후속 Risk Manager/보고서 오류는 이미 끝난 토론의 유효성을 바꾸지 않는다.
    # 토론까지의 오류만으로 동일한 평가를 재현하고, 후속 실패는 각 집계 단계에서 처리한다.
    errors = [error for error in state["errors"] if error.node not in
              ("risk_manager", "research_aggregator", "report_formatter")]
    if pool is not None:
        pool = checked_pool(state)
    if pool is None or not pool.technical_sufficient:
        status = "FAILED" if pool is None else "INSUFFICIENT_DATA"
        missing.append("검증된 핵심 기술 자료가 부족하거나 실패하여 방향 토론을 수행하지 않았습니다.")
    else:
        for side, key in (("BULL", "bull_result"), ("BEAR", "bear_result")):
            try:
                if state.get(key) is None:
                    raise DebateValidationError("분기 실패")
                result = validate_position(state[key], pool, side)
                recorded = sorted((item for item in state["debate_history"] if item.round == 1 and item.speaker == side), key=lambda item: item.claim_id)
                if recorded != result.messages:
                    raise DebateValidationError("최초 주장 기록 변경/누락")
                valid[side] = result
                verified_ids.extend(item.claim_id for item in result.messages)
            except (DebateValidationError, ValidationError):
                rejected.append(RejectedClaim(node="bull_agent" if side == "BULL" else "bear_agent", claim_id=None,
                    reason="해당 분기의 주장/기록이 입력 근거 또는 계약 검증에 실패하여 전체 분기를 제외했습니다."))
        for side in ("BULL", "BEAR"):
            responses = [item for item in state["debate_history"] if item.round == 2 and item.speaker == side]
            opponent = valid.get("BEAR" if side == "BULL" else "BULL")
            if state["debate_rounds"] == 2 and side in valid and opponent is not None:
                try:
                    values = validate_responses(responses, pool, side, opponent)
                    if any(item.validation_status != "VALID" for item in responses):
                        raise DebateValidationError("미검증 반박")
                    addressed.extend(item.target_claim_id for item in values if item.target_claim_id is not None)
                    verified_ids.extend(item.claim_id for item in values)
                except (DebateValidationError, ValidationError):
                    rejected.append(RejectedClaim(node="debate_round", claim_id=None,
                        reason=f"{side} 반박의 상대 주장 참조/근거/기록이 유효하지 않아 제외했습니다."))
            elif responses:
                rejected.append(RejectedClaim(node="debate_round", claim_id=None, reason="지원하지 않는 라운드 또는 실패한 분기의 반박을 제외했습니다."))
        status = "FAILED" if not valid else "PARTIAL" if rejected or errors else "COMPLETED"
    bull = [item for item in valid["BULL"].messages if item.message_type == "CLAIM"] if "BULL" in valid else []
    bear = [item for item in valid["BEAR"].messages if item.message_type == "CLAIM"] if "BEAR" in valid else []
    conflicts = []
    if bull and bear:
        conflicts.append("상승·하락 지표 관측이 공존합니다. 반박은 관측값의 무효화가 아니며 주장 개수로 방향을 결정하지 않습니다. "
                         "상승 주장: "+", ".join(item.claim_id for item in bull)+" / 하락 주장: "+", ".join(item.claim_id for item in bear))
    if pool is not None:
        if pool.macro_status != "CONTEXT_ONLY":
            missing.append("선택·조회된 유효 Macro 자료 부족" if pool.macro_status == "INSUFFICIENT_DATA" else "Macro 실행 실패: 단순 자료 부족과 구분합니다.")
        if not bull:
            missing.append("검증된 Bull 방향 주장 없음")
        if not bear:
            missing.append("검증된 Bear 방향 주장 없음")
    summaries = {"COMPLETED": "양측의 공개 주장과 상대 참조를 구조적으로 검증했습니다. 승자나 예측 성능을 산출하지 않습니다.",
                 "PARTIAL": "일부 토론/자료 노드가 실패하여 검증된 부분만 보존합니다. 완전한 토론 완료 결과가 아닙니다.",
                 "FAILED": "검증된 토론 결과가 부족하여 평가에 실패했습니다. 정상 토론 결과를 생성하지 않습니다.",
                 "INSUFFICIENT_DATA": "핵심 기술 자료 부족으로 토론을 건너뛰었습니다. 추가 데이터가 필요합니다."}
    signal = "INSUFFICIENT_DATA" if pool is None or not pool.technical_sufficient else pool.technical_signal
    if signal != "INSUFFICIENT_DATA" and (conflicts or status in ("PARTIAL", "FAILED")):
        signal = "NEUTRAL"
    return DebateEvaluation(valid_bull_claims=bull, valid_bear_claims=bear, rejected_claims=rejected,
        validated_message_ids=sorted(verified_ids),
        addressed_claim_ids=sorted(set(addressed)), unresolved_conflicts=conflicts, missing_evidence=missing,
        debate_summary=summaries[status], evaluation_status=status, suggested_signal=signal)


def evaluate_node(state, context):
    result = evaluate_debate(state)
    status = "SKIPPED" if result.evaluation_status == "INSUFFICIENT_DATA" else result.evaluation_status
    return {"debate_evaluation": result, "debate_status": status}


def aggregate_debate(state, context):
    base = aggregate(state, context)["aggregation_result"]
    evaluation = state.get("debate_evaluation")
    if evaluation is None or evaluation != evaluate_debate(state):
        raise DebateValidationError("토론 평가 변경 또는 누락")
    changes = {"summary": base.summary+" "+evaluation.debate_summary,
               "conflicts": [*base.conflicts, *evaluation.unresolved_conflicts],
               "missing_data": list(dict.fromkeys([*base.missing_data, *evaluation.missing_evidence])),
               "limitations": list(dict.fromkeys([*base.limitations[1:], *DEBATE_LIMITATIONS]))}
    if base.status != "FAILED":
        if evaluation.evaluation_status in ("PARTIAL", "FAILED"):
            changes["status"] = "PARTIAL"
            changes["risk_factors"] = [*base.risk_factors, "토론 일부가 실패했으므로 검증된 원본 분석과 부분 토론을 구분해야 합니다."]
        if base.final_signal != "INSUFFICIENT_DATA" and (evaluation.unresolved_conflicts or evaluation.evaluation_status in ("PARTIAL", "FAILED")):
            changes["final_signal"] = "NEUTRAL"
    return {"aggregation_result": base.model_copy(update=changes)}


def format_debate_report(state, context):
    result = format_report(state, context)
    if result["final_report"] is not None:
        report = result["final_report"]
        result["final_report"] = DebateReport(**report.model_dump(), debate_status=state["debate_status"],
            debate_rounds=state["debate_rounds"], debate_summary=state["debate_evaluation"].debate_summary,
            debate_evaluation=state["debate_evaluation"])
    return result


def finalize_debate(state):
    """실행 중단/취소도 정상 완료로 표시하지 않고, 결과/로그는 보존한다."""
    errors = {error.category for error in state["errors"]}
    if "CANCELLED" in errors:
        state = {**state, "debate_status": "CANCELLED", "execution_status": "FAILED", "final_report": None}
    elif "DUPLICATE_REQUEST" in errors:
        state = {**state, "debate_status": "FAILED", "execution_status": "FAILED", "final_report": None}
    elif state["execution_status"] == "FAILED":
        state = {**state, "debate_status": "FAILED"}
    history = sorted(state["debate_history"], key=lambda item: (item.round, item.speaker, item.claim_id))
    return {**state, "debate_history": history, "debate_round": max((item.round for item in history), default=0),
            "execution_logs": state["node_execution_logs"]}
