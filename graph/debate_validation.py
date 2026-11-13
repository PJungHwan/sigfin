"""스키마와 별도로 값/단위/시각/주장 참조/문장 계약을 검증한다."""

import hashlib

import pandas as pd

from agents.debate import initial_position, rebuttals
from graph.debate_state import DebateEvidence, DebateMessage, BullResult, BearResult
from graph.validation import WorkflowValidationError


class DebateValidationError(WorkflowValidationError):
    pass


def evidence_fingerprint(evidence):
    return hashlib.sha256(evidence.model_dump_json().encode()).hexdigest()


def validate_evidence(evidence):
    evidence = DebateEvidence.model_validate(evidence)
    ids = [fact.reference_id for fact in evidence.facts]
    if len(ids) != len(set(ids)):
        raise DebateValidationError("중복 근거")
    as_of = pd.Timestamp(evidence.data_as_of_utc)
    if as_of.tz is None:
        raise DebateValidationError("UTC 기준 시각 필요")
    for fact in evidence.facts:
        stamp = pd.Timestamp(fact.observation_time)
        if (stamp.tz is None or stamp > as_of or fact.source_type not in ("TECHNICAL", "MACRO")
                or (fact.source_type == "TECHNICAL" and stamp != as_of)
                or (fact.source_type == "MACRO" and fact.role != "context")):
            raise DebateValidationError("근거 시점/방향 제약 위반")
    return evidence


def _messages_equal(actual, expected, evidence):
    actual = sorted((DebateMessage.model_validate(item) for item in actual), key=lambda item: item.claim_id)
    expected = sorted(expected, key=lambda item: item.claim_id)
    if len(actual) != len(expected) or len({item.claim_id for item in actual}) != len(actual):
        raise DebateValidationError("중복/누락 주장")
    allowed = {fact.reference_id: fact for fact in evidence.facts}
    for value, canonical in zip(actual, expected):
        if value.validation_status == "REJECTED":
            raise DebateValidationError("거부된 주장 재사용")
        for fact in value.citations:
            if allowed.get(fact.reference_id) != fact:
                raise DebateValidationError("근거 ID/출처/수치/단위/관측 시각 오류")
        if value.model_dump(exclude={"validation_status"}) != canonical.model_dump(exclude={"validation_status"}):
            raise DebateValidationError("주장 문장/방향/상대 참조 오류")
    return [item.model_copy(update={"validation_status": "VALID"}) for item in actual]


def validate_position(result, evidence, side):
    validate_evidence(evidence)
    schema = BullResult if side == "BULL" else BearResult
    result = schema.model_validate(result)
    expected = initial_position(evidence, side)
    if result.model_dump(exclude={"bullish_claims", "bearish_claims"}) != expected.model_dump(exclude={"bullish_claims", "bearish_claims"}):
        raise DebateValidationError("반대 근거/불확실성 또는 지원 ID 오류")
    messages = _messages_equal(result.messages, expected.messages, evidence)
    key = "bullish_claims" if side == "BULL" else "bearish_claims"
    return result.model_copy(update={key: messages})


def validate_responses(messages, evidence, side, opponent):
    opponent = validate_position(opponent, evidence, "BEAR" if side == "BULL" else "BULL")
    expected = rebuttals(evidence, side, opponent)
    return _messages_equal(messages, expected, evidence)
