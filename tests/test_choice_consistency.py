import copy

from src.ai.context.narrative_history import current_history_choices
from src.ai.model.dialogue_choices import prepare_dialogue_choices
from src.ai.model.dialogue_output import DialogueOutput
from src.gameplay.adjudication_policy import check_conditions
from src.gameplay.choice_policy import filter_choice_targets


def world():
    return {"pc": {}, "current_scene": {"id": "study", "npcs_present": ["jin"], "exits": ["hall"]},
            "scene_catalog": {"hall": {}}, "clues_found": {"investigation": [{"id": "bell", "text": "一枚铜片"}]}}


def test_departed_and_acquired_targets_drop_and_hints_remap_together():
    output = DialogueOutput.model_validate({
        "segments": [{"kind": "narration", "text": "桌上摊着摹本。"}],
        "choices": ["找王妈", "找铜片", "研究摹本", "自由行动"],
        "choice_targets": [{"choice_index": 1, "kind": "npc", "target_id": "wang"},
                           {"choice_index": 2, "kind": "clue", "target_id": "bell"},
                           {"choice_index": 3, "kind": "goal", "target_id": "rubbing", "evidence_quote": "摹本"}],
        "choice_skill_hints": [{"choice_index": 1, "skill_id": "persuade"}, {"choice_index": 3, "skill_id": "history"}],
    })
    result, dropped = prepare_dialogue_choices(output, world())
    assert result.choices == ["研究摹本", "自由行动"]
    assert [row["reason"] for row in dropped] == ["speaker_not_present", "clue_already_acquired"]
    assert result.choice_skill_hints[0].choice_index == 1
    assert result.choice_skill_hints[0].skill_id == "history"
    assert result.choice_targets[0].choice_index == 1


def test_rewrite_removes_evidence_and_cannot_use_menu_as_its_own_evidence():
    output = DialogueOutput.model_validate({
        "segments": [{"kind": "narration", "text": "房间很安静。\n\n**你可以——**\n1. 研究秘密摹本\n2. 自由行动"}],
        "choice_targets": [{"choice_index": 1, "kind": "goal", "target_id": "rubbing", "evidence_quote": "秘密摹本"}],
    })
    result, dropped = prepare_dialogue_choices(output, world())
    assert result.choices == ["自由行动"]
    assert dropped[0]["reason"] == "no_public_evidence"
    assert result.segments[0].text.strip() == "房间很安静。"


def test_latest_goal_result_and_locked_destination():
    state = world()
    conditions = check_conditions(state)
    state["pc"]["_check_history"] = [
        {"goal_id": "rubbing", "success": True, "condition_snapshot": conditions},
        {"goal_id": "rubbing", "success": False, "condition_snapshot": conditions},
    ]
    targets = [{"choice_index": 1, "kind": "goal", "target_id": "rubbing", "evidence_quote": "摹本"},
               {"choice_index": 2, "kind": "scene", "target_id": "hall"}]
    state["scene_catalog"]["hall"]["required_flags"] = {"door_open": True}
    kept, dropped = filter_choice_targets(["研究摹本", "去大厅", "自由行动"], targets, state, "摹本")
    assert kept == [1, 3]
    assert dropped[0]["reason"] == "destination_locked"


def test_restore_preserves_historical_menu_but_refreshes_current_actions():
    history = [{"narrative": "王妈走了。", "choices": [
        {"label": "问王妈", "target": {"kind": "npc", "target_id": "wang"}, "isFree": False},
        {"label": "自由行动", "isFree": True},
    ]}]
    before = copy.deepcopy(history)
    assert current_history_choices(history, world()) == [history[0]["choices"][1]]
    assert history == before
    assert current_history_choices(history, {**world(), "game_over": {"id": "end"}}) == []


def test_unrevealed_name_and_exact_private_fact_do_not_become_menu_evidence():
    state = world()
    state["npcs"] = [{"id": "jin", "name": "金三爷", "secret": "铜钟其实藏在地板夹层里", "revealed": {"level": 0}}]
    kept, dropped = filter_choice_targets(["问金三爷", "铜钟其实藏在地板夹层里", "自由行动"],
        [{"choice_index": 1, "kind": "npc", "target_id": "jin"}], state, "一个男人坐在柜台后。")
    assert kept == [3]
    assert [row["reason"] for row in dropped] == ["speaker_not_introduced", "private_information"]
