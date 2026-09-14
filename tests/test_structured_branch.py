"""M4 生命周期：structured_v1 世界的分支与存档恢复。

验收约束（主规格 §12 生命周期行）：
- 存档分支恢复待办并绑定新世界权限：分支从当前已提交状态分叉，
  复制控制面 metadata、成员（含 can_keeper）与调查员认领、非终态
  请求与待结算检定；不复制 outbox（游标从 0 重同步）与 keeper_control
  （分支以无人掌控开始）。
- 读档：CAS 回滚状态，同事务 reconcile——非终态请求 failed、pending
  检定做废、晚于存档点的 outbox 事件删除，不允许静默不一致。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sqlalchemy import select
from test_structured_commands import make_structured_world

from src.app.config import AUTO_SAVE_SLOT
from src.storage.database import (
    CheckRequest,
    EventOutbox,
    GameCommand,
    KeeperControl,
    PlayerRequest,
    SaveSlot,
    World,
    WorldInvestigator,
    WorldMember,
    session_scope,
)
from src.storage.persistence import save_game
from src.structured.branch import create_structured_branch, restore_structured_save
from src.structured.errors import StructuredError
from src.structured.principal import Principal, take_control


class StructuredBranchTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.context = make_structured_world(self.root)
        self.db_url = self.context.database_url
        self.keeper = Principal(kind="keeper", user_id="u-keeper")
        self.alice = Principal(kind="player", user_id="u-alice", investigator_ids=("inv-alice",))

    def tearDown(self):
        self._temp.cleanup()

    def _make_activity(self):
        """一条已提交命令 + 一个 queued 请求 + 一个 pending 检定 + 一个终态请求。"""
        from src.structured.service import StructuredPlayService

        service = StructuredPlayService(self.db_url)
        service.execute_command(
            world_id="sp-world",
            principal=self.keeper,
            kind="move_party",
            payload={"destination_scene_id": "library", "travel_minutes": 20},
            command_id="cmd-branch-move",
            expected_revision=None,
        )
        service.submit_action_request(
            world_id="sp-world",
            principal=self.alice,
            request={
                "request_id": "req-open",
                "investigator_id": "inv-alice",
                "action": {"kind": "freeform", "text": "我搜查书架"},
            },
        )
        check_result = service.execute_command(
            world_id="sp-world",
            principal=self.keeper,
            kind="request_check",
            payload={
                "investigator_id": "inv-alice",
                "skill": "侦查",
                "attempt": "搜查书架",
                "related_request_id": "req-open",
            },
            command_id="cmd-branch-check",
            expected_revision=None,
        )
        self.check_id = check_result["result"]["check_request_id"]
        # 终态请求：不应被复制到分支
        service.submit_action_request(
            world_id="sp-world",
            principal=self.alice,
            request={
                "request_id": "req-done",
                "investigator_id": "inv-alice",
                "action": {"kind": "freeform", "text": "我打个招呼"},
            },
        )
        service.execute_command(
            world_id="sp-world",
            principal=self.keeper,
            kind="resolve_intent",
            payload={"request_id": "req-done", "resolution": "completed"},
            command_id="cmd-branch-resolve",
            expected_revision=None,
        )
        return service

    def _branch(self, label="测试分支") -> str:
        branch = create_structured_branch(
            self.context,
            project_root=self.root,
            runtime_root=self.root,
            label=label,
            user_id="u-alice",
        )
        return branch.context.world_id

    def test_branch_copies_control_plane_state_and_permissions(self):
        self._make_activity()
        branch_id = self._branch()
        self.assertNotEqual("sp-world", branch_id)

        with session_scope(self.db_url) as session:
            world = session.get(World, branch_id)
            meta = world.metadata_json
            # 控制面继承：不会退化为 legacy
            self.assertEqual("structured_v1", meta["execution_profile"])
            self.assertEqual("human", meta["keeper_mode"])
            self.assertEqual("测试分支", meta["display_name"])
            self.assertEqual("sp-world", meta["branch"]["parent_world_id"])
            self.assertIsNone(meta["branch"]["source_turn_id"])
            self.assertEqual("sp-world", world.root_world_id)
            # 成员与 keeper 授权复制
            members = {
                m.user_id: m
                for m in session.execute(
                    select(WorldMember).where(WorldMember.world_id == branch_id)
                ).scalars()
            }
            self.assertTrue(members["u-keeper"].can_keeper)
            self.assertFalse(members["u-owner"].can_keeper)
            self.assertEqual("owner", members["u-owner"].role)
            # 认领行复制而非搬走
            branch_claims = (
                session.execute(
                    select(WorldInvestigator).where(WorldInvestigator.world_id == branch_id)
                )
                .scalars()
                .all()
            )
            source_claims = (
                session.execute(
                    select(WorldInvestigator).where(WorldInvestigator.world_id == "sp-world")
                )
                .scalars()
                .all()
            )
            self.assertEqual(len(source_claims), len(branch_claims))
            self.assertEqual({"inv-alice", "inv-bob"}, {c.character_key for c in branch_claims})
            # 待办复制：请求（request_check 已把它置为 awaiting_player）与
            # pending 检定；终态请求不跨界
            requests = {
                r.request_id: r
                for r in session.execute(
                    select(PlayerRequest).where(PlayerRequest.world_id == branch_id)
                ).scalars()
            }
            self.assertEqual({"req-open"}, set(requests))
            self.assertEqual("awaiting_player", requests["req-open"].status)
            checks = (
                session.execute(select(CheckRequest).where(CheckRequest.world_id == branch_id))
                .scalars()
                .all()
            )
            self.assertEqual([self.check_id], [c.check_request_id for c in checks])
            self.assertEqual("pending", checks[0].status)
            # 幂等账本服随
            command_ids = {
                c.command_id
                for c in session.execute(
                    select(GameCommand).where(GameCommand.world_id == branch_id)
                ).scalars()
            }
            self.assertIn("cmd-branch-move", command_ids)
            # outbox 不复制：游标从 0 开始
            events = (
                session.execute(select(EventOutbox).where(EventOutbox.world_id == branch_id))
                .scalars()
                .all()
            )
            self.assertEqual([], events)
            # keeper_control 不复制（懒建 none；旧 agent epoch 不跨界）
            control = session.get(KeeperControl, branch_id)
            self.assertTrue(control is None or control.controller_kind == "none")
            # slot_000 存在 → 列表可见可续
            save = session.execute(
                select(SaveSlot).where(
                    SaveSlot.world_id == branch_id, SaveSlot.slot_key == AUTO_SAVE_SLOT
                )
            ).scalar_one_or_none()
            self.assertIsNotNone(save)

        # 状态与 revision 保留
        from src.storage.database_store import DatabaseWorldStore

        snapshot = DatabaseWorldStore(
            self.db_url, branch_id, self.root / "worlds" / branch_id
        ).snapshot()
        self.assertEqual("library", snapshot.state["current_scene"]["id"])
        source_snapshot = DatabaseWorldStore(
            self.db_url, "sp-world", self.context.world_dir
        ).snapshot()
        self.assertEqual(source_snapshot.revision, snapshot.revision)
        # 分支内同 command_id 重发：幂等返回原结果，不重复结算
        from src.structured.service import StructuredPlayService

        result = StructuredPlayService(self.db_url).execute_command(
            world_id=branch_id,
            principal=self.keeper,
            kind="move_party",
            payload={"destination_scene_id": "library", "travel_minutes": 20},
            command_id="cmd-branch-move",
            expected_revision=None,
        )
        self.assertEqual("library", result["result"]["scene_id"])
        self.assertTrue(result.get("deduplicated"))
        after = DatabaseWorldStore(
            self.db_url, branch_id, self.root / "worlds" / branch_id
        ).snapshot()
        self.assertEqual(snapshot.revision, after.revision)

    def test_branch_rejects_legacy_source(self):
        from src.app.runtime import RuntimeContext

        legacy = RuntimeContext.create(
            "legacy-world",
            "test-module",
            project_root=self.root,
            runtime_root=self.root,
        )
        with self.assertRaises(StructuredError) as raised:
            create_structured_branch(legacy, project_root=self.root, runtime_root=self.root)
        self.assertEqual("profile_mismatch", raised.exception.code)

    def test_restore_rolls_back_state_and_reconciles_tables(self):
        service = self._make_activity()
        # 存档点：当前进度（图书馆，req-open 排队，chk-open 待结算）
        save_game([], "slot_001", context=self.context)
        # 存档后继续推进：先结算检定（仍在图书馆，条件未变）再移动回书房 + 新请求
        service.submit_check_response(
            world_id="sp-world",
            principal=self.alice,
            request={
                "request_id": "req-roll-1",
                "check_request_id": self.check_id,
                "decision": "roll",
            },
        )
        service.execute_command(
            world_id="sp-world",
            principal=self.keeper,
            kind="move_party",
            payload={"destination_scene_id": "study", "travel_minutes": 5},
            command_id="cmd-after-save",
            expected_revision=None,
        )
        service.submit_action_request(
            world_id="sp-world",
            principal=self.alice,
            request={
                "request_id": "req-late",
                "investigator_id": "inv-alice",
                "action": {"kind": "freeform", "text": "晚期请求"},
            },
        )
        with session_scope(self.db_url) as session:
            late_events = (
                session.execute(select(EventOutbox).where(EventOutbox.world_id == "sp-world"))
                .scalars()
                .all()
            )
            self.assertTrue(late_events)

        result = restore_structured_save(self.context, "slot_001")

        from src.storage.database_store import DatabaseWorldStore

        snapshot = DatabaseWorldStore(self.db_url, "sp-world", self.context.world_dir).snapshot()
        self.assertEqual("library", snapshot.state["current_scene"]["id"])
        self.assertEqual(result["revision"], snapshot.revision)
        with session_scope(self.db_url) as session:
            # 读档后所有非终态请求一律 failed（引用状态可能已回滚，fail-closed）
            self.assertEqual(
                "failed",
                session.execute(
                    select(PlayerRequest).where(
                        PlayerRequest.world_id == "sp-world",
                        PlayerRequest.request_id == "req-open",
                    )
                )
                .scalar_one()
                .status,
            )
            # 存档点后的请求/检定：failed / cancelled，不静默悬挂
            self.assertEqual(
                "failed",
                session.execute(
                    select(PlayerRequest).where(
                        PlayerRequest.world_id == "sp-world",
                        PlayerRequest.request_id == "req-late",
                    )
                )
                .scalar_one()
                .status,
            )
            # 检定已在存档点后结算（终态历史保留；其状态效果随世界状态回滚）
            self.assertEqual(
                "resolved",
                session.execute(
                    select(CheckRequest).where(
                        CheckRequest.world_id == "sp-world",
                        CheckRequest.check_request_id == self.check_id,
                    )
                )
                .scalar_one()
                .status,
            )
            # 晚于存档 revision 的事件已删除
            remaining = (
                session.execute(
                    select(EventOutbox).where(
                        EventOutbox.world_id == "sp-world",
                        EventOutbox.revision > result["revision"],
                    )
                )
                .scalars()
                .all()
            )
            self.assertEqual([], remaining)
        # failed 请求可用同 request_id 同载荷重发回 queued（§3.1 恢复路径）
        resend = service.submit_action_request(
            world_id="sp-world",
            principal=self.alice,
            request={
                "request_id": "req-open",
                "investigator_id": "inv-alice",
                "action": {"kind": "freeform", "text": "我搜查书架"},
            },
        )
        self.assertEqual("queued", resend["status"])

    def test_restore_keeps_keeper_control(self):
        with session_scope(self.db_url) as session:
            take_control(session, "sp-world", self.keeper)
        save_game([], "slot_001", context=self.context)
        restore_structured_save(self.context, "slot_001")
        with session_scope(self.db_url) as session:
            control = session.get(KeeperControl, "sp-world")
            self.assertEqual("human", control.controller_kind)


class StructuredListingTests(unittest.TestCase):
    """结构化世界的列表可见性：无 Turn/SaveSlot 也能"已玩过、可续团"。"""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.context = make_structured_world(self.root)
        self.db_url = self.context.database_url
        self.keeper = Principal(kind="keeper", user_id="u-keeper")

    def tearDown(self):
        self._temp.cleanup()

    def _service(self):
        from src.storage.world_branches import WorldBranchService

        return WorldBranchService(self.root, self.root)

    def test_structured_activity_makes_world_played_and_resumable(self):
        from src.structured.service import StructuredPlayService

        service = self._service()
        # 无活动：不是存档位，不可续
        self.assertTrue(service.is_tree_untouched("sp-world"))
        self.assertEqual(
            [], service.list_adventures(active_world_id="sp-world", module_name="test-module")
        )
        # 一条命令后：可见且可续（续团 = 重连取快照，不依赖 SaveSlot）
        StructuredPlayService(self.db_url).execute_command(
            world_id="sp-world",
            principal=self.keeper,
            kind="record_fact",
            payload={"text": "开始调查"},
            command_id="cmd-listing-1",
            expected_revision=None,
        )
        self.assertFalse(service.is_tree_untouched("sp-world"))
        adventures = service.list_adventures(active_world_id="sp-world", module_name="test-module")
        self.assertEqual(1, len(adventures))
        timelines = adventures[0]["timelines"]
        entry = next(t for t in timelines if t["world_id"] == "sp-world")
        self.assertTrue(entry["resumable"])
        worlds = service.list_worlds("test-module", active_world_id="sp-world")
        entry = next(w for w in worlds if w["world_id"] == "sp-world")
        self.assertTrue(entry["resumable"])


if __name__ == "__main__":
    unittest.main()


class StructuredBranchRevisionTests(unittest.TestCase):
    """expected_revision：云端入口钉住分叉点，防止用户以为分叉的是旧状态。"""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.context = make_structured_world(self.root)
        self.db_url = self.context.database_url

    def tearDown(self):
        self._temp.cleanup()

    def _revision(self) -> int:
        from src.storage.database_store import DatabaseWorldStore

        return DatabaseWorldStore(self.db_url, "sp-world", self.context.world_dir).snapshot().revision

    def test_expected_revision_match_creates_branch(self):
        branch = create_structured_branch(
            self.context,
            project_root=self.root,
            runtime_root=self.root,
            label="钉版本分支",
            expected_revision=self._revision(),
        )
        self.assertTrue(branch.context.world_id)

    def test_expected_revision_mismatch_rejects_without_creating(self):
        from src.structured.errors import StructuredError

        with self.assertRaises(StructuredError) as caught:
            create_structured_branch(
                self.context,
                project_root=self.root,
                runtime_root=self.root,
                label="过期分叉",
                expected_revision=self._revision() + 99,
            )
        self.assertEqual("revision_conflict", caught.exception.code)
        # 不得创建任何分支世界。
        from src.storage.database import World, session_scope

        with session_scope(self.db_url) as session:
            worlds = session.execute(select(World)).scalars().all()
        self.assertEqual(["sp-world"], [w.id for w in worlds])
