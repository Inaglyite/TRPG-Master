"""M2：出示素材授权、孤注一掷、时间代价、玩家取消与失败重发（§3、§12）。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from test_structured_commands import make_structured_world

from src.storage.database import CheckRequest, PlayerRequest, session_scope
from src.storage.database_store import DatabaseWorldStore
from src.structured.errors import StructuredError
from src.structured.principal import Principal
from src.structured.service import StructuredPlayService


class StructuredM2Tests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.context = make_structured_world(self.root)
        self.service = StructuredPlayService(self.context.database_url)
        self.alice = Principal(kind="player", user_id="u-alice", investigator_ids=("inv-alice",))
        self.bob = Principal(kind="player", user_id="u-bob", investigator_ids=("inv-bob",))
        self.keeper = Principal(kind="keeper", user_id="u-keeper")

    def tearDown(self):
        self._temp.cleanup()

    def persisted(self):
        store = DatabaseWorldStore(self.context.database_url, "sp-world", self.context.world_dir)
        snapshot = store.snapshot()
        return snapshot.state, snapshot.revision

    def _rigged(self, rolls: list[int]) -> StructuredPlayService:
        queue = list(rolls)

        def rng(n: int) -> int:
            if not queue:
                raise AssertionError("骰子次数超出预期（重复掷骰？）")
            return queue.pop(0) % n

        return StructuredPlayService(self.context.database_url, rng=rng)

    def _request_check(self, service, payload: dict, command_id: str) -> str:
        result = service.execute_command(
            world_id="sp-world",
            principal=self.keeper,
            kind="request_check",
            payload=payload,
            command_id=command_id,
            expected_revision=None,
        )
        return result["result"]["check_request_id"]

    def _roll(self, service, check_id: str, request_id: str, decision="roll") -> dict:
        return service.submit_check_response(
            world_id="sp-world",
            principal=self.alice,
            request={
                "request_id": request_id,
                "world_id": "sp-world",
                "check_request_id": check_id,
                "decision": decision,
            },
        )

    def _check_row(self, check_id: str) -> CheckRequest:
        from sqlalchemy import select

        with session_scope(self.context.database_url) as session:
            return session.execute(
                select(CheckRequest).where(
                    CheckRequest.world_id == "sp-world",
                    CheckRequest.check_request_id == check_id,
                )
            ).scalar_one()

    # --------------------------------------------------------------
    # 孤注一掷（§12 检定行：孤注一掷受规则约束）
    # --------------------------------------------------------------

    def test_push_check_full_lifecycle(self):
        rigged = self._rigged([9, 9, 0, 1])  # 原检定 99 失败；孤注一掷 10 成功
        original = self._request_check(
            rigged,
            {
                "investigator_id": "inv-alice",
                "skill": "说服",
                "difficulty": "regular",
                "attempt": "向看守说明来意。",
                "visibility": "public",
            },
            "cmd-chk-p1",
        )
        failed = self._roll(rigged, original, "req-p1")
        self.assertEqual("failure", failed["result"]["outcome"])

        # 孤注一掷卡：引用原失败检定，keeper 写明升级代价
        push_id = self._request_check(
            rigged,
            {
                "investigator_id": "inv-alice",
                "skill": "说服",
                "difficulty": "regular",
                "attempt": "改以病历借阅登记为由再试一次。",
                "visibility": "public",
                "known_cost": "失败则看守起疑，今日不再接待。",
                "push_for": original,
            },
            "cmd-chk-p2",
        )
        self.assertTrue(self._check_row(original).result["push_pending"])
        # 原卡同一时刻不得再生第二张孤注一掷卡
        with self.assertRaises(StructuredError) as raised:
            self._request_check(
                rigged,
                {
                    "investigator_id": "inv-alice",
                    "skill": "说服",
                    "difficulty": "regular",
                    "attempt": "换说法再试。",
                    "visibility": "public",
                    "push_for": original,
                },
                "cmd-chk-p3",
            )
        self.assertEqual("invalid_action", raised.exception.code)

        pushed = self._roll(rigged, push_id, "req-p2")
        self.assertEqual("success", pushed["result"]["outcome"])
        self.assertEqual(original, pushed["events"][0]["payload"].get("push_for"))
        original_row = self._check_row(original)
        self.assertTrue(original_row.result["pushed"])
        self.assertNotIn("push_pending", original_row.result)

    def test_push_requires_failed_original_and_same_skill(self):
        rigged = self._rigged([0, 1])  # 10 ≤ 55 成功
        ok_id = self._request_check(
            rigged,
            {
                "investigator_id": "inv-alice",
                "skill": "说服",
                "difficulty": "regular",
                "attempt": "聊聊。",
                "visibility": "public",
            },
            "cmd-chk-p4",
        )
        self._roll(rigged, ok_id, "req-p4")
        with self.assertRaises(StructuredError) as raised:
            self._request_check(
                self.service,
                {
                    "investigator_id": "inv-alice",
                    "skill": "说服",
                    "difficulty": "regular",
                    "attempt": "再试。",
                    "visibility": "public",
                    "push_for": ok_id,
                },
                "cmd-chk-p5",
            )
        self.assertEqual("invalid_action", raised.exception.code)

    def test_push_decline_releases_original(self):
        rigged = self._rigged([9, 9])
        original = self._request_check(
            rigged,
            {
                "investigator_id": "inv-alice",
                "skill": "说服",
                "difficulty": "regular",
                "attempt": "试试。",
                "visibility": "public",
            },
            "cmd-chk-p6",
        )
        self._roll(rigged, original, "req-p6")
        push_id = self._request_check(
            self.service,
            {
                "investigator_id": "inv-alice",
                "skill": "说服",
                "difficulty": "regular",
                "attempt": "换个做法。",
                "visibility": "public",
                "push_for": original,
            },
            "cmd-chk-p7",
        )
        # 玩家放弃孤注一掷：不消耗，之后可再开一张
        self._roll(self.service, push_id, "req-p7", decision="decline")
        row = self._check_row(original)
        self.assertNotIn("push_pending", row.result)
        self.assertNotIn("pushed", row.result)

    # --------------------------------------------------------------
    # 时间代价（schema 已有 time_cost_minutes，M2 落实结算）
    # --------------------------------------------------------------

    def test_check_time_cost_advances_clock_on_resolution(self):
        rigged = self._rigged([0, 1])
        check_id = self._request_check(
            rigged,
            {
                "investigator_id": "inv-alice",
                "skill": "侦查",
                "difficulty": "regular",
                "attempt": "仔细搜查书房。",
                "visibility": "public",
                "time_cost_minutes": 30,
            },
            "cmd-chk-t1",
        )
        _state, before = self.persisted()
        resolved = self._roll(rigged, check_id, "req-t1")
        clock_events = [
            e
            for e in resolved["events"]
            if e["type"] == "state_changed" and "clock" in e["payload"]
        ]
        self.assertEqual(1, len(clock_events))
        state, after = self.persisted()
        self.assertEqual(30, state["world_clock"]["elapsed_minutes"])
        self.assertEqual(before + 1, after)  # 状态变化推进 revision

    # --------------------------------------------------------------
    # 出示 image：素材授权（§12 出示行）
    # --------------------------------------------------------------

    def _install_clue_asset(self) -> None:
        with session_scope(self.context.database_url) as session:
            from src.storage.database import WorldState

            row = session.get(WorldState, "sp-world")
            state = dict(row.state)
            state["asset_map"] = {"clues": {"clue_death_certificate": {"file": "death_cert.png"}}}
            row.state = state

    def _present(self, presentation: str, request_id: str):
        _state, revision = self.persisted()
        return self.service.submit_action_request(
            world_id="sp-world",
            principal=self.alice,
            request={
                "type": "action_request",
                "protocol_version": 1,
                "world_id": "sp-world",
                "expected_revision": revision,
                "investigator_id": "inv-alice",
                "request_id": request_id,
                "action": {
                    "kind": "present_clue",
                    "clue_id": "clue_death_certificate",
                    "presentation": presentation,
                    "physical_item_id": None,
                    "target": {"kind": "unresolved", "text": "看守"},
                },
            },
        )

    def test_image_presentation_requires_granted_asset(self):
        self._install_clue_asset()
        with self.assertRaises(StructuredError) as raised:
            self._present("image", "req-img-1")
        self.assertEqual("presentation_requires_asset", raised.exception.code)
        # describe 不受影响
        self.assertEqual("queued", self._present("describe", "req-img-2")["status"])
        # keeper 命令授权素材后 image 放行
        _state, revision = self.persisted()
        self.service.execute_command(
            world_id="sp-world",
            principal=self.keeper,
            kind="present_handout",
            payload={
                "asset_id": "clue_death_certificate",
                "recipient_investigator_ids": ["inv-alice"],
            },
            command_id="cmd-handout-1",
            expected_revision=revision,
        )
        self.assertEqual("queued", self._present("image", "req-img-3")["status"])

    def test_snapshot_advertises_image_only_when_granted(self):
        self._install_clue_asset()
        view = self.service.session_snapshot(world_id="sp-world", principal=self.alice)
        clue = next(c for c in view["clues"] if c["id"] == "clue_death_certificate")
        self.assertEqual(["describe"], clue["presentation"])
        _state, revision = self.persisted()
        self.service.execute_command(
            world_id="sp-world",
            principal=self.keeper,
            kind="present_handout",
            payload={
                "asset_id": "clue_death_certificate",
                "recipient_investigator_ids": ["inv-alice"],
            },
            command_id="cmd-handout-2",
            expected_revision=revision,
        )
        view = self.service.session_snapshot(world_id="sp-world", principal=self.alice)
        clue = next(c for c in view["clues"] if c["id"] == "clue_death_certificate")
        self.assertIn("image", clue["presentation"])

    # --------------------------------------------------------------
    # 玩家取消与失败重发（§3.1）
    # --------------------------------------------------------------

    def _queue_move(self, request_id: str) -> dict:
        _state, revision = self.persisted()
        return self.service.submit_action_request(
            world_id="sp-world",
            principal=self.alice,
            request={
                "type": "action_request",
                "protocol_version": 1,
                "world_id": "sp-world",
                "expected_revision": revision,
                "investigator_id": "inv-alice",
                "request_id": request_id,
                "action": {"kind": "move", "destination_scene_id": "library"},
            },
        )

    def test_cancel_own_queued_request(self):
        self._queue_move("req-c1")
        _state, revision = self.persisted()
        result = self.service.cancel_action_request(
            world_id="sp-world",
            principal=self.alice,
            request={
                "type": "cancel_request",
                "protocol_version": 1,
                "request_id": "req-c1-cancel",
                "world_id": "sp-world",
                "expected_revision": revision,
                "target_request_id": "req-c1",
            },
        )
        self.assertEqual("completed", result["status"])
        status_event = result["events"][0]
        self.assertEqual("action_status", status_event["type"])
        self.assertEqual("req-c1", status_event["payload"]["request_id"])
        self.assertEqual("cancelled", status_event["payload"]["status"])
        # 已取消的请求不能再被主持结案
        with self.assertRaises(StructuredError):
            self.service.execute_command(
                world_id="sp-world",
                principal=self.keeper,
                kind="resolve_intent",
                payload={"request_id": "req-c1", "resolution": "completed"},
                command_id="cmd-resolve-c1",
                expected_revision=None,
            )
        # 别人的请求不能取消
        self._queue_move("req-c2")
        with self.assertRaises(StructuredError) as raised:
            self.service.cancel_action_request(
                world_id="sp-world",
                principal=self.bob,
                request={
                    "type": "cancel_request",
                    "protocol_version": 1,
                    "request_id": "req-c2-cancel",
                    "world_id": "sp-world",
                    "target_request_id": "req-c2",
                },
            )
        self.assertEqual("not_investigator_controller", raised.exception.code)

    def test_failed_request_resubmission_requeues(self):
        self._queue_move("req-f1")
        with session_scope(self.context.database_url) as session:
            from sqlalchemy import select

            row = session.execute(
                select(PlayerRequest).where(
                    PlayerRequest.world_id == "sp-world",
                    PlayerRequest.request_id == "req-f1",
                )
            ).scalar_one()
            row.status = "failed"
        again = self._queue_move("req-f1")
        self.assertEqual("queued", again["status"])
        self.assertNotIn("deduplicated", again)
        types = [e["type"] for e in again["events"]]
        self.assertEqual(["action_ack", "intent_pending"], types)


if __name__ == "__main__":
    unittest.main()
