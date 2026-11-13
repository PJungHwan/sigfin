"""실제 LLM 호출이 없는 결정론적 노드 인터페이스. 미래 예측/다수결 점수를 만들지 않는다."""

from typing import Protocol

from graph.state import TechnicalResult, MacroResult
from llm.schemas import Evidence, Fact, MarketSnapshot

TECHNICAL_LIMITATIONS = [
    "Mock Workflow의 결정론적 기술 규칙이며 실제 LLM 판단이나 투자 실행이 아닙니다.",
    "단기/장기 평균·모멘텀의 일치 여부를 요약하며 예측 성공 확률을 계산하지 않습니다.",
]
MACRO_LIMITATIONS = [
    "관측일은 발표일이 아닙니다. 현재 개정된 FRED 값의 문맥 설명만 제공합니다.",
    "신뢰 가능한 발표 시각·당시 빈티지가 없어 과거 시점의 방향 판단을 수행하지 않습니다.",
    "사후 상관관계 결과를 실시간 예측이나 백테스트 입력으로 사용하지 않습니다.",
]
TECHNICAL_SUMMARIES = {
    "BULLISH": "두 이동평균 쌍과 MACD, 중립 이상 RSI가 상승 방향의 조건을 함께 충족합니다.",
    "BEARISH": "두 이동평균 쌍과 MACD, 중립 이하 RSI가 하락 방향의 조건을 함께 충족합니다.",
    "NEUTRAL": "추세·모멘텀의 동시 조건이 일치하지 않거나 극단 RSI 구간이어서 중립으로 요약합니다.",
    "INSUFFICIENT_DATA": "판단 근거 부족: 핵심 기술적 지표를 계산할 연속 확정 데이터가 부족합니다.",
}


def evidence_for(fact):
    return Evidence(**fact.model_dump(exclude={"role", "unit"}))


def technical_signal(snapshot):
    if not snapshot.sufficient:
        return "INSUFFICIENT_DATA"
    values = {fact.metric_name: fact.metric_value for fact in snapshot.facts if fact.source_type == "TECHNICAL"}
    rsi = values["rsi_14"]
    # AND 조건이다. 근거 개수의 다수결이나 임의 가중 합산을 하지 않는다.
    if (values["ema_spread_pct"] > 0 and values["sma_spread_pct"] > 0 and
            values["macd_histogram"] > 0 and 50 < rsi < 70):
        return "BULLISH"
    if (values["ema_spread_pct"] < 0 and values["sma_spread_pct"] < 0 and
            values["macd_histogram"] < 0 and 30 < rsi < 50):
        return "BEARISH"
    return "NEUTRAL"


class TechnicalAnalyzer(Protocol):
    def analyze(self, snapshot: MarketSnapshot) -> TechnicalResult: ...


class MacroAnalyzer(Protocol):
    def analyze(self, facts: list[Fact], metadata: list[dict[str, str]]) -> MacroResult: ...


class RuleTechnicalAgent:
    def analyze(self, snapshot):
        signal = technical_signal(snapshot)
        technical = [fact for fact in snapshot.facts if fact.source_type == "TECHNICAL"]
        return TechnicalResult(
            technical_signal=signal, technical_summary=TECHNICAL_SUMMARIES[signal],
            bullish_factors=[fact.explanation for fact in technical if fact.role == "bullish"],
            bearish_factors=[fact.explanation for fact in technical if fact.role == "bearish"],
            evidence=[evidence_for(fact) for fact in technical], limitations=TECHNICAL_LIMITATIONS,
        )


class RuleMacroAgent:
    def analyze(self, facts, metadata=None):
        return MacroResult(
            macro_summary=("선택한 FRED 지표의 최신 유효 관측 수준을 단위와 관측일에 따라 설명합니다. 시장 방향에는 사용하지 않습니다."
                           if facts else "판단 근거 부족: 선택·조회된 유효 거시경제 관측값이 없습니다."),
            macro_evidence=[evidence_for(fact) for fact in facts], macro_limitations=MACRO_LIMITATIONS,
            macro_status="CONTEXT_ONLY" if facts else "INSUFFICIENT_DATA",
        )
