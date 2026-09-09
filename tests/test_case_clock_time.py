"""时间型案件时钟 + 跨场景监视边界 + 未发现≠不存在的对偶测试。"""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from src.gameplay.action_adjudication import (
    ARRIVAL_TIME_CAP_MINUTES,
    NO_FINDING_CONTRACT,
    apply_adjudicated_effects,
    validate_proposal,
)
from src.gameplay.action_resolution import ActionPhase, ActionResolution, plan_player_action
from src.gameplay.case_clock_time import settle_time_clocks
from src.gameplay.destination_grounding import DestinationGroundingError
from src.gameplay.world_time import advance_time, elapsed_minutes
from tests.test_rule_authority_regressions import MemoryStore


def clock_world(**overrides) -> dict:
    world = {
        "pc": {"name": "调查员", "hp": 12, "san": 60, "attributes": {}, "skills": {"spot_hidden": 60}, "inventory": []},
        "current_scene": {"id": "office", "name": "莱特办公室", "npcs_present": []},
        "scene_catalog": {
            "office": {"id": "office", "name": "莱特办公室"},
            "shop": {"id": "shop", "name": "轻率琐事古董店", "aliases": ["古董店"]},
        },
        "npcs": [],
        "case_clocks": {"doom": 0, "pressure": 0},
        "case_clock_definitions": {
            "doom": {
                "max": 6,
                "levels": {"0": "静默", "1": "寒意"},
                "time_advance": {"every_minutes": 2880, "advance": 1, "daily_cap": 1, "activity": ["wait"]},
            },
            # 没有 time_advance：只由语义事件推进，时间不自动推进。
            "pressure": {"max": 5, "levels": {"0": "平静"}, "advance_when": ["公开指控"]},
        },
        "clue_catalog": {},
        "clues_found": {},
        "flags": {},
    }
    world.update(overrides)
    return world


# ---------------------------------------------------------------- 时钟结算


def test_clock_without_time_rule_never_advances_by_time():
    world = clock_world()
    advance_time(world, 10080, activity="wait")
    # pressure 未声明 time_advance：时间不推进它（只有语义事件可以）。
    assert world["case_clocks"]["pressure"] == 0
    # doom 声明了时间规则：一次 7 天蒙太奇也只推进 1 级（daily_cap）。
    assert world["case_clocks"]["doom"] == 1


def test_below_threshold_only_accumulates_carry():
    world = clock_world()
    event = advance_time(world, 1440, activity="wait")
    assert "clock_events" not in event
    assert world["case_clocks"]["doom"] == 0
    assert world["case_clock_time"]["doom"]["carry"] == 1440


def test_crossing_threshold_advances_once_and_keeps_carry():
    world = clock_world()
    advance_time(world, 1440, activity="wait")
    event = advance_time(world, 1440, activity="wait")
    assert world["case_clocks"]["doom"] == 1
    assert event["clock_events"][0]["source"] == "time"
    assert world["case_clock_time"]["doom"]["carry"] == 0


def test_multiple_thresholds_are_capped_per_settlement():
    world = clock_world()
    # 一次 5 天蒙太奇：单次结算最多推进 1 级，余量封顶在阈值以下。
    event = advance_time(world, 7200, activity="wait")
    assert world["case_clocks"]["doom"] == 1
    assert world["case_clock_time"]["doom"]["carry"] == 2880 - 1
    assert len(event["clock_events"]) == 1


def test_activity_condition_excludes_other_time_costs():
    world = clock_world()
    advance_time(world, 7200, activity="move")
    assert world["case_clocks"]["doom"] == 0
    # 非等待时间只推进锚点：后续 wait 只从锚点起算，不把旅行时间算进阈值。
    advance_time(world, 1440, activity="wait")
    assert world["case_clocks"]["doom"] == 0
    assert world["case_clock_time"]["doom"]["carry"] == 1440


def test_settlement_is_idempotent_under_replay():
    world = clock_world()
    advance_time(world, 2880, activity="wait")
    assert world["case_clocks"]["doom"] == 1
    # 重试/审计重放：同一时间点再次结算不重复记账。
    assert settle_time_clocks(world, activity="wait") == []
    assert world["case_clocks"]["doom"] == 1


def test_save_restore_continues_from_branch_state():
    world = clock_world()
    advance_time(world, 2880, activity="wait")
    snapshot = copy.deepcopy(world)
    advance_time(world, 2880, activity="wait")
    assert world["case_clocks"]["doom"] == 2

    # 读回更早的分支：按该分支自己的锚点继续，不补记更早的区间。
    restored = copy.deepcopy(snapshot)
    advance_time(restored, 1440, activity="wait")
    assert restored["case_clocks"]["doom"] == 1
    assert elapsed_minutes(restored) == 4320
    advance_time(restored, 1440, activity="wait")
    assert restored["case_clocks"]["doom"] == 2


def test_time_rewind_resets_anchor_without_retroactive_advance():
    world = clock_world()
    advance_time(world, 5760, activity="wait")
    assert world["case_clocks"]["doom"] == 1
    world["world_clock"] = {"elapsed_minutes": 0}
    advance_time(world, 60, activity="wait")
    assert world["case_clocks"]["doom"] == 1
    assert world["case_clock_time"]["doom"]["anchor"] == 60


def test_max_cap_is_respected():
    world = clock_world()
    world["case_clocks"]["doom"] = 6
    advance_time(world, 2880, activity="wait")
    assert world["case_clocks"]["doom"] == 6


def test_settlement_events_flow_into_adjudicated_result():
    state = clock_world()
    engine = SimpleNamespace(context=SimpleNamespace(world_store=MemoryStore(state)))
    content = "接下来几天我在办公室等待消息。"
    plan = {
        "intent": "wait",
        "input_quote": content,
        "approach": content,
        "time_minutes": 2880,
    }
    resolution = ActionResolution(
        content,
        ActionPhase.INTERACTION,
        "office",
        adjudication_json=json.dumps(plan, ensure_ascii=False),
    )
    result = apply_adjudicated_effects(engine, resolution, None)
    assert engine.context.world_store.load()["case_clocks"]["doom"] == 1
    assert any(event.get("target") == "doom" for event in result["events"])


# ---------------------------------------------------------------- 跨场景边界


def wait_proposal(content: str, minutes: int) -> dict:
    return {"intent": "wait", "input_quote": content, "approach": content, "time_minutes": minutes}


def test_monitoring_another_scene_requires_arrival_first():
    state = clock_world()
    content = "接下来几天，我白天在古董店对面的咖啡馆监视进出的人，晚上回旅馆整理线索"
    with pytest.raises(DestinationGroundingError, match="先 move 抵达"):
        validate_proposal(
            wait_proposal(content, 4320), content, state, plan_player_action(content, state)
        )


def test_monitoring_current_scene_is_allowed():
    state = clock_world()
    state["current_scene"] = {"id": "shop", "name": "轻率琐事古董店", "npcs_present": []}
    content = "接下来几天，我在古董店对面的咖啡馆监视进出的人"
    result = validate_proposal(
        wait_proposal(content, 4320), content, state, plan_player_action(content, state)
    )
    assert result.adjudication_json


def test_arrival_turn_clamps_montage_time():
    state = clock_world()
    content = "前往古董店，接下来几天在对面咖啡馆监视"
    raw = {
        "intent": "move",
        "input_quote": content,
        "approach": content,
        "destination_scene_id": "shop",
        "time_minutes": 4320,
    }
    result = validate_proposal(raw, content, state, plan_player_action(content, state))
    plan = json.loads(result.adjudication_json)
    assert plan["time_minutes"] == ARRIVAL_TIME_CAP_MINUTES
    assert result.destination_scene_id == "shop"


# ---------------------------------------------------------------- 未发现≠不存在


def discovery_world() -> dict:
    return clock_world(
        clue_catalog={
            "private_diary": {
                "id": "private_diary",
                "text": "莱特的私人日记记载了隐藏墨水层。",
                "type": "hidden",
                "source": "office",
                "related_scenes": ["office"],
                "granted_item": "莱特的私人日记",
                "discovery_rules": [
                    {
                        "intent": "search",
                        "targets": ["私人日记", "日记", "暗格"],
                        "requires_success": True,
                        "skill": "spot_hidden",
                        "difficulty": "regular",
                    }
                ],
            }
        }
    )


def test_search_without_check_cannot_declare_a_result():
    state = discovery_world()
    content = "搜查办公室的抽屉和暗格，找到莱特的私人日记"
    raw = {
        "intent": "interact",
        "input_quote": content,
        "approach": content,
        "time_minutes": 20,
        "success_description": "这间办公室确实没有藏匿私人日记。",
    }
    with pytest.raises(ValueError, match="必须提出对应检定"):
        validate_proposal(raw, content, state, plan_player_action(content, state))


def test_search_with_check_is_allowed():
    state = discovery_world()
    content = "搜查办公室的抽屉和暗格，找到莱特的私人日记"
    raw = {
        "intent": "interact",
        "input_quote": content,
        "approach": content,
        "time_minutes": 20,
        "discovery_refs": ["private_diary:0"],
        "check": {"skill": "spot_hidden", "reason": "暗格需要细致搜查"},
        "failure_description": "你翻遍了抽屉，没有找到暗格。",
    }
    result = validate_proposal(raw, content, state, plan_player_action(content, state))
    assert json.loads(result.adjudication_json)["discovery_refs"] == ["private_diary:0"]


@pytest.mark.parametrize(
    "check_result",
    [
        {"success": False, "skill": "spot_hidden"},
        {"success": False, "skill": "spot_hidden", "repeat_blocked": True, "detail": "相同做法已失败"},
    ],
)
def test_failed_or_repeat_refused_search_states_not_found_is_not_absent(check_result):
    state = discovery_world()
    engine = SimpleNamespace(context=SimpleNamespace(world_store=MemoryStore(state)))
    content = "搜查办公室的抽屉和暗格，找到莱特的私人日记"
    plan = {
        "intent": "interact",
        "input_quote": content,
        "approach": content,
        "time_minutes": 20,
        "discovery_refs": ["private_diary:0"],
        "check": {"skill": "spot_hidden", "reason": "暗格需要细致搜查"},
        "failure_description": "你翻遍了抽屉，没有找到暗格。",
        "success_description": "你找到了日记。",
    }
    resolution = ActionResolution(
        content,
        ActionPhase.CONTACT,
        "office",
        discovery_matches=(),
        adjudication_json=json.dumps(plan, ensure_ascii=False),
    )
    result = apply_adjudicated_effects(engine, resolution, check_result)
    assert NO_FINDING_CONTRACT in result["description"]
    assert result["status"] in {"executed_failure", "not_executed"}


def test_non_search_action_is_not_forced_into_a_check():
    state = discovery_world()
    content = "我在办公室里回忆这几天的见闻，整理思路。"
    raw = {
        "intent": "interact",
        "input_quote": content,
        "approach": content,
        "time_minutes": 10,
        "success_description": "你整理了思路。",
    }
    result = validate_proposal(raw, content, state, plan_player_action(content, state))
    assert result.adjudication_json
