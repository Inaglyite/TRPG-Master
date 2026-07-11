import unittest

from src.inventory import InventoryError, use_item


def make_world() -> dict:
    return {
        "pc": {
            "inventory": [
                ".38口径左轮手枪（6发）",
                "手电筒",
                "急救药剂（2瓶）",
                "一次性护符",
            ]
        }
    }


class InventoryUseTests(unittest.TestCase):
    def test_warning_shot_consumes_ammo_outside_combat(self):
        world = make_world()

        result = use_item(
            world,
            item=".38口径左轮手枪",
            operation="firearm_discharge",
            reason="鸣枪示警",
        )

        self.assertEqual(result["before"], 6)
        self.assertEqual(result["after"], 5)
        self.assertEqual(world["pc"]["inventory"][0], ".38口径左轮手枪（5发）")
        self.assertEqual(world["item_use_log"][-1]["reason"], "鸣枪示警")

    def test_stale_weapon_label_still_matches_current_ammo_count(self):
        world = make_world()
        world["pc"]["inventory"][0] = ".38口径左轮手枪（5发）"

        result = use_item(
            world,
            item=".38口径左轮手枪（6发）",
            operation="firearm_discharge",
            reason="再次开枪",
        )

        self.assertEqual(result["after"], 4)
        self.assertEqual(world["pc"]["inventory"][0], ".38口径左轮手枪（4发）")

    def test_empty_firearm_rejects_without_mutation(self):
        world = make_world()
        world["pc"]["inventory"][0] = ".38口径左轮手枪（0发）"

        with self.assertRaisesRegex(InventoryError, "弹药不足"):
            use_item(
                world,
                item="左轮手枪",
                operation="firearm_discharge",
                reason="鸣枪",
            )

        self.assertEqual(world["pc"]["inventory"][0], ".38口径左轮手枪（0发）")
        self.assertNotIn("item_use_log", world)

    def test_use_verifies_durable_item_without_consuming_it(self):
        world = make_world()

        result = use_item(world, item="手电筒", operation="use", reason="照亮走廊")

        self.assertEqual(result["consumed"], 0)
        self.assertIn("手电筒", world["pc"]["inventory"])

    def test_consume_decrements_stack_and_removes_last_item(self):
        world = make_world()

        first = use_item(world, item="急救药剂", operation="consume", reason="处理伤口")
        second = use_item(world, item="急救药剂", operation="consume", reason="再次处理伤口")

        self.assertEqual(first["item_after"], "急救药剂（1瓶）")
        self.assertIsNone(second["item_after"])
        self.assertFalse(any("急救药剂" in item for item in world["pc"]["inventory"]))

    def test_consume_removes_uncounted_disposable_but_rejects_durable_item(self):
        world = make_world()

        use_item(world, item="一次性护符", operation="consume", reason="挡下一次诅咒")
        self.assertNotIn("一次性护符", world["pc"]["inventory"])

        with self.assertRaisesRegex(InventoryError, "耐用品"):
            use_item(world, item="手电筒", operation="consume", reason="照明")


if __name__ == "__main__":
    unittest.main()
