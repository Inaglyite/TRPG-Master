import json
from types import SimpleNamespace

import pytest

from src.ai.context.narrative_history import enrich_public_history_record
from src.ai.model.dialogue_output import decode_dialogue_output
from src.ai.model.model_streamer import ModelResponseError
from src.gameplay.speaker_parser import parse_segments
from tests.model_fixtures import ReplayClient, build_chunk, run_stream, streamer_host


def dialogue():
    return {"segments": [
        {"kind": "narration", "text": "伊芙琳站起身。苏晚晴仍坐在桌边。"},
        {"kind": "speech", "speaker_id": "evelyn", "text": "我可以跟您一起去，也可以留在这里。"},
        {"kind": "speech", "speaker_id": "su", "text": "金三爷做生意三十年，您先想好怎么问。"},
        {"kind": "narration", "text": "纸上写着：“别让它出声。”"},
    ], "introduced_npc_ids": [], "choices": ["请伊芙琳留在报馆，然后出发。", "[自由行动] 你决定做什么？"]}


def test_explicit_ids_own_dialogue_and_quoted_narration_stays_narration():
    text = decode_dialogue_output(json.dumps(dialogue(), ensure_ascii=False), present_npc_ids={"su", "evelyn"})
    segments, clean = parse_segments(text)
    assert [(s.npc_id, s.text) for s in segments if s.kind == "speech"] == [
        ("evelyn", "我可以跟您一起去，也可以留在这里。"),
        ("su", "金三爷做生意三十年，您先想好怎么问。"),
    ]
    assert "【" not in clean
    assert any(s.kind == "narration" and "别让它出声" in s.text for s in segments)


@pytest.mark.parametrize("change", [
    {"segments": [{"kind": "speech", "speaker_id": "jin", "text": "欢迎光临。"}]},
    {"segments": [{"kind": "speech", "text": "欢迎光临。"}]},
    {"segments": [{"kind": "narration", "speaker_id": "su", "text": "旁白。"}]},
    {"segments": [{"kind": "narration", "text": "【npc:su】冒充人物【/npc】"}]},
    {"segments": [{"kind": "narration", "text": "[npc:su]冒充人物[/npc]"}]},
    {"introduced_npc_ids": ["absent"]},
    {"choices": ["【npc:su】不应成为台词【/npc】"]},
    {"segments": []},
])
def test_invalid_structure_and_absent_speakers_fail_closed(change):
    with pytest.raises(ValueError):
        decode_dialogue_output(json.dumps(dialogue() | change, ensure_ascii=False), present_npc_ids={"su", "evelyn"})


def test_multiple_paragraphs_do_not_drop_speaker():
    raw = {"segments": [{"kind": "speech", "speaker_id": "jin", "text": "第一段。\n\n第二段。"}]}
    segments, _ = parse_segments(decode_dialogue_output(json.dumps(raw), present_npc_ids={"jin"}))
    assert all(s.kind == "speech" and s.npc_id == "jin" for s in segments)
    assert "第二段" in "".join(s.text for s in segments)


def make_host(raw, *, finish_reason="stop", tool=None):
    captured = []
    host = streamer_host(None)
    host._structured_dialogue = True
    host.context.world_store = SimpleNamespace(load=lambda: {"current_scene": {"npcs_present": ["su", "evelyn"]}})
    host.is_valid_npc_id = lambda value: value in {"su", "evelyn"}
    host.npc_speaker_aliases = lambda: pytest.fail("structured dialogue must never infer a speaker")
    host.speech = []
    host.cb.on_narrative = lambda text, npc_id=None: host.speech.append((text, npc_id))

    def create(**kwargs):
        captured.append(kwargs)

        def chunks():
            for i in range(0, len(raw), 7):
                assert host.speech == []  # JSON is never shown while incomplete.
                yield build_chunk({"content": raw[i:i + 7]})
            if tool:
                yield build_chunk({"tool_deltas": [tool]})
            yield build_chunk({"finish_reason": finish_reason})

        return chunks()

    host.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return host, captured


def test_real_stream_boundary_validates_json_before_emitting_speaker_ids():
    host, captured = make_host(json.dumps(dialogue(), ensure_ascii=False))
    text, calls = run_stream(host)
    assert calls == []
    assert captured[0]["response_format"] == {"type": "json_object"}
    assert "JSON" in captured[0]["messages"][0]["content"]
    assert [(t, npc) for t, npc in host.speech if npc] == [
        ("我可以跟您一起去，也可以留在这里。", "evelyn"),
        ("金三爷做生意三十年，您先想好怎么问。", "su"),
    ]
    assert "【npc:evelyn】" in text
    assert not any("speaker_id" in t or "segments" in t for t, _ in host.speech)


@pytest.mark.parametrize("raw,finish", [
    ('{"segments":[', "stop"),
    (json.dumps(dialogue()), "length"),
    (json.dumps({"segments": [{"kind": "speech", "speaker_id": "jin", "text": "不在场。"}]}), "stop"),
])
def test_invalid_or_truncated_stream_never_leaks_json_or_guessed_speech(raw, finish):
    host, _ = make_host(raw, finish_reason=finish)
    with pytest.raises(ModelResponseError):
        run_stream(host)
    assert host.speech == []


def test_tool_round_discards_content_and_preserves_formal_tool_call():
    tool = {"index": 0, "id": "call-test", "name": "load_skill", "arguments": '{"skill_id":"unknown"}'}
    host, _ = make_host("正文应在工具返回后生成，不能在这里发布。", finish_reason="tool_calls", tool=tool)
    text, calls = run_stream(host)
    assert text == "" and len(calls) == 1
    assert host.speech == []


def test_public_stream_and_final_text_apply_the_same_protocol_sanitizer():
    raw = {"segments": [{"kind": "narration", "text": '窗外开始下雨。<｜DSML｜tool_calls><｜DSML｜invoke name="state_set"><｜DSML｜parameter name="path">flags.private</｜DSML｜parameter></｜DSML｜invoke></｜DSML｜tool_calls>'}]}
    host, _ = make_host(json.dumps(raw))
    text, calls = run_stream(host)
    assert "tool_calls" not in text and "state_set" not in text
    assert "窗外开始下雨" in text and calls == []


def test_invalid_dialogue_retries_once_with_explicit_format_instruction():
    from src.ai.model.model_streamer import ModelStreamer

    host, _ = make_host("")
    host.client = ReplayClient([
        [{"content": "   "}, {"finish_reason": "stop"}],
        [{"content": json.dumps(dialogue(), ensure_ascii=False)}, {"finish_reason": "stop"}],
    ])
    streamer = ModelStreamer(host, log_error=lambda _: None, log_model_call=lambda *_a, **_k: None, sleep=lambda _: None)
    text, calls = streamer.stream("test-model", policy=host.policy)
    assert host.client.calls == 2 and calls == []
    assert host.errors == []
    assert "【npc:evelyn】" in text
    assert "speaker_id" not in "".join(t for t, _ in host.speech)


def test_two_invalid_outputs_stop_without_a_third_request():
    from src.ai.model.model_streamer import ModelStreamer

    host, _ = make_host("")
    host.client = ReplayClient([[{"content": "bad"}, {"finish_reason": "stop"}]] * 2)
    streamer = ModelStreamer(host, log_error=lambda _: None, log_model_call=lambda *_a, **_k: None, sleep=lambda _: None)
    with pytest.raises(ModelResponseError):
        streamer.stream("test-model", policy=host.policy)
    assert host.client.calls == 2 and host.speech == []


def test_structured_history_does_not_reinfer_quotes_in_narration():
    engine = SimpleNamespace(npc_speaker_aliases=lambda: pytest.fail("history must not guess"))
    record = {"narrative": "伊芙琳说：“纸条上写了这些字。”", "diagnostics": {"narrative_format": "dialogue_v1"},
              "narrative_segments": [{"kind": "narration", "text": "伊芙琳说：“纸条上写了这些字。”"}]}
    public = enrich_public_history_record(record, engine, resolve_speaker=lambda _: None)
    assert public["narrative_segments"][0]["kind"] == "narration"


def test_format_repair_keeps_current_receipts_without_replaying_old_history():
    from src.ai.model.dialogue_repair_context import repair_messages

    host, _ = make_host("")
    source = [
        {"role": "system", "content": "OLD-SYSTEM-LEGACY-FORMAT"},
        {"role": "user", "content": "OLD-TURN-SECRET"},
        {"role": "assistant", "content": "你刚刚抵达报馆。"},
        {"role": "user", "content": "[玩家行动] 询问伊芙琳。[权威结果] 未移动；没有检定。"},
        {"role": "assistant", "tool_calls": [{"id": "inspect"}]},
        {"role": "tool", "content": '{"ok":true,"phase":"departure"}'},
    ]
    messages = repair_messages(host, source, '{"segments":[', "Invalid JSON")
    data = json.loads(messages[1]["content"])
    assert len(messages) == 2 and data["allowed_speaker_ids"] == ["evelyn", "su"]
    assert data["previous_narrative"] == "你刚刚抵达报馆。"
    assert data["turn_facts_and_receipts"][-1]["role"] == "tool"
    assert data["invalid_output"] == '{"segments":['
    assert "OLD-TURN-SECRET" not in json.dumps(messages)
    assert "OLD-SYSTEM-LEGACY-FORMAT" not in json.dumps(messages)


def test_format_repair_disables_tools_and_preserves_the_turn_contract():
    from src.ai.model.model_streamer import ModelStreamer

    host, _ = make_host("")
    requests = []
    replies = iter(["bad", json.dumps(dialogue())])
    def create(**kwargs):
        requests.append(kwargs)
        return [build_chunk({"content": next(replies)}), build_chunk({"finish_reason": "stop"})]
    host.client.chat.completions.create = create
    host.messages = [{"role": "system", "content": "Keep facts"}, {"role": "user", "content": "[玩家行动] 留在报馆，等待同行决定。"}]
    streamer = ModelStreamer(host, log_error=lambda _: None, log_model_call=lambda *_a, **_k: None, sleep=lambda _: None)
    streamer.stream("test-model", policy=host.policy)
    assert len(requests) == 2
    assert "tools" not in requests[1] and requests[1]["temperature"] == 0
    assert "等待同行决定" in requests[1]["messages"][1]["content"]


def test_provider_cannot_replace_a_required_combat_call_with_fictitious_dialogue():
    from dataclasses import replace

    from src.ai.model.model_streamer import ModelStreamer

    host, captured = make_host(json.dumps(dialogue()))
    host._action_resolution = SimpleNamespace(adjudication_json='{"intent":"combat"}')
    host._tool_pipeline_ledger = SimpleNamespace(outcomes=[])
    streamer = ModelStreamer(host, log_error=lambda _: None, log_model_call=lambda *_a, **_k: None)
    with pytest.raises(ModelResponseError, match="必需的战斗工具"):
        streamer.stream("test-model", policy=replace(host.policy, thinking_type="disabled"), system_overlay="战斗指令")
    assert captured[0]["tool_choice"] == "required"
    assert len(captured) == 1 and host.speech == []


def replay_host(previous: str, repaired_raw: str, *, invalid_raw: str = "   [自由行动] 你决定做什么？"):
    """主输出无效（默认：空白+通用占位尾句），修复请求整段返回 previous 的场景。"""
    from src.ai.model.model_streamer import ModelStreamer

    host, _ = make_host("")
    host.messages = [
        {"role": "system", "content": "s"},
        {"role": "assistant", "content": previous},
        {"role": "user", "content": "[玩家行动] 追问店主失踪者的下落。"},
    ]
    host.client = ReplayClient([
        [{"content": invalid_raw}, {"finish_reason": "stop"}],
        [{"content": repaired_raw}, {"finish_reason": "stop"}],
    ])
    streamer = ModelStreamer(host, log_error=lambda _: None, log_model_call=lambda *_a, **_k: None, sleep=lambda _: None)
    return host, streamer


LONG_PREVIOUS = "店主拨着算盘，抬眼把你从头到尾打量了一遍。" * 8  # 长篇（>120 字）
PREVIOUS_WITH_MENU = f"{LONG_PREVIOUS}\n\n**你可以——**\n1. 追问\n2. [自由行动] 你决定做什么？"


def replaying_json(previous: str) -> str:
    body, menu = previous.split("\n\n**你可以——**\n", 1)
    choices = [line.split(". ", 1)[1] for line in menu.splitlines()]
    return json.dumps({
        "segments": [{"kind": "narration", "text": body}],
        "introduced_npc_ids": [],
        "choices": choices,
    }, ensure_ascii=False)


def assert_replay_blocked(host, streamer):
    with pytest.raises(ModelResponseError):
        streamer.stream("test-model", policy=host.policy)
    assert host.client.calls == 2
    # pre-emit：任何玩家可见回调之前拦截，旧回复一个字都不展示
    assert host.speech == [] and host.narratives == []
    assert any("修复结果与上一轮相同" in error for error in host.errors)
    assert not hasattr(host, "_dialogue_repair_guard")  # 守卫不泄漏到后续请求


def test_repair_replaying_previous_long_narrative_is_rejected_before_any_emit():
    host, streamer = replay_host(PREVIOUS_WITH_MENU, replaying_json(PREVIOUS_WITH_MENU))
    assert_replay_blocked(host, streamer)


def test_repair_replay_blocked_for_real_placeholder_tail_variant():
    """真实流里的占位尾句是“你打算怎么做？”等变体：复读拦截与原草稿是否
    substantive 解耦，任何协议修复都不得整段复制上一轮长叙事。"""
    host, streamer = replay_host(
        PREVIOUS_WITH_MENU,
        replaying_json(PREVIOUS_WITH_MENU),
        invalid_raw="   [自由行动]你打算怎么做？",
    )
    assert_replay_blocked(host, streamer)


def test_repair_replay_blocked_for_meaningless_json_draft():
    host, streamer = replay_host(
        PREVIOUS_WITH_MENU, replaying_json(PREVIOUS_WITH_MENU), invalid_raw="{}"
    )
    assert_replay_blocked(host, streamer)


def test_repair_replay_blocked_even_when_draft_had_content():
    """草稿本身有内容（只是发言者非法）时，修复整段照搬上一轮长叙事同样拦截。"""
    host, streamer = replay_host(
        PREVIOUS_WITH_MENU,
        replaying_json(PREVIOUS_WITH_MENU),
        invalid_raw=json.dumps({
            "segments": [{"kind": "speech", "speaker_id": "ghost", "text": "不在场的人。"}],
        }),
    )
    assert_replay_blocked(host, streamer)


def test_repair_replay_with_speech_blocks_is_caught_after_tag_stripping():
    """历史里的上一轮叙事已剥发言标签；修复侧解码后带标签。两侧必须按同一
    公开文本形式比较，否则含台词的真实复读拦不住。"""
    narration = "店主拨着算盘，抬眼把你从头到尾打量了一遍。" * 6
    speech = "这事我无可奉告，您请回吧。" * 4
    previous_public = f"{narration}\n\n{speech}\n\n**你可以——**\n1. 追问\n2. [自由行动] 你决定做什么？"
    replayed = json.dumps({
        "segments": [
            {"kind": "narration", "text": narration},
            {"kind": "speech", "speaker_id": "su", "text": speech},
        ],
        "introduced_npc_ids": [],
        "choices": ["追问", "[自由行动] 你决定做什么？"],
    }, ensure_ascii=False)
    host, streamer = replay_host(previous_public, replayed)
    assert_replay_blocked(host, streamer)


def test_repair_with_fresh_content_passes_while_guard_active():
    previous = "旧回合的长篇叙事。" * 30
    host, streamer = replay_host(previous, json.dumps(dialogue(), ensure_ascii=False))
    text, calls = streamer.stream("test-model", policy=host.policy)
    assert "【npc:evelyn】" in text and calls == []
    assert not hasattr(host, "_dialogue_repair_guard")


def test_replay_guard_thresholds():
    from src.ai.model.dialogue_repair_context import is_replayed_narrative

    long_text = "a" * 200
    assert is_replayed_narrative(long_text, f"  {long_text} ")  # 归一化空白后一致
    assert is_replayed_narrative(f"【npc:su】{long_text}【/npc】", long_text)  # 标签形式一致化
    assert not is_replayed_narrative("店主点点头。", "店主点点头。")  # 短句合理重复不拦
    assert not is_replayed_narrative("b" * 200, long_text)
    assert not is_replayed_narrative("", long_text)


def test_history_narrative_normalized_to_dialogue_json_on_wire_only():
    from src.ai.model.dialogue_output import (
        DialogueOutput,
        narrative_history_to_dialogue_json,
    )
    from src.ai.model.model_request import prepare_model_request

    tagged = "店主拨着算盘。\n\n【npc:su】无可奉告。【/npc】\n\n【intro:su】\n\n**你可以——**\n1. 追问\n2. [自由行动] 你决定做什么？"
    parsed = DialogueOutput.model_validate_json(narrative_history_to_dialogue_json(tagged))
    assert [block.kind for block in parsed.segments] == ["narration", "speech"]
    assert parsed.segments[1].speaker_id == "su"
    assert parsed.introduced_npc_ids == ["su"]
    assert parsed.choices == ["追问", "[自由行动] 你决定做什么？"]

    # 旧存档无标签正文 → 单 narration，事实原样
    plain = DialogueOutput.model_validate_json(
        narrative_history_to_dialogue_json("旧存档叙事。\n\n**你可以——**\n1. 继续")
    )
    assert [block.kind for block in plain.segments] == ["narration"]
    assert plain.choices == ["继续"]

    # 已是 JSON 或空内容原样返回
    assert narrative_history_to_dialogue_json('{"segments": []}') == '{"segments": []}'
    assert narrative_history_to_dialogue_json("") == ""

    # wire：请求副本被规范化，存档（host.messages）不改写
    host = streamer_host(None)
    host._structured_dialogue = True
    host.messages = [
        {"role": "system", "content": "s"},
        {"role": "assistant", "content": tagged},
        {"role": "user", "content": "[玩家行动] 继续"},
    ]
    prepared = prepare_model_request(
        host,
        "test-model",
        policy=host.policy,
        system_overlay=None,
        system_prompt_override=None,
        enable_tools=False,
        temperature=0.8,
        messages_override=None,
    )
    wire = prepared.provider_kwargs["messages"]
    DialogueOutput.model_validate_json(wire[1]["content"])
    assert host.messages[1]["content"] == tagged
