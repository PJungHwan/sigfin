"""OHLC 리스크 판정. 현재 봉 극값은 현재 봉 trailing 기준에 소급 적용하지 않는다.

가격 경로를 추정하지 않는다. 시가 갭을 먼저 판단한 뒤 봉 범위에 닿은 기준을 판단한다.
손절/트레일링은 익절보다 먼저, 두 보호선 동시 접촉은 불리한 기준가를 선택한다.
정확한 intrabar 체결 시각은 알 수 없으며 엔진은 봉 시작을 위치 표시에만 사용한다.
"""

from dataclasses import dataclass
from math import isfinite

from backtest.models import BacktestError, Position, RiskConfig


@dataclass(frozen=True)
class RiskTrigger:
    reason: str
    raw_price: float
    trigger_price: float
    timing: str
    candidates: tuple[str, ...]


def adverse_fill_price(raw_price: float, *, buying: bool, slippage_rate: float) -> float:
    """기존 엔진과 동일한 매수/매도 슬리피지 공식. 엔진 결과는 변경하지 않는다."""
    if (not isfinite(raw_price) or raw_price <= 0 or type(buying) is not bool
            or not isfinite(slippage_rate) or not 0 <= slippage_rate < 1):
        raise BacktestError("슬리피지 체결 가정이 유효하지 않습니다.")
    return raw_price*(1+slippage_rate if buying else 1-slippage_rate)


def risk_levels(direction: Position, entry_price: float, favorable_price: float,
                config: RiskConfig) -> dict[str, float]:
    if direction not in (Position.LONG, Position.SHORT):
        raise BacktestError("리스크 기준에는 LONG 또는 SHORT 포지션이 필요합니다.")
    if not all(isfinite(value) and value > 0 for value in (entry_price, favorable_price)):
        raise BacktestError("리스크 기준 가격은 유한한 양수여야 합니다.")
    sign = 1 if direction is Position.LONG else -1
    levels = {}
    if config.stop_loss_enabled:
        levels["STOP_LOSS"] = entry_price * (1 - sign * config.stop_loss_rate)
    if config.take_profit_enabled:
        levels["TAKE_PROFIT"] = entry_price * (1 + sign * config.take_profit_rate)
    if config.trailing_stop_enabled:
        levels["TRAILING_STOP"] = favorable_price * (1 - sign * config.trailing_stop_rate)
    if not all(isfinite(value) and value > 0 for value in levels.values()):
        raise BacktestError("계산된 리스크 기준가가 유효한 양수 범위를 벗어났습니다.")
    return levels


def check_risk(direction: Position, levels: dict[str, float], open_price: float,
               high: float, low: float, *, opening_only: bool = False) -> RiskTrigger | None:
    """갭: 원시 시가, 봉 내부 접촉: 기준가. 비용은 공통 엔진에서 한 번만 반영한다."""
    is_long = direction is Position.LONG

    def touched(reason, level, opening):
        protective = reason != "TAKE_PROFIT"
        downward = is_long == protective
        price = open_price if opening else low if downward else high
        return price <= level if downward else price >= level

    for opening in (True, False):
        if not opening and opening_only:
            break
        hits = [name for name, level in levels.items() if touched(name, level, opening)]
        if not hits:
            continue
        protective = [name for name in hits if name != "TAKE_PROFIT"]
        if protective:
            # LONG 매도는 낮은 가격, SHORT 환매는 높은 가격이 더 보수적이다.
            reason = (min if is_long else max)(protective, key=lambda name: levels[name])
        else:
            reason = "TAKE_PROFIT"
        return RiskTrigger(reason, open_price if opening else levels[reason], levels[reason],
                           "OPEN_GAP" if opening else "INTRABAR_OHLC", tuple(hits))
    return None


def update_favorable_price(direction: Position, previous: float, high: float, low: float) -> float:
    """호출 시점은 현재 봉 청산 판단 이후. 다음 봉에서만 갱신값을 사용한다."""
    return max(previous, high) if direction is Position.LONG else min(previous, low)
