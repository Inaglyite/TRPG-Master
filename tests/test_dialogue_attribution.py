"""对话归属回归（2026-10-21 金三爷事故）：

- 结构化正本贯穿全链路：定稿后引擎消息历史保留发言标签，下一轮模型请求
  恢复 speech 结构；旧无归属存档保持单 narration，不臆造身份。
- 含明显直接台词但全部塞进 narration 的输出触发一次有界归属修复；
  修复仍是纯旁白时接受，不循环。
- 临时人物（伙计/路人）通过 guest_speakers 显式声明获得展示身份；
  未声明、伪造 id、场景不符一律拒绝。
- 时间一致性检查按结构区分台词与旁白；一致性重写不得丢失/串换归属。
"""

import copy
import json
from types import SimpleNamespace as NS

import pytest

from src.ai.model.dialogue_output import (
    decode_dialogue_output,
    finalized_history_content,
    parse_dialogue_output,
)
from src.ai.model.model_request import prepare_model_request
from src.ai.model.model_streamer import ModelResponseError, ModelStreamer
from src.gameplay.guest_speakers import (
    guest_registry,
    register_guest_declarations,
)
from src.gameplay.narrative_consistency import (
    apply_narrative_consistency,
    consistency_violations,
)
from src.gameplay.speaker_parser import parse_segments
from src.web.asset_payload import npc_id_known, npc_lookup, speaker_payload
from tests.model_fixtures import ReplayClient, build_chunk, streamer_host
from tests.test_outcome_contract_gates import consistency_engine


def wire(segments, *, choices=None, introduced=None, guests=None):
    payload = {
        "segments": segments,
        "introduced_npc_ids": list(introduced or []),
        "choices": list(choices or []),
    }
    if guests is not None:
        payload["guest_speakers"] = guests
    return json.dumps(payload, ensure_ascii=False)


JIN_REFUSAL = wire(
    [
        {"kind": "narration", "text": "金三爷拨着算盘，眼皮都没抬。"},
        {"kind": "speech", "speaker_id": "jin", "text": "侦探也好，巡捕也罢，洋先生的事我担不起。"},
        {"kind": "speech", "speaker_id": "jin", "text": "您要真想知道，去问洋人自己的圈子。"},
    ],
    choices=["换个话题。", "[自由行动] 你决定做什么？"],
)


def make_host(raw, *, present=("jin", "su"), valid=None, world=None, finish_reason="stop"):
    """与 test_dialogue_output.make_host 同构，但场景人物可定制。"""
    host = streamer_host(None)
    host._structured_dialogue = True
    world = world if world is not None else {"current_scene": {"npcs_present": list(present)}}
    host.context.world_store = NS(load=lambda: world)
    valid = set(valid if valid is not None else present)
    host.is_valid_npc_id = lambda value: value in valid
    host.npc_speaker_aliases = lambda: pytest.fail("structured dialogue must never infer a speaker")
    host.speech = []
    host.cb.on_narrative = lambda text, npc_id=None: host.speech.append((text, npc_id))

    def create(**kwargs):
        def chunks():
            for i in range(0, len(raw), 7):
                assert host.speech == []  # JSON 不完整时不得播出
                yield build_chunk({"content": raw[i : i + 7]})
            yield build_chunk({"finish_reason": finish_reason})

        return chunks()

    host.client = NS(chat=NS(completions=NS(create=create)))
    return host


def run(host, *, retry_on_empty=True):
    streamer = ModelStreamer(
        host,
        log_error=lambda _m: None,
        log_model_call=lambda *_a, **_k: None,
        sleep=lambda _s: None,
    )
    return streamer.stream(
        "test-model", policy=host.policy, enable_tools=False, retry_on_empty=retry_on_empty
    )


# ---- A. 结构化正本贯穿全链路（F2，矩阵 1/2/3） -------------------------------


def test_finalized_history_keeps_speaker_structure_for_next_request():
    """金三爷事故核心：定稿消息历史必须保留发言标签，下一轮请求还原 speech。"""
    host = make_host(JIN_REFUSAL)
    text, calls = run(host)
    assert calls == []
    segments, clean = parse_segments(text)
    assert [s.npc_id for s in segments if s.kind == "speech"] == ["jin", "jin"]
    assert "【" not in clean  # 展示/回合记录用干净正文

    content = finalized_history_content(text, clean, structured=True)
    assert "【npc:jin】" in content  # 模型历史正本保留归属
    assert finalized_history_content(text, clean, structured=False) == clean

    # 连续三轮：每一轮定稿后的历史都在下一次真实请求里还原为 speech 结构
    history = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "[玩家行动] 第1轮"},
        {"role": "assistant", "content": content},
    ]
    followups = [
        wire(
            [
                {"kind": "narration", "text": "苏晚晴放下钢笔。"},
                {
                    "kind": "speech",
                    "speaker_id": "su",
                    "text": "金三爷做生意三十年，您先想好怎么问。",
                },
            ]
        ),
        wire(
            [
                {"kind": "narration", "text": "伊芙琳站起身。"},
                {"kind": "speech", "speaker_id": "evelyn", "text": "我可以跟您一起去。"},
            ]
        ),
    ]
    prepared_histories = []
    for index, followup in enumerate(followups):
        host = make_host(followup, present=("jin", "su", "evelyn"))
        host.messages = list(history) + [{"role": "user", "content": f"[玩家行动] 第{index + 2}轮"}]
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
        prepared_histories.append(prepared.provider_kwargs["messages"])
        text, _ = run(host)
        _segs, clean = parse_segments(text)
        history.append({"role": "user", "content": f"[玩家行动] 第{index + 2}轮"})
        history.append(
            {
                "role": "assistant",
                "content": finalized_history_content(text, clean, structured=True),
            }
        )

    first = json.loads(prepared_histories[0][-2]["content"])
    assert [b["kind"] for b in first["segments"]] == ["narration", "speech", "speech"]
    assert [b.get("speaker_id") for b in first["segments"][1:]] == ["jin", "jin"]
    assert first["choices"] == ["换个话题。", "[自由行动] 你决定做什么？"]
    second = json.loads(prepared_histories[1][-2]["content"])
    assert [b.get("speaker_id") for b in second["segments"] if b["kind"] == "speech"] == ["su"]
    # 存档面不被改写：只有请求副本规范化
    assert "【npc:jin】" in history[2]["content"]


def test_legacy_unowned_history_stays_plain_narration():
    """旧存档没有权威归属：历史转换不臆造身份，整体作为旁白。"""
    content = finalized_history_content(
        "旧存档正文，没有任何标签。", "旧存档正文，没有任何标签。", structured=True
    )
    assert "【" not in content
    host = make_host(JIN_REFUSAL)
    host.messages = [
        {"role": "system", "content": "s"},
        {"role": "assistant", "content": content},
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
    parsed = parse_dialogue_output(prepared.provider_kwargs["messages"][1]["content"])
    assert [block.kind for block in parsed.segments] == ["narration"]


def test_restore_tagged_speakers_rebuilds_old_clean_save_history():
    """旧存档只存干净正文：载入时用回合记录的 narrative_segments 精确回填标签。"""
    from src.ai.context.narrative_history import restore_tagged_speakers

    clean = "金三爷摇头。\n\n侦探也好，巡捕也罢，洋先生的事我担不起。"
    segments = [
        {"kind": "narration", "text": "金三爷摇头。"},
        {"kind": "speech", "npc_id": "jin", "text": "侦探也好，巡捕也罢，洋先生的事我担不起。"},
    ]
    journal = NS(
        list_completed=lambda limit=100: [
            {"narrative": clean, "narrative_segments": segments},
            {"narrative": "旧纯文本回合", "narrative_segments": []},
        ]
    )
    messages = [
        {"role": "system", "content": "s"},
        {"role": "assistant", "content": clean},
        {"role": "assistant", "content": "旧纯文本回合"},
        {"role": "assistant", "content": "对不上任何记录的内容"},
    ]
    out = restore_tagged_speakers(messages, journal)
    assert "【npc:jin】" in out[1]["content"]
    assert out[2]["content"] == "旧纯文本回合"  # 无 speech 的记录不动
    assert out[3]["content"] == "对不上任何记录的内容"  # 匹配不上不臆造

    # journal 故障：原样返回，不阻断载入
    broken = NS(list_completed=lambda limit=100: (_ for _ in ()).throw(OSError("db down")))
    assert restore_tagged_speakers(messages, broken) is messages

    # 回填后的历史在请求线上还原为 speech 结构
    host = make_host(JIN_REFUSAL)
    host.messages = [out[0], out[1], {"role": "user", "content": "[玩家行动] 继续"}]
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
    restored = parse_dialogue_output(prepared.provider_kwargs["messages"][1]["content"])
    assert [b.kind for b in restored.segments] == ["narration", "speech"]
    assert restored.segments[1].speaker_id == "jin"


# ---- B. 归属校验与有界语义复核（F1/F8，矩阵 7/8） -----------------------------

ALL_NARRATION_QUOTED = wire(
    [
        {
            "kind": "narration",
            "text": "金三爷拨着算盘。“侦探也好，巡捕也罢，洋先生的事我担不起。”他叹了口气。“您要真想知道，去问洋人自己的圈子。”",
        },
    ]
)

GOOD_JIN = wire(
    [
        {"kind": "narration", "text": "金三爷拨着算盘。"},
        {"kind": "speech", "speaker_id": "jin", "text": "侦探也好，巡捕也罢，洋先生的事我担不起。"},
    ]
)


def review_client(story_raw, review_payload):
    """第一次流式 story 调用 + 一次非流式归属复核调用。"""
    calls = {"n": 0}

    def create(**kwargs):
        calls["n"] += 1
        if kwargs.get("stream"):
            return iter(
                [build_chunk({"content": story_raw}), build_chunk({"finish_reason": "stop"})]
            )
        return NS(choices=[NS(finish_reason="stop", message=NS(content=review_payload))])

    return NS(chat=NS(completions=NS(create=create))), calls


FIXED_JIN_VERDICT = json.dumps(
    {
        "contains_direct_speech": True,
        "segments": [
            {"kind": "narration", "text": "金三爷拨着算盘。"},
            {
                "kind": "speech",
                "speaker_id": "jin",
                "text": "侦探也好，巡捕也罢，洋先生的事我担不起。",
            },
            {"kind": "narration", "text": "他叹了口气。"},
            {"kind": "speech", "speaker_id": "jin", "text": "您要真想知道，去问洋人自己的圈子。"},
        ],
    },
    ensure_ascii=False,
)

CLEAN_NARRATION_VERDICT = json.dumps({"contains_direct_speech": False})


def test_attribution_review_accepts_the_choices_copy_required_by_its_prompt():
    from src.ai.model.dialogue_attribution import AttributionVerdict, validate_review_fix
    from src.ai.model.dialogue_output import DialogueOutput

    original = DialogueOutput.model_validate(json.loads(ALL_NARRATION_QUOTED))
    verdict = AttributionVerdict.model_validate({**json.loads(FIXED_JIN_VERDICT), "choices": original.choices})
    fixed = validate_review_fix(original, verdict, present_npc_ids={"jin"}, guest_speaker_ids=set())
    assert fixed.choices == original.choices


def test_attribution_review_still_rejects_changed_choices():
    from src.ai.model.dialogue_attribution import (
        AttributionReviewError,
        AttributionVerdict,
        validate_review_fix,
    )
    from src.ai.model.dialogue_output import DialogueOutput

    original = DialogueOutput.model_validate(json.loads(ALL_NARRATION_QUOTED))
    verdict = AttributionVerdict.model_validate({**json.loads(FIXED_JIN_VERDICT), "choices": ["凭空添加的行动"]})
    with pytest.raises(AttributionReviewError, match="choices"):
        validate_review_fix(original, verdict, present_npc_ids={"jin"}, guest_speaker_ids=set())


def test_suspect_narration_gets_one_bounded_semantic_review():
    host = make_host("")
    host.client, calls = review_client(ALL_NARRATION_QUOTED, FIXED_JIN_VERDICT)
    text, _ = run(host)
    assert calls["n"] == 2  # 一次故事 + 一次复核，有界
    assert "【npc:jin】" in text
    assert [(t, npc) for t, npc in host.speech if npc] == [
        ("侦探也好，巡捕也罢，洋先生的事我担不起。", "jin"),
        ("您要真想知道，去问洋人自己的圈子。", "jin"),
    ]


def test_review_confirmed_pure_narration_is_accepted():
    """语义确认纯旁白：放行原稿（与“复核失效”严格区分）。"""
    host = make_host("")
    host.client, calls = review_client(ALL_NARRATION_QUOTED, CLEAN_NARRATION_VERDICT)
    text, _ = run(host)
    assert calls["n"] == 2
    assert "洋先生的事" in text
    assert all(npc is None for _t, npc in host.speech)


def test_failed_review_never_publishes_suspect_draft():
    """复核调用失败或修正稿非法：不播出、不提交可疑草稿（可恢复失败）。"""
    host = make_host("")
    host.client, calls = review_client(ALL_NARRATION_QUOTED, "not-a-verdict")
    with pytest.raises(ModelResponseError):
        run(host)
    assert calls["n"] == 2
    assert host.speech == []
    assert any("归属" in error for error in host.errors)

    # 复核声称有台词但修正稿原样照搬：同样拒绝
    host2 = make_host("")
    unchanged = json.dumps(
        {
            "contains_direct_speech": True,
            "segments": [
                {
                    "kind": "narration",
                    "text": json.loads(ALL_NARRATION_QUOTED)["segments"][0]["text"],
                }
            ],
        },
        ensure_ascii=False,
    )
    host2.client, calls2 = review_client(ALL_NARRATION_QUOTED, unchanged)
    with pytest.raises(ModelResponseError):
        run(host2)
    assert calls2["n"] == 2 and host2.speech == []

    # 复核修正稿改写文字：文本保全拒绝
    host3 = make_host("")
    tampered = json.dumps(
        {
            "contains_direct_speech": True,
            "segments": [
                {"kind": "narration", "text": "金三爷拨着算盘。"},
                {"kind": "speech", "speaker_id": "jin", "text": "这事与我无关。"},
            ],
        },
        ensure_ascii=False,
    )
    host3.client, _ = review_client(ALL_NARRATION_QUOTED, tampered)
    with pytest.raises(ModelResponseError):
        run(host3)
    assert host3.speech == []


def test_mixed_output_with_unattributed_quote_still_reviews():
    """已有一段正确 speech 不再豁免其它 narration 的归属复核。"""
    mixed = wire(
        [
            {"kind": "speech", "speaker_id": "jin", "text": "我去门口招呼客人。"},
            {"kind": "narration", "text": "伙计擦着盘子。“二位随便看，这盘子是正经货。”"},
        ]
    )
    verdict = json.dumps(
        {
            "contains_direct_speech": True,
            "segments": [
                {"kind": "speech", "speaker_id": "jin", "text": "我去门口招呼客人。"},
                {"kind": "narration", "text": "伙计擦着盘子。"},
            ],
            "guest_speakers": [{"speaker_id": "guest_clerk_1", "display_name": "伙计"}],
        },
        ensure_ascii=False,
    )
    # 修正稿丢了引语文字 → 文本保全拒绝
    host = make_host("")
    host.client, _ = review_client(mixed, verdict)
    with pytest.raises(ModelResponseError):
        run(host)

    verdict_ok = json.dumps(
        {
            "contains_direct_speech": True,
            "segments": [
                {"kind": "speech", "speaker_id": "jin", "text": "我去门口招呼客人。"},
                {"kind": "narration", "text": "伙计擦着盘子。"},
                {
                    "kind": "speech",
                    "speaker_id": "guest_clerk_1",
                    "text": "二位随便看，这盘子是正经货。",
                },
            ],
            "guest_speakers": [{"speaker_id": "guest_clerk_1", "display_name": "伙计"}],
        },
        ensure_ascii=False,
    )
    host2 = make_host("")
    host2.client, calls = review_client(mixed, verdict_ok)
    text, _ = run(host2)
    assert calls["n"] == 2
    assert "【npc:guest_clerk_1】" in text
    assert host2._turn_guest_speaker_decls == [
        {"speaker_id": "guest_clerk_1", "display_name": "伙计"}
    ]


def test_guest_only_scene_is_reviewed():
    """纯临时人物场景同样触发归属复核。"""
    raw = wire(
        [{"kind": "narration", "text": "伙计擦着盘子。“二位随便看，这盘子是正经货。”"}],
        guests=[{"speaker_id": "guest_clerk_1", "display_name": "伙计"}],
    )
    verdict = json.dumps(
        {
            "contains_direct_speech": True,
            "segments": [
                {"kind": "narration", "text": "伙计擦着盘子。"},
                {
                    "kind": "speech",
                    "speaker_id": "guest_clerk_1",
                    "text": "二位随便看，这盘子是正经货。",
                },
            ],
        },
        ensure_ascii=False,
    )
    host = make_host("", present=(), valid=())
    host.client, calls = review_client(raw, verdict)
    text, _ = run(host)
    assert calls["n"] == 2
    assert "【npc:guest_clerk_1】" in text


def test_actual_incident_unquoted_dialogue_is_reviewed():
    """本次事故原文（无引号双人物台词）：必须触发复核而不是一次通过。"""
    narrative = (
        "你把盘子接在手里，随口夸了两句釉色，话锋一转，问那道帘子后头是什么去处。"
        "伙计的笑容僵了一下，把盘子往你手里一塞，退到帘前站定。\n\n"
        "后头是账房，客人不便。您看这盘子边口，一点磕碰都没有——\n\n"
        "他没再说下去，眼睛却不再离开你，脚下钉在原地。门口那边，金三爷朝这边瞥了一眼，笑意未变，缓步踱了过来。\n\n"
        "先生看上盘子了？这盘子我给您个实在价，伙计，还不快请这位先生上楼看看，三楼有几件新到的瓷。"
    )
    raw = wire([{"kind": "narration", "text": narrative}])
    verdict = json.dumps(
        {
            "contains_direct_speech": True,
            "segments": [
                {
                    "kind": "narration",
                    "text": "你把盘子接在手里，随口夸了两句釉色，话锋一转，问那道帘子后头是什么去处。伙计的笑容僵了一下，把盘子往你手里一塞，退到帘前站定。",
                },
                {
                    "kind": "speech",
                    "speaker_id": "guest_clerk_1",
                    "text": "后头是账房，客人不便。您看这盘子边口，一点磕碰都没有——",
                },
                {
                    "kind": "narration",
                    "text": "他没再说下去，眼睛却不再离开你，脚下钉在原地。门口那边，金三爷朝这边瞥了一眼，笑意未变，缓步踱了过来。",
                },
                {
                    "kind": "speech",
                    "speaker_id": "jin",
                    "text": "先生看上盘子了？这盘子我给您个实在价，伙计，还不快请这位先生上楼看看，三楼有几件新到的瓷。",
                },
            ],
            "guest_speakers": [{"speaker_id": "guest_clerk_1", "display_name": "伙计"}],
        },
        ensure_ascii=False,
    )
    host = make_host("")
    host.client, calls = review_client(raw, verdict)
    text, _ = run(host)
    assert calls["n"] == 2
    assert "【npc:guest_clerk_1】" in text and "【npc:jin】" in text
    spoken = [(t, npc) for t, npc in host.speech if npc]
    assert spoken[0][0].startswith("后头是账房") and spoken[0][1] == "guest_clerk_1"
    assert spoken[1][0].startswith("先生看上盘子了") and spoken[1][1] == "jin"


def test_quoted_short_note_does_not_trigger_review():
    note = wire([{"kind": "narration", "text": "纸上写着：“别让它出声。”"}])
    host = make_host(note)
    text, _ = run(host)
    assert len(host.speech) == 1 and host.speech[0][1] is None


def test_pure_narration_turn_never_triggers_repair():
    plain = wire([{"kind": "narration", "text": "雨下了一整夜，街上空无一人。"}])
    host = make_host(plain)
    text, _ = run(host)
    assert "雨下了一整夜" in text and not host.errors


def test_present_but_unresolvable_speaker_id_is_repaired_not_demoted():
    """在场名单与身份表不一致（悬空 id）：修复而不是静默降级为旁白。"""
    raw = wire([{"kind": "speech", "speaker_id": "jin", "text": "这话不该由我来说。"}])
    host = make_host(raw, valid=())  # present 有 jin，但身份表解析不了
    host.client = ReplayClient(
        [
            [{"content": raw}, {"finish_reason": "stop"}],
            [
                {"content": wire([{"kind": "narration", "text": "店里一阵沉默。"}])},
                {"finish_reason": "stop"},
            ],
        ]
    )
    text, _ = run(host)
    assert host.client.calls == 2
    assert text == "店里一阵沉默。"
    assert all(npc is None for _t, npc in host.speech)


def test_stubborn_unresolvable_speaker_fails_closed_without_emitting():
    raw = wire([{"kind": "speech", "speaker_id": "jin", "text": "这话不该由我来说。"}])
    host = make_host(raw, valid=())
    host.client = ReplayClient(
        [
            [{"content": raw}, {"finish_reason": "stop"}],
            [{"content": raw}, {"finish_reason": "stop"}],
        ]
    )
    with pytest.raises(ModelResponseError):
        run(host)
    assert host.speech == []


# ---- C. 临时人物 guest_speakers（F3，矩阵 6） --------------------------------

CLERK_TURN = wire(
    [
        {"kind": "narration", "text": "柜台后的伙计擦着盘子。"},
        {"kind": "speech", "speaker_id": "guest_clerk_1", "text": "二位看点什么？这盘子是正经货。"},
    ],
    guests=[{"speaker_id": "guest_clerk_1", "display_name": "伙计"}],
)


def test_declared_guest_speaker_gets_speech_bubble_and_declaration_recorded():
    host = make_host(CLERK_TURN)
    text, _ = run(host)
    assert "【npc:guest_clerk_1】" in text
    assert [(t, npc) for t, npc in host.speech if npc] == [
        ("二位看点什么？这盘子是正经货。", "guest_clerk_1")
    ]
    assert host._turn_guest_speaker_decls == [
        {"speaker_id": "guest_clerk_1", "display_name": "伙计"}
    ]


def test_undeclared_guest_id_is_rejected():
    raw = wire([{"kind": "speech", "speaker_id": "guest_clerk_1", "text": "随便看。"}])
    with pytest.raises(ValueError, match="发言者不在当前场景"):
        decode_dialogue_output(raw, present_npc_ids={"jin"})


def test_guest_id_must_use_guest_prefix():
    raw = wire(
        [{"kind": "speech", "speaker_id": "jin", "text": "随便看。"}],
        guests=[{"speaker_id": "jin", "display_name": "假金三爷"}],
    )
    with pytest.raises(ValueError):
        decode_dialogue_output(raw, present_npc_ids={"jin"}, guest_speaker_ids={"jin"})


def test_guest_declaration_rules():
    engine = NS(
        context=NS(world_store=None),
    )
    world = {"npcs": [{"id": "jin", "name": "金三爷"}], "current_scene": {"id": "shop"}}
    # 与真实 NPC 撞 id：拒绝（前缀约束之外的兜底）
    assert register_guest_declarations(engine, [], world=world) == []
    accepted = register_guest_declarations(
        engine,
        [{"speaker_id": "guest_a", "display_name": "伙计"}],
        world=world,
    )
    assert accepted == ["guest_a"]
    assert guest_registry(world)["guest_a"]["display_name"] == "伙计"
    assert guest_registry(world)["guest_a"]["scene_id"] == "shop"
    # 幂等：同 id 同称呼重复声明不重复落账
    assert (
        register_guest_declarations(
            engine, [{"speaker_id": "guest_a", "display_name": "伙计"}], world=world
        )
        == []
    )
    # 同名不串人：第二个“伙计”是独立身份
    accepted = register_guest_declarations(
        engine, [{"speaker_id": "guest_b", "display_name": "伙计"}], world=world
    )
    assert accepted == ["guest_b"]
    assert guest_registry(world)["guest_b"]["display_name"] == "伙计"


def test_registered_guest_speaks_next_turn_without_redeclaration_and_scene_binding():
    world = {
        "npcs": [{"id": "jin", "name": "金三爷"}],
        "current_scene": {"id": "shop", "npcs_present": ["jin"]},
        "guest_speakers": {"guest_clerk_1": {"display_name": "伙计", "scene_id": "shop"}},
    }
    raw = wire([{"kind": "speech", "speaker_id": "guest_clerk_1", "text": "您回来了。"}])
    host = make_host(raw, present=("jin",), valid=("jin",), world=world)
    text, _ = run(host)
    assert "【npc:guest_clerk_1】" in text
    assert host._turn_guest_speaker_decls == []

    # 换到别的场景：临时人物不在场，发言拒绝
    other = copy.deepcopy(world)
    other["current_scene"] = {"id": "street", "npcs_present": ["jin"]}
    with pytest.raises(ValueError, match="发言者不在当前场景"):
        decode_dialogue_output(raw, present_npc_ids={"jin"})


def test_guest_branch_copy_keeps_identity():
    """分支/读档直接复制世界状态：临时身份跟随世界字典，不共享缓存。"""
    world = {
        "npcs": [],
        "current_scene": {"id": "shop"},
        "guest_speakers": {"guest_clerk_1": {"display_name": "伙计", "scene_id": "shop"}},
    }
    branched = copy.deepcopy(world)
    branched["guest_speakers"]["guest_clerk_1"]["display_name"] = "柜台伙计"
    assert guest_registry(world)["guest_clerk_1"]["display_name"] == "伙计"
    assert guest_registry(branched)["guest_clerk_1"]["display_name"] == "柜台伙计"


def test_commit_guest_declarations_satisfies_world_store_contract():
    """world_store.update 的 mutator 只准返回 dict|None：登记必须就地改世界。"""
    from src.gameplay.guest_speakers import commit_guest_declarations

    class _Store:
        def __init__(self, world):
            self._world = world

        def update(self, mutator):
            replacement = mutator(self._world)
            assert replacement is None or isinstance(replacement, dict)

    world = {"npcs": [], "current_scene": {"id": "shop"}}
    engine = NS(context=NS(world_store=_Store(world)))
    accepted = commit_guest_declarations(
        engine, [{"speaker_id": "guest_a", "display_name": "伙计"}]
    )
    assert accepted == ["guest_a"]
    assert guest_registry(world)["guest_a"]["display_name"] == "伙计"


def test_guest_lookup_uses_only_public_display_name_and_never_avatar():
    world = {
        "npcs": [],
        "guest_speakers": {"guest_clerk_1": {"display_name": "伙计", "scene_id": "shop"}},
    }
    profile = npc_lookup(world, "guest_clerk_1")
    assert profile == {"id": "guest_clerk_1", "name": "伙计", "avatar_file": None}
    assert npc_id_known(world, "guest_clerk_1")
    engine = NS(context=NS(world_store=NS(load=lambda: world), initial_state_file=None))
    engine.context.initial_state_file = NS(is_file=lambda: False)
    payload = speaker_payload("guest_clerk_1", engine)
    assert payload == {"type": "npc", "id": "guest_clerk_1", "name": "伙计"}
    assert "avatar" not in payload


# ---- C. 无头像与资源故障（F6/F7） --------------------------------------------


def test_broken_optional_catalog_keeps_world_identity():
    """初始资源清单损坏不得抹掉世界状态里完整的人物身份。"""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as temp:
        initial = Path(temp) / "initial.json"
        initial.write_text("{broken", encoding="utf-8")
        world = {"npcs": [{"id": "jin", "name": "金三爷"}], "asset_map": {"npcs": {}}}
        engine = NS(
            context=NS(
                world_store=NS(load=lambda: world),
                initial_state_file=initial,
                assets_dir=Path(temp),
                module_name="audit",
            )
        )
        assert speaker_payload("jin", engine) == {"type": "npc", "id": "jin", "name": "金三爷"}


# ---- D. 一致性重写保归属（F4/F5，矩阵 8） -------------------------------------


def test_tagged_speech_recollection_is_not_a_time_violation():
    text = "【npc:jin】我三天前就告诉过他了，这事得从半个月前说起。【/npc】"
    assert consistency_violations(text, settled_minutes=5) == []


def test_narration_time_span_still_violates_with_tagged_speech():
    text = "你等了三天。【npc:jin】我三天前就告诉过他了。【/npc】"
    violations = consistency_violations(text, settled_minutes=5)
    assert violations and "天" in violations[0]


def _structured_consistency_engine(reply, *, present=("jin",)):
    engine = consistency_engine(
        {
            "status": "executed_success",
            "events": [{"type": "time_advanced", "before": 0, "after": 5}],
        },
        reply=reply,
    )
    engine._structured_dialogue = True
    engine.context.world_store = NS(load=lambda: {"current_scene": {"npcs_present": list(present)}})
    return engine


def test_consistency_rewrite_dropping_speakers_keeps_original():
    original = "你等了三天。【npc:jin】这事我担不起。【/npc】"
    bad_reply = wire([{"kind": "narration", "text": "你稍等片刻，金三爷摇头，这事他担不起。"}])
    engine = _structured_consistency_engine(bad_reply)
    assert apply_narrative_consistency(engine, original) == original


def test_consistency_rewrite_swapping_speaker_keeps_original():
    original = "你等了三天。【npc:jin】这事我担不起。【/npc】"
    swapped = wire(
        [
            {"kind": "narration", "text": "你稍等。"},
            {"kind": "speech", "speaker_id": "su", "text": "这事他担不起。"},
        ]
    )
    engine = _structured_consistency_engine(swapped, present=("jin", "su"))
    assert apply_narrative_consistency(engine, original) == original


def test_consistency_rewrite_preserving_speakers_is_accepted():
    original = "你等了三天。【npc:jin】这事我担不起。【/npc】"
    good = wire(
        [
            {"kind": "narration", "text": "你等了一会儿。"},
            {"kind": "speech", "speaker_id": "jin", "text": "这事我担不起。"},
        ]
    )
    engine = _structured_consistency_engine(good)
    rewritten = apply_narrative_consistency(engine, original)
    assert "【npc:jin】" in rewritten and "你等了一会儿" in rewritten
