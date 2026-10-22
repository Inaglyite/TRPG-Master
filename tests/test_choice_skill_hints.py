"""选项“可能检定”技能提示（T03）。

模型只在结构化输出里提议技能键（choice_skill_hints）；平台按统一有效技能
视图（skill_view，与正式检定同一来源）解析可用性、显示名与当前有效值。
注解仅作展示，不承诺必定检定；越界索引、未知技能、损坏元数据按条丢弃并
记诊断，整轮不失败；修复/归属复核后索引跟随选项不串位；回合记录与读档
保留已解析的注解。旧输出没有该字段时一切照旧。
"""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.ai.model.dialogue_output import (
    DialogueOutput,
    choice_skill_hint_map,
    decode_dialogue_output,
)
from src.gameplay.choices import extract_action_choices, resolve_skill_hints
from src.gameplay.skill_view import rule_skill_catalog
from tests.model_fixtures import build_chunk, run_stream, streamer_host


def pc_sheet(**overrides):
    sheet = {
        "name": "调查员",
        "attributes": {"STR": 45, "DEX": 55, "CON": 50, "INT": 65, "POW": 70, "SIZ": 50, "APP": 50, "EDU": 70},
        "skills": {"spot_hidden": 70, "history": 40, "甲骨文辨识": 25},
    }
    sheet.update(overrides)
    return sheet


def story_payload(**overrides):
    payload = {
        "segments": [{"kind": "narration", "text": "书房里静得可怕，摹本就摊在桌上。"}],
        "introduced_npc_ids": [],
        "choices": ["研究古文字摹本", "离开书房", "[自由行动] 你决定做什么？"],
    }
    payload.update(overrides)
    return payload


def hinted_choices(hint_map, pc):
    text = decode_dialogue_output(
        json.dumps(story_payload(), ensure_ascii=False), present_npc_ids=set()
    )
    choices = extract_action_choices(text)
    return resolve_skill_hints(choices, hint_map, pc)


class ResolveSkillHintTests(unittest.TestCase):
    def test_explicit_card_value_wins_and_displays(self):
        resolved, dropped = hinted_choices({1: "history"}, pc_sheet())
        self.assertEqual(
            resolved[0]["skillHint"],
            {"skillId": "history", "name": "历史", "value": 40},
        )
        self.assertEqual(dropped, [])
        self.assertNotIn("skillHint", resolved[1])

    def test_rule_base_value_applies_when_card_lacks_the_skill(self):
        # 卡上无显式 history：规则基础值兜底，低基础技能照常可尝试、可提示。
        sheet = pc_sheet()
        del sheet["skills"]["history"]
        resolved, dropped = hinted_choices({1: "history"}, sheet)
        self.assertEqual(dropped, [])
        hint = resolved[0]["skillHint"]
        self.assertEqual(hint["skillId"], "history")
        self.assertEqual(hint["name"], "历史")
        self.assertEqual(hint["value"], rule_skill_catalog()["history"]["base"])

    def test_custom_card_skill_uses_unified_view(self):
        resolved, dropped = hinted_choices({1: "甲骨文辨识"}, pc_sheet())
        self.assertEqual(dropped, [])
        self.assertEqual(
            resolved[0]["skillHint"],
            {"skillId": "甲骨文辨识", "name": "甲骨文辨识", "value": 25},
        )

    def test_chinese_rule_name_alias_resolves_to_skill_id(self):
        resolved, dropped = hinted_choices({1: "历史"}, pc_sheet())
        self.assertEqual(dropped, [])
        self.assertEqual(resolved[0]["skillHint"]["skillId"], "history")
        self.assertEqual(resolved[0]["skillHint"]["value"], 40)

    def test_unknown_skill_drops_only_that_hint(self):
        resolved, dropped = hinted_choices(
            {1: "cthulhu_mythos_forbidden", 2: "spot_hidden"}, pc_sheet()
        )
        self.assertNotIn("skillHint", resolved[0])
        self.assertEqual(resolved[1]["skillHint"]["skillId"], "spot_hidden")
        self.assertEqual(
            dropped, [{"choice_index": 1, "reason": "unknown_skill"}]
        )
        # 诊断不含模型控制原文
        self.assertNotIn("cthulhu_mythos_forbidden", json.dumps(dropped))

    def test_input_choices_are_not_mutated(self):
        choices = [{"label": "研究古文字摹本", "isFree": False}]
        resolved, _ = resolve_skill_hints(choices, {1: "history"}, pc_sheet())
        self.assertNotIn("skillHint", choices[0])
        self.assertIn("skillHint", resolved[0])


class HintProtocolTests(unittest.TestCase):
    def test_malformed_hint_metadata_has_body_free_diagnostics(self):
        raw = json.dumps(story_payload(choice_skill_hints=[{"choice_index": "PRIVATE_SECRET", "skill_id": "history"}]), ensure_ascii=False)
        host = make_stream_host(raw)
        text, _ = run_stream(host)
        self.assertIn("研究古文字摹本", text)
        self.assertEqual(host._turn_choice_hint_dropped, [{"choice_index": 0, "reason": "malformed_metadata", "count": 1}])
        self.assertNotIn("PRIVATE_SECRET", json.dumps(host._turn_choice_hint_dropped))

    def test_output_without_hints_stays_compatible(self):
        raw = story_payload()
        output = DialogueOutput.model_validate(raw)
        self.assertEqual(output.choice_skill_hints, [])
        text = decode_dialogue_output(json.dumps(raw, ensure_ascii=False), present_npc_ids=set())
        choices = extract_action_choices(text)
        self.assertEqual(len(choices), 3)
        self.assertTrue(choices[-1]["isFree"])  # 自由行动无提示合法
        resolved, dropped = resolve_skill_hints(choices, {}, pc_sheet())
        self.assertEqual(dropped, [])
        self.assertTrue(all("skillHint" not in choice for choice in resolved))

    def test_malformed_hints_are_dropped_not_rejected(self):
        raw = story_payload(
            choice_skill_hints=[
                {"choice_index": 1, "skill_id": "history"},
                {"choice_index": "1", "skill_id": "history"},  # 非整数索引
                {"choice_index": 2, "skill_id": 123},  # 非字符串技能
                {"choice_index": 2},  # 缺 skill_id
                "garbage",
                42,
                {"choice_index": 3, "skill_id": ""},  # 空技能键
            ]
        )
        output = DialogueOutput.model_validate(raw)
        self.assertEqual(
            [hint.model_dump() for hint in output.choice_skill_hints],
            [{"choice_index": 1, "skill_id": "history"}],
        )

    def test_out_of_range_duplicate_and_choiceless_hints_drop_with_diagnostics(self):
        output = DialogueOutput.model_validate(
            story_payload(
                choice_skill_hints=[
                    {"choice_index": 1, "skill_id": "history"},
                    {"choice_index": 1, "skill_id": "spot_hidden"},  # 重复
                    {"choice_index": 4, "skill_id": "listen"},  # 越界（只有 3 项）
                    {"choice_index": 0, "skill_id": "listen"},  # 0 越界
                ]
            )
        )
        hint_map, dropped = choice_skill_hint_map(output)
        self.assertEqual(hint_map, {1: "history"})
        self.assertEqual(
            dropped,
            [
                {"choice_index": 1, "reason": "duplicate_hint"},
                {"choice_index": 4, "reason": "index_out_of_range"},
                {"choice_index": 0, "reason": "index_out_of_range"},
            ],
        )

        # choices 为空（菜单改走 narration 兜底）时序号无从对应，全部丢弃。
        choiceless = DialogueOutput.model_validate(
            story_payload(
                choices=[],
                choice_skill_hints=[{"choice_index": 1, "skill_id": "history"}],
            )
        )
        hint_map, dropped = choice_skill_hint_map(choiceless)
        self.assertEqual(hint_map, {})
        self.assertEqual(dropped, [{"choice_index": 1, "reason": "index_out_of_range"}])


def make_stream_host(raw):
    host = streamer_host(None)
    host._structured_dialogue = True
    host.context.world_store = SimpleNamespace(
        load=lambda: {"current_scene": {"npcs_present": []}}
    )
    host.is_valid_npc_id = lambda _value: False
    host.npc_speaker_aliases = lambda: {}
    host.speech = []
    host.cb.on_narrative = lambda text, npc_id=None: host.speech.append((text, npc_id))

    def create(**_kwargs):
        def chunks():
            for i in range(0, len(raw), 7):
                yield build_chunk({"content": raw[i : i + 7]})
            yield build_chunk({"finish_reason": "stop"})

        return chunks()

    host.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    return host


class EmitPipelineTests(unittest.TestCase):
    def test_emit_stashes_hints_and_drops_for_finalize(self):
        raw = json.dumps(
            story_payload(
                choice_skill_hints=[
                    {"choice_index": 1, "skill_id": "history"},
                    {"choice_index": 9, "skill_id": "listen"},
                ]
            ),
            ensure_ascii=False,
        )
        host = make_stream_host(raw)
        text, _ = run_stream(host)
        self.assertIn("研究古文字摹本", text)
        self.assertEqual(host._turn_choice_skill_hints, {1: "history"})
        self.assertEqual(
            host._turn_choice_hint_dropped,
            [{"choice_index": 9, "reason": "index_out_of_range"}],
        )

    def test_output_without_hints_stashes_empty_map(self):
        host = make_stream_host(json.dumps(story_payload(), ensure_ascii=False))
        run_stream(host)
        self.assertEqual(host._turn_choice_skill_hints, {})

    def test_attribution_review_preserves_hints_and_index_alignment(self):
        """归属复核重新分段后：choices 照抄，提示按索引跟随，不串选项。"""
        from src.ai.model.dialogue_attribution import (
            AttributionVerdict,
            validate_review_fix,
        )

        original = DialogueOutput.model_validate(
            story_payload(
                segments=[
                    {
                        "kind": "narration",
                        "text": "伙计擦着盘子。“二位随便看，这盘子是正经货。”",
                    }
                ],
                choice_skill_hints=[{"choice_index": 1, "skill_id": "history"}],
            )
        )
        verdict = AttributionVerdict.model_validate(
            {
                "contains_direct_speech": True,
                "segments": [
                    {"kind": "narration", "text": "伙计擦着盘子。"},
                    {
                        "kind": "speech",
                        "speaker_id": "guest_clerk",
                        "text": "“二位随便看，这盘子是正经货。”",
                    },
                ],
                "guest_speakers": [
                    {"speaker_id": "guest_clerk", "display_name": "伙计"}
                ],
            }
        )
        fixed = validate_review_fix(
            original, verdict, present_npc_ids=set(), guest_speaker_ids=set()
        )
        self.assertEqual(
            [hint.model_dump() for hint in fixed.choice_skill_hints],
            [{"choice_index": 1, "skill_id": "history"}],
        )
        self.assertEqual(fixed.choices, original.choices)
        hint_map, dropped = choice_skill_hint_map(fixed)
        self.assertEqual(hint_map, {1: "history"})
        self.assertEqual(dropped, [])


class TurnRecordRestoreTests(unittest.TestCase):
    def test_journal_roundtrip_preserves_skill_hint(self):
        """定稿回合记录持久化已解析注解；读档公开历史原样带回（不重新解析）。"""
        from src.storage.turn_journal import TurnJournal

        with tempfile.TemporaryDirectory() as temp:
            journal = TurnJournal(
                Path(temp) / "worlds" / "test-world",
                world_id="test-world",
                module_name="test-module",
                owner_token="process-a",
            )
            turn_id = journal.begin(kind="action", player_input="研究古文字摹本")
            journal.complete(
                turn_id,
                messages=[{"role": "assistant", "content": "你辨认出几处笔画。"}],
                world_state={"revision": 1},
                narrative="你辨认出几处笔画。",
                choices=[
                    {
                        "label": "继续比对摹本",
                        "isFree": False,
                        "skillHint": {"skillId": "history", "name": "历史", "value": 40},
                    },
                    {"label": "[自由行动] 你决定做什么？", "isFree": True},
                ],
                diagnostics={"choice_skill_hints": {"dropped": [
                    {"choice_index": 3, "reason": "unknown_skill"}
                ]}},
            )
            history = journal.public_history()
        self.assertEqual(
            history[0]["choices"][0]["skillHint"],
            {"skillId": "history", "name": "历史", "value": 40},
        )
        self.assertNotIn("skillHint", history[0]["choices"][1])

    def test_legacy_saved_message_projection_has_no_hints(self):
        """旧字符串 choices（无存档注解）正常渲染：projection 不臆造提示。"""
        from src.web.local_save_restore import _saved_history_projection  # noqa: F401

        choices = extract_action_choices(
            "存档中的大门仍然紧闭。\n\n**你可以——**\n1. 敲门\n2. [自由行动] 你决定做什么？"
        )
        self.assertEqual([c["label"] for c in choices], ["敲门", "[自由行动] 你决定做什么？"])
        self.assertTrue(all("skillHint" not in c for c in choices))


if __name__ == "__main__":
    unittest.main()
