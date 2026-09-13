"""M1 真机冒烟：真实 server.app + TestClient WebSocket 跑结构化闭环。

不启动生产、不触网：TestClient 进程内跑 ASGI；世界建在 conftest 强制的
会话级临时 runtime root。验证 server.py 的真实接线（路由注册、快照下发、
旧回合门禁），而非 mock 的网关。
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from src.app.config import AUTO_SAVE_SLOT, PROJECT_ROOT
from src.app.runtime import RuntimeContext
from src.storage.database import World, WorldState, session_scope
from src.storage.persistence import save_game


class StructuredWsSmokeTests(unittest.TestCase):
    def setUp(self):
        runtime_root = Path(os.environ["TRPG_RUNTIME_ROOT"])
        self.context = RuntimeContext.create(
            "smoke-structured-1",
            "mansion_of_madness",
            project_root=PROJECT_ROOT,
            runtime_root=runtime_root,
        )
        # 该模组初始状态没有 scene_catalog：补成结构化可移动的形态（状态即权威）。
        with session_scope(self.context.database_url) as session:
            row = session.get(WorldState, "smoke-structured-1")
            state = dict(row.state)
            hall = {"id": "entrance_hall", "name": "门厅", "exits": ["grand_staircase"]}
            stairs = {"id": "grand_staircase", "name": "大楼梯", "exits": ["entrance_hall"]}
            state["scene_catalog"] = {"entrance_hall": hall, "grand_staircase": stairs}
            state["current_scene"] = dict(hall)
            row.state = state
            world = session.get(World, "smoke-structured-1")
            world.metadata_json = {
                **(world.metadata_json or {}),
                "execution_profile": "structured_v1",
                "keeper_mode": "human",
            }
        # /ws 直连某世界需要它可恢复（旧协议的一刀切导入约定）。
        save_game(
            [{"role": "assistant", "content": "结构化冒烟。"}],
            AUTO_SAVE_SLOT,
            context=self.context,
        )

    def _receive_until(self, ws, message_type: str, limit: int = 20) -> dict:
        for _ in range(limit):
            payload = ws.receive_json()
            if payload.get("type") == message_type:
                return payload
        self.fail(f"没有收到 {message_type} 协议帧")

    def test_structured_world_smoke_over_real_ws(self):
        import server

        with TestClient(server.app) as client:
            with client.websocket_connect("/ws?world_id=smoke-structured-1") as ws:
                snapshot = self._receive_until(ws, "session_snapshot")
                self.assertEqual("smoke-structured-1", snapshot["world_id"])
                payload = snapshot["payload"]
                self.assertEqual("structured_v1", payload["execution_profile"])
                self.assertEqual("human", payload["keeper_mode"])
                self.assertTrue(payload["server_capabilities"]["structured_protocol"])
                self.assertEqual("entrance_hall", payload["scene"]["id"])

                # 玩家行动请求：排队 + ack + keeper 待办（本地操作者两顶帽子）
                ws.send_json(
                    {
                        "type": "action_request",
                        "protocol_version": 1,
                        "world_id": "smoke-structured-1",
                        "expected_revision": payload["revision"],
                        "investigator_id": "pc",
                        "request_id": "smoke-req-1",
                        "action": {
                            "kind": "move",
                            "destination_scene_id": "grand_staircase",
                        },
                    }
                )
                ack = self._receive_until(ws, "action_ack")
                self.assertEqual("queued", ack["payload"]["status"])
                self._receive_until(ws, "intent_pending")

                # 旧文字回合通道在结构化世界关闭
                ws.send_json({"type": "action", "content": "我直接走上楼梯"})
                error = self._receive_until(ws, "error")
                self.assertEqual("structured_required", error.get("code"))

                # 主持命令：移动队伍（本地操作者有 keeper 授权）
                ws.send_json(
                    {
                        "type": "command_request",
                        "protocol_version": 1,
                        "command_id": "smoke-cmd-1",
                        "world_id": "smoke-structured-1",
                        "expected_revision": ack["revision"],
                        "kind": "move_party",
                        "payload": {
                            "destination_scene_id": "grand_staircase",
                            "travel_minutes": 3,
                        },
                        "cause_id": "smoke-req-1",
                    }
                )
                scene_changed = self._receive_until(ws, "scene_changed")
                self.assertEqual(
                    "grand_staircase", scene_changed["payload"]["scene"]["id"]
                )
                status = self._receive_until(ws, "action_status")
                self.assertEqual("smoke-cmd-1", status["payload"]["request_id"])
                self.assertEqual("completed", status["payload"]["status"])

                # 不合协议帧：request_error（真实 event_id，可去重）
                ws.send_json(
                    {
                        "type": "action_request",
                        "protocol_version": 1,
                        "world_id": "smoke-structured-1",
                        "expected_revision": scene_changed["revision"],
                        "investigator_id": "pc",
                        "request_id": "smoke-req-bad",
                        "action": {"kind": "explode"},
                    }
                )
                request_error = self._receive_until(ws, "request_error")
                self.assertEqual("invalid_action", request_error["payload"]["code"])
                self.assertGreater(request_error["event_id"], 0)


if __name__ == "__main__":
    unittest.main()
