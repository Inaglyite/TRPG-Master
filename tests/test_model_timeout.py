"""模型调用超时（TRPG_MODEL_TIMEOUT）的行为测试。"""

from __future__ import annotations

import src.ai.model.llm as llm_module
import src.app.config as config
import src.app.engine as engine_module


def test_model_timeout_default_matches_sdk_behavior(monkeypatch) -> None:
    monkeypatch.delenv("TRPG_MODEL_TIMEOUT", raising=False)
    assert config.model_timeout_seconds() == 600.0


def test_model_timeout_parses_and_clamps_environment(monkeypatch) -> None:
    monkeypatch.setenv("TRPG_MODEL_TIMEOUT", "120")
    assert config.model_timeout_seconds() == 120.0
    monkeypatch.setenv("TRPG_MODEL_TIMEOUT", "0.5")
    assert config.model_timeout_seconds() == 1.0
    monkeypatch.setenv("TRPG_MODEL_TIMEOUT", "99999")
    assert config.model_timeout_seconds() == 3600.0
    monkeypatch.setenv("TRPG_MODEL_TIMEOUT", "not-a-number")
    assert config.model_timeout_seconds() == 600.0


def test_engine_openai_client_uses_configured_timeout(monkeypatch, tmp_path) -> None:
    captured: dict = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(engine_module, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(engine_module, "TurnJournal", lambda *args, **kwargs: None)
    monkeypatch.setenv("TRPG_MODEL_TIMEOUT", "77")
    context = engine_module.RuntimeContext.local(runtime_root=tmp_path)
    engine_module.GameEngine(context)
    assert captured.get("timeout") == 77.0


def test_llm_glm_client_uses_configured_timeout(monkeypatch) -> None:
    captured: dict = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(llm_module, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(llm_module, "GLM_API_KEY", "test-key")
    monkeypatch.setenv("TRPG_MODEL_TIMEOUT", "123")
    llm_module._glm_client = None
    llm_module._get_glm()
    assert captured.get("timeout") == 123.0
