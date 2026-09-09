import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.ai.model.model_settings import (
    ModelSettings,
    persist_model_settings,
    validate_model_id,
)
from src.app.engine import GameEngine


class ModelSettingsTests(unittest.TestCase):
    def test_accepts_provider_model_ids_and_rejects_unsafe_values(self):
        self.assertEqual(
            validate_model_id("provider/deepseek-v4-pro:latest", "模型"),
            "provider/deepseek-v4-pro:latest",
        )
        for invalid in ("", "model name", "model\nother", "模型", "a" * 121):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    validate_model_id(invalid, "模型")

    def test_persistence_preserves_credentials(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / ".env.json"
            path.write_text(
                json.dumps(
                    {
                        "api_key": "secret",
                        "base_url": "https://api.example.test",
                    }
                ),
                encoding="utf-8",
            )
            settings = ModelSettings.validated("story-model", "judge-model")

            persist_model_settings(path, settings)
            saved = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(saved["api_key"], "secret")
        self.assertEqual(saved["base_url"], "https://api.example.test")
        self.assertEqual(saved["narrative_model"], "story-model")
        self.assertEqual(saved["judgement_model"], "judge-model")

    def test_engine_configuration_applies_on_next_request(self):
        engine = GameEngine.__new__(GameEngine)
        engine.narrative_model = "old-story"
        engine.judgement_model = "old-judge"
        engine.current_model = "old-story"

        result = engine.configure_models("new-story", "new-judge")

        self.assertEqual(result["narrative_model"], "new-story")
        self.assertEqual(engine.judgement_model, "new-judge")
        self.assertEqual(engine.current_model, "new-story")

    def test_websocket_update_persists_and_echoes_active_models(self):
        import server

        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "model_settings.local.json"
            with (
                patch.object(server, "_LOCAL_MODEL_SETTINGS_FILE", settings_path),
                patch("src.app.engine.API_KEY", "test-api-key"),
            ):
                with TestClient(server.app) as client:
                    with client.websocket_connect("/ws") as ws:
                        messages = [ws.receive_json() for _ in range(6)]
                        current = next(
                            message
                            for message in messages
                            if message.get("type") == "model_settings"
                        )
                        # 新合同：默认绑定 + 作用域/权限/版本字段
                        self.assertEqual(current["mode"], "local")
                        self.assertTrue(current["can_edit"])
                        self.assertEqual(current["narrative"]["mode"], "default")

                        # 未确认数据发送时拒绝保存自定义服务
                        ws.send_json(
                            {
                                "type": "model_settings_update",
                                "narrative": {
                                    "mode": "custom",
                                    "service": {
                                        "provider_kind": "openai_compatible",
                                        "base_url": "http://127.0.0.1:11434/v1",
                                        "api_key": "sk-local-test",
                                        "model_id": "qwen3:32b",
                                    },
                                },
                                "judgement": {"mode": "default"},
                            }
                        )
                        rejected = ws.receive_json()
                        self.assertEqual(rejected["type"], "model_settings_error")
                        self.assertIn("确认", rejected["message"])

                        ws.send_json(
                            {
                                "type": "model_settings_update",
                                "narrative": {
                                    "mode": "custom",
                                    "service": {
                                        "provider_kind": "openai_compatible",
                                        "base_url": "http://127.0.0.1:11434/v1",
                                        "api_key": "sk-local-test",
                                        "model_id": "qwen3:32b",
                                        "window_tokens": 32768,
                                    },
                                },
                                "judgement": {"mode": "default"},
                                "confirm_data_sharing": True,
                            }
                        )
                        updated = ws.receive_json()

            saved = json.loads(settings_path.read_text(encoding="utf-8"))

        self.assertTrue(updated["saved"])
        self.assertIn("下一回合", updated["notice"])
        self.assertEqual(updated["narrative"]["mode"], "custom")
        self.assertEqual(updated["narrative"]["model_id"], "qwen3:32b")
        self.assertEqual(updated["narrative"]["window_source"], "manual")
        # 凭据不回显：payload 只有 has_key，无任何 key 文本
        self.assertTrue(updated["narrative"]["service"]["has_key"])
        self.assertNotIn("sk-local-test", json.dumps(updated, ensure_ascii=False))
        # 本地文件持久化（下回合生效的路由来源）
        self.assertEqual(saved["narrative"]["service"]["api_key"], "sk-local-test")
        self.assertEqual(saved["narrative"]["service"]["model_id"], "qwen3:32b")
        self.assertEqual(saved["judgement"]["mode"], "default")


if __name__ == "__main__":
    unittest.main()
