"""검증된 JSON 다운로드/업로드. 서버 파일 경로나 업로드 파일명을 사용하지 않는다."""

from datetime import datetime, timezone
import json
import re
from uuid import uuid4

from strategies.models import (
    Condition, ConditionGroup, Direction, Indicator, Logic, Operand, OperandKind,
    Operator, Strategy, StrategyValidationError, MAX_CONDITIONS_PER_DIRECTION,
)

MAX_JSON_BYTES = 64 * 1024


def strategy_to_dict(strategy: Strategy) -> dict:
    if not isinstance(strategy, Strategy):
        raise StrategyValidationError("검증된 Strategy 객체가 필요합니다.")

    def group_dict(group):
        conditions = []
        for item in group.conditions:
            right = {"kind": item.right.kind.value}
            if item.right.kind is OperandKind.NUMBER:
                right["value"] = item.right.value
            else:
                right["indicator"] = item.right.indicator.value
            conditions.append({"type": "condition", "id": item.id,
                               "left": item.left.value, "operator": item.operator.value,
                               "right": right})
        return {"type": "group", "direction": group.direction.value,
                "combine": group.combine.value, "conditions": conditions}

    return {"schema_version": strategy.schema_version, "name": strategy.name,
            "long": group_dict(strategy.long), "short": group_dict(strategy.short)}


def _keys(value, required, path):
    if type(value) is not dict or set(value) != set(required):
        raise StrategyValidationError(f"{path}의 필수 필드 또는 지원 필드가 올바르지 않습니다.")


def _enum(kind, value, field):
    if type(value) is not str:
        raise StrategyValidationError(f"{field}는 지원 문자열이어야 합니다.")
    try:
        return kind(value)
    except ValueError as error:
        raise StrategyValidationError(f"지원하지 않는 {field}입니다.") from error


def strategy_from_dict(payload: dict) -> Strategy:
    _keys(payload, ("schema_version", "name", "long", "short"), "전략")

    def group(value):
        _keys(value, ("type", "direction", "combine", "conditions"), "조건 그룹")
        if value["type"] != "group" or type(value["conditions"]) is not list:
            raise StrategyValidationError("버전 1은 조건 목록을 가진 단일 그룹만 지원합니다.")
        if len(value["conditions"]) > MAX_CONDITIONS_PER_DIRECTION:
            raise StrategyValidationError(f"한 방향의 조건은 최대 {MAX_CONDITIONS_PER_DIRECTION}개입니다.")
        conditions = []
        for item in value["conditions"]:
            _keys(item, ("type", "id", "left", "operator", "right"), "조건")
            if item["type"] != "condition":
                raise StrategyValidationError("중첩 조건 그룹은 현재 버전에서 지원하지 않습니다.")
            right = item["right"]
            if type(right) is not dict or "kind" not in right:
                raise StrategyValidationError("우측 피연산자 형식이 올바르지 않습니다.")
            kind = _enum(OperandKind, right["kind"], "피연산자 유형")
            if kind is OperandKind.NUMBER:
                _keys(right, ("kind", "value"), "숫자 피연산자")
                operand = Operand(kind, value=right["value"])
            else:
                _keys(right, ("kind", "indicator"), "지표 피연산자")
                operand = Operand(kind, indicator=_enum(Indicator, right["indicator"], "우측 지표"))
            conditions.append(Condition(item["id"], _enum(Indicator, item["left"], "좌측 지표"),
                                        _enum(Operator, item["operator"], "연산자"), operand))
        return ConditionGroup(_enum(Direction, value["direction"], "방향"),
                              _enum(Logic, value["combine"], "결합 연산자"), tuple(conditions))

    return Strategy(payload["name"], group(payload["long"]), group(payload["short"]),
                    payload["schema_version"])


def strategy_to_json(strategy: Strategy) -> str:
    return json.dumps(strategy_to_dict(strategy), ensure_ascii=False, allow_nan=False, indent=2)


def _unique_keys(pairs):
    output = {}
    for key, value in pairs:
        if key in output:
            raise StrategyValidationError("JSON에 중복 필드가 있습니다.")
        output[key] = value
    return output


def _reject_constant(value):
    raise StrategyValidationError("JSON에 NaN/Infinity를 사용할 수 없습니다.")


def strategy_from_json(payload: str | bytes) -> Strategy:
    if not isinstance(payload, (str, bytes)):
        raise StrategyValidationError("JSON은 UTF-8 텍스트 또는 bytes여야 합니다.")
    try:
        raw = payload.encode("utf-8") if isinstance(payload, str) else payload
        if len(raw) > MAX_JSON_BYTES:
            raise StrategyValidationError("전략 JSON은 최대 64KB입니다.")
        decoded = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_keys,
                             parse_constant=_reject_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise StrategyValidationError("JSON 형식 또는 UTF-8 인코딩이 올바르지 않습니다.") from error
    return strategy_from_dict(decoded)


def download_filename(strategy: Strategy) -> str:
    safe_name = re.sub(r"[^A-Za-z0-9가-힣_-]+", "_", strategy.name).strip("_")[:40] or "strategy"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"strategy-{safe_name}-{stamp}-{uuid4().hex[:8]}.json"
