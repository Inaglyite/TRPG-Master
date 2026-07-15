"""Create and discover independent world timelines from committed turns."""

from __future__ import annotations

import json
import re
import secrets
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .persistence import save_game
from .player_notes import PlayerNotesStore
from .runtime import RuntimeContext
from .turn_journal import TurnJournal
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
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
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
            source_notes = PlayerNotesStore(source_context.world_dir).load()
            if source_notes.get("text"):
                PlayerNotesStore(target_context.world_dir).save(source_notes["text"])

            metadata = json.loads(target_context.metadata_file.read_text(encoding="utf-8"))
            metadata.update({
                "display_name": branch_label,
                "branch": {
                    "parent_world_id": source_context.world_id,
                    "source_turn_id": turn_id,
                    "source_world_revision": record.get("world_revision"),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            })
            atomic_write_json(target_context.metadata_file, metadata)
            return WorldBranch(target_context, messages, turn_id, branch_label)
        except Exception:
            if target_context is not None:
                shutil.rmtree(target_context.world_dir, ignore_errors=True)
            raise

    def open(self, world_id: str) -> RuntimeContext:
        if not world_id or Path(world_id).name != world_id or world_id in {".", ".."}:
            raise ValueError("非法 world_id")
        world_dir = (self.worlds_dir / world_id).resolve()
        if not world_dir.is_relative_to(self.worlds_dir.resolve()):
            raise ValueError("非法 world_id")
        metadata_file = world_dir / "world.json"
        if not metadata_file.is_file():
            raise FileNotFoundError(f"世界不存在: {world_id}")
        metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
        module_name = str(metadata.get("module_name") or "")
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
        if not self.worlds_dir.is_dir():
            return worlds
        for world_dir in self.worlds_dir.iterdir():
            metadata_file = world_dir / "world.json"
            if not world_dir.is_dir() or not metadata_file.is_file():
                continue
            try:
                metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
                if metadata.get("module_name") != module_name:
                    continue
                state = json.loads(
                    (world_dir / "world_state.json").read_text(encoding="utf-8")
                )
                save_meta_file = world_dir / "saves" / "slot_000" / "meta.json"
                save_meta = (
                    json.loads(save_meta_file.read_text(encoding="utf-8"))
                    if save_meta_file.is_file()
                    else {}
                )
            except (OSError, json.JSONDecodeError, TypeError):
                continue
            branch = metadata.get("branch") if isinstance(metadata.get("branch"), dict) else {}
            scene = state.get("current_scene") if isinstance(state.get("current_scene"), dict) else {}
            pc = state.get("pc") if isinstance(state.get("pc"), dict) else {}
            worlds.append({
                "world_id": world_dir.name,
                "label": metadata.get("display_name") or "主时间线",
                "module_name": module_name,
                "active": world_dir.name == active_world_id,
                "is_branch": bool(branch),
                "parent_world_id": branch.get("parent_world_id"),
                "source_turn_id": branch.get("source_turn_id"),
                "created_at": branch.get("created_at") or metadata.get("created_at"),
                "updated_at": save_meta.get("created_at") or metadata.get("created_at"),
                "scene_name": scene.get("name") or save_meta.get("scene_name") or "未知场景",
                "character_name": pc.get("name") or save_meta.get("character_name") or "未知调查员",
            })
        worlds.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        worlds.sort(key=lambda item: not item["active"])
        return worlds
