"""Create and discover independent world timelines from committed turns."""

from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func

from .config import AUTO_SAVE_SLOT
from .context_checkpoint import public_copy
from .database import SaveSlot, Turn, World, WorldState, database_url, session_scope, utcnow
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

    def _new_root_world_id(self, module_name: str) -> str:
        stem = re.sub(r"[^\w-]+", "-", module_name, flags=re.UNICODE).strip("-_")
        stem = (stem or "world")[:48]
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        while True:
            world_id = f"local-{stem}-{stamp}-{secrets.token_hex(2)}"
            if not (self.worlds_dir / world_id).exists():
                return world_id

    def create_root(self, module_name: str) -> RuntimeContext:
        """Create a brand-new root world: 一次新游戏 = 一个独立存档位。

        “开始新游戏”不再 reset 模组默认世界；每次都产生一棵新的世界树，
        旧存档位保持可玩。模组名拼进 world_id 便于排查，随机后缀保证唯一。
        """
        return RuntimeContext.create(
            self._new_root_world_id(module_name),
            module_name,
            project_root=self.project_root,
            runtime_root=self.runtime_root,
        )

    def is_tree_untouched(self, world_id: str) -> bool:
        """Whether the world's tree has never been played (no turns, no saves).

        连接初始化与 switch_module 会先打开（甚至创建）模组默认世界；玩家
        取消开局时不应留下可见的空存档位，下一次“开始新游戏”也可以直接
        复用这样的世界，而不是再建一棵新的空树。
        """
        with session_scope(database_url(self.runtime_root)) as session:
            if session.query(Turn.id).filter_by(world_id=world_id).first() is not None:
                return False
            if (
                session.query(SaveSlot).filter_by(world_id=world_id).first()
                is not None
            ):
                return False
        return not (
            self.worlds_dir / world_id / "saves" / AUTO_SAVE_SLOT / "messages.json"
        ).is_file()

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
            # An archived world deliberately keeps its historical rows and
            # compatibility exports, but it is no longer a runnable timeline.
            # Keep the observable behaviour equivalent to a missing world so
            # callers cannot revive it merely by rebuilding a RuntimeContext.
            if world is None or world.status != "active":
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

    def archive_branch(self, world_id: str, *, active_world_id: str) -> dict[str, str]:
        """Logically archive one inactive timeline branch.

        Timeline history is intentionally retained: only the control-plane
        status changes.  The caller owns the in-process target-world lock;
        this transaction adds a durable World-row fence and refuses a running
        database turn, which also covers another local backend process.
        """
        if not world_id or Path(world_id).name != world_id or world_id in {".", ".."}:
            raise ValueError("非法 world_id")
        if not active_world_id:
            raise ValueError("缺少当前 world_id")

        with session_scope(database_url(self.runtime_root)) as session:
            world = (
                session.query(World)
                .filter_by(id=world_id)
                .with_for_update()
                .one_or_none()
            )
            if world is None or world.status != "active":
                raise FileNotFoundError(f"时间线不存在或已删除: {world_id}")

            metadata = dict(world.metadata_json or {})
            branch = metadata.get("branch")
            if not isinstance(branch, dict):
                raise ValueError("主时间线不能删除")
            if world_id == active_world_id:
                raise ValueError("不能删除当前正在使用的时间线")

            active_turn = (
                session.query(Turn.id)
                .filter_by(world_id=world_id, status="active")
                .first()
            )
            if active_turn is not None:
                raise RuntimeError("目标时间线正在处理另一个回合，请稍后重试。")

            # Deleting an ancestor while leaving its active children behind
            # would hide those children from the branch-tree traversal.  Make
            # the operation explicit rather than silently orphaning history.
            for candidate in (
                session.query(World).filter_by(module_name=world.module_name, status="active").all()
            ):
                candidate_branch = dict(candidate.metadata_json or {}).get("branch")
                if (
                    isinstance(candidate_branch, dict)
                    and str(candidate_branch.get("parent_world_id") or "") == world_id
                ):
                    raise ValueError("该时间线仍有子分支，请先删除子分支")

            world.status = "archived"
            world.updated_at = utcnow()
            return {
                "world_id": world_id,
                # The active timeline is guaranteed to remain available: the
                # service rejects attempts to archive it above.  Returning it
                # makes a stale client selection converge safely.
                "fallback_world_id": active_world_id,
            }

    def archive_tree(self, root_world_id: str, *, active_world_id: str) -> dict:
        """Logically archive a whole save slot: root timeline plus all branches.

        删除存档位是高风险操作，但仍是逻辑删除：回合、存档与兼容导出全部
        保留，仅控制面 status 变化，误删后可恢复。当前正在游玩的存档位不可
        删除（调用方需先切换到其他世界）；树内任何世界有进行中回合时拒绝。
        """
        if not root_world_id or Path(root_world_id).name != root_world_id:
            raise ValueError("非法 root_world_id")

        with session_scope(database_url(self.runtime_root)) as session:
            root = (
                session.query(World)
                .filter_by(id=root_world_id)
                .with_for_update()
                .one_or_none()
            )
            if root is None or root.status != "active":
                raise FileNotFoundError(f"存档不存在或已删除: {root_world_id}")
            root_metadata = dict(root.metadata_json or {})
            if isinstance(root_metadata.get("branch"), dict):
                raise ValueError("这是一条时间线分支，请使用时间线删除")

            candidates = (
                session.query(World)
                .filter_by(module_name=root.module_name, status="active")
                .with_for_update()
                .all()
            )
            by_id = {world.id: world for world in candidates}

            def parent_id(world_id: str) -> str | None:
                item = by_id.get(world_id)
                if item is None:
                    return None
                branch = dict(item.metadata_json or {}).get("branch")
                if not isinstance(branch, dict):
                    return None
                parent = str(branch.get("parent_world_id") or "").strip()
                return parent if parent in by_id else None

            def root_of(world_id: str) -> str:
                root_id = world_id
                visited: set[str] = set()
                while root_id not in visited:
                    visited.add(root_id)
                    parent = parent_id(root_id)
                    if parent is None:
                        break
                    root_id = parent
                return root_id

            members = [
                world for world in candidates if root_of(world.id) == root_world_id
            ]
            member_ids = [world.id for world in members]
            if active_world_id and active_world_id in member_ids:
                raise ValueError("不能删除当前正在游玩的存档")

            busy_turn = (
                session.query(Turn.world_id)
                .filter(Turn.world_id.in_(member_ids), Turn.status == "active")
                .first()
            )
            if busy_turn is not None:
                raise RuntimeError("该存档正在处理回合，请稍后重试。")

            for world in members:
                world.status = "archived"
                world.updated_at = utcnow()
            return {
                "root_world_id": root_world_id,
                "archived_world_ids": member_ids,
                "count": len(member_ids),
            }

    def rename_slot(self, root_world_id: str, label: object) -> dict[str, str]:
        """Rename a save slot's custom display name (metadata only).

        存档位名是根世界 metadata 里的 ``slot_name``，与时间线显示名
        （``display_name``）相互独立；空标签清除自定义名，UI 回退为模组名。
        """
        if not root_world_id or Path(root_world_id).name != root_world_id:
            raise ValueError("非法 root_world_id")
        with session_scope(database_url(self.runtime_root)) as session:
            world = (
                session.query(World)
                .filter_by(id=root_world_id)
                .with_for_update()
                .one_or_none()
            )
            if world is None or world.status != "active":
                raise FileNotFoundError(f"存档不存在或已删除: {root_world_id}")
            metadata = dict(world.metadata_json or {})
            if isinstance(metadata.get("branch"), dict):
                raise ValueError("这是一条时间线分支，请使用时间线重命名")
            cleaned = re.sub(r"\s+", " ", str(label or "")).strip()[:60]
            if cleaned:
                metadata["slot_name"] = cleaned
            else:
                metadata.pop("slot_name", None)
            world.metadata_json = metadata
            world.updated_at = utcnow()
        if os.environ.get("TRPG_WRITE_COMPAT_EXPORTS", "1").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            atomic_write_json(self.worlds_dir / root_world_id / "world.json", metadata)
        return {"root_world_id": root_world_id, "slot_name": cleaned}

    def rename_branch(self, world_id: str, label: object) -> dict[str, str]:
        """Rename one active timeline (display name only; history is kept)."""
        if not world_id or Path(world_id).name != world_id or world_id in {".", ".."}:
            raise ValueError("非法 world_id")
        with session_scope(database_url(self.runtime_root)) as session:
            world = (
                session.query(World)
                .filter_by(id=world_id)
                .with_for_update()
                .one_or_none()
            )
            if world is None or world.status != "active":
                raise FileNotFoundError(f"时间线不存在或已删除: {world_id}")
            metadata = dict(world.metadata_json or {})
            fallback = (
                "时间线分支" if isinstance(metadata.get("branch"), dict) else "主时间线"
            )
            cleaned = self._clean_label(label, fallback)
            metadata["display_name"] = cleaned
            world.metadata_json = metadata
            world.updated_at = utcnow()
        if os.environ.get("TRPG_WRITE_COMPAT_EXPORTS", "1").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            atomic_write_json(self.worlds_dir / world_id / "world.json", metadata)
        return {"world_id": world_id, "label": cleaned}

    def has_compatible_auto_save(self, world_id: str) -> bool:
        """Whether ``slot_000`` can be resumed, including legacy file saves.

        Database rows are the normal authority.  A pre-migration desktop
        world may still have only ``saves/slot_000/messages.json``; exposing
        it as resumable lets the existing ``load_game`` compatibility importer
        migrate it when the user actually opens that timeline.
        """
        with session_scope(database_url(self.runtime_root)) as session:
            save = (
                session.query(SaveSlot)
                .filter_by(world_id=world_id, slot_key=AUTO_SAVE_SLOT)
                .one_or_none()
            )
            if save is not None:
                return True
        return (self.worlds_dir / world_id / "saves" / AUTO_SAVE_SLOT / "messages.json").is_file()

    def _tree_world_records(
        self, module_name: str, active_world_id: str
    ) -> list[dict]:
        """(world_id, metadata, state) records of the active world's branch tree.

        A module can have many unrelated worlds (old local games, regression
        worlds, or a separate private adventure).  They are not all branches
        of the world the player is currently viewing.  Find that world's
        root, then expose only its own branch tree.
        """
        with session_scope(database_url(self.runtime_root)) as session:
            rows = (
                session.query(World, WorldState)
                .join(WorldState)
                .filter(World.module_name == module_name, World.status == "active")
                .all()
            )
            rows_by_id = {world.id: (world, state_row) for world, state_row in rows}
            if active_world_id not in rows_by_id:
                return []

            def parent_id(world_id: str) -> str | None:
                item = rows_by_id.get(world_id)
                if item is None:
                    return None
                metadata = dict(item[0].metadata_json or {})
                branch = metadata.get("branch")
                if not isinstance(branch, dict):
                    return None
                parent = str(branch.get("parent_world_id") or "").strip()
                return parent if parent in rows_by_id else None

            root_world_id = active_world_id
            visited: set[str] = set()
            while root_world_id not in visited:
                visited.add(root_world_id)
                parent = parent_id(root_world_id)
                if parent is None:
                    break
                root_world_id = parent

            def belongs_to_current_timeline(world_id: str) -> bool:
                if world_id == root_world_id:
                    return True
                visited: set[str] = set()
                current = world_id
                while current not in visited:
                    visited.add(current)
                    parent = parent_id(current)
                    if parent is None:
                        return False
                    if parent == root_world_id:
                        return True
                    current = parent
                return False

            return [
                {
                    "world_id": world.id,
                    "metadata": dict(world.metadata_json or {}),
                    "state": dict(state_row.state or {}),
                }
                for world, state_row in rows
                if belongs_to_current_timeline(world.id)
            ]

    def list_tree_saves(
        self, module_name: str, *, active_world_id: str
    ) -> list[dict]:
        """Saves across the whole branch tree, each tagged with its timeline.

        The save panel browses per save: picking a save shows the timeline it
        belongs to, so the save list must span the tree instead of only the
        active world.  Every entry carries ``world_id``/``timeline_label``/
        ``world_active``; loading or renaming a save of another timeline is
        still only possible after switching to that timeline.
        """
        records = self._tree_world_records(module_name, active_world_id)
        if not any(record["world_id"] == active_world_id for record in records):
            # 极旧数据可能缺 World/WorldState 行；此时树遍历为空，但当前世界
            # 的存档必须仍然可见——回退为仅列出当前世界的存档。
            records = [{"world_id": active_world_id, "metadata": {}, "state": {}}]
        saves: list[dict] = []
        with session_scope(database_url(self.runtime_root)) as session:
            for record in records:
                world_id = record["world_id"]
                label = record["metadata"].get("display_name") or "主时间线"
                slots = (
                    session.query(SaveSlot).filter_by(world_id=world_id).all()
                )
                for slot in slots:
                    meta = public_copy(slot.metadata_json or {})
                    meta["id"] = slot.slot_key
                    meta["world_id"] = world_id
                    meta["timeline_label"] = label
                    meta["world_active"] = world_id == active_world_id
                    saves.append(meta)
        saves.sort(key=lambda m: m.get("created_at", ""), reverse=True)
        return saves

    def list_worlds(self, module_name: str, *, active_world_id: str) -> list[dict]:
        worlds: list[dict] = []
        records = self._tree_world_records(module_name, active_world_id)
        with session_scope(database_url(self.runtime_root)) as session:
            for record in records:
                world_id = record["world_id"]
                metadata = record["metadata"]
                state = record["state"]
                save = (
                    session.query(SaveSlot)
                    .filter_by(world_id=world_id, slot_key=AUTO_SAVE_SLOT)
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
                        "world_id": world_id,
                        "label": metadata.get("display_name") or "主时间线",
                        "module_name": module_name,
                        "active": world_id == active_world_id,
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
                        # ``load_game`` imports a legacy file slot on demand,
                        # so old desktop timelines remain selectable while a
                        # genuinely empty branch remains visibly non-resumable.
                        "resumable": save is not None
                        or (
                            self.worlds_dir
                            / world_id
                            / "saves"
                            / AUTO_SAVE_SLOT
                            / "messages.json"
                        ).is_file(),
                    }
                )
        worlds.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        worlds.sort(key=lambda item: not item["active"])
        return worlds

    def list_adventures(
        self,
        *,
        active_world_id: str,
        module_name: str | None = None,
        allowed_world_ids: set[str] | None = None,
    ) -> list[dict]:
        """One save slot per playthrough tree: root world plus its timelines.

        存档位（Save Slot）是玩家可见的最外层概念：一次游玩 = 一棵世界树
        （根 + 全部分支），时间线只是存档位内部的高级分支。从未开始过的树
        （无回合也无存档，例如 switch_module 打开过的模组默认世界）不算
        存档位，直接隐藏。存档位按根世界创建时间编号（slot_index 从 1
        开始），与最近游玩顺序无关，模拟传统 RPG 的 SAVE 01/02/03。

        传入 ``module_name`` 时只列出该模组的存档位：Load 页跟随开局页的
        模组选择器，避免其他模组的存档混进当前模组的列表（编号也在模组内
        顺延）。
        """
        with session_scope(database_url(self.runtime_root)) as session:
            rows = (
                session.query(World, WorldState)
                .join(WorldState)
                .filter(World.status == "active")
                .all()
            )
            rows_by_id = {
                world.id: (world, state_row)
                for world, state_row in rows
                if (module_name is None or world.module_name == module_name)
                and (allowed_world_ids is None or world.id in allowed_world_ids)
            }
            if not rows_by_id:
                return []

            def parent_id(world_id: str) -> str | None:
                item = rows_by_id.get(world_id)
                if item is None:
                    return None
                branch = dict(item[0].metadata_json or {}).get("branch")
                if not isinstance(branch, dict):
                    return None
                parent = str(branch.get("parent_world_id") or "").strip()
                return parent if parent in rows_by_id else None

            def root_of(world_id: str) -> str:
                root = world_id
                visited: set[str] = set()
                while root not in visited:
                    visited.add(root)
                    parent = parent_id(root)
                    if parent is None:
                        break
                    root = parent
                return root

            groups: dict[str, list[str]] = {}
            for world_id in rows_by_id:
                groups.setdefault(root_of(world_id), []).append(world_id)

            world_ids = list(rows_by_id)
            save_rows = (
                session.query(SaveSlot)
                .filter(SaveSlot.world_id.in_(world_ids))
                .all()
            )
            played_world_ids = {
                row[0]
                for row in (
                    session.query(Turn.world_id)
                    .filter(Turn.world_id.in_(world_ids))
                    .distinct()
                )
            }
            completed_turn_rows = (
                session.query(Turn.world_id, func.count(Turn.id))
                .filter(Turn.world_id.in_(world_ids), Turn.status == "completed")
                .group_by(Turn.world_id)
                .all()
            )
        saves_by_world: dict[str, list[SaveSlot]] = {}
        for save in save_rows:
            saves_by_world.setdefault(save.world_id, []).append(save)
        completed_turn_counts = {
            world_id: count for world_id, count in completed_turn_rows
        }

        def depth_of(world_id: str) -> int:
            depth = 0
            current = world_id
            visited: set[str] = set()
            while current not in visited:
                visited.add(current)
                parent = parent_id(current)
                if parent is None:
                    return depth
                depth += 1
                current = parent
            return depth

        def timeline_entry(world_id: str) -> dict:
            world, state_row = rows_by_id[world_id]
            metadata = dict(world.metadata_json or {})
            branch = metadata.get("branch") if isinstance(metadata.get("branch"), dict) else {}
            state = dict(state_row.state or {})
            scene = state.get("current_scene") if isinstance(state.get("current_scene"), dict) else {}
            pc = state.get("pc") if isinstance(state.get("pc"), dict) else {}
            slots = saves_by_world.get(world_id, [])
            auto_save = next((s for s in slots if s.slot_key == AUTO_SAVE_SLOT), None)
            auto_meta = dict(auto_save.metadata_json or {}) if auto_save else {}
            updated_at = max(
                [str(dict(s.metadata_json or {}).get("created_at") or "") for s in slots]
                + [str(branch.get("created_at") or metadata.get("created_at") or "")]
            )
            return {
                "world_id": world_id,
                "label": metadata.get("display_name")
                or ("时间线分支" if branch else "主时间线"),
                "is_branch": bool(branch),
                "parent_world_id": branch.get("parent_world_id"),
                "depth": depth_of(world_id),
                "active": world_id == active_world_id,
                "resumable": auto_save is not None
                or (
                    self.worlds_dir / world_id / "saves" / AUTO_SAVE_SLOT / "messages.json"
                ).is_file(),
                "scene_name": scene.get("name") or auto_meta.get("scene_name") or "未知场景",
                "character_name": pc.get("name")
                or auto_meta.get("character_name")
                or "未知调查员",
                "updated_at": updated_at,
                "save_count": sum(1 for s in slots if s.slot_key != AUTO_SAVE_SLOT),
            }

        adventures: list[dict] = []
        for root_id, member_ids in groups.items():
            # 从未开始过的树（无回合、无存档）不是存档位：来自连接初始化或
            # switch_module 打开的空默认世界，直接隐藏，避免垃圾存档位。
            played = any(
                world_id in played_world_ids or saves_by_world.get(world_id)
                for world_id in member_ids
            )
            if not played:
                played = any(
                    (
                        self.worlds_dir
                        / world_id
                        / "saves"
                        / AUTO_SAVE_SLOT
                        / "messages.json"
                    ).is_file()
                    for world_id in member_ids
                )
            if not played:
                continue
            timelines = [timeline_entry(world_id) for world_id in member_ids]
            # 根在前，分支按创建时间深度优先排列。
            timelines.sort(key=lambda item: str(item.get("updated_at") or ""))
            timelines.sort(key=lambda item: item["depth"])
            timelines.sort(key=lambda item: item["is_branch"])
            root_world = rows_by_id.get(root_id)
            module_name = root_world[0].module_name if root_world else ""
            root_metadata = dict(root_world[0].metadata_json or {}) if root_world else {}
            active = any(item["active"] for item in timelines)
            resumable = [item for item in timelines if item["resumable"]]
            resume = next((item for item in timelines if item["active"] and item["resumable"]), None)
            if resume is None and resumable:
                resume = max(resumable, key=lambda item: str(item.get("updated_at") or ""))
            latest = max(timelines, key=lambda item: str(item.get("updated_at") or ""))
            resume_world_id = resume["world_id"] if resume else ""
            adventures.append(
                {
                    "root_world_id": root_id,
                    "module_name": module_name,
                    "slot_name": str(root_metadata.get("slot_name") or ""),
                    "created_at": str(root_metadata.get("created_at") or ""),
                    "active": active,
                    "character_name": latest["character_name"],
                    "scene_name": latest["scene_name"],
                    "updated_at": latest["updated_at"],
                    "timeline_count": len(timelines),
                    "resume_world_id": resume_world_id,
                    "turn_count": completed_turn_counts.get(resume_world_id, 0),
                    "timelines": timelines,
                }
            )
        # 存档位按创建顺序编号：SAVE 01 是最早的一次游玩，删除中间存档位后
        # 后续编号顺延（与传统 RPG 动态存档位一致）。
        adventures.sort(key=lambda item: item["created_at"])
        for index, adventure in enumerate(adventures, start=1):
            adventure["slot_index"] = index
        return adventures
