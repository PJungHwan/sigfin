"""종가 기준의 가상 양방향 손절 시나리오. 실제 주문/체결/강제청산을 예측하지 않는다."""

from math import isfinite

from backtest.models import Position, RiskConfig
from backtest.risk import risk_levels, adverse_fill_price
from risk.models import PositionSettings, PositionAssessment, PositionScenario, RiskDataError


def simulate_position(price, settings):
    settings = PositionSettings.model_validate(settings)
    if type(price) not in (int, float) or not isfinite(price) or price <= 0:
        raise RiskDataError("가상 기준 가격은 유한한 양수여야 합니다.")
    config = RiskConfig(stop_loss_enabled=True, stop_loss_rate=settings.stop_distance)
    per_unit = []
    for direction in (Position.LONG, Position.SHORT):
        buying = direction is Position.LONG
        stop = risk_levels(direction, price, price, config)["STOP_LOSS"]
        entry = adverse_fill_price(price, buying=buying, slippage_rate=settings.slippage_rate)
        exit_fill = adverse_fill_price(stop, buying=not buying, slippage_rate=settings.slippage_rate)
        fees = (entry+exit_fill)*settings.fee_rate
        slip = abs(entry-price)+abs(exit_fill-stop)
        loss = (entry-exit_fill if buying else exit_fill-entry)+fees
        per_unit.append((direction, entry, stop, exit_fill, fees, slip, loss))
    worst_loss = max(row[-1] for row in per_unit)
    if not isfinite(worst_loss) or worst_loss <= 0:
        raise RiskDataError("손절 거리/비용이 계산 정밀도에서 유효한 손실을 만들지 못합니다.")
    allowed = settings.capital*settings.account_loss_rate
    # 증거금과 진입 수수료를 함께 원금 내에서 충당한다. 레버리지로 손실 예산을 늘리지 않는다.
    margin_and_fee = max(row[1]/settings.leverage+row[1]*settings.fee_rate for row in per_unit)
    quantity = min(allowed/worst_loss, settings.capital/margin_and_fee)
    if not isfinite(quantity) or quantity < 0:
        raise RiskDataError("감당 가능한 가상 포지션 규모를 계산할 수 없습니다.")
    scenarios = [PositionScenario(direction=direction.value, quantity=quantity, entry_fill=entry,
        stop_reference_price=stop, exit_fill=exit_fill, entry_notional=quantity*entry,
        margin_required=quantity*entry/settings.leverage, entry_fee=quantity*entry*settings.fee_rate,
        total_fees=quantity*fees, total_slippage_cost=quantity*slip, estimated_cost=quantity*(fees+slip),
        estimated_loss=quantity*loss) for direction, entry, stop, exit_fill, fees, slip, loss in per_unit]
    adjusted_loss = max(row.estimated_loss for row in scenarios)
    tolerance = max(1e-9, allowed*1e-12)
    adjusted_exceeded = adjusted_loss > allowed+tolerance
    if adjusted_exceeded or any(row.margin_required+row.entry_fee > settings.capital+max(1e-9, settings.capital*1e-12) for row in scenarios):
        raise RiskDataError("가상 포지션의 손실/증거금 회계 한도를 초과했습니다.")
    full_nominal = settings.capital*settings.leverage
    full_loss = full_nominal/price*worst_loss
    return PositionAssessment(reference_price=float(price), allowed_loss=allowed, requested_full_notional=full_nominal,
        requested_full_loss=full_loss, stop_only_quantity=allowed/(price*settings.stop_distance), cost_aware_quantity=quantity,
        adjusted_notional=quantity*price, adjusted_max_loss=adjusted_loss, adjusted_max_cost=max(row.estimated_cost for row in scenarios),
        full_exposure_limit_exceeded=full_loss > allowed+tolerance, adjusted_limit_exceeded=adjusted_exceeded, scenarios=scenarios)
