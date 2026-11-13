"""시장 근거와 상대 주장에서 결정론적으로 공개 문장을 구성한다. LLM/수집/API 호출 없음."""

import hashlib
from typing import Protocol

from graph.debate_state import BullResult, BearResult, DebateEvidence, DebateMessage

DEBATE_LIMITATIONS = [
    "실제 LangGraph 노드에서 실행한 결정론적 Mock Debate이며 LLM 토론이 아닙니다.",
    "구조적 근거 검증은 환각 제거·예측 정확도 향상·미래 수익을 입증하지 않습니다.",
    "FRED 관측일은 발표일이 아닙니다. 시점 미검증 현재 개정값은 방향 근거에서 제외합니다.",
]


class PositionAgent(Protocol):
    def propose(self, evidence: DebateEvidence) -> BullResult | BearResult: ...
    def respond(self, evidence: DebateEvidence, opponent: BullResult | BearResult) -> list[DebateMessage]: ...


def directional_facts(evidence, side):
    role = "bullish" if side == "BULL" else "bearish"
    return sorted((fact for fact in evidence.facts if fact.source_type == "TECHNICAL" and fact.role == role),
                  key=lambda fact: fact.reference_id) if evidence.technical_sufficient else []


def message(side, round_number, kind, content, facts=(), target=None):
    ids = [fact.reference_id for fact in facts]
    digest = hashlib.sha256(f"{side}:{round_number}:{kind}:{target}:{','.join(ids)}".encode()).hexdigest()[:20]
    return DebateMessage(round=round_number, speaker=side, claim_id=f"{side.lower()}:r{round_number}:{digest}",
        target_claim_id=target, evidence_ids=ids, citations=[fact.model_copy(deep=True) for fact in facts],
        message_type=kind, content=content)


def initial_position(evidence, side):
    own = directional_facts(evidence, side)
    opposite = directional_facts(evidence, "BEAR" if side == "BULL" else "BULL")
    direction = "상승" if side == "BULL" else "하락"
    claims = [message(side, 1, "CLAIM", f"{fact.explanation} 이 관측은 {direction} 관점의 기술적 근거입니다. 다른 지표와 시점 한계를 함께 확인해야 합니다.", [fact])
              for fact in own]
    if not claims:
        claims = [message(side, 1, "INSUFFICIENT_EVIDENCE", f"검증된 {direction} 방향 근거가 부족하여 주장을 강제로 생성하지 않습니다.")]
    values = {"supporting_evidence_ids": [fact.reference_id for fact in own],
              "counterarguments": [f"반대 방향 관측을 인정합니다: {fact.explanation}" for fact in opposite],
              "uncertainties": list(dict.fromkeys([*DEBATE_LIMITATIONS, *evidence.limitations]))[:40]}
    return BullResult(bullish_claims=claims, **values) if side == "BULL" else BearResult(bearish_claims=claims, **values)


def rebuttals(evidence, side, opponent):
    own = directional_facts(evidence, side)
    targets = [item for item in opponent.messages if item.message_type == "CLAIM"]
    if not targets:
        return [message(side, 2, "INSUFFICIENT_EVIDENCE", "상대가 검증된 방향 주장을 제시하지 않아 반박할 주장 참조가 없습니다.")]
    responses = []
    for target in targets:
        observed = target.citations[0]
        if own:
            # 상대 관측값 자체를 부정하지 않는다. 다른 지표의 반대 관측이 방향 확정을 제한한다.
            counter = next((fact for fact in own if fact.metric_name.split("_")[0] == observed.metric_name.split("_")[0]), own[0])
            content = (f"상대 주장 {target.claim_id}의 관측({observed.explanation})을 인정합니다. "
                       f"그러나 {counter.explanation}는 반대 방향 근거이므로 상대 관측만으로 시장 방향을 확정할 수 없습니다.")
            responses.append(message(side, 2, "REBUTTAL", content, [observed, counter], target.claim_id))
        else:
            content = (f"상대 주장 {target.claim_id}의 관측({observed.explanation})을 인정합니다. "
                       "반박할 검증된 반대 방향 근거가 없어 이 관측을 부정하지 않습니다.")
            responses.append(message(side, 2, "CONCESSION", content, [observed], target.claim_id))
    return responses
