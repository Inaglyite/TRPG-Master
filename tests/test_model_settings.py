import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.engine import GameEngine
from src.model_settings import (
    ModelSettings,
    persist_model_settings,
    validate_model_id,
)


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
            env_path = Path(temp_dir) / ".env.json"
            initial = ModelSettings.validated("story-before", "judge-before")
            with (
                patch.object(server, "_ENV_FILE", env_path),
                patch.object(server, "_active_model_settings", initial),
                patch("src.engine.API_KEY", "test-api-key"),
            ):
                with TestClient(server.app) as client:
                    with client.websocket_connect("/ws") as ws:
                        messages = [ws.receive_json() for _ in range(5)]
                        current = next(
                            message
                            for message in messages
                            if message.get("type") == "model_settings"
                        )
                        self.assertEqual(current["narrative_model"], "story-before")

                        ws.send_json(
                            {
                                "type": "model_settings_update",
                                "narrative_model": "story-after",
                                "judgement_model": "judge-after",
                            }
                        )
                        updated = ws.receive_json()

            saved = json.loads(env_path.read_text(encoding="utf-8"))

        self.assertTrue(updated["saved"])
        self.assertEqual(updated["judgement_model"], "judge-after")
        self.assertEqual(saved["narrative_model"], "story-after")
        self.assertEqual(saved["judgement_model"], "judge-after")


if __name__ == "__main__":
    unittest.main()
