"""tools/playthrough.py 纯逻辑单测：不依赖真实模型与 API。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# tools.playthrough 为了让 CLI 直接可跑，import 时会把 .env.json 映射进
# os.environ（同 server.py 语义）。测试进程里这会污染 config 默认值断言，
# 因此导入后立即还原 environ；模块内已冻结的 src.* 引用不受影响。
_saved_environ = dict(os.environ)
from tools.playthrough import (  # noqa: E402
    Capture,
    Check,
    _ammo_count,
    _madness_check,
    check_clue,
    check_handout,
    check_scene,
    make_callbacks,
    scene_is,
    snapshot_world,
)

os.environ.clear()
os.environ.update(_saved_environ)


def _world() -> dict:
    return {
        "current_scene": {"id": "miskatonic_medical", "npcs_present": ["john_whitcroft"]},
        "clues_found": ["wright_body_evidence"],
        "flags": {"monster_defeated": True},
        "pc": {
            "hp": 10,
            "san": 25,
            "inventory": [".38口径左轮手枪（5发）", "笔记本"],
            "psychological_profile": {"trauma": ["恐血症（恐惧症）"]},
        },
        "case_clocks": {},
        "combat_state": {"active": False},
    }


def test_snapshot_world_extracts_acceptance_fields() -> None:
    snap = snapshot_world(_world())
    assert snap["scene"] == "miskatonic_medical"
    assert snap["npcs_present"] == ["john_whitcroft"]
    assert snap["clues_found"] == ["wright_body_evidence"]
    assert snap["san"] == 25
    assert snap["combat_active"] is False


def test_check_helpers() -> None:
    world = _world()
    cap = Capture(
        handouts=[{"entity_id": "john_whitcroft", "file": "x.png"}],
        turn_snapshots=[{"scene": "miskatonic_medical"}],
    )
    assert scene_is("miskatonic_medical")(world, cap)
    assert not scene_is("wright_cottage")(world, cap)
    # check_scene 是轨迹断言：beat 期间到过即算（终态可能已离开）
    assert check_scene(2, "miskatonic_medical").fn(world, cap)
    assert not check_scene(2, "wright_cottage").fn(world, cap)
    assert check_clue(3, "wright_body_evidence").fn(world, cap)
    assert not check_clue(3, "hunter_copy").fn(world, cap)
    assert check_handout(3, "john_whitcroft").fn(world, cap)
    assert not check_handout(3, "bryce_fallon").fn(world, cap)


def test_ammo_count_parses_firearm_inventory() -> None:
    assert _ammo_count(_world()) == 5
    assert _ammo_count({"pc": {"inventory": ["笔记本"]}}) is None
    assert _ammo_count({"pc": {"inventory": ["猎枪（2发）"]}}) == 2


def test_madness_check_detects_symptoms() -> None:
    assert _madness_check(_world()) is True
    sane = _world()
    sane["pc"]["psychological_profile"] = {"trauma": []}
    assert _madness_check(sane) is False


def test_callbacks_capture_events_and_auto_answer_decisions() -> None:
    cap = Capture()
    cb = make_callbacks(cap)
    cb.on_narrative("叙事", None)
    cb.on_narrative_segments([{"kind": "speech", "npc_id": "x"}])
    cb.on_handout({"entity_id": "john_whitcroft"})
    cb.on_dice("检定 42/60")
    cb.on_game_over("truth_and_seal", "真相大白", "…")
    selected = cb.on_decision(
        {
            "kind": "escalation",
            "options": [{"id": "confirm", "label": "确认"}, {"id": "abort", "label": "放弃"}],
            "default_option": "abort",
        }
    )
    assert selected == "abort"  # 默认策略选 default_option
    assert cap.narratives == ["叙事"]
    assert cap.handouts[0]["entity_id"] == "john_whitcroft"
    assert cap.game_over["ending_type"] == "truth_and_seal"
    assert len(cap.decisions) == 1

    preview_selected = cb.on_decision(
        {
            "kind": "action_preview",
            "options": [
                {"id": "continue_action", "label": "继续"},
                {"id": "cancel_action", "label": "取消"},
            ],
            "default_option": "cancel_action",
        }
    )
    assert preview_selected == "continue_action"

    cap2 = Capture()
    cb2 = make_callbacks(cap2, decision_strategy="confirm")
    assert (
        cb2.on_decision(
            {
                "options": [{"id": "confirm", "label": "确认"}, {"id": "abort", "label": "放弃"}],
                "default_option": "abort",
            }
        )
        == "confirm"
    )


def test_check_result_records_pass_fail() -> None:
    check = Check(area=2, desc="场景", fn=lambda w, c: True)
    check.result = bool(check.fn({}, Capture()))
    assert check.result is True
