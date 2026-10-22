from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


@pytest.fixture
def choose():
    # Import this CLI without loading a developer's real model credentials into
    # the shared pytest process. The actual live playthrough is a separate run.
    original = Path.read_text

    def read(path, *args, **kwargs):
        return "{}" if path.name == ".env.json" else original(path, *args, **kwargs)

    with patch.object(Path, "read_text", read):
        from tools.playthrough import choose_beat_input
    return choose_beat_input


def test_student_beat_recovers_a_rolled_back_move_instead_of_talking_to_absent_students(choose):
    beat = SimpleNamespace(key="B2_students", inputs=["前往历史系", "前往学生公社", "追问在场学生"])
    action = choose(beat, 4, {"current_scene": {"id": "miskatonic_medical"}})
    assert "前往历史系" in action and "到了以后" in action


def test_failed_flag_only_search_can_be_pushed_without_inventing_clue_ids(choose):
    beat = SimpleNamespace(key="B3b_cottage", inputs=["搜查小屋"])
    state = {"current_scene": {"id": "wright_cottage"}, "pc": {
        "_check_history": [{"scene_id": "wright_cottage", "skill": "spot_hidden", "success": False,
                            "check_id": "c1", "goal_achieved": False, "discovery_ids": []}],
        "_push_contexts": {"c1": {"target_id": "原先搜查的书桌和床铺", "push_risk": "耗时并留下翻动痕迹", "used": False}},
    }}
    action = choose(beat, 2, state)
    assert "明确选择孤注一掷" in action and "原先搜查的书桌和床铺" in action
    state["pc"]["_push_contexts"]["c1"]["used"] = True
    assert choose(beat, 2, state) == "搜查小屋"
