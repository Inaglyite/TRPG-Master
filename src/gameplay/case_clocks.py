"""案件时钟（doom clock）的每轮权威视图。

时钟数值存于 ``world["case_clocks"]``，模组声明的等级表存于
``world["case_clock_definitions"]``（levels / advance_when / max）。
本模块把两者合成叙事模型每轮可见的状态视图：当前值、下一级征兆、
推进情形——叙事只需照下一级征兆给氛围，记账由回合审计与引擎完成。
"""

from __future__ import annotations


def clock_status(case_clocks: object, definitions: object) -> dict:
    """每轮权威状态里的时钟视图。

    无等级表时原样返回数值字典（向后兼容）；有等级表时每个时钟附
    ``next_level``（下一级征兆描述）与 ``advance_when``（推进情形），
    叙事模型据此主动写出对应等级的征兆，而不是等时钟先动。
    """
    if not isinstance(case_clocks, dict):
        return {}
    if not isinstance(definitions, dict) or not definitions:
        return dict(case_clocks)
    status: dict[str, object] = {}
    for clock_id, value in case_clocks.items():
        definition = definitions.get(clock_id)
        if not isinstance(definition, dict):
            status[clock_id] = value
            continue
        entry: dict[str, object] = {"value": value}
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            levels = definition.get("levels")
            if isinstance(levels, dict):
                next_text = levels.get(str(int(value) + 1))
                if next_text:
                    entry["next_level"] = str(next_text)
        max_value = definition.get("max")
        if isinstance(max_value, int) and not isinstance(max_value, bool):
            entry["max"] = max_value
        advance_when = definition.get("advance_when")
        if isinstance(advance_when, list) and advance_when:
            entry["advance_when"] = [str(item) for item in advance_when[:6]]
        status[clock_id] = entry
    return status
