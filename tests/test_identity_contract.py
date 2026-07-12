import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.config import SKILL_LOAD_ORDER
from src.engine import GameEngine
from src.persistence import load_system_prompt


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

    def test_control_instruction_is_distinct_from_player_action(self):
        engine = GameEngine.__new__(GameEngine)
        engine.messages = []

        engine.append_control_instruction("读取当前模组状态。")

        self.assertTrue(engine._has_pending_control_instruction())
        self.assertIn("静默执行", engine.messages[-1]["content"])
        self.assertNotIn("[玩家行动]", engine.messages[-1]["content"])

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


if __name__ == "__main__":
    unittest.main()
