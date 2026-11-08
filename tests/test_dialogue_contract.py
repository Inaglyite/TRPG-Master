"""Live menu contract: reject before display and repair without game tools."""
import json
from types import SimpleNamespace

import pytest

from src.ai.model.dialogue_contract import validate_live_dialogue
from src.ai.model.dialogue_output import DialogueValidationError, parse_dialogue_output
from src.ai.model.model_streamer import ModelResponseError
from tests.model_fixtures import build_chunk, run_stream
from tests.test_dialogue_output import make_host


def payload(text="窗外雨声渐密。", choices=None, kind="narration"):
    block = {"kind": kind, "text": text}
    if kind == "speech":
        block["speaker_id"] = "su"
    return json.dumps({"segments": [block], "choices": choices or ["翻看剪报", "询问细节"]}, ensure_ascii=False)


@pytest.mark.parametrize("text", [
    "雨落下来。\n你可以——\n1. 翻看剪报。\n2. 询问细节。",
    "### **你可以选择：**\n1）翻看剪报\n2）询问细节\n雨还在下。",
    "可选行动\n① 翻看剪报\n② 询问细节",
    "接下来你可以 ──\n1、翻看剪报\n2、询问细节",
    "你可以——",
    "雨落下来。\n1. 翻看剪报。\n2. 询问细节。",
])
@pytest.mark.parametrize("kind", ["narration", "speech"])
def test_embedded_menu_rejected_before_any_display(text, kind):
    host, _ = make_host(payload(text, kind=kind))
    with pytest.raises(ModelResponseError) as caught:
        run_stream(host)
    assert caught.value.__cause__.category == "embedded_choice_menu"
    assert host.speech == []


@pytest.mark.parametrize("choice", [" ", "1. 翻看剪报", "① 翻看剪报", "翻看\n剪报", "翻看\r剪报", "你可以——"])
def test_choice_items_require_plain_single_line_text(choice):
    with pytest.raises(DialogueValidationError) as caught:
        validate_live_dialogue(parse_dialogue_output(payload(choices=[choice])))
    assert caught.value.category == "invalid_choice_format"


@pytest.mark.parametrize("text", [
    "她说你可以先坐下，慢慢看看。",
    "纸上列着：\n1. 门窗完好。\n2. 没有脚印。",
    "他写下：你可以——相信自己的判断。",
])
def test_ordinary_prose_and_document_lists_remain_valid(text):
    validate_live_dialogue(parse_dialogue_output(payload(text)))


@pytest.mark.parametrize("repair_succeeds", [True, False])
def test_menu_repair_is_bounded_and_never_exposes_rejected_draft(repair_succeeds):
    bad = payload("雨声渐密。\n你可以——\n1. 翻看剪报。\n2. 询问细节。")
    host, _ = make_host("")
    requests = []
    replies = iter([bad, payload() if repair_succeeds else bad])

    def create(**kwargs):
        assert host.speech == []
        requests.append(kwargs)
        return iter([build_chunk({"content": next(replies)}), build_chunk({"finish_reason": "stop"})])

    host.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    if repair_succeeds:
        text, calls = run_stream(host, retry_on_empty=True)
        assert calls == []
        assert text.count("你可以——") == 1
        assert host.errors == []
    else:
        with pytest.raises(ModelResponseError):
            run_stream(host, retry_on_empty=True)
        assert host.speech == []
        assert any("选项格式" in error for error in host.errors)
    rejected = [stage for stage in host._turn_stage_trace if stage["phase"] == "dialogue_validation"]
    assert len(rejected) == (1 if repair_succeeds else 2)
    assert all(stage["code"] == "embedded_choice_menu" for stage in rejected)
    assert len(requests) == 2
    assert not requests[1].get("tools")
    assert requests[1]["temperature"] == 0
    instruction = str(requests[1]["messages"])
    assert "choices" in instruction and "菜单" in instruction


def test_consistency_rewrite_cannot_reintroduce_embedded_menu():
    from src.gameplay.narrative_consistency import apply_narrative_consistency
    from tests.test_dialogue_attribution import _structured_consistency_engine

    original = "你在门前等了三天。"
    engine = _structured_consistency_engine(payload("你等了片刻。\n你可以——\n1. 翻看剪报\n2. 询问细节"))
    assert apply_narrative_consistency(engine, original) == original
