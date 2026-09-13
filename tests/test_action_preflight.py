import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.app.engine import GameEngine
from src.gameplay.action_preflight import match_action_preview, upgrade_legacy_advisories
from src.gameplay.action_resolution import plan_player_action
from src.gameplay.transition_prelude import upgrade_legacy_entry_beats
from src.storage.world_store import WorldStore


def make_world() -> dict:
    return {
        "pc": {
            "name": "黄千陆",
            "backstory": {
                "beliefs": "以头脑解决问题",
                "violence_stance": "avoidant",
            },
        },
        "current_scene": {
            "id": "office",
            "name": "法伦主任办公室",
            "npcs_present": ["bryce_fallon"],
        },
        "npcs": [
            {
                "id": "bryce_fallon",
                "name": "布莱斯·法伦",
                "disposition": "cooperative",
            }
        ],
    }


def witch_route_world(*, occult: int = 5, traits: str = "坚定的唯物主义者") -> dict:
    return {
        "pc": {
            "occupation": "警方顾问",
            "skills": {"occult": occult},
            "backstory": {"traits": traits},
        },
        "flags": {},
        "current_scene": {
            "id": "inn",
            "name": "旅店",
            "npcs_present": ["john"],
        },
        "scene_catalog": {
            "inn": {
                "id": "inn",
                "name": "旅店",
                "npcs_present": ["john"],
                "action_advisories": [
                    {
                        "id": "witch_social_warning",
                        "destination_scene_id": "witch_hut",
                        "trigger_if": {
                            "skill_below": {"occult": 40},
                            "traits_contain_any": ["唯物主义"],
                        },
                        "npc_id": "john",
                        "npc_text": "那位女士不喜欢只相信眼前事实的调查者。",
                        "keeper_text": "镇上的传闻说，那位女士不喜欢侦探。",
                        "public_hint": "『这项行动可能受益于“神秘学”或社交类技能。』",
                        "continue_label": "仍然去拜访女巫",
                        "prepare_options": [
                            {
                                "id": "ask_customs",
                                "label": "先询问当地礼节",
                                "action_text": "我先问约翰，拜访她时应该注意什么？",
                            }
                        ],
                        "cancel_label": "暂时不去",
                    }
                ],
            },
            "witch_hut": {
                "id": "witch_hut",
                "name": "女巫居所",
                "aliases": ["女巫那里"],
                "npcs_present": ["witch"],
            },
        },
        "npcs": [
            {"id": "john", "name": "约翰", "secret": "约翰其实为女巫工作"},
            {"id": "witch", "name": "女巫", "secret": "仪式的真正代价"},
        ],
        "private_memory": {"hidden_facts": {"witch": "仪式的真正代价"}},
    }


class ActionPreviewPlanningTests(unittest.TestCase):
    def test_low_skill_or_conflicting_trait_triggers_public_chat_preview(self):
        world = witch_route_world()
        action = plan_player_action("我去女巫那里调查。", world)

        preview = match_action_preview(action, world)

        self.assertIsNotNone(preview)
        assert preview is not None
        self.assertIn("【npc:john】", preview.narrative)
        self.assertIn("神秘学", preview.narrative)
        payload = preview.decision_payload()
        self.assertEqual(payload["presentation"], "chat")
        self.assertEqual(payload["kind"], "action_preview")
        self.assertEqual(payload["default_option"], "cancel_action")
        self.assertEqual(
            [option.id for option in preview.options],
            ["continue_action", "prepare_ask_customs", "cancel_action"],
        )

        public_surface = preview.narrative + json.dumps(
            {
                "title": payload["title"],
                "description": payload["description"],
                "options": payload["options"],
            },
            ensure_ascii=False,
        )
        self.assertNotIn("40", public_surface)
        self.assertNotIn("约翰其实为女巫工作", public_surface)
        self.assertNotIn("仪式的真正代价", public_surface)

    def test_sufficient_skill_without_conflicting_trait_skips_preview(self):
        world = witch_route_world(occult=60, traits="谨慎而尊重地方习俗")
        action = plan_player_action("我去女巫那里调查。", world)

        self.assertIsNone(match_action_preview(action, world))

    def test_missing_npc_falls_back_to_keeper_warning(self):
        world = witch_route_world()
        world["current_scene"]["npcs_present"] = []
        action = plan_player_action("我去女巫那里调查。", world)

        preview = match_action_preview(action, world)

        self.assertIsNotNone(preview)
        assert preview is not None
        self.assertIsNone(preview.npc_id)
        self.assertNotIn("【npc:", preview.narrative)
        self.assertIn("镇上的传闻", preview.narrative)


class NonBlockingTransitionTests(unittest.TestCase):
    """非阻塞提醒只能是过渡节拍，绝不能复用决策卡文案。"""

    def _world(self, *, npc_present: bool = True) -> dict:
        world = witch_route_world()
        if not npc_present:
            world["current_scene"]["npcs_present"] = []
        advisory = world["scene_catalog"]["inn"]["action_advisories"][0]
        advisory["blocking"] = False
        advisory["transition_text"] = (
            "【npc:john】去吧，她已经知道你要来。【/npc】约翰替你收拾好行装。"
        )
        advisory["npc_text"] = "你也许愿意再想想。"
        advisory["public_hint"] = "『你可以立即前往，也可以先问问约翰。』"
        return world

    def test_non_blocking_advisory_is_a_beat_without_a_card(self):
        world = self._world()
        action = plan_player_action("我去女巫那里调查。", world)

        preview = match_action_preview(action, world)

        self.assertIsNotNone(preview)
        assert preview is not None
        self.assertFalse(preview.blocking)
        self.assertEqual(
            preview.transition_text,
            world["scene_catalog"]["inn"]["action_advisories"][0]["transition_text"],
        )
        self.assertEqual(preview.narrative, preview.transition_text)
        self.assertEqual(preview.options, ())
        self.assertEqual(preview.default_option, "")
        # 卡片文案（劝留、分支提示）一律不得进入过渡素材。
        self.assertNotIn("你也许愿意再想想", preview.narrative)
        self.assertNotIn("你可以立即前往", preview.narrative)

    def test_legacy_non_blocking_advisory_without_beat_is_skipped(self):
        """迁移前的非阻塞条目只有卡片文案：宁可不提醒，也不回灌成出发素材。"""

        world = self._world()
        advisory = world["scene_catalog"]["inn"]["action_advisories"][0]
        advisory.pop("transition_text")
        action = plan_player_action("我去女巫那里调查。", world)

        self.assertIsNone(match_action_preview(action, world))

    def test_absent_npc_uses_keeper_fallback_beat(self):
        world = self._world(npc_present=False)
        action = plan_player_action("我去女巫那里调查。", world)

        preview = match_action_preview(action, world)

        self.assertIsNotNone(preview)
        assert preview is not None
        self.assertFalse(preview.blocking)
        self.assertIsNone(preview.npc_id)
        self.assertIn("镇上的传闻", preview.transition_text)
        self.assertNotIn("【npc:", preview.transition_text)

    def test_blocking_advisory_keeps_card_semantics(self):
        world = self._world()
        advisory = world["scene_catalog"]["inn"]["action_advisories"][0]
        advisory["blocking"] = True
        action = plan_player_action("我去女巫那里调查。", world)

        preview = match_action_preview(action, world)

        self.assertIsNotNone(preview)
        assert preview is not None
        self.assertTrue(preview.blocking)
        self.assertEqual(preview.transition_text, "")
        self.assertIn("你也许愿意再想想", preview.narrative)
        self.assertEqual(
            [option.id for option in preview.options],
            ["continue_action", "prepare_ask_customs", "cancel_action"],
        )


class LegacyAdvisoryUpgradeTests(unittest.TestCase):
    """旧快照的兼容升级：只升级与作者声明旧载荷逐字一致的条目，可重复执行。"""

    LEGACY_HANDOFF = {
        "id": "handoff",
        "blocking": False,
        "npc_text": "你也许愿意先听听我对这件事的看法。",
        "public_hint": "『你可以立即前往，也可以先了解情况。』",
    }

    def _template(self) -> dict:
        return {
            "scene_catalog": {
                "university": {
                    "action_advisories": [
                        {
                            "id": "handoff",
                            "blocking": False,
                            "transition_text": "法伦替你拨通了医学院的内线。",
                            "supersedes": [dict(self.LEGACY_HANDOFF)],
                        },
                        {
                            "id": "gate",
                            "blocking": True,
                            "npc_text": "现在进去很危险。",
                        },
                    ]
                }
            }
        }

    def _legacy_state(self) -> dict:
        return {
            "scene_catalog": {
                "university": {
                    "action_advisories": [
                        dict(self.LEGACY_HANDOFF),
                        {
                            "id": "gate",
                            "blocking": True,
                            "npc_text": "旧版本的守门文案。",
                        },
                        {
                            "id": "hand_written",
                            "blocking": False,
                            "transition_text": "使用者自写的过渡节拍。",
                        },
                    ]
                }
            }
        }

    def test_only_the_declared_legacy_payload_is_upgraded(self):
        state = self._legacy_state()

        upgraded = upgrade_legacy_advisories(state, self._template())

        self.assertEqual(upgraded, ["university/handoff"])
        advisories = {
            item["id"]: item for item in state["scene_catalog"]["university"]["action_advisories"]
        }
        self.assertIn("transition_text", advisories["handoff"])
        self.assertNotIn("npc_text", advisories["handoff"])
        # 迁移记录不进入世界状态，保持世界副本干净。
        self.assertNotIn("supersedes", advisories["handoff"])
        self.assertEqual(advisories["gate"]["npc_text"], "旧版本的守门文案。")
        self.assertEqual(advisories["hand_written"]["transition_text"], "使用者自写的过渡节拍。")

    def test_edited_legacy_entries_are_never_overwritten(self):
        """对偶用例：同名、非阻塞、无 transition_text，但用户改过文案 → 不升级。"""
        for field, value in (
            ("npc_text", "（用户改写）你也许愿意先听听我对这件事的看法。"),
            ("keeper_text", "（用户自写的守秘人提醒）"),
            ("public_hint", "（用户自写的公开提示）"),
            ("title", "（用户改写的标题）"),
        ):
            with self.subTest(field=field):
                state = self._legacy_state()
                advisory = state["scene_catalog"]["university"]["action_advisories"][0]
                advisory[field] = value

                self.assertEqual(upgrade_legacy_advisories(state, self._template()), [])
                self.assertEqual(
                    state["scene_catalog"]["university"]["action_advisories"][0],
                    advisory,
                )

        # 增删字段同样视为改写
        extra = self._legacy_state()
        extra["scene_catalog"]["university"]["action_advisories"][0]["cancel_label"] = "先不走"
        self.assertEqual(upgrade_legacy_advisories(extra, self._template()), [])

    def test_template_without_declared_payload_upgrades_nothing(self):
        state = self._legacy_state()
        template = self._template()
        template["scene_catalog"]["university"]["action_advisories"][0].pop("supersedes")

        self.assertEqual(upgrade_legacy_advisories(state, template), [])
        self.assertIn(
            "npc_text",
            state["scene_catalog"]["university"]["action_advisories"][0],
        )

    def test_upgrade_is_idempotent_and_skips_absent_template_entries(self):
        state = self._legacy_state()
        template = self._template()

        self.assertEqual(upgrade_legacy_advisories(state, template), ["university/handoff"])
        self.assertEqual(upgrade_legacy_advisories(state, template), [])

        template["scene_catalog"]["university"]["action_advisories"] = [
            item
            for item in template["scene_catalog"]["university"]["action_advisories"]
            if item["id"] != "gate"
        ]
        self.assertEqual(upgrade_legacy_advisories(state, template), [])
        self.assertEqual(
            state["scene_catalog"]["university"]["action_advisories"][1]["npc_text"],
            "旧版本的守门文案。",
        )

    def test_upgrade_ignores_scenes_the_world_does_not_have(self):
        state = {"scene_catalog": {"tavern": {"action_advisories": []}}}

        self.assertEqual(upgrade_legacy_advisories(state, self._template()), [])
        self.assertEqual(state["scene_catalog"], {"tavern": {"action_advisories": []}})

    def test_official_scarlet_payload_still_matches_after_the_module_edit(self):
        """真实模组数据：事故快照的旧条目必须在 supersedes 里逐字命中。"""
        from src.app.config import PROJECT_ROOT

        module = json.loads(
            (PROJECT_ROOT / "mod/猩红文档/world_state_initial.json").read_text(encoding="utf-8")
        )
        stored = module["scene_catalog"]["miskatonic_university"]["action_advisories"][0]
        legacy = dict(stored["supersedes"][0])
        self.assertNotIn("transition_text", legacy)
        self.assertIn("你也许愿意先听听我对这件事的看法", legacy["npc_text"])

        state = {"scene_catalog": {"miskatonic_university": {"action_advisories": [legacy]}}}

        self.assertEqual(
            upgrade_legacy_advisories(state, module),
            ["miskatonic_university/wright_body_handoff"],
        )
        migrated = state["scene_catalog"]["miskatonic_university"]["action_advisories"][0]
        self.assertEqual(migrated["transition_text"], stored["transition_text"])


class LegacyEntryBeatUpgradeTests(unittest.TestCase):
    """入场节拍的旧措辞升级：同样只认作者声明的逐字旧载荷，手改一律保留。"""

    LEGACY_BEAT = {
        "npc_id": "doctor",
        "public_text": "冷柜间门口，医生正攥着病历夹等候。",
    }

    def _template(self) -> dict:
        return {
            "scene_catalog": {
                "medical": {
                    "entry_beat": {
                        "npc_id": "doctor",
                        "public_text": "冷柜间门口，医生攥着病历夹站在那里。",
                        "supersedes": [dict(self.LEGACY_BEAT)],
                    }
                }
            }
        }

    def _legacy_state(self) -> dict:
        return {"scene_catalog": {"medical": {"entry_beat": dict(self.LEGACY_BEAT)}}}

    def test_only_the_declared_legacy_payload_is_upgraded(self):
        state = self._legacy_state()

        upgraded = upgrade_legacy_entry_beats(state, self._template())

        self.assertEqual(upgraded, ["medical/entry_beat"])
        beat = state["scene_catalog"]["medical"]["entry_beat"]
        self.assertEqual(beat["public_text"], "冷柜间门口，医生攥着病历夹站在那里。")
        # 迁移记录不进入世界状态，保持世界副本干净。
        self.assertNotIn("supersedes", beat)

    def test_edited_legacy_beats_are_never_overwritten(self):
        """对偶用例：改写措辞、改 NPC、增删字段都算手改 → 不升级。"""
        for mutate in (
            lambda beat: beat.update(public_text="（用户改写）医生正攥着病历夹等候。"),
            lambda beat: beat.update(npc_id="nurse"),
            lambda beat: beat.update(note="用户自注"),
            lambda beat: beat.pop("npc_id"),
        ):
            with self.subTest(mutate=mutate):
                state = self._legacy_state()
                beat = state["scene_catalog"]["medical"]["entry_beat"]
                mutate(beat)

                self.assertEqual(upgrade_legacy_entry_beats(state, self._template()), [])
                self.assertEqual(
                    state["scene_catalog"]["medical"]["entry_beat"],
                    beat,
                )

    def test_template_without_declared_payload_upgrades_nothing(self):
        state = self._legacy_state()
        template = self._template()
        template["scene_catalog"]["medical"]["entry_beat"].pop("supersedes")

        self.assertEqual(upgrade_legacy_entry_beats(state, template), [])
        self.assertEqual(
            state["scene_catalog"]["medical"]["entry_beat"]["public_text"],
            "冷柜间门口，医生正攥着病历夹等候。",
        )

    def test_upgrade_is_idempotent_and_ignores_scenes_without_template_beat(self):
        state = self._legacy_state()
        template = self._template()

        self.assertEqual(upgrade_legacy_entry_beats(state, template), ["medical/entry_beat"])
        self.assertEqual(upgrade_legacy_entry_beats(state, template), [])

        state["scene_catalog"]["tavern"] = {"entry_beat": dict(self.LEGACY_BEAT)}
        self.assertEqual(upgrade_legacy_entry_beats(state, template), [])
        self.assertEqual(
            state["scene_catalog"]["tavern"]["entry_beat"],
            self.LEGACY_BEAT,
        )

    def test_official_scarlet_payload_still_matches_after_the_module_edit(self):
        """真实模组数据：事故快照的旧入场节拍必须在 supersedes 里逐字命中。"""
        from src.app.config import PROJECT_ROOT

        module = json.loads(
            (PROJECT_ROOT / "mod/猩红文档/world_state_initial.json").read_text(encoding="utf-8")
        )
        stored = module["scene_catalog"]["miskatonic_medical"]["entry_beat"]
        legacy = dict(stored["supersedes"][0])
        self.assertIn("等候", legacy["public_text"])

        state = {"scene_catalog": {"miskatonic_medical": {"entry_beat": legacy}}}

        self.assertEqual(
            upgrade_legacy_entry_beats(state, module),
            ["miskatonic_medical/entry_beat"],
        )
        migrated = state["scene_catalog"]["miskatonic_medical"]["entry_beat"]
        self.assertEqual(migrated["public_text"], stored["public_text"])
        self.assertNotIn("等候", migrated["public_text"])
        self.assertNotIn("supersedes", migrated)


class FakeGraph:
    def __init__(self, events: list[str]):
        self.events = events
        self.inputs = []

    def invoke(self, state, config=None):
        self.events.append("graph")
        self.inputs.append((state, config))


class ActionPreflightTests(unittest.TestCase):
    def _engine(
        self,
        selected: str,
        events: list[str],
        store: WorldStore | None = None,
    ) -> GameEngine:
        engine = GameEngine.__new__(GameEngine)
        if store is not None:
            engine.context = SimpleNamespace(world_store=store)

        def on_decision(_decision):
            engine._test_decision = _decision
            events.append("decision")
            return selected

        engine.cb = SimpleNamespace(
            on_decision=on_decision,
            on_narrative=lambda _text, _npc_id=None: events.append("narrative"),
            on_done=lambda: events.append("done"),
        )
        engine.messages = []
        engine._preconfirmed_escalation = None
        engine._resume_pending_combat_decision = lambda: None
        engine._turn_graph = FakeGraph(events)
        engine.save = lambda _slot: events.append("save")
        return engine

    def test_confirmation_happens_before_graph_and_first_model_token(self):
        events: list[str] = []

        with tempfile.TemporaryDirectory() as temp_dir:
            store = WorldStore(Path(temp_dir) / "world")
            store.initialize(make_world())
            engine = self._engine("confirm_violence", events, store)
            engine.handle_action("朝着法伦开枪")

        self.assertEqual(events, ["narrative", "decision", "graph"])
        self.assertEqual(engine._test_decision["presentation"], "chat")
        submitted = engine._turn_graph.inputs[0][0]["user_content"]
        self.assertIn("玩家已在叙事开始前确认", submitted)

    def test_cancelling_preflight_never_starts_gm_graph(self):
        events: list[str] = []

        with tempfile.TemporaryDirectory() as temp_dir:
            store = WorldStore(Path(temp_dir) / "world")
            store.initialize(make_world())
            engine = self._engine("cancel_violence", events, store)
            engine.handle_action("朝着法伦开枪")

        self.assertEqual(events, ["narrative", "decision", "save", "done"])
        self.assertEqual(engine._turn_graph.inputs, [])
        self.assertIn("行动发生前取消", engine.messages[-1]["content"])

    def test_conversation_about_a_death_reaches_gm_without_confirmation(self):
        events: list[str] = []
        content = (
            "你是说，莱特教授的死很有可能和巫术有关？法伦先生，我来自遥远的东方，"
            "也从来没有听说过这样神奇的巫术。能够通过一个文档将人杀死。"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            store = WorldStore(Path(temp_dir) / "world")
            store.initialize(make_world())
            engine = self._engine("cancel_violence", events, store)
            engine.handle_action(content)

        self.assertEqual(events, ["graph"])
        submitted = engine._turn_graph.inputs[0][0]["user_content"]
        self.assertEqual(submitted, content)

    def test_matching_tool_confirmation_consumes_one_time_authorization(self):
        engine = self._engine("confirm_violence", [])
        engine._preconfirmed_escalation = {
            "kind": "irreversible_violence",
            "target_id": "bryce_fallon",
            "confirm_option": "confirm_violence",
        }

        selected = engine._preconfirmed_option(
            {
                "kind": "irreversible_violence",
                "target_id": "bryce_fallon",
            }
        )

        self.assertEqual(selected, "confirm_violence")
        self.assertIsNone(engine._preconfirmed_escalation)


if __name__ == "__main__":
    unittest.main()
