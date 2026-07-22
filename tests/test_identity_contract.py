import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.config import PROJECT_ROOT, SKILL_LOAD_ORDER
from src.engine import GameEngine, _thinking_type_for_request
from src.persistence import load_system_prompt
from src.runtime import RuntimeContext
from src.tools import MODEL_TOOLS, model_tools_for


def stream_chunk(content=None, tool_calls=None, finish_reason=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice])


class IdentityContractTests(unittest.TestCase):
    def test_keeper_identity_contract_is_first_in_system_prompt(self):
        self.assertEqual(SKILL_LOAD_ORDER[0], "core/trpg_master.skill")
        prompt = load_system_prompt()
        self.assertTrue(prompt.startswith("# TRPG Master"))
        self.assertIn("玩家不是守秘人", prompt)
        self.assertIn("[引擎控制指令｜非玩家发言]", prompt)

    def test_core_prompt_does_not_seed_module_specific_evidence(self):
        prompt = load_system_prompt(profile="full")

        self.assertIn("不得为衔接层级", prompt)
        self.assertNotIn("手指抓挠的痕迹", prompt)
        self.assertNotIn("管家格里高利三十年前死于大火", prompt)
        self.assertNotIn("地下室的封印正在减弱", prompt)
        self.assertNotIn("一只深潜者从水中浮现", prompt)

        atmosphere = (
            PROJECT_ROOT / "skills" / "keeper" / "keeper_atmosphere.skill"
        ).read_text(encoding="utf-8")
        self.assertIn("事实不可以增殖", atmosphere)
        self.assertNotIn("指纹残留在血迹最薄处", atmosphere)
        self.assertNotIn("腐烂的甜腻气息扑面而来", atmosphere)

    def test_hybrid_prompt_uses_marked_module_spine(self):
        context = SimpleNamespace(
            project_root=PROJECT_ROOT,
            module_dir=PROJECT_ROOT / "mod" / "猩红文档",
        )

        full = load_system_prompt(context, profile="full")
        hybrid = load_system_prompt(context, profile="hybrid")

        self.assertIn("守秘人私有时间线", hybrid)
        self.assertNotIn("# 猩红文档调查压力与解谜节奏 Skill", hybrid)
        self.assertIn("# 猩红文档调查压力与解谜节奏 Skill", full)
        self.assertLess(len(hybrid), len(full) * 0.8)

    def test_opening_prompt_profile_excludes_private_module_material(self):
        context = SimpleNamespace(
            project_root=PROJECT_ROOT,
            module_dir=PROJECT_ROOT / "mod" / "猩红文档",
        )

        opening = load_system_prompt(context, profile="opening")
        hybrid = load_system_prompt(context, profile="hybrid")

        self.assertTrue(opening.startswith("# TRPG Master"))
        self.assertIn("# 防剧透硬约束", opening)
        self.assertIn("# 叙事表现与氛围控制", opening)
        self.assertNotIn("守秘人私有时间线", opening)
        self.assertNotIn("九月关键事件", opening)
        self.assertLess(len(opening), len(hybrid) * 0.7)

    def test_hybrid_prompt_falls_back_when_module_has_no_spine(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            module_dir = Path(temp_dir)
            (module_dir / "module.md").write_text(
                "# Test module\n\nAuthoritative plot facts.",
                encoding="utf-8",
            )
            skill_dir = module_dir / "skills"
            skill_dir.mkdir()
            (skill_dir / "custom.skill").write_text(
                "# Custom module behavior\n\nKeep this behavior.",
                encoding="utf-8",
            )
            context = SimpleNamespace(
                project_root=PROJECT_ROOT,
                module_dir=module_dir,
            )

            full = load_system_prompt(context, profile="full")
            hybrid = load_system_prompt(context, profile="hybrid")

        self.assertEqual(hybrid, full)

    def test_scarlet_opening_is_injected_outside_hybrid_system_prompt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            context = RuntimeContext.create(
                "opening-contract",
                "猩红文档",
                project_root=PROJECT_ROOT,
                runtime_root=Path(temp_dir),
            )
            engine = GameEngine.__new__(GameEngine)
            engine.context = context
            engine.messages = [{
                "role": "user",
                "content": f"{engine.CONTROL_MESSAGE_PREFIX}\n开始新游戏。",
            }]

            self.assertTrue(engine._has_pending_new_game_opening())

            authority = engine._authoritative_turn_context()
            payload = json.loads(
                authority.split("\n", 1)[1].split("\n约束：", 1)[0]
            )

            self.assertGreater(len(payload["module_opening"]), 400)
            self.assertIn("不要压缩为任务摘要", payload["module_opening"])
            self.assertIn("**你可以——**", payload["module_opening"])
            self.assertIn("不得把它们记录成线索", payload["module_opening"])
            self.assertTrue(
                payload["narrative_fact_scope"][
                    "closed_world_for_this_action"
                ]
            )
            self.assertEqual(
                payload["narrative_fact_scope"]["mode"],
                "module_opening",
            )
            self.assertEqual(
                len(payload["narrative_fact_scope"]["opening_public_facts"]),
                3,
            )
            opening_facts = " ".join(
                payload["narrative_fact_scope"]["opening_public_facts"]
            )
            self.assertNotIn("哈兰德·洛奇", opening_facts)
            self.assertNotIn("艾米莉亚·考特", opening_facts)
            self.assertEqual(
                payload["narrative_fact_scope"][
                    "uncatalogued_opening_details"
                ],
                "flavor_only_never_persist_as_clue_or_state",
            )
            self.assertEqual(
                payload["current_scene"]["npcs_present"],
                ["bryce_fallon"],
            )
            self.assertIn("永远只是叙事点缀", authority)

            engine.messages[-1]["content"] = "[玩家行动] 我继续追问法伦。"
            self.assertFalse(engine._has_pending_new_game_opening())
            normal_payload = json.loads(
                engine._authoritative_turn_context()
                .split("\n", 1)[1]
                .split("\n约束：", 1)[0]
            )
            self.assertEqual(normal_payload["module_opening"], "")
            self.assertEqual(
                normal_payload["narrative_fact_scope"]["mode"],
                "normal_turn",
            )

    def test_control_instruction_is_distinct_from_player_action(self):
        engine = GameEngine.__new__(GameEngine)
        engine.messages = []

        engine.append_control_instruction("读取当前模组状态。")

        self.assertTrue(engine._has_pending_control_instruction())
        self.assertIn("静默执行", engine.messages[-1]["content"])
        self.assertNotIn("[玩家行动]", engine.messages[-1]["content"])

    def test_public_npc_names_generate_safe_short_speaker_aliases(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            initial = Path(temp_dir) / "world_state_initial.json"
            initial.write_text('{"npcs": []}', encoding="utf-8")
            world = {
                "npcs": [
                    {"id": "bryce_fallon", "name": "布莱斯·法伦"},
                    {"id": "john_whitcroft", "name": "约翰·惠特克罗夫特医生"},
                ]
            }
            engine = GameEngine.__new__(GameEngine)
            engine.context = SimpleNamespace(
                world_store=SimpleNamespace(load=lambda: world),
                initial_state_file=initial,
            )

            aliases = engine.npc_speaker_aliases()

        self.assertEqual(aliases["法伦"], "bryce_fallon")
        self.assertEqual(aliases["惠特克罗夫特医生"], "john_whitcroft")
        self.assertEqual(aliases["惠特克罗夫特"], "john_whitcroft")

    def test_opening_contract_never_authors_player_dialogue_or_evidence_use(self):
        engine = GameEngine.__new__(GameEngine)
        engine.context = SimpleNamespace(
            project_root=PROJECT_ROOT,
            module_dir=PROJECT_ROOT / "mod" / "mansion_of_madness",
        )

        prompt = engine._opening_system_prompt()

        self.assertIn("不得替调查员说出台词", prompt)
        self.assertIn("展示证物", prompt)
        self.assertIn("第一个真实选择点", prompt)

    def test_first_control_tool_preamble_is_not_shown_or_saved_as_narrative(self):
        tool_call = SimpleNamespace(
            index=0,
            id="call_1",
            function=SimpleNamespace(name="read_file", arguments='{"path":"state.json"}'),
        )
        chunks = [
            stream_chunk(content="好的，守秘人。让我先读取文件。"),
            stream_chunk(tool_calls=[tool_call]),
            stream_chunk(finish_reason="tool_calls"),
        ]
        completions = SimpleNamespace(create=lambda **_kwargs: chunks)
        engine = GameEngine.__new__(GameEngine)
        engine.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        engine.messages = []
        visible = []
        errors = []
        engine.cb = SimpleNamespace(on_narrative=visible.append, on_error=errors.append)

        with patch("src.engine.log_model_call"):
            text, tool_calls = engine._stream_llm("test-model", buffer_if_tools=True)

        self.assertEqual(text, "")
        self.assertEqual(visible, [])
        self.assertEqual(errors, [])
        self.assertEqual(tool_calls[0]["function"]["name"], "read_file")

    def test_buffered_control_text_is_kept_when_model_skips_tools(self):
        chunks = [
            stream_chunk(content="雨落在阿卡姆的石板路上。"),
            stream_chunk(finish_reason="stop"),
        ]
        completions = SimpleNamespace(create=lambda **_kwargs: chunks)
        engine = GameEngine.__new__(GameEngine)
        engine.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        engine.messages = []
        visible = []
        engine.cb = SimpleNamespace(on_narrative=visible.append, on_error=lambda _msg: None)

        with patch("src.engine.log_model_call"):
            text, tool_calls = engine._stream_llm("test-model", buffer_if_tools=True)

        self.assertEqual(text, "雨落在阿卡姆的石板路上。")
        self.assertEqual(visible, [text])
        self.assertEqual(tool_calls, [])

    def test_story_model_only_receives_non_redundant_tools(self):
        names = {
            tool["function"]["name"]
            for tool in MODEL_TOOLS
        }

        self.assertIn("sanity_event", names)
        self.assertNotIn("read_file", names)
        self.assertNotIn("state_npcs", names)
        self.assertNotIn("state_clues", names)
        self.assertNotIn("get_private_memory", names)
        self.assertNotIn("cache_scene", names)
        self.assertNotIn("update_private_memory", names)
        self.assertNotIn("show_handout", names)
        self.assertNotIn("sanity_trigger", names)
        self.assertNotIn("sanity_loss", names)

    def test_role_specific_tool_profiles_keep_transition_tools(self):
        story = {tool["function"]["name"] for tool in model_tools_for("story")}
        combat = {tool["function"]["name"] for tool in model_tools_for("combat")}

        self.assertIn("combat_start", story)
        self.assertNotIn("combat_action", story)
        self.assertNotIn("combat_end", story)
        self.assertIn("combat_action", combat)
        self.assertIn("combat_end", combat)
        self.assertNotIn("combat_start", combat)
        self.assertIn("sanity_event", story & combat)
        self.assertNotIn("create_character", story | combat)

    def test_story_request_uses_dynamic_tool_subset(self):
        captured = {}

        def create(**kwargs):
            captured.update(kwargs)
            return [
                stream_chunk(content="雨声落在窗外。"),
                stream_chunk(finish_reason="stop"),
            ]

        engine = GameEngine.__new__(GameEngine)
        engine.client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        engine.messages = []
        engine.cb = SimpleNamespace(
            on_narrative=lambda _text: None,
            on_error=lambda _message: None,
        )

        with (
            patch("src.engine.ENABLE_DYNAMIC_TOOLS", True),
            patch("src.engine.log_model_call"),
        ):
            engine._stream_llm("test-model")

        names = {tool["function"]["name"] for tool in captured["tools"]}
        self.assertEqual(names, {
            tool["function"]["name"] for tool in model_tools_for("story")
        })

    def test_opening_request_replaces_system_and_omits_tools_without_mutation(self):
        captured = {}

        def create(**kwargs):
            captured.update(kwargs)
            return [
                stream_chunk(content="公开开场。"),
                stream_chunk(finish_reason="stop"),
            ]

        engine = GameEngine.__new__(GameEngine)
        engine.client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        engine.messages = [
            {"role": "system", "content": "private-module-spine"},
            {"role": "user", "content": "structured-opening"},
        ]
        engine.cb = SimpleNamespace(
            on_narrative=lambda _text: None,
            on_error=lambda _message: None,
        )

        with patch("src.engine.log_model_call") as model_log:
            engine._stream_llm(
                "test-model",
                system_prompt_override="public-opening-system",
                enable_tools=False,
                prompt_profile="opening",
            )

        self.assertEqual(
            captured["messages"][0]["content"],
            "public-opening-system",
        )
        self.assertNotIn("tools", captured)
        self.assertNotIn("tool_choice", captured)
        self.assertEqual(engine.messages[0]["content"], "private-module-spine")
        self.assertEqual(model_log.call_args.kwargs["prompt_profile"], "opening")
        self.assertEqual(model_log.call_args.kwargs["tool_schema_chars"], 2)

    def test_auto_thinking_only_disables_official_deepseek_flash_story(self):
        with (
            patch("src.engine.STORY_THINKING_MODE", "auto"),
            patch("src.engine.BASE_URL", "https://api.deepseek.com"),
            patch("src.engine.MODEL_FLASH", "deepseek-v4-flash"),
        ):
            self.assertEqual(
                _thinking_type_for_request("deepseek-v4-flash", "story"),
                "disabled",
            )
            self.assertIsNone(
                _thinking_type_for_request("deepseek-v4-flash", "combat")
            )
            self.assertIsNone(
                _thinking_type_for_request("deepseek-v4-pro", "story")
            )

        with patch("src.engine.BASE_URL", "https://example.test/v1"):
            self.assertIsNone(
                _thinking_type_for_request("deepseek-v4-flash", "story")
            )

    def test_story_request_sends_selected_thinking_override(self):
        captured = {}

        def create(**kwargs):
            captured.update(kwargs)
            return [
                stream_chunk(content="雨声落在窗外。"),
                stream_chunk(finish_reason="stop"),
            ]

        engine = GameEngine.__new__(GameEngine)
        engine.client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        engine.messages = []
        engine.cb = SimpleNamespace(
            on_narrative=lambda _text: None,
            on_error=lambda _message: None,
        )

        with (
            patch("src.engine.STORY_THINKING_MODE", "disabled"),
            patch("src.engine.log_model_call"),
        ):
            engine._stream_llm("test-model")

        self.assertEqual(
            captured.get("extra_body"),
            {"thinking": {"type": "disabled"}},
        )

    def test_stream_usage_is_forwarded_to_model_log(self):
        usage = SimpleNamespace(model_dump=lambda: {
            "prompt_tokens": 1200,
            "completion_tokens": 20,
            "total_tokens": 1220,
            "prompt_cache_hit_tokens": 1100,
            "prompt_cache_miss_tokens": 100,
        })
        chunks = [
            stream_chunk(content="缓存测试。"),
            stream_chunk(finish_reason="stop"),
            SimpleNamespace(choices=[], usage=usage),
        ]
        captured_kwargs = {}

        def create(**kwargs):
            captured_kwargs.update(kwargs)
            return chunks

        engine = GameEngine.__new__(GameEngine)
        engine.client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        engine.messages = [{"role": "system", "content": "stable-prefix"}]
        engine.cb = SimpleNamespace(
            on_narrative=lambda _text: None,
            on_error=lambda _message: None,
        )

        with (
            patch("src.engine.ENABLE_STREAM_USAGE", True),
            patch("src.engine.log_model_call") as model_log,
        ):
            engine._stream_llm("test-model")

        self.assertEqual(
            captured_kwargs.get("stream_options"),
            {"include_usage": True},
        )
        self.assertEqual(model_log.call_args.kwargs["usage"]["prompt_tokens"], 1200)
        self.assertEqual(model_log.call_args.kwargs["system_chars"], 13)
        diagnostic = engine._turn_diagnostics[-1]
        self.assertEqual(diagnostic["usage"]["prompt_tokens"], 1200)
        self.assertEqual(diagnostic["context_sections"]["system"]["chars"], 13)
        self.assertGreater(
            diagnostic["context_sections"]["tool_schema"]["estimated_tokens"],
            0,
        )

    def test_authoritative_context_closes_resolved_fact_scope(self):
        world = {
            "module_meta": {"id": "test", "era": "1920s"},
            "pc": {"name": "调查员", "inventory": []},
            "current_scene": {
                "id": "room",
                "name": "房间",
                "npcs_present": ["witness"],
            },
            "npcs": [{
                "id": "witness",
                "name": "目击者",
                "secret": "绝不能进入最近一条模型消息的幕后秘密",
                "disposition": "guarded",
                "revealed": {"level": 0, "entries": []},
            }],
            "clues_found": {},
            "clue_catalog": {},
            "module_rules": {},
            "flags": {},
        }
        engine = GameEngine.__new__(GameEngine)
        engine.context = SimpleNamespace(
            world_store=SimpleNamespace(load=lambda: world)
        )
        engine.messages = []

        context = engine._authoritative_turn_context(
            check_result={"skill": "spot_hidden", "success": True},
            resolved_discoveries=[{
                "discovered": True,
                "text": "窗框上有一道已确认的擦痕。",
            }],
        )
        raw_payload = context.split("\n", 1)[1].split("\n约束：", 1)[0]
        payload = json.loads(raw_payload)

        self.assertTrue(
            payload["narrative_fact_scope"]["closed_world_for_this_action"]
        )
        self.assertEqual(
            payload["narrative_fact_scope"]["newly_confirmed_facts"],
            ["窗框上有一道已确认的擦痕。"],
        )
        self.assertNotIn("绝不能进入", context)
        self.assertIn("不是扩写提纲", context)

    def test_empty_transport_failure_retries_once(self):
        calls = []

        def create(**_kwargs):
            calls.append(True)
            if len(calls) == 1:
                def broken_stream():
                    raise ConnectionError("stream closed")
                    yield

                return broken_stream()
            return [
                stream_chunk(content="重连后继续叙述。"),
                stream_chunk(finish_reason="stop"),
            ]

        engine = GameEngine.__new__(GameEngine)
        engine.client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        engine.messages = []
        visible = []
        errors = []
        engine.cb = SimpleNamespace(on_narrative=visible.append, on_error=errors.append)

        with (
            patch("src.engine.log_model_call"),
            patch("src.engine.log_error"),
            patch("src.engine.time.sleep"),
        ):
            text, tool_calls = engine._stream_llm("test-model")

        self.assertEqual(len(calls), 2)
        self.assertEqual(text, "重连后继续叙述。")
        self.assertEqual(visible, [text])
        self.assertEqual(errors, [])
        self.assertEqual(tool_calls, [])

    def test_normal_empty_response_retries_once(self):
        calls = []

        def create(**_kwargs):
            calls.append(True)
            if len(calls) == 1:
                return [stream_chunk(finish_reason="stop")]
            return [
                stream_chunk(content="第二次请求恢复叙述。"),
                stream_chunk(finish_reason="stop"),
            ]

        engine = GameEngine.__new__(GameEngine)
        engine.client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        engine.messages = []
        visible = []
        engine.cb = SimpleNamespace(
            on_narrative=visible.append,
            on_error=lambda _message: None,
        )

        with (
            patch("src.engine.log_model_call"),
            patch("src.engine.log_error"),
            patch("src.engine.time.sleep"),
        ):
            text, tool_calls = engine._stream_llm("test-model")

        self.assertEqual(len(calls), 2)
        self.assertEqual(text, "第二次请求恢复叙述。")
        self.assertEqual(visible, [text])
        self.assertEqual(tool_calls, [])

    def test_opening_empty_retry_preserves_public_request_settings(self):
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return [stream_chunk(finish_reason="stop")]
            return [
                stream_chunk(content="重试后的公开开场。"),
                stream_chunk(finish_reason="stop"),
            ]

        engine = GameEngine.__new__(GameEngine)
        engine.client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        engine.messages = [
            {"role": "system", "content": "private-module-spine"},
            {"role": "user", "content": "structured-opening"},
        ]
        engine.cb = SimpleNamespace(
            on_narrative=lambda _text: None,
            on_error=lambda _message: None,
        )

        with (
            patch("src.engine.log_model_call"),
            patch("src.engine.log_error"),
            patch("src.engine.time.sleep"),
        ):
            text, tool_calls = engine._stream_llm(
                "test-model",
                system_prompt_override="public-opening-system",
                enable_tools=False,
                prompt_profile="opening",
                temperature=0.65,
            )

        self.assertEqual(text, "重试后的公开开场。")
        self.assertEqual(tool_calls, [])
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertEqual(
                call["messages"][0]["content"],
                "public-opening-system",
            )
            self.assertEqual(call["temperature"], 0.65)
            self.assertNotIn("tools", call)

    def test_partial_transport_failure_finishes_with_received_text(self):
        calls = []

        def create(**_kwargs):
            calls.append(True)

            def partial_stream():
                yield stream_chunk(content="已经收到的半段叙述。")
                raise ConnectionError("stream closed")

            return partial_stream()

        engine = GameEngine.__new__(GameEngine)
        engine.client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        engine.messages = []
        visible = []
        errors = []
        engine.cb = SimpleNamespace(on_narrative=visible.append, on_error=errors.append)

        with patch("src.engine.log_model_call"), patch("src.engine.log_error"):
            text, tool_calls = engine._stream_llm("test-model")

        self.assertEqual(len(calls), 1)
        self.assertEqual(text, "已经收到的半段叙述。")
        self.assertEqual(visible, [text])
        self.assertEqual(len(errors), 1)
        self.assertEqual(tool_calls, [])

    def test_internal_control_sentence_is_filtered_across_stream_chunks(self):
        chunks = [
            stream_chunk(content="让我先确认当前的"),
            stream_chunk(content="信息边界。你仍站在停尸房。"),
            stream_chunk(finish_reason="stop"),
        ]
        engine = GameEngine.__new__(GameEngine)
        engine.client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **_kwargs: chunks)
            )
        )
        engine.messages = []
        visible = []
        engine.cb = SimpleNamespace(
            on_narrative=visible.append,
            on_error=lambda _message: None,
        )

        with patch("src.engine.log_model_call"):
            text, tool_calls = engine._stream_llm("test-model")

        self.assertEqual(text, "你仍站在停尸房。")
        self.assertEqual(visible, ["你仍站在停尸房。"])
        self.assertEqual(tool_calls, [])

    def test_story_streams_each_provider_delta_after_first_sentence_guard(self):
        chunks = [
            stream_chunk(content="雨落在阿卡姆。"),
            stream_chunk(content="你"),
            stream_chunk(content="推开"),
            stream_chunk(content="了门。"),
            stream_chunk(finish_reason="stop"),
        ]
        engine = GameEngine.__new__(GameEngine)
        engine.client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **_kwargs: chunks)
            )
        )
        engine.messages = []
        visible = []
        engine.cb = SimpleNamespace(
            on_narrative=visible.append,
            on_error=lambda _message: None,
        )

        with patch("src.engine.log_model_call"):
            text, tool_calls = engine._stream_llm("test-model")

        self.assertEqual(text, "雨落在阿卡姆。你推开了门。")
        self.assertEqual(visible, ["雨落在阿卡姆。", "你", "推开", "了门。"])
        self.assertEqual(tool_calls, [])

    def test_dsml_tool_protocol_is_parsed_without_leaking_private_arguments(self):
        secret = "法伦私下调查死因，不愿让玩家知道。"
        protocol = (
            '<｜DSML｜tool_calls><｜DSML｜invoke name="npc_reveal">'
            '<｜DSML｜parameter name="npc_id" string="true">bryce_fallon'
            '</｜DSML｜parameter><｜DSML｜parameter name="tier" integer="1">1'
            '</｜DSML｜parameter><｜DSML｜parameter name="entry_text" string="true">'
            f'{secret}</｜DSML｜parameter></｜DSML｜invoke></｜DSML｜tool_calls>'
        )
        # Every boundary is deliberately hostile, including a split start marker.
        chunks = [stream_chunk(content="门外仍在下雨。<｜DS")]
        chunks.extend(stream_chunk(content=char) for char in protocol[len("<｜DS"):])
        chunks.extend([
            stream_chunk(content="你听见走廊尽头传来脚步声。"),
            stream_chunk(finish_reason="stop"),
        ])
        engine = GameEngine.__new__(GameEngine)
        engine.client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **_kwargs: chunks)
            )
        )
        engine.messages = []
        visible = []
        engine.cb = SimpleNamespace(
            on_narrative=visible.append,
            on_error=lambda _message: None,
        )

        with patch("src.engine.log_model_call"), patch("src.engine.log_error"):
            text, tool_calls = engine._stream_llm("test-model")

        rendered = "".join(visible)
        self.assertEqual(text, "门外仍在下雨。你听见走廊尽头传来脚步声。")
        self.assertEqual(rendered, text)
        self.assertNotIn("DSML", rendered)
        self.assertNotIn(secret, rendered)
        self.assertEqual(tool_calls[0]["function"]["name"], "npc_reveal")
        arguments = json.loads(tool_calls[0]["function"]["arguments"])
        self.assertEqual(arguments["npc_id"], "bryce_fallon")
        self.assertEqual(arguments["tier"], 1)
        self.assertEqual(arguments["entry_text"], secret)

    def test_unclosed_dsml_protocol_is_dropped_instead_of_rendered(self):
        chunks = [
            stream_chunk(content="公开文本。<|DSML|tool_calls>"),
            stream_chunk(content='<|DSML|invoke name="npc_reveal">绝密内容'),
            stream_chunk(finish_reason="stop"),
        ]
        engine = GameEngine.__new__(GameEngine)
        engine.client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **_kwargs: chunks)
            )
        )
        engine.messages = []
        visible = []
        engine.cb = SimpleNamespace(
            on_narrative=visible.append,
            on_error=lambda _message: None,
        )

        with patch("src.engine.log_model_call"), patch("src.engine.log_error"):
            text, tool_calls = engine._stream_llm("test-model")

        self.assertEqual(text, "公开文本。")
        self.assertEqual("".join(visible), text)
        self.assertNotIn("绝密内容", "".join(visible))
        self.assertEqual(tool_calls, [])

    def test_repeated_fullwidth_bar_dsml_is_parsed_without_leaking(self):
        secret = "法伦知道真相但没有公开。"
        protocol = (
            '<｜｜DSML｜｜tool_calls><｜｜DSML｜｜invoke name="npc_reveal">'
            '<｜｜DSML｜｜parameter name="npc_id" string="true">bryce_fallon'
            '</｜｜DSML｜｜parameter><｜｜DSML｜｜parameter name="tier" integer="true">2'
            '</｜｜DSML｜｜parameter><｜｜DSML｜｜parameter name="entry_text" string="true">'
            f'{secret}</｜｜DSML｜｜parameter></｜｜DSML｜｜invoke>'
            '</｜｜DSML｜｜tool_calls>'
        )
        chunks = [stream_chunk(content=char) for char in "公开叙事。" + protocol]
        chunks.append(stream_chunk(finish_reason="stop"))
        engine = GameEngine.__new__(GameEngine)
        engine.client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **_kwargs: chunks)
            )
        )
        engine.messages = []
        visible = []
        engine.cb = SimpleNamespace(
            on_narrative=visible.append,
            on_error=lambda _message: None,
        )

        with patch("src.engine.log_model_call"), patch("src.engine.log_error"):
            text, tool_calls = engine._stream_llm("test-model")

        self.assertEqual(text, "公开叙事。")
        self.assertEqual("".join(visible), text)
        self.assertNotIn(secret, "".join(visible))
        self.assertEqual(tool_calls[0]["function"]["name"], "npc_reveal")
        arguments = json.loads(tool_calls[0]["function"]["arguments"])
        self.assertEqual(arguments["tier"], 2)
        self.assertEqual(arguments["entry_text"], secret)

    def test_speech_start_callback_receives_npc_id_not_empty_piece_slot(self):
        chunks = [
            stream_chunk(content="【npc:bryce_fallon】“请坐。”【/npc】"),
            stream_chunk(finish_reason="stop"),
        ]
        engine = GameEngine.__new__(GameEngine)
        engine.client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **_kwargs: chunks)
            )
        )
        engine.messages = []
        visible = []
        speakers = []
        engine.is_valid_npc_id = lambda npc_id: npc_id == "bryce_fallon"
        engine.cb = SimpleNamespace(
            on_narrative=lambda text, npc_id=None: visible.append((text, npc_id)),
            on_speaker_segment=speakers.append,
            on_error=lambda _message: None,
        )

        with patch("src.engine.log_model_call"):
            text, tool_calls = engine._stream_llm("test-model")

        self.assertEqual(speakers, ["bryce_fallon"])
        self.assertEqual(visible, [("“请坐。”", "bryce_fallon")])
        self.assertEqual(text, "【npc:bryce_fallon】“请坐。”【/npc】")
        self.assertEqual(tool_calls, [])


if __name__ == "__main__":
    unittest.main()
