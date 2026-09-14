"""structured_v1 领域命令实现（协议 §6）。

每个命令是纯领域函数：在状态工作副本上校验前置条件并落账，返回结果与
待发布事件；持久化、去重、权限与提交边界由 service 层负责。
规则计算复用 src/gameplay 的既有实现；本模块不反向依赖任何模型代码。
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from src.gameplay.percentile import choose_percentile
from src.gameplay.world_time import advance_time as _advance_time
from src.storage.database import utcnow

from .errors import StructuredError
from .ids import new_stable_id
from .registries import ensure_clue_registry, ensure_item_registry, find_item

# 事件接收范围的简写
PUBLIC = {"kind": "public"}
KEEPER = {"kind": "keeper"}


@dataclass
class EventSpec:
    type: str
    payload: dict
    audience: dict = field(default_factory=lambda: dict(PUBLIC))


@dataclass
class CommandResult:
    result: dict
    events: list[EventSpec] = field(default_factory=list)
    bump_revision: bool = True


@dataclass
class CommandContext:
    world_id: str
    principal: Any
    cause_id: str = ""
    session: Any = None  # resolve_intent / request_check / resolve_check 需要
    rng: Callable[[int], int] = secrets.randbelow  # 返回 [0, n)
    # 处理器被调用时的世界 revision（写回前）；需要把行与「提交后版本」关联的
    # 处理器（线程/记忆：created_revision 是读档截止依据）按自身 bump 语义推算。
    revision: int = 0

    def roll_d100(self, bonus_penalty: int = 0) -> tuple[int, list[int]]:
        units = self.rng(10)
        count = 1 + abs(int(bonus_penalty))
        tens = [self.rng(10) for _ in range(count)]
        return choose_percentile(tens, units, penalty=bonus_penalty < 0)


def _require_text(payload: dict, key: str, *, limit: int = 500) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise StructuredError("invalid_action", f"缺少字段：{key}")
    return value[:limit]


def _investigator_sheet(state: dict, investigator_id: str) -> dict:
    investigators = state.get("investigators")
    if isinstance(investigators, dict) and investigator_id in investigators:
        sheet = investigators[investigator_id]
        if isinstance(sheet, dict):
            return sheet
    pc = state.get("pc")
    if isinstance(pc, dict) and investigator_id in {
        str(pc.get("id") or ""),
        str(pc.get("stable_id") or ""),
        "pc",
        "",
    }:
        return pc
    raise StructuredError("object_not_found", f"调查员不存在：{investigator_id}")


def _present_npc_ids(state: dict) -> set[str]:
    scene = state.get("current_scene")
    present = scene.get("npcs_present", []) if isinstance(scene, dict) else []
    return {str(value) for value in present} if isinstance(present, list) else set()


def _recompute_presence(state: dict) -> None:
    """按 NPC 的 current_location 重算当前场景在场列表（与旧引擎同一规则）。"""
    scene = state.get("current_scene")
    if not isinstance(scene, dict):
        return
    scene_id = str(scene.get("id") or "")
    present = [
        str(npc.get("id"))
        for npc in state.get("npcs", []) or []
        if isinstance(npc, dict)
        and npc.get("id")
        and str(npc.get("current_location") or "") == scene_id
    ]
    scene["npcs_present"] = present


def _npc_name(state: dict, npc_id: str) -> str:
    for npc in state.get("npcs", []) or []:
        if isinstance(npc, dict) and str(npc.get("id") or "") == npc_id:
            return str(npc.get("name") or npc_id)
    return npc_id


def _public_targets(state: dict) -> list[dict]:
    targets = [
        {"kind": "npc", "id": npc_id, "name": _npc_name(state, npc_id)}
        for npc_id in sorted(_present_npc_ids(state))
    ]
    investigators = state.get("investigators")
    if isinstance(investigators, dict):
        for investigator_id, sheet in sorted(investigators.items()):
            name = sheet.get("name") if isinstance(sheet, dict) else None
            targets.append(
                {
                    "kind": "investigator",
                    "id": str(investigator_id),
                    "name": str(name or investigator_id),
                }
            )
    return targets


def _stat_projection(sheet: dict, investigator_id: str) -> dict:
    return {
        "investigator_id": investigator_id,
        "hp": int(sheet.get("hp", 0) or 0),
        "max_hp": int(sheet.get("max_hp", 0) or 0),
        "san": int(sheet.get("san", 0) or 0),
        "max_san": int(sheet.get("max_san", 0) or 0),
        "conditions": list(sheet.get("conditions") or []),
    }


def _known_destinations(state: dict) -> dict[str, str]:
    """玩家已知可选目的地：当前场景出口 + 到过的场景（遭遇史为凭）。不泄露完整地图。"""
    scenes = state.get("scene_catalog", {})
    if not isinstance(scenes, dict):
        return {}
    known: dict[str, str] = {}
    current = state.get("current_scene") or {}
    for scene_id in current.get("exits", []) or []:
        scene = scenes.get(str(scene_id))
        if isinstance(scene, dict):
            known[str(scene_id)] = str(scene.get("name") or scene_id)
    history = state.get("encounter_history", {})
    if isinstance(history, dict):
        for scene_id in history:
            scene = scenes.get(str(scene_id))
            if isinstance(scene, dict):
                known[str(scene_id)] = str(scene.get("name") or scene_id)
    return known


def _scene_public(scene_id: str, scene: dict) -> dict:
    return {"id": scene_id, "name": str(scene.get("name") or scene_id)}


# ---------------------------------------------------------------------------
# 发言 / 时间 / 状态
# ---------------------------------------------------------------------------


def cmd_publish_message(state: dict, payload: dict, ctx: CommandContext) -> CommandResult:
    speaker = payload.get("speaker") or {}
    audience = payload.get("audience") or dict(PUBLIC)
    text = _require_text(payload, "text", limit=4000)
    if not isinstance(speaker, dict) or speaker.get("kind") not in {
        "keeper",
        "npc",
        "investigator",
        "system",
    }:
        raise StructuredError("invalid_action", "发言身份不合法。")
    if not isinstance(audience, dict):
        # 模型可能把 audience 写成字符串；投递过滤按对象解析，写进去会让
        # 快照/补发/投递在读取时崩溃（真实模型验收实测），必须在写入前拒绝。
        raise StructuredError(
            "invalid_action", "audience 必须是对象（public/keeper/investigators）。"
        )
    message_id = new_stable_id("msg")
    envelope = {"message_id": message_id, "speaker": speaker, "audience": audience}
    return CommandResult(
        result={"message_id": message_id},
        events=[
            EventSpec("message_started", dict(envelope), audience),
            EventSpec("message_completed", {**envelope, "text": text}, audience),
        ],
        bump_revision=False,  # 纯消息不推进世界 revision，但有独立事件顺序
    )


def cmd_advance_time(state: dict, payload: dict, ctx: CommandContext) -> CommandResult:
    minutes = payload.get("minutes")
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not 0 <= minutes <= 10080:
        raise StructuredError("invalid_action", "minutes 必须是 0–10080 的整数。")
    reason = str(payload.get("reason") or "")[:200]
    event = _advance_time(state, minutes, activity=reason)
    return CommandResult(
        result={**event, "reason": reason},
        events=[
            EventSpec(
                "state_changed",
                {"clock": {"elapsed_minutes": event["after"]}},
            )
        ],
    )


def cmd_adjust_stat(state: dict, payload: dict, ctx: CommandContext) -> CommandResult:
    investigator_id = _require_text(payload, "investigator_id", limit=160)
    field_name = str(payload.get("field") or "")
    if field_name not in {"hp", "san", "max_hp", "max_san"}:
        raise StructuredError("invalid_action", "field 只支持 hp/san/max_hp/max_san。")
    delta = payload.get("delta")
    if isinstance(delta, bool) or not isinstance(delta, int) or not -99 <= delta <= 99:
        raise StructuredError("invalid_action", "delta 必须是 -99–99 的整数。")
    reason = _require_text(payload, "reason", limit=200)
    sheet = _investigator_sheet(state, investigator_id)
    before = int(sheet.get(field_name, 0) or 0)
    after = before + delta
    if field_name in {"max_hp", "max_san"}:
        after = max(1, after)
    else:
        upper = int(sheet.get(f"max_{field_name}", 0) or 0)
        after = max(0, min(after, upper if upper > 0 else after))
    sheet[field_name] = after
    return CommandResult(
        result={
            "status": "success",
            "field": field_name,
            "before": before,
            "after": after,
            "reason": reason,
        },
        events=[EventSpec("state_changed", _stat_projection(sheet, investigator_id))],
    )


# ---------------------------------------------------------------------------
# 移动 / NPC 在场
# ---------------------------------------------------------------------------


def cmd_move_party(state: dict, payload: dict, ctx: CommandContext) -> CommandResult:
    destination = _require_text(payload, "destination_scene_id", limit=160)
    scenes = state.get("scene_catalog", {})
    scene = scenes.get(destination) if isinstance(scenes, dict) else None
    if not isinstance(scene, dict):
        raise StructuredError("unknown_target", f"目的地不存在：{destination}")
    current_id = str((state.get("current_scene") or {}).get("id") or "")
    if destination == current_id:
        raise StructuredError("stale_target", "队伍已经在该场景。")
    known = _known_destinations(state)
    if known and destination not in known:
        raise StructuredError("unknown_target", "该目的地对队伍未知；只能用已知出口或到过的场景。")
    catalog_entry = {
        key: value for key, value in scene.items() if key not in {"document", "npcs_present"}
    }
    catalog_entry["id"] = destination
    state["current_scene"] = catalog_entry
    _recompute_presence(state)
    travel = payload.get("travel_minutes", 0)
    travel = travel if isinstance(travel, int) and not isinstance(travel, bool) else 0
    travel = max(0, min(travel, 1440))
    events = [
        EventSpec(
            "scene_changed",
            {
                "scene": _scene_public(destination, scene),
                "destinations": [
                    {"id": scene_id, "name": name}
                    for scene_id, name in sorted(_known_destinations(state).items())
                ],
                "travel_minutes": travel,
            },
        )
    ]
    result: dict[str, Any] = {
        "status": "success",
        "scene_id": destination,
        "arrival_only": True,  # 抵达不等于调查：不检查、不取物、不结算 SAN
    }
    if travel:
        time_event = _advance_time(state, travel, activity="travel")
        events.append(
            EventSpec("state_changed", {"clock": {"elapsed_minutes": time_event["after"]}})
        )
        result["time"] = time_event
    # 已抵达目的地：把目标一致的开放移动线程收尾为 completed（记录收尾，
    # 方向永远是「命令改变世界，线程只记录」，不是线程触发移动）。
    if ctx.session is not None:
        from . import interactions

        for event_type, event_payload, audience in interactions.auto_complete_move_threads(
            ctx.session,
            ctx.world_id,
            destination_scene_id=destination,
            revision=int(ctx.revision) + 1,  # move_party 推进 revision
        ):
            events.append(EventSpec(event_type, event_payload, audience))
    return CommandResult(result=result, events=events)


def cmd_set_npc_presence(state: dict, payload: dict, ctx: CommandContext) -> CommandResult:
    npc_id = _require_text(payload, "npc_id", limit=160)
    scene_id = _require_text(payload, "scene_id", limit=160)
    presence = str(payload.get("presence") or "")
    if presence not in {"enter", "leave"}:
        raise StructuredError("invalid_action", "presence 只支持 enter/leave。")
    npc = next(
        (
            npc
            for npc in state.get("npcs", []) or []
            if isinstance(npc, dict) and str(npc.get("id") or "") == npc_id
        ),
        None,
    )
    if npc is None:
        raise StructuredError("object_not_found", f"NPC 不存在：{npc_id}")
    npc["current_location"] = scene_id if presence == "enter" else "unknown"
    _recompute_presence(state)
    return CommandResult(
        result={"status": "success", "npc_id": npc_id, "presence": presence},
        events=[EventSpec("state_changed", {"targets": _public_targets(state)})],
    )


def cmd_record_fact(state: dict, payload: dict, ctx: CommandContext) -> CommandResult:
    text = _require_text(payload, "text", limit=1000)
    audience = payload.get("audience") or dict(KEEPER)
    source = str(payload.get("source") or "keeper")
    if source not in {"keeper", "module", "ruling"}:
        raise StructuredError("invalid_action", "source 只支持 keeper/module/ruling。")
    facts = state.setdefault("keeper_facts", [])
    facts.append({"text": text, "audience": audience, "source": source})
    message_id = new_stable_id("msg")
    envelope = {
        "message_id": message_id,
        "speaker": {"kind": "system"},
        "audience": audience,
    }
    return CommandResult(
        result={"fact_index": len(facts) - 1},
        events=[
            EventSpec("message_started", dict(envelope), audience),
            EventSpec("message_completed", {**envelope, "text": text}, audience),
        ],
    )


# ---------------------------------------------------------------------------
# 线索 / 素材 / 物品
# ---------------------------------------------------------------------------


def cmd_grant_clue(state: dict, payload: dict, ctx: CommandContext) -> CommandResult:
    clue_id = _require_text(payload, "clue_id", limit=160)
    recipients = payload.get("recipient_investigator_ids")
    if not isinstance(recipients, list) or not recipients:
        raise StructuredError("invalid_action", "recipient_investigator_ids 不能为空。")
    basis = _require_text(payload, "basis", limit=500)
    registry = ensure_clue_registry(state)
    catalog = state.get("clue_catalog", {})
    entry = registry["clues"].get(clue_id)
    if entry is None and isinstance(catalog, dict) and clue_id in catalog:
        catalog_entry = catalog[clue_id]
        entry = {
            "clue_id": clue_id,
            "legacy_key": f"{catalog_entry.get('category', 'investigation')}:{catalog_entry.get('text', '')}",
            "category": str(catalog_entry.get("category") or "investigation"),
            "text": str(catalog_entry.get("text") or ""),
            "granted_to": [],
        }
        registry["clues"][clue_id] = entry
        registry.setdefault("by_legacy_key", {}).setdefault(entry["legacy_key"], clue_id)
    if entry is None:
        raise StructuredError("object_not_found", f"线索不存在：{clue_id}")

    events: list[EventSpec] = []
    granted: list[str] = []
    for recipient in {str(value) for value in recipients}:
        sheet = _investigator_sheet(state, recipient)  # 不存在直接拒绝
        del sheet
        if recipient not in entry["granted_to"]:
            entry["granted_to"].append(recipient)
        granted.append(recipient)
        audience = {"kind": "investigators", "investigator_ids": [recipient]}
        events.append(
            EventSpec(
                "clue_granted",
                {
                    "clue_id": clue_id,
                    "investigator_id": recipient,
                    "category": entry["category"],
                    "text": entry["text"],
                },
                audience,
            )
        )
    # 旧投影兼容：clues_found 追加带授权记录的条目（visibility 不全局公开）。
    groups = state.setdefault("clues_found", {})
    group = groups.setdefault(entry["category"], [])
    existing = next(
        (
            clue
            for clue in group
            if isinstance(clue, dict)
            and str(clue.get("catalog_id") or clue.get("id") or "") == clue_id
        ),
        None,
    )
    if existing is None:
        group.append(
            {
                "id": clue_id,
                "catalog_id": clue_id,
                "text": entry["text"],
                "category": entry["category"],
                "visibility": "private",
                "granted_to": sorted(granted),
                "source": "keeper_command",
            }
        )
    else:
        merged = set(existing.get("granted_to") or []) | set(granted)
        existing["granted_to"] = sorted(merged)
    asset_id = str(payload.get("present_asset_id") or "")
    if asset_id:
        for recipient in granted:
            audience = {"kind": "investigators", "investigator_ids": [recipient]}
            events.append(
                EventSpec(
                    "handout_presented",
                    {
                        "asset_id": asset_id,
                        "investigator_id": recipient,
                        "caption": str(payload.get("note") or "")[:200],
                    },
                    audience,
                )
            )
    return CommandResult(
        result={
            "status": "success",
            "clue_id": clue_id,
            "recipients": sorted(granted),
            "basis": basis,
        },
        events=events,
    )


def cmd_present_handout(state: dict, payload: dict, ctx: CommandContext) -> CommandResult:
    asset_id = _require_text(payload, "asset_id", limit=160)
    recipients = payload.get("recipient_investigator_ids")
    if not isinstance(recipients, list) or not recipients:
        raise StructuredError("invalid_action", "recipient_investigator_ids 不能为空。")
    assets = state.get("assets") or state.get("handout_assets") or {}
    asset_map = state.get("asset_map") or {}
    known_asset_ids = set(assets) if isinstance(assets, dict) else set()
    if isinstance(asset_map, dict):
        for group_entries in asset_map.values():
            if isinstance(group_entries, dict):
                known_asset_ids.update(group_entries)
    if known_asset_ids and asset_id not in known_asset_ids:
        raise StructuredError("object_not_found", f"素材不存在：{asset_id}")
    grants = state.setdefault("asset_grants", [])
    events: list[EventSpec] = []
    granted: list[str] = []
    for recipient in {str(value) for value in recipients}:
        _investigator_sheet(state, recipient)
        grants.append({"asset_id": asset_id, "investigator_id": recipient, "by": ctx.cause_id})
        granted.append(recipient)
        events.append(
            EventSpec(
                "handout_presented",
                {
                    "asset_id": asset_id,
                    "investigator_id": recipient,
                    "caption": str(payload.get("caption") or "")[:200],
                },
                {"kind": "investigators", "investigator_ids": [recipient]},
            )
        )
    return CommandResult(
        result={"status": "success", "asset_id": asset_id, "recipients": sorted(granted)},
        events=events,
    )


def _inventory_projection(state: dict, investigator_id: str) -> list[dict]:
    registry = ensure_item_registry(state)
    items = [
        {
            "id": entry["item_id"],
            "label": entry["label"],
            "quantity": int(entry["quantity"]),
        }
        for entry in registry["items"].values()
        if (entry.get("holder") or {}).get("kind") == "investigator"
        and str((entry.get("holder") or {}).get("id")) == investigator_id
    ]
    return sorted(items, key=lambda item: item["id"])


def cmd_use_item(state: dict, payload: dict, ctx: CommandContext) -> CommandResult:
    investigator_id = _require_text(payload, "investigator_id", limit=160)
    item_id = _require_text(payload, "item_id", limit=160)
    quantity = payload.get("quantity")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= 999:
        raise StructuredError("invalid_action", "quantity 必须是 1–999 的整数。")
    operation = _require_text(payload, "operation", limit=60)
    entry = find_item(state, item_id)
    if entry is None:
        raise StructuredError("object_not_found", f"物品不存在：{item_id}")
    holder = entry.get("holder") or {}
    if holder.get("kind") != "investigator" or str(holder.get("id")) != investigator_id:
        raise StructuredError("object_not_held", "该物品不在此调查员身上。")
    held = int(entry.get("quantity") or 0)
    if held < quantity:
        raise StructuredError("object_not_held", f"数量不足：持有 {held}，需要 {quantity}。")
    consume = payload.get("consume") is True
    consumed = 0
    if consume:
        entry["quantity"] = held - quantity
        consumed = quantity
    return CommandResult(
        result={
            "status": "success",
            "item_id": entry["item_id"],
            "operation": operation,
            "consumed": consumed,
            "remaining": int(entry["quantity"]),
            "note": str(payload.get("result_note") or "")[:500],
        },
        events=[
            EventSpec(
                "inventory_changed",
                {
                    "investigator_id": investigator_id,
                    "items": _inventory_projection(state, investigator_id),
                },
            )
        ],
    )


def cmd_transfer_item(state: dict, payload: dict, ctx: CommandContext) -> CommandResult:
    item_id = _require_text(payload, "item_id", limit=160)
    quantity = payload.get("quantity")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= 999:
        raise StructuredError("invalid_action", "quantity 必须是 1–999 的整数。")
    source = payload.get("from") or {}
    target = payload.get("to") or {}
    entry = find_item(state, item_id)
    if entry is None:
        raise StructuredError("object_not_found", f"物品不存在：{item_id}")
    holder = entry.get("holder") or {}
    if holder.get("kind") != source.get("kind") or str(holder.get("id")) != str(source.get("id")):
        raise StructuredError("object_not_held", "物品不在指定来源处。")
    if target.get("kind") not in {"investigator", "npc", "scene"} or not target.get("id"):
        raise StructuredError("unknown_target", "转移去向不合法。")
    held = int(entry.get("quantity") or 0)
    if held < quantity:
        raise StructuredError("object_not_held", f"数量不足：持有 {held}，需要 {quantity}。")
    if quantity == held:
        entry["holder"] = {"kind": target["kind"], "id": str(target["id"])}
        moved_id = entry["item_id"]
    else:
        # 部分转移：拆分为新堆叠，原堆叠保留剩余数量。
        entry["quantity"] = held - quantity
        registry = ensure_item_registry(state)
        moved_id = new_stable_id("item")
        registry["items"][moved_id] = {
            "item_id": moved_id,
            "label": entry["label"],
            "quantity": quantity,
            "holder": {"kind": target["kind"], "id": str(target["id"])},
            "stack_key": f"{target['kind']}:{target['id']}/{entry['label']}#split-{moved_id}",
            "legacy_label": entry["legacy_label"],
            "operations": list(entry.get("operations") or []),
        }
    events: list[EventSpec] = []
    for side in (source, target):
        if side.get("kind") == "investigator":
            events.append(
                EventSpec(
                    "inventory_changed",
                    {
                        "investigator_id": str(side["id"]),
                        "items": _inventory_projection(state, str(side["id"])),
                    },
                )
            )
    return CommandResult(
        result={
            "status": "success",
            "item_id": moved_id,
            "quantity": quantity,
            "from": source,
            "to": target,
            "note": str(payload.get("note") or "")[:500],
        },
        events=events,
    )


def cmd_present_information(state: dict, payload: dict, ctx: CommandContext) -> CommandResult:
    clue_id = _require_text(payload, "clue_id", limit=160)
    presentation = str(payload.get("presentation") or "")
    if presentation not in {"describe", "image", "original"}:
        raise StructuredError("invalid_action", "presentation 只支持 describe/image/original。")
    registry = ensure_clue_registry(state)
    if clue_id not in registry["clues"] and clue_id not in (state.get("clue_catalog") or {}):
        raise StructuredError("object_not_found", f"线索不存在：{clue_id}")
    log = state.setdefault("presentation_log", [])
    log.append(
        {
            "clue_id": clue_id,
            "presentation": presentation,
            "target": payload.get("target"),
            "note": str(payload.get("note") or "")[:500],
            "cause": ctx.cause_id,
        }
    )
    return CommandResult(
        result={"status": "success", "clue_id": clue_id, "presentation": presentation},
        events=[],
    )


# ---------------------------------------------------------------------------
# 主持意图收尾（需要 session 的命令在 service 层组装 ctx 后调用）
# ---------------------------------------------------------------------------


def cmd_resolve_intent(state: dict, payload: dict, ctx: CommandContext) -> CommandResult:
    from src.storage.database import PlayerRequest

    if ctx.session is None:
        raise StructuredError("internal_error", "resolve_intent 需要数据库会话。")
    request_id = _require_text(payload, "request_id", limit=160)
    resolution = str(payload.get("resolution") or "")
    if resolution not in {"completed", "declined", "cancelled", "paused", "awaiting_player"}:
        raise StructuredError("invalid_action", "resolution 不合法。")
    from sqlalchemy import select

    row = ctx.session.execute(
        select(PlayerRequest).where(
            PlayerRequest.world_id == ctx.world_id,
            PlayerRequest.request_id == request_id,
        )
    ).scalar_one_or_none()
    if row is None:
        raise StructuredError("request_not_found", f"没有找到原请求：{request_id}")
    if row.status in {"completed", "declined", "cancelled"}:
        raise StructuredError("invalid_action", f"请求已是终态：{row.status}")
    outcome = str(payload.get("outcome") or "")
    if outcome and outcome not in {"success", "failure", "not_executed"}:
        raise StructuredError("invalid_action", "outcome 不合法。")
    note = str(payload.get("note") or "")[:500]
    # awaiting_player：守秘人用正常叙事过渡后把后续行动挂起等玩家自由回应。
    # 这里只记录「哪项尚未执行 / 已告知什么」，绝不执行任何命令——待办只能
    # 由主持在下一轮按当时情境重新判断后执行（见协议 §3.1）。
    awaiting = None
    if resolution == "awaiting_player":
        # 命令层与 Agent 运行器口径一致：没写 pending_action 时**从请求本身派生**
        # 尚未执行的意图（玩家原请求就是待办），而不是拒一条没有待办内容的等待。
        record = dict(payload)
        if not isinstance(record.get("pending_action"), dict):
            derived = _derive_pending_from_request(row)
            if derived is None:
                raise StructuredError(
                    "invalid_action",
                    "等待玩家时需要说明尚未执行什么（pending_action），"
                    "或让原请求本身带有可识别的行动。",
                )
            record["pending_action"] = derived
        awaiting = _awaiting_record(record)
    if resolution == "awaiting_player":
        # 等待中的请求没有领域结果：忽略客户端可能带上来的 outcome，
        # 避免把「尚未执行」记成成功/失败。
        outcome = ""
    # 交互线程（第 2 层上下文）：跨越请求终态存活的「已讨论目标」。
    # awaiting_player 自动开/续线程；其余终态由主持用 thread 参数显式
    # open/continue/close/replace。线程只是记录，不构成执行授权。
    # 写入线程的 resolve_intent 推进 revision：线程落在独立表里，revision 是
    # 读档截止的唯一依据——不推进就无法区分「存档点之前/之后创建的线程」。
    from . import interactions

    revision = int(ctx.revision)
    thread_events: list[EventSpec] = []
    thread_row = None
    thread_spec = payload.get("thread")
    # 意图的落实/明确取消自动收尾**由该请求发起**的开放线程（正确关联）：
    # - completed + outcome=success：意图已落实 → 线程 completed；
    # - cancelled：明确取消 → 线程 cancelled；
    # declined、completed+(failure|not_executed)、paused 不动线程——
    # 讨论过的目标不丢、未落实的意图仍悬着（回答一次追问 ≠ 结束原交互，
    # 追问收尾时 origin_request_id 不匹配本请求，天然不会误关）。
    auto_close_status = ""
    if resolution == "completed" and outcome == "success":
        auto_close_status = "completed"
    elif resolution == "cancelled":
        auto_close_status = "cancelled"
    if auto_close_status and thread_spec is None:
        linked_id = str((row.payload or {}).get("thread_id") or "")
        if linked_id:
            linked = interactions.get_thread(ctx.session, ctx.world_id, linked_id)
            if (
                linked is not None
                and linked.status == "open"
                and linked.origin_request_id == request_id
                and linked.investigator_id == row.investigator_id
            ):
                interactions.close_thread(linked, status=auto_close_status, note=None, revision=revision + 1)
                thread_row = linked
                thread_events.append(
                    EventSpec(*interactions.thread_event_with_audience(linked))
                )
    writes_thread = (
        resolution == "awaiting_player" or isinstance(thread_spec, dict) or thread_row is not None
    )
    if writes_thread:
        revision += 1
    if resolution == "awaiting_player":
        thread_row = interactions.auto_thread_for_awaiting(
            ctx.session,
            ctx.world_id,
            request_row=row,
            awaiting=awaiting,
            revision=revision,
        )
        awaiting["thread_id"] = thread_row.thread_id
        thread_events.append(EventSpec(*interactions.thread_event_with_audience(thread_row)))
    elif isinstance(thread_spec, dict):
        thread_events_list, thread_row = interactions.apply_thread_action(
            ctx.session,
            ctx.world_id,
            request_row=row,
            thread_spec=thread_spec,
            resolution=resolution,
            revision=revision,
        )
        for event_type, event_payload, audience in thread_events_list:
            thread_events.append(EventSpec(event_type, event_payload, audience))
    row.status = resolution
    row.outcome = outcome
    row.detail = note
    stored = dict(row.payload or {})
    if awaiting is not None:
        stored["awaiting"] = awaiting
    else:
        stored.pop("awaiting", None)  # 终态/暂停时不再保留待办指纹
    if thread_row is not None:
        stored["thread_id"] = thread_row.thread_id
    row.payload = stored
    row.updated_at = utcnow()
    ctx.session.flush()
    return CommandResult(
        result={
            "status": "success",
            "request_id": request_id,
            "resolution": resolution,
            **({"awaiting": awaiting} if awaiting else {}),
            **(
                {"thread": interactions.public_projection(thread_row)}
                if thread_row is not None
                else {}
            ),
        },
        events=[
            EventSpec(
                "action_status",
                {
                    "request_id": request_id,
                    "status": resolution,
                    **({"outcome": outcome} if outcome else {}),
                    **({"detail": note} if note else {}),
                    **({"awaiting": awaiting} if awaiting else {}),
                },
                # awaiting 明细含「尚未执行/已告知」：只对本人与主持可见，
                # 不能在 WS 上广播给其他玩家（快照投影本来就按人过滤）。
                (
                    {"kind": "investigators", "investigator_ids": [row.investigator_id]}
                    if awaiting
                    else dict(PUBLIC)
                ),
            ),
            *thread_events,
        ],
        bump_revision=writes_thread,
    )


def _derive_pending_from_request(row: Any) -> dict | None:
    """从玩家原请求派生「尚未执行」的待办记录（命令层与运行器共用同一口径）。"""
    action = (row.payload or {}).get("action") or {}
    kind = str(action.get("kind") or "")
    if kind == "move":
        return {
            "kind": "move",
            "destination_scene_id": str(action.get("destination_scene_id") or "")[:160],
            "note": "尚未出发",
        }
    if kind in {"present_clue", "use_item"}:
        target = action.get("target") or {}
        return {
            "kind": kind,
            "target": str(target.get("id") or target.get("text") or "")[:160],
            "note": "",
        }
    if kind == "freeform":
        return {"kind": "freeform", "note": str(action.get("text") or "")[:300]}
    return None


# 待办里「尚未执行」的行动类型：只用于让下一轮知道自己停在什么上，不是执行授权。
_AWAITING_ACTION_KINDS = {"freeform", "move", "present_clue", "use_item", "other"}


def _awaiting_record(payload: dict) -> dict:
    """校验并归一化「等待玩家回应」的待办记录（公开安全，不含主持判断）。

    返回 ``{"pending_action": {...}, "disclosed": [...], "note": str}``——与命令
    schema 同形，前端与下一轮上下文直接读同一份结构。
    """
    raw_action = payload.get("pending_action") or {}
    if not isinstance(raw_action, dict):
        raise StructuredError("invalid_action", "pending_action 必须是对象。")
    kind = str(raw_action.get("kind") or "other")
    if kind not in _AWAITING_ACTION_KINDS:
        raise StructuredError("invalid_action", "pending_action.kind 不合法。")
    pending: dict = {"kind": kind}
    for key, limit in (("note", 300), ("target", 160), ("destination_scene_id", 160)):
        value = str(raw_action.get(key) or "")[:limit]
        if value:
            pending[key] = value
    if not pending.get("note") and not pending.get("destination_scene_id") and not pending.get("target"):
        raise StructuredError("invalid_action", "等待玩家时必须说明尚未执行什么（pending_action）。")
    record: dict = {"pending_action": pending}
    raw_disclosed = payload.get("disclosed") or []
    if not isinstance(raw_disclosed, list):
        raise StructuredError("invalid_action", "disclosed 必须是字符串数组。")
    disclosed = [str(item)[:200] for item in raw_disclosed if str(item).strip()][:8]
    if disclosed:
        record["disclosed"] = disclosed
    note = str(payload.get("note") or "")[:500]
    if note:
        record["note"] = note
    return record


def cmd_resolve_draft(state: dict, payload: dict, ctx: CommandContext) -> CommandResult:
    """assisted 草稿收尾：approved/edited → completed，rejected → declined。

    批准本身不执行命令——主持以各自的 command_request 单独提交（幂等）；
    edited 表示主持改过内容再发，同样只收尾草稿。
    """
    from sqlalchemy import select

    from src.storage.database import PlayerRequest

    if ctx.session is None:
        raise StructuredError("internal_error", "resolve_draft 需要数据库会话。")
    draft_id = _require_text(payload, "draft_id", limit=160)
    decision = str(payload.get("decision") or "")
    if decision not in {"approved", "rejected", "edited"}:
        raise StructuredError("invalid_action", "decision 只支持 approved/rejected/edited。")
    row = ctx.session.execute(
        select(PlayerRequest).where(
            PlayerRequest.world_id == ctx.world_id,
            PlayerRequest.request_id == draft_id,
            PlayerRequest.request_type == "keeper_draft",
        )
    ).scalar_one_or_none()
    if row is None:
        raise StructuredError("request_not_found", f"没有找到草稿：{draft_id}")
    if row.status != "queued":
        raise StructuredError("invalid_action", f"草稿已处理：{row.status}")
    note = str(payload.get("note") or "")[:500]
    row.status = "declined" if decision == "rejected" else "completed"
    row.detail = note or f"草稿{decision}"
    row.updated_at = utcnow()
    ctx.session.flush()
    return CommandResult(
        result={"status": "success", "draft_id": draft_id, "decision": decision},
        events=[
            EventSpec(
                "keeper_draft_resolved",
                {
                    "draft_id": draft_id,
                    "decision": decision,
                    **({"note": note} if note else {}),
                },
                dict(KEEPER),
            )
        ],
        bump_revision=False,
    )


def cmd_record_memory(state: dict, payload: dict, ctx: CommandContext) -> CommandResult:
    """主持显式记录一条角色记忆（传闻/被告知/更正等）。

    与确定性派生的分工：派生只覆盖已提交事件里「明确可知」的部分；主持
    用本命令记录叙事中产生的角色知识（尤其传闻与推测），并可用 supersedes
    更正旧记忆。记忆不是权威世界状态，不产生玩家可见事件（keeper 定向）。
    """
    if ctx.session is None:
        raise StructuredError("internal_error", "record_memory 需要数据库会话。")
    from . import interactions, memories

    character_id = _require_text(payload, "character_id", limit=160)
    content = _require_text(payload, "content", limit=500)
    knowledge_type = str(payload.get("knowledge_type") or "")
    character_kind = str(payload.get("character_kind") or "")
    if not character_kind:
        character_kind = _infer_character_kind(state, character_id)
    scene_id = str(payload.get("scene_id") or "")[:160]
    if scene_id:
        scenes = state.get("scene_catalog") or {}
        if isinstance(scenes, dict) and scenes and scene_id not in scenes:
            raise StructuredError("unknown_target", f"场景不存在：{scene_id}")
    revision = int(ctx.revision) + 1  # record_memory 推进 revision（读档截止依据）
    row = memories.insert_memory(
        ctx.session,
        ctx.world_id,
        character_id=character_id,
        character_kind=character_kind,
        knowledge_type=knowledge_type,
        content=content,
        scene_id=scene_id,
        subjects=payload.get("subjects") if isinstance(payload.get("subjects"), list) else [],
        topics=payload.get("topics") if isinstance(payload.get("topics"), list) else [],
        source={"kind": "keeper", "command_id": ctx.cause_id},
        revision=revision,
        sequence=interactions._next_sequence(ctx.session, ctx.world_id),
    )
    superseded_id = str(payload.get("supersedes") or "")[:160]
    if superseded_id:
        memories.supersede_memory(
            ctx.session, ctx.world_id, superseded_id, superseded_by=row.memory_id, revision=revision
        )
    return CommandResult(
        result={
            "status": "success",
            "memory_id": row.memory_id,
            **({"superseded": superseded_id} if superseded_id else {}),
        },
        events=[
            EventSpec(
                "memory_recorded",
                {
                    "memory_id": row.memory_id,
                    "character_id": row.character_id,
                    "character_kind": row.character_kind,
                    "knowledge_type": row.knowledge_type,
                    "content": row.content,
                    **({"scene_id": row.scene_id} if row.scene_id else {}),
                    "topics": list(row.topics or []),
                    "subjects": list(row.subjects or []),
                    **({"supersedes": superseded_id} if superseded_id else {}),
                },
                dict(KEEPER),
            )
        ],
        bump_revision=True,
    )


def _infer_character_kind(state: dict, character_id: str) -> str:
    investigators = state.get("investigators")
    if isinstance(investigators, dict) and character_id in investigators:
        return "investigator"
    pc = state.get("pc")
    if isinstance(pc, dict) and character_id in {
        str(pc.get("id") or ""),
        str(pc.get("stable_id") or ""),
        "pc",
    }:
        return "investigator"
    for npc in state.get("npcs", []) or []:
        if isinstance(npc, dict) and str(npc.get("id") or "") == character_id:
            return "npc"
    raise StructuredError("object_not_found", f"角色不存在：{character_id}")


COMMAND_HANDLERS = {
    "publish_message": cmd_publish_message,
    "advance_time": cmd_advance_time,
    "adjust_stat": cmd_adjust_stat,
    "move_party": cmd_move_party,
    "set_npc_presence": cmd_set_npc_presence,
    "record_fact": cmd_record_fact,
    "grant_clue": cmd_grant_clue,
    "present_handout": cmd_present_handout,
    "use_item": cmd_use_item,
    "transfer_item": cmd_transfer_item,
    "present_information": cmd_present_information,
    "resolve_intent": cmd_resolve_intent,
    "resolve_draft": cmd_resolve_draft,
    "record_memory": cmd_record_memory,
}
