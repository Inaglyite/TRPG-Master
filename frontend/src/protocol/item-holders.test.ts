import { expect, it } from "vitest";
import { holderValue, readHolder, holdingsSchema } from "./item-holders";
import {
  buildKeeperPayload,
  findKeeperCommand,
  emptyKeeperValues,
  validateKeeperFields,
} from "./keeper-commands";
import {
  initialStructuredState,
  useStructuredStore,
} from "../state/structured-store";

const holdings = {
  holders: [
    { kind: "npc" as const, id: "keeper/npc", name: "老看守" },
    { kind: "scene" as const, id: "study", name: "书房" },
  ],
  items: [
    {
      id: "key",
      label: "私有钥匙",
      quantity: 1,
      holder: { kind: "npc" as const, id: "keeper/npc" },
    },
  ],
};
it("holder encoding retains complete IDs including slash and never treats scene objects as custodians", () => {
  expect(readHolder(holderValue(holdings.holders[0]))).toEqual({
    kind: "npc",
    id: "keeper/npc",
  });
  for (const value of ["npc", "npc/", "scene_object/door", "unknown/x", 123])
    expect(readHolder(value)).toBeNull();
});
it("all three holder kinds produce exact nested wire payload, not form-only fields", () => {
  const spec = findKeeperCommand("transfer_item")!;
  for (const kind of ["npc", "scene", "investigator"] as const) {
    const values = {
      ...emptyKeeperValues(spec),
      item_id: "key",
      quantity: "1",
      from_holder: holderValue(holdings.holders[0]),
      to_holder: holderValue({ kind, id: "target" }),
    };
    expect(validateKeeperFields(spec, values)).toEqual([]);
    expect(buildKeeperPayload(spec, values)).toEqual({
      item_id: "key",
      quantity: 1,
      from: { kind: "npc", id: "keeper/npc" },
      to: { kind, id: "target" },
    });
  }
});
it("invalid holder selection is not a valid transfer", () => {
  const spec = findKeeperCommand("transfer_item")!;
  expect(
    validateKeeperFields(spec, {
      ...emptyKeeperValues(spec),
      item_id: "key",
      quantity: "1",
      from_holder: "unresolved/text",
      to_holder: "npc/",
    }),
  ).not.toEqual([]);
});
it("legacy investigator form values still compile to the unchanged protocol payload", () => {
  const spec = findKeeperCommand("transfer_item")!;
  expect(
    buildKeeperPayload(spec, {
      item_id: "old-item",
      quantity: "1",
      from_investigator_id: "a",
      to_investigator_id: "b",
    }),
  ).toEqual({
    item_id: "old-item",
    quantity: 1,
    from: { kind: "investigator", id: "a" },
    to: { kind: "investigator", id: "b" },
  });
});
it("private holdings replace on event/refresh and clear on player snapshot and world change", () => {
  useStructuredStore.setState({ ...initialStructuredState });
  const store = useStructuredStore.getState();
  store.applySnapshot(
    { keeper_progress: { clues: [], clocks: [], holdings } },
    "world",
  );
  expect(useStructuredStore.getState().keeperProgress?.holdings).toEqual(
    holdings,
  );
  expect(useStructuredStore.getState().items).toEqual([]);
  store.applySnapshot({}, "world");
  expect(useStructuredStore.getState().keeperProgress).toBeNull();
  store.applySnapshot(
    { keeper_progress: { clues: [], clocks: [], holdings } },
    "world",
  );
  store.bindWorld("other");
  expect(useStructuredStore.getState().keeperProgress).toBeNull();
});
it("malformed quantities and custodian types fail closed; old projections remain readable", () => {
  expect(
    holdingsSchema.safeParse({
      ...holdings,
      items: [{ ...holdings.items[0], quantity: 0 }],
    }).success,
  ).toBe(false);
  expect(
    holdingsSchema.safeParse({
      ...holdings,
      holders: [{ kind: "unresolved", id: "x", name: "x" }],
    }).success,
  ).toBe(false);
});
