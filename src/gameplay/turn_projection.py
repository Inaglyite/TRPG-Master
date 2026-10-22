"""每轮重建的“当前目标与权威状态摘要”投影。

唯一数据源是权威世界状态（world_store）与本轮已结算的引擎结果（检定、
发现、遭遇、裁决回执）。历史压缩（history_compactor / context_summary）
只处理叙事回忆，绝不参与本投影；读档与分支切换后按新世界重新生成。

输出分两层：

- ``keeper``：守秘人（模型）私有投影，含候选未来目标、未揭示线索、
  NPC 内心态度等——只用于叙述/裁决上下文，绝不进入玩家正文。
- ``public``：玩家可见投影，只含玩家已经知道或现场可见的事实；
  状态不确定时显式标 ``unknown``，不用模组资料代替当前事实。

摘要绑定模组身份与版本：世界内身份缺失或与运行时不符时给出可诊断结果
（``diagnostics``），并停用模组创作内容派生的候选目标，不套用另一版本。
"""

from __future__ import annotations

import json
from typing import Any

from src.ai.context.context_summary import CONTROL_MESSAGE_PREFIX
from src.gameplay.case_clocks import clock_status
from src.gameplay.encounters import SceneEncounterResolution
from src.gameplay.endings import eligible_endings
from src.gameplay.scene_speaker_presence import (
    departed_speaker_briefs,
    departed_speakers,
    speaker_display_name,
)

# 正常回合的权威块远低于此预算；只有恶性膨胀（长历史堆积的大字段）
# 才触发裁剪。预算紧张时优先保护当前目标、待决事项与已结算结果。
DEFAULT_CHAR_BUDGET = 24_000


def _world_source(path: str) -> dict:
    return {"kind": "world", "path": path}


def _turn_source(turn_id: str | None) -> dict:
    return {"kind": "turn", "id": str(turn_id or "unknown")}


def _engine_turn_id(engine: Any) -> str | None:
    if engine is None:
        return None
    turn_id = getattr(engine, "active_turn_id", None)
    if turn_id is None:
        turn_id = getattr(engine, "_active_turn_id", None)
    return str(turn_id) if turn_id else None


def module_identity(world: dict, engine: Any = None) -> tuple[dict, list[dict], bool]:
    """模组身份与版本绑定。

    返回 ``(identity, diagnostics, authored_available)``。世界身份与运行时
    解析到的模组记录不符时 ``authored_available`` 为 False：调用方不得再
    套用模组创作内容派生的目标与候选线索。
    """
    meta = world.get("module_meta")
    identity = dict(meta) if isinstance(meta, dict) else {}
    if not identity.get("id") and world.get("module"):
        identity["id"] = world.get("module")
    if identity.get("version") is None and world.get("module_version") is not None:
        identity["version"] = world.get("module_version")
    diagnostics: list[dict] = []
    if not identity.get("id"):
        diagnostics.append(
            {
                "code": "module_identity_missing",
                "detail": "世界状态缺少 module_meta/module 标识，无法绑定模组版本。",
            }
        )
        return identity, diagnostics, False
    record = getattr(getattr(engine, "context", None), "module_record", None)
    runtime_version = getattr(record, "version", None) if record is not None else None
    # legacy 模组记录没有真实版本概念（version == "legacy"），无法构成版本
    # 绑定，跳过比对；installed 包的 package_id/version 即清单 id/version。
    if (
        record is not None
        and identity.get("id")
        and runtime_version
        and str(runtime_version) != "legacy"
    ):
        runtime_id = getattr(record, "package_id", None) or getattr(record, "id", None)
        world_version = identity.get("version")
        id_mismatch = bool(runtime_id) and str(runtime_id) != str(identity.get("id"))
        version_mismatch = world_version is None or str(runtime_version) != str(world_version)
        if id_mismatch or version_mismatch:
            diagnostics.append(
                {
                    "code": "module_version_mismatch",
                    "world": {"id": identity.get("id"), "version": world_version},
                    "runtime": {"id": runtime_id, "version": runtime_version},
                    "detail": "运行时模组与本世界创建时的模组身份/版本不符；模组创作目标与候选线索已停用。",
                }
            )
            return identity, diagnostics, False
    return identity, diagnostics, True


def goal_projection(world: dict) -> dict:
    """当前目标分层投影：未解决的当前目标与已完成目标。

    数据源是 ``pc._check_history`` 中按稳定 goal_id 记录的检定回执；
    同一目标取最近一次结算状态，支持并行目标与部分完成。
    """
    history = (world.get("pc") or {}).get("_check_history") or []
    latest: dict[str, dict] = {}
    order: list[str] = []
    for row in history[-96:]:
        if not isinstance(row, dict) or not row.get("goal_id"):
            continue
        goal_id = str(row["goal_id"])
        if goal_id not in latest:
            order.append(goal_id)
        latest[goal_id] = row
    open_goals: list[dict] = []
    completed: list[dict] = []
    for goal_id in order:
        row = latest[goal_id]
        achieved = bool(row.get("goal_achieved") or row.get("success"))
        entry = {
            "goal_id": goal_id,
            "goal": str(row.get("goal") or ""),
            "status": "achieved" if achieved else "open",
            "scene_id": str(row.get("scene_id") or "unknown"),
            "source": {
                "kind": "check",
                "check_id": str(row.get("check_id") or "unknown"),
                "turn_id": str(row.get("source_turn_id") or "unknown"),
                "resolution_id": str(row.get("resolution_id") or "unknown"),
            },
        }
        (completed if achieved else open_goals).append(entry)
    return {"open": open_goals[-12:], "completed": completed[-12:]}


def pending_matters(world: dict, engine: Any = None, turn_id: str | None = None) -> list[dict]:
    """待决事项：出发待决、本回合待答问题、悬置的战斗决定。"""
    matters: list[dict] = []
    pending = world.get("pending_departure")
    if isinstance(pending, dict) and pending.get("destination_scene_id"):
        matters.append(
            {
                "kind": "departure_pending",
                "question": str(pending.get("question") or ""),
                "destination_scene_id": str(pending.get("destination_scene_id") or ""),
                "source": _world_source("pending_departure"),
            }
        )
    resolution = getattr(engine, "_action_resolution", None)
    if resolution is not None and getattr(resolution, "is_departure_pending", False):
        question = str(getattr(resolution, "departure_question", "") or "")
        if question and not any(
            matter["kind"] == "departure_pending" and matter["question"] == question
            for matter in matters
        ):
            matters.append(
                {
                    "kind": "departure_question",
                    "question": question,
                    "destination_scene_id": str(
                        getattr(resolution, "pending_destination_scene_id", None) or ""
                    ),
                    "source": _turn_source(turn_id),
                }
            )
    combat = world.get("combat_state")
    decision = combat.get("pending_decision") if isinstance(combat, dict) else None
    if isinstance(decision, dict) and decision.get("id"):
        matters.append(
            {
                "kind": "combat_decision",
                "decision_id": str(decision.get("id")),
                "source": _world_source("combat_state.pending_decision"),
            }
        )
    return matters


def planned_destination(world: dict, engine: Any = None) -> dict | None:
    """计划目的地（含出发待决）；名称无法确定时显式标 unknown。"""
    destination_id = ""
    source = _world_source("pending_departure")
    pending = world.get("pending_departure")
    if isinstance(pending, dict) and pending.get("destination_scene_id"):
        destination_id = str(pending["destination_scene_id"])
    resolution = getattr(engine, "_action_resolution", None)
    if not destination_id and resolution is not None and getattr(resolution, "is_arrival", False):
        return None
    if not destination_id and resolution is not None:
        candidate = getattr(resolution, "pending_destination_scene_id", None) or getattr(
            resolution, "destination_scene_id", None
        )
        if candidate:
            destination_id = str(candidate)
            source = _turn_source(_engine_turn_id(engine))
    if not destination_id:
        return None
    catalog = world.get("scene_catalog") or {}
    definition = catalog.get(destination_id)
    name = (
        str(definition.get("name") or "")
        if isinstance(definition, dict)
        else ""
    )
    return {
        "scene_id": destination_id,
        "name": name or "unknown",
        "departure_pending": bool(pending) or bool(getattr(resolution, "is_departure_pending", False)),
        "source": source,
    }


def presence_projection(world: dict, turn_id: str | None = None) -> dict:
    """人物在场/离场投影：keeper 完整视图 + 玩家可见简表。"""
    scene = world.get("current_scene") or {}
    present_ids = {str(key) for key in scene.get("npcs_present", [])}
    present: list[dict] = []
    npc_briefs: dict[str, dict] = {}
    for npc in world.get("npcs", []):
        if not isinstance(npc, dict) or not npc.get("id"):
            continue
        revealed = npc.get("revealed") or {}
        brief = {
            "id": npc.get("id"),
            "name": npc.get("name"),
            "visible_tags": npc.get("visible_tags", []),
        }
        npc_briefs[str(npc["id"])] = brief
        if str(npc.get("id")) not in present_ids:
            continue
        present.append(
            {
                **brief,
                "revealed_level": revealed.get("level", 0),
                "revealed_entries": revealed.get("entries", []),
                "goals": npc.get("goals", []),
                "keeper_private": {"disposition": npc.get("disposition")},
                "source": _world_source("current_scene.npcs_present"),
            }
        )
    departed = departed_speakers(world)
    # Guest display identities participate in the same public presence view.
    from src.gameplay.guest_speakers import guest_registry, registered_guest_ids

    for speaker_id in sorted(registered_guest_ids(world, scene.get("id"))):
        guest = guest_registry(world)[speaker_id]
        brief = {"id": speaker_id, "name": guest.get("display_name"), "visible_tags": []}
        npc_briefs[speaker_id] = brief
        present.append({**brief, "revealed_level": 1, "revealed_entries": [], "goals": [],
                        "source": _world_source("guest_speakers")})
    departed_public = [
        {
            "id": speaker_id,
            "name": speaker_display_name(world, speaker_id) or speaker_id,
            "source": {
                "kind": "turn",
                "id": str(info.get("turn_id") or turn_id or "unknown"),
            },
        }
        for speaker_id, info in sorted(departed.items())
    ]
    return {
        "present": present,
        "npc_briefs": npc_briefs,
        "departed": departed,
        "departed_briefs": departed_speaker_briefs(world),
        "departed_public": departed_public,
    }


def known_clue_ids(world: dict) -> set[str]:
    found = world.get("clues_found")
    ids: set[str] = set()
    if isinstance(found, dict):
        for entries in found.values():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if isinstance(entry, dict):
                    clue_id = entry.get("catalog_id") or entry.get("id")
                    if clue_id:
                        ids.add(str(clue_id))
    return ids


def locked_candidates(
    world: dict,
    *,
    discovered_ids: set[str],
    authored_available: bool,
) -> list[dict]:
    """未解锁后继的分层投影（守秘人私有）。

    候选未来目标不等于已解锁：未发现的场景线索、前置未满足的结局与
    目的地只标 ``locked``，不进入玩家可见层。
    """
    if not authored_available:
        return []
    flags = world.get("flags") or {}
    locked: list[dict] = []
    scene = world.get("current_scene") or {}
    scene_id = str(scene.get("id") or "")
    catalog = world.get("clue_catalog")
    if isinstance(catalog, dict):
        for clue_id, clue in catalog.items():
            if not isinstance(clue, dict) or str(clue_id) in discovered_ids:
                continue
            related = clue.get("related_scenes", [])
            if clue.get("source") != scene_id and scene_id not in related:
                continue
            locked.append(
                {
                    "kind": "clue",
                    "id": str(clue.get("id") or clue_id),
                    "locked": True,
                    "source": _world_source(f"clue_catalog.{clue_id}"),
                }
            )
    for ending in world.get("endings", []) or []:
        if not isinstance(ending, dict) or not ending.get("id"):
            continue
        unmet = sorted(
            str(key)
            for key, value in (ending.get("required_flags") or {}).items()
            if flags.get(key) != value
        )
        if unmet:
            locked.append(
                {
                    "kind": "ending",
                    "id": str(ending.get("id")),
                    "unmet_flags": unmet,
                    "locked": True,
                    "source": _world_source("endings"),
                }
            )
    for candidate_id, definition in (world.get("scene_catalog") or {}).items():
        if not isinstance(definition, dict):
            continue
        unmet = sorted(
            str(key)
            for key, value in (definition.get("required_flags") or {}).items()
            if flags.get(key) != value
        )
        if unmet:
            locked.append(
                {
                    "kind": "destination",
                    "id": str(candidate_id),
                    "unmet_flags": unmet,
                    "locked": True,
                    "source": _world_source(f"scene_catalog.{candidate_id}"),
                }
            )
    return locked[:20]


def _recent_known_clues(world: dict) -> list[dict]:
    known: list[dict] = []
    groups = world.get("clues_found", {})
    if isinstance(groups, dict):
        for category, clues in groups.items():
            if not isinstance(clues, list):
                continue
            for clue in clues[-8:]:
                if isinstance(clue, dict):
                    known.append(
                        {
                            "id": clue.get("catalog_id") or clue.get("id"),
                            "category": category,
                            "text": str(clue.get("text") or "")[:220],
                        }
                    )
    return known


def _scene_clue_candidates(world: dict) -> list[dict]:
    scene = world.get("current_scene") or {}
    # 已发现的线索不再是"待调查"候选：已取得的实物（含 granted_item）不得
    # 再作为待寻找目标出现在守秘人建议里；它们只在 recent_known_clues 中。
    known = known_clue_ids(world)
    candidates: list[dict] = []
    catalog = world.get("clue_catalog", {})
    if isinstance(catalog, dict):
        for clue_id, clue in catalog.items():
            if not isinstance(clue, dict) or str(clue_id) in known:
                continue
            related_scenes = clue.get("related_scenes", [])
            if clue.get("source") != scene.get("id") and scene.get("id") not in related_scenes:
                continue
            candidates.append(
                {
                    "id": clue.get("id") or clue_id,
                    "text": str(clue.get("text") or "")[:300],
                    "category": clue.get("category"),
                    "type": clue.get("type"),
                    "discovery_notes": str(clue.get("discovery_notes") or "")[:300],
                }
            )
    return candidates


def _settled_this_turn(
    engine: Any,
    check_result: dict | None,
    resolved_discoveries: list[dict],
    turn_id: str | None,
) -> dict:
    """刚结算的本轮结果：检定回执、发现、遭遇与裁决状态，各带来源。"""
    encounter = getattr(engine, "_encounter_resolution", None)
    outcome = getattr(engine, "_adjudicated_outcome", None)
    return {
        "turn_id": str(turn_id or "unknown"),
        "check": (
            {
                "receipt_id": str(
                    check_result.get("check_id") or check_result.get("id") or "unknown"
                ),
                "result": check_result,
            }
            if isinstance(check_result, dict)
            else None
        ),
        "discoveries": [
            {
                "clue_id": discovery.get("clue_id"),
                "discovered": bool(discovery.get("discovered")),
            }
            for discovery in resolved_discoveries
            if isinstance(discovery, dict)
        ],
        "encounter_scene_id": (
            encounter.scene_id if isinstance(encounter, SceneEncounterResolution) else None
        ),
        "adjudication_status": (
            str(outcome.get("status")) if isinstance(outcome, dict) and outcome.get("status") else None
        ),
        "source": _turn_source(turn_id),
    }


def build_turn_projection(
    world: dict,
    *,
    engine: Any = None,
    check_result: dict | None = None,
    resolved_discoveries: list[dict] | None = None,
    char_budget: int = DEFAULT_CHAR_BUDGET,
) -> dict:
    """从权威状态重建本轮“当前目标与权威状态摘要”。

    每轮调用都从传入的世界快照重新派生；不读取、也不依赖任何历史压缩
    产物。返回 ``available/diagnostics/module/public/keeper/budget``。
    """
    resolved_discoveries = resolved_discoveries or []
    turn_id = _engine_turn_id(engine)
    identity, diagnostics, authored_available = module_identity(world, engine)

    pc = world.get("pc", {})
    scene = world.get("current_scene", {})
    present_npc_ids = set(scene.get("npcs_present", []))
    presence = presence_projection(world, turn_id)
    goals = goal_projection(world)
    matters = pending_matters(world, engine, turn_id)
    destination = planned_destination(world, engine)
    discovered_ids = known_clue_ids(world)
    locked = locked_candidates(
        world, discovered_ids=discovered_ids, authored_available=authored_available
    )

    module_rules = world.get("module_rules") or {}
    messages = getattr(engine, "messages", None) or []
    prefix = getattr(engine, "CONTROL_MESSAGE_PREFIX", None) or CONTROL_MESSAGE_PREFIX
    latest_content = str(messages[-1].get("content") or "") if messages else ""
    opening = (
        str(world.get("module_opening") or "")[:1600]
        if authored_available
        and latest_content.startswith(prefix)
        and "开始新游戏" in latest_content
        else ""
    )
    action_resolution = getattr(engine, "_action_resolution", None)
    encounter_resolution = getattr(engine, "_encounter_resolution", None)
    arrival_only = bool(
        action_resolution and getattr(action_resolution, "is_arrival", False)
    )
    model_transition = bool(
        action_resolution
        and getattr(action_resolution, "transition_kind", None) == "model_adjudicated"
    )
    entry_beat = scene.get("entry_beat") if arrival_only and not model_transition else None
    if (
        not isinstance(entry_beat, dict)
        or str(entry_beat.get("npc_id") or "") not in present_npc_ids
    ):
        entry_beat = None
    known_clues = _recent_known_clues(world)
    opening_public_facts = (
        [str(clue.get("text") or "")[:400] for clue in known_clues] if opening else []
    )
    newly_confirmed_facts = [
        str(discovery.get("text") or "")[:400]
        for discovery in resolved_discoveries
        if isinstance(discovery, dict) and discovery.get("discovered") and discovery.get("text")
    ]
    scene_clues = _scene_clue_candidates(world) if authored_available else []
    settled = _settled_this_turn(engine, check_result, resolved_discoveries, turn_id)

    keeper: dict[str, Any] = {
        "pc": {
            "name": pc.get("name"),
            "occupation": pc.get("occupation"),
            "hp": pc.get("hp"),
            "max_hp": pc.get("max_hp"),
            "san": pc.get("san"),
            "max_san": pc.get("max_san"),
            "inventory": pc.get("inventory", []),
            "conditions": pc.get("conditions", []),
        },
        "current_scene": {
            "id": scene.get("id"),
            "name": scene.get("name"),
            "description": str(scene.get("description") or "")[:900],
            "exits": scene.get("exits", []),
            "npcs_present": scene.get("npcs_present", []),
            "npc_public_state": [
                {key: value for key, value in npc.items() if key != "goals"}
                for npc in presence["present"]
            ],
        },
        "guest_speakers": world.get("guest_speakers", {}),
        "departed_speakers": presence["departed"],
        "flags": world.get("flags", {}),
        "eligible_endings": eligible_endings(world) if authored_available else [],
        "case_clocks": clock_status(
            world.get("case_clocks"), world.get("case_clock_definitions")
        ),
        "recent_known_clues": known_clues[-20:],
        "recent_npc_conversations": {
            npc_id: list(entries[-6:])
            for npc_id, entries in (world.get("npc_conversations") or {}).items()
            if npc_id in present_npc_ids and isinstance(entries, list)
        },
        "available_scene_clues": scene_clues[:20],
        "sanity_triggers": module_rules.get("sanity_triggers", []) if authored_available else [],
        "keeper_memory": {
            "goals_and_plans": str(
                (world.get("private_memory") or {}).get("goals_and_plans") or ""
            )[:700],
            "inference_notes": str(
                (world.get("private_memory") or {}).get("inference_notes") or ""
            )[:700],
        },
        "resolved_check": check_result,
        "resolved_discoveries": resolved_discoveries,
        "resolved_encounter": (
            {
                "scene_id": encounter_resolution.scene_id,
                "present_npc_ids": list(encounter_resolution.present_npc_ids),
                "outcomes": [
                    {
                        "npc_id": outcome.npc_id,
                        "present": outcome.present,
                        "availability": outcome.availability,
                    }
                    for outcome in encounter_resolution.outcomes
                ],
            }
            if isinstance(encounter_resolution, SceneEncounterResolution)
            else None
        ),
        "scene_entry_beat": entry_beat,
        "narrative_fact_scope": {
            "closed_world_for_this_action": bool(
                opening or arrival_only or check_result is not None or resolved_discoveries
            ),
            "mode": "module_opening" if opening else "normal_turn",
            "opening_public_facts": opening_public_facts,
            "uncatalogued_opening_details": (
                "flavor_only_never_persist_as_clue_or_state" if opening else ""
            ),
            "newly_confirmed_facts": newly_confirmed_facts,
            "unlisted_observations": "本行动没有额外可验证发现",
            "action_resolution": (
                action_resolution.public_contract()
                if action_resolution is not None
                and callable(getattr(action_resolution, "public_contract", None))
                else None
            ),
            "arrival_only": arrival_only,
        },
        "module_opening": opening,
        # —— 目标与待决的分层投影（本模块新增，T05/T06/T09 共用数据源）——
        "goals": goals,
        "pending_matters": matters,
        "planned_destination": destination,
        "locked_candidates": locked,
        "settled_this_turn": settled,
        "presence": {
            "npc_briefs": presence["npc_briefs"],
            "departed_briefs": presence["departed_briefs"],
            "departed_public": presence["departed_public"],
        },
    }

    public: dict[str, Any] = {
        "scene": {
            "id": scene.get("id") or "unknown",
            "name": scene.get("name") or "unknown",
            # Authored scene descriptions can contain a hidden room, item or
            # NPC schedule. Only an explicitly public description is public.
            "description": str(scene.get("public_description") or "")[:900],
            "exits": scene.get("exits", []),
        },
        "planned_destination": destination,
        "pc": {
            "name": pc.get("name"),
            "hp": pc.get("hp"),
            "max_hp": pc.get("max_hp"),
            "san": pc.get("san"),
            "max_san": pc.get("max_san"),
            "inventory": pc.get("inventory", []),
            "conditions": pc.get("conditions", []),
        },
        "present_characters": [
            {
                "id": npc["id"],
                "name": npc["name"],
                "visible_tags": npc["visible_tags"],
                "revealed_entries": npc["revealed_entries"],
            }
            for npc in presence["present"]
        ],
        "departed_characters": [
            {"id": entry["id"], "name": entry["name"]}
            for entry in presence["departed_public"]
        ],
        "open_goals": goals["open"],
        "completed_goals": goals["completed"],
        "pending_matters": matters,
        "known_clues": known_clues[-20:],
        "unknowns": [
            label
            for label, missing in (
                ("current_scene", not scene.get("id")),
                (
                    "planned_destination_name",
                    bool(destination) and destination.get("name") == "unknown",
                ),
            )
            if missing
        ],
    }

    payload: dict[str, Any] = {"module": identity, **keeper}
    if diagnostics:
        payload["diagnostics"] = diagnostics
    budget = _apply_budget(payload, char_budget)
    return {
        "available": authored_available,
        "diagnostics": diagnostics,
        "module": identity,
        "turn_id": turn_id,
        "public": public,
        "keeper": {key: value for key, value in payload.items() if key != "module"},
        "budget": budget,
    }


def _payload_chars(payload: dict) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _apply_budget(payload: dict, char_budget: int) -> dict:
    """预算优先裁剪：先缩短说明文字，再裁低优先级块，记录每步行为。

    受保护块（当前目标、待决事项、已结算结果、在场名单、事实边界）永不
    裁剪；只缩短描述性文字或丢弃低优先级列表的尾部。
    """
    actions: list[dict] = []
    initial = _payload_chars(payload)

    def record(step: str, before: int) -> None:
        after = _payload_chars(payload)
        if after < before:
            actions.append({"step": step, "before_chars": before, "after_chars": after})

    def over() -> bool:
        return _payload_chars(payload) > char_budget

    scene = payload.get("current_scene")
    if over() and isinstance(scene, dict) and len(str(scene.get("description") or "")) > 400:
        before = _payload_chars(payload)
        scene["description"] = str(scene.get("description") or "")[:400]
        record("shorten_scene_description", before)
    memory = payload.get("keeper_memory")
    if over() and isinstance(memory, dict):
        before = _payload_chars(payload)
        for key in ("goals_and_plans", "inference_notes"):
            memory[key] = str(memory.get(key) or "")[:300]
        record("shorten_keeper_memory", before)
    clues = payload.get("available_scene_clues")
    if over() and isinstance(clues, list) and clues:
        before = _payload_chars(payload)
        payload["available_scene_clues"] = [
            {**clue, "text": str(clue.get("text") or "")[:160],
             "discovery_notes": str(clue.get("discovery_notes") or "")[:160]}
            for clue in clues[:10]
            if isinstance(clue, dict)
        ]
        record("trim_scene_clue_candidates", before)
    known = payload.get("recent_known_clues")
    if over() and isinstance(known, list) and len(known) > 8:
        before = _payload_chars(payload)
        payload["recent_known_clues"] = [
            {**clue, "text": str(clue.get("text") or "")[:120]}
            for clue in known[-8:]
            if isinstance(clue, dict)
        ]
        record("trim_known_clues", before)
    conversations = payload.get("recent_npc_conversations")
    if over() and conversations:
        before = _payload_chars(payload)
        payload["recent_npc_conversations"] = {}
        record("drop_npc_conversations", before)
    locked = payload.get("locked_candidates")
    if over() and isinstance(locked, list) and len(locked) > 10:
        before = _payload_chars(payload)
        payload["locked_candidates"] = locked[:10]
        record("trim_locked_candidates", before)
    goals = payload.get("goals")
    if over() and isinstance(goals, dict) and len(goals.get("completed") or []) > 5:
        before = _payload_chars(payload)
        goals["completed"] = goals["completed"][-5:]
        record("trim_completed_goals", before)

    sections = {
        key: _payload_chars({key: value})
        for key, value in payload.items()
    }
    return {
        "char_budget": char_budget,
        "initial_chars": initial,
        "final_chars": _payload_chars(payload),
        "over_budget": _payload_chars(payload) > char_budget,
        "actions": actions,
        "sections": sections,
    }
