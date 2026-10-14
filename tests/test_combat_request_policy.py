import json
from types import SimpleNamespace

import pytest

from src.ai.model.combat_request_policy import needs_combat_tool
from src.ai.tools.registry import tool_catalog_for_names


def host(intent="combat", outcomes=()):
    return SimpleNamespace(_action_resolution=SimpleNamespace(adjudication_json=json.dumps({"intent": intent})),
                           _tool_pipeline_ledger=SimpleNamespace(outcomes=outcomes))


TOOLS = tool_catalog_for_names(["combat_action", "combat_status", "combat_end"])


def test_unexecuted_combat_intent_requires_a_tool_and_readonly_status_is_insufficient():
    assert needs_combat_tool(host(), "combat", "disabled", TOOLS)
    status = SimpleNamespace(name="combat_status", output='{"ok":true,"event":"combat_status"}')
    assert needs_combat_tool(host(outcomes=[status]), "combat", "disabled", TOOLS)


@pytest.mark.parametrize("output", ['{"ok":false}', '{"ok":true,"event":"decision_required"}', 'invalid'])
def test_denied_or_unresolved_actions_do_not_authorize_fictitious_narration(output):
    outcome = SimpleNamespace(name="combat_action", output=output)
    assert needs_combat_tool(host(outcomes=[outcome]), "combat", "disabled", TOOLS)


@pytest.mark.parametrize("name,event", [("combat_action", "action_resolved"), ("combat_end", "combat_ended")])
def test_executed_mechanics_release_final_narration(name, event):
    outcome = SimpleNamespace(name=name, output=json.dumps({"ok": True, "event": event}))
    assert not needs_combat_tool(host(outcomes=[outcome]), "combat", "disabled", TOOLS)


def test_questions_story_and_thinking_routes_do_not_force_an_action():
    assert not needs_combat_tool(host("clarify"), "combat", "disabled", TOOLS)
    assert not needs_combat_tool(host(), "story", "disabled", TOOLS)
    assert not needs_combat_tool(host(), "combat", "enabled", TOOLS)
    assert not needs_combat_tool(host(), "combat", None, TOOLS)
    assert not needs_combat_tool(host(), "combat", "disabled", [])
