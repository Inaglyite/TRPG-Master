"""T01 事故回归：已离场人物台词的类型化校验、修复上下文与绕过拦截。

事故原型：guest_concierge（王妈）上一回合经 speaker_departure 离场，下一回合
主叙述与协议修复都惯性复述她的台词，整轮失败且玩家不知道结果未保存。本文件
固定：decode 错误类型化、修复请求显式携带离场事实、farewell 两路径对称、
改名/补在场表绕过仍被拒。
"""

import json
from types import SimpleNamespace as NS

import pytest

from src.ai.model.dialogue_output import (
    DialogueFormatError,
    DialogueValidationError,
    SpeakerAbsentError,
    SpeakerDepartedError,
    decode_dialogue_output,
    parse_dialogue_output,
)
from src.ai.model.dialogue_repair_context import repair_messages
from src.ai.model.model_stream_helpers import _guest_context
from src.ai.model.model_streamer import ModelResponseError, ModelStreamer
from src.gameplay.narrative_consistency import _rewrite_allowed_speakers
from tests.model_fixtures import build_chunk, streamer_host

DEPART_REASON = "主人辞退了她，当即收拾东西离开"


def departed_world():
    """guest_concierge（王妈）上一回合已离场；guard 仍在场。"""
    return {
        "current_scene": {"id": "hall", "name": "门厅", "npcs_present": ["guard"]},
        "scene_catalog": {"hall": {"id": "hall", "npcs_present": ["guard"]}},
        "npcs": [{"id": "guard", "name": "护卫"}],
        "guest_speakers": {"guest_concierge": {"display_name": "王妈", "scene_id": "hall"}},
        "scene_speaker_presence": {"hall": {"guest_concierge": {
            "present": False, "reason": DEPART_REASON, "turn_id": "turn-9"}}},
    }


def speech_json(speaker_id, text):
    return json.dumps(
        {"segments": [{"kind": "speech", "speaker_id": speaker_id, "text": text}]},
        ensure_ascii=False,
    )


def make_host(world):
    host = streamer_host(None)
    host._structured_dialogue = True
    host.context.world_store = NS(load=lambda: world)
    host.is_valid_npc_id = lambda value: value in {"guard"}
    host.npc_speaker_aliases = lambda: {}
    host.speech = []
    host.cb.on_narrative = lambda text, npc_id=None: host.speech.append((text, npc_id))
    host.messages = [
        {"role": "system", "content": "s"},
        {"role": "assistant", "content": "王妈临走前欲言又止。"},
        {"role": "user", "content": "[玩家行动] 研究古楚文字摹本。[权威结果] 未移动；没有检定。"},
    ]
    return host


class QueueClient:
    """按序产出录制流并捕获每次请求参数的最小 client。"""

    def __init__(self, streams):
        self._streams = [list(stream) for stream in streams]
        self.requests = []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        if not self._streams:
            raise AssertionError("录制流已耗尽")
        return iter(build_chunk(record) for record in self._streams.pop(0))


def run_streamer(host):
    streamer = ModelStreamer(
        host, log_error=lambda _m: None, log_model_call=lambda *_a, **_k: None, sleep=lambda _s: None
    )
    return streamer.stream("test-model", policy=host.policy)


def test_departed_speaker_error_is_typed_and_distinct():
    raw = speech_json("guest_concierge", "“老爷，汤要凉了。”")
    departed = {"guest_concierge": {"present": False, "reason": DEPART_REASON, "turn_id": "turn-9"}}
    with pytest.raises(SpeakerDepartedError) as err:
        decode_dialogue_output(raw, present_npc_ids={"guard"}, departed=departed)
    exc = err.value
    assert isinstance(exc, ValueError) and exc.category == "departed_speaker"
    assert exc.speaker_id == "guest_concierge"
    assert exc.departure["reason"] == DEPART_REASON and exc.departure["turn_id"] == "turn-9"
    assert "guest_concierge" in str(exc) and "已离场" in str(exc)
    # 未传 departed 时退化为现有“不在场”错误（向后兼容）
    with pytest.raises(SpeakerAbsentError) as absent:
        decode_dialogue_output(raw, present_npc_ids={"guard"})
    assert absent.value.category == "absent_speaker"
    assert "发言者不在当前场景：guest_concierge" in str(absent.value)
    # 不在离场记录里的陌生人仍是“不在场”，两类错误可区分
    with pytest.raises(SpeakerAbsentError):
        decode_dialogue_output(speech_json("stranger", "“谁？”"),
                               present_npc_ids={"guard"}, departed=departed)


def test_broken_json_error_is_distinct_from_speaker_errors():
    with pytest.raises(DialogueFormatError) as broken:
        decode_dialogue_output('{"segments":[', present_npc_ids=set())
    assert broken.value.category == "invalid_json"
    with pytest.raises(DialogueFormatError):
        decode_dialogue_output('{"segments": []}', present_npc_ids=set())
    categories = {
        SpeakerDepartedError("x").category,
        SpeakerAbsentError("x").category,
        DialogueFormatError("x").category,
    }
    assert categories == {"departed_speaker", "absent_speaker", "invalid_json"}


def test_repair_context_lists_departed_speakers_with_reason_and_turn():
    world = departed_world()
    host = make_host(world)
    error = SpeakerDepartedError(
        "guest_concierge", world["scene_speaker_presence"]["hall"]["guest_concierge"]
    )
    messages = repair_messages(host, host.messages, speech_json("guest_concierge", "“汤。”"), error)
    data = json.loads(messages[1]["content"])
    assert data["departed_speakers"] == [
        {"id": "guest_concierge", "name": "王妈", "reason": DEPART_REASON, "turn_id": "turn-9"}
    ]
    assert data["validation_error"] == str(error)
    assert "guest_concierge" not in data["allowed_speaker_ids"]
    system = messages[0]["content"]
    assert "已离场" in system and "告别" in system
    # 发言者类错误的修复指令：服从权威事实，禁止换 id / 补在场表 / 无引号旁白绕过
    assert "换个 guest id" in system and "无引号旁白" in system and "只修复格式" not in system


def test_repair_instruction_for_broken_json_only_fixes_format():
    world = departed_world()
    host = make_host(world)
    messages = repair_messages(
        host, host.messages, '{"segments":[', DialogueFormatError("对话 JSON 损坏或结构缺失：x")
    )
    system = messages[0]["content"]
    assert "只修复格式" in system
    assert "换个 guest id" not in system
    # 离场事实仍然随请求提供（与错误类型无关）
    assert json.loads(messages[1]["content"])["departed_speakers"]


def test_this_turn_departed_guest_is_farewell_only_in_repair_context():
    world = departed_world()
    host = make_host(world)
    host._adjudicated_outcome = {"events": [{"type": "speaker_departure", "target": "guest_concierge"}]}
    data = json.loads(repair_messages(host, host.messages, "bad", ValueError("x"))[1]["content"])
    assert "guest_concierge" in data["allowed_speaker_ids"]
    assert data["farewell_only_speaker_ids"] == ["guest_concierge"]
    # 下一回合（没有本回合离场事件）即禁止
    host._adjudicated_outcome = {"events": []}
    data = json.loads(repair_messages(host, host.messages, "bad", ValueError("x"))[1]["content"])
    assert "guest_concierge" not in data["allowed_speaker_ids"]
    assert data["farewell_only_speaker_ids"] == []


def test_incident_repair_drops_departed_speaker_and_turn_commits():
    """事故回归：主输出复述已离场王妈的台词→类型化拒绝；修复请求携带离场事实，
    修复输出不再让她开口→整轮正常完成。"""
    world = departed_world()
    host = make_host(world)
    valid = json.dumps({"segments": [
        {"kind": "narration", "text": "你埋头比对摹本上的古楚文字。"},
        {"kind": "speech", "speaker_id": "guard", "text": "“有需要就叫我。”"},
    ]}, ensure_ascii=False)
    host.client = QueueClient([
        [{"content": speech_json("guest_concierge", "“老爷，汤要凉了。”")}, {"finish_reason": "stop"}],
        [{"content": valid}, {"finish_reason": "stop"}],
    ])
    text, _ = run_streamer(host)
    assert "【npc:guard】" in text and "guest_concierge" not in text
    assert len(host.client.requests) == 2 and host.errors == []
    repair_user = json.loads(host.client.requests[1]["messages"][1]["content"])
    assert [d["id"] for d in repair_user["departed_speakers"]] == ["guest_concierge"]
    assert repair_user["departed_speakers"][0]["reason"] == DEPART_REASON
    assert repair_user["departed_speakers"][0]["turn_id"] == "turn-9"
    repair_system = host.client.requests[1]["messages"][0]["content"]
    assert "已离场" in repair_system


def test_repair_repeating_departed_speaker_fails_and_reports_unsaved():
    """修复输出仍含已离场人物台词：整轮以终态错误结束，玩家文案声明结果未保存。"""
    world = departed_world()
    host = make_host(world)
    bad = speech_json("guest_concierge", "“老爷，汤要凉了。”")
    host.client = QueueClient([
        [{"content": bad}, {"finish_reason": "stop"}],
        [{"content": bad}, {"finish_reason": "stop"}],
    ])
    with pytest.raises(ModelResponseError):
        run_streamer(host)
    assert host.speech == []
    assert any("未保存" in error and "王妈" in error and "离场" in error for error in host.errors)


def test_broken_json_failure_reports_unsaved_without_speaker_details():
    world = departed_world()
    host = make_host(world)
    host.client = QueueClient([
        [{"content": '{"segments":['}, {"finish_reason": "stop"}],
        [{"content": '{"segments":['}, {"finish_reason": "stop"}],
    ])
    with pytest.raises(ModelResponseError):
        run_streamer(host)
    assert any("未保存" in error and "格式" in error for error in host.errors)
    assert not any("王妈" in error for error in host.errors)


def test_renaming_departed_speaker_to_new_guest_id_is_rejected():
    """绕过尝试：把离场者的台词换一个新 guest id（同名称呼）仍被校验拒绝。"""
    world = departed_world()
    host = make_host(world)
    raw = json.dumps({
        "segments": [{"kind": "speech", "speaker_id": "guest_maid", "text": "“老爷，汤要凉了。”"}],
        "guest_speakers": [{"speaker_id": "guest_maid", "display_name": "王妈"}],
    }, ensure_ascii=False)
    with pytest.raises(DialogueValidationError) as err:
        _guest_context(host, parse_dialogue_output(raw))
    assert err.value.category == "departed_speaker"
    assert err.value.speaker_id == "guest_concierge"


def test_redeclaring_departed_guest_id_is_typed_departed_error():
    world = departed_world()
    host = make_host(world)
    raw = json.dumps({
        "segments": [{"kind": "speech", "speaker_id": "guest_concierge", "text": "“汤。”"}],
        "guest_speakers": [{"speaker_id": "guest_concierge", "display_name": "王妈"}],
    }, ensure_ascii=False)
    with pytest.raises(SpeakerDepartedError, match="已离场"):
        _guest_context(host, parse_dialogue_output(raw))


def test_departed_npc_cannot_reenter_via_introduced_list():
    """绕过尝试：把已离场人物补进 introduced_npc_ids 同样按事实冲突拒绝。"""
    raw = json.dumps({
        "segments": [{"kind": "narration", "text": "门口有人。"}],
        "introduced_npc_ids": ["old_butler"],
    }, ensure_ascii=False)
    departed = {"old_butler": {"present": False, "reason": "夜班结束离开", "turn_id": "turn-8"}}
    with pytest.raises(SpeakerDepartedError) as err:
        decode_dialogue_output(raw, present_npc_ids={"guard"}, departed=departed)
    assert err.value.speaker_id == "old_butler"
    with pytest.raises(SpeakerAbsentError):
        decode_dialogue_output(raw, present_npc_ids={"guard"})


def test_consistency_gate_uses_main_path_speaker_sets():
    """一致性重写闸门与主路径同一套判定：抵达回合出发地送别、本回合离场者
    告别都合法；上一回合已离场者不在集合内（闸门 fail-open，这里测判定集合）。"""
    world = departed_world()
    world["scene_catalog"]["gate"] = {"id": "gate", "npcs_present": ["porter"]}
    world["npcs"].append({"id": "porter", "name": "门房"})
    engine = NS(
        context=NS(world_store=NS(load=lambda: world)),
        _adjudicated_outcome={"events": [{"type": "speaker_departure", "target": "guest_concierge"}]},
        _action_resolution=NS(is_arrival=True, origin_scene_id="gate"),
    )
    present, guest_ids, departed = _rewrite_allowed_speakers(engine)
    assert {"guard", "porter"} <= present  # 抵达回合放行出发场景人物
    assert "guest_concierge" in guest_ids  # 本回合离场者可告别
    assert set(departed) == {"guest_concierge"}
    engine._adjudicated_outcome = {"events": []}
    _present, guest_ids, _departed = _rewrite_allowed_speakers(engine)
    assert "guest_concierge" not in guest_ids  # 下一回合禁止
