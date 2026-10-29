import json
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

import start
from src.ai.model.llm_concurrency import ModelResponseError
from src.app import game_loop as cli


@pytest.fixture
def terminal(tmp_path, monkeypatch):
    world = {"npcs": [{"id": "su", "name": "苏晚晴"}], "flags": {"already_played": True}}
    context = NS(runtime_root=tmp_path, world_id="cli-test", world_store=NS(load=lambda: world))
    engine = NS(
        narrative_model="test-story", judgement_model="test-judge",
        prepare_session=Mock(), list_saves=Mock(return_value=[]), reset=Mock(),
        handle_action=Mock(), load=Mock(return_value=2), save=Mock(side_effect=lambda slot: slot),
        messages=[{"role": "assistant", "content": "【npc:su】“门口的钟停了。”【/npc】"}],
        is_valid_npc_id=lambda npc_id: npc_id == "su",
        cancel_active_turn=Mock(), finish_turn_record=Mock(), clear_turn_cancellation=Mock(),
    )
    monkeypatch.setattr(cli.RuntimeContext, "from_env", lambda: context)
    monkeypatch.setattr(cli, "GameEngine", lambda ctx: engine)
    monkeypatch.setattr(cli, "API_KEY", "test-key")
    monkeypatch.setattr(cli, "apply_pending_routes", Mock())
    return engine, world


def inputs(monkeypatch, values):
    monkeypatch.setattr("builtins.input", Mock(side_effect=values))


def test_opening_rejection_stays_in_cli_and_explicit_retry_succeeds(terminal, monkeypatch, capsys):
    engine, _ = terminal

    def succeed(action):
        engine.cb.on_narrative("“请听我说完。”", "su")
        engine.cb.on_game_over("neutral", "故事暂告一段落", "你收起调查记录。")

    calls = []

    def handle(action=None):
        calls.append(action)
        if len(calls) == 1:
            raise ModelResponseError("private provider failure")
        succeed(action)

    engine.handle_action.side_effect = handle
    inputs(monkeypatch, ["/help", "/retry", "/quit"])
    assert cli.game_loop() == 0
    out = capsys.readouterr().out
    assert "/retry" in out and "苏晚晴" in out and "故事暂告一段落" in out
    assert "Traceback" not in out and "private provider failure" not in out
    assert calls == [None, None]


def test_failed_action_retry_keeps_the_exact_player_input(terminal, monkeypatch):
    engine, _ = terminal
    engine.handle_action.side_effect = [None, ModelResponseError("bad attribution"), None]
    inputs(monkeypatch, ["我向门房询问钟声。", "/retry", "/quit"])
    assert cli.game_loop() == 0
    assert [call.args for call in engine.handle_action.call_args_list] == [(None,), ("我向门房询问钟声。",), ("我向门房询问钟声。",)]


def test_starting_with_a_save_does_not_reset_or_call_a_model(terminal, monkeypatch, capsys):
    engine, world = terminal
    engine.list_saves.return_value = [{"id": "slot_001"}]
    inputs(monkeypatch, ["/load slot_001", "/quit"])
    assert cli.game_loop() == 0
    engine.reset.assert_not_called()
    engine.handle_action.assert_not_called()
    engine.load.assert_called_once_with("slot_001")
    assert world["flags"]["already_played"]
    assert "门口的钟停了" in capsys.readouterr().out


def test_save_new_allocates_a_distinct_manual_slot(terminal, monkeypatch):
    engine, _ = terminal
    monkeypatch.setattr(cli, "_next_slot", lambda context: "slot_001")
    inputs(monkeypatch, ["/save", "/save-new", "/quit"])
    assert cli.game_loop() == 0
    assert [call.args[0] for call in engine.save.call_args_list] == ["slot_000", "slot_001", "slot_000"]


def test_eof_with_failed_save_exits_instead_of_retrying_forever(terminal, monkeypatch):
    engine, _ = terminal
    engine.save.side_effect = PermissionError("disk full")
    inputs(monkeypatch, [EOFError()])
    assert cli.game_loop() == 1
    engine.save.assert_called_once()


def test_cancel_generation_can_retry_from_the_prompt(terminal, monkeypatch):
    engine, _ = terminal
    engine.handle_action.side_effect = [KeyboardInterrupt(), None]
    inputs(monkeypatch, ["/retry", "/quit"])
    assert cli.game_loop() == 0
    engine.cancel_active_turn.assert_called_once()
    engine.clear_turn_cancellation.assert_called_once()
    assert engine.finish_turn_record.call_args.kwargs["status"] == "cancelled"


def test_local_custom_roles_allow_cli_without_an_environment_key(terminal, tmp_path, monkeypatch):
    engine, _ = terminal
    monkeypatch.setattr(cli, "API_KEY", "")
    # The actual resolver remains used; only its file content is a fixture.
    service = {"label": "local", "provider_kind": "openai_compatible", "base_url": "http://127.0.0.1:9999/v1",
               "api_key": "test-custom", "model_id": "local-model"}
    (tmp_path / "model_settings.local.json").write_text(json.dumps({
        "narrative": {"mode": "custom", "service": service},
        "judgement": {"mode": "custom", "service": service}}))
    inputs(monkeypatch, ["/quit"])
    assert cli.game_loop() == 0
    cli.apply_pending_routes.assert_called_once_with(engine)


def test_launcher_respects_active_python_role_models_and_child_exit(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(start, "load_config", lambda: {"api_key": "test-config-secret", "narrative_model": "story-id", "judgement_model": "judge-id", "glm_base_url": "https://summary.invalid/v1"})
    monkeypatch.setattr(start.os, "chdir", lambda root: None)
    monkeypatch.setattr(start.sys, "prefix", "active-venv")
    monkeypatch.setattr(start.sys, "base_prefix", "base")
    monkeypatch.setattr(start, "PYTHON_EXE", "active-python")
    monkeypatch.delenv("TRPG_NARRATIVE_MODEL", raising=False)
    monkeypatch.delenv("TRPG_JUDGEMENT_MODEL", raising=False)
    child = Mock(return_value=NS(returncode=7))
    monkeypatch.setattr(start.subprocess, "run", child)
    assert start.run_game() == 7
    assert child.call_args.args[0][0] == "active-python"
    env = child.call_args.kwargs["env"]
    assert env["TRPG_NARRATIVE_MODEL"] == "story-id" and env["TRPG_JUDGEMENT_MODEL"] == "judge-id"
    assert "test-config-secret" not in capsys.readouterr().out
