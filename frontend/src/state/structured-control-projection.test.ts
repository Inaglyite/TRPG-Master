import { beforeEach, expect, it } from "vitest";
import { EVENT_FIXTURES, WORLD_ID } from "../protocol/structured-fixtures";
import { initialStructuredState, useStructuredStore } from "./structured-store";

beforeEach(() => useStructuredStore.setState({ ...initialStructuredState }));

it("projects committed controller events without changing configured mode or granting membership", () => {
  const store = useStructuredStore.getState();
  store.applySnapshot(
    { ...EVENT_FIXTURES.snapshot.payload, keeper_mode: "agent", keeper: null },
    WORLD_ID,
  );
  store.applyEvent({
    ...EVENT_FIXTURES.snapshot,
    type: "keeper_control",
    event_id: 44,
    payload: {
      controller_epoch: 2,
      controller: { kind: "human", id: "me" },
      reason: "human_takeover",
    },
  });
  expect(useStructuredStore.getState().keeperControl).toMatchObject({
    controllerKind: "human",
    controllerId: "me",
    state: "takeover",
  });
  expect(useStructuredStore.getState().identity).toMatchObject({
    keeperUserId: "me",
    keeperMode: "agent",
  });
  store.applyEvent({
    ...EVENT_FIXTURES.snapshot,
    type: "keeper_control",
    event_id: 45,
    payload: {
      controller_epoch: 3,
      controller: { kind: "none", id: "unassigned" },
      reason: "returned_to_ai",
    },
  });
  expect(useStructuredStore.getState().keeperControl).toMatchObject({
    controllerKind: "none",
    controllerId: null,
  });
  expect(useStructuredStore.getState().identity.keeperUserId).toBeNull();
});

it("authoritative null snapshot clears stale ownership while partial snapshots preserve it", () => {
  const store = useStructuredStore.getState();
  store.applySnapshot(
    {
      ...EVENT_FIXTURES.snapshot.payload,
      keeper_mode: "human",
      keeper: { mode: "human", user_id: "local" },
    },
    WORLD_ID,
  );
  store.applySnapshot({ revision: 14 });
  expect(useStructuredStore.getState().keeperControl?.controllerId).toBe(
    "local",
  );
  store.applySnapshot({ keeper: null, keeper_mode: "agent" });
  expect(useStructuredStore.getState().identity.keeperUserId).toBeNull();
  expect(useStructuredStore.getState().keeperControl).toMatchObject({
    controllerId: null,
    controllerKind: "none",
  });
  expect(useStructuredStore.getState().identity.keeperMode).toBe("agent");
});

it("legacy diagnostic updates do not silently discard known current control", () => {
  const store = useStructuredStore.getState();
  store.applySnapshot(
    {
      ...EVENT_FIXTURES.snapshot.payload,
      keeper_mode: "agent",
      keeper: { mode: "human", user_id: "me" },
    },
    WORLD_ID,
  );
  store.applyEvent({
    ...EVENT_FIXTURES.snapshot,
    type: "keeper_control",
    event_id: 55,
    payload: {
      state: "paused",
      detail: "等待人工处理",
      takeover_available: true,
    },
  });
  expect(useStructuredStore.getState().keeperControl).toMatchObject({
    controllerId: "me",
    controllerKind: "human",
    state: "paused",
    detail: "等待人工处理",
  });
});
