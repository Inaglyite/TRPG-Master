"""Pre-roll semantic adjudication, with bounded and evidence-linked effects.

The model chooses meaningful actions and stakes. Only this validator can turn
that proposal into an ActionResolution; prose is never a second write channel.
"""

from __future__ import annotations

import json
import time
from dataclasses import replace
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.ai.model.llm_concurrency import llm_call_slot
from src.app.config import JUDGEMENT_MODEL, _enabled_env
from src.app.logger import game_event as log_game
from src.app.logger import model_call as log_model_call
from src.gameplay.action_resolution import ActionPhase, ActionResolution, pc_incapacitated
from src.gameplay.discovery import DiscoveryMatch, _known_clue_ids, _rule_flags_met
from src.gameplay.world_time import advance_time, elapsed_minutes


class StrictProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CheckProposal(StrictProposal):
    skill: str = Field(max_length=80)
    required_success_level: Literal["regular", "hard", "extreme"] = "regular"
    bonus_dice: int = Field(default=0, ge=0, le=2)
    penalty_dice: int = Field(default=0, ge=0, le=2)
    reason: str = Field(min_length=1, max_length=300)
    push_context_id: str = Field(default="", max_length=80)
    push_risk: str = Field(default="", max_length=500)


class EffectProposal(StrictProposal):
    kind: Literal["clock_advance", "npc_reaction", "take_item", "consume_item", "flag_set"]
    target: str = Field(min_length=1, max_length=180)
    value: str = Field(default="", max_length=80, description="npc_reaction 时仅限 cooperative/guarded/nervous/submissive/unknown")
    source_id: str = Field(default="", max_length=180)
    evidence_quote: str = Field(min_length=1, max_length=300)
    reason: str = Field(min_length=1, max_length=300)


class ActionProposal(StrictProposal):
    intent: Literal["interact", "move", "wait", "clarify", "decline", "combat"]
    input_quote: str = Field(min_length=1, max_length=1200)
    approach: str = Field(min_length=1, max_length=500)
    target_npc_id: str = Field(default="", max_length=80)
    destination_scene_id: str = Field(default="", max_length=100)
    discovery_refs: list[str] = Field(default_factory=list, max_length=8)
    check: CheckProposal | None = None
    time_minutes: int = Field(default=0, ge=0, le=10080)
    success_description: str = Field(default="", max_length=500)
    failure_description: str = Field(default="", max_length=500)
    on_success: list[EffectProposal] = Field(default_factory=list, max_length=8)
    on_failure: list[EffectProposal] = Field(default_factory=list, max_length=8)
    npc_direction: str = Field(default="", max_length=600)
    sanity_source_id: str = Field(default="", max_length=100)
    sanity_severity: Literal["", "trivial", "minor", "moderate", "major", "catastrophic"] = ""


def discovery_candidates(world: dict) -> dict[str, DiscoveryMatch]:
    scene_id = str((world.get("current_scene") or {}).get("id") or "")
    known = _known_clue_ids(world)
    candidates = {}
    for clue_id, clue in (world.get("clue_catalog") or {}).items():
        if clue_id in known or not isinstance(clue, dict):
            continue
        if scene_id != clue.get("source") and scene_id not in clue.get("related_scenes", []):
            continue
        rules = clue.get("discovery_rules") or [{
            "intent": "search" if clue.get("type") == "hidden" else "examine",
            "targets": [clue.get("discovery_notes") or clue_id],
            "requires_success": clue.get("type") == "hidden",
            "skill": "spot_hidden",
        }]
        for index, rule in enumerate(rules):
            if isinstance(rule, dict) and _rule_flags_met(rule, world):
                candidates[f"{clue_id}:{index}"] = DiscoveryMatch(clue_id, clue, rule)
    return candidates


def available_items(world: dict) -> dict[str, str]:
    items = {}
    scene = world.get("current_scene") or {}
    for item in scene.get("items", []):
        if isinstance(item, str):
            items[f"scene:{scene.get('id')}:{item}"] = item
    present = set(scene.get("npcs_present", []))
    for npc in world.get("npcs", []):
        if npc.get("id") in present:
            for item in npc.get("inventory", []):
                if isinstance(item, str):
                    items[f"npc:{npc['id']}:{item}"] = item
    return items


def adjudication_context(world: dict, content: str, fallback: ActionResolution) -> dict:
    from src.gameplay.sanity_sources import available_sanity_sources

    scene = world.get("current_scene") or {}
    present = set(scene.get("npcs_present", []))
    return {
        "player_input": content,
        "current_scene": {k: v for k, v in scene.items() if k != "document"},
        "pc": {k: world.get("pc", {}).get(k, {}) for k in ("skills", "attributes", "inventory", "conditions", "_push_contexts")},
        "npcs_present": [{k: npc.get(k) for k in ("id", "name", "disposition", "visible_tags", "revealed", "goals")}
                         for npc in world.get("npcs", []) if npc.get("id") in present],
        "destinations": {key: {"name": value.get("name"), "aliases": value.get("aliases", [])}
                         for key, value in (world.get("scene_catalog") or {}).items() if isinstance(value, dict)},
        "discovery_candidates": {ref: {"intent": match.rule.get("intent"), "targets": match.rule.get("targets"),
                                      "requires_success": match.rule.get("requires_success", False), "skill": match.rule.get("skill"),
                                      "difficulty": match.rule.get("difficulty", "regular"),
                                      "grants_item": bool(match.clue.get("granted_item")),
                                      "sets_flags": sorted((match.clue.get("flag_effects") or {}).keys())}
                                 for ref, match in discovery_candidates(world).items()},
        "available_items": available_items(world),
        "case_clocks": world.get("case_clocks", {}),
        "clock_definitions": world.get("case_clock_definitions", {}),
        "elapsed_minutes": elapsed_minutes(world),
        "module_rules": world.get("module_rules", {}),
        "sanity_sources": available_sanity_sources(world),
        "combat_state": world.get("combat_state", {}),
        "pc_incapacitated": pc_incapacitated(world),
        "completion_flags": {
            key: {"achieved": bool((world.get("flags") or {}).get(key)), "description": description}
            for key, description in (world.get("completion_flags") or {}).items()
        },
        "deterministic_hint": fallback.public_contract(),
    }


def validate_proposal(raw: dict, content: str, world: dict, fallback: ActionResolution) -> ActionResolution:
    proposal = ActionProposal.model_validate(raw)
    if proposal.input_quote not in content:
        raise ValueError("input_quote 必须逐字引用玩家输入")
    if pc_incapacitated(world):
        # 失去行动能力的调查员本人不能再执行身体动作：
        # 只能等待/询问/收场；救援与恢复由后续状态或他人完成。
        if proposal.intent not in {"wait", "clarify", "decline"}:
            raise ValueError("调查员已失去行动能力，不能执行身体动作")
        if proposal.check or proposal.discovery_refs or proposal.on_success or proposal.on_failure:
            raise ValueError("失去行动能力时不能产生检定、发现或状态变化")
    present = set((world.get("current_scene") or {}).get("npcs_present", []))
    if proposal.target_npc_id and proposal.target_npc_id not in present:
        raise ValueError("交互目标 NPC 不在当前场景")
    if (world.get("combat_state") or {}).get("active") and proposal.intent != "combat":
        raise ValueError("战斗中必须交由战斗行动流程，不能免费调查或跨场景移动")
    if proposal.intent == "combat" and (proposal.check or proposal.discovery_refs or proposal.on_success or proposal.on_failure or proposal.time_minutes):
        raise ValueError("战斗动作的检定、消耗和结果由战斗工具结算")
    if proposal.intent in {"clarify", "decline"}:
        if proposal.check or proposal.discovery_refs or proposal.on_success or proposal.on_failure or proposal.time_minutes or proposal.sanity_source_id:
            raise ValueError("询问或拒绝执行的行动不能有检定和状态变化")
    current_scene_id = str((world.get("current_scene") or {}).get("id") or "")
    if proposal.intent == "move" and proposal.destination_scene_id == current_scene_id:
        # 同场景内走位（地下室→楼上办公室）不是跨场景旅行：重归类为交互，
        # 否则模型会旁白越界（叙述了搜查/取物）而验证器禁止任何效果落账。
        proposal = proposal.model_copy(update={"intent": "interact", "destination_scene_id": ""})
    if proposal.intent == "move":
        scene = (world.get("scene_catalog") or {}).get(proposal.destination_scene_id)
        if not isinstance(scene, dict):
            raise ValueError("目的地不存在")
        flags = world.get("flags") or {}
        if any(flags.get(k) != v for k, v in scene.get("required_flags", {}).items()):
            raise ValueError("目的地前置条件未满足")
        if proposal.check or proposal.discovery_refs or proposal.on_success or proposal.on_failure or proposal.sanity_source_id:
            raise ValueError("抵达回合只允许旅行；调查与其他后果留到抵达后")
    elif proposal.destination_scene_id:
        raise ValueError("只有移动行动能指定目的地")
    if proposal.time_minutes > 60 and proposal.intent not in {"wait", "move"}:
        raise ValueError("超过一小时的行动需明确为等待或旅行")
    if proposal.time_minutes > 60 and not any(word in content for word in ("等", "天", "日", "夜", "小时", "监视", "守候", "旅行")):
        raise ValueError("玩家没有授权长时间行动")
    skills = {**world.get("pc", {}).get("attributes", {}), **world.get("pc", {}).get("skills", {})}
    if proposal.check and proposal.check.skill not in skills:
        raise ValueError("检定技能不在当前角色表中")
    if proposal.check and not proposal.failure_description:
        raise ValueError("检定前必须定义失败的后果")
    if proposal.check and proposal.check.push_context_id:
        from src.gameplay.check_context import validate_push

        if not any(word in content for word in ("孤注一掷", "再试", "再来", "冒险重试")):
            raise ValueError("孤注一掷需要玩家明确请求重试")
        validate_push(world, proposal.check.skill, proposal.check.push_context_id, proposal.approach, proposal.target_npc_id)
    candidates = discovery_candidates(world)
    from src.gameplay.discovery import disclaims_acquisition

    no_take = disclaims_acquisition(content)
    matches = []
    for ref in proposal.discovery_refs:
        if ref not in candidates:
            raise ValueError("线索不在当前场景可授权的发现候选中")
        match = candidates[ref]
        if no_take and (match.rule.get("intent") == "take" or match.clue.get("granted_item")):
            raise ValueError("玩家明确表示不取得该物品；改为查看/阅读类候选，或不选 discovery_refs")
        if match.rule.get("requires_success"):
            required_skill = str(match.rule.get("skill") or "spot_hidden")
            if not proposal.check or proposal.check.skill != required_skill:
                raise ValueError("必须执行模组规定的技能检定")
            difficulty = str(match.rule.get("difficulty") or "regular")
            from src.gameplay.percentile import REQUIRED_RANKS

            if REQUIRED_RANKS[proposal.check.required_success_level] < REQUIRED_RANKS.get(difficulty, 1):
                raise ValueError("不能降低模组规定的检定难度")
        if not any(existing.clue_id == match.clue_id for existing in matches):
            matches.append(match)
    if proposal.sanity_source_id:
        from src.gameplay.sanity_sources import validate_sanity_source

        validate_sanity_source(world, proposal.sanity_source_id, proposal.sanity_severity)
    elif proposal.sanity_severity:
        raise ValueError("SAN 裁决必须有已声明的恐怖源 ID")
    items = available_items(world)
    for branch in (proposal.on_success, proposal.on_failure):
        effect_keys = [(effect.kind, effect.target) for effect in branch]
        if len(effect_keys) != len(set(effect_keys)):
            raise ValueError("同一结果分支不能重复提交相同效果")
    for effect in [*proposal.on_success, *proposal.on_failure]:
        if effect.evidence_quote not in content:
            raise ValueError("后果必须关联玩家输入中的证据")
        if effect.kind == "clock_advance":
            if effect.target not in (world.get("case_clocks") or {}) or effect.target not in (world.get("case_clock_definitions") or {}):
                raise ValueError("时钟必须由模组声明")
        elif effect.kind == "npc_reaction":
            if effect.target not in present or effect.value not in {"cooperative", "guarded", "nervous", "submissive", "unknown"}:
                raise ValueError("NPC 反应必须限于在场人物的社交态度")
        elif effect.kind == "take_item":
            if no_take:
                raise ValueError("玩家明确表示不取得物品，不能改用 take_item 发放")
            if items.get(effect.source_id) != effect.target:
                redirect = next(
                    (ref for ref, match in candidates.items() if effect.target and effect.target in str(match.clue.get("text") or "")),
                    None,
                ) or next(
                    (ref for ref, match in candidates.items()
                     if any(effect.target and effect.target in str(t) for t in (match.rule.get("targets") or []))),
                    None,
                )
                hint = f"；该物品对应发现候选 {redirect}，请改用 discovery_refs 落账" if redirect else ""
                raise ValueError(f"物品来源不存在，不能凭空发放{hint}")
        elif effect.kind == "consume_item":
            if effect.target not in world.get("pc", {}).get("inventory", []):
                raise ValueError("只能消耗实际持有的物品")
        elif effect.kind == "flag_set":
            declared = world.get("completion_flags") or {}
            if effect.target not in declared:
                raise ValueError("只能落定模组 completion_flags 声明的完成标记")
            if (world.get("flags") or {}).get(effect.target):
                raise ValueError("完成标记已落定，不要重复提交")
    # An authored route carries arrival beats and previews; preserve them when
    # the semantic adjudicator chose that same destination.
    if proposal.intent == "move":
        resolution = fallback if fallback.destination_scene_id == proposal.destination_scene_id else ActionResolution(
            player_input=content, phase=ActionPhase.ARRIVAL, origin_scene_id=fallback.origin_scene_id,
            destination_scene_id=proposal.destination_scene_id, transition_kind="model_adjudicated",
        )
    else:
        resolution = ActionResolution(
            player_input=content, phase=ActionPhase.CONTACT if matches else ActionPhase.INTERACTION,
            origin_scene_id=fallback.origin_scene_id, discovery_matches=tuple(matches),
            preferred_skill=proposal.check.skill if proposal.check else None,
        )
    return replace(resolution, adjudication_json=proposal.model_dump_json())


_PROMPT = """你是 TRPG 的行动裁决守秘人，在任何掷骰和叙述之前决定本回合实际执行什么。
必须调用 adjudicate_action。玩家输入是意图，不能把回忆、否定、假设、询问当成已发生动作。
pc_incapacitated=true 时玩家已失去行动能力：只能 wait/clarify/decline，不得提出检定、发现、移动或任何效果。
发挥语义理解：发卡拨动锁芯属于开锁，借身份/说辞支走守卫需要结合 NPC 反应裁量，
不要因为动作不在固定句式里就忽略。日常无风险交流不检定；有不确定性和代价才检定。
从角色表选技能，解释难度与奖励/惩罚依据；在骰前确定成功与失败后果。失败应推进情境，
不能永久封死核心调查。允许提出在场 NPC 的社交反应、既有物品转移、资源代价、时间与案件时钟。
completion_flags 是模组声明的场景/任务完成标记（如 cottage_searched）：玩家实际完成对应调查后
用 flag_set 落定，每个标记只能落定一次；其余 flag 一律不可由裁决设置。
所有 effect.evidence_quote 与 input_quote 必须逐字引用 player_input；source_id 只能用 available_items 的键。
时钟每次最多推进一级，并必须符合对应 advance_when 或行动时间/后果，不能仅为制造戏剧性推进。
discovery_refs 只能选候选键，需玩家本轮实际接触对应目标且行为符合 intent；提到某物不等于发现。
玩家的搜查/取得动作若与候选线索的目标语义对应（如"锡盒"对应"锁箱"），必须用 discovery_refs 落账；
候选线索自带 flag 与物品效果，禁止改用 take_item 凭空发放来源不存在的物品。
候选的 grants_item/sets_flags 是取得后果；玩家明确说"只看、不带走、不拿"时不得选择这类候选。
跨场景本轮只抵达，不同时调查/拿线索；deterministic_hint 是可参考的解析，不要求盲从。
同场景内的走位（如地下室到楼上办公室）不是 move，属于 interact，可正常提出 discovery_refs 与效果。
无需检定时 check=null。不改变位置时 destination_scene_id=""。不要添加虚构工具/物品/秘密。
NPC 的动机、策略、对话态度写入 npc_direction；不要替玩家选择下一步或凭空宣布主线秘密。
success_description/failure_description 描述本次行动的结果与代价，供掷骰后叙述，不写已掷出的骰点。
combat 意图只给动作语义/NPC 战术方向；战斗的出手、防御、伤害由后续战斗工具处理。
恐怖事件必须从 sanity_sources 选择稳定 ID，按作者允许的范围选择严重度；回忆、假设或重复曝光不再扣 SAN。
若 discovery_refs 已含同一恐怖发现，由发现流程处理 SAN，无需再提出 sanity_source_id。
孤注一掷仅当玩家明确要求、给出新做法时，引用 _push_contexts 中已有失败记录；不得自行重复检定。
time_minutes 必须与玩家授权的时间跨度一致：玩家没说"几天"就不要结算数日。
"""


def adjudicate_player_action(engine: Any, content: str, world: dict, fallback: ActionResolution) -> ActionResolution:
    if not _enabled_env("TRPG_ACTION_ADJUDICATION", True) or not getattr(engine, "client", None):
        return fallback
    session = getattr(engine, "_resolution_session", None)
    if session is not None and session.plan is not None:
        return validate_proposal(session.plan, content, world, fallback)
    payload = adjudication_context(world, content, fallback)
    messages = [{"role": "system", "content": _PROMPT}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
    model = getattr(engine, "judgement_model", JUDGEMENT_MODEL)
    started = time.monotonic()
    last_error = ""
    for attempt in range(2):
        try:
            with llm_call_slot(model=model, world_id=str(getattr(engine.context, "world_id", ""))):
                response = engine.client.chat.completions.create(
                    model=model, messages=messages, temperature=0, max_tokens=4000,
                    tools=[{"type": "function", "function": {"name": "adjudicate_action", "description": "提交骰前行动裁决", "parameters": ActionProposal.model_json_schema()}}],
                    tool_choice={"type": "function", "function": {"name": "adjudicate_action"}},
                    extra_body={"thinking": {"type": "disabled"}},
                )
            engine.raise_if_turn_cancelled()
            calls = response.choices[0].message.tool_calls or []
            if len(calls) != 1 or calls[0].function.name != "adjudicate_action":
                raise ValueError("需要一个完整的 adjudicate_action 调用")
            if response.choices[0].finish_reason not in {"stop", "tool_calls"}:
                raise ValueError("裁决响应未完整结束")
            proposal = json.loads(calls[0].function.arguments)
            resolution = validate_proposal(proposal, content, world, fallback)
            if session is not None:
                session.freeze_plan(json.loads(resolution.adjudication_json))
            elapsed = time.monotonic() - started
            log_model_call(model, "adjudication", elapsed, None, "tool_calls", 1)
            if hasattr(engine, "_turn_diagnostics"):
                engine._turn_diagnostics.append({"model": model, "role": "adjudication", "status": "completed", "elapsed_ms": round(elapsed * 1000), "attempts": attempt + 1})
            return resolution
        except Exception as exc:
            engine.raise_if_turn_cancelled()
            last_error = f"{type(exc).__name__}: {exc}"
            messages.append({"role": "user", "content": f"裁决未通过验证：{last_error[:600]}。请修正提案，不能绕过条件。"})
    log_game(f"行动裁决回退 | {last_error[:300]}")
    if hasattr(engine, "_turn_diagnostics"):
        engine._turn_diagnostics.append({"model": model, "role": "adjudication", "status": "fallback", "error": last_error[:300]})
    return fallback


def apply_adjudicated_effects(engine: Any, resolution: ActionResolution, check_result: dict | None) -> dict:
    if not resolution.adjudication_json:
        return {}
    plan = ActionProposal.model_validate_json(resolution.adjudication_json)
    if plan.intent == "combat":
        return {"combat_intent": plan.approach, "npc_direction": plan.npc_direction, "events": []}
    succeeded = check_result is None or bool(check_result.get("success"))
    effects = plan.on_success if succeeded else plan.on_failure
    result = {"success": succeeded, "description": plan.success_description if succeeded else plan.failure_description,
              "npc_direction": plan.npc_direction, "events": []}
    if check_result and check_result.get("push_consequence"):
        result["description"] = check_result["push_consequence"]

    def mutate(world: dict):
        result["events"].append(advance_time(world, min(10080, plan.time_minutes + (10 if check_result and check_result.get("push_consequence") else 0))))
        for effect in effects:
            if effect.kind == "clock_advance":
                clocks = world["case_clocks"]
                definition = world["case_clock_definitions"][effect.target]
                before = int(clocks[effect.target])
                clocks[effect.target] = min(before + 1, int(definition.get("max", before + 1)))
                result["events"].append({"type": effect.kind, "target": effect.target, "before": before, "after": clocks[effect.target]})
            elif effect.kind == "npc_reaction":
                npc = next(n for n in world["npcs"] if n.get("id") == effect.target)
                npc["disposition"] = effect.value
                result["events"].append({"type": effect.kind, "target": effect.target, "value": effect.value})
            elif effect.kind == "take_item":
                if available_items(world).get(effect.source_id) != effect.target:
                    raise ValueError("物品来源已变化")
                if effect.source_id.startswith("npc:"):
                    npc_id = effect.source_id.split(":", 2)[1]
                    next(n for n in world["npcs"] if n.get("id") == npc_id)["inventory"].remove(effect.target)
                else:
                    world["current_scene"]["items"].remove(effect.target)
                world["pc"].setdefault("inventory", []).append(effect.target)
                result["events"].append({"type": effect.kind, "item": effect.target})
            elif effect.kind == "consume_item":
                from src.gameplay.inventory import use_item

                result["events"].append(use_item(world, item=effect.target, operation="consume", reason=effect.reason))
            elif effect.kind == "flag_set":
                if effect.target not in (world.get("completion_flags") or {}):
                    raise ValueError("完成标记未经模组声明")
                world.setdefault("flags", {})[effect.target] = True
                result["events"].append({"type": effect.kind, "target": effect.target})
        session = getattr(engine, "_resolution_session", None)
        world["last_action_outcome"] = {"resolution_id": session.id if session else "", **result}

    engine.context.world_store.update(mutate)
    if plan.sanity_source_id:
        from src.gameplay.sanity_sources import validate_sanity_source

        source = validate_sanity_source(engine.context.world_store.load(), plan.sanity_source_id, plan.sanity_severity)
        output = engine._execute_tool("sanity_event", {
            "description": source["match"], "severity": plan.sanity_severity,
            "exposure_id": plan.sanity_source_id,
        })
        engine._emit_sanity_result(output)
        result["events"].append({"type": "sanity_event", "result": json.loads(output)})
    return result
