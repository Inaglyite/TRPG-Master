"""危机触发机制测试：条件判定、落地顺序、自动 once、真实模组数据链。"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from src.config import PROJECT_ROOT
from src.crisis import maybe_fire_crisis, select_crisis_trigger


def crisis_world() -> dict:
    return {
        "flags": {"deep_basement_found": False, "monster_defeated": False},
        "case_clocks": {"monster_manifestation": 2},
        "case_clock_definitions": {"monster_manifestation": {"max": 6}},
        "current_scene": {"id": "trivial_pursuits"},
        "crisis_triggers": [
            {
                "id": "feldman_ambush",
                "scene": "trivial_pursuits",
                "required_flags": {"deep_basement_found": True},
                "forbidden_flags": {"monster_defeated": True},
                "narrative": "兄妹扑出。",
                "combat": {
                    "reason": "伏击",
                    "participants": [{"id": "hector_kara_feldman", "dex": 45}],
                },
                "flags_set": {"feldman_ambushed": True},
            },
            {
                "id": "ink_manifestation",
                "required_flags": {"documents_recovered": True},
                "required_clocks": {"monster_manifestation": 4},
                "narrative": "怪物显形。",
                "flags_set": {"monster_manifested": True},
                "clocks_set": {"monster_manifestation": 9},
            },
        ],
    }


class StubEngine:
    def __init__(self, world: dict, combat_ok: bool = True):
        self.context = SimpleNamespace(world_store=SimpleNamespace(load=lambda: world))
        self.calls: list[tuple[str, dict]] = []
        self.combat_ok = combat_ok

    def _execute_tool(self, name: str, args: dict) -> str:
        self.calls.append((name, args))
        if name == "combat_start":
            return json.dumps({"ok": self.combat_ok})
        return json.dumps({"ok": True})


class SelectCrisisTriggerTests(unittest.TestCase):
    def test_no_conditions_met_returns_none(self):
        self.assertIsNone(select_crisis_trigger(crisis_world()))

    def test_required_flags_and_scene_gate(self):
        world = crisis_world()
        world["flags"]["deep_basement_found"] = True
        self.assertEqual(select_crisis_trigger(world)["id"], "feldman_ambush")
        world["current_scene"] = {"id": "miskatonic_university"}
        self.assertIsNone(select_crisis_trigger(world))

    def test_forbidden_flags_block(self):
        world = crisis_world()
        world["flags"]["deep_basement_found"] = True
        world["flags"]["monster_defeated"] = True
        self.assertIsNone(select_crisis_trigger(world))

    def test_required_clocks_threshold(self):
        world = crisis_world()
        world["flags"]["documents_recovered"] = True
        # 2 < 4：不够
        self.assertIsNone(select_crisis_trigger(world))
        world["case_clocks"]["monster_manifestation"] = 4
        self.assertEqual(select_crisis_trigger(world)["id"], "ink_manifestation")

    def test_fired_flag_skips_trigger(self):
        world = crisis_world()
        world["flags"]["deep_basement_found"] = True
        world["flags"]["crisis_fired_feldman_ambush"] = True
        self.assertIsNone(select_crisis_trigger(world))


class FireCrisisTests(unittest.TestCase):
    def test_fire_applies_combat_flags_clocks_and_auto_mark(self):
        world = crisis_world()
        world["flags"]["deep_basement_found"] = True
        engine = StubEngine(world)

        narrative = maybe_fire_crisis(engine)

        self.assertEqual(narrative, "兄妹扑出。")
        names = [name for name, _ in engine.calls]
        self.assertEqual(names[0], "combat_start")
        self.assertEqual(engine.calls[0][1]["reason"], "伏击")
        # 危机战斗默认注入 hostile_to_pc（伏击者是敌方袭击，非中立目标）
        self.assertTrue(engine.calls[0][1]["participants"][0]["hostile_to_pc"])
        paths = [args["path"] for name, args in engine.calls if name == "state_set"]
        self.assertIn("flags.feldman_ambushed", paths)
        # 机制自动 once 标记
        self.assertIn("flags.crisis_fired_feldman_ambush", paths)

    def test_fire_without_combat_sets_flags_and_clamps_clock(self):
        world = crisis_world()
        world["flags"]["documents_recovered"] = True
        world["case_clocks"]["monster_manifestation"] = 4
        engine = StubEngine(world)

        narrative = maybe_fire_crisis(engine)

        self.assertEqual(narrative, "怪物显形。")
        self.assertNotIn("combat_start", [name for name, _ in engine.calls])
        clock_calls = [
            args
            for name, args in engine.calls
            if name == "state_set" and args["path"].startswith("case_clocks.")
        ]
        # clocks_set 9 按 definitions max=6 收拢
        self.assertEqual(clock_calls[0]["value"], "6")

    def test_combat_failure_aborts_without_effects(self):
        world = crisis_world()
        world["flags"]["deep_basement_found"] = True
        engine = StubEngine(world, combat_ok=False)

        self.assertEqual(maybe_fire_crisis(engine), "")
        # 只有 combat_start 一次尝试，无任何 state_set、不标记已触发
        self.assertEqual([name for name, _ in engine.calls], ["combat_start"])

    def test_explicit_non_hostile_participant_is_respected(self):
        world = crisis_world()
        world["flags"]["deep_basement_found"] = True
        trigger = world["crisis_triggers"][0]
        trigger["combat"]["participants"].append(
            {"id": "innocent_bystander", "hostile_to_pc": False}
        )
        engine = StubEngine(world)

        maybe_fire_crisis(engine)

        participants = engine.calls[0][1]["participants"]
        self.assertFalse(participants[1]["hostile_to_pc"])

    def test_second_call_does_not_refire(self):
        world = crisis_world()
        world["flags"]["deep_basement_found"] = True
        engine = StubEngine(world)
        self.assertTrue(maybe_fire_crisis(engine))
        # 模拟上一回合落账后的世界：自动标记已在 flags 中
        world["flags"]["crisis_fired_feldman_ambush"] = True
        self.assertEqual(maybe_fire_crisis(engine), "")


class ScarletModuleCrisisDataTests(unittest.TestCase):
    @staticmethod
    def _world() -> dict:
        path = PROJECT_ROOT / "mod" / "猩红文档" / "world_state_initial.json"
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)

    def test_ambush_chain(self):
        world = self._world()
        world["current_scene"] = {"id": "trivial_pursuits"}
        self.assertIsNone(select_crisis_trigger(world))
        world["flags"]["deep_basement_found"] = True
        trigger = select_crisis_trigger(world)
        self.assertEqual(trigger["id"], "feldman_ambush")
        self.assertEqual(trigger["combat"]["participants"][0]["id"], "hector_kara_feldman")

    def test_manifestation_chain_any_scene(self):
        world = self._world()
        world["current_scene"] = {"id": "miskatonic_university"}
        world["flags"]["documents_recovered"] = True
        trigger = select_crisis_trigger(world)
        self.assertEqual(trigger["id"], "ink_manifestation")
        # 显形不强制开战：给模型真实目标，由模型 combat_start / 封印
        self.assertNotIn("combat", trigger)
        self.assertEqual(trigger["flags_set"], {"monster_manifested": True})

    def test_monster_defeated_forbids_both(self):
        world = self._world()
        world["current_scene"] = {"id": "trivial_pursuits"}
        world["flags"]["deep_basement_found"] = True
        world["flags"]["documents_recovered"] = True
        world["flags"]["monster_defeated"] = True
        self.assertIsNone(select_crisis_trigger(world))


if __name__ == "__main__":
    unittest.main()
