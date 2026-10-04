import { beforeEach, describe, expect, it } from "vitest";
import { initialStructuredState, useStructuredStore } from "./structured-store";
import { EVENT_FIXTURES } from "../protocol/structured-fixtures";

const alice = {
  investigator_id: "alice",
  name: "甲",
  hp: 10,
  max_hp: 11,
  san: 60,
  skills: { rare_skill: 17 },
  attributes: { INT: 70 },
  inventory: [{ id: "pen", label: "钢笔", quantity: 1 }],
  conditions: [],
};
const bob = { ...alice, investigator_id: "bob", name: "乙", inventory: [] };
beforeEach(() => useStructuredStore.setState({ ...initialStructuredState }));
function event(type: string, payload: Record<string, unknown>) {
  useStructuredStore
    .getState()
    .applyEvent({ ...EVENT_FIXTURES.snapshot, type, payload });
}
describe("private keeper party projection", () => {
  it("keeps complete exact skill keys and discards malformed numbers", () => {
    useStructuredStore.getState().applySnapshot({
      keeper_investigators: [
        { ...alice, skills: { ...alice.skills, bogus: "70" } },
      ],
    });
    expect(useStructuredStore.getState().keeperInvestigators[0].skills).toEqual(
      { rare_skill: 17 },
    );
  });
  it("a fresh non-keeper snapshot clears every party sheet", () => {
    useStructuredStore
      .getState()
      .applySnapshot({ keeper_investigators: [alice, bob] });
    useStructuredStore.getState().applySnapshot({ investigator_id: "alice" });
    expect(useStructuredStore.getState().keeperInvestigators).toEqual([]);
  });
  it("world switches also erase party data", () => {
    useStructuredStore
      .getState()
      .applySnapshot({ keeper_investigators: [alice] }, "one");
    useStructuredStore.getState().bindWorld("two");
    expect(useStructuredStore.getState().keeperInvestigators).toEqual([]);
  });
  it("committed stat changes update only the identified party member", () => {
    useStructuredStore
      .getState()
      .applySnapshot(
        { keeper_investigators: [alice, bob] },
        EVENT_FIXTURES.snapshot.world_id,
      );
    event("state_changed", {
      investigator_id: "bob",
      hp: 3,
      conditions: ["major_wound"],
    });
    const [a, b] = useStructuredStore.getState().keeperInvestigators;
    expect(a.hp).toBe(10);
    expect(b.hp).toBe(3);
    expect(b.san).toBe(60);
    expect(b.conditions).toEqual(["major_wound"]);
  });
  it("other people's inventory events cannot replace my backpack", () => {
    useStructuredStore.getState().applySnapshot(
      {
        investigator_id: "alice",
        items: alice.inventory,
        keeper_investigators: [alice, bob],
      },
      EVENT_FIXTURES.snapshot.world_id,
    );
    event("inventory_changed", {
      investigator_id: "bob",
      items: [{ id: "key", label: "钥匙", quantity: 2 }],
    });
    expect(useStructuredStore.getState().items[0].id).toBe("pen");
    expect(
      useStructuredStore.getState().keeperInvestigators[1].inventory[0].id,
    ).toBe("key");
    event("inventory_changed", { investigator_id: "alice", items: [] });
    expect(useStructuredStore.getState().items).toEqual([]);
    expect(
      useStructuredStore.getState().keeperInvestigators[0].inventory,
    ).toEqual([]);
  });
});
