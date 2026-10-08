"""结案命令重放与读档回滚的对偶验证（服务层、真实 SQLite、无模型）。

浏览器验收 frontend/e2e/structured-combat-lifecycle.spec.ts 覆盖真实 UI 链路；
这里补它难以直接驱动、但属于同一结案生命周期语义的两条对偶：

1. 结案后用同一 command_id 原样重放 end_game：命中幂等账本（deduplicated），
   不发新事件、不重发奖励。
2. 读档回滚到结案之前后：
   - 重放旧 command_id：命中账本返回旧回执，但不得把 game_over/奖励带回
     已回滚的世界状态（状态不回春、事件不重放、奖励不双发）；
   - 换新 command_id：可以正常重新结案，且生涯奖励恰好一份。

这两条是「历史结案凭证不得混入未来卡面」在服务层的对偶保证。
"""

import copy

from test_structured_combat_transactions import battle as battle

from src.app.runtime import RuntimeContext
from src.storage.persistence import save_game
from src.structured.branch import restore_structured_save


def _runtime(tmp_path: object) -> RuntimeContext:
    return RuntimeContext.create(
        "sp-world",
        "test-module",
        project_root=tmp_path,
        runtime_root=tmp_path,
    )


def _career_case_ids(state: dict, investigator: str = "inv-alice") -> list[str]:
    career = state["investigators"][investigator].get("career") or {}
    return [entry["case_id"] for entry in career.get("case_history", [])]


def test_end_game_replay_is_deduplicated_without_double_rewards(battle) -> None:
    url, service, _, execute, snapshot, _ = battle
    execute("combat_end", {"reason": "准备结案"})

    first = execute(
        "end_game",
        {"ending_type": "neutral", "title": "撤离", "summary": "全员撤出。"},
        command_id="settle-dedup",
    )
    assert first["status"] == "committed"
    state_after, _, commands_after, events_after = snapshot()
    assert state_after["game_over"]["title"] == "撤离"
    case_ids = _career_case_ids(state_after)
    assert len(case_ids) == 1

    # 同一 command_id + 同一载荷重放：账本命中，零事件、状态不变、奖励不双发。
    # （结案后非 POST_GAME 命令会被终局门禁拒绝，不能用别的命令做台账探针。）
    replayed = execute(
        "end_game",
        {"ending_type": "neutral", "title": "撤离", "summary": "全员撤出。"},
        command_id="settle-dedup",
    )
    assert replayed.get("deduplicated") is True
    assert replayed["events"] == []
    state_replayed, _, commands_replayed, events_replayed = snapshot()
    assert events_replayed == events_after
    assert commands_replayed == commands_after
    assert _career_case_ids(state_replayed) == case_ids


def test_restore_then_replay_settlement_cannot_resurrect_old_receipt(battle, tmp_path) -> None:
    url, service, _, execute, snapshot, _ = battle
    execute("combat_end", {"reason": "准备结案"})
    runtime = _runtime(tmp_path)
    save_game([], "slot_001", context=runtime)

    execute(
        "end_game",
        {"ending_type": "bad", "title": "旧结局", "summary": "将被读档回滚。"},
        command_id="settle-old",
    )
    settled = snapshot()[0]
    assert settled["game_over"]["title"] == "旧结局"
    old_case_ids = _career_case_ids(settled)
    assert len(old_case_ids) == 1

    restore_structured_save(runtime, "slot_001")
    rolled_back = snapshot()[0]
    assert not rolled_back.get("game_over")
    assert not rolled_back.get("case_settlements")
    assert _career_case_ids(rolled_back) == []

    # 重放旧 command_id：账本命中（deduplicated），但世界状态必须保持回滚后的
    # 未结案——旧结案凭证不得借重放回春。
    events_before = snapshot()[3]
    replayed = execute(
        "end_game",
        {"ending_type": "bad", "title": "旧结局", "summary": "将被读档回滚。"},
        command_id="settle-old",
    )
    assert replayed.get("deduplicated") is True
    assert replayed["events"] == []
    after_replay = snapshot()
    assert not after_replay[0].get("game_over"), "重放旧命令不得恢复 game_over"
    assert not after_replay[0].get("case_settlements")
    assert _career_case_ids(after_replay[0]) == []
    assert after_replay[3] == events_before, "重放不得产生新事件"

    # 换新 command_id 重新结案：正常执行，生涯奖励恰好一份（不混入旧凭证）。
    fresh = execute(
        "end_game",
        {"ending_type": "good", "title": "新结局", "summary": "回滚后的真实结局。"},
        command_id="settle-new",
    )
    assert fresh["status"] == "committed"
    final = snapshot()[0]
    assert final["game_over"]["title"] == "新结局"
    assert list(final["case_settlements"].keys()) == ["sp-world:manual"]
    final_case_ids = _career_case_ids(final)
    assert final_case_ids == ["sp-world:manual"]
    career = final["investigators"]["inv-alice"]["career"]
    # good 结局 reputation +3；若旧凭证混入则为 +3+0（bad=0）或更多。
    assert career["reputation"] == 3
    assert copy.deepcopy(final["game_over"])["type"] == "good"
