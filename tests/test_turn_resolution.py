import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.ai.tools.registry import tool_catalog_for_names
from src.ai.tools.tool_policy import MODEL_CALLER, ToolRequestSnapshot, attach_request_snapshot
from src.ai.tools.tool_request_authority import issue_model_request
from src.app.agent_graph import (
    _call_combat_agent,
    _call_story_agent,
    _emit_sanity_dice,
    _execute_tools,
    _finalize_turn,
    _prepare_turn,
)
from src.gameplay.action_checks import infer_action_check, infer_scene_transition
from src.gameplay.turn_reconciler import (
    _compact_world,
    apply_turn_commit,
    narrative_body,
    reconcile_narrative_entities,
    reconcile_turn,
    turn_needs_model_audit,
)
from src.storage.world_store import WorldStore


def resolution_world() -> dict:
    return {
        "module_meta": {"era": "1920s"},
        "pc": {
            "name": "黄千陆",
            "hp": 10,
            "san": 70,
            "skills": {
                "spot_hidden": 70,
                "listen": 60,
                "track": 50,
            },
            "inventory": ["手电筒"],
        },
        "current_scene": {"id": "hall", "name": "大厅"},
        "scene_catalog": {
            "hall": {"id": "hall", "name": "大厅", "npcs_present": []},
            "office": {
                "id": "office",
                "name": "莱特的办公室",
                "npcs_present": ["fallon"],
            },
        },
        "npcs": [
            {
                "id": "fallon",
                "name": "布莱斯·法伦",
                "revealed": {"level": 0, "entries": []},
            }
        ],
        "clues_found": {
            "investigation": [],
            "event": [],
            "task": [],
            "npc": [],
        },
        "clue_catalog": {
            "melted_mirror": {
                "id": "melted_mirror",
                "text": "裂镜的一角像被高热熔化。",
                "category": "investigation",
            }
        },
        "flags": {"office_searched": False},
        "endings": [],
    }


def model_tool_calls(
    engine: SimpleNamespace,
    calls: list[tuple[str, str, str]],
    *,
    issued_names: list[str] | None = None,
) -> list[dict]:
    """Build one server-issued model response for graph tests.

    All calls in one provider response share exactly one frozen request
    snapshot.  Tests must not manufacture bearer-like request metadata: the
    execution path accepts it only after the server has issued the catalog to
    the same engine.
    """
    catalog = tool_catalog_for_names(issued_names or [name for _call_id, name, _arguments in calls])
    snapshot = ToolRequestSnapshot.create(
        step=1,
        profile="story:test",
        caller=MODEL_CALLER,
        tools=catalog,
    )
    issue_model_request(engine, snapshot, catalog)
    return [
        attach_request_snapshot(
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            },
            snapshot,
        )
        for call_id, name, arguments in calls
    ]


class ActionCheckInferenceTests(unittest.TestCase):
    def test_explicit_search_is_prechecked(self):
        check = infer_action_check("我仔细搜查莱特的办公室", resolution_world())

        self.assertIsNotNone(check)
        self.assertEqual(check.skill, "spot_hidden")

    def test_explicit_body_examination_is_prechecked(self):
        check = infer_action_check(
            "我亲眼完整检查莱特教授的遗体，尤其检查他的眼睛和躯干。",
            resolution_world(),
        )

        self.assertIsNotNone(check)
        self.assertEqual(check.skill, "spot_hidden")

    def test_routine_view_and_discussed_action_do_not_roll(self):
        world = resolution_world()

        self.assertIsNone(infer_action_check("我先看一眼莱特的遗体", world))
        self.assertIsNone(infer_action_check("我问法伦能不能让我搜查办公室", world))
        self.assertIsNone(infer_action_check("我问医生：你仔细检查过莱特的遗体吗？", world))
        self.assertIsNone(infer_action_check("我不搜查这间屋子", world))

    def test_explicit_known_scene_travel_is_resolved_locally(self):
        world = resolution_world()
        world["scene_catalog"]["medical"] = {
            "id": "medical",
            "name": "密斯卡托尼克大学医学院",
            "description": "医学院地下的冰冷停尸房。",
            "npcs_present": [],
        }

        self.assertEqual(
            infer_scene_transition(
                "我立刻前往大学医学院的地下停尸房。",
                world,
            ),
            "medical",
        )

    def test_discussed_or_negated_travel_does_not_change_scene(self):
        world = resolution_world()

        self.assertIsNone(infer_scene_transition("我不去莱特的办公室。", world))
        self.assertIsNone(infer_scene_transition("我问法伦怎么去莱特的办公室。", world))

    def test_travel_after_connector_adverb_is_resolved_locally(self):
        """「打电话，然后前往停尸房」这类复合句也必须触发权威场景切换。"""
        world = resolution_world()
        world["scene_catalog"]["medical"] = {
            "id": "medical",
            "name": "密斯卡托尼克大学医学院",
            "description": "医学院地下的冰冷停尸房。",
            "npcs_present": [],
        }

        self.assertEqual(
            infer_scene_transition(
                "请法伦现在给惠特克罗夫特医生打电话，然后前往校医院地下停尸房",
                world,
            ),
            "medical",
        )

    def test_travel_with_carry_phrase_is_resolved_locally(self):
        """「拿着便签前往停尸房」这类携行短语开头也必须触发场景切换。"""
        world = resolution_world()
        world["scene_catalog"]["medical"] = {
            "id": "medical",
            "name": "密斯卡托尼克大学医学院",
            "description": "医学院地下的冰冷停尸房。",
            "npcs_present": [],
        }

        self.assertEqual(
            infer_scene_transition("拿着便签前往停尸房，先找医生说明来意", world),
            "medical",
        )
        self.assertEqual(
            infer_scene_transition("带上钥匙前往医学院", world),
            "medical",
        )

    def test_carry_like_non_move_phrases_do_not_change_scene(self):
        """「活着回去」这类含 着 的非移动句绝不能误判为场景切换。"""
        world = resolution_world()
        world["scene_catalog"]["medical"] = {
            "id": "medical",
            "name": "密斯卡托尼克大学医学院",
            "description": "医学院地下的冰冷停尸房。",
            "npcs_present": [],
        }

        self.assertIsNone(infer_scene_transition("我不想死，要活着回去", world))
        self.assertIsNone(infer_scene_transition("我们商量着去停尸房的事", world))


class StoryStreamingTests(unittest.TestCase):
    def test_prepare_turn_uses_narrative_model_without_marking_routine_turn_risky(self):
        engine = SimpleNamespace(
            current_model="old-model",
            _has_pending_control_instruction=lambda: False,
            _has_pending_new_game_opening=lambda: False,
        )

        with patch("src.app.agent_graph.NARRATIVE_MODEL", "story-model"):
            result = _prepare_turn({"engine": engine, "user_content": None})

        self.assertEqual(engine.current_model, "story-model")
        self.assertFalse(result["turn_had_check"])
        self.assertFalse(result["opening_turn"])

    def test_control_turn_buffers_until_tool_plan_is_known(self):
        calls = []

        def stream(model, **kwargs):
            calls.append((model, kwargs))
            return "开场。", []

        engine = SimpleNamespace(current_model="flash", _stream_llm=stream)

        result = _call_story_agent(
            {
                "engine": engine,
                "control_turn": True,
                "tool_round": 0,
            }
        )

        self.assertEqual(result, {"text": "开场。", "tool_calls": []})
        self.assertTrue(calls[0][1]["buffer_if_tools"])

    def test_structured_opening_uses_public_prompt_without_tools(self):
        calls = []

        def stream(model, **kwargs):
            calls.append((model, kwargs))
            return "完整开场。", []

        engine = SimpleNamespace(
            current_model="story-model",
            _stream_llm=stream,
            _opening_system_prompt=lambda: "public-opening-system",
        )

        result = _call_story_agent(
            {
                "engine": engine,
                "opening_turn": True,
            }
        )

        self.assertEqual(result["text"], "完整开场。")
        self.assertEqual(
            calls[0][1]["system_prompt_override"],
            "public-opening-system",
        )
        self.assertFalse(calls[0][1]["enable_tools"])
        self.assertEqual(calls[0][1]["prompt_profile"], "opening")
        self.assertEqual(calls[0][1]["temperature"], 0.65)

    def test_story_followup_after_resolved_check_cannot_roll_again(self):
        calls = []
        engine = SimpleNamespace(
            current_model="story-model",
            _stream_llm=lambda model, **kwargs: (
                calls.append((model, kwargs)) or ("判定结果已经显现。", [])
            ),
        )

        _call_story_agent({"engine": engine, "turn_had_check": True})

        self.assertFalse(calls[0][1]["enable_tools"])

    def test_combat_agent_uses_judgement_model(self):
        calls = []

        def stream(model, **kwargs):
            calls.append((model, kwargs))
            return "战斗结算。", []

        engine = SimpleNamespace(
            judgement_model="judge-model",
            _stream_llm=stream,
            _combat_system_overlay=lambda: "combat-state",
        )

        result = _call_combat_agent({"engine": engine})

        self.assertEqual(result["text"], "战斗结算。")
        self.assertEqual(calls[0][0], "judge-model")
        self.assertEqual(calls[0][1]["system_overlay"], "combat-state")


class FakeCommitEngine:
    def __init__(self, store: WorldStore):
        self.context = SimpleNamespace(world_store=store)
        self.cb = SimpleNamespace(on_dice=lambda *_args: None, on_game_over=lambda *_args: None)
        self.calls: list[tuple[str, dict]] = []

    def _execute_tool(self, name: str, args: dict) -> str:
        self.calls.append((name, args))

        def update(state: dict) -> None:
            if name == "state_set":
                value = json.loads(args["value"])
                root, key = args["path"].split(".", 1) if "." in args["path"] else ("", "")
                if root:
                    state[root][key] = value
                else:
                    state[args["path"]] = value
            elif name == "state_add_item":
                state["pc"]["inventory"].append(args["item"])
            elif name == "state_remove_item":
                state["pc"]["inventory"].remove(args["item"])
            elif name == "state_add_clue":
                state["clues_found"][args["category"]].append(
                    {
                        "id": args.get("clue_id", "generated"),
                        "text": args["text"],
                    }
                )
            elif name == "npc_reveal":
                npc = next(item for item in state["npcs"] if item["id"] == args["npc_id"])
                npc["revealed"]["entries"].append(
                    {
                        "tier": args["tier"],
                        "text": args["entry_text"],
                    }
                )
                npc["revealed"]["level"] = args["tier"]

        self.context.world_store.update(update)
        if name == "state_add_clue":
            return json.dumps({"ok": True, "clue": {"text": args["text"]}})
        if name == "npc_reveal":
            return json.dumps({"ok": True, "duplicate": False})
        return json.dumps({"ok": True})


class TurnCommitTests(unittest.TestCase):
    def test_commit_applies_only_known_authoritative_entities(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = WorldStore(Path(temp_dir) / "world")
            store.initialize(resolution_world())
            engine = FakeCommitEngine(store)
            commit = {
                "scene_id": "office",
                "items_add": ["黄铜钥匙", "手电筒"],
                "items_remove": [],
                "clues": [
                    {
                        "text": "裂镜的一角像被高热熔化。",
                        "category": "investigation",
                        "clue_id": "melted_mirror",
                    }
                ],
                "npc_reveals": [
                    {
                        "npc_id": "fallon",
                        "tier": 1,
                        "text": "法伦显得异常紧张。",
                    }
                ],
                "flags_set": [
                    {"key": "office_searched", "value_json": "true"},
                    {"key": "invented_flag", "value_json": "true"},
                ],
                "sanity_events": [],
                "ending_id": "",
            }

            result = apply_turn_commit(
                engine,
                commit,
                player_action="我进入办公室仔细搜查，并与法伦交谈。",
                narrative="你抵达莱特的办公室。布莱斯·法伦站在门边，神情异常紧张。",
            )

            world = store.load()
            self.assertEqual(world["current_scene"]["id"], "office")
            self.assertEqual(world["pc"]["inventory"].count("手电筒"), 1)
            self.assertIn("黄铜钥匙", world["pc"]["inventory"])
            self.assertTrue(world["flags"]["office_searched"])
            self.assertNotIn("invented_flag", world["flags"])
            self.assertEqual(
                world["clues_found"]["investigation"][0]["id"],
                "melted_mirror",
            )
            self.assertEqual(world["npcs"][0]["revealed"]["level"], 1)
            self.assertIn("flag:invented_flag", result["skipped"])

    def test_commit_advances_only_declared_clocks_and_never_decreases(self):
        """案件时钟：只接受已声明的键、数值、且严格递增；其余一律 skipped。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            store = WorldStore(Path(temp_dir) / "world")
            world = resolution_world()
            world["case_clocks"] = {"monster_manifestation": 1, "human_pressure": 0}
            world["case_clock_definitions"] = {
                "monster_manifestation": {"max": 6, "levels": {"1": "寒意"}}
            }
            store.initialize(world)
            engine = FakeCommitEngine(store)
            commit = {
                "scene_id": "",
                "items_add": [],
                "items_remove": [],
                "clues": [],
                "npc_reveals": [],
                "flags_set": [],
                "clocks_set": [
                    {"key": "monster_manifestation", "value_json": "2"},
                    {"key": "human_pressure", "value_json": "0"},
                    {"key": "invented_clock", "value_json": "3"},
                    {"key": "monster_manifestation", "value_json": '"high"'},
                ],
                "sanity_events": [],
                "ending_id": "",
            }

            result = apply_turn_commit(
                engine,
                commit,
                player_action="我在旅馆里又守了一天。",
                narrative="第四天夜里，你听见楼下传来低语声，电灯忽明忽暗。",
            )

            clocks = store.load()["case_clocks"]
            self.assertEqual(clocks["monster_manifestation"], 2)
            self.assertEqual(clocks["human_pressure"], 0)
            self.assertNotIn("invented_clock", clocks)
            self.assertIn("clock:monster_manifestation=2", result["applied"])
            self.assertIn("clock:human_pressure", result["skipped"])
            self.assertIn("clock:invented_clock", result["skipped"])
            self.assertIn("clock:monster_manifestation", result["skipped"])

    def test_commit_clock_value_is_clamped_to_declared_max(self):
        """审计越界报数时按模组声明的 max 收拢，不允许跳级爆表。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            store = WorldStore(Path(temp_dir) / "world")
            world = resolution_world()
            world["case_clocks"] = {"monster_manifestation": 4}
            world["case_clock_definitions"] = {
                "monster_manifestation": {"max": 6, "levels": {"6": "完全显形"}}
            }
            store.initialize(world)
            engine = FakeCommitEngine(store)
            commit = {
                "scene_id": "",
                "items_add": [],
                "items_remove": [],
                "clues": [],
                "npc_reveals": [],
                "flags_set": [],
                "clocks_set": [{"key": "monster_manifestation", "value_json": "99"}],
                "sanity_events": [],
                "ending_id": "",
            }

            result = apply_turn_commit(
                engine,
                commit,
                player_action="我撕开了那卷文档。",
                narrative="墨迹从纸面挣脱，怪物的轮廓在字里行间凝聚。",
            )

            self.assertEqual(store.load()["case_clocks"]["monster_manifestation"], 6)
            self.assertIn("clock:monster_manifestation=6", result["applied"])

    def test_audit_payload_exposes_case_clocks(self):
        """审计负载必须携带时钟与等级表：叙事模型无工具，记账只有审计能做。"""
        state = resolution_world()
        state["case_clocks"] = {"monster_manifestation": 2}
        state["case_clock_definitions"] = {
            "monster_manifestation": {"max": 6, "levels": {"2": "鬼火"}}
        }
        compact = _compact_world(state)
        self.assertEqual(compact["case_clocks"], {"monster_manifestation": 2})
        self.assertEqual(
            compact["case_clock_definitions"],
            {"monster_manifestation": {"max": 6, "levels": {"2": "鬼火"}}},
        )


class ClueClarityClockTests(unittest.TestCase):
    """clue_clarity 时钟由引擎在每次真实线索入册后确定性推进（按 max 封顶）。"""

    @staticmethod
    def _add_clue(world: dict, text: str = "新的物证"):
        from tools import state_manager

        previous = state_manager._TRANSACTION_STATE
        had_print = "print" in state_manager.__dict__
        previous_print = state_manager.__dict__.get("print")
        state_manager._TRANSACTION_STATE = world
        state_manager.print = lambda *_args, **_kwargs: None
        try:
            return state_manager.cmd_add_clue(text, "investigation")
        finally:
            state_manager._TRANSACTION_STATE = previous
            if had_print:
                state_manager.print = previous_print
            else:
                state_manager.__dict__.pop("print", None)

    @staticmethod
    def _world(clocks, definitions=None):
        return {
            "pc": {"inventory": []},
            "clues_found": {"investigation": [], "event": [], "task": [], "npc": []},
            "clue_catalog": {},
            "case_clocks": clocks,
            "case_clock_definitions": definitions or {},
        }

    def test_add_clue_bumps_declared_clue_clarity_clock(self):
        world = self._world({"clue_clarity": 0})
        result = self._add_clue(world)
        self.assertTrue(result.get("ok"))
        self.assertEqual(world["case_clocks"]["clue_clarity"], 1)

    def test_add_clue_respects_declared_max(self):
        definitions = {"clue_clarity": {"max": 5, "levels": {"5": "真相拼合"}}}
        world = self._world({"clue_clarity": 5}, definitions)
        self._add_clue(world)
        self.assertEqual(world["case_clocks"]["clue_clarity"], 5)
        world = self._world({"clue_clarity": 4}, definitions)
        self._add_clue(world)
        self.assertEqual(world["case_clocks"]["clue_clarity"], 5)

    def test_add_clue_without_clock_declaration_is_a_noop(self):
        world = self._world({"monster_manifestation": 0})
        self._add_clue(world)
        self.assertEqual(world["case_clocks"], {"monster_manifestation": 0})

    def test_option_menu_is_not_part_of_completed_narrative(self):
        text = "你仍站在大厅里。\n\n**你可以——**\n1. 前往莱特的办公室查看尸体"
        self.assertEqual(narrative_body(text), "你仍站在大厅里。")

    def test_audit_uses_thinking_compatible_auto_tool_choice(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = WorldStore(Path(temp_dir) / "world")
            store.initialize(resolution_world())
            engine = FakeCommitEngine(store)
            calls = []
            arguments = json.dumps(
                {
                    "scene_id": "",
                    "items_add": [],
                    "items_remove": [],
                    "clues": [],
                    "npc_reveals": [],
                    "flags_set": [],
                    "sanity_events": [],
                    "ending_id": "",
                }
            )
            message = SimpleNamespace(
                tool_calls=[
                    SimpleNamespace(
                        function=SimpleNamespace(arguments=arguments),
                    )
                ]
            )

            def create(**kwargs):
                calls.append(kwargs)
                return SimpleNamespace(choices=[SimpleNamespace(message=message)])

            engine.client = SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(create=create))
            )

            with patch("src.gameplay.turn_reconciler.JUDGEMENT_MODEL", "judge-model"):
                result = reconcile_turn(
                    engine,
                    player_action="我环顾大厅。",
                    narrative="你仍站在大厅里。",
                )

            self.assertEqual(calls[0]["model"], "judge-model")
            self.assertEqual(calls[0]["tool_choice"], "auto")
            self.assertEqual(result, {"applied": [], "skipped": []})

    def test_story_state_commit_skips_second_model_audit(self):
        self.assertFalse(
            turn_needs_model_audit(
                [
                    {
                        "name": "state_add_clue",
                        "output": '{"ok": true}',
                    }
                ]
            )
        )
        self.assertTrue(
            turn_needs_model_audit(
                [
                    {
                        "name": "show_handout",
                        "output": '{"found": true}',
                    }
                ]
            )
        )
        self.assertTrue(
            turn_needs_model_audit(
                [
                    {
                        "name": "state_add_item",
                        "output": "[错误] failed",
                    }
                ]
            )
        )
        self.assertFalse(
            turn_needs_model_audit(
                [
                    {
                        "name": "state_set",
                        "output": '{"ok": true}',
                    }
                ]
            )
        )

    def test_routine_prose_skips_second_model_audit(self):
        self.assertFalse(
            turn_needs_model_audit(
                [],
                player_action="我环顾大厅。",
                narrative="你仍站在大厅里，雨点轻敲着窗玻璃。",
            )
        )

    def test_stateful_prose_keeps_second_model_audit_as_fallback(self):
        self.assertTrue(
            turn_needs_model_audit(
                [],
                player_action="我追问死亡证明。",
                narrative="医生终于承认，那份死亡证明经过了伪造。",
            )
        )

    def test_body_examination_prose_keeps_second_model_audit_as_fallback(self):
        """验尸叙事（揭开白布/遗体显露）必须触发审计兜底——检定回合模型无工具。"""
        narrative = (
            "他没说完，只是又擦了擦额角，最终叹了口气，走到台边，缓缓揭开了白布。\n\n"
            "莱特的遗体露了出来。第一眼看上去——你几乎觉得那不是一具普通的尸体。"
        )
        self.assertTrue(
            turn_needs_model_audit(
                [],
                player_action="请惠特克罗夫特揭开白布，仔细查看莱特的遗体",
                narrative=narrative,
            )
        )

    @staticmethod
    def _keyword_gate_world() -> dict:
        return {
            "current_scene": {"id": "hall", "name": "大厅"},
            "scene_catalog": {
                "hall": {"id": "hall", "name": "大厅"},
                "morgue": {"id": "morgue", "name": "校医院", "description": "地下停尸房。"},
            },
            "npcs": [
                {
                    "id": "doctor",
                    "name": "约翰·惠特克罗夫特医生",
                    "revealed": {"level": 0, "entries": []},
                },
                {
                    "id": "host",
                    "name": "布莱斯·法伦",
                    "revealed": {"level": 1, "entries": []},
                },
            ],
            "clues_found": {"investigation": []},
            "clue_catalog": {
                "body": {
                    "id": "body",
                    "discovery_rules": [{"intent": "examine", "targets": ["莱特的尸体"]}],
                }
            },
        }

    def test_module_keyword_mentions_keep_audit_as_fallback(self):
        """模组关键词表命中即审计：未揭示 NPC（含称谓简写）、其他场景别名、
        未发现线索的 discovery target——全程数据驱动，无领域词。"""
        world = self._keyword_gate_world()

        self.assertTrue(
            turn_needs_model_audit([], narrative="惠特克罗夫特站在几步之外。", world=world)
        )
        self.assertTrue(
            turn_needs_model_audit([], narrative="停尸房里的冷气裹住你的后背。", world=world)
        )
        self.assertTrue(
            turn_needs_model_audit([], narrative="他推开冷柜，莱特的尸体静静躺着。", world=world)
        )

    def test_settled_entities_do_not_trigger_audit(self):
        """实体都已进入权威状态后，提及它们的普通叙事不触发审计。"""
        world = self._keyword_gate_world()
        world["current_scene"] = {"id": "morgue", "name": "校医院"}
        world["npcs"][0]["revealed"] = {"level": 1, "entries": []}
        world["clues_found"]["investigation"].append({"catalog_id": "body"})

        self.assertFalse(
            turn_needs_model_audit(
                [],
                narrative="惠特克罗夫特站在你身边，停尸房里一片安静。",
                world=world,
            )
        )

    @staticmethod
    def _clock_gate_world() -> dict:
        return {
            "current_scene": {"id": "hall", "name": "大厅"},
            "scene_catalog": {"hall": {"id": "hall", "name": "大厅"}},
            "npcs": [],
            "clues_found": {"investigation": []},
            "clue_catalog": {},
            "case_clock_definitions": {
                "monster_manifestation": {
                    "max": 6,
                    "levels": {
                        "0": "只存在幕后。",
                        "1": "局部寒意、电灯闪烁、收音机噪声、电话短暂失真。",
                    },
                    "advance_when": ["调查员长时间拖延、夜间独处。"],
                },
                "human_pressure": {
                    "max": 5,
                    "levels": {"0": "低声议论。"},
                    "advance_when": ["公开指控、粗暴威胁、枪战、非法闯入被看见。"],
                },
            },
        }

    def test_clock_symptom_narrative_triggers_audit(self):
        """时钟征兆/推进情形出现时必须审计，否则时钟永远不记账（真机事故：
        玩家深夜潜入、叙事写了开锁成功，审计未运行，human_pressure 恒 0）。"""
        world = self._clock_gate_world()

        # levels 征兆词段（守秘人按 next_level 原文叙述 → 可靠命中）
        self.assertTrue(
            turn_needs_model_audit(
                [], narrative="走廊尽头只剩下局部寒意，你裹紧了外套。", world=world
            )
        )
        # advance_when 推进词段（玩家行动侧）
        self.assertTrue(
            turn_needs_model_audit(
                [], narrative="深夜的巷子里爆发了枪战，子弹击碎了橱窗。", world=world
            )
        )
        self.assertTrue(
            turn_needs_model_audit(
                [], narrative="你夜间独处在这间旧宅里，只有蜡烛为伴。", world=world
            )
        )
        # 无时钟信号的平淡叙事不触发
        self.assertFalse(
            turn_needs_model_audit([], narrative="你在大厅里踱步，思考下一步。", world=world)
        )

    def test_no_clock_definitions_keeps_old_behavior(self):
        """模组未声明 case_clock_definitions 时时钟阀门不参与判定。"""
        world = self._clock_gate_world()
        del world["case_clock_definitions"]

        self.assertFalse(
            turn_needs_model_audit(
                [], narrative="走廊尽头只剩下局部寒意，你裹紧了外套。", world=world
            )
        )

    def test_clock_keywords_quota_never_starves_action_terms(self):
        """levels 征兆词再多也不得挤掉 advance_when 行动词——真机事故：
        全局/单时钟配额被 levels 占满，「枪战」从未进入信号表，审计整局未跑。"""
        world = self._clock_gate_world()
        monster = world["case_clock_definitions"]["monster_manifestation"]
        monster["levels"] = {
            str(i): f"第{i}级征兆、很长很长的征兆描述{i}、还有补充。" for i in range(20)
        }
        world["case_clock_definitions"]["human_pressure"]["levels"] = {
            str(i): f"第{i}级人类压力征兆、冗长描述{i}。" for i in range(20)
        }
        from src.gameplay.turn_reconciler import _clock_keywords

        keywords = _clock_keywords(world)
        self.assertIn("枪战", keywords)
        self.assertIn("粗暴威胁", keywords)

    def test_periodic_doom_clock_audit(self):
        """声明时钟表的模组每 3 回合强制对账一次（拖延本身推进显形，
        不能只靠关键词信号）；无定义时不参与。"""
        from src.gameplay.turn_reconciler import engine_turn_needs_model_audit

        class _Store:
            def __init__(self, world):
                self._world = world

            def load(self):
                return self._world

        class _Engine:
            def __init__(self, world, round_count):
                self.context = type("Ctx", (), {"world_store": _Store(world)})()
                self._round_count = round_count
                self._turn_mutations = type("M", (), {"has_authoritative_mutation": False})()

        world = self._clock_gate_world()
        quiet = "你在大厅里踱步，思考下一步。"
        self.assertTrue(engine_turn_needs_model_audit(_Engine(world, 0), [], narrative=quiet))
        self.assertTrue(engine_turn_needs_model_audit(_Engine(world, 3), [], narrative=quiet))
        self.assertFalse(engine_turn_needs_model_audit(_Engine(world, 1), [], narrative=quiet))
        no_clocks = self._clock_gate_world()
        del no_clocks["case_clock_definitions"]
        self.assertFalse(engine_turn_needs_model_audit(_Engine(no_clocks, 0), [], narrative=quiet))

    def test_scene_sync_requires_explicit_transition(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = WorldStore(Path(temp_dir) / "world")
            store.initialize(resolution_world())
            engine = FakeCommitEngine(store)

            reconcile_narrative_entities(
                engine,
                "法伦问：莱特的办公室里那面镜子，你已经看过了吗？",
            )
            self.assertEqual(store.load()["current_scene"]["id"], "hall")

            reconcile_narrative_entities(
                engine,
                "你推开沉重的木门，走进莱特的办公室。",
            )
            self.assertEqual(store.load()["current_scene"]["id"], "office")

    def test_narrative_flavor_is_never_promoted_to_a_clue(self):
        world = resolution_world()
        world["current_scene"] = dict(world["scene_catalog"]["office"])
        with tempfile.TemporaryDirectory() as temp_dir:
            store = WorldStore(Path(temp_dir) / "world")
            store.initialize(world)
            engine = FakeCommitEngine(store)

            reconcile_narrative_entities(
                engine,
                "布莱斯·法伦随口说莱特从没请过病假，窗外雨声渐密。",
            )

            self.assertFalse(any(name == "state_add_clue" for name, _args in engine.calls))
            self.assertEqual(
                store.load()["clues_found"],
                world["clues_found"],
            )

    def test_scene_sync_prefers_longest_nested_scene_name(self):
        world = resolution_world()
        world["scene_catalog"].update(
            {
                "campus": {"id": "campus", "name": "密斯卡托尼克大学"},
                "medical": {
                    "id": "medical",
                    "name": "密斯卡托尼克大学医学院",
                    "npcs_present": [],
                },
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            store = WorldStore(Path(temp_dir) / "world")
            store.initialize(world)
            engine = FakeCommitEngine(store)

            reconcile_narrative_entities(
                engine,
                "你站在密斯卡托尼克大学医学院地下停尸房内。",
            )

            self.assertEqual(store.load()["current_scene"]["id"], "medical")


class FinalizeTurnTests(unittest.TestCase):
    def test_empty_opening_fails_before_commit_or_done(self):
        events: list[str] = []
        engine = SimpleNamespace(
            messages=[],
            cb=SimpleNamespace(
                on_error=lambda _message: events.append("error"),
                on_done=lambda: events.append("done"),
            ),
            _complete_turn_record=lambda **_kwargs: events.append("commit"),
        )

        with self.assertRaisesRegex(RuntimeError, "开场模型未生成任何叙述"):
            _finalize_turn(
                {
                    "engine": engine,
                    "opening_turn": True,
                    "narrative": "",
                    "text": "",
                    "tool_calls": [],
                    "executed_tools": [],
                    "turn_had_check": False,
                }
            )

        self.assertEqual(events, [])

    def test_explicit_action_menu_is_emitted_before_done(self):
        events: list[object] = []
        engine = SimpleNamespace(
            messages=[],
            cb=SimpleNamespace(
                on_error=lambda _message: events.append("error"),
                on_choices=lambda choices: events.append(("choices", choices)),
                on_done=lambda: events.append("done"),
            ),
            _reconcile_narrative_entities=lambda _text: None,
            _turn_needs_model_audit=lambda _tools, **_kwargs: False,
            _reconcile_turn=lambda *_args: None,
            _dispatch_narrative_handouts=lambda _text: None,
            save=lambda _slot: events.append("save"),
            _last_turn_high_risk=False,
            _round_count=0,
            _maybe_summarize_after_turn=lambda: None,
        )

        _finalize_turn(
            {
                "engine": engine,
                "narrative": "你看见两件编号证物。\n\n你可以——\n1. 检查门锁\n2. [自由行动] 你决定做什么？",
                "text": "",
                "tool_calls": [],
                "executed_tools": [],
                "turn_had_check": False,
            }
        )

        self.assertEqual(events[0][0], "choices")
        self.assertEqual(events[0][1][0]["label"], "检查门锁")
        self.assertEqual(events[1], "done")

    def test_final_text_is_appended_after_tool_round_narrative(self):
        events: list[str] = []
        engine = SimpleNamespace(
            messages=[],
            cb=SimpleNamespace(
                on_error=lambda _message: events.append("error"),
                on_done=lambda: events.append("done"),
            ),
            _reconcile_narrative_entities=lambda _text: events.append("entities"),
            _turn_needs_model_audit=lambda _tools, **_kwargs: True,
            _reconcile_turn=lambda *_args: events.append("reconcile"),
            _dispatch_narrative_handouts=lambda _text: events.append("handouts"),
            save=lambda _slot: events.append("save"),
            _last_turn_high_risk=False,
            _round_count=0,
            _maybe_summarize_after_turn=lambda: events.append("summary"),
        )

        result = _finalize_turn(
            {
                "engine": engine,
                "user_content": "继续调查",
                "narrative": "第一段叙述。",
                "text": "第二段叙述。",
                "tool_calls": [],
                "executed_tools": [],
                "turn_had_check": False,
            }
        )

        self.assertEqual(result["narrative"], "第一段叙述。\n\n第二段叙述。")
        self.assertEqual(engine.messages[-1]["content"], result["narrative"])
        # 审计默认开启后，stub 宣告需要审计时 reconcile 在 handouts 之前运行。
        self.assertEqual(
            events,
            ["entities", "reconcile", "handouts", "done", "summary"],
        )

    def test_capacity_rejected_turn_rolls_back_appended_messages(self):
        """容量熔断（未发起 provider 请求）的空回合必须回滚本回合追加的消息，
        否则每次重试都叠加一份玩家输入与注入，历史越来越超。"""
        events: list[str] = []
        engine = SimpleNamespace(
            messages=[
                {"role": "system", "content": "keeper"},
                {"role": "user", "content": "[玩家行动] 上一轮"},
                {"role": "assistant", "content": "上一段叙事"},
                {"role": "user", "content": "[玩家行动] 本回合输入"},
            ],
            cb=SimpleNamespace(
                on_error=lambda _message: events.append("error"),
                on_done=lambda: events.append("done"),
            ),
            _capacity_rejected_turn=True,
            _last_turn_high_risk=False,
            _round_count=0,
            _maybe_summarize_after_turn=lambda: None,
        )

        _finalize_turn(
            {
                "engine": engine,
                "user_content": "本回合输入",
                "narrative": "",
                "text": "",
                "tool_calls": [],
                "executed_tools": [],
                "turn_had_check": False,
                "pre_turn_message_len": 3,
            }
        )

        self.assertEqual(events, ["error", "done"])
        self.assertEqual(len(engine.messages), 3)
        self.assertEqual(engine.messages[-1]["content"], "上一段叙事")

    def test_opening_prose_never_runs_the_state_auditor(self):
        events: list[str] = []
        engine = SimpleNamespace(
            messages=[],
            cb=SimpleNamespace(
                on_error=lambda _message: events.append("error"),
                on_done=lambda: events.append("done"),
            ),
            _reconcile_narrative_entities=lambda _text: events.append("entities"),
            _turn_needs_model_audit=lambda _tools, **_kwargs: events.append("audit") or True,
            _reconcile_turn=lambda *_args: events.append("reconcile"),
            _dispatch_narrative_handouts=lambda _text: events.append("handouts"),
            save=lambda _slot: events.append("save"),
            _last_turn_high_risk=False,
            _round_count=0,
            _maybe_summarize_after_turn=lambda: events.append("summary"),
        )

        with patch("src.app.agent_graph.ENABLE_TURN_AUDIT", True):
            _finalize_turn(
                {
                    "engine": engine,
                    "user_content": None,
                    "narrative": "法伦随口说了几句模组未定义的往事。",
                    "text": "",
                    "tool_calls": [],
                    "executed_tools": [],
                    "turn_had_check": False,
                }
            )

        self.assertNotIn("audit", events)
        self.assertNotIn("reconcile", events)


class ToolExecutionSafetyTests(unittest.TestCase):
    def test_complex_tool_switches_followup_to_judgement_model(self):
        engine = SimpleNamespace(
            messages=[],
            narrative_model="story-model",
            judgement_model="judge-model",
            current_model="story-model",
            cb=SimpleNamespace(
                on_tension=lambda *_args: None,
                on_dice=lambda *_args: None,
            ),
            _execute_tool=lambda _name, _args: json.dumps(
                {
                    "spec": "1d20",
                    "rolls": [12],
                    "total": 12,
                    "modifier": 0,
                }
            ),
            _maybe_hint_optional_skill=lambda _name: None,
        )

        with patch("src.app.agent_graph.glm_quick_summary", return_value=None):
            _execute_tools(
                {
                    "engine": engine,
                    "tool_round": 0,
                    "tool_calls": model_tool_calls(
                        engine,
                        [("call_check", "dice_roll", '{"spec":"1d20"}')],
                    ),
                }
            )

        self.assertEqual(engine.current_model, "judge-model")

    def test_tool_exception_becomes_model_visible_error(self):
        def fail_tool(_name: str, _args: dict) -> str:
            raise IsADirectoryError("unexpected directory")

        engine = SimpleNamespace(
            messages=[],
            cb=SimpleNamespace(on_tension=lambda *_args: None),
            _execute_tool=fail_tool,
            _maybe_hint_optional_skill=lambda _name: None,
        )

        result = _execute_tools(
            {
                "engine": engine,
                "tool_round": 0,
                # A model response was legitimately issued a different,
                # harmless catalog; it must still not smuggle read_file into
                # that response.
                "tool_calls": model_tool_calls(
                    engine,
                    [("call_read", "read_file", '{"path": ""}')],
                    issued_names=["dice_roll"],
                ),
            }
        )

        self.assertEqual(
            json.loads(result["executed_tools"][0]["output"])["reason"],
            "model_tool_forbidden",
        )
        self.assertEqual(
            json.loads(engine.messages[-2]["content"])["error"],
            "tool_policy_denied",
        )
        self.assertIn("NPC 直接引语", engine.messages[-1]["content"])

    def test_identical_check_in_one_turn_reuses_result_without_rerolling(self):
        executions = []
        dice_events = []
        output = json.dumps(
            {
                "skill": "psychology",
                "skill_value": 60,
                "d100_roll": 24,
                "level": "困难成功",
            }
        )
        engine = SimpleNamespace(
            messages=[],
            judgement_model="judge-model",
            current_model="story-model",
            cb=SimpleNamespace(
                on_tension=lambda *_args: None,
                on_dice=lambda *args: dice_events.append(args),
            ),
            _execute_tool=lambda name, args: executions.append((name, args)) or output,
            _maybe_hint_optional_skill=lambda _name: None,
        )

        with patch("src.app.agent_graph.glm_quick_summary", return_value=None):
            result = _execute_tools(
                {
                    "engine": engine,
                    "tool_round": 0,
                    "tool_calls": model_tool_calls(
                        engine,
                        [
                            ("first", "skill_check", '{"skill":"psychology"}'),
                            ("duplicate", "skill_check", '{"skill":"psychology"}'),
                        ],
                    ),
                }
            )

        self.assertEqual(len(executions), 1)
        self.assertEqual(len(dice_events), 1)
        self.assertTrue(result["turn_had_check"])

    def test_optional_skill_messages_follow_entire_tool_batch(self):
        engine = SimpleNamespace(
            messages=[],
            cb=SimpleNamespace(on_tension=lambda *_args: None),
            _execute_tool=lambda name, _args: json.dumps({"ok": True, "tool": name}),
            _maybe_hint_optional_skill=lambda name: engine.messages.append(
                {
                    "role": "user",
                    "content": f"loaded {name}",
                }
            ),
        )

        _execute_tools(
            {
                "engine": engine,
                "tool_round": 0,
                "tool_calls": model_tool_calls(
                    engine,
                    [
                        ("call_one", "apply_damage", '{"target":"pc","amount":1}'),
                        (
                            "call_two",
                            "use_item",
                            '{"item":"手电筒","operation":"use","reason":"照明"}',
                        ),
                    ],
                ),
            }
        )

        self.assertEqual(
            [message["role"] for message in engine.messages],
            ["assistant", "tool", "tool", "user", "user", "user"],
        )
        self.assertEqual(engine.messages[1]["tool_call_id"], "call_one")
        self.assertEqual(engine.messages[2]["tool_call_id"], "call_two")
        self.assertIn("同一人物再次开口", engine.messages[-1]["content"])

    def test_sanity_loss_emits_d100_event(self):
        events = []
        engine = SimpleNamespace(
            cb=SimpleNamespace(on_dice=lambda summary, data: events.append((summary, data)))
        )

        _emit_sanity_dice(
            engine,
            json.dumps(
                {
                    "san_roll": 42,
                    "san_before": 70,
                    "san_check_success": True,
                    "actual_loss": 1,
                }
            ),
        )

        self.assertEqual(len(events), 1)
        self.assertIn("SAN -1", events[0][0])
        self.assertEqual(events[0][1]["rolls"], [42])
        self.assertTrue(events[0][1]["sanity"])


if __name__ == "__main__":
    unittest.main()
