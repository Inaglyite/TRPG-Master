"""Project the current world's legal effect targets into the model schema."""

from __future__ import annotations

import copy

from src.gameplay.action_resolution import pc_incapacitated
from src.gameplay.skill_view import effective_skills


def constrain_adjudication_schema(schema: dict, world: dict, *, candidate_ids: list[str], items: dict[str, str]) -> dict:
    properties = schema["properties"]
    properties["intent"]["description"] = "move 实际抵达；prepare_move 出发前待决；wait 包括持续一小时以上的等待、蹲守或监视；interact 是现场短行动；clarify 不执行行动"
    properties["destination_scene_id"]["enum"] = ["", *(world.get("scene_catalog") or {})]
    check_skill = schema["$defs"]["CheckProposal"]["properties"]["skill"]
    # 与掷骰层共用同一有效技能视图：规则表基础值可补卡上缺项，虚构技能没有枚举位。
    check_skill["enum"] = sorted(effective_skills(world.get("pc") or {}))
    check_skill["description"] = "只能从此枚举选择；卡上未列出的规则技能按规则表基础值结算"
    if candidate_ids:
        properties["discovery_refs"]["items"]["enum"] = candidate_ids
    else:
        properties["discovery_refs"]["maxItems"] = 0

    present = list((world.get("current_scene") or {}).get("npcs_present") or [])
    from src.gameplay.destination_grounding import known_people

    known = [person["id"] for person in known_people(world, []) if person.get("id")]
    properties["target_npc_id"]["enum"] = list(dict.fromkeys(["", *present, *known]))
    properties["target_npc_id"]["description"] = (
        "现场交互仅填 npcs_present 中的正式 id；前往已知人物所在地可用已知人物 id。"
        "临时展示人物不填此字段，用 check.target 描述检定对象，不提交正式 NPC 的态度效果。"
    )
    inventory = [item for item in (world.get("pc") or {}).get("inventory", []) if isinstance(item, str)]
    targets = {
        "clock_advance": list(world.get("case_clock_definitions") or {}),
        "npc_reaction": present,
        "take_item": list(dict.fromkeys(items.values())),
        "consume_item": inventory,
        "flag_set": list(world.get("completion_flags") or {}),
        "rescue": present if pc_incapacitated(world) else [],
    }
    from src.gameplay.guest_speakers import registered_guest_ids

    targets["speaker_departure"] = list(dict.fromkeys([
        *present, *sorted(registered_guest_ids(world, str((world.get("current_scene") or {}).get("id") or ""))),
    ]))
    original = schema["$defs"]["EffectProposal"]
    branches = []
    for kind, legal in targets.items():
        if not legal:
            continue
        branch = copy.deepcopy(original)
        branch["properties"]["kind"] = {"type": "string", "const": kind}
        branch["properties"]["target"]["enum"] = legal
        if kind == "npc_reaction":
            branch["properties"]["value"]["enum"] = ["cooperative", "guarded", "nervous", "submissive", "unknown"]
            branch["required"].append("value")
        if kind == "clock_advance":
            branch["required"].append("clock_trigger")
            branch["properties"]["clock_trigger"]["minLength"] = 1
        if kind == "take_item":
            branch["properties"]["source_id"]["enum"] = list(items)
            branch["required"].append("source_id")
        if kind == "flag_set":
            branch["properties"]["target"]["description"] = "实际完成调查后必须记账，即使没有发现新线索。定义：" + str(world.get("completion_flags"))
        branches.append(branch)
    if branches:
        schema["$defs"]["EffectProposal"] = {"anyOf": branches}
    else:
        for name in ("on_success", "on_failure"):
            properties[name]["maxItems"] = 0
    return schema
