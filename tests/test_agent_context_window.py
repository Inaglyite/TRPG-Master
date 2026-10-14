"""Per-request capacity, rather than repeated-context accumulation across a run."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_structured_commands import make_structured_world
from test_structured_trigger import _RecordingCompletions

from src.structured.agent import KeeperAgentRunner
from src.structured.agent_runtime import build_byok_caller
from src.structured.errors import StructuredError


class AgentContextWindowTests(unittest.IsolatedAsyncioTestCase):
    async def _long_query_run(self, assisted):
        with tempfile.TemporaryDirectory() as directory:
            context = make_structured_world(Path(directory))
            calls = []

            async def caller(system, user):
                calls.append((system, user))
                if len(calls) < 4:
                    return json.dumps({"queries": [{"kind": "notes"}], "commands": []})
                return json.dumps({"commands": [], "assessment": "核对完成", "stop_reason": "done"})

            runner = KeeperAgentRunner(context.database_url, caller=caller)
            original = runner._build_context

            def context_with_rules(*args, **kwargs):
                value = json.loads(original(*args, **kwargs))
                value["long_rules_fixture"] = "规则" * 30000
                return json.dumps(value, ensure_ascii=False)

            runner._build_context = context_with_rules
            method = runner.run_assisted if assisted else runner.run
            result = await method(world_id=context.world_id)
            self.assertEqual("done", result.status)
            self.assertEqual(4, result.model_calls)
            self.assertEqual(0, result.commands_committed)
            self.assertGreater(sum(len(s) + len(u) for s, u in calls) // 3, 60000)
            self.assertTrue(all("notes 查询结果" in u for _, u in calls[1:]))

    async def test_agent_can_finish_multiple_large_queries(self):
        await self._long_query_run(False)

    async def test_assisted_can_finish_multiple_large_queries(self):
        await self._long_query_run(True)

    async def test_capacity_rejection_pauses_without_gameplay_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            context = make_structured_world(Path(directory))

            async def caller(system, user):
                raise StructuredError("context_window_exceeded", "模型窗口不足，未调用。")

            result = await KeeperAgentRunner(context.database_url, caller=caller).run(
                world_id=context.world_id
            )
            self.assertEqual("paused", result.status)
            self.assertEqual("context_window_exceeded", result.stop_reason)
            self.assertEqual(0, result.commands_committed)

    async def _byok_calls(self, *, window, text, output=4096, count=1):
        completions = _RecordingCompletions()
        role = SimpleNamespace(
            client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
            model_id="test-model",
            window_tokens=window,
            max_output_tokens=output,
            provider_kind="openai_compatible",
            base_url_host="example.com",
        )
        with (
            patch("src.storage.model_config_store.room_route_resolver", lambda *args: lambda: None),
            patch(
                "src.ai.model.route_service.resolve_routes",
                lambda *args: SimpleNamespace(narrative=role),
            ),
        ):
            caller = build_byok_caller("sqlite:///:memory:", "test-world")
            try:
                for _ in range(count):
                    await caller("system", text)
            except StructuredError:
                self.assertIsNone(completions.kwargs)  # Rejected before provider call.
                raise
        return completions.kwargs

    async def test_unset_window_uses_256k_default(self):
        with patch.dict("os.environ", {"TRPG_CONTEXT_WINDOW_TOKENS": "262144"}):
            result = await self._byok_calls(window=None, text="界" * 100000)
        self.assertEqual(4096, result["max_tokens"])

    async def test_explicit_smaller_window_is_honored(self):
        with self.assertRaises(StructuredError) as error:
            await self._byok_calls(window=65536, text="界" * 80000)
        self.assertEqual("context_window_exceeded", error.exception.code)

    async def test_output_reservation_counts_toward_window(self):
        with self.assertRaises(StructuredError):
            await self._byok_calls(window=8192, text="界" * 5000, output=4096)

    async def test_repeated_requests_do_not_consume_shared_token_budget(self):
        result = await self._byok_calls(window=65536, text="界" * 60000, count=4)
        self.assertEqual(2, len(result["messages"]))


def test_legacy_preflight_honors_model_window_and_output_reservation():
    from src.ai.context.context_capacity import STATUS_IRREDUCIBLE
    from src.ai.model.model_request import StreamPolicy, prepare_model_request

    role = SimpleNamespace(
        config_revision=1,
        binding_id="test",
        provider_kind="openai_compatible",
        window_tokens=8192,
        window_source="manual",
        max_output_tokens=2048,
    )
    host = SimpleNamespace(
        messages=[{"role": "user", "content": "界" * 7000}],
        _model_routes=SimpleNamespace(role=lambda slot: role),
    )
    prepared = prepare_model_request(
        host,
        "test-model",
        policy=StreamPolicy(False, False, "test", None),
        system_overlay=None,
        system_prompt_override=None,
        enable_tools=False,
        temperature=0,
        messages_override=None,
    )
    assert prepared.capacity.plan.window_tokens == 8192
    assert prepared.capacity.plan.hard_tokens == 6144
    assert prepared.capacity.status == STATUS_IRREDUCIBLE
    assert prepared.provider_kwargs["max_tokens"] == 2048
