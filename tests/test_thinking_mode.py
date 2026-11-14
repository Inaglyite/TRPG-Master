"""Thinking is persisted and frozen per turn, independently of its display."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_provider_adapter import _chunk, _host, _stream

from src.ai.model import route_service
from src.ai.model.provider_adapter import apply_reasoning_passback
from src.ai.model.thinking_policy import parse_thinking_mode, thinking_for_host
from src.app.settings_service import SettingsScope, apply_update, build_payload


def test_local_thinking_toggle_roundtrip_and_turn_boundary(tmp_path):
    path = tmp_path / "models.json"
    scope = SettingsScope(mode="local", can_edit=True, local_path=path)
    initial = route_service.default_settings()
    route_service.save_local_settings(path, initial)
    engine = SimpleNamespace(route_resolver=lambda: route_service.load_local_settings(path))
    route_service.apply_pending_routes(engine)
    result = apply_update(scope, {"thinking_mode": "enabled"})
    assert result["thinking_enabled"] and result["thinking_mode"] == "enabled"
    assert thinking_for_host(engine, "story", "disabled") == "disabled"
    route_service.apply_pending_routes(engine)
    assert thinking_for_host(engine, "story", "disabled") == "enabled"
    apply_update(scope, {"thinking_mode": "disabled"})
    assert thinking_for_host(engine, "story", "disabled") == "enabled"
    route_service.apply_pending_routes(engine)
    assert thinking_for_host(engine, "story", "enabled") == "disabled"
    assert build_payload(scope)["thinking_mode"] == "disabled"


def test_toggle_preserves_bindings_and_rejects_bad_modes(tmp_path):
    path = tmp_path / "models.json"
    scope = SettingsScope(mode="local", can_edit=True, local_path=path)
    saved = replace(route_service.default_settings(), revision=5)
    route_service.save_local_settings(path, saved)
    apply_update(scope, {"thinking_mode": "enabled"})
    loaded = route_service.load_local_settings(path)
    assert loaded.narrative == saved.narrative and loaded.judgement == saved.judgement
    assert loaded.revision == 6
    for invalid in [True, None, {}, "yes"]:
        with pytest.raises(ValueError):
            parse_thinking_mode(invalid)
    with pytest.raises(PermissionError):
        apply_update(replace(scope, can_edit=False), {"thinking_mode": "enabled"})
    assert route_service.load_local_settings(path) == loaded


def test_non_deepseek_route_does_not_receive_deepseek_switch():
    other = SimpleNamespace(provider_kind="openai", base_url_host="api.openai.com")
    host = SimpleNamespace(
        _model_routes=SimpleNamespace(narrative=other, judgement=other, thinking_mode="enabled")
    )
    assert thinking_for_host(host, "story", "disabled") is None


def test_thinking_passback_includes_text_only_assistant_without_public_leak():
    host = _host(
        [_chunk(reasoning="完整私有思考"), _chunk(content="公开回答。"), _chunk(finish="stop")]
    )
    text, calls = _stream(host)
    messages = [
        {"role": "assistant", "content": "旧的非思考回答"},
        {"role": "assistant", "content": text},
    ]
    wire = apply_reasoning_passback(host, messages, complete=True)
    assert not calls
    assert wire[0]["reasoning_content"] == ""
    assert wire[1]["reasoning_content"] == "完整私有思考"
    assert all("reasoning_content" not in message for message in messages)
