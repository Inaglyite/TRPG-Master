"""时间型案件时钟的确定性结算：模组声明规则，引擎按实际结算时间推进。

模组在 ``case_clock_definitions.<clock>.time_advance`` 声明结构化规则：

    every_minutes  累计多少游戏内分钟推进一次（阈值）
    advance        每次推进的级数（默认 1，保持"每次最多推进一级"）
    daily_cap      单次结算最多推进几级（上限，缺省不额外限制）
    activity       计入的结算类型（适用条件；缺省只计 wait）
    carry          未达阈值的余量是否结转（默认 true）

设计边界：

- 只有声明了 ``time_advance`` 的时钟才按时间推进；没有声明的时钟不受影响，
  因此不存在"每等一天所有时钟都推进"。
- 幂等：进度锚点是 ``world_clock.elapsed_minutes`` 的绝对值，重复结算
  （裁决重试、审计重放、读档恢复）不会重复记账；锚点与余量随世界状态一起
  持久化，读档后按该分支自己的锚点继续计算。
- 时间回溯（锚点大于当前时间，例如读回更早的分支）时重置锚点，不补记。
- 语义事件（欺骗、暴露、公开指控、反复阅读文档等）仍由模型提出，
  由裁决/审计校验后落账；本模块只负责时间这一条确定性通道。
"""

from __future__ import annotations

STATE_KEY = "case_clock_time"


def _elapsed_minutes(world: dict) -> int:
    """游戏内已流逝分钟数（与 world_time.elapsed_minutes 同口径，避免循环导入）。"""
    clock = world.get("world_clock") or {}
    if not isinstance(clock, dict):
        return 0
    return max(0, _int_or(clock.get("elapsed_minutes"), 0))


def _int_or(value: object, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return int(value)


def _activity_allowed(rule: dict, activity: str) -> bool:
    """适用条件：规则声明了 activity 时，只有匹配的结算类型计入。"""
    allowed = rule.get("activity")
    if not isinstance(allowed, list) or not allowed:
        return activity == "wait"
    return activity in {str(item) for item in allowed}


def settle_time_clocks(
    world: dict, *, activity: str = "", new_time_start: int | None = None
) -> list[dict]:
    """按实际结算时间推进时间型案件时钟；返回推进事件（可能为空）。

    ``activity`` 是本回合的时间结算类型（裁决 intent），用于适用条件过滤；
    不适用的结算只推进锚点、不计入余量，避免把非拖延时间算进阈值。
    ``new_time_start`` 是本次时间推进前的游戏内分钟数：首次为某个时钟建立
    锚点时用它，使本次推进的时长计入阈值（否则第一次结算会被锚点吞掉）。
    """
    definitions = world.get("case_clock_definitions")
    clocks = world.get("case_clocks")
    if not isinstance(definitions, dict) or not isinstance(clocks, dict) or not clocks:
        return []
    state = world.get(STATE_KEY)
    if not isinstance(state, dict):
        state = {}
        world[STATE_KEY] = state
    now = _elapsed_minutes(world)
    events: list[dict] = []
    for clock_id, definition in definitions.items():
        if clock_id not in clocks or not isinstance(definition, dict):
            continue
        rule = definition.get("time_advance")
        if not isinstance(rule, dict):
            continue
        every = _int_or(rule.get("every_minutes"), 0)
        if every <= 0:
            continue
        entry = state.get(clock_id)
        if not isinstance(entry, dict):
            entry = {"anchor": new_time_start if new_time_start is not None else now, "carry": 0}
            state[clock_id] = entry
        anchor = _int_or(entry.get("anchor"), now)
        carry = max(0, _int_or(entry.get("carry"), 0))
        if now < anchor:
            # 读档回到更早的分支：按该分支状态继续，不补记更早的区间。
            entry["anchor"] = now
            entry["carry"] = 0
            continue
        if not _activity_allowed(rule, activity):
            # 适用条件不满足：只推进锚点，这段时间不计入余量。
            entry["anchor"] = now
            entry["carry"] = carry
            continue
        delta = now - anchor
        if delta <= 0:
            continue
        keep_carry = rule.get("carry", True) is not False
        pending = carry + delta if keep_carry else delta
        steps = pending // every
        entry["anchor"] = now
        if steps <= 0:
            entry["carry"] = pending if keep_carry else 0
            continue
        cap = rule.get("daily_cap")
        if isinstance(cap, (int, float)) and not isinstance(cap, bool) and int(cap) > 0:
            steps = min(steps, int(cap))
        step = max(1, _int_or(rule.get("advance"), 1))
        before = _int_or(clocks.get(clock_id), 0)
        maximum = definition.get("max")
        ceiling = (
            int(maximum)
            if isinstance(maximum, (int, float)) and not isinstance(maximum, bool)
            else before + steps * step
        )
        after = min(before + steps * step, ceiling)
        clocks[clock_id] = after
        if after != before:
            events.append(
                {
                    "type": "clock_advance",
                    "target": clock_id,
                    "before": before,
                    "after": after,
                    "source": "time",
                }
            )
        # 余量封顶在阈值以下：长蒙太奇不会积压出后续多级跳进。
        entry["carry"] = min(pending - steps * every, every - 1) if keep_carry else 0
    return events
