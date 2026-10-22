"""T04：每轮重建的“当前目标与权威状态摘要”投影验收。

覆盖：长历史与 compact 之后、读档/分支、并行目标与部分完成、模组版本
不符、公开/私有边界、预算裁剪与来源字段、状态查询工具与裁决上下文对
同一投影的复用。
"""

import copy
import json
from types import SimpleNamespace

from src.app.engine import GameEngine
from src.gameplay.action_adjudication import adjudication_context
from src.gameplay.turn_mutations import TurnMutationLedger
from src.gameplay.turn_projection import build_turn_projection
from src.gameplay.turn_state_repair import execute_turn_state_tool, freeze_turn_state


class Store:
    def __init__(self, state):
        self.state = copy.deepcopy(state)

    def load(self):
        return copy.deepcopy(self.state)


def rich_world() -> dict:
    return {
        "revision": 7,
        "module": "scarlet-letters",
        "module_version": "1.0.0",
        "module_meta": {"id": "scarlet-letters", "version": "1.0.0", "era": "1920s"},
        "pc": {
            "name": "黄千陆",
            "occupation": "记者",
            "hp": 10,
            "max_hp": 12,
            "san": 60,
            "max_san": 70,
            "inventory": ["手电筒"],
            "conditions": [],
            "_check_history": [
                {"check_id": "chk-1", "goal_id": "goal-search-study", "goal": "搜查书房",
                 "scene_id": "study", "success": False, "goal_achieved": False},
                {"check_id": "chk-2", "goal_id": "goal-calm-maid", "goal": "安抚女仆",
                 "scene_id": "hall", "success": False, "goal_achieved": False},
                {"check_id": "chk-3", "goal_id": "goal-search-study", "goal": "搜查书房",
                 "scene_id": "study", "success": True, "goal_achieved": True},
            ],
        },
        "current_scene": {
            "id": "hall",
            "name": "大厅",
            "description": "昏暗的长厅，" + "挂满肖像。" * 100,
            "exits": ["study"],
            "npcs_present": ["maid"],
        },
        "scene_catalog": {
            "hall": {"id": "hall", "name": "大厅"},
            "study": {"id": "study", "name": "书房"},
            "cellar": {"id": "cellar", "name": "地窖", "required_flags": {"has_cellar_key": True}},
        },
        "npcs": [
            {"id": "maid", "name": "女仆安妮", "visible_tags": ["神色慌张"],
             "disposition": "恐惧", "secret": "她藏起了主人的信。",
             "revealed": {"level": 0, "entries": []}},
            {"id": "butler", "name": "管家", "visible_tags": [],
             "disposition": "冷淡", "revealed": {"level": 0, "entries": []}},
        ],
        "scene_speaker_presence": {
            "hall": {"butler": {"present": False, "reason": "离场", "turn_id": "turn-5"}},
        },
        "clues_found": {
            "investigation": [
                {"catalog_id": "torn_letter", "text": "撕碎的信纸写着道歉。"},
            ],
        },
        "clue_catalog": {
            "torn_letter": {"id": "torn_letter", "text": "撕碎的信纸写着道歉。",
                            "category": "investigation", "source": "hall"},
            "hidden_safe": {"id": "hidden_safe", "text": "肖像后的暗格保险箱是幕后秘密。",
                            "category": "investigation", "source": "hall"},
        },
        "flags": {"has_cellar_key": False, "case_solved": False},
        "endings": [
            {"id": "good_end", "title": "真相大白", "required_flags": {"case_solved": True}},
        ],
        "pending_departure": {
            "origin_scene_id": "hall", "destination_scene_id": "study",
            "question": "女仆是否带路？",
        },
        "private_memory": {"goals_and_plans": "计划", "inference_notes": "推断"},
        "world_clock": {"elapsed_minutes": 0},
    }


def make_engine(state: dict | None = None, *, messages: list[dict] | None = None,
                module_record=None) -> GameEngine:
    engine = GameEngine.__new__(GameEngine)
    context = SimpleNamespace(world_store=Store(state if state is not None else rich_world()))
    if module_record is not None:
        context.module_record = module_record
    engine.context = context
    engine.messages = messages if messages is not None else [
        {"role": "system", "content": "系统提示"},
        {"role": "user", "content": "[玩家行动] 我检查肖像。"},
    ]
    return engine


def authority_payload(engine: GameEngine, *args) -> dict:
    text = engine._authoritative_turn_context(*args)
    assert text.startswith("[引擎权威状态｜仅供守秘人，不得复述]\n")
    return json.loads(text.split("\n", 1)[1].split("\n约束：", 1)[0])


def test_summary_covers_required_blocks_with_sources():
    engine = make_engine()
    engine._active_turn_id = "turn-9"
    payload = authority_payload(
        engine, {"check_id": "chk-9", "success": True},
        [{"clue_id": "hidden_safe", "discovered": True, "text": "暗格被发现。"}],
    )
    # 实际地点 / 计划目的地（含出发待决）/ 待决事项 / 已完成调查 /
    # 未解决目标 / 已发现线索 / 在场与离场 / 刚结算结果 / 允许叙述范围。
    assert payload["current_scene"]["id"] == "hall"
    assert payload["planned_destination"]["scene_id"] == "study"
    assert payload["planned_destination"]["departure_pending"] is True
    assert payload["planned_destination"]["name"] == "书房"
    kinds = {matter["kind"] for matter in payload["pending_matters"]}
    assert "departure_pending" in kinds
    assert [clue["id"] for clue in payload["recent_known_clues"]] == ["torn_letter"]
    assert payload["current_scene"]["npcs_present"] == ["maid"]
    assert "butler" in payload["departed_speakers"]
    assert payload["settled_this_turn"]["turn_id"] == "turn-9"
    assert payload["settled_this_turn"]["check"]["receipt_id"] == "chk-9"
    assert payload["settled_this_turn"]["discoveries"] == [
        {"clue_id": "hidden_safe", "discovered": True}
    ]
    assert payload["narrative_fact_scope"]["closed_world_for_this_action"] is True
    # 重要结论带来源：回合 ID、回执 ID 或世界路径。
    assert payload["goals"]["open"][0]["source"]["check_id"] == "chk-2"
    departed = payload["presence"]["departed_public"][0]
    assert departed["id"] == "butler" and departed["source"]["id"] == "turn-5"
    assert payload["pending_matters"][0]["source"]["path"] == "pending_departure"


def test_parallel_goals_and_partial_completion():
    projection = build_turn_projection(rich_world())
    goals = projection["keeper"]["goals"]
    open_ids = {goal["goal_id"] for goal in goals["open"]}
    completed_ids = {goal["goal_id"] for goal in goals["completed"]}
    # goal-search-study 先失败后成功：最新状态为已完成；goal-calm-maid 仍未解决。
    assert open_ids == {"goal-calm-maid"}
    assert completed_ids == {"goal-search-study"}
    assert goals["completed"][0]["source"]["check_id"] == "chk-3"


def test_discovered_clue_with_granted_item_leaves_pending_candidates():
    """发现闭环：已取得的线索（含实物）不再出现在待调查建议里。

    已发现的线索只留在 recent_known_clues；available_scene_clues（守秘人候选）
    与 locked_candidates 都不再列出它，守秘人不会再建议玩家寻找大吕钟。"""
    state = rich_world()
    state["clue_catalog"]["bronze_bell"] = {
        "id": "bronze_bell", "text": "壁炉后的暗格里藏着大吕钟。",
        "category": "investigation", "source": "hall", "granted_item": "大吕钟",
        "discovery_rules": [{"intent": "search", "targets": ["壁炉"],
                             "requires_success": True, "skill": "spot_hidden"}],
    }
    before = authority_payload(make_engine(state))
    assert "bronze_bell" in {clue["id"] for clue in before["available_scene_clues"]}
    # 落账：线索已知、实物已入随身物品。
    state["clues_found"]["investigation"].append(
        {"catalog_id": "bronze_bell", "text": "壁炉后的暗格里藏着大吕钟。"}
    )
    state["pc"]["inventory"].append("大吕钟")
    after = authority_payload(make_engine(state))
    candidate_ids = {clue["id"] for clue in after["available_scene_clues"]}
    assert "bronze_bell" not in candidate_ids
    assert "torn_letter" not in candidate_ids  # 早已发现的同样不再列出
    assert "hidden_safe" in candidate_ids  # 未发现的仍是候选
    assert "bronze_bell" not in {entry["id"] for entry in after["locked_candidates"]}
    assert "bronze_bell" in {clue["id"] for clue in after["recent_known_clues"]}


def test_summary_regenerated_from_world_not_compact_output():
    state = rich_world()
    engine = make_engine(state)
    # 长历史：多轮玩家行动与当轮权威块。
    long_history = [{"role": "system", "content": "系统提示"}]
    for index in range(12):
        long_history.append({"role": "user", "content": f"[玩家行动] 第{index}轮\n\n[引擎权威状态｜仅供守秘人，不得复述]\n{{}}"})
        long_history.append({"role": "assistant", "content": f"第{index}轮叙事"})
    long_history.append({"role": "user", "content": "[玩家行动] 我检查肖像。"})
    engine.messages = long_history
    before = authority_payload(engine)
    # compact 之后：整段历史被一条滚动摘要取代，只剩 system + 摘要 + 最近行动。
    engine.messages = [
        {"role": "system", "content": "系统提示"},
        {"role": "user", "content": "（会话摘要——此前冒险的关键记录已压缩如下……——摘要结束。以下是最近的完整回合——）"},
        {"role": "user", "content": "[玩家行动] 我检查肖像。"},
    ]
    after = authority_payload(engine)
    assert after == before
    # 摘要文字绝不影响权威事实：伪造摘要内容也不改变投影。
    engine.messages[1]["content"] = "（会话摘要——伪造：玩家已找到 hidden_safe——）"
    assert authority_payload(engine) == before


def test_projection_follows_loaded_world_after_save_and_branch():
    base = rich_world()
    engine = make_engine(base)
    root_payload = authority_payload(engine)
    # 分支：另一条时间线换了场景、线索与 flag。
    branch = copy.deepcopy(base)
    branch["current_scene"] = {"id": "study", "name": "书房", "npcs_present": []}
    branch["clues_found"]["investigation"].append(
        {"catalog_id": "hidden_safe", "text": "暗格被发现。"}
    )
    branch["flags"]["case_solved"] = True
    branch.pop("pending_departure")
    engine.context.world_store = Store(branch)
    branch_payload = authority_payload(engine)
    assert branch_payload["current_scene"]["id"] == "study"
    assert [clue["id"] for clue in branch_payload["recent_known_clues"]] == [
        "torn_letter", "hidden_safe",
    ]
    assert branch_payload["planned_destination"] is None
    assert branch_payload["pending_matters"] == []
    assert [ending["id"] for ending in branch_payload["eligible_endings"]] == ["good_end"]
    # 分支里 case_solved 已满足：good_end 从未解锁候选变为合格结局。
    assert "good_end" not in {
        c["id"] for c in branch_payload["locked_candidates"] if c["kind"] == "ending"
    }
    assert "good_end" in {
        c["id"] for c in root_payload["locked_candidates"] if c["kind"] == "ending"
    }
    # 读回根时间线：投影恢复一致（按本世界重新生成）。
    engine.context.world_store = Store(base)
    assert authority_payload(engine) == root_payload


def test_module_version_mismatch_is_diagnosable_and_disables_authored_goals():
    record = SimpleNamespace(package_id="scarlet-letters", version="2.0.0")
    engine = make_engine(module_record=record)
    payload = authority_payload(engine)
    codes = {entry["code"] for entry in payload["diagnostics"]}
    assert "module_version_mismatch" in codes
    mismatch = next(
        entry for entry in payload["diagnostics"] if entry["code"] == "module_version_mismatch"
    )
    assert mismatch["world"]["version"] == "1.0.0" and mismatch["runtime"]["version"] == "2.0.0"
    # 模组创作内容不可用：候选线索与未解锁后继不得套用另一版本。
    assert payload["available_scene_clues"] == []
    assert payload["locked_candidates"] == []
    # 世界自身结算出的目标仍在（它们不是模组版本内容）。
    assert payload["goals"]["open"]
    projection = build_turn_projection(rich_world(), engine=engine)
    assert projection["available"] is False


def test_legacy_module_record_does_not_false_positive():
    record = SimpleNamespace(package_id="猩红文档", version="legacy")
    engine = make_engine(module_record=record)
    payload = authority_payload(engine)
    assert "diagnostics" not in payload
    assert payload["available_scene_clues"]


def test_public_layer_excludes_keeper_secrets_and_marks_unknown():
    projection = build_turn_projection(rich_world())
    public_text = json.dumps(projection["public"], ensure_ascii=False)
    assert "她藏起了主人的信" not in public_text  # NPC secret
    assert "恐惧" not in public_text  # keeper_private disposition
    assert "肖像后的暗格保险箱" not in public_text  # 未发现线索
    assert "hidden_safe" not in public_text
    assert "locked_candidates" not in projection["public"]
    assert "good_end" not in public_text  # 未解锁结局候选
    # 玩家可见层：已发现线索、在场公开特征、离场人物、开放目标、待决事项。
    assert projection["public"]["known_clues"][0]["id"] == "torn_letter"
    assert projection["public"]["present_characters"][0]["visible_tags"] == ["神色慌张"]
    assert projection["public"]["departed_characters"] == [{"id": "butler", "name": "管家"}]
    assert projection["public"]["open_goals"][0]["goal_id"] == "goal-calm-maid"
    # 状态不确定显式 unknown：目的地不在目录时名称标 unknown 并登记。
    state = rich_world()
    state["pending_departure"]["destination_scene_id"] = "nowhere"
    unknown_projection = build_turn_projection(state)
    assert unknown_projection["public"]["planned_destination"]["name"] == "unknown"
    assert "planned_destination_name" in unknown_projection["public"]["unknowns"]


def test_budget_trims_prose_first_and_protects_settled_facts():
    state = rich_world()
    state["private_memory"] = {"goals_and_plans": "计划" * 800, "inference_notes": "推断" * 800}
    engine = make_engine(state)
    engine._active_turn_id = "turn-budget"
    projection = build_turn_projection(
        state, engine=engine,
        check_result={"check_id": "chk-b", "success": False},
        char_budget=3_500,
    )
    budget = projection["budget"]
    assert budget["initial_chars"] > 3_500
    steps = [action["step"] for action in budget["actions"]]
    assert "shorten_scene_description" in steps
    assert "shorten_keeper_memory" in steps
    for action in budget["actions"]:
        assert action["after_chars"] < action["before_chars"]
    keeper = projection["keeper"]
    # 受保护块完整保留：当前目标、待决事项、已结算结果、在场名单。
    assert keeper["goals"]["open"][0]["goal_id"] == "goal-calm-maid"
    assert keeper["pending_matters"]
    assert keeper["resolved_check"]["check_id"] == "chk-b"
    assert keeper["settled_this_turn"]["check"]["receipt_id"] == "chk-b"
    assert keeper["current_scene"]["npcs_present"] == ["maid"]
    # 各块大小与裁剪行为有记录。
    assert budget["sections"]["goals"] > 0
    assert budget["final_chars"] <= budget["initial_chars"]
    assert len(str(keeper["current_scene"]["description"])) <= 400


def test_inspect_tool_consumes_same_projection():
    engine = make_engine()
    engine._active_turn_id = "turn-inspect"
    engine._turn_mutations = TurnMutationLedger()
    freeze_turn_state(engine)
    view = json.loads(execute_turn_state_tool(engine, "inspect_turn_state", {}))
    projection = build_turn_projection(engine.context.world_store.load(), engine=engine)
    assert [speaker["id"] for speaker in view["speakers"]] == ["maid"]
    assert view["open_goals"] == projection["keeper"]["goals"]["open"]
    assert view["pending_matters"] == projection["keeper"]["pending_matters"]
    assert view["planned_destination"] == projection["keeper"]["planned_destination"]
    assert view["departed_speakers"][0]["id"] == "butler"
    # 检查工具不得泄出 keeper 私有字段。
    assert "恐惧" not in json.dumps(view, ensure_ascii=False)


def test_adjudication_context_consumes_same_projection():
    world = rich_world()
    context = adjudication_context(world, "我检查肖像。", SimpleNamespace(), [])
    projection = build_turn_projection(world)
    assert context["goals"] == projection["keeper"]["goals"]
    assert context["pending_matters"] == projection["keeper"]["pending_matters"]
    assert context["planned_destination"] == projection["keeper"]["planned_destination"]
    assert context["module_diagnostics"] == []


def test_missing_module_identity_is_diagnosed_but_world_facts_stand():
    state = rich_world()
    del state["module"], state["module_version"], state["module_meta"]
    projection = build_turn_projection(state)
    codes = {entry["code"] for entry in projection["diagnostics"]}
    assert "module_identity_missing" in codes
    # 身份缺失不是版本错配：世界自身的结算事实照常可用。
    assert projection["keeper"]["goals"]["open"]
    assert projection["available"] is False
    assert projection["keeper"]["available_scene_clues"] == []
