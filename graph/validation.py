"""노드 결과를 입력 근거와 대조한다. 형식만 맞는 출력·임의 문장은 통과시키지 않는다."""

from agents.research import RuleTechnicalAgent, RuleMacroAgent
from graph.state import TechnicalResult, MacroResult


class WorkflowValidationError(ValueError):
    pass


def validate_technical(result, snapshot, mode="Mock Workflow"):
    result = TechnicalResult.model_validate(result)
    if mode == "LLM Workflow":
        from llm.agent_contracts import TechnicalLLMOutput, validate_technical_output
        validate_technical_output(TechnicalLLMOutput(signal=result.technical_signal,
            technical_summary=result.technical_summary, bullish_factors=result.bullish_factors,
            bearish_factors=result.bearish_factors, evidence_ids=[item.reference_id for item in result.evidence],
            evidence=result.evidence, risk_factors=result.risk_factors, limitations=result.limitations), snapshot)
        return result
    # 이번 단계의 규칙·근거는 완전히 재현 가능하다. 향후 LLM 어댑터는 별도의 근거 검증 정책이 필요하다.
    expected = RuleTechnicalAgent().analyze(snapshot)
    if result != expected:
        raise WorkflowValidationError("기술 분석 결과가 검증된 입력 근거/규칙과 다릅니다.")
    return result


def validate_macro(result, facts, metadata=None, mode="Mock Workflow"):
    result = MacroResult.model_validate(result)
    if mode == "LLM Workflow":
        from llm.agent_contracts import MacroLLMOutput, validate_macro_output
        validate_macro_output(MacroLLMOutput(macro_summary=result.macro_summary,
            macro_evidence_ids=[item.reference_id for item in result.macro_evidence], macro_evidence=result.macro_evidence,
            macro_risk_factors=result.macro_risk_factors, macro_limitations=result.macro_limitations,
            macro_status=result.macro_status), facts, metadata)
        return result
    if result != RuleMacroAgent().analyze(facts, metadata):
        raise WorkflowValidationError("거시 분석 결과가 원본 관측값/시점 제약과 다릅니다.")
    return result
