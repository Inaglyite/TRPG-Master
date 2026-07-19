import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.action_checks import infer_action_check, infer_scene_transition
from src.agent_graph import (
    _call_combat_agent,
    _call_story_agent,
    _emit_sanity_dice,
    _execute_tools,
    _finalize_turn,
    _prepare_turn,
)
from src.turn_reconciler import (
    apply_turn_commit,
    narrative_body,
    reconcile_narrative_entities,
    reconcile_turn,
    turn_needs_model_audit,
)
from src.world_store import WorldStore


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
        "npcs": [{
            "id": "fallon",
            "name": "布莱斯·法伦",
            "revealed": {"level": 0, "entries": []},
        }],
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
        self.assertIsNone(
            infer_action_check("我问法伦能不能让我搜查办公室", world)
        )
        self.assertIsNone(
            infer_action_check("我问医生：你仔细检查过莱特的遗体吗？", world)
        )
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
        self.assertIsNone(
            infer_scene_transition("我问法伦怎么去莱特的办公室。", world)
        )


class StoryStreamingTests(unittest.TestCase):
    def test_prepare_turn_uses_narrative_model_without_marking_routine_turn_risky(self):
        engine = SimpleNamespace(
            current_model="old-model",
            _has_pending_control_instruction=lambda: False,
            _has_pending_new_game_opening=lambda: False,
        )

        with patch("src.agent_graph.NARRATIVE_MODEL", "story-model"):
            result = _prepare_turn({"engine": engine, "user_content": None})

        self.assertEqual(engine.current_model, "story-model")
        self.assertFalse(result["turn_had_check"])
        self.assertFalse(result["opening_turn"])

    def test_control_turn_does_not_buffer_normal_opening_narrative(self):
        calls = []

        def stream(model, **kwargs):
            calls.append((model, kwargs))
            return "开场。", []

        engine = SimpleNamespace(current_model="flash", _stream_llm=stream)

        result = _call_story_agent({
            "engine": engine,
            "control_turn": True,
            "tool_round": 0,
        })

        self.assertEqual(result, {"text": "开场。", "tool_calls": []})
        self.assertFalse(calls[0][1]["buffer_if_tools"])

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

        result = _call_story_agent({
            "engine": engine,
            "opening_turn": True,
        })

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
                state["clues_found"][args["category"]].append({
                    "id": args.get("clue_id", "generated"),
                    "text": args["text"],
                })
            elif name == "npc_reveal":
                npc = next(item for item in state["npcs"] if item["id"] == args["npc_id"])
                npc["revealed"]["entries"].append({
                    "tier": args["tier"],
                    "text": args["entry_text"],
                })
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
                "clues": [{
                    "text": "裂镜的一角像被高热熔化。",
                    "category": "investigation",
                    "clue_id": "melted_mirror",
                }],
                "npc_reveals": [{
                    "npc_id": "fallon",
                    "tier": 1,
                    "text": "法伦显得异常紧张。",
                }],
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

    def test_option_menu_is_not_part_of_completed_narrative(self):
        text = "你仍站在大厅里。\n\n**你可以——**\n1. 前往莱特的办公室查看尸体"
        self.assertEqual(narrative_body(text), "你仍站在大厅里。")

    def test_audit_uses_thinking_compatible_auto_tool_choice(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = WorldStore(Path(temp_dir) / "world")
            store.initialize(resolution_world())
            engine = FakeCommitEngine(store)
            calls = []
            arguments = json.dumps({
                "scene_id": "",
                "items_add": [],
                "items_remove": [],
                "clues": [],
                "npc_reveals": [],
                "flags_set": [],
                "sanity_events": [],
                "ending_id": "",
            })
            message = SimpleNamespace(tool_calls=[SimpleNamespace(
                function=SimpleNamespace(arguments=arguments),
            )])

            def create(**kwargs):
                calls.append(kwargs)
                return SimpleNamespace(choices=[SimpleNamespace(message=message)])

            engine.client = SimpleNamespace(
                chat=SimpleNamespace(completions=SimpleNamespace(create=create))
            )

            with patch("src.turn_reconciler.JUDGEMENT_MODEL", "judge-model"):
                result = reconcile_turn(
                    engine,
                    player_action="我环顾大厅。",
                    narrative="你仍站在大厅里。",
                )

            self.assertEqual(calls[0]["model"], "judge-model")
            self.assertEqual(calls[0]["tool_choice"], "auto")
            self.assertEqual(result, {"applied": [], "skipped": []})

    def test_story_state_commit_skips_second_model_audit(self):
        self.assertFalse(turn_needs_model_audit([{
            "name": "state_add_clue",
            "output": '{"ok": true}',
        }]))
        self.assertTrue(turn_needs_model_audit([{
            "name": "show_handout",
            "output": '{"found": true}',
        }]))
        self.assertTrue(turn_needs_model_audit([{
            "name": "state_add_item",
            "output": "[错误] failed",
        }]))
        self.assertFalse(turn_needs_model_audit([{
            "name": "state_set",
            "output": '{"ok": true}',
        }]))

    def test_routine_prose_skips_second_model_audit(self):
        self.assertFalse(turn_needs_model_audit(
            [],
            player_action="我环顾大厅。",
            narrative="你仍站在大厅里，雨点轻敲着窗玻璃。",
        ))

    def test_stateful_prose_keeps_second_model_audit_as_fallback(self):
        self.assertTrue(turn_needs_model_audit(
            [],
            player_action="我追问死亡证明。",
            narrative="医生终于承认，那份死亡证明经过了伪造。",
        ))

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

            self.assertFalse(any(
                name == "state_add_clue" for name, _args in engine.calls
            ))
            self.assertEqual(
                store.load()["clues_found"],
                world["clues_found"],
            )

    def test_scene_sync_prefers_longest_nested_scene_name(self):
        world = resolution_world()
        world["scene_catalog"].update({
            "campus": {"id": "campus", "name": "密斯卡托尼克大学"},
            "medical": {
                "id": "medical",
                "name": "密斯卡托尼克大学医学院",
                "npcs_present": [],
            },
        })
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

        _finalize_turn({
            "engine": engine,
            "narrative": "你看见两件编号证物。\n\n你可以——\n1. 检查门锁\n2. [自由行动] 你决定做什么？",
            "text": "",
            "tool_calls": [],
            "executed_tools": [],
            "turn_had_check": False,
        })

        self.assertEqual(events[0], "save")
        self.assertEqual(events[1][0], "choices")
        self.assertEqual(events[1][1][0]["label"], "检查门锁")
        self.assertEqual(events[2], "done")

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

        result = _finalize_turn({
            "engine": engine,
            "user_content": "继续调查",
            "narrative": "第一段叙述。",
            "text": "第二段叙述。",
            "tool_calls": [],
            "executed_tools": [],
            "turn_had_check": False,
        })

        self.assertEqual(result["narrative"], "第一段叙述。\n\n第二段叙述。")
        self.assertEqual(engine.messages[-1]["content"], result["narrative"])
        self.assertEqual(
            events,
            ["entities", "handouts", "save", "done", "summary"],
        )

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

        with patch("src.agent_graph.ENABLE_TURN_AUDIT", True):
            _finalize_turn({
                "engine": engine,
                "user_content": None,
                "narrative": "法伦随口说了几句模组未定义的往事。",
                "text": "",
                "tool_calls": [],
                "executed_tools": [],
                "turn_had_check": False,
            })

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
            _execute_tool=lambda _name, _args: json.dumps({
                "spec": "1d20",
                "rolls": [12],
                "total": 12,
                "modifier": 0,
            }),
            _maybe_hint_optional_skill=lambda _name: None,
        )

        with patch("src.agent_graph.glm_quick_summary", return_value=None):
            _execute_tools({
                "engine": engine,
                "tool_round": 0,
                "tool_calls": [{
                    "id": "call_check",
                    "function": {
                        "name": "dice_roll",
                        "arguments": '{"spec":"1d20"}',
                    },
                }],
            })

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

        result = _execute_tools({
            "engine": engine,
            "tool_round": 0,
            "tool_calls": [{
                "id": "call_read",
                "function": {
                    "name": "read_file",
                    "arguments": '{"path": ""}',
                },
            }],
        })

        self.assertIn("[错误]", result["executed_tools"][0]["output"])
        self.assertIn("[错误]", engine.messages[-2]["content"])
        self.assertIn("NPC 直接引语", engine.messages[-1]["content"])

    def test_identical_check_in_one_turn_reuses_result_without_rerolling(self):
        executions = []
        dice_events = []
        output = json.dumps({
            "skill": "psychology",
            "skill_value": 60,
            "d100_roll": 24,
            "level": "困难成功",
        })
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
        def call(call_id):
            return {
                "id": call_id,
                "function": {
                    "name": "skill_check",
                    "arguments": '{"skill":"psychology"}',
                },
            }

        with patch("src.agent_graph.glm_quick_summary", return_value=None):
            result = _execute_tools({
                "engine": engine,
                "tool_round": 0,
                "tool_calls": [call("first"), call("duplicate")],
            })

        self.assertEqual(len(executions), 1)
        self.assertEqual(len(dice_events), 1)
        self.assertTrue(result["turn_had_check"])

    def test_optional_skill_messages_follow_entire_tool_batch(self):
        engine = SimpleNamespace(
            messages=[],
            cb=SimpleNamespace(on_tension=lambda *_args: None),
            _execute_tool=lambda name, _args: json.dumps({"ok": True, "tool": name}),
            _maybe_hint_optional_skill=lambda name: engine.messages.append({
                "role": "user",
                "content": f"loaded {name}",
            }),
        )

        _execute_tools({
            "engine": engine,
            "tool_round": 0,
            "tool_calls": [
                {
                    "id": "call_one",
                    "function": {"name": "state_get", "arguments": "{}"},
                },
                {
                    "id": "call_two",
                    "function": {"name": "state_clues", "arguments": "{}"},
                },
            ],
        })

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

        _emit_sanity_dice(engine, json.dumps({
            "san_roll": 42,
            "san_before": 70,
            "san_check_success": True,
            "actual_loss": 1,
        }))

        self.assertEqual(len(events), 1)
        self.assertIn("SAN -1", events[0][0])
        self.assertEqual(events[0][1]["rolls"], [42])
        self.assertTrue(events[0][1]["sanity"])


if __name__ == "__main__":
    unittest.main()
