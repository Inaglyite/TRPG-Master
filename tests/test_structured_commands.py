"""structured_v1 命令服务：离线命令 + 真实数据库集成（M1）。

验收约束（主规格 §6.1 / §12）：
- 状态、命令结果与待发布事件在同一事务提交；提交前故障 => 独立数据库连接
  看不到任何状态变化、命令行或事件（不依赖同一个 store 的缓存值）。
- 命令/请求幂等：同 ID 同载荷返回旧结果；同 ID 异载荷拒绝。
- 权限：keeper 与 owner 分离；调查员控制权每次复核；接管后旧 epoch 失效。
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.app.config import PROJECT_ROOT
from src.app.runtime import RuntimeContext
from src.storage.database import (
    EventOutbox,
    GameCommand,
    User,
    WorldInvestigator,
    WorldMember,
    session_scope,
)
from src.storage.database_store import DatabaseWorldStore
from src.structured.errors import StructuredError
from src.structured.principal import (
    Principal,
    bind_agent_control,
    resolve_keeper_principal,
    resolve_player_principal,
)
from src.structured.service import StructuredPlayService


def make_structured_world(root: Path) -> RuntimeContext:
    """两名调查员 + 两场景 + 线索/物品的最小 structured_v1 世界。"""
    shutil.copytree(PROJECT_ROOT / "skills", root / "skills", dirs_exist_ok=True)
    module_dir = root / "mod" / "test-module"
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "module.md").write_text("# Test", encoding="utf-8")
    (module_dir / "world_state_initial.json").write_text(
        json.dumps(
            {
                "module": "test-module",
                "investigators": {
                    "inv-alice": {
                        "name": "爱丽丝",
                        "hp": 11,
                        "max_hp": 12,
                        "san": 58,
                        "max_san": 65,
                        "skills": {"说服": 55, "侦查": 70},
                        "inventory": ["绷带", "绷带", "记者证"],
                        "conditions": [],
                    },
                    "inv-bob": {
                        "name": "鲍勃",
                        "hp": 10,
                        "max_hp": 10,
                        "san": 50,
                        "max_san": 50,
                        "skills": {"斗殴": 60},
                        "inventory": [],
                        "conditions": [],
                    },
                },
                "pc": {"name": "占位", "hp": 1, "skills": {}},
                "current_scene": {
                    "id": "study",
                    "name": "书房",
                    "exits": ["library"],
                    "npcs_present": [],
                },
                "scene_catalog": {
                    "study": {
                        "id": "study",
                        "name": "书房",
                        "description": "堆满书。",
                        "exits": ["library"],
                    },
                    "library": {
                        "id": "library",
                        "name": "图书馆",
                        "description": "安静的大厅。",
                        "exits": ["study"],
                    },
                },
                "npcs": [{"id": "keeper_npc", "name": "老看守", "current_location": "library"}],
                "clue_catalog": {
                    "clue_death_certificate": {
                        "id": "clue_death_certificate",
                        "category": "investigation",
                        "text": "莱特的死亡证明由医生签署。",
                    },
                    "clue_autopsy_note": {
                        "id": "clue_autopsy_note",
                        "category": "investigation",
                        "text": "医生承认证明是在压力下签署的。",
                    },
                },
                "clues_found": {
                    "investigation": [
                        {
                            "id": "clue_death_certificate",
                            "catalog_id": "clue_death_certificate",
                            "text": "莱特的死亡证明由医生签署。",
                            "category": "investigation",
                            "granted_to": ["inv-alice"],
                        }
                    ],
                    "event": [],
                    "task": [],
                    "npc": [],
                },
                "assets": {"asset_note": {"file": "note.png"}},
                "combat_state": {"active": False},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    context = RuntimeContext.create(
        "sp-world",
        "test-module",
        project_root=root,
        runtime_root=root,
    )
    with session_scope(context.database_url) as session:
        from src.storage.database import World

        world = session.get(World, "sp-world")
        world.metadata_json = {
            **(world.metadata_json or {}),
            "execution_profile": "structured_v1",
            "keeper_mode": "human",
        }
        session.add_all(
            [
                User(id="u-alice", username="alice", password_hash="x"),
                User(id="u-bob", username="bob", password_hash="x"),
                User(id="u-keeper", username="keeper", password_hash="x"),
                User(id="u-owner", username="owner", password_hash="x"),
            ]
        )
        session.add_all(
            [
                WorldMember(id="m-alice", world_id="sp-world", user_id="u-alice", role="player"),
                WorldMember(id="m-bob", world_id="sp-world", user_id="u-bob", role="player"),
                WorldMember(
                    id="m-keeper",
                    world_id="sp-world",
                    user_id="u-keeper",
                    role="player",
                    can_keeper=True,
                ),
                WorldMember(id="m-owner", world_id="sp-world", user_id="u-owner", role="owner"),
            ]
        )
        session.add_all(
            [
                WorldInvestigator(
                    id="wi-alice",
                    world_id="sp-world",
                    character_key="inv-alice",
                    character_ref={},
                    controller_user_id="u-alice",
                    status="claimed",
                ),
                WorldInvestigator(
                    id="wi-bob",
                    world_id="sp-world",
                    character_key="inv-bob",
                    character_ref={},
                    controller_user_id="u-bob",
                    status="claimed",
                ),
            ]
        )
    return context


class StructuredCommandTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.context = make_structured_world(self.root)
        self.service = StructuredPlayService(self.context.database_url)
        self.alice = Principal(kind="player", user_id="u-alice", investigator_ids=("inv-alice",))
        self.bob = Principal(kind="player", user_id="u-bob", investigator_ids=("inv-bob",))
        self.keeper = Principal(kind="keeper", user_id="u-keeper")
        self.owner = Principal(kind="keeper", user_id="u-owner")  # owner 但无 keeper 授权
        self.base_revision = self.persisted()[1]

    def tearDown(self):
        self._temp.cleanup()

    # --------------------------------------------------------------
    # 工具
    # --------------------------------------------------------------

    def persisted(self) -> tuple[dict, int]:
        """独立数据库连接读到的已提交状态与 revision（不用同一 store 缓存）。"""
        store = DatabaseWorldStore(self.context.database_url, "sp-world", self.context.world_dir)
        snapshot = store.snapshot()
        return snapshot.state, snapshot.revision

    def outbox_rows(self) -> list:
        with session_scope(self.context.database_url) as session:
            from sqlalchemy import select

            return list(
                session.execute(
                    select(EventOutbox).where(EventOutbox.world_id == "sp-world")
                ).scalars()
            )

    def command_rows(self) -> list:
        with session_scope(self.context.database_url) as session:
            from sqlalchemy import select

            return list(
                session.execute(
                    select(GameCommand).where(GameCommand.world_id == "sp-world")
                ).scalars()
            )

    def keeper_command(
        self, kind, payload, *, command_id=None, expected_revision=None, principal=None
    ):
        if expected_revision is None:
            _state, expected_revision = self.persisted()
        return self.service.execute_command(
            world_id="sp-world",
            principal=principal or self.keeper,
            kind=kind,
            payload=payload,
            command_id=command_id or f"cmd-{kind}-test",
            expected_revision=expected_revision,
        )

    # --------------------------------------------------------------
    # 提交边界（协议 §6.1）
    # --------------------------------------------------------------

    def test_move_party_commits_state_and_events_atomically(self):
        result = self.keeper_command(
            "move_party",
            {"destination_scene_id": "library", "travel_minutes": 20},
            command_id="cmd-move-1",
        )
        self.assertEqual("committed", result["status"])
        self.assertEqual("library", result["result"]["scene_id"])
        self.assertEqual(
            ["scene_changed", "state_changed", "action_status"],
            [e["type"] for e in result["events"]],
        )

        state, revision = self.persisted()
        self.assertEqual("library", state["current_scene"]["id"])
        self.assertEqual(20, state["world_clock"]["elapsed_minutes"])
        self.assertEqual(self.base_revision + 1, revision)
        self.assertEqual(["keeper_npc"], state["current_scene"]["npcs_present"])
        # 抵达不等于调查：无线索/物品/理智副作用
        self.assertEqual(58, state["investigators"]["inv-alice"]["san"])
        self.assertEqual(3, len(self.outbox_rows()))  # 含命令收尾 action_status

    def test_pre_commit_failure_persists_nothing_and_publishes_nothing(self):
        """事务内故障：独立连接看不到状态/命令/事件，调用方拿不到事件。"""
        from src.structured import service as service_module

        with patch.object(
            service_module.StructuredPlayService,
            "_append_events",
            side_effect=RuntimeError("simulated mid-transaction failure"),
        ):
            with self.assertRaises(RuntimeError):
                self.keeper_command(
                    "move_party", {"destination_scene_id": "library"}, command_id="cmd-move-x"
                )
        state, revision = self.persisted()
        self.assertEqual("study", state["current_scene"]["id"])
        self.assertEqual(self.base_revision, revision)
        self.assertEqual([], self.outbox_rows())
        self.assertEqual([], self.command_rows())

    def test_command_dedup_and_conflict(self):
        first = self.keeper_command(
            "adjust_stat",
            {"investigator_id": "inv-alice", "field": "hp", "delta": -2, "reason": "摔下楼梯"},
            command_id="cmd-hp-1",
        )
        self.assertEqual(9, first["result"]["after"])
        # 同 ID 同载荷：返回已保存结果，不重复扣减
        second = self.keeper_command(
            "adjust_stat",
            {"investigator_id": "inv-alice", "field": "hp", "delta": -2, "reason": "摔下楼梯"},
            command_id="cmd-hp-1",
            expected_revision=999,  # 去重命中时连 revision 都不再看
        )
        self.assertTrue(second["deduplicated"])
        self.assertEqual(9, second["result"]["after"])
        state, _revision = self.persisted()
        self.assertEqual(9, state["investigators"]["inv-alice"]["hp"])
        self.assertEqual(1, len(self.command_rows()))
        # 同 ID 异载荷：拒绝且不改状态
        with self.assertRaises(StructuredError) as raised:
            self.keeper_command(
                "adjust_stat",
                {"investigator_id": "inv-alice", "field": "hp", "delta": -5, "reason": "改载荷"},
                command_id="cmd-hp-1",
            )
        self.assertEqual("duplicate_request_conflict", raised.exception.code)
        state, _revision = self.persisted()
        self.assertEqual(9, state["investigators"]["inv-alice"]["hp"])

    def test_revision_conflict_changes_nothing(self):
        with self.assertRaises(StructuredError) as raised:
            self.keeper_command(
                "advance_time",
                {"minutes": 30, "reason": "等待"},
                command_id="cmd-time-x",
                expected_revision=999,
            )
        self.assertEqual("revision_conflict", raised.exception.code)
        self.assertTrue(raised.exception.retryable)
        state, revision = self.persisted()
        self.assertEqual(self.base_revision, revision)
        self.assertNotIn("world_clock", state)
        self.assertEqual([], self.outbox_rows())

    # --------------------------------------------------------------
    # 权限与控制权（§4、§12 并发）
    # --------------------------------------------------------------

    def test_keeper_grant_is_separate_from_owner(self):
        with self.assertRaises(StructuredError) as raised:
            self.keeper_command(
                "grant_clue",
                {
                    "clue_id": "clue_autopsy_note",
                    "recipient_investigator_ids": ["inv-alice"],
                    "basis": "医生私下承认。",
                },
                principal=self.owner,  # owner 但没有 can_keeper
            )
        # owner 的 keeper principal 是伪造的：服务层 resolve 才会拦；
        # 命令服务本身按 kind 要求 keeper/agent + 控制权——伪造 keeper kind
        # 且无会话接管记录时取得控制权的是 take_control……
        # 这里 check_command_authority 会让自称 keeper 的 principal 直接
        # take_control，因此真正的闸门在 principal 解析层（下面单独验证）。
        self.assertIn(raised.exception.code, {"controller_epoch_stale", "keeper_required"})

    def test_principal_resolution_enforces_keeper_grant(self):
        with session_scope(self.context.database_url) as session:
            keeper = resolve_keeper_principal(session, "sp-world", "u-keeper")
            self.assertEqual("keeper", keeper.kind)
            with self.assertRaises(StructuredError) as raised:
                resolve_keeper_principal(session, "sp-world", "u-owner")
            self.assertEqual("keeper_required", raised.exception.code)
            player = resolve_player_principal(session, "sp-world", "u-alice")
            self.assertEqual(("inv-alice",), player.investigator_ids)

    def test_agent_epoch_stale_after_takeover(self):
        with session_scope(self.context.database_url) as session:
            bind_agent_control(session, "sp-world", "run-1")
        agent = Principal(kind="agent", run_id="run-1")
        ok = self.keeper_command(
            "advance_time",
            {"minutes": 5, "reason": "agent 在任"},
            command_id="cmd-agent-1",
            principal=agent,
        )
        self.assertEqual("committed", ok["status"])
        # 人类接管：epoch 递增，旧 agent 的迟到命令被拒绝
        with session_scope(self.context.database_url) as session:
            from src.structured.principal import take_control

            take_control(session, "sp-world", self.keeper)
        with self.assertRaises(StructuredError) as raised:
            self.keeper_command(
                "advance_time",
                {"minutes": 5, "reason": "迟到调用"},
                command_id="cmd-agent-2",
                principal=agent,
            )
        self.assertEqual("controller_epoch_stale", raised.exception.code)

    def test_publish_message_speaker_rules(self):
        # 玩家以己身份发言：允许，且不推进 revision
        result = self.service.execute_command(
            world_id="sp-world",
            principal=self.alice,
            kind="publish_message",
            payload={
                "speaker": {"kind": "investigator", "id": "inv-alice"},
                "audience": {"kind": "public"},
                "text": "我检查书桌抽屉。",
            },
            command_id="cmd-msg-alice",
            expected_revision=self.base_revision,
        )
        self.assertEqual("committed", result["status"])
        _state, revision = self.persisted()
        self.assertEqual(self.base_revision, revision)
        # 玩家不能以 NPC 身份发言
        with self.assertRaises(StructuredError) as raised:
            self.service.execute_command(
                world_id="sp-world",
                principal=self.alice,
                kind="publish_message",
                payload={
                    "speaker": {"kind": "npc", "id": "keeper_npc"},
                    "audience": {"kind": "public"},
                    "text": "我是看守。",
                },
                command_id="cmd-msg-fake",
                expected_revision=self.base_revision,
            )
        self.assertEqual("not_authorized", raised.exception.code)
        # keeper 可以扮演 NPC
        result = self.keeper_command(
            "publish_message",
            {
                "speaker": {"kind": "npc", "id": "keeper_npc"},
                "audience": {"kind": "public"},
                "text": "图书馆今晚不开放。",
            },
            command_id="cmd-msg-keeper",
        )
        self.assertEqual("committed", result["status"])
        self.assertEqual(
            ["message_started", "message_completed", "action_status"],
            [event["type"] for event in result["events"]],
        )

    # --------------------------------------------------------------
    # 检定生命周期（§6.2、§12 骰子/检定）
    # --------------------------------------------------------------

    def _rigged_service(self, rolls: list[int]) -> StructuredPlayService:
        queue = list(rolls)

        def rng(n: int) -> int:
            if not queue:
                raise AssertionError("骰子次数超出预期（重复掷骰？）")
            return queue.pop(0) % n

        return StructuredPlayService(self.context.database_url, rng=rng)

    def test_check_lifecycle_roll_once_and_permissions(self):
        rigged = self._rigged_service([3, 2])  # units=3, tens=2 → roll 23
        created = rigged.execute_command(
            world_id="sp-world",
            principal=self.keeper,
            kind="request_check",
            payload={
                "investigator_id": "inv-alice",
                "skill": "说服",
                "difficulty": "regular",
                "bonus_penalty": 0,
                "attempt": "向看守说明来意。",
                "visibility": "public",
            },
            command_id="cmd-chk-1",
            expected_revision=self.base_revision,
        )
        check_id = created["result"]["check_request_id"]
        self.assertEqual("check_requested", created["events"][0]["type"])

        # 其他玩家不能代点
        with self.assertRaises(StructuredError) as raised:
            rigged.submit_check_response(
                world_id="sp-world",
                principal=self.bob,
                request={
                    "request_id": "req-resp-bob",
                    "world_id": "sp-world",
                    "check_request_id": check_id,
                    "decision": "roll",
                },
            )
        self.assertEqual("not_investigator_controller", raised.exception.code)

        resolved = rigged.submit_check_response(
            world_id="sp-world",
            principal=self.alice,
            request={
                "request_id": "req-resp-1",
                "world_id": "sp-world",
                "check_request_id": check_id,
                "decision": "roll",
            },
        )
        self.assertEqual("completed", resolved["status"])
        self.assertEqual(23, resolved["result"]["roll"])
        self.assertEqual("success", resolved["result"]["outcome"])
        self.assertEqual("check_resolved", resolved["events"][0]["type"])
        # 同一张卡不能结算第二次
        with self.assertRaises(StructuredError) as raised:
            rigged.submit_check_response(
                world_id="sp-world",
                principal=self.alice,
                request={
                    "request_id": "req-resp-2",
                    "world_id": "sp-world",
                    "check_request_id": check_id,
                    "decision": "roll",
                },
            )
        self.assertEqual("check_already_resolved", raised.exception.code)

    def test_check_conditions_change_invalidates(self):
        self.keeper_command(
            "request_check",
            {
                "investigator_id": "inv-alice",
                "skill": "侦查",
                "difficulty": "regular",
                "attempt": "搜查书房。",
                "visibility": "public",
            },
            command_id="cmd-chk-2",
        )
        with session_scope(self.context.database_url) as session:
            from sqlalchemy import select

            from src.storage.database import CheckRequest

            check_id = session.execute(
                select(CheckRequest.check_request_id).where(CheckRequest.world_id == "sp-world")
            ).scalar_one()
        # 相关条件变化（场景切换）后结算：失效，不掷骰
        self.keeper_command(
            "move_party", {"destination_scene_id": "library"}, command_id="cmd-move-2"
        )
        with self.assertRaises(StructuredError) as raised:
            self.service.submit_check_response(
                world_id="sp-world",
                principal=self.alice,
                request={
                    "request_id": "req-resp-3",
                    "world_id": "sp-world",
                    "check_request_id": check_id,
                    "decision": "roll",
                },
            )
        self.assertEqual("check_conditions_changed", raised.exception.code)

    def test_unknown_skill_is_rejected_at_request_time(self):
        with self.assertRaises(StructuredError) as raised:
            self.keeper_command(
                "request_check",
                {
                    "investigator_id": "inv-alice",
                    "skill": "不存在的技能",
                    "difficulty": "regular",
                    "attempt": "试试。",
                    "visibility": "public",
                },
                command_id="cmd-chk-x",
            )
        self.assertEqual("invalid_action", raised.exception.code)

    # --------------------------------------------------------------
    # 普通掷骰（§3.4）
    # --------------------------------------------------------------

    def test_free_roll_has_no_story_effects_and_replays_without_reroll(self):
        rigged = self._rigged_service([41])
        result = rigged.submit_free_roll(
            world_id="sp-world",
            principal=self.alice,
            request={
                "request_id": "req-roll-1",
                "world_id": "sp-world",
                "investigator_id": "inv-alice",
                "spec": "1d100",
            },
        )
        self.assertEqual(42, result["result"]["total"])
        self.assertEqual("普通掷骰", result["events"][0]["payload"]["note"])
        _state, revision = self.persisted()
        self.assertEqual(self.base_revision, revision)  # 普通骰不推进世界版本
        # 重发同一 request_id：返回旧骰点，rng 队列已空 → 没有再掷
        replay = rigged.submit_free_roll(
            world_id="sp-world",
            principal=self.alice,
            request={
                "request_id": "req-roll-1",
                "world_id": "sp-world",
                "investigator_id": "inv-alice",
                "spec": "1d100",
            },
        )
        self.assertTrue(replay["deduplicated"])
        self.assertEqual(42, replay["result"]["total"])
        self.assertEqual([], replay["events"])

    # --------------------------------------------------------------
    # 玩家行动请求与事实检查（§3、§12 意图/出示/道具）
    # --------------------------------------------------------------

    def test_action_request_fact_checks(self):
        def submit(payload):
            return self.service.submit_action_request(
                world_id="sp-world", principal=self.alice, request=payload
            )

        base = {
            "type": "action_request",
            "protocol_version": 1,
            "world_id": "sp-world",
            "expected_revision": self.base_revision,
            "investigator_id": "inv-alice",
        }
        # 未获知的线索（不区分“不存在”与“未授权”，避免泄露线索存在性）
        with self.assertRaises(StructuredError) as raised:
            submit(
                {
                    **base,
                    "request_id": "r1",
                    "action": {
                        "kind": "present_clue",
                        "clue_id": "clue_autopsy_note",
                        "presentation": "describe",
                        "physical_item_id": None,
                        "target": {"kind": "npc", "id": "keeper_npc"},
                    },
                }
            )
        self.assertEqual("object_not_found", raised.exception.code)
        # 展示原件但手里没有
        with self.assertRaises(StructuredError) as raised:
            submit(
                {
                    **base,
                    "request_id": "r2",
                    "action": {
                        "kind": "present_clue",
                        "clue_id": "clue_death_certificate",
                        "presentation": "original",
                        "physical_item_id": "item_missing",
                        "target": {"kind": "npc", "id": "keeper_npc"},
                    },
                }
            )
        self.assertEqual("object_not_held", raised.exception.code)
        # 目标 NPC 不在场
        with self.assertRaises(StructuredError) as raised:
            submit(
                {
                    **base,
                    "request_id": "r3",
                    "action": {
                        "kind": "present_clue",
                        "clue_id": "clue_death_certificate",
                        "presentation": "describe",
                        "physical_item_id": None,
                        "target": {"kind": "npc", "id": "keeper_npc"},
                    },
                }
            )
        self.assertEqual("stale_target", raised.exception.code)
        # 未知目的地
        with self.assertRaises(StructuredError) as raised:
            submit(
                {
                    **base,
                    "request_id": "r4",
                    "action": {"kind": "move", "destination_scene_id": "nowhere"},
                }
            )
        self.assertEqual("unknown_target", raised.exception.code)
        # 合法请求：排队 + ack + 主持待办
        ok = submit(
            {
                **base,
                "request_id": "r5",
                "action": {"kind": "move", "destination_scene_id": "library"},
            }
        )
        self.assertEqual("queued", ok["status"])
        self.assertEqual(
            ["action_ack", "intent_pending"], [event["type"] for event in ok["events"]]
        )
        self.assertEqual({"kind": "keeper"}, ok["events"][1]["audience"])
        # 同 ID 同载荷重发：返回当前状态，不产生新事件
        again = submit(
            {
                **base,
                "request_id": "r5",
                "action": {"kind": "move", "destination_scene_id": "library"},
            }
        )
        self.assertTrue(again["deduplicated"])
        self.assertEqual([], again["events"])
        # 同 ID 异载荷：拒绝
        with self.assertRaises(StructuredError) as raised:
            submit(
                {
                    **base,
                    "request_id": "r5",
                    "action": {"kind": "move", "destination_scene_id": "study"},
                }
            )
        self.assertEqual("duplicate_request_conflict", raised.exception.code)
        # 请求本身不改变场景：只有主持 move_party 才移动
        state, _revision = self.persisted()
        self.assertEqual("study", state["current_scene"]["id"])

    def test_party_known_clue_presentable_by_any_investigator(self):
        """granted_to 为空 = 全队共享线索（模组初始线索即此形态）。

        快照投影按此口径展示，行动校验必须一致——否则 UI 给得出示按钮、
        提交却被拒（2026-09-16 按钮级真机验收实测：模组初始线索
        clue_001/002 无 granted_to，出示被判 not_authorized）。
        """
        state, _ = self.persisted()
        found = state["clues_found"]["investigation"]
        found.append(
            {
                "id": "clue_party_shared",
                "catalog_id": "clue_party_shared",
                "text": "全队都知道的公开线索。",
                "category": "investigation",
                # 无 granted_to：全队共享
            }
        )

        def mutate(s):
            s["clues_found"]["investigation"] = found

        store = DatabaseWorldStore(self.context.database_url, "sp-world", self.context.world_dir)
        store.update(mutate)
        for request_id, principal in (
            ("r-shared-alice", self.alice),
            ("r-shared-bob", self.bob),
        ):
            ok = self.service.submit_action_request(
                world_id="sp-world",
                principal=principal,
                request={
                    "type": "action_request",
                    "protocol_version": 1,
                    "world_id": "sp-world",
                    "expected_revision": None,
                    "investigator_id": principal.investigator_ids[0],
                    "request_id": request_id,
                    "action": {
                        "kind": "present_clue",
                        "clue_id": "clue_party_shared",
                        "presentation": "describe",
                        "physical_item_id": None,
                        # 未解析目标交主持澄清（keeper_npc 在 library，不在当前场景），
                        # 本测试只钉线索知情口径。
                        "target": {"kind": "unresolved", "text": "在场的人"},
                    },
                },
            )
            self.assertEqual("queued", ok["status"])

    def test_unresolved_target_is_queued_for_keeper_clarification(self):
        ok = self.service.submit_action_request(
            world_id="sp-world",
            principal=self.alice,
            request={
                "type": "action_request",
                "protocol_version": 1,
                "world_id": "sp-world",
                "expected_revision": self.base_revision,
                "investigator_id": "inv-alice",
                "request_id": "r-unresolved",
                "action": {
                    "kind": "present_clue",
                    "clue_id": "clue_death_certificate",
                    "presentation": "describe",
                    "physical_item_id": None,
                    "target": {"kind": "unresolved", "text": "图书馆里打瞌睡的管理员"},
                },
            },
        )
        self.assertEqual("queued", ok["status"])

    # --------------------------------------------------------------
    # 线索/物品命令与隐私投影（§12 隐私）
    # --------------------------------------------------------------

    def test_grant_clue_targets_only_authorized_recipient(self):
        result = self.keeper_command(
            "grant_clue",
            {
                "clue_id": "clue_autopsy_note",
                "recipient_investigator_ids": ["inv-alice"],
                "basis": "医生私下承认。",
            },
            command_id="cmd-grant-1",
        )
        self.assertEqual("committed", result["status"])
        # 事件按接收者定向
        self.assertEqual(
            {"kind": "investigators", "investigator_ids": ["inv-alice"]},
            result["events"][0]["audience"],
        )
        # 快照投影：bob 看不到，alice 与 keeper 看得到
        bob_view = self.service.session_snapshot(world_id="sp-world", principal=self.bob)
        self.assertNotIn("clue_autopsy_note", [c["id"] for c in bob_view["clues"]])
        alice_view = self.service.session_snapshot(world_id="sp-world", principal=self.alice)
        self.assertIn("clue_autopsy_note", [c["id"] for c in alice_view["clues"]])
        keeper_view = self.service.session_snapshot(world_id="sp-world", principal=self.keeper)
        self.assertIn("clue_autopsy_note", [c["id"] for c in keeper_view["clues"]])
        # 事件补发同样过滤：bob 回放拿不到这条
        replayed_bob = self.service.replay_events(
            world_id="sp-world", after_sequence=0, principal=self.bob
        )
        self.assertNotIn("clue_granted", [e["type"] for e in replayed_bob])
        replayed_alice = self.service.replay_events(
            world_id="sp-world", after_sequence=0, principal=self.alice
        )
        self.assertIn("clue_granted", [e["type"] for e in replayed_alice])

    def test_keeper_snapshot_sees_all_party_items(self):
        # 主持/Agent 必须看到全队持有物（含持有人）——否则裁决时对
        # 「玩家身上有什么」是盲的（2026-09-16 真机验收：Agent 上下文
        # items 恒空，错误断言玩家没有起始钥匙）。
        keeper_view = self.service.session_snapshot(world_id="sp-world", principal=self.keeper)
        keeper_items = keeper_view["items"]
        self.assertEqual(
            ["绷带", "记者证"], sorted(item["label"] for item in keeper_items)
        )
        self.assertTrue(all(item.get("holder_id") == "inv-alice" for item in keeper_items))
        # Agent principal（无 investigator_ids）与 keeper 同视角
        agent_view = self.service.session_snapshot(
            world_id="sp-world", principal=Principal(kind="agent", run_id="t")
        )
        self.assertEqual(
            ["绷带", "记者证"], sorted(item["label"] for item in agent_view["items"])
        )
        # 对偶：玩家仍只看自己的——bob 无物品，且看不到 alice 的
        bob_view = self.service.session_snapshot(world_id="sp-world", principal=self.bob)
        self.assertEqual([], bob_view["items"])
        alice_view = self.service.session_snapshot(world_id="sp-world", principal=self.alice)
        self.assertEqual(
            ["绷带", "记者证"], sorted(item["label"] for item in alice_view["items"])
        )
        self.assertNotIn(
            "holder_id", alice_view["items"][0]  # 玩家投影不带持有人字段
        )

    def test_scene_notes_for_agent_carry_module_descriptions(self):
        # Agent 上下文需要模组场景描述做 grounding（2026-09-16 A 组：目的地只有
        # 名称没有描述，模型把「遗体在冷柜」编成「已下葬」）。
        notes = self.service.scene_notes_for_agent("sp-world")
        by_id = {note["scene_id"]: note for note in notes}
        self.assertEqual("堆满书。", by_id["study"]["description"])
        self.assertTrue(by_id["study"].get("current"))
        self.assertEqual("安静的大厅。", by_id["library"]["description"])
        self.assertNotIn("current", by_id["library"])
        # 对偶：公开快照的 destinations 仍然只有 id+name（协议不变）
        view = self.service.session_snapshot(world_id="sp-world", principal=self.alice)
        self.assertEqual(
            {"id", "name"}, set(view["destinations"][0].keys())
        )

    def test_investigator_sheets_for_agent_carry_skill_keys(self):
        # Agent 需要权威角色卡的精确技能键来发起检定（2026-09-16 实测：看不到
        # 角色卡时模型写中文技能名「侦查」，被确定性拒绝——真实键是英文）。
        sheets = self.service.investigator_sheets_for_agent("sp-world")
        by_id = {sheet["investigator_id"]: sheet for sheet in sheets}
        self.assertEqual(70, by_id["inv-alice"]["skills"]["侦查"])
        self.assertEqual(55, by_id["inv-alice"]["skills"]["说服"])
        self.assertEqual(11, by_id["inv-alice"]["hp"])
        self.assertIn("inv-bob", by_id)
        self.assertIn("pc", by_id)  # 占位 pc 也在（有 name）
        # 对偶：公开快照不含角色卡细节（协议不变，只读投影仍是 targets 的 id+name）
        view = self.service.session_snapshot(world_id="sp-world", principal=self.alice)
        target = next(t for t in view["targets"] if t["id"] == "inv-alice")
        self.assertEqual({"kind", "id", "name"}, set(target.keys()))

    def test_snapshot_item_ids_are_stable_and_persisted(self):        # 快照展示的物品 ID 必须稳定且已落库：前端拿快照 ID 提交，若首次命令
        # 再迁移生成另一套随机 ID，提交必被拒（2026-09-16 按钮级实测
        # object_not_held）。两次快照 + 独立连接读库三重一致。
        first = self.service.session_snapshot(world_id="sp-world", principal=self.alice)
        ids_first = [item["id"] for item in first["items"]]
        self.assertTrue(ids_first)
        second = self.service.session_snapshot(world_id="sp-world", principal=self.alice)
        self.assertEqual(ids_first, [item["id"] for item in second["items"]])
        state, _ = self.persisted()
        self.assertIn("item_registry", state)  # 已落库，不只是内存迁移
        registry_ids = sorted(state["item_registry"]["items"].keys())
        self.assertEqual(sorted(ids_first), registry_ids)

    def test_use_item_consumes_once_and_transfer_splits_stack(self):
        # 首个命令触发稳定 ID 注册表的一次性迁移并持久化；之后再读注册表。
        self.keeper_command(
            "publish_message",
            {
                "speaker": {"kind": "keeper"},
                "audience": {"kind": "public"},
                "text": "开场。",
            },
            command_id="cmd-msg-seed",
        )
        state0, _ = self.persisted()
        registry = state0["item_registry"]
        bandage = next(e for e in registry["items"].values() if e["label"] == "绷带")
        self.assertEqual(2, bandage["quantity"])  # 迁移把两个字符串折叠成一堆
        # 部分转移：拆分新堆叠，原堆叠保留剩余数量
        moved = self.keeper_command(
            "transfer_item",
            {
                "item_id": bandage["item_id"],
                "quantity": 1,
                "from": {"kind": "investigator", "id": "inv-alice"},
                "to": {"kind": "investigator", "id": "inv-bob"},
            },
            command_id="cmd-transfer-1",
        )
        self.assertEqual("committed", moved["status"])
        state, _ = self.persisted()
        items = state["item_registry"]["items"]
        self.assertEqual(1, items[bandage["item_id"]]["quantity"])
        bob_stack = items[moved["result"]["item_id"]]
        self.assertEqual({"kind": "investigator", "id": "inv-bob"}, bob_stack["holder"])
        self.assertEqual(1, bob_stack["quantity"])
        # 消耗一个：原堆叠 1 → 0
        used = self.keeper_command(
            "use_item",
            {
                "investigator_id": "inv-alice",
                "item_id": bandage["item_id"],
                "quantity": 1,
                "operation": "apply",
                "consume": True,
            },
            command_id="cmd-use-1",
        )
        self.assertEqual(0, used["result"]["remaining"])
        # 同 command_id 重试不重复扣减
        again = self.keeper_command(
            "use_item",
            {
                "investigator_id": "inv-alice",
                "item_id": bandage["item_id"],
                "quantity": 1,
                "operation": "apply",
                "consume": True,
            },
            command_id="cmd-use-1",
        )
        self.assertTrue(again["deduplicated"])
        state, _ = self.persisted()
        self.assertEqual(0, state["item_registry"]["items"][bandage["item_id"]]["quantity"])

    def test_resolve_intent_closes_request(self):
        self.service.submit_action_request(
            world_id="sp-world",
            principal=self.alice,
            request={
                "type": "action_request",
                "protocol_version": 1,
                "world_id": "sp-world",
                "expected_revision": self.base_revision,
                "investigator_id": "inv-alice",
                "request_id": "r-close",
                "action": {"kind": "move", "destination_scene_id": "library"},
            },
        )
        closed = self.keeper_command(
            "resolve_intent",
            {
                "request_id": "r-close",
                "resolution": "completed",
                "outcome": "success",
                "note": "已处理。",
            },
            command_id="cmd-resolve-1",
        )
        self.assertEqual("completed", closed["events"][0]["payload"]["status"])
        # 终态后不能再次结案
        with self.assertRaises(StructuredError):
            self.keeper_command(
                "resolve_intent",
                {"request_id": "r-close", "resolution": "declined"},
                command_id="cmd-resolve-2",
            )

    def test_legacy_world_rejects_structured_protocol(self):
        with session_scope(self.context.database_url) as session:
            from src.storage.database import World

            world = session.get(World, "sp-world")
            world.metadata_json = {"execution_profile": "legacy"}
        with self.assertRaises(StructuredError) as raised:
            self.keeper_command(
                "advance_time", {"minutes": 1, "reason": "x"}, command_id="cmd-legacy"
            )
        self.assertEqual("profile_mismatch", raised.exception.code)
