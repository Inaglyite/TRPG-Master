"""Skill 加载 + 存档/读档（文件夹式多槽位 + 世界状态快照）

存档结构:
  saves/slot_000/              ← 自动存档（退出时）
    messages.json              ← LLM 对话历史
    snapshot.json              ← 世界状态快照（读档时恢复，防止线索污染）
    meta.json                  ← { created_at, scene, hp, san, clue_count }
  saves/slot_001/              ← 手动存档
  saves/slot_002/
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path

from .config import (
    AUTO_SAVE_SLOT,
    DEFAULT_MODULE_NAME,
    PROJECT_ROOT,
    PROMPT_PROFILE,
    RUNTIME_ROOT,
    SKILL_LOAD_ORDER,
)
from .database import SaveSlot, Snapshot, new_id, session_scope, utcnow
from .handouts import refresh_static_handout_config
from .runtime import RuntimeContext, default_world_id
from .world_migrations import migrate_world_state
from .world_store import atomic_write_json


def _runtime_context(context: RuntimeContext | None = None) -> RuntimeContext:
    if context is not None:
        return context
    return RuntimeContext(
        PROJECT_ROOT,
        RUNTIME_ROOT,
        default_world_id(DEFAULT_MODULE_NAME),
        DEFAULT_MODULE_NAME,
    )


def _write_compat_exports() -> bool:
    return os.environ.get("TRPG_WRITE_COMPAT_EXPORTS", "1").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _import_legacy_slot(context: RuntimeContext, slot_id: str) -> SaveSlot | None:
    """Import one legacy slot on demand; database remains the read authority."""
    slot_dir = _slot_dir(slot_id, context)
    message_file = slot_dir / "messages.json"
    if not message_file.is_file():
        return None
    try:
        messages = normalize_tool_message_history(
            json.loads(message_file.read_text(encoding="utf-8"))
        )
        snapshot_file = slot_dir / "snapshot.json"
        snapshot = (
            json.loads(snapshot_file.read_text(encoding="utf-8")) if snapshot_file.is_file() else {}
        )
        meta_file = slot_dir / "meta.json"
        meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.is_file() else {}
    except (OSError, json.JSONDecodeError):
        return None
    with session_scope(context.database_url) as session:
        row = (
            session.query(SaveSlot)
            .filter_by(world_id=context.world_id, slot_key=slot_id)
            .one_or_none()
        )
        if row is None:
            row = SaveSlot(
                id=new_id("save"),
                world_id=context.world_id,
                slot_key=slot_id,
                kind="auto" if slot_id == AUTO_SAVE_SLOT else "manual",
                snapshot_id="pending",
            )
            session.add(row)
        snapshot_row = Snapshot(
            id=new_id("snapshot"),
            world_id=context.world_id,
            kind="legacy_import",
            revision=int(snapshot.get("revision", 0)),
            state=snapshot,
        )
        session.add(snapshot_row)
        row.messages = messages
        row.snapshot_id = snapshot_row.id
        row.metadata_json = meta
        row.label = str(meta.get("label") or "")
        row.world_revision = int(snapshot.get("revision", 0))
        session.flush()
        return row


# ---- Skill 加载 ----


def _module_prompt_content(content: str) -> str:
    """Drop module default PC templates from the runtime prompt.

    The active investigator is copied into world_state.json at game start. Keeping a
    module.md default PC block in the system prompt can make the model call the
    player by the template name after a different character is selected.
    """
    default_pc = ""
    pc_block = re.search(r"\n# PC[^\n]*\n(.*?)(?=\n# )", content, flags=re.DOTALL)
    if pc_block:
        name_match = re.search(r"(?m)^\s*name:\s*(.+?)\s*$", pc_block.group(1))
        if name_match:
            default_pc = name_match.group(1).strip().strip("\"'")

    content = re.sub(
        r"\n# PC[^\n]*\n.*?(?=\n# )",
        "\n# PC - 调查员\n\n（运行时调查员以 world_state.json 的 pc 字段为准；模组模板 PC 不作为玩家身份。）\n",
        content,
        count=1,
        flags=re.DOTALL,
    )
    if default_pc:
        content = content.replace(default_pc, "所选调查员")
        content = content.replace("私家侦探所选调查员", "所选调查员")
    return content


_PROMPT_SPINE_MARKER = "trpg-master:prompt-role=spine"
_OPENING_SKILL_LOAD_ORDER = (
    "core/trpg_master.skill",
    "core/no_spoiler.skill",
    "keeper/keeper_atmosphere.skill",
    "keeper/keeper_npc.skill",
)


def load_system_prompt(
    context: RuntimeContext | None = None,
    *,
    profile: str | None = None,
) -> str:
    context = _runtime_context(context)
    profile = (profile or PROMPT_PROFILE).lower()
    if profile not in {"full", "hybrid", "opening"}:
        profile = "full"
    parts = []
    # 核心 skill
    skill_order = _OPENING_SKILL_LOAD_ORDER if profile == "opening" else SKILL_LOAD_ORDER
    for name in skill_order:
        path = context.project_root / "skills" / name
        if path.exists():
            content = path.read_text(encoding="utf-8").strip()
            if content:
                parts.append(content)
    # 结构化开场的公开剧情由本轮权威快照提供。这里不加载 module.md、
    # 模组 skill 或无关规则，避免私有时间线泄露并缩短首轮输入。
    if profile == "opening":
        return "\n\n---\n\n".join(parts)
    # hybrid 只在模组明确提供足量剧情脊柱时生效；否则无声回退 full。
    mod_skills_dir = context.module_dir / "skills"
    mod_skill_contents: list[str] = []
    if mod_skills_dir.exists():
        for mod_skill in sorted(mod_skills_dir.glob("*.skill")):
            content = mod_skill.read_text(encoding="utf-8").strip()
            if content:
                mod_skill_contents.append(content)
    spine_parts = [
        content for content in mod_skill_contents if _PROMPT_SPINE_MARKER in content[:300]
    ]
    use_spine = profile == "hybrid" and sum(map(len, spine_parts)) >= 1000

    # 当前模组的剧情设定（module.md）——让 GM 知道本模组的故事背景
    module_md = context.module_dir / "module.md"
    if module_md.exists() and not use_spine:
        content = _module_prompt_content(module_md.read_text(encoding="utf-8")).strip()
        if content:
            parts.append(content)
    # 仅加载【当前模组】的专属 skill，避免多模组内容串扰
    parts.extend(spine_parts if use_spine else mod_skill_contents)
    return "\n\n---\n\n".join(parts)


# ---- 存档 ----


def _slot_dir(slot_id: str, context: RuntimeContext | None = None) -> Path:
    if not re.fullmatch(r"slot_\d{3,}", str(slot_id)):
        raise ValueError(f"非法存档槽位: {slot_id!r}")
    return _runtime_context(context).saves_dir / slot_id


def _next_slot(context: RuntimeContext | None = None) -> str:
    """返回下一个可用的手动存档槽位 ID"""
    context = _runtime_context(context)
    with session_scope(context.database_url) as session:
        existing = sorted(
            int(row.slot_key.split("_")[1])
            for row in session.query(SaveSlot).filter_by(world_id=context.world_id).all()
            if row.slot_key != AUTO_SAVE_SLOT
        )
    n = 1
    while n in existing:
        n += 1
    return f"slot_{n:03d}"


def _slot_meta(messages: list, world_state: dict, context: RuntimeContext) -> dict:
    """从消息和世界状态生成存档元数据"""
    pc = world_state.get("pc", {})
    scene = world_state.get("current_scene", {})
    clues = world_state.get("clues_found", {})
    if isinstance(clues, dict):
        clue_count = sum(len(v) for v in clues.values())
    else:
        clue_count = len(clues) if isinstance(clues, list) else 0

    return {
        "created_at": datetime.now().isoformat(),
        "scene_id": scene.get("id", ""),
        "scene_name": scene.get("name", ""),
        "character_id": pc.get("character_id", ""),
        "character_name": pc.get("name", ""),
        "character_source": pc.get("character_source", ""),
        "character_source_path": pc.get("character_source_path", ""),
        "hp": f"{pc.get('hp', 0)}/{pc.get('max_hp', 0)}",
        "san": f"{pc.get('san', 0)}/{pc.get('max_san', 0)}",
        "clue_count": clue_count,
        "message_count": len(messages),
        "world_id": context.world_id,
        "module_name": context.module_name,
        "world_revision": world_state.get("revision", 0),
        "schema_version": world_state.get("schema_version", 0),
    }


def save_game(
    messages: list,
    slot_id: str | None = None,
    *,
    context: RuntimeContext | None = None,
) -> str:
    """保存游戏到指定槽位（默认自动存档）。返回槽位 ID。"""
    context = _runtime_context(context)
    if slot_id is None:
        slot_id = AUTO_SAVE_SLOT

    _slot_dir(slot_id, context)  # validation only

    # 序列化消息（去掉不可序列化的字段）
    serializable = []
    for m in messages:
        entry = {"role": m["role"], "content": m.get("content", "")}
        if "tool_calls" in m:
            entry["tool_calls"] = m["tool_calls"]
        if "tool_call_id" in m:
            entry["tool_call_id"] = m["tool_call_id"]
        serializable.append(entry)

    # 读取当前世界状态作为快照
    world_state = context.world_store.load()

    meta = _slot_meta(serializable, world_state, context)
    with session_scope(context.database_url) as session:
        row = (
            session.query(SaveSlot)
            .filter_by(world_id=context.world_id, slot_key=slot_id)
            .one_or_none()
        )
        if row is None:
            row = SaveSlot(
                id=new_id("save"),
                world_id=context.world_id,
                slot_key=slot_id,
                kind="auto" if slot_id == AUTO_SAVE_SLOT else "manual",
                snapshot_id="pending",
            )
            session.add(row)
        snapshot_row = Snapshot(
            id=new_id("snapshot"),
            world_id=context.world_id,
            kind="save",
            revision=int(world_state.get("revision", 0)),
            state=world_state,
        )
        session.add(snapshot_row)
        row.metadata_json = meta
        row.messages = serializable
        row.snapshot_id = snapshot_row.id
        row.world_revision = int(world_state.get("revision", 0))
        row.updated_at = utcnow()

    if _write_compat_exports():
        slot_dir = _slot_dir(slot_id, context)
        slot_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(slot_dir / "messages.json", serializable)
        atomic_write_json(slot_dir / "snapshot.json", world_state)
        atomic_write_json(slot_dir / "meta.json", meta)

    return slot_id


def normalize_tool_message_history(messages: list[dict]) -> list[dict]:
    """Repair interrupted tool batches from older saves.

    OpenAI-compatible APIs require all responses to an assistant tool-call batch
    before any user or assistant message. Older builds could insert an optional
    skill instruction between those responses.
    """
    repaired: list[dict] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        tool_calls = message.get("tool_calls") if isinstance(message, dict) else None
        if message.get("role") != "assistant" or not isinstance(tool_calls, list):
            repaired.append(message)
            index += 1
            continue

        expected_ids = [
            str(call.get("id") or "")
            for call in tool_calls
            if isinstance(call, dict) and call.get("id")
        ]
        if not expected_ids:
            repaired.append(message)
            index += 1
            continue

        repaired.append(message)
        responses: dict[str, dict] = {}
        deferred: list[dict] = []
        cursor = index + 1
        while cursor < len(messages) and len(responses) < len(expected_ids):
            candidate = messages[cursor]
            role = candidate.get("role") if isinstance(candidate, dict) else None
            if role == "assistant":
                break
            if role == "tool":
                call_id = str(candidate.get("tool_call_id") or "")
                if call_id in expected_ids and call_id not in responses:
                    responses[call_id] = candidate
            else:
                deferred.append(candidate)
            cursor += 1

        for call_id in expected_ids:
            repaired.append(
                responses.get(
                    call_id,
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": "[错误] 旧存档缺少该工具调用的返回结果",
                    },
                )
            )
        repaired.extend(deferred)
        index = cursor
    return repaired


def load_game(
    slot_id: str | None = None,
    *,
    context: RuntimeContext | None = None,
) -> tuple[list, dict] | tuple[None, None]:
    """读取存档。返回 (messages, world_snapshot) 或 (None, None)。
    如果 slot_id 为 None，加载最新存档（按修改时间）。
    """
    context = _runtime_context(context)
    if slot_id:
        _slot_dir(slot_id, context)
        with session_scope(context.database_url) as session:
            row = (
                session.query(SaveSlot)
                .filter_by(world_id=context.world_id, slot_key=slot_id)
                .one_or_none()
            )
            if row is None:
                imported = _import_legacy_slot(context, slot_id)
                if imported is None:
                    return None, None
                with session_scope(context.database_url) as imported_session:
                    snapshot = imported_session.get(Snapshot, imported.snapshot_id)
                    return normalize_tool_message_history(imported.messages or []), dict(
                        snapshot.state if snapshot else {}
                    )
            snapshot = session.get(Snapshot, row.snapshot_id)
            return normalize_tool_message_history(row.messages or []), dict(
                snapshot.state if snapshot else {}
            )

    # 找最新存档
    slots = list_saves(context=context)
    if not slots:
        return None, None

    latest = slots[0]  # 已按时间倒序
    return load_game(latest["id"], context=context)


def _load_slot(slot_dir: Path) -> tuple[list, dict] | tuple[None, None]:
    """从槽位目录加载"""
    msg_file = slot_dir / "messages.json"
    snap_file = slot_dir / "snapshot.json"

    if not msg_file.exists():
        return None, None

    messages = normalize_tool_message_history(json.loads(msg_file.read_text(encoding="utf-8")))
    snapshot = json.loads(snap_file.read_text(encoding="utf-8")) if snap_file.exists() else {}

    return messages, snapshot


def _migrate_snapshot(snapshot: dict) -> dict:
    """将旧版快照迁移到最新数据结构（向下兼容）。"""
    migrated, _ = migrate_world_state(snapshot)
    return migrated


def restore_snapshot(
    snapshot: dict,
    *,
    context: RuntimeContext | None = None,
    expected_revision: int | None = None,
) -> bool:
    """将世界状态快照恢复到 world_state.json（自动迁移旧版数据结构）。返回是否成功。"""
    context = _runtime_context(context)
    if not snapshot:
        return False
    snapshot = _migrate_snapshot(snapshot)
    if context.initial_state_file.exists():
        template = json.loads(context.initial_state_file.read_text(encoding="utf-8"))
        refresh_static_handout_config(snapshot, template)
    context.world_store.restore(snapshot, expected_revision=expected_revision)
    return True


def list_saves(*, context: RuntimeContext | None = None) -> list[dict]:
    """列出所有存档的元数据，按时间倒序"""
    context = _runtime_context(context)
    result = []
    if context.saves_dir.is_dir():
        for slot_dir in context.saves_dir.glob("slot_*"):
            if slot_dir.is_dir():
                _import_legacy_slot(context, slot_dir.name)
    with session_scope(context.database_url) as session:
        rows = session.query(SaveSlot).filter_by(world_id=context.world_id).all()
        for row in rows:
            meta = dict(row.metadata_json or {})
            meta["id"] = row.slot_key
            result.append(meta)

    result.sort(key=lambda m: m.get("created_at", ""), reverse=True)
    return result


def has_save(*, context: RuntimeContext | None = None) -> bool:
    """检查是否有任何存档"""
    return len(list_saves(context=context)) > 0


def delete_save(slot_id: str, *, context: RuntimeContext | None = None):
    """删除指定存档"""
    context = _runtime_context(context)
    _slot_dir(slot_id, context)
    with session_scope(context.database_url) as session:
        row = (
            session.query(SaveSlot)
            .filter_by(world_id=context.world_id, slot_key=slot_id)
            .one_or_none()
        )
        if row is not None:
            session.delete(row)


def rename_save(
    slot_id: str,
    label: str,
    *,
    context: RuntimeContext | None = None,
) -> bool:
    """重命名存档——更新 meta.json 中的 label 字段"""
    context = _runtime_context(context)
    _slot_dir(slot_id, context)
    with session_scope(context.database_url) as session:
        row = (
            session.query(SaveSlot)
            .filter_by(world_id=context.world_id, slot_key=slot_id)
            .one_or_none()
        )
        if row is None:
            return False
        meta = dict(row.metadata_json or {})
        meta["label"] = label
        row.label = label
        row.metadata_json = meta
        row.updated_at = utcnow()
        return True
