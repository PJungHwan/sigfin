"""원본 실행/첨부와 결정론적 통합 보고서의 별도 계약."""

from typing import Literal
from pydantic import Field

from llm.schemas import StrictModel,Fact,Signal
from portfolio.models import Account,Quote,Valuation
from risk.portfolio import PortfolioRiskSettings,PortfolioRiskReport
from risk.models import PositionSettings
from services.research_snapshots import TableSnapshot


class PortfolioResearchInput(StrictModel):
    account: Account
    quotes: dict[str,Quote]
    frames: dict[str,TableSnapshot]
    history: list[Valuation]
    risk_settings: PortfolioRiskSettings | None
    evaluated_at_utc: str


class BacktestResearchInput(StrictModel):
    strategy_json: str = Field(max_length=64000)
    config: dict
    risk: dict


class ResearchContext(StrictModel):
    memo: str = Field(default='',max_length=2000)
    strategy_json: str | None = Field(default=None,max_length=64000)
    risk_agent_settings: PositionSettings = Field(default_factory=PositionSettings)
    portfolio: PortfolioResearchInput | None = None
    backtest: BacktestResearchInput | None = None


class ResearchReport(StrictModel):
    schema_version: Literal[1] = 1
    report_id: str
    app_version: str
    symbol: str
    timeframe: str
    period: str
    strategy_name: str | None
    execution_mode: Literal['Mock Workflow','LLM Workflow']
    status: Literal['COMPLETED','PARTIAL','FAILED']
    analyzed_at_utc: str
    data_as_of_utc: str | None
    source_input_digest: str
    signal: Signal
    interpretation_origin: str
    technical_summary: str
    macro_summary: str
    agent_execution: dict
    debate_source: dict
    risk_source: dict
    conflicts: list[str]
    risk_factors: list[str]
    evidence: list[Fact]
    valuation: Valuation | None
    portfolio_risk: PortfolioRiskReport | None
    backtest: dict | None
    research_action: Literal['REVIEW','HOLD','ABSTAIN']
    missing_data: list[str]
    limitations: list[str]
    user_memo: str
    calculation_origin: Literal['Python 검산 · LLM 생성 수치 아님'] = 'Python 검산 · LLM 생성 수치 아님'
