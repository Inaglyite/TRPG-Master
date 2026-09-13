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
    if speaker.get("kind") not in {"keeper", "npc", "investigator", "system"}:
        raise StructuredError("invalid_action", "发言身份不合法。")
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
    if isinstance(assets, dict) and assets and asset_id not in assets:
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
    if resolution not in {"completed", "declined", "cancelled", "paused"}:
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
    row.status = resolution
    row.outcome = outcome
    row.detail = note
    ctx.session.flush()
    return CommandResult(
        result={"status": "success", "request_id": request_id, "resolution": resolution},
        events=[
            EventSpec(
                "action_status",
                {
                    "request_id": request_id,
                    "status": resolution,
                    **({"outcome": outcome} if outcome else {}),
                    **({"detail": note} if note else {}),
                },
            )
        ],
        bump_revision=False,
    )


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
}
