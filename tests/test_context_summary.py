"""Gold visibility/fact-safety fixtures for H2 narrative summaries."""

from __future__ import annotations

import copy

from src.context_summary import is_control_message, validate_summary_visibility
from src.history_compactor import build_summary_input


def _world() -> dict:
    return {
        "flags": {"public_flag": True},
        "private_memory": {"hidden_facts": {"keeper": "地下室真正的门在壁炉后方。"}},
        "npcs": [
            {
                "id": "butler",
                "secret": "格里高利在三十年前的大火中已经死亡，现在只是幽灵。",
                "revealed": {"level": 0, "entries": []},
            }
        ],
        "clue_catalog": {
            "hidden_clue": {"text": "壁炉后藏着通向地下室的血迹密门。"},
            "seen_clue": {"text": "书桌上有一张公开的便条。"},
        },
        "clues_found": {"investigation": [{"catalog_id": "seen_clue"}]},
    }


def test_summary_input_excludes_engine_control_and_private_authority() -> None:
    text = build_summary_input(
        [
            {"role": "system", "content": "system"},
            {
                "role": "user",
                "content": "[引擎控制指令｜非玩家发言]\\n地下室真正的门在壁炉后方。",
            },
            {"role": "user", "content": "我检查书桌。"},
            {"role": "assistant", "content": "你发现了一张便条。"},
        ]
    )
    assert "地下室真正的门" not in text
    assert "我检查书桌" in text
    assert is_control_message({"role": "user", "content": "[引擎控制指令｜非玩家发言]\\nanything"})


def test_gold_summary_rejects_unrevealed_tier_and_private_memory() -> None:
    world = _world()
    assert validate_summary_visibility('{"events":["你发现公开便条"]}', world).allowed
    assert not validate_summary_visibility(
        '{"events":["格里高利在三十年前的大火中已经死亡，现在只是幽灵。"]}', world
    ).allowed
    assert not validate_summary_visibility(
        '{"events":["地下室真正的门在壁炉后方。"]}', world
    ).allowed
    assert not validate_summary_visibility(
        '{"events":["壁炉后藏着通向地下室的血迹密门。"]}', world
    ).allowed


def test_short_private_fragment_is_never_exempt_from_summary_guard() -> None:
    """第三方模组也可能把密码/代号写成很短的私密文本。"""
    world = _world()
    world["npcs"][0]["secret"] = "黑钥匙"
    result = validate_summary_visibility('{"events":["管家提到黑钥匙。"]}', world)
    assert not result.allowed
    assert result.reason == "private_fragment"


def test_summary_cannot_mint_authority_in_gold_scene() -> None:
    world = _world()
    before = copy.deepcopy(world)
    result = validate_summary_visibility('{"events":["玩家检查书桌并发现公开便条"]}', world)
    assert result.allowed
    # The guard is pure. It cannot mutate flags, clues, reveal levels or any
    # other WorldState authority.
    assert world == before
