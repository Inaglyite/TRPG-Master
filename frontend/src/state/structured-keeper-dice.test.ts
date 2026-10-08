import { beforeEach, expect, it } from "vitest";
import { parseServerMessage } from "../protocol/server-message";
import {
  keeperRollPayloadSchema,
  keeperRollReceiptSchema,
} from "../protocol/keeper-dice";
import {
  buildCommandRequest,
  type StructuredEventEnvelope,
} from "../protocol/structured";
import { initialStructuredState, useStructuredStore } from "./structured-store";

const receipt = {
  command_id: "roll-one",
  visibility: "keeper",
  expression: "2d6+3",
  dice: [{ sides: 6, values: [2, 5] }],
  total: 10,
  modifier: 3,
};
const event = {
  protocol_version: 1,
  world_id: "world",
  event_id: 1,
  sequence: 1,
  revision: 0,
  type: "keeper_roll_resolved",
  payload: receipt,
};
beforeEach(() => useStructuredStore.setState({ ...initialStructuredState }));

it("typed dice validates limits and refuses skill/HP injection and cause linkage", () => {
  expect(keeperRollPayloadSchema.safeParse({ spec: "1d100" }).success).toBe(
    true,
  );
  for (const spec of ["0d6", "11d6", "1d101", "1d1", "bad", "1d6+1000"]) {
    expect(keeperRollPayloadSchema.safeParse({ spec }).success).toBe(false);
  }
  expect(
    buildCommandRequest(
      "keeper_roll",
      { spec: "1d6", damage: 9 },
      { worldId: "world", expectedRevision: 0 },
    ).ok,
  ).toBe(false);
  expect(
    buildCommandRequest(
      "keeper_roll",
      { spec: "1d6" },
      { worldId: "world", expectedRevision: 0 },
      "dice-id",
      "player-request",
    ).ok,
  ).toBe(false);
  expect(
    keeperRollReceiptSchema.safeParse({ ...receipt, total: 99 }).success,
  ).toBe(false);
});

it("registered event updates one receipt, never creates or resolves a skill check", () => {
  expect(parseServerMessage(event)).not.toBeNull();
  const store = useStructuredStore.getState();
  store.applyEvent(event as StructuredEventEnvelope);
  store.applyEvent(event as StructuredEventEnvelope);
  expect(useStructuredStore.getState().keeperRolls).toEqual([receipt]);
  expect(useStructuredStore.getState().checks).toEqual({});
  expect(useStructuredStore.getState().requests).toEqual({});
  expect(useStructuredStore.getState().clockMinutes).toBeNull();
});

it("snapshot replaces rolled-back receipts, missing old fields and new worlds clear them", () => {
  const store = useStructuredStore.getState();
  store.applySnapshot({ world_id: "world", keeper_rolls: [receipt] }, "world");
  expect(useStructuredStore.getState().keeperRolls).toEqual([receipt]);
  store.applySnapshot({ keeper_rolls: [] }, "world");
  expect(useStructuredStore.getState().keeperRolls).toEqual([]);
  store.applyEvent(event as StructuredEventEnvelope);
  store.applySnapshot({}, "world");
  expect(useStructuredStore.getState().keeperRolls).toEqual([]);
  store.applyEvent(event as StructuredEventEnvelope);
  store.bindWorld("another");
  expect(useStructuredStore.getState().keeperRolls).toEqual([]);
});

it("bad result is not displayed and the history is bounded at 20", () => {
  useStructuredStore
    .getState()
    .applyEvent({
      ...event,
      payload: { ...receipt, total: 100 },
    } as StructuredEventEnvelope);
  expect(useStructuredStore.getState().keeperRolls).toEqual([]);
  for (let n = 0; n < 25; n++)
    useStructuredStore
      .getState()
      .applyEvent({
        ...event,
        event_id: n + 2,
        payload: { ...receipt, command_id: `roll-${n}` },
      } as StructuredEventEnvelope);
  expect(useStructuredStore.getState().keeperRolls).toHaveLength(20);
});
