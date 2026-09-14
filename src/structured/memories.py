"""角色长期记忆（第 4 层）：按角色归属的追加式记忆与按需检索。

边界与不变量：

- 记忆不是权威世界状态。权威状态只看 WorldState/事件；记忆是「某角色知道/
  相信什么」的附属记录，供上下文组装与主持查询，绝不反向改写世界。
- 知识类型四分：experienced（亲历）/told（被告知）/rumor（传闻）/belief
  （推测）。传闻与推测必须带着类型出场，检索与提示词都不得把它们当事实。
- 派生只来自**已提交**的命令与事件（事务提交后才运行，见
  service.execute_command 的钩子）；未执行的计划、被拒绝的命令、模型叙述
  里未落账的内容都不进入记忆。派生失败不得拖垮已提交事件——失败只记日志，
  可用 repair_derivation 幂等补建（derivation_key 去重）。
- 知情依据显式：派生只给事件明确指向的角色（被授予者/持有者/队伍成员）
  增加记忆，不因「与事件相关」默认知情；更正用 supersedes 保留来源链。
- 读档/分支隔离：created_revision > 存档点的记忆在恢复时删除（不泄漏未来
  知识）；分支复制分叉点已有的记忆行，之后按 world_id 各自分叉。
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.storage.database import (
    CharacterMemory,
    EventOutbox,
    GameCommand,
    WorldState,
    session_scope,
    utcnow,
)
from src.storage.database_store import migrate_world_state

from .errors import StructuredError
from .ids import new_row_id, new_stable_id

logger = logging.getLogger("trpg.structured_memory")

KNOWLEDGE_TYPES = {"experienced", "told", "rumor", "belief"}
CHARACTER_KINDS = {"investigator", "npc"}

_CONTENT_LIMIT = 500
_MAX_TOPICS = 6
_MAX_SUBJECTS = 8
# 检索默认预算：条数与总字符双上限；必需区（权威状态/当前交互）永不被它挤占。
DEFAULT_LIMIT = 8
DEFAULT_CHAR_BUDGET = 1200
MAX_LIMIT = 20
MAX_CHAR_BUDGET = 4000


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------


def insert_memory(
    session,
    world_id: str,
    *,
    character_id: str,
    character_kind: str,
    knowledge_type: str,
    content: str,
    scene_id: str = "",
    subjects: list | None = None,
    topics: list | None = None,
    derivation_key: str | None = None,
    source: dict | None = None,
    revision: int,
    sequence: int,
) -> CharacterMemory:
    if knowledge_type not in KNOWLEDGE_TYPES:
        raise StructuredError("invalid_action", f"knowledge_type 不合法：{knowledge_type}")
    if character_kind not in CHARACTER_KINDS:
        raise StructuredError("invalid_action", f"character_kind 不合法：{character_kind}")
    content = str(content or "").strip()[:_CONTENT_LIMIT]
    if not content:
        raise StructuredError("invalid_action", "记忆内容不能为空。")
    row = CharacterMemory(
        id=new_row_id("mem"),
        world_id=world_id,
        memory_id=new_stable_id("mem"),
        character_id=str(character_id)[:160],
        character_kind=character_kind,
        knowledge_type=knowledge_type,
        content=content,
        scene_id=str(scene_id or "")[:160],
        subjects=[str(s)[:160] for s in (subjects or [])][:_MAX_SUBJECTS],
        topics=[str(t)[:40] for t in (topics or [])][:_MAX_TOPICS],
        derivation_key=derivation_key,
        source=dict(source or {}),
        status="active",
        superseded_by="",
        created_revision=int(revision),
        updated_revision=int(revision),
        created_sequence=int(sequence),
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    session.add(row)
    session.flush()
    return row


def supersede_memory(
    session, world_id: str, memory_id: str, *, superseded_by: str, revision: int
) -> CharacterMemory:
    row = session.execute(
        select(CharacterMemory).where(
            CharacterMemory.world_id == world_id,
            CharacterMemory.memory_id == memory_id,
        )
    ).scalar_one_or_none()
    if row is None:
        raise StructuredError("object_not_found", f"记忆不存在：{memory_id}")
    if row.status != "active":
        raise StructuredError("invalid_action", f"记忆已失效：{row.status}")
    row.status = "superseded"
    row.superseded_by = superseded_by
    row.updated_revision = int(revision)
    row.updated_at = utcnow()
    session.flush()
    return row


# ---------------------------------------------------------------------------
# 已提交事件/命令的确定性派生
# ---------------------------------------------------------------------------


def _roster_ids(state: dict) -> list[str]:
    investigators = state.get("investigators")
    if isinstance(investigators, dict) and investigators:
        return sorted(str(key) for key in investigators)
    pc = state.get("pc")
    if isinstance(pc, dict):
        return [str(pc.get("id") or pc.get("stable_id") or "pc")]
    return []


def _clue_text(state: dict, clue_id: str) -> str:
    registry = state.get("clue_registry") or {}
    entry = (registry.get("clues") or {}).get(clue_id) if isinstance(registry, dict) else None
    if isinstance(entry, dict) and entry.get("text"):
        return str(entry["text"])
    catalog = state.get("clue_catalog") or {}
    if isinstance(catalog, dict) and clue_id in catalog:
        return str((catalog[clue_id] or {}).get("text") or clue_id)
    return clue_id


def _item_label(state: dict, item_id: str) -> str:
    registry = state.get("item_registry") or {}
    entry = (registry.get("items") or {}).get(item_id) if isinstance(registry, dict) else None
    if isinstance(entry, dict) and entry.get("label"):
        return str(entry["label"])
    return item_id


def _derive_plan(
    state: dict,
    *,
    command_kind: str,
    command_payload: dict,
    events: list[dict],
) -> list[dict]:
    """把一条已提交命令/其事件翻译成记忆写入计划（纯函数，不写库）。

    每条计划：{character_id, knowledge_type, content, scene_id, topics, subjects,
    derivation_key, event_type, event_sequence}。知情依据只取事件明确指向的角色。
    """
    plan: list[dict] = []
    by_type: dict[str, list[dict]] = {}
    for envelope in events:
        by_type.setdefault(str(envelope.get("type") or ""), []).append(envelope)

    def seq_of(event_type: str) -> int:
        items = by_type.get(event_type) or []
        return int(items[0].get("sequence") or 0) if items else 0

    for envelope in by_type.get("scene_changed", []):
        payload = envelope.get("payload") or {}
        scene = payload.get("scene") or {}
        scene_id = str(scene.get("id") or "")
        scene_name = str(scene.get("name") or scene_id)
        for character_id in _roster_ids(state):
            plan.append(
                {
                    "character_id": character_id,
                    "knowledge_type": "experienced",
                    "content": f"抵达了〈{scene_name}〉",
                    "scene_id": scene_id,
                    "topics": ["scene"],
                    "derivation_key": f"ev:{envelope.get('event_id')}:{character_id}",
                    "event_type": "scene_changed",
                    "event_sequence": int(envelope.get("sequence") or 0),
                }
            )
    for envelope in by_type.get("clue_granted", []):
        payload = envelope.get("payload") or {}
        character_id = str(payload.get("investigator_id") or "")
        clue_id = str(payload.get("clue_id") or "")
        if not character_id:
            continue
        plan.append(
            {
                "character_id": character_id,
                "knowledge_type": "told",
                "content": f"得知线索：{_clue_text(state, clue_id)[:200]}",
                "scene_id": "",
                "topics": ["clue"],
                "subjects": [clue_id] if clue_id else [],
                "derivation_key": f"ev:{envelope.get('event_id')}:{character_id}",
                "event_type": "clue_granted",
                "event_sequence": int(envelope.get("sequence") or 0),
            }
        )
    for envelope in by_type.get("handout_presented", []):
        payload = envelope.get("payload") or {}
        character_id = str(payload.get("investigator_id") or "")
        label = str(payload.get("caption") or payload.get("asset_id") or "")
        if not character_id:
            continue
        plan.append(
            {
                "character_id": character_id,
                "knowledge_type": "experienced",
                "content": f"查看了资料〈{label[:120]}〉",
                "scene_id": "",
                "topics": ["handout"],
                "derivation_key": f"ev:{envelope.get('event_id')}:{character_id}",
                "event_type": "handout_presented",
                "event_sequence": int(envelope.get("sequence") or 0),
            }
        )
    for envelope in by_type.get("check_resolved", []):
        payload = envelope.get("payload") or {}
        character_id = str(payload.get("investigator_id") or "")
        if not character_id:
            continue
        outcome = "成功" if payload.get("outcome") == "success" else "失败"
        plan.append(
            {
                "character_id": character_id,
                "knowledge_type": "experienced",
                "content": (
                    f"进行了〈{payload.get('skill')}〉检定：{payload.get('level')}（{outcome}）"
                ),
                "scene_id": str((state.get("current_scene") or {}).get("id") or ""),
                "topics": ["check"],
                "derivation_key": f"ev:{envelope.get('event_id')}:{character_id}",
                "event_type": "check_resolved",
                "event_sequence": int(envelope.get("sequence") or 0),
            }
        )
    # 命令载荷级派生（事件投影不足以还原语义的几种）。
    if command_kind == "use_item":
        character_id = str(command_payload.get("investigator_id") or "")
        item_id = str(command_payload.get("item_id") or "")
        if character_id and item_id:
            plan.append(
                {
                    "character_id": character_id,
                    "knowledge_type": "experienced",
                    "content": (
                        f"使用了〈{_item_label(state, item_id)}〉"
                        f"（{str(command_payload.get('operation') or '')[:60]}）"
                    ),
                    "scene_id": str((state.get("current_scene") or {}).get("id") or ""),
                    "topics": ["item"],
                    "derivation_key": f"cmd:{command_payload.get('_command_id')}:{character_id}",
                    "event_type": "inventory_changed",
                    "event_sequence": seq_of("inventory_changed"),
                }
            )
    if command_kind == "transfer_item":
        target = command_payload.get("to") or {}
        item_id = str(command_payload.get("item_id") or "")
        if target.get("kind") == "investigator" and target.get("id"):
            character_id = str(target["id"])
            plan.append(
                {
                    "character_id": character_id,
                    "knowledge_type": "experienced",
                    "content": (
                        f"获得了〈{_item_label(state, item_id)}〉"
                        f"×{int(command_payload.get('quantity') or 1)}"
                    ),
                    "scene_id": str((state.get("current_scene") or {}).get("id") or ""),
                    "topics": ["item"],
                    "derivation_key": f"cmd:{command_payload.get('_command_id')}:{character_id}",
                    "event_type": "inventory_changed",
                    "event_sequence": seq_of("inventory_changed"),
                }
            )
    if command_kind == "adjust_stat":
        character_id = str(command_payload.get("investigator_id") or "")
        if character_id:
            delta = int(command_payload.get("delta") or 0)
            plan.append(
                {
                    "character_id": character_id,
                    "knowledge_type": "experienced",
                    "content": (
                        f"{command_payload.get('field')} {'+' if delta >= 0 else ''}{delta}"
                        f"（{str(command_payload.get('reason') or '')[:120]}）"
                    ),
                    "scene_id": str((state.get("current_scene") or {}).get("id") or ""),
                    "topics": ["condition"],
                    "derivation_key": f"cmd:{command_payload.get('_command_id')}:{character_id}",
                    "event_type": "state_changed",
                    "event_sequence": seq_of("state_changed"),
                }
            )
    return plan


def _apply_plan(session, world_id: str, plan: list[dict], *, revision: int, source: dict) -> int:
    written = 0
    for item in plan:
        try:
            with session.begin_nested():  # 单条冲突不影响本批其它条目
                insert_memory(
                    session,
                    world_id,
                    character_id=item["character_id"],
                    character_kind="investigator",
                    knowledge_type=item["knowledge_type"],
                    content=item["content"],
                    scene_id=item.get("scene_id") or "",
                    subjects=item.get("subjects") or [],
                    topics=item.get("topics") or [],
                    derivation_key=item.get("derivation_key"),
                    source={
                        **source,
                        "event_type": item.get("event_type") or "",
                        "event_sequence": int(item.get("event_sequence") or 0),
                    },
                    revision=revision,
                    sequence=int(item.get("event_sequence") or 0),
                )
            written += 1
        except IntegrityError:
            continue  # derivation_key 命中：已派生过（重试/补建幂等）
    return written


def derive_from_commit(
    database_url: str,
    world_id: str,
    *,
    command_kind: str = "",
    command_payload: dict | None = None,
    command_id: str = "",
    events: list[dict],
    revision_after: int,
) -> int:
    """从一次已提交的事务派生记忆。独立事务运行：失败只记日志，绝不影响
    已提交的游戏事件；调用方负责 try/except（这里也不抛出数据库异常）。"""
    payload = dict(command_payload or {})
    if command_id:
        payload["_command_id"] = command_id
    with session_scope(database_url) as session:
        row = session.get(WorldState, world_id)
        if row is None:
            return 0
        state, _ = migrate_world_state(dict(row.state or {}))
        plan = _derive_plan(
            state,
            command_kind=command_kind,
            command_payload=payload,
            events=events,
        )
        if not plan:
            return 0
        return _apply_plan(
            session,
            world_id,
            plan,
            revision=revision_after,
            source={"kind": "derived", "command_id": command_id},
        )


def repair_derivation(database_url: str, world_id: str) -> dict:
    """幂等补建：重扫已提交命令账本与事件 outbox，补齐缺失的派生记忆。"""
    with session_scope(database_url) as session:
        commands = (
            session.execute(
                select(GameCommand)
                .where(
                    GameCommand.world_id == world_id,
                    GameCommand.status == "committed",
                )
                .order_by(GameCommand.created_at)
            )
            .scalars()
            .all()
        )
        rows = [
            (command.kind, dict(command.payload or {}), command.command_id, int(command.revision))
            for command in commands
        ]
        events = (
            session.execute(
                select(EventOutbox)
                .where(EventOutbox.world_id == world_id)
                .order_by(EventOutbox.sequence)
            )
            .scalars()
            .all()
        )
        by_cause: dict[str, list[dict]] = {}
        for event in events:
            by_cause.setdefault(str(event.cause_request_id or ""), []).append(
                {
                    "event_id": int(event.id),
                    "type": event.event_type,
                    "sequence": int(event.sequence),
                    "payload": dict(event.payload or {}),
                }
            )
    total = 0
    for kind, payload, command_id, revision in rows:
        derived = derive_from_commit(
            database_url,
            world_id,
            command_kind=kind,
            command_payload=payload,
            command_id=command_id,
            events=by_cause.get(command_id, []),
            revision_after=revision,
        )
        total += derived
    return {"scanned_commands": len(rows), "derived": total}


# ---------------------------------------------------------------------------
# 检索（权限检查在 service 层；这里只做数据过滤与预算）
# ---------------------------------------------------------------------------


def _project(row: CharacterMemory) -> dict:
    return {
        "memory_id": row.memory_id,
        "character_id": row.character_id,
        "character_kind": row.character_kind,
        "knowledge_type": row.knowledge_type,
        "content": row.content,
        "scene_id": row.scene_id,
        "subjects": list(row.subjects or []),
        "topics": list(row.topics or []),
        "status": row.status,
        **({"superseded_by": row.superseded_by} if row.superseded_by else {}),
        "source": dict(row.source or {}),
        "created_sequence": int(row.created_sequence),
    }


def retrieve(
    session,
    world_id: str,
    *,
    character_ids: list[str] | None = None,
    scene_id: str | None = None,
    topics: list[str] | None = None,
    text: str = "",
    limit: int = DEFAULT_LIMIT,
    char_budget: int = DEFAULT_CHAR_BUDGET,
    include_history: bool = False,
) -> list[dict]:
    """按角色/场景/主题/文本检索。第一阶段就是数据库过滤 + 简单相关度，
    不引向量能见度以外的东西；预算（条数 × 字符）在这里硬执行。
    """
    limit = max(1, min(int(limit), MAX_LIMIT))
    char_budget = max(200, min(int(char_budget), MAX_CHAR_BUDGET))
    query = select(CharacterMemory).where(CharacterMemory.world_id == world_id)
    if not include_history:
        query = query.where(CharacterMemory.status == "active")
    if character_ids:
        query = query.where(
            CharacterMemory.character_id.in_([str(value) for value in character_ids])
        )
    rows = (
        session.execute(query.order_by(CharacterMemory.created_sequence.desc()).limit(200))
        .scalars()
        .all()
    )
    want_topics = {str(t) for t in (topics or []) if str(t).strip()}
    needle = str(text or "").casefold().strip()

    def score(row: CharacterMemory) -> int:
        value = 0
        if scene_id and row.scene_id == scene_id:
            value += 2
        if want_topics and want_topics & {str(t) for t in (row.topics or [])}:
            value += 2
        if needle and needle in row.content.casefold():
            value += 3
        return value

    ranked = sorted(rows, key=lambda row: (score(row), int(row.created_sequence)), reverse=True)
    if needle or want_topics or scene_id:
        ranked = [row for row in ranked if score(row) > 0] or ranked[:limit]
    out: list[dict] = []
    spent = 0
    for row in ranked:
        projection = _project(row)
        cost = len(projection["content"]) + 40
        if len(out) >= limit or spent + cost > char_budget:
            break
        out.append(projection)
        spent += cost
    return out
