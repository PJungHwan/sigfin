"""향후 어댑터 교체가 가능한 순수 규칙 기반 Risk Manager. API 호출 없음."""

from typing import Protocol

from risk.models import RiskInput, RiskReport
from risk.evaluator import evaluate_risk


class RiskAnalyzer(Protocol):
    def analyze(self, inputs: RiskInput) -> RiskReport: ...


class RiskManager:
    def analyze(self, inputs):
        return evaluate_risk(inputs)
