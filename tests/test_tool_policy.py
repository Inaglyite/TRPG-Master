import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.agent_graph import _execute_tools, _route_after_tools
from src.config import MAX_TOOL_ROUNDS
from src.engine import GameEngine
from src.logger import _brief
from src.tool_policy import (
    ENGINE_INTERNAL_CALLER,
    MODEL_CALLER,
    REQUEST_METADATA_KEY,
    ToolRequestSnapshot,
    attach_request_snapshot,
    public_tool_call,
)
from src.tools import MODEL_TOOLS, execute_function, tool_catalog_for_names


def stream_chunk(*, content=None, tool_calls=None, finish_reason=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            delta=SimpleNamespace(content=content, tool_calls=tool_calls),
            finish_reason=finish_reason,
        )]
    )


def model_call(
    call_id: str,
    name: str,
    arguments: str,
    *,
    allowed_names: list[str] | None = None,
) -> dict:
    catalog = tool_catalog_for_names(allowed_names or [name])
    snapshot = ToolRequestSnapshot.create(
        step=1,
        profile="story:test",
        caller=MODEL_CALLER,
        tools=catalog,
    )
    return attach_request_snapshot(
        {
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": arguments},
        },
        snapshot,
    )


class ToolPolicyExecutionTests(unittest.TestCase):
    def _engine(self):
        calls = []
        engine = SimpleNamespace(
            messages=[],
            cb=SimpleNamespace(on_tension=lambda *_args: None),
            _execute_tool=lambda name, args: calls.append((name, args)) or '{"ok":true}',
            _maybe_hint_optional_skill=lambda _name: None,
        )
        return engine, calls

    def _run(self, call: dict):
        engine, calls = self._engine()
        result = _execute_tools({"engine": engine, "tool_round": 0, "tool_calls": [call]})
        return engine, calls, result

    def test_missing_snapshot_replay_is_rejected_without_execution(self):
        engine, calls, result = self._run({
            "id": "replay-1",
            "function": {"name": "state_add_item", "arguments": '{"item":"钥匙"}'},
        })

        self.assertEqual(calls, [])
        self.assertEqual(
            json.loads(result["executed_tools"][0]["output"])["reason"],
            "missing_request_snapshot",
        )
        self.assertEqual(engine.messages[1]["tool_call_id"], "replay-1")

    def test_unlisted_dsml_or_native_name_is_rejected_without_execution(self):
        secret_path = "private/法伦绝密档案.txt"
        call = model_call(
            "call-1", "read_file", json.dumps({"path": secret_path}, ensure_ascii=False),
            allowed_names=["state_add_item"],
        )
        engine, calls, result = self._run(call)

        self.assertEqual(calls, [])
        output = result["executed_tools"][0]["output"]
        self.assertEqual(
            json.loads(output)["reason"],
            "model_tool_forbidden",
        )
        self.assertNotIn(secret_path, output)
        self.assertNotIn(secret_path, engine.messages[1]["content"])

    def test_state_get_and_state_set_are_forbidden_even_with_forged_catalog(self):
        for name, arguments in (
            ("state_get", '{"path":"npcs.0.secret"}'),
            ("state_set", '{"path":"flags.open","value":"true"}'),
        ):
            with self.subTest(name=name):
                _engine, calls, result = self._run(model_call(
                    f"call-{name}", name, arguments, allowed_names=[name]
                ))
                self.assertEqual(calls, [])
                self.assertEqual(
                    json.loads(result["executed_tools"][0]["output"])["reason"],
                    "model_tool_forbidden",
                )

    def test_unknown_and_malformed_arguments_do_not_reach_handler(self):
        cases = (
            ('{"item":"钥匙","unexpected":"secret"}', "unknown_argument"),
            ("{not-json", "invalid_arguments"),
        )
        for arguments, expected_reason in cases:
            with self.subTest(arguments=arguments):
                _engine, calls, result = self._run(model_call(
                    "call-args", "state_add_item", arguments
                ))
                self.assertEqual(calls, [])
                self.assertEqual(
                    json.loads(result["executed_tools"][0]["output"])["reason"],
                    expected_reason,
                )

    def test_valid_snapshotted_call_executes_once_and_persists_public_shape_only(self):
        call = model_call("call-ok", "state_add_item", '{"item":"钥匙"}')
        engine, calls, result = self._run(call)

        self.assertEqual(calls, [("state_add_item", {"item": "钥匙"})])
        self.assertEqual(result["executed_tools"][0]["name"], "state_add_item")
        self.assertNotIn(REQUEST_METADATA_KEY, engine.messages[0]["tool_calls"][0])
        self.assertEqual(public_tool_call(call)["id"], "call-ok")

    def test_tool_round_limit_is_exactly_maximum_number_of_batches(self):
        self.assertEqual(
            _route_after_tools({"tool_round": MAX_TOOL_ROUNDS - 1, "engine": SimpleNamespace(_combat_active=lambda: False)}),
            "call_story_agent",
        )
        self.assertEqual(
            _route_after_tools({"tool_round": MAX_TOOL_ROUNDS, "engine": SimpleNamespace(_combat_active=lambda: False)}),
            "finalize",
        )

    def test_operational_tool_log_redacts_textual_arguments(self):
        secret = "法伦在地下室藏了一具尸体"
        rendered = _brief({"entry_text": secret, "tier": 2, "npc_id": "fallon"})
        self.assertNotIn(secret, rendered)
        self.assertIn("entry_text=<str:", rendered)
        self.assertIn("tier=2", rendered)

    def test_direct_execution_cannot_bypass_model_snapshot_or_private_state_policy(self):
        model_result = json.loads(execute_function(
            "state_add_item",
            {"item": "钥匙"},
            caller=MODEL_CALLER,
        ))
        self.assertEqual(model_result["error"], "model_tool_forbidden")

        private_result = json.loads(execute_function(
            "state_get",
            {"path": "npcs.0.secret"},
            caller=ENGINE_INTERNAL_CALLER,
        ))
        self.assertEqual(private_result["error"], "engine_state_path_forbidden")


class ModelRequestSnapshotTests(unittest.TestCase):
    def test_request_envelope_golden_fixture_is_digest_only_and_stable(self):
        engine = GameEngine.__new__(GameEngine)
        engine.client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **_kwargs: [stream_chunk(finish_reason="stop")]
                )
            )
        )
        engine.messages = [
            {"role": "system", "content": "# Core\nDo not spoil."},
            {"role": "user", "content": "[玩家行动] 我检查书桌。"},
        ]
        engine.cb = SimpleNamespace(on_narrative=lambda *_args: None, on_error=lambda _msg: None)

        with patch("src.engine.log_model_call"), patch("src.engine.time.sleep"):
            engine._stream_llm("test-model", _retry_on_empty=False)

        envelope = engine._turn_diagnostics[-1]["request_envelope"]
        self.assertEqual(
            envelope["message_digest"],
            "df6f5c46632165bc9bddb301087f34ad5ec3b5d0598b79333d91232c7874507e",
        )
        self.assertEqual(
            envelope["context_section_digests"]["system"],
            "d4f92c48bb015c87a41f674b743e05614d4754062dc6326a2638695a8cdddc8f",
        )
        self.assertEqual(
            envelope["context_section_digests"]["history"],
            "1a7b24f5beb9ed0ddddcf61a04a8fdcb084819c92d2d96044039bbeb43173050",
        )
        self.assertEqual(
            envelope["context_section_digests"]["tools"],
            "d0cc668612bff31820ae87df39ecf210da43482f32781af9b84ed359c264ae4d",
        )

    def test_provider_calls_receive_server_snapshot_and_catalog_digest(self):
        captured = {}
        provider_call = SimpleNamespace(
            index=0,
            id="native-call",
            function=SimpleNamespace(name="state_add_item", arguments='{"item":"钥匙"}'),
        )

        def create(**kwargs):
            captured.update(kwargs)
            return [
                stream_chunk(tool_calls=[provider_call]),
                stream_chunk(finish_reason="tool_calls"),
            ]

        engine = GameEngine.__new__(GameEngine)
        engine.client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        engine.messages = []
        engine.cb = SimpleNamespace(on_narrative=lambda *_args: None, on_error=lambda _msg: None)

        with patch("src.engine.log_model_call"):
            _text, calls = engine._stream_llm("test-model", buffer_if_tools=True)

        self.assertEqual(len(calls), 1)
        snapshot = calls[0][REQUEST_METADATA_KEY]
        self.assertEqual(snapshot["caller"], MODEL_CALLER)
        self.assertEqual(snapshot["allowed_tool_names"], [
            tool["function"]["name"] for tool in captured["tools"]
        ])
        self.assertEqual(
            snapshot["tool_catalog_digest"],
            engine._turn_diagnostics[-1]["request_snapshot"]["tool_catalog_digest"],
        )
        self.assertNotIn("state_get", snapshot["allowed_tool_names"])
        self.assertNotIn("state_set", snapshot["allowed_tool_names"])
        self.assertNotIn("read_file", snapshot["allowed_tool_names"])
        self.assertNotIn("get_npc_secret", snapshot["allowed_tool_names"])

    def test_model_catalog_omits_generic_state_and_secret_capabilities(self):
        names = {tool["function"]["name"] for tool in MODEL_TOOLS}
        self.assertTrue({"state_get", "state_set", "read_file", "get_npc_secret"}.isdisjoint(names))

    def test_dsml_read_file_is_quarantined_then_rejected_by_same_snapshot_policy(self):
        protocol = (
            '<|DSML|tool_calls><|DSML|invoke name="read_file">'
            '<|DSML|parameter name="path" string="true">skills/core/trpg_master.skill'
            '</|DSML|parameter></|DSML|invoke></|DSML|tool_calls>'
        )
        engine = GameEngine.__new__(GameEngine)
        engine.client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **_kwargs: [
                        stream_chunk(content=protocol),
                        stream_chunk(finish_reason="stop"),
                    ]
                )
            )
        )
        visible = []
        engine.messages = []
        engine.cb = SimpleNamespace(
            on_narrative=visible.append,
            on_error=lambda _msg: None,
        )

        with patch("src.engine.log_model_call"), patch("src.engine.log_error"):
            text, calls = engine._stream_llm("test-model", buffer_if_tools=True)

        self.assertEqual(text, "")
        self.assertEqual(visible, [])
        self.assertEqual(calls[0]["function"]["name"], "read_file")
        self.assertEqual(calls[0][REQUEST_METADATA_KEY]["caller"], MODEL_CALLER)

        fake, executions = ToolPolicyExecutionTests()._engine()
        result = _execute_tools({"engine": fake, "tool_round": 0, "tool_calls": calls})
        self.assertEqual(executions, [])
        self.assertEqual(
            json.loads(result["executed_tools"][0]["output"])["reason"],
            "model_tool_forbidden",
        )


if __name__ == "__main__":
    unittest.main()
