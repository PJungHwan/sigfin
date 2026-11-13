"""공식 StateGraph 컴파일. 두 노드 모두 끝나야 집계하는 list-form barrier edge."""

from functools import lru_cache

from langgraph.graph import StateGraph, START, END

from agents.research import RuleTechnicalAgent, RuleMacroAgent
from graph.nodes import tracked, validate_input, technical_node, macro_node, aggregate, format_report
from graph.state import WorkflowState, WorkflowContext


def route_after_validation(state):
    return "stop" if state["execution_status"] == "FAILED" else ["technical_agent", "macro_agent"]


def build_workflow(*, technical_agent=None, macro_agent=None, risk_enabled=False, risk_manager=None):
    if risk_enabled:
        from agents.risk_manager import RiskManager
        from graph.risk_state import RiskState, RiskContext
        from graph.risk_nodes import risk_node, risk_tracked, aggregate_risk, format_risk_report
    builder = StateGraph(RiskState if risk_enabled else WorkflowState, context_schema=RiskContext if risk_enabled else WorkflowContext)
    builder.add_node("input_validator", tracked("input_validator", validate_input))
    builder.add_node("technical_agent", technical_node(technical_agent or RuleTechnicalAgent()))
    builder.add_node("macro_agent", macro_node(macro_agent or RuleMacroAgent()))
    if risk_enabled:
        builder.add_node("risk_manager", risk_node(risk_manager or RiskManager()))
    builder.add_node("research_aggregator", risk_tracked("research_aggregator", aggregate_risk) if risk_enabled else tracked("research_aggregator", aggregate))
    builder.add_node("report_formatter", risk_tracked("report_formatter", format_risk_report) if risk_enabled else tracked("report_formatter", format_report))
    builder.add_edge(START, "input_validator")
    builder.add_conditional_edges("input_validator", route_after_validation,
                                 {"stop": END, "technical_agent": "technical_agent", "macro_agent": "macro_agent"})
    builder.add_edge(["technical_agent", "macro_agent"], "risk_manager" if risk_enabled else "research_aggregator")
    if risk_enabled:
        builder.add_edge("risk_manager", "research_aggregator")
    builder.add_edge("research_aggregator", "report_formatter")
    builder.add_edge("report_formatter", END)
    return builder.compile()


@lru_cache(maxsize=2)
def get_workflow(risk_enabled=False):
    # checkpointer/외부 store가 없는 무상태 그래프 구조만 공유한다. 실행 상태는 호출마다 별도다.
    return build_workflow(risk_enabled=risk_enabled)
