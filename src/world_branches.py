"""Create and discover independent world timelines from committed turns."""

from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .database import SaveSlot, World, WorldState, database_url, session_scope, utcnow
from .database_turn_journal import DatabaseTurnJournal as TurnJournal
from .persistence import save_game
from .player_notes import PlayerNotesStore
from .runtime import RuntimeContext
from .world_store import atomic_write_json


@dataclass(frozen=True)
class WorldBranch:
    context: RuntimeContext
    messages: list[dict]
    source_turn_id: str
    label: str


class WorldBranchService:
    def __init__(self, project_root: Path, runtime_root: Path) -> None:
        self.project_root = Path(project_root).resolve()
        self.runtime_root = Path(runtime_root).resolve()
        self.worlds_dir = self.runtime_root / "worlds"

    @staticmethod
    def _clean_label(label: object, fallback: str) -> str:
        cleaned = re.sub(r"\s+", " ", str(label or "")).strip()
        return (cleaned or fallback)[:60]

    def _new_world_id(self, parent_world_id: str) -> str:
        stem = re.sub(r"[^\w-]+", "-", parent_world_id, flags=re.UNICODE).strip("-_")
        stem = (stem or "world")[:48]
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        while True:
            world_id = f"{stem}-branch-{stamp}-{secrets.token_hex(2)}"
            if not (self.worlds_dir / world_id).exists():
                return world_id

    def create(
        self,
        source_context: RuntimeContext,
        source_journal: TurnJournal,
        turn_id: str,
        *,
        label: object = "",
        user_id: str | None = None,
    ) -> WorldBranch:
        record = source_journal.read(turn_id)
        if record.get("status") != "completed":
            raise ValueError("只能从完整提交的回合创建分支")
        messages, snapshot = source_journal.load_artifacts(turn_id)
        scene = snapshot.get("current_scene", {})
        scene_name = scene.get("name") if isinstance(scene, dict) else ""
        fallback_label = f"分支 · {scene_name or '新的时间线'}"
        branch_label = self._clean_label(label, fallback_label)
        world_id = self._new_world_id(source_context.world_id)
        target_context: RuntimeContext | None = None

        try:
            target_context = RuntimeContext.create(
                world_id,
                source_context.module_name,
                project_root=self.project_root,
                runtime_root=self.runtime_root,
            )
            target_context.world_store.seed_from_snapshot(
                snapshot,
                expected_revision=target_context.world_store.revision,
            )
            target_journal = TurnJournal(
                target_context.world_dir,
                world_id=world_id,
                module_name=source_context.module_name,
            )
            source_journal.clone_lineage_to(target_journal, turn_id)
            save_game(messages, "slot_000", context=target_context)
            source_notes = PlayerNotesStore(source_context.world_dir, user_id=user_id).load()
            if source_notes.get("text"):
                PlayerNotesStore(target_context.world_dir, user_id=user_id).save(
                    source_notes["text"]
                )

            with session_scope(target_context.database_url) as session:
                world = session.get(World, world_id)
                metadata = dict(world.metadata_json or {})
                metadata.update(
                    {
                        "display_name": branch_label,
                        "branch": {
                            "parent_world_id": source_context.world_id,
                            "source_turn_id": turn_id,
                            "source_world_revision": record.get("world_revision"),
                            "created_at": datetime.now(UTC).isoformat(),
                        },
                    }
                )
                world.metadata_json = metadata
                world.updated_at = utcnow()
            if os.environ.get("TRPG_WRITE_COMPAT_EXPORTS", "1").lower() in {
                "1",
                "true",
                "yes",
                "on",
            }:
                atomic_write_json(target_context.metadata_file, metadata)
            return WorldBranch(target_context, messages, turn_id, branch_label)
        except Exception:
            if target_context is not None:
                with session_scope(target_context.database_url) as session:
                    world = session.get(World, target_context.world_id)
                    if world is not None:
                        session.delete(world)
            raise

    def open(self, world_id: str) -> RuntimeContext:
        if not world_id or Path(world_id).name != world_id or world_id in {".", ".."}:
            raise ValueError("非法 world_id")
        with session_scope(database_url(self.runtime_root)) as session:
            world = session.get(World, world_id)
            if world is None:
                raise FileNotFoundError(f"世界不存在: {world_id}")
            module_name = world.module_name
        if not module_name:
            raise ValueError(f"世界 {world_id} 缺少 module_name")
        return RuntimeContext.create(
            world_id,
            module_name,
            project_root=self.project_root,
            runtime_root=self.runtime_root,
        )

    def list_worlds(self, module_name: str, *, active_world_id: str) -> list[dict]:
        worlds: list[dict] = []
        with session_scope(database_url(self.runtime_root)) as session:
            rows = (
                session.query(World, WorldState)
                .join(WorldState)
                .filter(World.module_name == module_name, World.status == "active")
                .all()
            )
            for world, state_row in rows:
                metadata = dict(world.metadata_json or {})
                state = dict(state_row.state or {})
                save = (
                    session.query(SaveSlot)
                    .filter_by(world_id=world.id, slot_key="slot_000")
                    .one_or_none()
                )
                save_meta = dict(save.metadata_json or {}) if save else {}
                branch = metadata.get("branch") if isinstance(metadata.get("branch"), dict) else {}
                scene = (
                    state.get("current_scene")
                    if isinstance(state.get("current_scene"), dict)
                    else {}
                )
                pc = state.get("pc") if isinstance(state.get("pc"), dict) else {}
                worlds.append(
                    {
                        "world_id": world.id,
                        "label": metadata.get("display_name") or "主时间线",
                        "module_name": module_name,
                        "active": world.id == active_world_id,
                        "is_branch": bool(branch),
                        "parent_world_id": branch.get("parent_world_id"),
                        "source_turn_id": branch.get("source_turn_id"),
                        "created_at": branch.get("created_at") or metadata.get("created_at"),
                        "updated_at": save_meta.get("created_at") or metadata.get("created_at"),
                        "scene_name": scene.get("name")
                        or save_meta.get("scene_name")
                        or "未知场景",
                        "character_name": pc.get("name")
                        or save_meta.get("character_name")
                        or "未知调查员",
                    }
                )
        worlds.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        worlds.sort(key=lambda item: not item["active"])
        return worlds
