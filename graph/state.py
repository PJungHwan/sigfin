"""분기별 결과는 독립 채널, 실행 이벤트/오류만 append reducer로 병합한다."""

from dataclasses import dataclass, field
from operator import add
from typing import Annotated, Literal, TypedDict

import pandas as pd
from pydantic import Field

from llm.schemas import Evidence, Fact, MarketSnapshot, Signal, StrictModel, TokenUsage

NODES = ("input_validator", "technical_agent", "macro_agent", "research_aggregator", "report_formatter")
NodeStatus = Literal["WAITING", "RUNNING", "COMPLETED", "FAILED", "SKIPPED"]


class NodeLog(StrictModel):
    node: str
    status: NodeStatus
    started_at_utc: str | None
    ended_at_utc: str | None
    duration_ms: float | None = Field(default=None, ge=0)
    error_type: str | None = None


class WorkflowError(StrictModel):
    node: str
    category: str
    message: str


class TechnicalResult(StrictModel):
    technical_signal: Signal
    technical_summary: str
    bullish_factors: list[str]
    bearish_factors: list[str]
    evidence: list[Evidence]
    limitations: list[str]
    risk_factors: list[str] = Field(default_factory=list)


class MacroResult(StrictModel):
    macro_summary: str
    macro_evidence: list[Evidence]
    macro_limitations: list[str]
    macro_status: Literal["CONTEXT_ONLY", "INSUFFICIENT_DATA"]
    macro_risk_factors: list[str] = Field(default_factory=list)


class AgentExecution(StrictModel):
    execution_mode: Literal["Mock Workflow", "LLM Workflow"]
    model: str | None
    api_called: bool
    validation_status: str
    usage: TokenUsage | None
    estimated_cost_usd: float | None
    reserved_cost_usd: float
    error_type: str | None


class AggregationResult(StrictModel):
    status: Literal["COMPLETED", "PARTIAL", "FAILED"]
    final_signal: Signal | None
    summary: str
    conflicts: list[str]
    risk_factors: list[str]
    missing_data: list[str]
    evidence: list[Evidence]
    limitations: list[str]


class WorkflowReport(StrictModel):
    run_id: str
    symbol: str
    timeframe: str
    execution_mode: Literal["Mock Workflow", "LLM Workflow"]
    execution_status: Literal["COMPLETED", "PARTIAL"]
    data_as_of_utc: str
    analyzed_at_utc: str
    final_signal: Signal
    technical_summary: str
    macro_summary: str
    bullish_factors: list[str]
    bearish_factors: list[str]
    conflicts: list[str]
    risk_factors: list[str]
    missing_data: list[str]
    evidence: list[Evidence]
    limitations: list[str]
    started_at_utc: str
    completed_at_utc: str
    duration_ms: float = Field(ge=0)


class WorkflowState(TypedDict, total=False):
    run_id: str
    symbol: str
    timeframe: str
    execution_mode: Literal["Mock Workflow", "LLM Workflow"]
    input_data: MarketSnapshot | None
    input_fingerprint: str
    data_as_of_utc: str | None
    technical_result: TechnicalResult | None
    technical_execution: AgentExecution | None
    macro_input: list[Fact]
    macro_metadata: list[dict[str, str]]
    macro_result: MacroResult | None
    macro_execution: AgentExecution | None
    aggregation_result: AggregationResult | None
    final_report: WorkflowReport | None
    node_execution_logs: Annotated[list[NodeLog], add]
    errors: Annotated[list[WorkflowError], add]
    execution_status: Literal["WAITING", "RUNNING", "COMPLETED", "PARTIAL", "FAILED"]


@dataclass(frozen=True)
class WorkflowContext:
    # 원본 DataFrame은 이 호출의 runtime에만 보관한다. 공유 상태·로그·외부 저장소에는 넣지 않는다.
    market_data: pd.DataFrame | None
    macro_frames: dict[str, pd.DataFrame]
    requested_at_utc: str
    started_monotonic: float
    llm_session: object | None = field(default=None, repr=False)
