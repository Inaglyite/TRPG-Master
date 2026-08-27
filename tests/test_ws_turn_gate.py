import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.app.config import AUTO_SAVE_SLOT, PROJECT_ROOT
from src.app.runtime import RuntimeContext
from src.storage.database import World, session_scope
from src.storage.persistence import save_game
from src.storage.world_branches import WorldBranchService


class WebSocketTurnGateTests(unittest.TestCase):
    def _receive_until(self, ws, message_type: str) -> dict:
        for _ in range(12):
            payload = ws.receive_json()
            if payload.get("type") == message_type:
                return payload
        self.fail(f"没有收到 {message_type} 协议帧")

    def _make_local_branch_contexts(
        self,
        runtime_root: Path,
        *,
        branch_world_id: str = "archive-branch",
        resumable: bool = True,
    ) -> tuple[RuntimeContext, RuntimeContext, WorldBranchService]:
        root = RuntimeContext.local(
            "mansion_of_madness",
            project_root=PROJECT_ROOT,
            runtime_root=runtime_root,
        )
        branch = RuntimeContext.create(
            branch_world_id,
            "mansion_of_madness",
            project_root=PROJECT_ROOT,
            runtime_root=runtime_root,
        )
        with session_scope(branch.database_url) as session:
            world = session.get(World, branch.world_id)
            assert world is not None
            world.metadata_json = {
                **dict(world.metadata_json or {}),
                "branch": {"parent_world_id": root.world_id},
            }
        if resumable:
            save_game(
                [{"role": "assistant", "content": "已保存的时间线。"}],
                AUTO_SAVE_SLOT,
                context=branch,
            )
        return root, branch, WorldBranchService(PROJECT_ROOT, runtime_root)

    def test_unknown_message_returns_explicit_protocol_error(self):
        import server

        with patch("src.app.engine.API_KEY", "test-api-key"):
            with TestClient(server.app) as client:
                with client.websocket_connect("/ws") as ws:
                    for _ in range(6):
                        ws.receive_json()
                    ws.send_json({"type": "future_client_message"})
                    response = ws.receive_json()

        self.assertEqual("protocol_error", response["type"])
        self.assertEqual("unknown_message_type", response["code"])
        self.assertEqual("future_client_message", response["message_type"])

    def test_world_switch_never_releases_another_sessions_world_lock(self):
        import server

        with tempfile.TemporaryDirectory() as temp_dir:
            target = RuntimeContext(
                project_root=PROJECT_ROOT,
                runtime_root=Path(temp_dir),
                world_id="busy-world",
                module_name="mansion_of_madness",
            )
            target_lock = server._world_turn_lock(target)
            target_lock.acquire()
            try:
                with (
                    patch("src.app.engine.API_KEY", "test-api-key"),
                    patch.object(server.WORLD_BRANCHES, "open", return_value=target),
                ):
                    with TestClient(server.app) as client:
                        with client.websocket_connect("/ws") as ws:
                            for _ in range(6):
                                ws.receive_json()
                            ws.send_json({
                                "type": "world_switch",
                                "world_id": "busy-world",
                            })
                            response = ws.receive_json()

                self.assertEqual("world_switch_failed", response["type"])
                self.assertTrue(target_lock.locked())
            finally:
                if target_lock.locked():
                    target_lock.release()

    def test_world_switch_rejects_a_timeline_without_resume_save(self):
        import server

        with tempfile.TemporaryDirectory() as temp_dir:
            target = RuntimeContext(
                project_root=PROJECT_ROOT,
                runtime_root=Path(temp_dir),
                world_id="empty-world",
                module_name="mansion_of_madness",
            )
            with (
                patch("src.app.engine.API_KEY", "test-api-key"),
                patch.object(server.WORLD_BRANCHES, "open", return_value=target),
            ):
                with TestClient(server.app) as client:
                    with client.websocket_connect("/ws") as ws:
                        for _ in range(6):
                            ws.receive_json()
                        ws.send_json({"type": "world_switch", "world_id": "empty-world"})
                        response = ws.receive_json()
                        ws.send_json({"type": "ping"})
                        pong = ws.receive_json()

            self.assertEqual("world_switch_failed", response["type"])
            self.assertIn("自动存档", response["message"])
            self.assertEqual("pong", pong["type"])

    def test_world_archive_marks_inactive_branch_and_emits_fallback_list(self):
        import server

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_root = Path(temp_dir)
            root, branch, service = self._make_local_branch_contexts(runtime_root)
            with (
                patch("src.app.engine.API_KEY", "test-api-key"),
                patch.object(server, "RUNTIME_ROOT", runtime_root),
                patch.object(server, "WORLD_BRANCHES", service),
            ):
                with TestClient(server.app) as client:
                    with client.websocket_connect("/ws") as ws:
                        ws.send_json({"type": "world_archive", "world_id": branch.world_id})
                        archived = self._receive_until(ws, "world_archived")

            self.assertEqual(branch.world_id, archived["world_id"])
            self.assertEqual(root.world_id, archived["fallback_world_id"])
            self.assertNotIn(
                branch.world_id,
                {item["world_id"] for item in archived["worlds"]},
            )
            with session_scope(branch.database_url) as session:
                world = session.get(World, branch.world_id)
                assert world is not None
                self.assertEqual("archived", world.status)
            with self.assertRaises(FileNotFoundError):
                service.open(branch.world_id)

    def test_world_archive_rejects_a_busy_target_without_releasing_its_lock(self):
        import server

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_root = Path(temp_dir)
            _root, branch, service = self._make_local_branch_contexts(runtime_root)
            target_lock = server._world_turn_lock(branch)
            target_lock.acquire()
            try:
                with (
                    patch("src.app.engine.API_KEY", "test-api-key"),
                    patch.object(server, "RUNTIME_ROOT", runtime_root),
                    patch.object(server, "WORLD_BRANCHES", service),
                ):
                    with TestClient(server.app) as client:
                        with client.websocket_connect("/ws") as ws:
                            ws.send_json({"type": "world_archive", "world_id": branch.world_id})
                            failed = self._receive_until(ws, "world_archive_failed")

                self.assertIn("正在处理", failed["message"])
                self.assertTrue(target_lock.locked())
                with session_scope(branch.database_url) as session:
                    world = session.get(World, branch.world_id)
                    assert world is not None
                    self.assertEqual("active", world.status)
            finally:
                if target_lock.locked():
                    target_lock.release()

    def test_preferred_archived_or_empty_branch_falls_back_to_local_world(self):
        import server

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_root = Path(temp_dir)
            root, archived_branch, service = self._make_local_branch_contexts(runtime_root)
            service.archive_branch(archived_branch.world_id, active_world_id=root.world_id)
            _root, empty_branch, _service = self._make_local_branch_contexts(
                runtime_root,
                branch_world_id="empty-branch",
                resumable=False,
            )

            with (
                patch("src.app.engine.API_KEY", "test-api-key"),
                patch.object(server, "RUNTIME_ROOT", runtime_root),
                patch.object(server, "WORLD_BRANCHES", service),
            ):
                with TestClient(server.app) as client:
                    for bad_world_id in (archived_branch.world_id, empty_branch.world_id):
                        with client.websocket_connect(
                            f"/ws?world_id={bad_world_id}&module=mansion_of_madness"
                        ) as ws:
                            initial = ws.receive_json()
                            self.assertEqual("module_list", initial["type"])
                            self.assertEqual(root.world_id, initial["world_id"])
                            self.assertEqual("mansion_of_madness", initial["module_name"])

    def test_empty_action_is_rejected_without_leaking_turn_lease(self):
        import server

        def completed_action(engine, content):
            engine.cb.on_narrative(f"已处理：{content}")
            engine.cb.on_done()

        with (
            patch("src.app.engine.API_KEY", "test-api-key"),
            patch.object(server.GameEngine, "handle_action", completed_action),
        ):
            with TestClient(server.app) as client:
                with client.websocket_connect("/ws") as ws:
                    for _ in range(6):
                        ws.receive_json()
                    ws.send_json({"type": "action", "content": "   "})
                    rejected = ws.receive_json()
                    ws.send_json({"type": "action", "content": "查看书桌"})
                    started = ws.receive_json()

        self.assertEqual("error", rejected["type"])
        self.assertIn("不能为空", rejected["message"])
        self.assertEqual("gm_turn_start", started["type"])

    def test_missing_resume_save_returns_error_and_session_stays_usable(self):
        import server

        with (
            patch("src.app.engine.API_KEY", "test-api-key"),
            patch.object(server.GameEngine, "load", return_value=None),
        ):
            with TestClient(server.app) as client:
                with client.websocket_connect("/ws") as ws:
                    for _ in range(6):
                        ws.receive_json()
                    ws.send_json({"type": "continue", "slot_id": "missing"})
                    error = ws.receive_json()
                    ws.send_json({"type": "ping"})
                    pong = ws.receive_json()

        self.assertEqual("error", error["type"])
        self.assertIn("未找到存档", error["message"])
        self.assertEqual("pong", pong["type"])

    def test_failed_save_does_not_terminate_shared_protocol_driver(self):
        import server

        with (
            patch("src.app.engine.API_KEY", "test-api-key"),
            patch.object(
                server.GameEngine,
                "save",
                side_effect=RuntimeError("simulated persistence failure"),
            ),
        ):
            with TestClient(server.app) as client:
                with client.websocket_connect("/ws") as ws:
                    for _ in range(6):
                        ws.receive_json()
                    ws.send_json({"type": "save", "manual": False})
                    error = ws.receive_json()
                    ws.send_json({"type": "ping"})
                    pong = ws.receive_json()

        self.assertEqual("error", error["type"])
        self.assertEqual("operation_failed", error["code"])
        self.assertEqual("save", error["operation"])
        self.assertNotIn("simulated persistence failure", error["message"])
        self.assertEqual("pong", pong["type"])

    def test_second_action_is_rejected_before_another_turn_starts(self):
        import server

        entered = threading.Event()
        release = threading.Event()

        def blocked_action(engine, content):
            entered.set()
            release.wait(timeout=3)
            engine.cb.on_narrative(f"已处理：{content}")
            engine.cb.on_done()

        with (
            patch("src.app.engine.API_KEY", "test-api-key"),
            patch.object(server.GameEngine, "handle_action", blocked_action),
        ):
            with TestClient(server.app) as client:
                with client.websocket_connect("/ws") as ws:
                    ws.send_json({"type": "action", "content": "第一项行动"})
                    first_start = None
                    for _ in range(8):
                        message = ws.receive_json()
                        if message.get("type") == "gm_turn_start":
                            first_start = message
                            break
                    self.assertIsNotNone(first_start)
                    self.assertEqual(first_start["type"], "gm_turn_start")
                    self.assertTrue(entered.wait(timeout=1))

                    ws.send_json({"type": "action", "content": "第二项行动"})
                    rejected = ws.receive_json()
                    self.assertEqual(rejected["type"], "turn_rejected")
                    self.assertEqual(rejected["reason"], "turn_in_progress")

                    release.set()
                    narrative = ws.receive_json()
                    done = ws.receive_json()

        self.assertEqual(narrative["type"], "narrative_chunk")
        self.assertEqual(done["type"], "done")
        self.assertEqual(narrative["turn_id"], first_start["turn_id"])
        self.assertEqual(done["turn_id"], first_start["turn_id"])

    def test_disconnect_cancels_old_turn_and_releases_world_for_new_session(self):
        import server

        first_entered = threading.Event()
        first_cancelled = threading.Event()
        call_count = 0
        call_guard = threading.Lock()

        def cancellable_action(engine, content):
            nonlocal call_count
            with call_guard:
                call_count += 1
                current_call = call_count
            if current_call == 1:
                first_entered.set()
                deadline = time.monotonic() + 3
                while (
                    not engine.turn_cancellation_requested()
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.01)
                if engine.turn_cancellation_requested():
                    first_cancelled.set()
                return
            engine.cb.on_narrative(f"已处理：{content}")
            engine.cb.on_done()

        with tempfile.TemporaryDirectory() as temp_dir:
            context = RuntimeContext.local(
                "mansion_of_madness",
                project_root=PROJECT_ROOT,
                runtime_root=Path(temp_dir),
            )
            world_lock = server._world_turn_lock(context)
            with (
                patch("src.app.engine.API_KEY", "test-api-key"),
                patch.object(server.RuntimeContext, "local", return_value=context),
                patch.object(server.GameEngine, "handle_action", cancellable_action),
            ):
                with TestClient(server.app) as client:
                    with client.websocket_connect("/ws") as first_ws:
                        for _ in range(6):
                            first_ws.receive_json()
                        first_ws.send_json({"type": "action", "content": "旧开场"})
                        self.assertEqual(
                            "gm_turn_start",
                            first_ws.receive_json()["type"],
                        )
                        self.assertTrue(first_entered.wait(timeout=1))

                    self.assertTrue(first_cancelled.wait(timeout=1))
                    deadline = time.monotonic() + 1
                    while world_lock.locked() and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertFalse(world_lock.locked())

                    with client.websocket_connect("/ws") as second_ws:
                        for _ in range(6):
                            second_ws.receive_json()
                        second_ws.send_json({"type": "action", "content": "新开场"})
                        started = second_ws.receive_json()
                        narrative = second_ws.receive_json()
                        done = second_ws.receive_json()

        self.assertEqual("gm_turn_start", started["type"])
        self.assertEqual("narrative_chunk", narrative["type"])
        self.assertEqual("done", done["type"])


if __name__ == "__main__":
    unittest.main()
