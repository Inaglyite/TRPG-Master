import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.config import PROJECT_ROOT
from src.runtime import RuntimeContext


class WebSocketTurnGateTests(unittest.TestCase):
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
            patch("src.engine.API_KEY", "test-api-key"),
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
                patch("src.engine.API_KEY", "test-api-key"),
                patch.object(server.RuntimeContext, "local", return_value=context),
                patch.object(server.GameEngine, "handle_action", cancellable_action),
            ):
                with TestClient(server.app) as client:
                    with client.websocket_connect("/ws") as first_ws:
                        for _ in range(5):
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
                        for _ in range(5):
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
