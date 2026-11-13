"""전략 편집 UI. 검증과 저장은 strategies 모듈에 위임한다."""

from copy import deepcopy
from ui.uploads import session_upload
from uuid import uuid4

import streamlit as st

from strategies.models import (
    INDICATOR_SPECS, MAX_CONDITIONS_PER_DIRECTION, Direction, Indicator,
    OperandKind, Operator, StrategyValidationError, compatible_indicators,
    default_strategy, describe_group,
)
from strategies.storage import (
    download_filename, strategy_from_dict, strategy_from_json, strategy_to_dict,
    strategy_to_json,
)


def _initialize():
    if "_strategy_draft" not in st.session_state:
        st.session_state["_strategy_draft"] = strategy_to_dict(default_strategy())
        st.session_state["_strategy_revision"] = 0
    st.session_state.setdefault("strategy_name", st.session_state["_strategy_draft"]["name"])


def load_builder_json(payload: str | bytes):
    """검증 성공 후에만 편집 상태를 교체한다. 콜백 또는 위젯 생성 전에 호출한다."""
    strategy = strategy_from_json(payload)
    st.session_state["_strategy_draft"] = strategy_to_dict(strategy)
    st.session_state["_strategy_revision"] = st.session_state.get("_strategy_revision", 0) + 1
    st.session_state["strategy_name"] = strategy.name
    st.session_state["_strategy_notice"] = ("success", "전략을 불러왔습니다.")


def _load_uploaded():
    uploaded = st.session_state.get("strategy_upload") or st.session_state.get("_ui_upload_strategy_upload")
    try:
        if uploaded is None:
            raise StrategyValidationError("불러올 전략 JSON 파일을 선택해 주세요.")
        load_builder_json(uploaded.getvalue())
    except StrategyValidationError as error:
        st.session_state["_strategy_notice"] = ("error", str(error))


def _add_condition(direction):
    draft = deepcopy(st.session_state["_strategy_draft"])
    conditions = draft[direction.lower()]["conditions"]
    if len(conditions) < MAX_CONDITIONS_PER_DIRECTION:
        template = deepcopy(strategy_to_dict(default_strategy())[direction.lower()]["conditions"][0])
        template["id"] = uuid4().hex
        conditions.append(template)
        st.session_state["_strategy_draft"] = draft


def _delete_condition(direction, condition_id):
    draft = deepcopy(st.session_state["_strategy_draft"])
    group = draft[direction.lower()]
    group["conditions"] = [item for item in group["conditions"] if item["id"] != condition_id]
    st.session_state["_strategy_draft"] = draft


def _choice(key, options, initial):
    if key not in st.session_state or st.session_state[key] not in options:
        st.session_state[key] = initial if initial in options else options[0]


def _render_direction(direction):
    group = st.session_state["_strategy_draft"][direction.lower()]
    revision = st.session_state["_strategy_revision"]
    logic_key = f"strategy_{revision}_{direction}_logic"
    _choice(logic_key, ["AND", "OR"], group["combine"])
    group["combine"] = st.selectbox(
        "모든 조건 결합", ["AND", "OR"], key=logic_key,
        help="한 방향의 모든 조건은 동일한 AND 또는 OR로 결합합니다.",
     persist_state="session")
    if not group["conditions"]:
        st.info("조건이 없어서 이 방향은 비활성입니다.")
    for position, condition in enumerate(group["conditions"], 1):
        prefix = f"strategy_{revision}_{direction}_{condition['id']}"
        with st.container(border=True):
            st.caption(f"{direction} 조건 {position}")
            left_options = [item.value for item in Indicator]
            _choice(f"{prefix}_left", left_options, condition["left"])
            left_value = st.selectbox(
                "지표", left_options, key=f"{prefix}_left",
                format_func=lambda value: INDICATOR_SPECS[Indicator(value)].label,
             persist_state="session")
            left = Indicator(left_value)
            condition["left"] = left_value
            operator_options = [item.value for item in Operator]
            _choice(f"{prefix}_operator", operator_options, condition["operator"])
            condition["operator"] = st.selectbox("연산자", operator_options, key=f"{prefix}_operator", persist_state="session")
            compatible = compatible_indicators(left)
            kinds = ["number", "indicator"] if compatible else ["number"]
            _choice(f"{prefix}_kind", kinds, condition["right"]["kind"])
            kind = st.selectbox(
                "비교 대상 유형", kinds, key=f"{prefix}_kind",
                format_func=lambda value: "숫자 상수" if value == "number" else "호환 지표",
             persist_state="session")
            if kind == OperandKind.INDICATOR.value:
                options = [item.value for item in compatible]
                _choice(f"{prefix}_right", options, condition["right"].get("indicator", options[0]))
                right = st.selectbox(
                    "비교 지표", options, key=f"{prefix}_right",
                    format_func=lambda value: INDICATOR_SPECS[Indicator(value)].label,
                 persist_state="session")
                condition["right"] = {"kind": kind, "indicator": right}
            else:
                spec = INDICATOR_SPECS[left]
                number_key = f"{prefix}_number"
                default = condition["right"].get("value", 30.0 if left is Indicator.RSI else 0.0)
                st.session_state.setdefault(number_key, float(default))
                number = st.session_state[number_key]
                if spec.minimum is not None and number < spec.minimum:
                    st.session_state[number_key] = float(spec.minimum)
                    st.caption("기준값을 새 지표의 허용 범위로 조정했습니다.")
                if spec.maximum is not None and number > spec.maximum:
                    st.session_state[number_key] = float(spec.maximum)
                    st.caption("기준값을 새 지표의 허용 범위로 조정했습니다.")
                value = st.number_input(
                    "기준값", min_value=None if spec.minimum is None else float(spec.minimum),
                    max_value=None if spec.maximum is None else float(spec.maximum),
                    key=number_key, step=0.5,
                 persist_state="session")
                condition["right"] = {"kind": kind, "value": value}
                if not compatible:
                    st.caption("이 지표는 허용 범위의 숫자 상수와 비교합니다.")
            st.button("조건 삭제", key=f"{prefix}_delete", on_click=_delete_condition,
                      args=(direction, condition["id"]), width="stretch")
    st.button(
        f"{direction} 조건 추가", key=f"strategy_add_{direction}",
        on_click=_add_condition, args=(direction,), width="stretch",
        disabled=len(group["conditions"]) >= MAX_CONDITIONS_PER_DIRECTION,
    )


def render_strategy_builder(*, expanded=False):
    _initialize()
    strategy = None
    with st.expander("전략 조건 조립기", expanded=expanded):
        name = st.text_input("전략 이름", key="strategy_name", max_chars=80, persist_state="session")
        long_tab, short_tab = st.tabs(["LONG 진입 조건", "SHORT 진입 조건"])
        with long_tab:
            _render_direction(Direction.LONG.value)
        with short_tab:
            _render_direction(Direction.SHORT.value)
        st.session_state["_strategy_draft"]["name"] = name
        try:
            strategy = strategy_from_dict(st.session_state["_strategy_draft"])
        except StrategyValidationError as error:
            st.error(f"조건 검증 실패: {error}")
        else:
            st.success("전략 조건 검증 완료")
            st.text(describe_group(strategy.long) + "\n\n" + describe_group(strategy.short))
            st.download_button(
                "전략 JSON 저장", data=strategy_to_json(strategy).encode("utf-8"),
                file_name=download_filename(strategy), mime="application/json",
                key="strategy_download", on_click="ignore", width="stretch",
            )
        session_upload("전략 JSON 파일", type=["json"], key="strategy_upload", max_upload_size=1)
        st.button("전략 불러오기", key="strategy_load", on_click=_load_uploaded, width="stretch")
        notice = st.session_state.pop("_strategy_notice", None)
        if notice:
            (st.success if notice[0] == "success" else st.error)(notice[1])
    return strategy
