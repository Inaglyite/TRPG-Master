import { beforeEach, describe, expect, it } from "vitest";
import { elapsedGameTime, readGameMinutes } from "../protocol/game-clock";
import { EVENT_FIXTURES, WORLD_ID } from "../protocol/structured-fixtures";
import { initialStructuredState, useStructuredStore } from "./structured-store";

beforeEach(() => useStructuredStore.setState({ ...initialStructuredState }));

describe("game clock is numeric committed authority only", () => {
  it.each([
    undefined,
    null,
    [],
    {},
    { elapsed_minutes: true },
    { elapsed_minutes: "60" },
    { elapsed_minutes: -1 },
    { elapsed_minutes: 0.1 },
    { elapsed_minutes: Infinity },
    { elapsed_minutes: 2 ** 53 },
    { elapsed_minutes: 20, secret: "deadline" },
  ])("rejects absent/invalid/extra fields: %j", (value) => {
    expect(readGameMinutes(value)).toBeNull();
  });
  it("does not convert missing recovery fields into zero or keep future time", () => {
    const store = useStructuredStore.getState();
    store.applySnapshot(
      { ...EVENT_FIXTURES.snapshot.payload, clock: { elapsed_minutes: 200 } },
      WORLD_ID,
    );
    expect(useStructuredStore.getState().clockMinutes).toBe(200);
    store.applySnapshot(
      { ...EVENT_FIXTURES.snapshot.payload, clock: { elapsed_minutes: 0 } },
      WORLD_ID,
    );
    expect(useStructuredStore.getState().clockMinutes).toBe(0);
    store.applySnapshot(EVENT_FIXTURES.snapshot.payload, WORLD_ID);
    expect(useStructuredStore.getState().clockMinutes).toBeNull();
  });
  it("formats elapsed duration, not dates or a ticking real-world clock", () => {
    expect(elapsedGameTime(0)).toBe("0分钟");
    expect(elapsedGameTime(200)).toBe("3小时20分钟");
    expect(elapsedGameTime(1440)).toBe("1天");
    expect(elapsedGameTime(1561)).toBe("1天2小时1分钟");
    expect(elapsedGameTime(null)).toBe("未提供");
  });
  it("only numeric clock events update time; prose/HP do not; binding clears it", () => {
    const store = useStructuredStore.getState();
    store.applySnapshot(
      { ...EVENT_FIXTURES.snapshot.payload, clock: { elapsed_minutes: 200 } },
      WORLD_ID,
    );
    store.applyEvent({
      ...EVENT_FIXTURES.stateChanged,
      payload: { clock: { elapsed_minutes: 240 } },
    });
    expect(useStructuredStore.getState().clockMinutes).toBe(240);
    store.applyEvent({
      ...EVENT_FIXTURES.stateChanged,
      payload: { hp: 3, description: "他说自己守了三天" },
    });
    expect(useStructuredStore.getState().clockMinutes).toBe(240);
    store.bindWorld("new-world");
    expect(useStructuredStore.getState().clockMinutes).toBeNull();
  });
});
