"""기존 State/Runtime의 선택적 확장. Risk OFF의 계약은 변경하지 않는다."""

from dataclasses import dataclass, field
from typing import Literal

from graph.state import WorkflowReport
from graph.debate_state import DebateState, DebateContext, DebateReport
from llm.schemas import Signal
from risk.models import RiskInput, RiskReport, PositionSettings, RiskThresholds, ResearchAction


class RiskState(DebateState, total=False):
    risk_manager_enabled: bool
    risk_input: RiskInput | None
    risk_result: RiskReport | None
    research_action: ResearchAction | None
    pre_risk_signal: Signal | None
    risk_manager_status: Literal["WAITING", "COMPLETED", "FAILED"]


@dataclass(frozen=True)
class RiskContext(DebateContext):
    risk_settings: PositionSettings = field(default_factory=PositionSettings)
    risk_thresholds: RiskThresholds = field(default_factory=RiskThresholds)


class RiskWorkflowReport(WorkflowReport):
    risk_report: RiskReport | None
    research_action: ResearchAction
    risk_manager_status: Literal["COMPLETED", "FAILED"]
    pre_risk_signal: Signal | None


class RiskDebateReport(DebateReport):
    risk_report: RiskReport | None
    research_action: ResearchAction
    risk_manager_status: Literal["COMPLETED", "FAILED"]
    pre_risk_signal: Signal | None
