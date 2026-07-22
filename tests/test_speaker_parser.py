import unittest

from src.speaker_parser import SpeakerStreamParser, parse_segments


def npc_ok(npc_id: str) -> bool:
    return npc_id in {"bryce_fallon", "butler_gregory"}


class SpeakerParserTests(unittest.TestCase):
    def feed_all(self, chunks, is_valid=npc_ok):
        parser = SpeakerStreamParser(is_valid_npc=is_valid)
        pieces = []
        for chunk in chunks:
            pieces.extend(parser.feed(chunk))
        pieces.extend(parser.flush())
        return pieces

    def test_plain_narration_has_no_speech(self):
        segments, clean = parse_segments("雨水顺着窗户滑落。他看了一眼抽屉。")
        self.assertEqual(clean, "雨水顺着窗户滑落。他看了一眼抽屉。")
        self.assertEqual([s.kind for s in segments], ["narration"])

    def test_known_name_prefix_recovers_speech_without_model_tag(self):
        segments, clean = parse_segments(
            "冷风掠过窗沿。\n法伦：黄先生，我需要你查明真相。\n他把文件推过桌面。",
            is_valid_npc=npc_ok,
            speaker_aliases={"法伦": "bryce_fallon"},
        )

        self.assertEqual(clean, "冷风掠过窗沿。\n法伦：黄先生，我需要你查明真相。\n他把文件推过桌面。")
        self.assertEqual(
            [(segment.kind, segment.npc_id) for segment in segments],
            [("narration", None), ("speech", "bryce_fallon"), ("narration", None)],
        )
        self.assertEqual(segments[1].text, "黄先生，我需要你查明真相。")

    def test_unknown_name_prefix_stays_narration(self):
        segments, _ = parse_segments(
            "线索：桌面留有墨迹。\n陌生人：不应被伪造成已知 NPC。",
            speaker_aliases={"法伦": "bryce_fallon"},
        )

        self.assertEqual([(segment.kind, segment.npc_id) for segment in segments], [("narration", None)])

    def test_novel_dialogue_with_trailing_known_speaker_is_recovered(self):
        text = (
            "你推门进去时，他正站在窗边。\n"
            "“黄先生，感谢你冒雨前来。”法伦示意你坐下，自己绕过办公桌。\n"
            "他沉默了几秒。"
        )
        segments, _ = parse_segments(
            text,
            speaker_aliases={"法伦": "bryce_fallon"},
        )

        self.assertEqual(
            [(segment.kind, segment.npc_id) for segment in segments],
            [
                ("narration", None),
                ("speech", "bryce_fallon"),
                ("narration", None),
            ],
        )
        self.assertEqual(segments[1].text, "“黄先生，感谢你冒雨前来。”")
        self.assertIn("法伦示意你坐下", segments[2].text)

    def test_novel_dialogue_with_leading_known_speaker_is_recovered(self):
        segments, _ = parse_segments(
            "法伦望向窗外，低声说：“莱特并没有告诉我全部真相。”",
            speaker_aliases={"法伦": "bryce_fallon"},
        )

        self.assertEqual(
            [(segment.kind, segment.npc_id) for segment in segments],
            [("narration", None), ("speech", "bryce_fallon")],
        )
        self.assertEqual(segments[1].text, "“莱特并没有告诉我全部真相。”")

    def test_unattributed_quotation_remains_keeper_narration(self):
        segments, _ = parse_segments(
            "档案首页写着：“此件不得外借。”",
            speaker_aliases={"法伦": "bryce_fallon"},
        )

        self.assertEqual([(segment.kind, segment.npc_id) for segment in segments], [("narration", None)])

    def test_short_alias_and_active_speaker_recover_opening_dialogue(self):
        text = (
            "布莱斯·法伦站在窗边，直到门关上才转过身。\n\n"
            "“黄先生，请坐。”他示意你坐进扶手椅。\n\n"
            "“但我并不认为事情这么简单。”法伦说这句话时没收起笑容。\n\n"
            "“查清楚莱特到底是怎么死的。尽快，也尽量别声张。”他顿了顿。\n\n"
            "“他不是第一个死在这件事上的人。我不希望再有人跟着沉默了。”"
        )
        segments, _ = parse_segments(
            text,
            speaker_aliases={
                "布莱斯·法伦": "bryce_fallon",
                "法伦": "bryce_fallon",
            },
        )

        speeches = [segment for segment in segments if segment.kind == "speech"]
        self.assertEqual(len(speeches), 4)
        self.assertTrue(all(segment.npc_id == "bryce_fallon" for segment in speeches))
        self.assertIn("黄先生，请坐", speeches[0].text)
        self.assertIn("不希望再有人", speeches[-1].text)

    def test_short_quoted_terms_stay_inside_narration(self):
        segments, _ = parse_segments(
            "布莱斯·法伦解释，档案里写作“柯布家族”，校方称其为“意外”。",
            speaker_aliases={"布莱斯·法伦": "bryce_fallon", "法伦": "bryce_fallon"},
        )

        self.assertEqual([(segment.kind, segment.npc_id) for segment in segments], [("narration", None)])

    def test_speech_tag_produces_speech_segment(self):
        text = '雨还在下。【npc:bryce_fallon】"莱特生前一直在隐瞒什么。"【/npc】他说完看向抽屉。'
        segments, clean = parse_segments(text, is_valid_npc=npc_ok)
        self.assertEqual(clean, '雨还在下。"莱特生前一直在隐瞒什么。"他说完看向抽屉。')
        self.assertEqual([s.kind for s in segments], ["narration", "speech", "narration"])
        self.assertEqual(segments[1].npc_id, "bryce_fallon")
        self.assertIn("隐瞒", segments[1].text)

    def test_ascii_tags_are_accepted_and_never_leak(self):
        text = '[npc:bryce_fallon]"门打开以后，空气里全是墨水味。"[/npc]'
        segments, clean = parse_segments(text, is_valid_npc=npc_ok)
        self.assertEqual([s.kind for s in segments], ["speech"])
        self.assertEqual(segments[0].npc_id, "bryce_fallon")
        self.assertNotIn("[npc:", clean)
        self.assertNotIn("[/npc]", clean)

    def test_ascii_tag_split_across_deltas(self):
        pieces = self.feed_all(
            ["[n", "pc:bryce_fa", "llon]", "\"台词\"", "[/n", "pc]"]
        )
        self.assertEqual(pieces[0][0], "speech_start")
        self.assertIn("speech_end", [kind for kind, _, _ in pieces])

    def test_multiple_speakers_split_into_units(self):
        text = (
            "【npc:bryce_fallon】「第一句。」【/npc】"
            "【npc:butler_gregory】「第二句。」【/npc】"
        )
        segments, _ = parse_segments(text, is_valid_npc=npc_ok)
        self.assertEqual([s.npc_id for s in segments], ["bryce_fallon", "butler_gregory"])

    def test_unknown_npc_id_falls_back_to_narration(self):
        calls = []
        segments, clean = parse_segments(
            '【npc:ghost】"不存在的人。"【/npc】',
            is_valid_npc=npc_ok,
            on_unknown_npc=calls.append,
        )
        self.assertEqual(calls, ["ghost"])
        self.assertEqual([s.kind for s in segments], ["narration"])
        self.assertNotIn("【npc:", clean)

    def test_unclosed_speech_is_closed_at_end_of_stream(self):
        segments, clean = parse_segments('开场。【npc:bryce_fallon】"没说完的话。', is_valid_npc=npc_ok)
        self.assertEqual([s.kind for s in segments], ["narration", "speech"])
        self.assertEqual(segments[1].npc_id, "bryce_fallon")
        self.assertNotIn("【npc:", clean)
        self.assertIn("没说完的话", clean)

    def test_close_tag_with_id_is_accepted_and_stripped(self):
        # 生产实测：模型会写 【/npc:butler_gregory】 作为闭标签
        text = '开场。【npc:butler_gregory】"请您把它收起来吧。"【/npc:butler_gregory】他紧了紧双手。'
        segments, clean = parse_segments(text, is_valid_npc=npc_ok)
        self.assertNotIn("/npc", clean)
        self.assertEqual([s.kind for s in segments], ["narration", "speech", "narration"])
        self.assertEqual(segments[1].npc_id, "butler_gregory")
        self.assertIn("紧了紧双手", segments[2].text)

    def test_open_only_markers_end_at_paragraph_or_next_speaker_marker(self):
        text = (
            "【npc:bryce_fallon】「第一句。」\n\n他说完看向窗外。\n\n"
            "【npc:bryce_fallon】「第二句。」\n\n"
            "【npc:butler_gregory】「第三句。」"
        )
        segments, clean = parse_segments(text, is_valid_npc=npc_ok)
        self.assertEqual(
            [(segment.kind, segment.npc_id) for segment in segments],
            [
                ("speech", "bryce_fallon"),
                ("narration", None),
                ("speech", "bryce_fallon"),
                ("speech", "butler_gregory"),
            ],
        )
        self.assertNotIn("npc:", clean)

    def test_legacy_mismatched_right_bracket_is_accepted(self):
        segments, clean = parse_segments(
            '【npc:bryce_fallon⟧"旧格式台词。"【/npc】',
            is_valid_npc=npc_ok,
        )
        self.assertEqual([(s.kind, s.npc_id) for s in segments], [("speech", "bryce_fallon")])
        self.assertNotIn("npc:", clean)

    def test_stray_close_tag_is_stripped(self):
        segments, clean = parse_segments("叙述【/npc】继续。", is_valid_npc=npc_ok)
        self.assertEqual(clean, "叙述继续。")
        self.assertEqual([s.kind for s in segments], ["narration"])

    def test_literal_bracket_in_prose_passes_through(self):
        segments, clean = parse_segments("他写下【注】这个符号。", is_valid_npc=npc_ok)
        self.assertEqual(clean, "他写下【注】这个符号。")
        self.assertEqual([s.kind for s in segments], ["narration"])

    def test_incremental_matches_finalize_across_chunk_boundaries(self):
        text = '第一段。【npc:bryce_fallon】"跨块台词。"【/npc】第二段。【npc:butler_gregory】"二句。"【/npc】结尾。'
        expected_segments, expected_clean = parse_segments(text, is_valid_npc=npc_ok)
        # 以各种粒度切分喂入，结果必须一致
        for step in (1, 2, 3, 7, 13, 64):
            chunks = [text[i : i + step] for i in range(0, len(text), step)]
            pieces = self.feed_all(chunks)
            clean = "".join(t for kind, t, _ in pieces if kind == "text")
            self.assertEqual(clean, expected_clean, f"chunk size {step}")
            from src.speaker_parser import pieces_to_segments

            segments = pieces_to_segments(pieces)
            self.assertEqual(
                [(s.kind, s.npc_id) for s in segments],
                [(s.kind, s.npc_id) for s in expected_segments],
                f"chunk size {step}",
            )

    def test_tag_split_across_many_deltas(self):
        chunks = ["【n", "pc:bryce_fa", "llon】", "\"台词\"", "【/n", "pc】"]
        pieces = self.feed_all(chunks)
        kinds = [k for k, _, _ in pieces]
        self.assertEqual(kinds[0], "speech_start")
        self.assertIn("speech_end", kinds)

    def test_empty_and_none_inputs(self):
        parser = SpeakerStreamParser()
        self.assertEqual(parser.feed(""), [])
        segments, clean = parse_segments("")
        self.assertEqual(segments, [])
        self.assertEqual(clean, "")


if __name__ == "__main__":
    unittest.main()
