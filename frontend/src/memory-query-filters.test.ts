import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  buildMemoryQuery,
  memoryQueryFilterError,
  readInteractionThread,
} from "./protocol/structured";
import { EVENT_FIXTURES } from "./protocol/structured-fixtures";
import { useAppStore } from "./state/app-store";
import {
  initialStructuredState,
  useStructuredStore,
} from "./state/structured-store";
import {
  handleStructuredPayload,
  resetStructuredTransport,
  sendMemoryQuery,
  setStructuredSender,
  MEMORY_QUERY_TIMEOUT_MS,
  rebindStructuredWorld,
} from "./structured-transport";

beforeEach(() => {
  resetStructuredTransport();
  useStructuredStore.setState({ ...initialStructuredState });
  useAppStore.setState({ mode: "local", connection: "connected" });
  handleStructuredPayload(EVENT_FIXTURES.snapshot);
  useStructuredStore.setState((state) => ({
    capabilities: { ...state.capabilities, memoryQuery: true },
  }));
});
afterEach(() => {
  resetStructuredTransport();
  vi.useRealTimers();
});

describe("memory query filters preserve meaning", () => {
  it("switching worlds clears old private memory results and interaction projections", () => {
    setStructuredSender(() => true);
    const result = sendMemoryQuery({ text: "旧伤" });
    if (!result.ok) throw new Error(result.reason);
    useStructuredStore.getState().applyMemoryQueryResult({
      query_id: result.requestId,
      memories: [
        {
          memory_id: "old-memory",
          character_id: "npc",
          knowledge_type: "told",
          content: "上一局秘密",
          topics: [],
        },
      ],
    });
    expect(useStructuredStore.getState().memoryQuery.entries).toHaveLength(1);
    const thread = readInteractionThread({
      thread_id: "old-thread",
      status: "open",
      investigator_id: "pc",
      pending_action: {
        kind: "move",
        note: "上一局待办",
        destination_scene_id: "old-scene",
      },
    });
    if (!thread) throw new Error("valid old thread required");
    useStructuredStore.getState().upsertInteraction(thread);
    expect(useStructuredStore.getState().interactionOrder).toEqual([
      "old-thread",
    ]);
    rebindStructuredWorld("another-world");
    expect(useStructuredStore.getState().memoryQuery).toMatchObject({
      status: "idle",
      queryId: "",
      entries: [],
      filters: {},
    });
    expect(useStructuredStore.getState().interactionOrder).toEqual([]);
    useStructuredStore.getState().applyMemoryQueryResult({
      query_id: result.requestId,
      memories: [{ memory_id: "late-memory", content: "迟到的旧秘密" }],
    });
    expect(useStructuredStore.getState().memoryQuery.entries).toHaveLength(0);
  });
  it("reconnecting the same world does not leave a query spinning after its timer is cleared", () => {
    setStructuredSender(() => true);
    expect(sendMemoryQuery({ text: "医生" }).ok).toBe(true);
    rebindStructuredWorld(useStructuredStore.getState().identity.worldId);
    expect(useStructuredStore.getState().memoryQuery).toMatchObject({
      status: "failed",
      error: expect.stringContaining("重新同步"),
    });
  });
  it("times out a missing read-only result without resending or changing worlds", () => {
    vi.useFakeTimers();
    const frames: unknown[] = [];
    setStructuredSender((frame) => {
      frames.push(frame);
      return true;
    });
    expect(sendMemoryQuery({ text: "旧伤" }).ok).toBe(true);
    const identity = useStructuredStore.getState().identity;
    vi.advanceTimersByTime(MEMORY_QUERY_TIMEOUT_MS);
    expect(useStructuredStore.getState().memoryQuery).toMatchObject({
      status: "failed",
      error: expect.stringContaining("超时"),
    });
    expect(frames).toHaveLength(1);
    expect(useStructuredStore.getState().identity).toEqual(identity);
  });
  it("does not turn a matching result into a timeout or accept a stale query result", () => {
    vi.useFakeTimers();
    setStructuredSender(() => true);
    const first = sendMemoryQuery({ text: "医生" });
    const second = sendMemoryQuery({ text: "旧伤" });
    if (!first.ok || !second.ok) throw new Error("query should be submitted");
    const store = useStructuredStore.getState();
    store.applyMemoryQueryResult({ query_id: first.requestId, memories: [] });
    expect(useStructuredStore.getState().memoryQuery.status).toBe("querying");
    store.applyMemoryQueryResult({ query_id: second.requestId, memories: [] });
    vi.advanceTimersByTime(MEMORY_QUERY_TIMEOUT_MS);
    expect(useStructuredStore.getState().memoryQuery.status).toBe("done");
  });
  it("a server rejection ends a pending memory query with the real explanation", () => {
    setStructuredSender(() => true);
    const result = sendMemoryQuery({ text: "旧伤" });
    if (!result.ok) throw new Error(result.reason);
    handleStructuredPayload({
      ...EVENT_FIXTURES.snapshot,
      type: "request_error",
      event_id: 999,
      cause_request_id: result.requestId,
      payload: {
        request_id: result.requestId,
        message: "主持授权已撤销",
        code: "not_authorized",
      },
    });
    expect(useStructuredStore.getState().memoryQuery).toMatchObject({
      status: "failed",
      error: "主持授权已撤销",
    });
  });
  it("copies all topics without truncation or sharing the caller's array", () => {
    const topics = ["一", "二", "三", "四", "五", "六"];
    const frame = buildMemoryQuery("query-1", { topics });
    topics.push("七");
    expect(frame.filters).toEqual({
      topics: ["一", "二", "三", "四", "五", "六"],
    });
  });
  it("counts Unicode code points like JSON Schema, including the 200-character boundary", () => {
    expect(memoryQueryFilterError({ text: "🕵".repeat(200) })).toBeNull();
    expect(memoryQueryFilterError({ text: "🕵".repeat(201) })).toContain("200");
    expect(memoryQueryFilterError({ topics: ["伤".repeat(40)] })).toBeNull();
    expect(memoryQueryFilterError({ topics: ["伤".repeat(41)] })).toContain(
      "40",
    );
  });
  it.each([
    { topics: ["一", "二", "三", "四", "五", "六", "七"] },
    { text: "字".repeat(201) },
    { limit: 21 },
    { limit: 1.5 },
    { charBudget: 199 },
    { characterId: "" },
  ])(
    "rejects invalid filters before sending or replacing query state: %j",
    (filters) => {
      const frames: unknown[] = [];
      setStructuredSender((frame) => {
        frames.push(frame);
        return true;
      });
      const before = useStructuredStore.getState().memoryQuery;
      expect(sendMemoryQuery(filters)).toMatchObject({ ok: false });
      expect(frames).toHaveLength(0);
      expect(useStructuredStore.getState().memoryQuery).toBe(before);
    },
  );
});
