#!/usr/bin/env python3
"""One-time, idempotent import of legacy worlds/ directories into the database."""

from __future__ import annotations

import argparse
import json
import sys
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
    utcnow,
)
from src.player_notes import PLAYER_NOTES_SCHEMA_VERSION  # noqa: E402
from src.turn_journal import TurnJournal  # noqa: E402
from src.world_migrations import migrate_world_state  # noqa: E402


def read_object(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} 根节点不是 object")
    return data


def import_world(world_dir: Path, db_url: str, owner: User | None, *, replace: bool) -> dict:
    metadata = read_object(world_dir / "world.json")
    state, _ = migrate_world_state(read_object(world_dir / "world_state.json"))
    world_id = world_dir.name
    module_name = str(metadata.get("module_name") or "")
    if not module_name:
        raise ValueError("缺少 module_name")
    with session_scope(db_url) as session:
        world = session.get(World, world_id)
        if world and not replace:
            return {"world_id": world_id, "status": "skipped"}
        if world is None:
            world = World(id=world_id, module_name=module_name)
            session.add(world)
        world.module_name = module_name
        world.module_id = str(metadata.get("module_id") or "")
        world.module_version = str(metadata.get("module_version") or "")
        world.metadata_json = metadata
        world.created_by = owner.id if owner else None
        row = session.get(WorldState, world_id)
        if row is None:
            row = WorldState(
                world_id=world_id,
                schema_version=state["schema_version"],
                revision=state["revision"],
                state=state,
            )
            session.add(row)
        else:
            row.schema_version = state["schema_version"]
            row.revision = state["revision"]
            row.state = state
        if (
            owner
            and not session.query(WorldMember)
            .filter_by(world_id=world_id, user_id=owner.id)
            .first()
        ):
            session.add(
                WorldMember(id=new_id("member"), world_id=world_id, user_id=owner.id, role="owner")
            )

    saves = 0
    for slot_dir in sorted((world_dir / "saves").glob("slot_*")):
        try:
            messages = json.loads((slot_dir / "messages.json").read_text(encoding="utf-8"))
            snapshot = read_object(slot_dir / "snapshot.json")
            meta = read_object(slot_dir / "meta.json") if (slot_dir / "meta.json").is_file() else {}
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        with session_scope(db_url) as session:
            row = (
                session.query(SaveSlot)
                .filter_by(world_id=world_id, slot_key=slot_dir.name)
                .one_or_none()
            )
            if row is None:
                row = SaveSlot(
                    id=new_id("save"),
                    world_id=world_id,
                    slot_key=slot_dir.name,
                    kind="auto" if slot_dir.name == "slot_000" else "manual",
                    snapshot_id="pending",
                )
                session.add(row)
            snapshot_row = Snapshot(
                id=new_id("snapshot"),
                world_id=world_id,
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
        saves += 1

    turns = 0
    legacy = TurnJournal(world_dir, world_id=world_id, module_name=module_name)
    for public in legacy.list_completed(limit=1_000_000):
        turn_id = str(public["turn_id"])
        record = legacy.read(turn_id)
        messages, snapshot = legacy.load_artifacts(turn_id)
        with session_scope(db_url) as session:
            if session.query(Turn).filter_by(world_id=world_id, id=turn_id).first():
                continue
            snapshot_row = Snapshot(
                id=new_id("snapshot"),
                world_id=world_id,
                source_turn_id=turn_id,
                kind="legacy_turn",
                revision=int(snapshot.get("revision", 0)),
                state=snapshot,
            )
            session.add(snapshot_row)
            row = Turn(
                pk=new_id("turnrow"),
                id=turn_id,
                world_id=world_id,
                parent_turn_id=record.get("parent_turn_id"),
                origin_world_id=record.get("origin_world_id"),
                kind=record.get("kind", "action"),
                status="completed",
                owner_token=record.get("owner_token", ""),
                player_input=record.get("player_input"),
                record=record,
                messages=messages,
                snapshot_id=snapshot_row.id,
                completed_at=utcnow(),
            )
            session.add(row)
            session.flush()
            for sequence, event in enumerate(record.get("events", [])):
                if isinstance(event, dict):
                    session.add(
                        TurnEvent(
                            id=new_id("event"),
                            turn_pk=row.pk,
                            turn_id=row.id,
                            sequence=sequence,
                            event_type=str(event.get("type") or "unknown"),
                            payload=event,
                        )
                    )
        turns += 1

    note_path = world_dir / "player_notes.json"
    if note_path.is_file():
        note = read_object(note_path)
        with session_scope(db_url) as session:
            row = (
                session.query(PlayerNote)
                .filter_by(world_id=world_id, owner_key="__local__")
                .one_or_none()
            )
            if row is None:
                row = PlayerNote(
                    id=new_id("note"),
                    world_id=world_id,
                    user_id=None,
                    owner_key="__local__",
                )
                session.add(row)
            row.revision = int(note.get("revision", 0))
            row.text = str(note.get("text") or "")
    return {
        "world_id": world_id,
        "status": "imported",
        "saves": saves,
        "turns": turns,
        "notes_schema": PLAYER_NOTES_SCHEMA_VERSION,
    }


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
                print(json.dumps({"status": "already_imported"}, ensure_ascii=False))
                return 0
    owner = None
    if args.owner:
        with session_scope(db_url) as session:
            owner = session.query(User).filter_by(username=args.owner.lower()).one_or_none()
            if owner is None:
                raise SystemExit(f"owner 不存在: {args.owner}")
    results = []
    for world_dir in sorted(
        (args.runtime_root / "worlds").iterdir() if (args.runtime_root / "worlds").is_dir() else []
    ):
        if (
            world_dir.is_dir()
            and (world_dir / "world.json").is_file()
            and (world_dir / "world_state.json").is_file()
        ):
            results.append(import_world(world_dir, db_url, owner, replace=args.replace))
    if args.once:
        with session_scope(db_url) as session:
            session.add(
                AuditEvent(
                    id=new_id("audit"),
                    event_type="legacy_import_completed",
                    success=True,
                    details={"world_count": len(results)},
                )
            )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
