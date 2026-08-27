import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.app.engine import GameEngine
from src.gameplay.action_preflight import match_action_preview
from src.gameplay.action_resolution import plan_player_action
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
        "npcs": [{
            "id": "bryce_fallon",
            "name": "布莱斯·法伦",
            "disposition": "cooperative",
        }],
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
                "action_advisories": [{
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
                    "prepare_options": [{
                        "id": "ask_customs",
                        "label": "先询问当地礼节",
                        "action_text": "我先问约翰，拜访她时应该注意什么？",
                    }],
                    "cancel_label": "暂时不去",
                }],
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

        selected = engine._preconfirmed_option({
            "kind": "irreversible_violence",
            "target_id": "bryce_fallon",
        })

        self.assertEqual(selected, "confirm_violence")
        self.assertIsNone(engine._preconfirmed_escalation)


if __name__ == "__main__":
    unittest.main()
