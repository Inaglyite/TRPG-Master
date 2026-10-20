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
    choose_beat_input,
    make_callbacks,
    scarlet_beats,
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


def test_empty_gun_and_low_hp_change_player_strategy():
    beats = {beat.key: beat for beat in scarlet_beats()}
    world = _world()
    world["combat_state"]["active"] = True
    world["pc"]["inventory"] = [".38口径左轮手枪（0发）"]
    assert "谈判" in choose_beat_input(beats["B6b_fight"], 5, world)
    assert "谈判" in choose_beat_input(beats["B6c_documents"], 0, world)
    world["pc"]["inventory"] = [".38口径左轮手枪（6发）"]
    world["pc"]["hp"] = 3
    assert "谈判" in choose_beat_input(beats["B6b_fight"], 5, world)
    world["combat_state"]["active"] = False
    assert "取回" in choose_beat_input(beats["B6c_documents"], 1, world)


def test_pending_departure_gets_a_player_reply_instead_of_repeated_exam_request():
    beat = next(b for b in scarlet_beats() if b.key == "B1_morgue")
    world = _world()
    world["current_scene"]["id"] = "miskatonic_university"
    world["scene_catalog"] = {"miskatonic_medical": {"name": "医学院"}}
    world["pending_departure"] = {"origin_scene_id": "miskatonic_university", "destination_scene_id": "miskatonic_medical", "question": "要先联系医生吗？"}
    chosen = choose_beat_input(beat, 3, world)
    assert "我已经决定" in chosen and "前往医学院" in chosen
    assert "不需要额外联系" in chosen
    assert world["current_scene"]["id"] == "miskatonic_university"  # Steering is not an execution channel.


def test_pending_departure_driver_respects_flags_and_discards_stale_origin():
    beat = next(b for b in scarlet_beats() if b.key == "B1_morgue")
    world = _world()
    world["scene_catalog"] = {"library": {"name": "图书馆", "required_flags": {"permission": True}}}
    world["pending_departure"] = {"origin_scene_id": "miskatonic_medical", "destination_scene_id": "library"}
    assert "不出发" in choose_beat_input(beat, 2, world)
    world["pending_departure"]["origin_scene_id"] = "old-scene"
    assert choose_beat_input(beat, 2, world) == beat.inputs[2]


def test_battle_driver_enters_real_basement_before_assuming_an_enemy():
    beat = next(b for b in scarlet_beats() if b.key == "B6b_fight")
    world = _world()
    world["current_scene"]["id"] = "trivial_pursuits"
    world["flags"] = {"wicks_shop_searched": True}
    assert "实际进入深层地下室" in choose_beat_input(beat, 9, world)
    assert "非人生物" not in choose_beat_input(beat, 9, world)
    world["flags"]["wicks_shop_searched"] = False
    assert "储藏室" in choose_beat_input(beat, 9, world)


def test_battle_driver_shoots_actual_attacker_once_and_does_not_invent_targets():
    beat = next(b for b in scarlet_beats() if b.key == "B6b_fight")
    world = _world()
    world["combat_state"] = {"active": True, "participants": [
        {"id": "pc", "hp": 10}, {"id": "fallen", "hp": 0}, {"id": "feldman", "name": "费德曼兄妹", "hp": 14},
    ]}
    chosen = choose_beat_input(beat, 9, world)
    assert "费德曼兄妹" in chosen and "只射击一次" in chosen
    assert "非人生物" not in chosen and "fallen" not in chosen


def test_b5b_clock_gives_real_wait_after_arrival_regardless_of_attempt():
    """B5b 真实失败形态：出发交涉/prepare_move 吃掉 attempt 后，抵达回合
    不得跳到“汇总未发生事实”的尾输入；在店时每回合都给可结算的多日等待。"""
    beat = next(b for b in scarlet_beats() if b.key == "B5b_clock")
    world = _world()
    world["current_scene"]["id"] = "miskatonic_lodge_office"
    world["scene_catalog"] = {"trivial_pursuits": {"name": "轻率琐事古董店"}}

    # 人不在古董店：先真实出发，而不是给可结算的等待或“汇总”输入
    chosen = choose_beat_input(beat, 0, world)
    assert "前往轻率琐事古董店" in chosen
    assert "连续三天" not in chosen and "汇总" not in chosen

    # pending_departure 保留优先级：先回答出发决定
    world["pending_departure"] = {
        "origin_scene_id": "miskatonic_lodge_office",
        "destination_scene_id": "trivial_pursuits",
        "question": "要先和洛奇交接吗？",
    }
    assert "我已经决定" in choose_beat_input(beat, 1, world)

    # 抵达古董店时 attempt 已被消耗到 3：仍给真实多日等待，不跳到汇总
    world.pop("pending_departure")
    world["current_scene"]["id"] = "trivial_pursuits"
    chosen = choose_beat_input(beat, 3, world)
    assert "连续三天" in chosen and "等待" in chosen
    assert "汇总" not in chosen
