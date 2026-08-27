"""模组可声明的确定性危机触发（伏击/显形）。

世界数据顶层 ``crisis_triggers`` 列表。每个触发器声明触发条件与效果：
条件按上一回合末的世界状态判定，满足即由引擎确定性落地——不等待
叙事模型自由发挥。这是古董店终局缺口的修复：keeper 可以无限对话，
但「地下室被入侵 → 费德曼兄妹伏击」「文档被取走 → 怪物显形」是模组
的机械装置，不是气氛选项。

触发器形状（全部键除 id/narrative 外均可选）::

    {
      "id": "feldman_ambush",
      "scene": "trivial_pursuits",                  # 要求当前场景
      "required_flags": {"deep_basement_found": true},
      "forbidden_flags": {"monster_defeated": true},
      "required_clocks": {"monster_manifestation": 4},   # >= 阈值
      "narrative": "……向玩家展示的作者文本……",
      "combat": {"reason": "…", "participants": [{"id": "…", "dex": 45}]},
      "flags_set": {"feldman_ambushed": true},
      "clocks_set": {"monster_manifestation": 5}
    }

每个触发器每世界最多触发一次：机制在触发后自动记录
``flags.crisis_fired_<id>``，不依赖作者记得写自禁止 flag。
"""

from __future__ import annotations

import json
from typing import Any

FIRED_FLAG_PREFIX = "crisis_fired_"


def _flags_match(rule: dict, flags: dict) -> bool:
    """required/forbidden 精确匹配（与 encounters._conditions_match 同语义）。"""
    required = rule.get("required_flags", {})
    forbidden = rule.get("forbidden_flags", {})
    return (
        isinstance(required, dict)
        and isinstance(forbidden, dict)
        and all(flags.get(key) == value for key, value in required.items())
        and all(flags.get(key) != value for key, value in forbidden.items())
    )


def _clocks_met(rule: dict, clocks: dict) -> bool:
    required = rule.get("required_clocks", {})
    if not isinstance(required, dict):
        return True
    for clock_id, threshold in required.items():
        value = clocks.get(clock_id)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return False
        try:
            if value < float(threshold):
                return False
        except (TypeError, ValueError):
            return False
    return True


def select_crisis_trigger(world: dict) -> dict | None:
    """声明序中第一个满足条件的触发器；已触发过的（自动标记）跳过。"""
    if not isinstance(world, dict):
        return None
    triggers = world.get("crisis_triggers", [])
    if not isinstance(triggers, list):
        return None
    flags = world.get("flags", {})
    flags = flags if isinstance(flags, dict) else {}
    clocks = world.get("case_clocks", {})
    clocks = clocks if isinstance(clocks, dict) else {}
    current_scene = str((world.get("current_scene") or {}).get("id") or "")
    for trigger in triggers:
        if not isinstance(trigger, dict):
            continue
        trigger_id = str(trigger.get("id") or "").strip()
        if not trigger_id:
            continue
        if flags.get(f"{FIRED_FLAG_PREFIX}{trigger_id}"):
            continue
        scene = str(trigger.get("scene") or "").strip()
        if scene and scene != current_scene:
            continue
        if not _flags_match(trigger, flags):
            continue
        if not _clocks_met(trigger, clocks):
            continue
        return trigger
    return None


def _clock_max(world: dict, clock_id: str) -> int | None:
    definitions = world.get("case_clock_definitions", {})
    if not isinstance(definitions, dict):
        return None
    definition = definitions.get(clock_id)
    if not isinstance(definition, dict):
        return None
    max_value = definition.get("max")
    return max_value if isinstance(max_value, int) and not isinstance(max_value, bool) else None


def _set_flag(engine: Any, key: str, value: object) -> None:
    engine._execute_tool(
        "state_set",
        {"path": f"flags.{key}", "value": json.dumps(value, ensure_ascii=False)},
    )


def maybe_fire_crisis(engine: Any) -> str:
    """评估并落地一个危机触发器，返回应向玩家展示的作者文本（无则空串）。

    combat 先行：战斗建立失败（如已有进行中的战斗）时整体放弃——不落
    flags/clocks、不标记已触发，下一回合重试。
    """
    try:
        world = engine.context.world_store.load()
    except Exception:
        return ""
    trigger = select_crisis_trigger(world)
    if trigger is None:
        return ""
    trigger_id = str(trigger.get("id"))

    combat = trigger.get("combat")
    if isinstance(combat, dict):
        # 危机战斗按定义是敌方主动袭击：参战 NPC 默认对 PC 敌对，
        # 否则暴力确认门会把危机战斗当成「攻击非敌对者」逐枪取消
        # （真机事故：伏击战中 12 回合射击全被 action_cancelled）。
        # 作者可显式声明 hostile_to_pc=false 保留非敌对参战者。
        participants = []
        for spec in combat.get("participants") or []:
            if isinstance(spec, dict):
                spec = {"hostile_to_pc": True, **spec}
                participants.append(spec)
        output = engine._execute_tool(
            "combat_start",
            {
                "reason": str(combat.get("reason") or trigger_id),
                "participants": participants,
            },
        )
        try:
            result = json.loads(output)
        except json.JSONDecodeError:
            result = {}
        if not result.get("ok"):
            return ""

    flags_set = trigger.get("flags_set", {})
    if isinstance(flags_set, dict):
        for key, value in flags_set.items():
            _set_flag(engine, str(key), value)

    clocks_set = trigger.get("clocks_set", {})
    if isinstance(clocks_set, dict):
        for clock_id, value in clocks_set.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            max_value = _clock_max(world, str(clock_id))
            settled = int(value)
            if max_value is not None:
                settled = min(settled, max_value)
            engine._execute_tool(
                "state_set",
                {"path": f"case_clocks.{clock_id}", "value": json.dumps(settled)},
            )

    _set_flag(engine, f"{FIRED_FLAG_PREFIX}{trigger_id}", True)
    return str(trigger.get("narrative") or "").strip()
