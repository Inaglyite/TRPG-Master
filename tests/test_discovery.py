import json
import tempfile
import unittest
from pathlib import Path

from src.app.agent_graph import _parse_final_narrative, _prepare_turn
from src.app.config import PROJECT_ROOT
from src.app.engine import EngineCallbacks, GameEngine
from src.app.runtime import RuntimeContext
from src.gameplay.action_resolution import ActionPhase, plan_player_action
from src.gameplay.discovery import (
    match_discovery_rules,
    preferred_check_skill,
    preferred_luck_difficulty,
)
from src.gameplay.encounters import resolve_scene_encounters


def make_morgue_preview_blocking(context) -> None:
    """模组默认的停尸房预演是非阻塞提醒；测试卡牌流程时显式翻回阻塞。"""

    def mutate(world: dict) -> None:
        advisory = world["scene_catalog"]["miskatonic_university"]["action_advisories"][0]
        advisory["blocking"] = True

    context.world_store.update(mutate)


def discovery_world() -> dict:
    return {
        "pc": {"skills": {"spot_hidden": 70}},
        "current_scene": {"id": "morgue"},
        "clues_found": {"investigation": []},
        "clue_catalog": {
            "body": {
                "id": "body",
                "source": "morgue",
                "related_scenes": ["morgue"],
                "discovery_rules": [
                    {
                        "intent": "examine",
                        "targets": ["教授遗体", "尸体"],
                        "skill": "spot_hidden",
                        "requires_success": True,
                    }
                ],
            },
        },
    }


class DiscoveryMatchingTests(unittest.TestCase):
    def test_declarative_encounter_supports_luck_and_absence(self):
        world = {
            "flags": {"campus_open": True},
            "npcs": [
                {
                    "id": "professor",
                    "current_location": "office",
                }
            ],
            "scene_catalog": {
                "office": {
                    "encounters": [
                        {
                            "id": "professor_after_hours",
                            "npc_id": "professor",
                            "availability": "luck",
                            "required_flags": {"campus_open": True},
                            "on_present_text": "教授恰好还在办公室。",
                            "on_absent_text": "办公室已经空了。",
                        }
                    ],
                },
            },
        }

        absent = resolve_scene_encounters(
            "office",
            world,
            luck_check=lambda _difficulty: {
                "success": False,
                "d100_roll": 88,
                "skill_value": 60,
            },
        )
        self.assertEqual(absent.present_npc_ids, ())
        self.assertEqual(absent.narrative_text, "办公室已经空了。")

        present = resolve_scene_encounters(
            "office",
            world,
            luck_check=lambda _difficulty: {
                "success": True,
                "d100_roll": 12,
                "skill_value": 60,
            },
        )
        self.assertEqual(present.present_npc_ids, ("professor",))
        self.assertEqual(present.narrative_text, "教授恰好还在办公室。")

        world["encounter_history"] = {
            "office": {
                "professor_after_hours": {
                    "present": False,
                    "check_result": {"d100_roll": 88, "skill_value": 60},
                },
            },
        }
        rerolls = 0

        def unexpected_roll(_difficulty: str) -> dict:
            nonlocal rerolls
            rerolls += 1
            return {"d100_roll": 1, "skill_value": 60, "success": True}

        cached = resolve_scene_encounters("office", world, luck_check=unexpected_roll)
        self.assertEqual(cached.present_npc_ids, ())
        self.assertTrue(cached.outcomes[0].cached)
        self.assertEqual(rerolls, 0)

    def test_scarlet_opening_does_not_pregrant_document_contacts(self):
        world = json.loads(
            (PROJECT_ROOT / "mod" / "猩红文档" / "world_state_initial.json").read_text(
                encoding="utf-8"
            )
        )
        initial_text = " ".join(
            str(clue.get("text") or "") for clues in world["clues_found"].values() for clue in clues
        )
        self.assertNotIn("哈兰德·洛奇", initial_text)
        self.assertNotIn("艾米莉亚·考特", initial_text)

        matches = match_discovery_rules(
            "我问法伦：还有谁参与文档评估？",
            world,
        )
        self.assertEqual(
            [match.clue_id for match in matches],
            ["fallon_document_contacts"],
        )

    def test_scarlet_history_department_is_an_authoritative_scene(self):
        world = json.loads(
            (PROJECT_ROOT / "mod" / "猩红文档" / "world_state_initial.json").read_text(
                encoding="utf-8"
            )
        )
        world["current_scene"] = world["scene_catalog"]["miskatonic_medical"]

        action = plan_player_action("立刻前往历史系找艾米莉亚·考特。", world)

        self.assertEqual(action.phase, ActionPhase.ARRIVAL)
        self.assertEqual(action.destination_scene_id, "miskatonic_history")
        self.assertEqual(
            world["scene_catalog"]["miskatonic_history"]["npcs_present"],
            ["emilia_court"],
        )

        by_name = plan_player_action("我去找考特谈谈。", world)
        self.assertEqual(by_name.destination_scene_id, "miskatonic_history")

        lodge = plan_player_action("接下来去找哈兰德·洛奇。", world)
        self.assertEqual(lodge.destination_scene_id, "miskatonic_lodge_office")

        # Old saves may retain a broad university location.  Authored scene
        # presence is the stronger navigation authority.
        next(npc for npc in world["npcs"] if npc["id"] == "harland_lodge")["current_location"] = (
            "miskatonic_university"
        )
        old_save_lodge = plan_player_action("去东翼二层找哈兰德·洛奇。", world)
        self.assertEqual(
            old_save_lodge.destination_scene_id,
            "miskatonic_lodge_office",
        )

    def test_scarlet_authored_targets_route_before_any_discovery_effect(self):
        world = json.loads(
            (PROJECT_ROOT / "mod" / "猩红文档" / "world_state_initial.json").read_text(
                encoding="utf-8"
            )
        )

        body = plan_player_action("我想先看看莱特教授的尸体。", world)
        self.assertEqual(body.phase, ActionPhase.ARRIVAL)
        self.assertEqual(body.destination_scene_id, "miskatonic_medical")
        self.assertEqual(body.transition_kind, "discovery_target")
        self.assertEqual(body.discovery_matches, ())

        # Existing worlds retain their old runtime catalog; the target
        # normalizer must still route the player phrase before a save is reset.
        legacy = json.loads(json.dumps(world))
        legacy["clue_catalog"]["wright_body_evidence"]["discovery_rules"][0]["targets"] = [
            "莱特教授的遗体",
            "莱特教授遗体",
            "莱特遗体",
            "莱特的尸体",
        ]
        legacy["scene_catalog"]["miskatonic_university"].pop("action_routes", None)
        legacy["scene_catalog"]["miskatonic_medical"].pop("entry_beat", None)
        legacy_body = plan_player_action("我想先看看莱特教授的尸体。", legacy)
        self.assertEqual(legacy_body.destination_scene_id, "miskatonic_medical")
        self.assertEqual(legacy_body.transition_kind, "discovery_target")

        world["current_scene"] = world["scene_catalog"]["miskatonic_medical"]
        world["pc"]["inventory"].append("真实尸检记录副本")
        carried_copy = plan_player_action("检查翻看一下真实的尸检记录副本", world)
        self.assertEqual(carried_copy.phase, ActionPhase.INTERACTION)
        self.assertIsNone(carried_copy.destination_scene_id)

        world["pc"]["inventory"].remove("真实尸检记录副本")
        bare_copy = plan_player_action("翻看记录副本", world)
        self.assertEqual(bare_copy.phase, ActionPhase.INTERACTION)
        self.assertIsNone(bare_copy.destination_scene_id)

        hunter_copy = plan_player_action("查看亨特的复制件", world)
        self.assertEqual(hunter_copy.destination_scene_id, "arkham_sanatorium")
        self.assertEqual(hunter_copy.transition_kind, "discovery_target")

        world["current_scene"] = world["scene_catalog"]["arkham_sanatorium"]
        local_copy = plan_player_action("翻看副本", world)
        self.assertEqual(local_copy.phase, ActionPhase.CONTACT)
        self.assertEqual(
            [match.clue_id for match in local_copy.discovery_matches],
            ["hunter_copy"],
        )

        world["current_scene"] = world["scene_catalog"]["miskatonic_university"]
        contact = plan_player_action("让法伦联系惠特克罗夫特医生。", world)
        self.assertEqual(contact.phase, ActionPhase.ARRIVAL)
        self.assertEqual(contact.destination_scene_id, "miskatonic_medical")
        self.assertEqual(contact.transition_kind, "authored_route")
        self.assertEqual(contact.route_id, "contact_whitcroft")
        self.assertIn("惠特克罗夫特医生", contact.departure_text)
        self.assertIn("医学院", contact.travel_text)
        self.assertEqual(contact.entry_text, "")

        refused = plan_player_action("我暂时不看莱特教授的尸体。", world)
        self.assertEqual(refused.phase, ActionPhase.INTERACTION)
        self.assertIsNone(refused.destination_scene_id)

        asked = plan_player_action("我问法伦能否让我看莱特教授的尸体？", world)
        self.assertEqual(asked.phase, ActionPhase.INTERACTION)
        self.assertIsNone(asked.destination_scene_id)

        declined_contact = plan_player_action("我不让法伦联系惠特克罗夫特医生。", world)
        self.assertEqual(declined_contact.phase, ActionPhase.INTERACTION)
        self.assertIsNone(declined_contact.destination_scene_id)

    def test_scarlet_scene_graph_covers_authored_npc_locations_and_returns(self):
        world = json.loads(
            (PROJECT_ROOT / "mod" / "猩红文档" / "world_state_initial.json").read_text(
                encoding="utf-8"
            )
        )
        scenes = world["scene_catalog"]
        for scene_id, scene in scenes.items():
            for exit_id in scene["exits"]:
                self.assertIn(exit_id, scenes, f"{scene_id} has unknown exit")
            for npc_id in scene["npcs_present"]:
                npc = next(npc for npc in world["npcs"] if npc["id"] == npc_id)
                self.assertEqual(npc["current_location"], scene_id)

        for npc in world["npcs"]:
            location = npc.get("current_location")
            if location == "unknown":
                continue
            self.assertIn(location, scenes, npc["id"])
            self.assertIn(npc["id"], scenes[location]["npcs_present"])

        hub = "miskatonic_university"
        for scene_id, scene in scenes.items():
            if scene_id != hub and hub in scene["exits"]:
                self.assertIn(scene_id, scenes[hub]["exits"])

    def test_action_resolution_separates_arrival_from_contact(self):
        world = discovery_world()
        world["current_scene"] = {"id": "campus"}
        world["scene_catalog"] = {
            "campus": {"id": "campus", "name": "大学"},
            "morgue": {"id": "morgue", "name": "停尸房"},
        }

        arrival = plan_player_action("我去停尸房检查教授遗体。", world)
        self.assertEqual(arrival.phase, ActionPhase.ARRIVAL)
        self.assertEqual(arrival.destination_scene_id, "morgue")
        self.assertFalse(arrival.permits_discovery_effects)
        self.assertEqual(arrival.discovery_matches, ())

        world["current_scene"] = {"id": "morgue"}
        contact = plan_player_action("我仔细检查教授遗体。", world)
        self.assertEqual(contact.phase, ActionPhase.CONTACT)
        self.assertTrue(contact.permits_discovery_effects)
        self.assertEqual([match.clue_id for match in contact.discovery_matches], ["body"])

    def test_matches_explicit_action_and_declared_skill(self):
        world = discovery_world()

        matches = match_discovery_rules("我仔细检查教授遗体的眼睛。", world)

        self.assertEqual([match.clue_id for match in matches], ["body"])
        self.assertEqual(preferred_check_skill(matches, world), "spot_hidden")

    def test_target_matching_ignores_structural_particle(self):
        """玩家说「教授的遗体」必须命中声明目标「教授遗体」（的 不参与匹配）。"""
        world = discovery_world()

        matches = match_discovery_rules("请惠特克罗夫特揭开白布，仔细查看教授的遗体", world)

        self.assertEqual([match.clue_id for match in matches], ["body"])

    def test_discovery_can_declare_luck_instead_of_a_skill(self):
        world = discovery_world()
        rule = world["clue_catalog"]["body"]["discovery_rules"][0]
        rule.pop("skill")
        rule["check_type"] = "luck"
        rule["difficulty"] = "hard"

        matches = match_discovery_rules("我仔细检查教授遗体。", world)

        self.assertIsNone(preferred_check_skill(matches, world))
        self.assertEqual(preferred_luck_difficulty(matches), "hard")

    def test_rejects_negated_or_discussed_action(self):
        world = discovery_world()

        self.assertEqual(
            match_discovery_rules("我暂时不检查教授遗体。", world),
            [],
        )
        self.assertEqual(
            match_discovery_rules("请问我能不能检查教授遗体？", world),
            [],
        )
        self.assertEqual(
            match_discovery_rules("我问医生：你检查过教授遗体吗？", world),
            [],
        )
        self.assertEqual(
            match_discovery_rules("我让医生检查教授遗体。", world),
            [],
        )

    def test_does_not_rediscover_known_clue(self):
        world = discovery_world()
        world["clues_found"]["investigation"].append({"catalog_id": "body"})

        self.assertEqual(
            match_discovery_rules("我检查教授遗体。", world),
            [],
        )


class DiscoveryResolutionTests(unittest.TestCase):
    def test_arrival_can_find_an_authored_npc_location_empty(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            context = RuntimeContext.create(
                "empty-lodge-office",
                "猩红文档",
                project_root=PROJECT_ROOT,
                runtime_root=Path(temp_dir),
            )
            handouts: list[dict] = []
            engine = GameEngine.__new__(GameEngine)
            engine.context = context
            engine.cb = EngineCallbacks(on_handout=handouts.append)
            world = context.world_store.load()
            lodge_index = next(
                index for index, npc in enumerate(world["npcs"]) if npc["id"] == "harland_lodge"
            )
            engine._execute_tool(
                "state_set",
                {
                    "path": f"npcs.{lodge_index}.current_location",
                    "value": '"miskatonic_student_commons"',
                },
            )

            engine._execute_tool(
                "state_set",
                {
                    "path": "current_scene.id",
                    "value": '"miskatonic_lodge_office"',
                },
            )

            current = context.world_store.load()["current_scene"]
            self.assertEqual(current["id"], "miskatonic_lodge_office")
            self.assertEqual(current["npcs_present"], [])
            self.assertNotIn(
                "harland_lodge",
                [event.get("entity_id") for event in handouts],
            )

    def test_morgue_arrival_does_not_examine_body_or_trigger_effects(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            context = RuntimeContext.create(
                "discovery-timeline",
                "猩红文档",
                project_root=PROJECT_ROOT,
                runtime_root=Path(temp_dir),
            )
            make_morgue_preview_blocking(context)
            events: list[tuple[str, str]] = []
            engine = GameEngine.__new__(GameEngine)
            engine.context = context
            engine.messages = []
            engine._player_turn_count = 0
            engine.narrative_model = "story-model"
            engine.cb = EngineCallbacks(
                on_narrative=lambda text, npc_id=None: events.append(("narrative", (text, npc_id))),
                on_speaker_segment=lambda npc_id: events.append(("speaker", npc_id)),
                on_decision=lambda info: events.append(("decision", info)) or "continue_action",
                on_tension=lambda _text, category: events.append(("tension", category)),
                on_dice=lambda summary, _data: events.append(("dice", summary)),
                on_handout=lambda info: events.append(("handout", info["asset_id"])),
            )
            engine._maybe_inject_tier = lambda: None
            engine._detect_content_skill_hint = lambda _content: None
            engine._retrieve_lore_context = lambda _content=None: None
            engine._resolve_action_check = lambda *_args: events.append(("check", "unexpected"))
            frozen_input = "我想先看看莱特教授的尸体。"
            engine._preplanned_action_resolution = plan_player_action(
                frozen_input,
                context.world_store.load(),
            )
            engine._plan_player_action = lambda _content: self.fail(
                "确认行动时不应重新解析已冻结的计划"
            )

            result = _prepare_turn(
                {
                    "engine": engine,
                    "user_content": frozen_input,
                }
            )

            # 预演罐头文案不再直接播为最终叙事；决策卡携带大意，
            # 完整演出由故事模型按素材展开。
            narrative_events = [value for kind, value in events if kind == "narrative"]
            self.assertFalse(narrative_events)
            decisions = [value for kind, value in events if kind == "decision"]
            self.assertEqual(decisions[0]["presentation"], "chat")
            self.assertEqual(decisions[0]["kind"], "action_preview")
            self.assertIn("想亲眼看看查尔斯", decisions[0]["description"])
            self.assertNotIn("【npc:", decisions[0]["description"])
            self.assertEqual(
                result["player_followups"],
                [
                    {
                        "text": "请法伦联系医生，前往停尸房",
                        "after_narrative_segment": 0,
                    }
                ],
            )
            self.assertNotIn("check", [event[0] for event in events])
            dice_events = [value for kind, value in events if kind == "dice"]
            self.assertEqual(dice_events, [])
            self.assertEqual(
                [value for kind, value in events if kind == "handout"],
                ["john_whitcroft"],
            )
            self.assertEqual(
                context.world_store.load()["current_scene"]["id"],
                "miskatonic_medical",
            )
            self.assertEqual(result["narrative"], "")
            self.assertEqual(result["authored_segments"], [])
            model_content = engine.messages[-1]["content"]
            self.assertIn("行动预演素材", model_content)
            self.assertIn("想亲眼看看查尔斯", model_content)
            # 既定事实按顺序交付模型：先赶路，再抵达
            self.assertLess(
                model_content.index("你前往密斯卡托尼克大学医学院"),
                model_content.index("冷柜间门口"),
            )
            self.assertIn('"arrival_only":true', model_content)

            model_suffix = (
                "\n\n医学院地下的空气更冷。惠特克罗夫特医生站在门口等候。\n\n"
                '惠特克罗夫特医生："法伦主任说你想亲眼看看。"'
            )
            final_segments, _ = _parse_final_narrative(
                engine,
                result,
                model_suffix,
            )
            self.assertEqual(final_segments[0].kind, "narration")
            self.assertIn("医学院地下的空气更冷", final_segments[0].text)

            blocked = engine._execute_model_tool(
                "sanity_event",
                {"description": "看见莱特遗体", "severity": "minor", "clue_id": ""},
                player_action="前往停尸间查看莱特遗体",
            )
            self.assertEqual(
                __import__("json").loads(blocked)["error"],
                "arrival_turn_effect_not_authorized",
            )

    def test_cancelling_morgue_preview_keeps_origin_scene_and_skips_story_agent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            context = RuntimeContext.create(
                "discovery-preview-cancel",
                "猩红文档",
                project_root=PROJECT_ROOT,
                runtime_root=Path(temp_dir),
            )
            make_morgue_preview_blocking(context)
            events: list[tuple[str, object]] = []
            engine = GameEngine.__new__(GameEngine)
            engine.context = context
            engine.messages = []
            engine._player_turn_count = 0
            engine.narrative_model = "story-model"
            engine.cb = EngineCallbacks(
                on_narrative=lambda text, npc_id=None: events.append(("narrative", (text, npc_id))),
                on_speaker_segment=lambda npc_id: events.append(("speaker", npc_id)),
                on_decision=lambda info: events.append(("decision", info)) or "cancel_action",
            )
            engine._maybe_inject_tier = lambda: None
            engine._retrieve_lore_context = lambda _content=None: None

            result = _prepare_turn(
                {
                    "engine": engine,
                    "user_content": "我想先看看莱特教授的尸体。",
                }
            )

            self.assertTrue(result["skip_agent"])
            self.assertTrue(result["skip_model_audit"])
            self.assertEqual(
                context.world_store.load()["current_scene"]["id"],
                "miskatonic_university",
            )
            self.assertIn("玩家暂不执行原行动", engine.messages[-1]["content"])
            self.assertNotIn("你前往密斯卡托尼克大学医学院", result["narrative"])

    def test_nonblocking_morgue_preview_plays_the_beat_without_a_card(self):
        """模组默认的停尸房预演是过渡节拍：不弹卡、不追问。作者文案作为
        已结算的出发节拍先播给玩家，赶路与抵达按既定顺序接上，决策卡文案
        绝不回灌给故事模型。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            context = RuntimeContext.create(
                "discovery-preview-nonblocking",
                "猩红文档",
                project_root=PROJECT_ROOT,
                runtime_root=Path(temp_dir),
            )
            beat = self._morgue_advisory(context)["transition_text"]
            self.assertTrue(beat)
            san_before = context.world_store.load()["pc"]["san"]
            events: list[tuple[str, object]] = []
            engine = self._preview_engine(context, events)

            result = _prepare_turn(
                {
                    "engine": engine,
                    "user_content": "我想先看看莱特教授的尸体。",
                }
            )

            self.assertFalse([value for kind, value in events if kind == "decision"])
            narratives = [value for kind, value in events if kind == "narrative"]
            self.assertEqual(narratives[0][1], None)
            self.assertEqual(narratives[0][0].strip(), beat)
            # 通知事实只预播一次，不随节拍重复。
            self.assertEqual(
                [text.strip() for text, _npc in narratives].count(beat),
                1,
            )
            self.assertEqual(
                [value for kind, value in events if kind == "handout"],
                ["john_whitcroft"],
            )
            world = context.world_store.load()
            self.assertEqual(world["current_scene"]["id"], "miskatonic_medical")
            # 到达不等于查看遗体：线索、flag 与 SAN 都必须等合法的调查动作。
            self.assertFalse(world["flags"]["body_examined"])
            self.assertEqual(world["pc"]["san"], san_before)
            self.assertNotIn(
                "wright_body_evidence",
                [
                    clue.get("catalog_id") or clue.get("id")
                    for clue in world["clues_found"]["investigation"]
                ],
            )
            self.assertFalse(result["skip_agent"])
            # 流式文本（已播给玩家的节拍）顺序：过渡节拍 → 赶路 → 抵达 → 接待。
            streamed = result["narrative"]
            self.assertLess(streamed.index(beat), streamed.index("你前往密斯卡托尼克大学医学院"))
            self.assertLess(
                streamed.index("你前往密斯卡托尼克大学医学院"), streamed.index("冷柜间门口")
            )
            self.assertNotIn("也许愿意先听听我对这件事的看法", streamed)
            model_content = engine.messages[-1]["content"]
            self.assertIn(beat, model_content)
            self.assertEqual(model_content.count(beat), 1)
            self.assertIn("已结算的既定事实", model_content)
            # 决策卡文案（劝留与"立即前往/先问法伦"的分支提示）不得再出现。
            self.assertNotIn("行动预演素材", model_content)
            self.assertNotIn("想亲眼看看查尔斯", model_content)
            self.assertNotIn("也许愿意先听听我对这件事的看法", model_content)
            self.assertNotIn("你可以立即前往", model_content)
            self.assertLess(
                model_content.index(beat),
                model_content.index("你前往密斯卡托尼克大学医学院"),
            )
            self.assertLess(
                model_content.index("你前往密斯卡托尼克大学医学院"),
                model_content.index("冷柜间门口"),
            )

    def test_asking_fallon_first_keeps_the_investigator_in_the_office(self):
        """明确选择先问法伦时仍留在原场景：过渡节拍不产生任何移动或提醒。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            context = RuntimeContext.create(
                "discovery-preview-ask-fallon",
                "猩红文档",
                project_root=PROJECT_ROOT,
                runtime_root=Path(temp_dir),
            )
            events: list[tuple[str, object]] = []
            engine = self._preview_engine(context, events)

            result = _prepare_turn(
                {
                    "engine": engine,
                    "user_content": (
                        "我先留下来问法伦：你为什么不相信莱特的死亡证明？"
                        "在我去看遗体之前，还有什么应该知道的？"
                    ),
                }
            )

            self.assertFalse([value for kind, value in events if kind == "decision"])
            self.assertFalse([value for kind, value in events if kind == "narrative"])
            self.assertEqual(
                context.world_store.load()["current_scene"]["id"],
                "miskatonic_university",
            )
            self.assertFalse(result["skip_agent"])
            model_content = engine.messages[-1]["content"]
            self.assertNotIn("行动预演素材", model_content)
            self.assertNotIn("已结算的既定事实", model_content)
            self.assertNotIn("也许愿意先听听我对这件事的看法", model_content)
            self.assertNotIn("你可以立即前往", model_content)

    def test_absent_fallon_never_announces_a_completed_contact(self):
        """法伦不在场时改用守秘人回落节拍：不得预播"法伦已打电话"。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            context = RuntimeContext.create(
                "discovery-preview-absent-npc",
                "猩红文档",
                project_root=PROJECT_ROOT,
                runtime_root=Path(temp_dir),
            )
            keeper_text = self._morgue_advisory(context)["keeper_text"]

            def clear_fallon(world: dict) -> None:
                world["current_scene"]["npcs_present"] = []

            context.world_store.update(clear_fallon)
            events: list[tuple[str, object]] = []
            engine = self._preview_engine(context, events)

            _prepare_turn({"engine": engine, "user_content": "我想先看看莱特教授的尸体。"})

            self.assertFalse([value for kind, value in events if kind == "decision"])
            narratives = [value for kind, value in events if kind == "narrative"]
            self.assertEqual(narratives[0][1], None)
            self.assertEqual(narratives[0][0].strip(), keeper_text)
            streamed = "".join(text for text, _npc in narratives)
            model_content = engine.messages[-1]["content"]
            for claim in ("拨通医学院的内线", "知会", "打过招呼"):
                self.assertNotIn(claim, streamed)
                self.assertNotIn(claim, model_content)

    def test_arrival_beat_does_not_imply_a_prior_arrangement(self):
        """到医学院的接待节拍不得隐含"有人替你安排过"（否则会逼模型补出通知）。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            context = RuntimeContext.create(
                "discovery-preview-entry-beat",
                "猩红文档",
                project_root=PROJECT_ROOT,
                runtime_root=Path(temp_dir),
            )
            entry_beat = context.world_store.load()["scene_catalog"]["miskatonic_medical"][
                "entry_beat"
            ]

            self.assertEqual(entry_beat["npc_id"], "john_whitcroft")
            for claim in ("等候", "等待", "打过招呼", "已经通知", "接到电话"):
                self.assertNotIn(claim, entry_beat["public_text"])

    def test_cancelling_the_blocking_card_never_plays_a_transition_beat(self):
        """阻塞卡取消：只播卡片文案，绝不播过渡节拍，也不触发模型。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            context = RuntimeContext.create(
                "discovery-preview-cancel-beat",
                "猩红文档",
                project_root=PROJECT_ROOT,
                runtime_root=Path(temp_dir),
            )
            beat = self._morgue_advisory(context)["transition_text"]
            self.assertTrue(beat)
            make_morgue_preview_blocking(context)
            events: list[tuple[str, object]] = []
            engine = self._preview_engine(context, events, decision="cancel_action")

            result = _prepare_turn({"engine": engine, "user_content": "我想先看看莱特教授的尸体。"})

            self.assertTrue(result["skip_agent"])
            # 取消回合没有模型调用，但已展示的节拍仍写进本轮消息：卡片文案在，
            # 过渡节拍与"已完成联系"的表述都不在。
            prompt = engine.messages[-1]["content"]
            streamed = "".join(value[0] for kind, value in events if kind == "narrative")
            for surface in (prompt, streamed):
                self.assertNotIn(beat, surface)
                self.assertNotIn("拨通医学院的内线", surface)
            # 卡片文案本身可以被展示，但不得带上"联系已完成"的节拍表述。
            self.assertNotIn("已结算的既定事实", streamed)
            self.assertEqual(
                context.world_store.load()["current_scene"]["id"],
                "miskatonic_university",
            )

    def test_refused_cross_scene_stay_settles_nothing_and_plays_no_beat(self):
        """被拒绝的跨场景停留（B5b 原输入形态）：不移动、不预播节拍、不结算时间。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            context = RuntimeContext.create(
                "discovery-refused-stay",
                "猩红文档",
                project_root=PROJECT_ROOT,
                runtime_root=Path(temp_dir),
            )
            before = context.world_store.load()
            events: list[tuple[str, object]] = []
            engine = self._preview_engine(context, events)

            _prepare_turn(
                {
                    "engine": engine,
                    "user_content": (
                        "接下来几天，我白天在古董店对面的咖啡馆监视进出的人，"
                        "晚上回旅馆整理三个买家的线索"
                    ),
                }
            )

            self.assertFalse([value for kind, value in events if kind == "decision"])
            self.assertFalse([value for kind, value in events if kind == "narrative"])
            after = context.world_store.load()
            self.assertEqual(after["current_scene"]["id"], before["current_scene"]["id"])
            self.assertEqual(after.get("world_clock"), before.get("world_clock"))

    def test_legacy_snapshot_advisory_is_upgraded_and_never_replays_card_text(self):
        """旧存档/旧分支直接继承状态时也覆盖：升级按条目幂等执行，
        升级后重放的仍是过渡节拍，而不是旧决策卡文案。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            context = RuntimeContext.create(
                "discovery-legacy-snapshot",
                "猩红文档",
                project_root=PROJECT_ROOT,
                runtime_root=Path(temp_dir),
            )
            # 事故世界里的原始条目：作者在 supersedes 中声明的官方旧载荷。
            legacy = dict(self._morgue_advisory(context)["supersedes"][0])
            self.assertNotIn("transition_text", legacy)

            def downgrade(world: dict) -> None:
                world["scene_catalog"]["miskatonic_university"]["action_advisories"][0] = dict(
                    legacy
                )

            context.world_store.update(downgrade)

            self.assertEqual(
                context.sync_module_metadata(),
                ["miskatonic_university/wright_body_handoff"],
            )
            # 可重复执行：第二次不再改动任何条目。
            self.assertEqual(context.sync_module_metadata(), [])
            stored = self._morgue_advisory(context)
            self.assertTrue(stored["transition_text"])
            self.assertNotIn("也许愿意先听听我对这件事的看法", stored["npc_text"])
            self.assertNotIn("supersedes", stored)

            events: list[tuple[str, object]] = []
            engine = self._preview_engine(context, events)
            _prepare_turn(
                {
                    "engine": engine,
                    "user_content": "我想先看看莱特教授的尸体。",
                }
            )

            self.assertFalse([value for kind, value in events if kind == "decision"])
            beats = [value for kind, value in events if kind == "narrative"]
            self.assertEqual([value[1] for value in beats], [None, None, None])
            self.assertEqual(beats[0][0].strip(), stored["transition_text"])
            model_content = engine.messages[-1]["content"]
            self.assertIn(stored["transition_text"], model_content)
            self.assertNotIn("也许愿意先听听我对这件事的看法", model_content)

    def test_legacy_snapshot_without_the_fix_skips_the_beat_instead_of_the_card(self):
        """未升级的历史快照只降级为"没有提醒"，绝不重播卡片文案。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            context = RuntimeContext.create(
                "discovery-legacy-untouched",
                "猩红文档",
                project_root=PROJECT_ROOT,
                runtime_root=Path(temp_dir),
            )

            def downgrade(world: dict) -> None:
                advisory = world["scene_catalog"]["miskatonic_university"]["action_advisories"][0]
                advisory.pop("transition_text", None)
                advisory["npc_text"] = "你也许愿意先听听我对这件事的看法。"

            context.world_store.update(downgrade)
            events: list[tuple[str, object]] = []
            engine = self._preview_engine(context, events)

            _prepare_turn(
                {
                    "engine": engine,
                    "user_content": "我想先看看莱特教授的尸体。",
                }
            )

            self.assertFalse([value for kind, value in events if kind == "decision"])
            # 只剩赶路与抵达两条引擎节拍，没有劝留、也没有分支提示。
            self.assertEqual(
                len([value for kind, value in events if kind == "narrative"]),
                2,
            )
            model_content = engine.messages[-1]["content"]
            self.assertNotIn("行动预演素材", model_content)
            self.assertNotIn("也许愿意先听听我对这件事的看法", model_content)
            self.assertNotIn("你可以立即前往", model_content)

    def test_customized_legacy_advisory_survives_the_entry_level_upgrade(self):
        """对偶用例（真实模组数据）：同名、非阻塞、无 transition_text，
        但用户改写过的文案必须原样保留。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            context = RuntimeContext.create(
                "discovery-legacy-customized",
                "猩红文档",
                project_root=PROJECT_ROOT,
                runtime_root=Path(temp_dir),
            )
            official = self._morgue_advisory(context)
            legacy = dict(official["supersedes"][0])
            self.assertNotIn("transition_text", legacy)

            def downgrade(world: dict) -> None:
                advisory = dict(legacy)
                advisory["npc_text"] = "（用户自写的法伦台词）你也许愿意先听听我对这件事的看法。"
                advisory["keeper_text"] = "（用户自写的守秘人提醒）"
                world["scene_catalog"]["miskatonic_university"]["action_advisories"][0] = advisory

            context.world_store.update(downgrade)
            before = self._morgue_advisory(context)

            self.assertEqual(context.sync_module_metadata(), [])

            self.assertEqual(self._morgue_advisory(context), before)
            self.assertNotIn("transition_text", self._morgue_advisory(context))
            # 未升级 ⇒ 引擎跳过该条目：既不弹卡，也不把用户文案当出发素材回灌。
            events: list[tuple[str, object]] = []
            engine = self._preview_engine(context, events)
            _prepare_turn({"engine": engine, "user_content": "我想先看看莱特教授的尸体。"})
            model_content = engine.messages[-1]["content"]
            self.assertNotIn("用户自写的法伦台词", model_content)
            self.assertNotIn("行动预演素材", model_content)

    def test_stale_module_revision_refresh_replaces_the_whole_scene_catalog(self):
        """既有整体刷新的边界（**不是**条目级迁移的保护范围）。

        世界记录的文件版本变化时 refresh_static_handout_config 会整体替换
        scene_catalog（含 action_advisories），因此这条路径上世界内对场景目录的
        任何改写——包括用户自写的 advisory——都会被模组当前内容覆盖。
        条目级 supersedes 精确匹配只负责"版本已一致"的路径（分支/存档恢复），
        两条路径的保护范围不同，验收结论不能互相代入。
        """
        template = json.loads(
            (PROJECT_ROOT / "mod/猩红文档/world_state_initial.json").read_text(encoding="utf-8")
        )
        template_advisory = template["scene_catalog"]["miskatonic_university"]["action_advisories"][
            0
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_root = Path(temp_dir)
            world_id = "discovery-stale-revision"
            context = RuntimeContext.create(
                world_id,
                "猩红文档",
                project_root=PROJECT_ROOT,
                runtime_root=runtime_root,
            )

            def customize(world: dict) -> None:
                advisory = world["scene_catalog"]["miskatonic_university"]["action_advisories"][0]
                advisory["npc_text"] = "（用户自写）"
                world["scene_catalog"]["miskatonic_university"]["action_advisories"].append(
                    {
                        "id": "user_added",
                        "destination_scene_id": "miskatonic_medical",
                        "blocking": False,
                        "npc_text": "（用户自己加的条目）",
                    }
                )

            context.world_store.update(customize)
            # revision 记录在兼容导出文件里（存在时优先于数据库元数据）
            metadata = json.loads(context.metadata_file.read_text(encoding="utf-8"))
            metadata["initial_state_revision"] = "1:1"
            context.metadata_file.write_text(
                json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
            )

            context = RuntimeContext.create(
                world_id,
                "猩红文档",
                project_root=PROJECT_ROOT,
                runtime_root=runtime_root,
            )

            advisories = context.world_store.load()["scene_catalog"]["miskatonic_university"][
                "action_advisories"
            ]
            # 整体刷新已经发生：用户改写与用户新增条目都被模组当前内容替换。
            self.assertEqual(len(advisories), 1)
            self.assertEqual(advisories[0]["npc_text"], template_advisory["npc_text"])
            self.assertEqual(advisories[0]["transition_text"], template_advisory["transition_text"])

    def test_legacy_branch_world_with_absent_fallon_announces_no_arrangement(self):
        """组合验证：旧快照分支 + 法伦不在场。

        分支世界的 advisory 与 entry_beat 都按条目升级；法伦不在场时回落到
        守秘人节拍（"还没有安排"），接待节拍也绝不能沿用"医生正等候你"
        的旧措辞——否则会再次逼模型补出"有人打过招呼"。
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            context = RuntimeContext.create(
                "discovery-legacy-absent-fallon",
                "猩红文档",
                project_root=PROJECT_ROOT,
                runtime_root=Path(temp_dir),
            )
            template = json.loads(
                (PROJECT_ROOT / "mod/猩红文档/world_state_initial.json").read_text(encoding="utf-8")
            )
            legacy_advisory = dict(self._morgue_advisory(context)["supersedes"][0])
            legacy_beat = dict(
                template["scene_catalog"]["miskatonic_medical"]["entry_beat"]["supersedes"][0]
            )
            self.assertIn("等候", legacy_beat["public_text"])

            def downgrade(world: dict) -> None:
                world["scene_catalog"]["miskatonic_university"]["action_advisories"][0] = dict(
                    legacy_advisory
                )
                world["scene_catalog"]["miskatonic_medical"]["entry_beat"] = dict(legacy_beat)
                world["current_scene"]["npcs_present"] = []

            context.world_store.update(downgrade)

            self.assertEqual(
                sorted(context.sync_module_metadata()),
                ["miskatonic_medical/entry_beat", "miskatonic_university/wright_body_handoff"],
            )
            keeper_text = self._morgue_advisory(context)["keeper_text"]

            events: list[tuple[str, object]] = []
            engine = self._preview_engine(context, events)
            _prepare_turn({"engine": engine, "user_content": "我想先看看莱特教授的尸体。"})

            world = context.world_store.load()
            self.assertEqual(world["current_scene"]["id"], "miskatonic_medical")
            streamed = "".join(value[0] for kind, value in events if kind == "narrative")
            model_content = engine.messages[-1]["content"]
            # 守秘人回落节拍（前置事实是"还没有安排"）照常播。
            self.assertIn(keeper_text, streamed)
            # 升级后的接待节拍不隐含"有人替你安排过"。
            self.assertIn("攥着病历夹站在那里", streamed)
            for surface in (streamed, model_content):
                self.assertNotIn("正攥着病历夹等候", surface)
                self.assertNotIn("等候", surface)
                for claim in ("拨通医学院的内线", "知会", "打过招呼"):
                    self.assertNotIn(claim, surface)

    def test_arrival_settle_failure_announces_no_beat_and_fails_the_turn(self):
        """移动结算被拒绝（返回 None）：节拍、赶路、抵达一律不播，回合失败。

        不能仅凭计划是 arrival 就宣布"已通知"：先记账，后播报。
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            context = RuntimeContext.create(
                "discovery-settle-refused",
                "猩红文档",
                project_root=PROJECT_ROOT,
                runtime_root=Path(temp_dir),
            )
            beat = self._morgue_advisory(context)["transition_text"]
            events: list[tuple[str, object]] = []
            engine = self._preview_engine(context, events)
            engine._resolve_scene_transition = lambda *_args, **_kwargs: None

            with self.assertRaises(RuntimeError):
                _prepare_turn({"engine": engine, "user_content": "我想先看看莱特教授的尸体。"})

            self.assertFalse([value for kind, value in events if kind == "narrative"])
            self.assertFalse([value for kind, value in events if kind == "handout"])
            world = context.world_store.load()
            self.assertEqual(world["current_scene"]["id"], "miskatonic_university")
            self.assertNotIn(beat, "".join(str(event) for event in events))

    def test_arrival_settle_exception_announces_no_beat(self):
        """移动结算抛异常（遭遇解析失败等）：同样不播节拍，异常原样上抛。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            context = RuntimeContext.create(
                "discovery-settle-crash",
                "猩红文档",
                project_root=PROJECT_ROOT,
                runtime_root=Path(temp_dir),
            )

            def broken_resolve(*_args, **_kwargs):
                raise OSError("world store unavailable")

            events: list[tuple[str, object]] = []
            engine = self._preview_engine(context, events)
            engine._resolve_scene_transition = broken_resolve

            with self.assertRaises(OSError):
                _prepare_turn({"engine": engine, "user_content": "我想先看看莱特教授的尸体。"})

            self.assertFalse([value for kind, value in events if kind == "narrative"])
            self.assertEqual(
                context.world_store.load()["current_scene"]["id"],
                "miskatonic_university",
            )

    def test_transition_beat_is_announced_only_after_settlement(self):
        """执行顺序：移动先结算落账，过渡节拍（含"已通知"事实）后宣布。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            context = RuntimeContext.create(
                "discovery-settle-before-beat",
                "猩红文档",
                project_root=PROJECT_ROOT,
                runtime_root=Path(temp_dir),
            )
            beat = self._morgue_advisory(context)["transition_text"]
            events: list[tuple[str, object]] = []
            engine = self._preview_engine(context, events)

            original_resolve = GameEngine._resolve_scene_transition
            call_order: list[str] = []

            def recording_resolve(self_engine, *args, **kwargs):
                call_order.append("settle")
                return original_resolve(self_engine, *args, **kwargs)

            engine._resolve_scene_transition = lambda *a, **k: recording_resolve(engine, *a, **k)
            original_on_narrative = engine.cb.on_narrative

            def recording_narrative(text, npc_id=None):
                if beat in text:
                    call_order.append("beat")
                    # 节拍到达玩家时，权威状态必须已经完成移动。
                    call_order.append(context.world_store.load()["current_scene"]["id"])
                original_on_narrative(text, npc_id)

            engine.cb.on_narrative = recording_narrative

            _prepare_turn({"engine": engine, "user_content": "我想先看看莱特教授的尸体。"})

            self.assertEqual(call_order, ["settle", "beat", "miskatonic_medical"])

    def test_blocking_preview_continue_with_failed_settle_also_fails_closed(self):
        """对偶：阻塞卡选择继续后结算同样失败关闭——卡片流程也不能先宣布。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            context = RuntimeContext.create(
                "discovery-blocking-settle-refused",
                "猩红文档",
                project_root=PROJECT_ROOT,
                runtime_root=Path(temp_dir),
            )
            make_morgue_preview_blocking(context)
            events: list[tuple[str, object]] = []
            engine = self._preview_engine(context, events, decision="continue_action")
            engine._resolve_scene_transition = lambda *_args, **_kwargs: None

            with self.assertRaises(RuntimeError):
                _prepare_turn({"engine": engine, "user_content": "我想先看看莱特教授的尸体。"})

            self.assertFalse([value for kind, value in events if kind == "narrative"])
            self.assertEqual(
                context.world_store.load()["current_scene"]["id"],
                "miskatonic_university",
            )

    @staticmethod
    def _morgue_advisory(context) -> dict:
        return context.world_store.load()["scene_catalog"]["miskatonic_university"][
            "action_advisories"
        ][0]

    @staticmethod
    def _preview_engine(
        context,
        events: list[tuple[str, object]],
        *,
        decision: str = "continue_action",
    ) -> GameEngine:
        engine = GameEngine.__new__(GameEngine)
        engine.context = context
        engine.messages = []
        engine._player_turn_count = 0
        engine.narrative_model = "story-model"
        engine.cb = EngineCallbacks(
            on_narrative=lambda text, npc_id=None: events.append(("narrative", (text, npc_id))),
            on_speaker_segment=lambda npc_id: events.append(("speaker", npc_id)),
            on_decision=lambda info: events.append(("decision", info)) or decision,
            on_handout=lambda info: events.append(("handout", info["asset_id"])),
        )
        engine._maybe_inject_tier = lambda: None
        engine._detect_content_skill_hint = lambda _content: None
        engine._retrieve_lore_context = lambda _content=None: None
        engine._resolve_action_check = lambda *_args: None
        return engine

    def test_prepare_choice_uses_authored_action_without_reparsing_its_label(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            context = RuntimeContext.create(
                "discovery-preview-prepare",
                "猩红文档",
                project_root=PROJECT_ROOT,
                runtime_root=Path(temp_dir),
            )
            make_morgue_preview_blocking(context)
            original = "我想先看看莱特教授的尸体。"
            authored = (
                "我先留下来问法伦：你为什么不相信莱特的死亡证明？"
                "在我去看遗体之前，还有什么应该知道的？"
            )
            planned: list[str] = []
            engine = GameEngine.__new__(GameEngine)
            engine.context = context
            engine.messages = []
            engine._player_turn_count = 0
            engine.narrative_model = "story-model"
            engine.cb = EngineCallbacks(
                on_narrative=lambda _text, npc_id=None: None,
                on_speaker_segment=lambda _npc_id: None,
                on_decision=lambda _info: "prepare_ask_fallon_first",
            )
            engine._maybe_inject_tier = lambda: None
            engine._detect_content_skill_hint = lambda _content: None
            engine._retrieve_lore_context = lambda _content=None: None
            engine._resolve_action_check = lambda *_args: None
            engine._preplanned_action_resolution = plan_player_action(
                original,
                context.world_store.load(),
            )

            def plan_replacement(content: str):
                planned.append(content)
                return plan_player_action(content, context.world_store.load())

            engine._plan_player_action = plan_replacement

            result = _prepare_turn({"engine": engine, "user_content": original})

            self.assertEqual(planned, [authored])
            self.assertEqual(result["user_content"], authored)
            self.assertEqual(
                result["player_followups"][0]["text"],
                "先问法伦为什么不相信死亡证明",
            )
            self.assertEqual(
                context.world_store.load()["current_scene"]["id"],
                "miskatonic_university",
            )
            self.assertNotIn("你前往密斯卡托尼克大学医学院", result["narrative"])

    def test_declared_body_event_commits_before_story_generation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            context = RuntimeContext.create(
                "discovery-preflight",
                "猩红文档",
                project_root=PROJECT_ROOT,
                runtime_root=Path(temp_dir),
            )
            handouts: list[dict] = []
            dice: list[tuple[str, dict]] = []
            tension: list[tuple[str, str]] = []
            engine = GameEngine.__new__(GameEngine)
            engine.context = context
            engine.cb = EngineCallbacks(
                on_handout=handouts.append,
                on_dice=lambda summary, data: dice.append((summary, data)),
                on_tension=lambda text, category: tension.append((text, category)),
            )
            engine._execute_tool(
                "state_set",
                {
                    "path": "current_scene.id",
                    "value": '"miskatonic_medical"',
                },
            )

            matches, skill = engine._match_discoveries("我掀开白布，检查莱特教授的遗体。")
            resolved = engine._resolve_discoveries(matches, None)

            self.assertIsNone(skill)
            self.assertEqual(len(resolved), 1)
            self.assertTrue(resolved[0]["discovered"])
            self.assertEqual(resolved[0]["clue_id"], "wright_body_evidence")
            self.assertEqual(len(dice), 1)
            self.assertEqual(tension[0][1], "sanity")
            self.assertEqual(
                [event["asset_id"] for event in handouts if event.get("asset_id") == "wright_body"],
                ["wright_body"],
            )
            self.assertIn(
                "john_whitcroft",
                [event.get("asset_id") for event in handouts],
            )
            world = context.world_store.load()
            self.assertTrue(world["flags"]["body_examined"])
            self.assertIn(
                "john_whitcroft",
                world["seen_handouts"]["npcs"],
            )
            found_ids = {
                clue.get("catalog_id") or clue.get("id")
                for clues in world["clues_found"].values()
                for clue in clues
            }
            self.assertIn("wright_body_evidence", found_ids)


if __name__ == "__main__":
    unittest.main()


def antique_shop_world() -> dict:
    """古董店三步推进链：店面搜查 → 活板门 → 审判文档，全靠 requires_flags 门控。"""
    return {
        "flags": {"wicks_shop_searched": False, "deep_basement_found": False},
        "current_scene": {"id": "trivial_pursuits"},
        "clues_found": {"investigation": []},
        "clue_catalog": {
            "wick_shop_secrets": {
                "id": "wick_shop_secrets",
                "source": "trivial_pursuits",
                "related_scenes": ["trivial_pursuits"],
                "flag_effects": {"wicks_shop_searched": True},
                "discovery_rules": [
                    {
                        "intent": "search",
                        "targets": ["隔断", "储藏室", "店面后面", "厨房"],
                    },
                    {
                        "intent": "examine",
                        "targets": ["隔断", "暗门", "储藏室"],
                    },
                ],
            },
            "wick_trapdoor": {
                "id": "wick_trapdoor",
                "source": "trivial_pursuits",
                "related_scenes": ["trivial_pursuits"],
                "flag_effects": {"deep_basement_found": True},
                "discovery_rules": [
                    {
                        "intent": "search",
                        "targets": ["活板门", "地下室"],
                        "requires_flags": ["wicks_shop_searched"],
                    }
                ],
            },
            "witch_trial_documents": {
                "id": "witch_trial_documents",
                "source": "trivial_pursuits",
                "related_scenes": ["trivial_pursuits"],
                "flag_effects": {"documents_recovered": True},
                "discovery_rules": [
                    {
                        "intent": "take",
                        "targets": ["女巫审判文档", "审判文档原件"],
                        "requires_flags": ["deep_basement_found"],
                    }
                ],
            },
        },
    }


class RequiresFlagsGateTests(unittest.TestCase):
    def test_requires_flags_blocks_premature_match(self):
        world = antique_shop_world()
        self.assertEqual(
            match_discovery_rules("搜查店面后面的隔断", world)[0].clue_id, "wick_shop_secrets"
        )
        # 终局线索不能跳过推进链直接命中
        self.assertEqual(match_discovery_rules("我要拿走女巫审判文档", world), [])
        self.assertEqual(match_discovery_rules("搜查地下室", world), [])

    def test_chain_unlocks_step_by_step(self):
        world = antique_shop_world()
        world["flags"]["wicks_shop_searched"] = True
        self.assertEqual(match_discovery_rules("搜查地下室", world)[0].clue_id, "wick_trapdoor")
        self.assertEqual(match_discovery_rules("我要拿走女巫审判文档", world), [])
        world["flags"]["deep_basement_found"] = True
        self.assertEqual(
            match_discovery_rules("我要拿走女巫审判文档", world)[0].clue_id,
            "witch_trial_documents",
        )

    def test_missing_flags_mapping_blocks_gated_rules(self):
        world = antique_shop_world()
        del world["flags"]
        self.assertEqual(match_discovery_rules("搜查地下室", world), [])


class NegationGuardTests(unittest.TestCase):
    def test_negation_does_not_cross_punctuation(self):
        """「趁店员不注意，检查隔断」不是否定检查——真机里被误判导致
        wick_shop_secrets 漏匹配，推进链差点断在第一步。"""
        world = antique_shop_world()
        self.assertEqual(
            match_discovery_rules("趁店员不注意，检查大厅北端那堵不起眼的木质隔断", world)[
                0
            ].clue_id,
            "wick_shop_secrets",
        )

    def test_true_negation_still_blocked(self):
        world = antique_shop_world()
        self.assertEqual(match_discovery_rules("先不要检查隔断", world), [])
        self.assertEqual(match_discovery_rules("我没有搜查隔断", world), [])
