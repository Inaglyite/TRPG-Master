import copy
import json
from types import SimpleNamespace

import pytest

from src.ai.tools.registry import execute_function, tool_catalog_for_names
from src.ai.tools.tool_pipeline import ToolPipeline, tool_descriptor_for
from src.app.engine import GameEngine
from src.app.turn_narration_fallback import narrate_settled_tools
from src.gameplay.turn_mutations import TurnMutationLedger
from src.gameplay.turn_state_repair import execute_turn_state_tool, freeze_turn_state
from src.storage.world_store import StaleRevisionError
from tests.test_tool_pipeline import _model_call


class Store:
    def __init__(self, state):
        self.state = copy.deepcopy(state)

    def load(self):
        return copy.deepcopy(self.state)

    def update(self, mutate, *, expected_revision=None):
        if expected_revision != self.state["revision"]:
            raise StaleRevisionError(expected_revision, self.state["revision"])
        working = self.load()
        mutate(working)
        working["revision"] += 1
        self.state = working


def engine():
    host = GameEngine.__new__(GameEngine)
    state = {
        "revision": 1,
        "current_scene": {"id": "office", "name": "报馆", "exits": ["shop"], "npcs_present": ["su", "evelyn"]},
        "scene_catalog": {"office": {"id": "office", "name": "报馆", "exits": ["shop"]}},
        "npcs": [{"id": "su", "name": "苏晚晴", "secret": "private-a"},
                 {"id": "evelyn", "name": "伊芙琳", "secret": "private-b"},
                 {"id": "jin", "name": "金三爷", "secret": "private-c"}],
        "pc": {"hp": 10, "san": 60, "inventory": ["钥匙"]},
        "flags": {"investigated": False}, "clues_found": {},
        "pending_departure": {"origin_scene_id": "office", "destination_scene_id": "shop", "question": "是否同行？"},
        "world_clock": {"elapsed_minutes": 0},
    }
    host.context = SimpleNamespace(world_store=Store(state), world_id="repair-world")
    host._active_turn_id = "repair-turn"
    host._action_resolution = SimpleNamespace(phase="departure")
    host._turn_mutations = TurnMutationLedger()
    host.raise_if_turn_cancelled = lambda: None
    host.record_tool_pipeline_outcome = lambda *_: None
    freeze_turn_state(host)
    return host


def call(host, name="inspect_turn_state", **args):
    return json.loads(execute_turn_state_tool(host, name, args))


def test_inspection_only_returns_present_names_and_no_private_fields():
    host = engine()
    before = host.context.world_store.load()
    result = call(host)
    assert result["phase"] == "departure" and result["conflicts"] == []
    assert [npc["id"] for npc in result["speakers"]] == ["su", "evelyn"]
    assert "private-" not in json.dumps(result)
    assert host.context.world_store.load() == before


def test_repair_restores_settled_scene_without_new_movement_or_effects():
    host = engine()
    store = host.context.world_store
    before = store.load()
    store.state["current_scene"] = {"id": "shop", "name": "藏宝楼", "npcs_present": ["jin"]}
    view = call(host)
    assert set(view["conflicts"]) == {"id", "name", "exits", "npcs_present"}
    repaired = call(host, "repair_turn_state", snapshot_token=view["snapshot_token"], reason="回合仍在等待同行选择，场景投影提前移动")
    assert repaired["status"] == "repaired"
    after = store.load()
    assert after["current_scene"] == before["current_scene"]
    assert {k: v for k, v in after.items() if k != "revision"} == {k: v for k, v in before.items() if k != "revision"}
    assert host._turn_mutations.entries[-1]["name"] == "scene_projection_repair"
    assert not call(host)["conflicts"]


@pytest.mark.parametrize("change", ["revision", "scene", "new_turn"])
def test_stale_snapshot_is_rejected_without_write(change):
    host = engine()
    view = call(host)
    if change == "revision":
        host.context.world_store.state["revision"] += 1
    elif change == "scene":
        host.context.world_store.state["current_scene"]["name"] = "错名"
    else:
        host._action_resolution.phase = "arrival"
        freeze_turn_state(host)
    before = host.context.world_store.load()
    result = call(host, "repair_turn_state", snapshot_token=view["snapshot_token"], reason="修正")
    assert result["error"] == "stale_state_snapshot"
    assert host.context.world_store.load() == before


def test_consistent_world_is_noop_and_does_not_increment_revision():
    host = engine()
    view = call(host)
    result = call(host, "repair_turn_state", snapshot_token=view["snapshot_token"], reason="叙事写错了地点")
    assert result["status"] == "unchanged"
    assert host.context.world_store.load()["revision"] == 1


def test_repair_catalog_disallows_model_chosen_values_and_uses_turn_cache():
    catalog = tool_catalog_for_names(["inspect_turn_state", "repair_turn_state"])
    assert len(catalog) == 2
    schema = next(tool["function"]["parameters"] for tool in catalog if tool["function"]["name"] == "repair_turn_state")
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"snapshot_token", "reason"}
    assert tool_descriptor_for("repair_turn_state", timeout_ms=5000).mutation_mode == "turn_cache"
    assert tool_descriptor_for("inspect_turn_state", timeout_ms=5000).mutation_mode == "read_only"


def test_pipeline_rejects_unissued_or_extra_state_values():
    host = engine()
    pipeline = ToolPipeline(host, timeout_ms=5000)
    view = call(host)
    args = {"snapshot_token": view["snapshot_token"], "reason": "修正", "scene_id": "shop", "hp": 999}
    result = pipeline.execute(_model_call(host, "forged", "repair_turn_state", json.dumps(args)))
    assert result.status == "denied"
    assert host.context.world_store.load()["current_scene"]["id"] == "office"


def test_issued_calls_repair_and_retry_without_duplicate_mutation():
    host = engine()
    host.context.world_store.state["current_scene"]["npcs_present"] = ["jin"]
    pipeline = ToolPipeline(host, timeout_ms=5000)
    inspected = pipeline.execute(_model_call(host, "inspect", "inspect_turn_state", "{}"))
    view = json.loads(inspected.output)
    args = json.dumps({"snapshot_token": view["snapshot_token"], "reason": "旧在场角色残留"})
    first = pipeline.execute(_model_call(host, "repair", "repair_turn_state", args))
    retry = pipeline.execute(_model_call(host, "retry", "repair_turn_state", args))
    assert json.loads(first.output)["status"] == "repaired"
    assert retry.status == "reused"
    assert host.context.world_store.load()["revision"] == 2


def test_registry_alone_cannot_write_without_active_engine():
    host = engine()
    result = json.loads(execute_function("repair_turn_state", {"snapshot_token": "a" * 64, "reason": "修正"}, context=host.context))
    assert result["error"] == "active_turn_engine_required"


def test_missing_checkpoint_and_empty_reason_are_rejected():
    host = engine()
    view = call(host)
    assert call(host, "repair_turn_state", snapshot_token=view["snapshot_token"], reason=" ")["error"] == "repair_reason_required"
    del host._turn_state_checkpoint
    assert call(host)["error"] == "no_settled_turn_checkpoint"


def test_checkpoint_from_previous_turn_is_unusable():
    host = engine()
    host._active_turn_id = "next-turn"
    assert call(host)["error"] == "no_settled_turn_checkpoint"


def test_tool_budget_closes_with_narration_and_no_more_effects():
    requests = []
    host = SimpleNamespace(messages=[], current_model="narrator", _combat_active=lambda: False)
    host._stream_llm = lambda *args, **kwargs: (requests.append(kwargs) or "已结算的枪声渐渐散去。", [])
    result = narrate_settled_tools({"engine": host})
    assert result["text"] and not result["tool_calls"]
    assert requests[0]["enable_tools"] is False


@pytest.mark.parametrize("output", [("", []), ("正文", [{"id": "extra-call"}])])
def test_tool_budget_cannot_commit_an_empty_or_extra_tool_response(output):
    host = SimpleNamespace(messages=[], current_model="narrator", _combat_active=lambda: False,
                           _stream_llm=lambda *_a, **_k: output)
    with pytest.raises(RuntimeError, match="最终叙述"):
        narrate_settled_tools({"engine": host})


def test_unexecuted_combat_cannot_close_with_only_narration():
    host = SimpleNamespace(_combat_active=lambda: True,
                           _action_resolution=SimpleNamespace(adjudication_json='{"intent":"combat"}'),
                           _tool_pipeline_ledger=SimpleNamespace(outcomes=[]))
    with pytest.raises(RuntimeError, match="战斗行动仍未结算"):
        narrate_settled_tools({"engine": host})


def test_resolved_check_catalog_retains_repair_but_no_dice_or_inventory(monkeypatch):
    from src.ai.model.model_request import StreamPolicy, prepare_model_request

    host = engine()
    host.messages = [{"role": "system", "content": "遵守执行回执。"}, {"role": "user", "content": "查看书桌。"}]
    host._repair_only_tools = True
    monkeypatch.setattr("src.ai.skills.skill_activation.loadable_skill_allowlist", lambda _: ())
    monkeypatch.setattr("src.ai.skills.skill_activation.request_tool_policy", lambda _: (frozenset({"skill_check"}), frozenset({"skill_check"})))
    request = prepare_model_request(host, "fixture", policy=StreamPolicy(dynamic_tools=True, stream_usage=False, prompt_profile="hybrid", thinking_type=None), system_overlay=None, system_prompt_override=None, enable_tools=True, temperature=0.5, messages_override=None)
    assert {t["function"]["name"] for t in request.request_tools} == {"inspect_turn_state", "repair_turn_state"}
