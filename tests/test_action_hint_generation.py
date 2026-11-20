"""Generation preferences reach provider requests without changing game authority."""
import asyncio
import copy
import json
import threading
from types import SimpleNamespace

import pytest

from src.ai.model.action_hint_policy import DISABLED_INSTRUCTION
from src.multiplayer.ws_session import SessionTurnGate, WsSessionContext
from tests.model_fixtures import build_chunk, run_stream
from tests.test_dialogue_attribution import _structured_consistency_engine
from tests.test_dialogue_output import make_host
from tests.test_ws_session import _Outbound


def reply():
    return json.dumps({"segments": [{"kind": "narration", "text": "窗外雨声渐密。"}],
                       "choices": ["翻看剪报", "询问细节"],
                       "choice_skill_hints": [{"choice_index": 1, "skill_id": "library_use"}]}, ensure_ascii=False)


@pytest.mark.parametrize("enabled", [True, False])
def test_request_and_accepted_output_follow_the_same_preference(enabled):
    host, requests = make_host(reply())
    host._turn_generate_action_hints = enabled
    host.messages.insert(1, {"role": "assistant", "content": "旧正文。\n\n**你可以——**\n1. 翻看剪报"})
    before = copy.deepcopy(host.messages)
    text, tools = run_stream(host)
    assert tools == []
    assert ("你可以——" in text) is enabled
    assert (DISABLED_INSTRUCTION in requests[0]["messages"][0]["content"]) is not enabled
    history = json.loads(requests[0]["messages"][1]["content"])
    assert bool(history["choices"]) is enabled
    assert host.messages == before  # Provider projection never rewrites a save.
    if not enabled:
        assert host._turn_choice_skill_hints == {}
        assert host._turn_choice_targets == {}
        assert not any("你可以" in text for text, _ in host.speech)
    assert len(requests) == 1  # A returned menu does not trigger extra repair calls.


def test_consistency_rewrite_does_not_restore_disabled_choices():
    from src.gameplay.narrative_consistency import apply_narrative_consistency

    engine = _structured_consistency_engine(reply())
    engine._turn_generate_action_hints = False
    rewritten = apply_narrative_consistency(engine, "你在门前等了三天。")
    assert "窗外雨声渐密。" in rewritten
    assert "你可以" not in rewritten


def test_format_repair_keeps_generation_disabled_and_never_reexecutes_tools():
    bad = json.loads(reply())
    bad["segments"][0]["text"] += "\n你可以——\n1. 翻看剪报\n2. 询问细节"
    host, _ = make_host("")
    host._turn_generate_action_hints = False
    replies = iter([json.dumps(bad, ensure_ascii=False), reply()])
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        return iter([build_chunk({"content": next(replies)}),
                     build_chunk({"finish_reason": "stop"})])

    host.client.chat.completions.create = create
    text, calls = run_stream(host, retry_on_empty=True)
    assert not calls and "你可以" not in text
    assert len(requests) == 2
    assert all(DISABLED_INSTRUCTION in r["messages"][0]["content"] for r in requests)
    assert not requests[1].get("tools")


def test_busy_or_other_session_cannot_change_running_turn_preference():
    asyncio.run(_check_session_freezing())


async def _check_session_freezing():
    engine = SimpleNamespace()
    lock = threading.Lock()
    first = WsSessionContext(_Outbound(), SessionTurnGate(lock), generation_target=engine)
    second = WsSessionContext(_Outbound(), SessionTurnGate(lock), generation_target=engine)

    async def reserve(_router, _payload):
        return await first.reserve_turn()

    wire = SimpleNamespace(dispatch=reserve)
    assert await first.dispatch(wire, None, {"generate_action_hints": False})
    assert engine._turn_generate_action_hints is False
    assert not await first.dispatch(wire, None, {})
    assert engine._turn_generate_action_hints is False
    assert not await second.reserve_turn()
    assert engine._turn_generate_action_hints is False
    first.release_turn()
    assert await first.dispatch(wire, None, {})
    assert engine._turn_generate_action_hints is True
    first.release_turn()
