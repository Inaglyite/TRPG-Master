#!/usr/bin/env python3
"""One-time, idempotent import of legacy worlds/ directories into the database."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.database import (  # noqa: E402
    AuditEvent,
    PlayerNote,
    SaveSlot,
    Snapshot,
    Turn,
    TurnEvent,
    User,
    World,
    WorldMember,
    WorldState,
    database_url,
    initialize_database,
    new_id,
    session_scope,
)
from src.player_notes import PLAYER_NOTES_SCHEMA_VERSION  # noqa: E402
from src.world_migrations import migrate_world_state  # noqa: E402

MAX_BIGINT = (1 << 63) - 1


def read_object(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} 根节点不是 object")
    return data


def _bounded_text(value: object, label: str, max_length: int) -> str:
    text = str(value or "")
    if not text:
        raise ValueError(f"{label} 不能为空")
    if "\x00" in text or len(text) > max_length:
        raise ValueError(f"{label} 超出数据库允许范围")
    return text


def _optional_text(value: object, label: str, max_length: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} 必须是字符串或 null")
    if "\x00" in value or len(value) > max_length:
        raise ValueError(f"{label} 超出数据库允许范围")
    return value


def _revision(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} 必须是非负整数")
    try:
        revision = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 必须是非负整数") from exc
    if revision < 0 or revision > MAX_BIGINT:
        raise ValueError(f"{label} 必须位于 0..{MAX_BIGINT}")
    return revision


def _record_time(record: dict, key: str, fallback_path: Path) -> datetime:
    """Preserve the requested legacy timestamp without conflating lifecycle fields."""
    value = record.get(key)
    if value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
        except ValueError:
            pass
    return datetime.fromtimestamp(fallback_path.stat().st_mtime, UTC)


def _legacy_artifacts(world_dir: Path, world_id: str, module_name: str):
    """Read and validate every source artifact before opening a transaction."""
    saves = []
    for slot_dir in sorted((world_dir / "saves").glob("slot_*")):
        slot_key = _bounded_text(slot_dir.name, "存档 slot_key", 40)
        messages_path = slot_dir / "messages.json"
        snapshot_path = slot_dir / "snapshot.json"
        try:
            messages = json.loads(messages_path.read_text(encoding="utf-8"))
            snapshot = read_object(snapshot_path)
            meta = read_object(slot_dir / "meta.json") if (slot_dir / "meta.json").is_file() else {}
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"存档 {slot_dir.name} 无法导入: {exc}") from exc
        if not isinstance(messages, list):
            raise ValueError(f"存档 {slot_dir.name} 的 messages.json 根节点不是 array")
        revision = _revision(
            snapshot.get("revision", 0),
            f"存档 {slot_dir.name} 的 revision",
        )
        label = str(meta.get("label") or "")
        if "\x00" in label or len(label) > 200:
            raise ValueError(f"存档 {slot_dir.name} 的 label 超出数据库允许范围")
        saves.append(
            {
                "slot_key": slot_key,
                "messages": messages,
                "snapshot": snapshot,
                "metadata": meta,
                "revision": revision,
                "label": label,
            }
        )

    turns = []
    turns_root = world_dir / "turns"
    turn_dirs = (
        sorted(path for path in turns_root.iterdir() if path.is_dir())
        if turns_root.is_dir()
        else []
    )
    for turn_dir in turn_dirs:
        record_path = turn_dir / "record.json"
        if not record_path.is_file():
            raise ValueError(f"回合目录 {turn_dir.name} 缺少 record.json")
        try:
            record = read_object(record_path)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"回合 {turn_dir.name} 的 record.json 损坏: {exc}") from exc
        turn_id = str(record.get("turn_id") or "")
        if turn_id != turn_dir.name:
            raise ValueError(
                f"回合目录 {turn_dir.name} 与 record.turn_id={turn_id!r} 不一致"
            )
        _bounded_text(turn_id, "turn_id", 80)
        if record.get("status") != "completed":
            continue
        try:
            messages = json.loads(
                (turn_dir / "messages.json").read_text(encoding="utf-8")
            )
            snapshot = read_object(turn_dir / "snapshot.json")
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"回合 {turn_id} 的提交产物损坏: {exc}") from exc
        if not isinstance(messages, list):
            raise ValueError(f"回合 {turn_id} 的 messages.json 根节点不是 array")
        events = record.get("events", [])
        if not isinstance(events, list):
            raise ValueError(f"回合 {turn_id} 的 events 不是 array")
        if not all(isinstance(event, dict) for event in events):
            raise ValueError(f"回合 {turn_id} 的 events 必须全部是 object")
        kind = _bounded_text(record.get("kind") or "action", "回合 kind", 40)
        owner_token = str(record.get("owner_token") or "")
        if "\x00" in owner_token or len(owner_token) > 80:
            raise ValueError(f"回合 {turn_id} 的 owner_token 超出数据库允许范围")
        turns.append(
            {
                "turn_id": turn_id,
                "record": record,
                "messages": messages,
                "snapshot": snapshot,
                "revision": _revision(
                    snapshot.get("revision", 0),
                    f"回合 {turn_id} 快照的 revision",
                ),
                "created_at": _record_time(record, "created_at", record_path),
                "completed_at": _record_time(
                    record,
                    "completed_at",
                    record_path,
                ),
                "parent_turn_id": _optional_text(
                    record.get("parent_turn_id"),
                    f"回合 {turn_id} 的 parent_turn_id",
                    80,
                ),
                "origin_world_id": _optional_text(
                    record.get("origin_world_id"),
                    f"回合 {turn_id} 的 origin_world_id",
                    160,
                ),
                "kind": kind,
                "owner_token": owner_token,
                "player_input": _optional_text(
                    record.get("player_input"),
                    f"回合 {turn_id} 的 player_input",
                    MAX_BIGINT,
                ),
            }
        )
    turns.sort(key=lambda item: item["completed_at"])
    return saves, turns


def prepare_world(world_dir: Path) -> dict:
    """Strictly validate a world and every importable artifact before any write."""
    metadata = read_object(world_dir / "world.json")
    state, _ = migrate_world_state(read_object(world_dir / "world_state.json"))
    world_id = _bounded_text(world_dir.name, "world_id", 160)
    module_name = _bounded_text(metadata.get("module_name"), "module_name", 160)
    state["revision"] = _revision(state.get("revision", 0), "世界状态 revision")
    module_id = str(metadata.get("module_id") or "")
    module_version = str(metadata.get("module_version") or "")
    if "\x00" in module_id or len(module_id) > 160:
        raise ValueError("module_id 超出数据库允许范围")
    if "\x00" in module_version or len(module_version) > 80:
        raise ValueError("module_version 超出数据库允许范围")
    source_saves, source_turns = _legacy_artifacts(world_dir, world_id, module_name)
    note = (
        read_object(world_dir / "player_notes.json")
        if (world_dir / "player_notes.json").is_file()
        else None
    )
    if note is not None:
        note = {
            **note,
            "revision": _revision(note.get("revision", 0), "玩家笔记 revision"),
            "text": str(note.get("text") or ""),
        }
    return {
        "metadata": metadata,
        "state": state,
        "world_id": world_id,
        "module_name": module_name,
        "module_id": module_id,
        "module_version": module_version,
        "saves": source_saves,
        "turns": source_turns,
        "note": note,
    }


def _assert_owner_compatible(session, world: World | None, world_id: str, owner: User) -> None:
    if world is not None and world.created_by and world.created_by != owner.id:
        raise ValueError(
            f"世界 {world_id} 已属于其他房主；--owner 不会隐式转移所有权"
        )
    owner_members = (
        session.query(WorldMember)
        .filter_by(world_id=world_id, role="owner")
        .all()
    )
    if len(owner_members) > 1:
        raise ValueError(f"世界 {world_id} 已存在多个房主，拒绝继续导入")
    if owner_members and owner_members[0].user_id != owner.id:
        raise ValueError(
            f"世界 {world_id} 已属于其他房主；--owner 不会隐式转移所有权"
        )


def _delete_orphan_snapshot(session, snapshot_id: str | None) -> None:
    if not snapshot_id:
        return
    session.flush()
    if session.query(SaveSlot.id).filter_by(snapshot_id=snapshot_id).first():
        return
    if session.query(Turn.pk).filter_by(snapshot_id=snapshot_id).first():
        return
    snapshot = session.get(Snapshot, snapshot_id)
    if snapshot is not None:
        session.delete(snapshot)


def import_world(
    world_dir: Path,
    db_url: str,
    owner: User | None,
    *,
    replace: bool,
    prepared: dict | None = None,
) -> dict:
    source = prepared or prepare_world(world_dir)
    metadata = source["metadata"]
    state = source["state"]
    world_id = source["world_id"]
    module_name = source["module_name"]
    module_id = source["module_id"]
    module_version = source["module_version"]
    source_saves = source["saves"]
    source_turns = source["turns"]
    note = source["note"]

    imported_saves = 0
    imported_turns = 0
    changed = False
    with session_scope(db_url) as session:
        world = session.get(World, world_id)
        existed = world is not None
        if owner:
            _assert_owner_compatible(session, world, world_id, owner)
        if world is None:
            world = World(id=world_id, module_name=module_name)
            session.add(world)
            changed = True
        if not existed or replace:
            world.module_name = module_name
            world.module_id = module_id
            world.module_version = module_version
            world.metadata_json = metadata
            world.created_by = owner.id if owner else None
            changed = True
        row = session.get(WorldState, world_id)
        if row is None:
            row = WorldState(
                world_id=world_id,
                schema_version=state["schema_version"],
                revision=state["revision"],
                state=state,
            )
            session.add(row)
            changed = True
        elif replace:
            row.schema_version = state["schema_version"]
            row.revision = state["revision"]
            row.state = state
            changed = True
        if owner:
            target_member = (
                session.query(WorldMember)
                .filter_by(world_id=world_id, user_id=owner.id)
                .one_or_none()
            )
            if target_member is None:
                session.add(
                    WorldMember(
                        id=new_id("member"),
                        world_id=world_id,
                        user_id=owner.id,
                        role="owner",
                    )
                )
                changed = True
            elif target_member.role != "owner":
                target_member.role = "owner"
                changed = True
            world.created_by = owner.id

        for source_save in source_saves:
            slot_key = source_save["slot_key"]
            save_row = (
                session.query(SaveSlot)
                .filter_by(world_id=world_id, slot_key=slot_key)
                .one_or_none()
            )
            if save_row is not None and not replace:
                continue
            previous_snapshot_id = save_row.snapshot_id if save_row is not None else None
            snapshot_row = Snapshot(
                id=new_id("snapshot"),
                world_id=world_id,
                kind="legacy_import",
                revision=source_save["revision"],
                state=source_save["snapshot"],
            )
            session.add(snapshot_row)
            session.flush()
            if save_row is None:
                save_row = SaveSlot(
                    id=new_id("save"),
                    world_id=world_id,
                    slot_key=slot_key,
                    kind="auto" if slot_key == "slot_000" else "manual",
                    snapshot_id=snapshot_row.id,
                )
                session.add(save_row)
            save_row.messages = source_save["messages"]
            save_row.snapshot_id = snapshot_row.id
            save_row.metadata_json = source_save["metadata"]
            save_row.label = source_save["label"]
            save_row.world_revision = source_save["revision"]
            _delete_orphan_snapshot(session, previous_snapshot_id)
            imported_saves += 1
            changed = True

        for source_turn in source_turns:
            turn_id = source_turn["turn_id"]
            turn_row = (
                session.query(Turn)
                .filter_by(world_id=world_id, id=turn_id)
                .one_or_none()
            )
            if turn_row is not None and not replace:
                continue
            previous_snapshot_id = turn_row.snapshot_id if turn_row is not None else None
            snapshot_row = Snapshot(
                id=new_id("snapshot"),
                world_id=world_id,
                source_turn_id=turn_id,
                kind="legacy_turn",
                revision=source_turn["revision"],
                state=source_turn["snapshot"],
            )
            session.add(snapshot_row)
            session.flush()
            if turn_row is None:
                turn_row = Turn(
                    pk=new_id("turnrow"),
                    id=turn_id,
                    world_id=world_id,
                    status="completed",
                    snapshot_id=snapshot_row.id,
                )
                session.add(turn_row)
            else:
                session.query(TurnEvent).filter_by(turn_pk=turn_row.pk).delete(
                    synchronize_session=False
                )
            turn_row.parent_turn_id = source_turn["parent_turn_id"]
            turn_row.origin_world_id = source_turn["origin_world_id"]
            turn_row.kind = source_turn["kind"]
            turn_row.status = "completed"
            turn_row.owner_token = source_turn["owner_token"]
            turn_row.player_input = source_turn["player_input"]
            turn_row.record = source_turn["record"]
            turn_row.messages = source_turn["messages"]
            turn_row.snapshot_id = snapshot_row.id
            turn_row.created_at = source_turn["created_at"]
            turn_row.completed_at = source_turn["completed_at"]
            session.flush()
            for sequence, event in enumerate(source_turn["record"].get("events", [])):
                session.add(
                    TurnEvent(
                        id=new_id("event"),
                        turn_pk=turn_row.pk,
                        turn_id=turn_row.id,
                        sequence=sequence,
                        event_type=str(event.get("type") or "unknown"),
                        payload=event,
                    )
                )
            _delete_orphan_snapshot(session, previous_snapshot_id)
            imported_turns += 1
            changed = True

        if note is not None:
            note_row = (
                session.query(PlayerNote)
                .filter_by(world_id=world_id, owner_key="__local__")
                .one_or_none()
            )
            if note_row is None:
                note_row = PlayerNote(
                    id=new_id("note"),
                    world_id=world_id,
                    user_id=None,
                    owner_key="__local__",
                )
                session.add(note_row)
                changed = True
            if replace or note_row.revision == 0 and not note_row.text:
                note_row.revision = note["revision"]
                note_row.text = note["text"]
                changed = True
    return {
        "world_id": world_id,
        "status": "imported" if changed else "skipped",
        "saves": imported_saves,
        "turns": imported_turns,
        "notes_schema": PLAYER_NOTES_SCHEMA_VERSION,
    }


def source_fingerprint(world_dirs: list[Path]) -> str:
    """Fingerprint all legacy source files so --once cannot hide new data."""
    digest = hashlib.sha256()
    for world_dir in world_dirs:
        digest.update(world_dir.name.encode("utf-8"))
        for path in sorted(item for item in world_dir.rglob("*") if item.is_file()):
            digest.update(path.relative_to(world_dir).as_posix().encode("utf-8"))
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--database-url")
    parser.add_argument("--owner")
    parser.add_argument("--replace", action="store_true")
    parser.add_argument(
        "--once",
        action="store_true",
        help="数据库已有成功导入标记时直接退出",
    )
    args = parser.parse_args()
    db_url = args.database_url or database_url(args.runtime_root)
    initialize_database(db_url)
    if args.once:
        with session_scope(db_url) as session:
            completed = (
                session.query(AuditEvent)
                .filter_by(event_type="legacy_import_completed", success=True)
                .first()
            )
            if completed is not None:
                # Compatibility exports continue changing after the database
                # becomes authoritative. --once is therefore a true one-shot
                # guard: re-importing with --replace could overwrite newer
                # database state with a stale world_state.json.
                print(json.dumps({"status": "already_imported"}, ensure_ascii=False))
                return 0
    world_dirs = [
        world_dir
        for world_dir in sorted(
            (args.runtime_root / "worlds").iterdir()
            if (args.runtime_root / "worlds").is_dir()
            else []
        )
        if (
            world_dir.is_dir()
            and (world_dir / "world.json").is_file()
            and (world_dir / "world_state.json").is_file()
        )
    ]
    fingerprint = source_fingerprint(world_dirs)
    owner = None
    if args.owner:
        with session_scope(db_url) as session:
            owner = session.query(User).filter_by(username=args.owner.lower()).one_or_none()
            if owner is None:
                raise SystemExit(f"owner 不存在: {args.owner}")
    # Validate every source first. A broken later world must not leave earlier
    # worlds partially imported while still failing to write the completion marker.
    prepared_worlds = [(world_dir, prepare_world(world_dir)) for world_dir in world_dirs]
    if owner:
        with session_scope(db_url) as session:
            for _world_dir, prepared in prepared_worlds:
                world_id = prepared["world_id"]
                _assert_owner_compatible(
                    session,
                    session.get(World, world_id),
                    world_id,
                    owner,
                )
    results = []
    for world_dir, prepared in prepared_worlds:
        results.append(
            import_world(
                world_dir,
                db_url,
                owner,
                replace=args.replace,
                prepared=prepared,
            )
        )
    if args.once:
        with session_scope(db_url) as session:
            session.query(AuditEvent).filter_by(
                event_type="legacy_import_completed",
                success=True,
            ).delete(synchronize_session=False)
            session.add(
                AuditEvent(
                    id=new_id("audit"),
                    event_type="legacy_import_completed",
                    success=True,
                    details={
                        "world_count": len(results),
                        "world_ids": [path.name for path in world_dirs],
                        "source_fingerprint": fingerprint,
                    },
                )
            )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
