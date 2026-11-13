"""두 차례 병렬 분기와 barrier 합류를 가진 실제 StateGraph. 루프/LLM 호출 없음."""

from functools import lru_cache

from langgraph.graph import StateGraph, START, END

from agents.bull_agent import BullAgent
from agents.bear_agent import BearAgent
from agents.research import RuleTechnicalAgent, RuleMacroAgent
from graph.debate_state import DebateState, DebateContext
from graph.debate_nodes import (debate_tracked, prepare_evidence, position_node, round_node,
                                 evaluate_node, aggregate_debate, format_debate_report)
from graph.nodes import validate_input, technical_node, macro_node
from graph.workflow import route_after_validation


def after_preparation(state):
    if any(error.category in ("CANCELLED", "DUPLICATE_REQUEST") for error in state["errors"]):
        return "stop"
    pool = state.get("debate_evidence")
    return ["bull_agent", "bear_agent"] if pool is not None and pool.technical_sufficient else "evaluate"


def after_round(state):
    return "stop" if any(error.category == "CANCELLED" for error in state["errors"]) else "evaluate"


def build_debate_workflow(*, technical_agent=None, macro_agent=None, bull_agent=None, bear_agent=None,
                          risk_enabled=False, risk_manager=None):
    bull, bear = bull_agent or BullAgent(), bear_agent or BearAgent()
    if risk_enabled:
        from agents.risk_manager import RiskManager
        from graph.risk_state import RiskState, RiskContext
        from graph.risk_nodes import risk_node, risk_tracked, aggregate_risk, format_risk_report
    builder = StateGraph(RiskState if risk_enabled else DebateState, context_schema=RiskContext if risk_enabled else DebateContext)
    builder.add_node("input_validator", debate_tracked("input_validator", validate_input))
    builder.add_node("technical_agent", technical_node(technical_agent or RuleTechnicalAgent()))
    builder.add_node("macro_agent", macro_node(macro_agent or RuleMacroAgent()))
    builder.add_node("evidence_preparation", debate_tracked("evidence_preparation", prepare_evidence))
    builder.add_node("bull_agent", position_node("BULL", bull))
    builder.add_node("bear_agent", position_node("BEAR", bear))
    builder.add_node("debate_round", round_node(bull, bear))
    builder.add_node("debate_evaluator", debate_tracked("debate_evaluator", evaluate_node))
    if risk_enabled:
        builder.add_node("risk_manager", risk_node(risk_manager or RiskManager()))
    builder.add_node("research_aggregator", risk_tracked("research_aggregator", aggregate_risk) if risk_enabled else debate_tracked("research_aggregator", aggregate_debate))
    builder.add_node("report_formatter", risk_tracked("report_formatter", format_risk_report) if risk_enabled else debate_tracked("report_formatter", format_debate_report))
    builder.add_edge(START, "input_validator")
    builder.add_conditional_edges("input_validator", route_after_validation,
        {"stop": END, "technical_agent": "technical_agent", "macro_agent": "macro_agent"})
    builder.add_edge(["technical_agent", "macro_agent"], "evidence_preparation")
    builder.add_conditional_edges("evidence_preparation", after_preparation,
        {"stop": END, "evaluate": "debate_evaluator", "bull_agent": "bull_agent", "bear_agent": "bear_agent"})
    builder.add_edge(["bull_agent", "bear_agent"], "debate_round")
    builder.add_conditional_edges("debate_round", after_round, {"stop": END, "evaluate": "debate_evaluator"})
    builder.add_edge("debate_evaluator", "risk_manager" if risk_enabled else "research_aggregator")
    if risk_enabled:
        builder.add_edge("risk_manager", "research_aggregator")
    builder.add_edge("research_aggregator", "report_formatter")
    builder.add_edge("report_formatter", END)
    return builder.compile()


@lru_cache(maxsize=2)
def get_debate_workflow(risk_enabled=False):
    return build_debate_workflow(risk_enabled=risk_enabled)
