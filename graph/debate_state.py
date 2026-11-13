"""공개 주장/반박 데이터 계약. 비공개 추론이나 인증 정보를 보관하지 않는다."""

from dataclasses import dataclass, field
from operator import add
from threading import Event
from typing import Annotated, Literal

from pydantic import Field

from graph.state import WorkflowState, WorkflowContext, WorkflowReport, NodeLog
from llm.budget import UsageLedger
from llm.schemas import Fact, Signal, StrictModel

DEBATE_NODES = ("input_validator", "technical_agent", "macro_agent", "evidence_preparation",
                "bull_agent", "bear_agent", "debate_round", "debate_evaluator", "research_aggregator", "report_formatter")
Side = Literal["BULL", "BEAR"]
DebateStatus = Literal["WAITING", "RUNNING", "COMPLETED", "PARTIAL", "FAILED", "SKIPPED", "CANCELLED"]


class DebateEvidence(StrictModel):
    symbol: str
    timeframe: str
    data_as_of_utc: str
    facts: list[Fact]
    technical_signal: Signal
    technical_sufficient: bool
    technical_summary: str
    macro_summary: str
    macro_status: Literal["CONTEXT_ONLY", "INSUFFICIENT_DATA", "FAILED"]
    limitations: list[str]


class DebateMessage(StrictModel):
    round: Literal[1, 2]
    speaker: Side
    claim_id: str = Field(pattern=r"^(bull|bear):r[12]:[a-f0-9]{20}$")
    target_claim_id: str | None
    evidence_ids: list[str] = Field(max_length=40)
    citations: list[Fact] = Field(max_length=40)
    message_type: Literal["CLAIM", "REBUTTAL", "CONCESSION", "INSUFFICIENT_EVIDENCE"]
    content: str = Field(min_length=1, max_length=1600)
    validation_status: Literal["PENDING", "VALID", "REJECTED"] = "PENDING"


class BullResult(StrictModel):
    bullish_claims: list[DebateMessage] = Field(max_length=40)
    supporting_evidence_ids: list[str] = Field(max_length=40)
    counterarguments: list[str] = Field(max_length=40)
    uncertainties: list[str] = Field(max_length=40)

    @property
    def messages(self):
        return self.bullish_claims


class BearResult(StrictModel):
    bearish_claims: list[DebateMessage] = Field(max_length=40)
    supporting_evidence_ids: list[str] = Field(max_length=40)
    counterarguments: list[str] = Field(max_length=40)
    uncertainties: list[str] = Field(max_length=40)

    @property
    def messages(self):
        return self.bearish_claims


class RejectedClaim(StrictModel):
    node: str
    claim_id: str | None
    reason: str


class DebateEvaluation(StrictModel):
    valid_bull_claims: list[DebateMessage]
    valid_bear_claims: list[DebateMessage]
    rejected_claims: list[RejectedClaim]
    validated_message_ids: list[str]
    addressed_claim_ids: list[str]
    unresolved_conflicts: list[str]
    missing_evidence: list[str]
    debate_summary: str
    evaluation_status: Literal["COMPLETED", "PARTIAL", "FAILED", "INSUFFICIENT_DATA"]
    suggested_signal: Signal


class DebateReport(WorkflowReport):
    debate_mode: Literal["Mock Debate"] = "Mock Debate"
    debate_status: DebateStatus
    debate_rounds: int = Field(ge=1, le=2)
    debate_summary: str
    debate_evaluation: DebateEvaluation


class DebateState(WorkflowState, total=False):
    debate_enabled: bool
    debate_mode: Literal["Mock Debate"]
    debate_evidence: DebateEvidence | None
    evidence_fingerprint: str | None
    bull_result: BullResult | None
    bear_result: BearResult | None
    debate_round: int
    debate_rounds: int
    debate_history: Annotated[list[DebateMessage], add]
    debate_evaluation: DebateEvaluation | None
    debate_status: DebateStatus
    # 기존 append-reducer 로그의 최종 조회용 별칭. 노드가 이 필드에 병렬 기록하지 않는다.
    execution_logs: list[NodeLog]


@dataclass(frozen=True)
class DebateContext(WorkflowContext):
    rounds: int = 2
    guard: UsageLedger | None = field(default=None, repr=False)
    cancel_event: Event | None = field(default=None, repr=False)


def initial_debate_state(rounds):
    return {"debate_enabled": True, "debate_mode": "Mock Debate", "debate_evidence": None,
            "evidence_fingerprint": None, "bull_result": None, "bear_result": None, "debate_round": 0,
            "debate_rounds": rounds, "debate_history": [], "debate_evaluation": None,
            "debate_status": "WAITING", "execution_logs": []}
