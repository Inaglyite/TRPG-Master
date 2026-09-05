"""Counterexamples found in the rule/engine authority audit."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.ai.tools.registry import _roll_check
from src.gameplay.combat import CombatError, _roll_damage, start_combat
from src.gameplay.combat_authority import authorize_combat_proposal
from src.gameplay.percentile import choose_percentile


class MemoryStore:
    def __init__(self, state):
        self.state = copy.deepcopy(state)

    def load(self):
        return copy.deepcopy(self.state)

    def update(self, mutation):
        working = self.load()
        mutation(working)
        self.state = working


def test_percentile_zero_boundary_is_compared_as_one_hundred():
    assert choose_percentile([1, 0], 0)[0] == 10
    assert choose_percentile([0, 9], 0, penalty=True)[0] == 100


def test_unknown_skill_is_rejected_before_any_roll():
    context = SimpleNamespace(world_store=MemoryStore({"pc": {"skills": {}}}))
    with patch("src.gameplay.resolution.random.randint") as random:
        result = json.loads(_roll_check(context, "invented_skill"))
    assert result["error"] == "unknown_skill"
    random.assert_not_called()


def test_regular_success_does_not_complete_a_hard_task():
    context = SimpleNamespace(
        world_store=MemoryStore({"pc": {"skills": {"locksmith": 60}}}),
        project_root=Path(__file__).resolve().parents[1],
    )
    with patch("src.gameplay.resolution.random.randint", side_effect=[4, 0]):
        result = json.loads(_roll_check(context, "locksmith", required_success_level="hard"))
    assert result["level"] == "regular_success"
    assert result["success"] is False


def test_push_without_failed_context_is_rejected():
    context = SimpleNamespace(world_store=MemoryStore({"pc": {"skills": {"fighting_brawl": 60}}}))
    assert json.loads(_roll_check(context, "fighting_brawl", push=True))["ok"] is False


def combat_world():
    return {
        "pc": {"hp": 12, "attributes": {"DEX": 80}, "inventory": ["左轮手枪（6发）"]},
        "npcs": [{"id": "guard", "hp": 10, "attributes": {"DEX": 70, "CON": 60}, "skills": {"fighting_brawl": 65}}],
    }


def test_existing_npc_stats_cannot_be_overridden():
    world = combat_world()
    start_combat(world, [{"id": "guard", "dex": 1, "con": 999, "fighting_brawl": 999}])
    guard = next(p for p in world["combat_state"]["participants"] if p["id"] == "guard")
    assert (guard["dex"], guard["con"], guard["skills"]["fighting_brawl"]) == (70, 60, 65)


def test_model_damage_is_replaced_by_held_weapon_profile():
    args = authorize_combat_proposal(combat_world(), "combat_action", {
        "actor_id": "pc", "target_id": "guard", "action_type": "firearm", "damage_spec": "1d2+999999",
    })
    assert args["damage_spec"] == "1d8"
    assert args["skill"] == "firearms_handgun"


def test_damage_modifier_is_bounded_even_for_internal_calls():
    with pytest.raises(CombatError):
        _roll_damage("1d2+999999", SimpleNamespace(randint=lambda *_: 1))


def test_sanity_exposure_and_daily_treatment_are_persistent():
    from tools import sanity

    state = {"pc": {"san": 60, "skills": {"psychoanalysis": 80}}, "current_scene": {"id": "street"}}
    with patch.object(sanity, "_TRANSACTION_STATE", state), patch.object(sanity.random, "randint", return_value=2):
        first = sanity.apply_sanity_loss("1/1", source="body", exposure_id="body_seen")
        duplicate = sanity.apply_sanity_loss("1/1", source="renamed body", exposure_id="body_seen")
        assert first["actual_loss"] == 1
        assert duplicate["actual_loss"] == 0
        assert sanity.psychoanalysis()["success"] is True
        assert sanity.psychoanalysis()["error"] == "psychoanalysis_daily_limit"
        state["world_clock"] = {"elapsed_minutes": 1440}
        assert sanity.psychoanalysis()["success"] is True
        sanity.apply_sanity_loss("1/1", exposure_id="new_body")
        assert state["pc"]["_san_loss_today"] == 1
        assert state["pc"]["sanity_day"]["day"] == 1


def test_incapacitated_pc_cannot_act_outside_combat():
    """A11：HP 归零后调查、取物、封印等身体动作必须在工具层被拒绝。"""
    from src.app.engine import GameEngine
    from src.gameplay.action_resolution import pc_incapacitated

    state = combat_world()
    state["pc"]["hp"] = 0
    state["pc"]["conditions"] = ["dying"]
    assert pc_incapacitated(state)

    engine = SimpleNamespace(
        context=SimpleNamespace(world_store=MemoryStore(state)),
        _action_resolution=None,
    )
    for tool in ("skill_check", "state_add_item", "use_item", "state_add_clue"):
        result = json.loads(GameEngine._execute_model_tool(engine, tool, {"skill": "spot_hidden"}, player_action="撬锁"))
        assert result["error"] == "pc_incapacitated", tool
    # sanity/end_game 等通道不被误伤
    engine2 = SimpleNamespace(
        context=SimpleNamespace(world_store=MemoryStore(state)),
        _action_resolution=None,
        _execute_tool=lambda *_args, **_kwargs: "{}",
    )
    output = GameEngine._execute_model_tool(engine2, "sanity_check", {}, player_action="")
    assert "pc_incapacitated" not in output


def test_incapacitated_pc_gets_no_preroll_check():
    """A11：HP 归零后确定性预检不再掷骰。"""
    from src.gameplay.action_checks import resolve_action_check

    state = combat_world()
    state["pc"]["hp"] = 0
    state["pc"]["skills"] = {"locksmith": 60}
    engine = SimpleNamespace(context=SimpleNamespace(world_store=MemoryStore(state)))
    assert resolve_action_check(engine, "我撬开门锁") is None


def test_stalemate_ends_combat_after_unchanged_turns():
    from src.gameplay.combat import track_stalemate

    world = combat_world()
    start_combat(world, [{"id": "guard"}])
    assert track_stalemate(world) is None
    assert track_stalemate(world) is None
    outcome = track_stalemate(world)
    assert outcome is not None
    assert world["combat_state"]["active"] is False
    assert world["combat_state"]["outcome"] == "stalemate"


def test_stalemate_counter_resets_on_state_change():
    from src.gameplay.combat import track_stalemate

    world = combat_world()
    start_combat(world, [{"id": "guard"}])
    assert track_stalemate(world) is None
    assert track_stalemate(world) is None
    # 有实质变化（伤害落账）则计数重置
    world["combat_state"]["participants"][1]["hp"] -= 3
    assert track_stalemate(world) is None
    assert track_stalemate(world) is None
    assert world["combat_state"]["active"] is True
    assert track_stalemate(world) is not None
