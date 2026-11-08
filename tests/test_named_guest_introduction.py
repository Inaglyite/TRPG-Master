"""新人物首次介绍不得被误判不在场，更不能在修复中把台词转给熟人。"""
import json

import pytest

from src.ai.model.dialogue_output import SpeakerAbsentError, decode_dialogue_output
from src.ai.model.model_streamer import ModelResponseError
from src.gameplay.guest_speakers import register_guest_declarations
from src.gameplay.speaker_parser import parse_segments_full
from src.web.asset_payload import npc_lookup
from tests.model_fixtures import ReplayClient
from tests.test_dialogue_attribution import make_host, run, wire

GUEST = "guest_shen_qingwu"
SU = "su_wanqing"
LINE = "“先生，我这儿一天收几十件来稿，写什么的都有。您要找哪一件，总得先告诉我为什么。”"


def world():
    return {"current_scene": {"id": "newspaper", "npcs_present": [SU]},
            "npcs": [{"id": SU, "name": "苏晚晴"}]}


def arrival():
    return wire([
        {"kind": "narration", "text": "沈青梧放下红笔。"},
        {"kind": "speech", "speaker_id": SU, "text": "“沈小姐，这位是来查案的侦探。”"},
        {"kind": "speech", "speaker_id": GUEST, "text": LINE},
    ], guests=[{"speaker_id": GUEST, "display_name": "沈青梧"}], introduced=[GUEST])


def test_named_guest_can_be_introduced_and_speak_without_repair():
    state = world()
    host = make_host("", present=(SU,), world=state)
    host.client = ReplayClient([[{"content": arrival()}, {"finish_reason": "stop"}]])
    text, tools = run(host)
    assert host.client.calls == 1 and tools == [] and host.errors == []
    assert (LINE, GUEST) in host.speech
    assert not any(line == LINE and speaker == SU for line, speaker in host.speech)
    assert "【intro:guest_shen_qingwu】" not in text
    declarations = host._turn_guest_speaker_decls
    assert register_guest_declarations(host, declarations, world=state) == [GUEST]
    assert npc_lookup(state, GUEST) == {"id": GUEST, "name": "沈青梧", "avatar_file": None}
    segments, _, intros = parse_segments_full(text, is_valid_npc=lambda key: key in {SU, GUEST})
    assert intros == []  # guest registration does not invoke the authoritative reveal tool
    assert [s.npc_id for s in segments if s.kind == "speech"] == [SU, GUEST]
    assert [npc["id"] for npc in state["npcs"]] == [SU]  # no fabricated authoritative NPC
    next_raw = wire([{"kind": "speech", "speaker_id": GUEST, "text": "“您接着问吧。”"}])
    next_host = make_host(next_raw, present=(SU,), world=state)
    next_text, _ = run(next_host)
    assert "【npc:guest_shen_qingwu】" in next_text
    assert next_host._turn_guest_speaker_decls == []


def test_registered_guest_introduction_uses_same_presence_boundary_as_speech():
    raw = wire([{"kind": "narration", "text": "沈青梧起身迎接。"}], introduced=[GUEST])
    text = decode_dialogue_output(raw, present_npc_ids={SU}, guest_speaker_ids={GUEST})
    assert "【intro:guest_shen_qingwu】" not in text
    with pytest.raises(SpeakerAbsentError):
        decode_dialogue_output(raw, present_npc_ids={SU})


def test_format_repair_cannot_transfer_guest_words_to_existing_npc():
    original = json.loads(arrival())
    original["segments"].append({"kind": "narration", "text": "你可以——\n1. 追问"})
    broken = json.dumps(original, ensure_ascii=False)
    repaired = wire([
        {"kind": "narration", "text": "沈青梧没有立刻接话。"},
        {"kind": "speech", "speaker_id": SU, "text": LINE.replace("先生，", "")},
    ])
    host = make_host("", present=(SU,), world=world())
    host.client = ReplayClient([
        [{"content": broken}, {"finish_reason": "stop"}],
        [{"content": repaired}, {"finish_reason": "stop"}],
    ])
    with pytest.raises(ModelResponseError) as caught:
        run(host)
    assert caught.value.__cause__.category == "speaker_reassigned"
    assert host.client.calls == 2 and host.speech == []
    assert "_dialogue_repair_original" not in host.__dict__
    assert any("台词的归属" in error for error in host.errors)


def test_format_repair_preserves_new_guest_identity_and_then_clears_guard():
    original = json.loads(arrival())
    original["segments"].append({"kind": "narration", "text": "你可以——\n1. 追问"})
    host = make_host("", present=(SU,), world=world())
    host.client = ReplayClient([
        [{"content": json.dumps(original, ensure_ascii=False)}, {"finish_reason": "stop"}],
        [{"content": arrival()}, {"finish_reason": "stop"}],
    ])
    text, _ = run(host)
    assert host.client.calls == 2 and host.errors == []
    assert "【npc:guest_shen_qingwu】" in text
    assert (LINE, GUEST) in host.speech
    assert "_dialogue_repair_original" not in host.__dict__


@pytest.mark.parametrize("repaired", [
    [{"kind": "speech", "speaker_id": GUEST, "text": LINE.replace("先生，", "")}],
    [{"kind": "narration", "text": "她已离场，不能继续现场交谈。"}],
    [{"kind": "speech", "speaker_id": SU, "text": "“你好。”"}],
])
def test_identity_guard_preserves_valid_repair_and_allows_removing_invalid_speech(repaired):
    from src.ai.model.dialogue_output import parse_dialogue_output
    from src.ai.model.dialogue_repair_guard import validate_repair_speakers

    validate_repair_speakers(arrival(), parse_dialogue_output(wire(repaired)))
