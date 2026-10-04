import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { EVENT_FIXTURES, WORLD_ID } from "./protocol/structured-fixtures";
import { buildCancelRequest, cancelRequestSchema } from "./protocol/structured";
import { useAppStore } from "./state/app-store";
import { initialOnlineState, useOnlineStore } from "./state/online-store";
import {
  initialStructuredState,
  useStructuredStore,
} from "./state/structured-store";
import {
  handleStructuredPayload,
  ACK_TIMEOUT_MS,
  resetStructuredTransport,
  resendStructuredRequest,
  sendStructuredAction,
  sendCancelAction,
  setStructuredSender,
  resubmitWithFreshRevision,
} from "./structured-transport";

let frames: Record<string, unknown>[];
beforeEach(() => {
  resetStructuredTransport();
  useStructuredStore.setState({ ...initialStructuredState });
  useOnlineStore.setState({ ...initialOnlineState });
  useAppStore.setState({
    mode: "local",
    connection: "connected",
    activeWorldId: WORLD_ID,
  });
  frames = [];
  setStructuredSender((payload) => {
    frames.push(payload as Record<string, unknown>);
    return true;
  });
  handleStructuredPayload(EVENT_FIXTURES.snapshot);
});
afterEach(() => {
  resetStructuredTransport();
  vi.useRealTimers();
});

function queue() {
  const result = sendStructuredAction({
    kind: "freeform",
    text: "我想去调查图书馆。",
  });
  if (!result.ok) throw new Error(result.reason);
  const state = useStructuredStore.getState();
  useStructuredStore.setState({
    requests: {
      ...state.requests,
      [result.requestId]: {
        ...state.requests[result.requestId],
        serverReceived: true,
      },
    },
  });
  return result.requestId;
}

describe("player cancellation and guarded recovery", () => {
  it("uses a distinct, schema-valid cancel ID and does not optimistically cancel the original", () => {
    const originalId = queue();
    const result = sendCancelAction(originalId);
    expect(result.ok).toBe(true);
    expect(frames.at(-1)).toMatchObject({
      type: "cancel_request",
      target_request_id: originalId,
      world_id: WORLD_ID,
    });
    expect(frames.at(-1)?.request_id).not.toBe(originalId);
    expect(cancelRequestSchema.safeParse(frames.at(-1)).success).toBe(true);
    expect(frames.at(-1)).not.toHaveProperty("investigator_id");
    expect(useStructuredStore.getState().requests[originalId].status).toBe(
      "queued",
    );
    const before = frames.length;
    expect(sendCancelAction(originalId).ok).toBe(false);
    expect(frames).toHaveLength(before);
  });
  it("cannot cancel processing or awaiting actions", () => {
    const id = queue();
    for (const status of [
      "processing",
      "awaiting_player",
      "completed",
    ] as const) {
      const state = useStructuredStore.getState();
      useStructuredStore.setState({
        requests: {
          ...state.requests,
          [id]: { ...state.requests[id], status },
        },
      });
      expect(sendCancelAction(id).ok).toBe(false);
    }
    expect(frames).toHaveLength(1);
  });
  it("cannot send replay or cancellation while disconnected", () => {
    const id = queue();
    useAppStore.setState({ connection: "disconnected" });
    expect(resendStructuredRequest(id).ok).toBe(false);
    expect(sendCancelAction(id).ok).toBe(false);
    expect(frames).toHaveLength(1);
  });
  it("the automatic acknowledgement retry obeys the same connection gate", () => {
    vi.useFakeTimers();
    const result = sendStructuredAction({
      kind: "freeform",
      text: "等待主持处理。",
    });
    expect(result.ok).toBe(true);
    useAppStore.setState({ connection: "disconnected" });
    vi.advanceTimersByTime(ACK_TIMEOUT_MS + 1);
    expect(frames).toHaveLength(1);
    if (result.ok)
      expect(
        useStructuredStore.getState().requests[result.requestId].awaitingAck,
      ).toBe(true);
  });
  it("cannot replay an old world or another character under the current identity", () => {
    const id = queue();
    const identity = useStructuredStore.getState().identity;
    useStructuredStore.setState({
      identity: { ...identity, worldId: "replacement-world" },
    });
    expect(resendStructuredRequest(id).ok).toBe(false);
    expect(resubmitWithFreshRevision(id)).toMatchObject({ ok: false });
    useStructuredStore.setState({
      identity: { ...identity, investigatorId: "other-investigator" },
    });
    expect(resendStructuredRequest(id).ok).toBe(false);
    expect(sendCancelAction(id).ok).toBe(false);
    expect(frames).toHaveLength(1);
  });
  it("can cancel an own queued action restored from an authoritative snapshot without fabricating its text", () => {
    const state = useStructuredStore.getState();
    state.applySnapshot(
      {
        ...EVENT_FIXTURES.snapshot.payload,
        requests: [
          {
            request_id: "restored-action",
            request_type: "action_request",
            investigator_id: state.identity.investigatorId,
            status: "queued",
            summary: "行动待办",
          },
        ],
      },
      WORLD_ID,
    );
    expect(sendCancelAction("restored-action").ok).toBe(true);
    expect(resendStructuredRequest("restored-action").ok).toBe(false);
    expect(frames).toHaveLength(1);
  });
  it("keeps an ordinary replay byte-identical rather than generating a new action", () => {
    const id = queue();
    expect(resendStructuredRequest(id).ok).toBe(true);
    expect(frames[1]).toEqual(frames[0]);
  });
  it("an explicitly rejected revision conflict can become a new request after synchronization, not an old-ID replay", () => {
    const id = queue();
    const store = useStructuredStore.getState();
    store.applyRequestError(id, "revision_conflict", "世界版本已变化。", false);
    store.setRevision(store.identity.revision + 1);
    expect(resendStructuredRequest(id).ok).toBe(false);
    const fresh = resubmitWithFreshRevision(id);
    expect(fresh?.ok).toBe(true);
    expect(frames).toHaveLength(2);
    expect(frames[1].request_id).not.toBe(id);
    expect(frames[1].action).toEqual(frames[0].action);
  });
  it("an ordinary cloud player can resubmit a rejected action after synchronization without keeper authority", () => {
    useAppStore.setState({ mode: "online" });
    useOnlineStore.setState({
      user: { id: "ordinary-player", username: "玩家" },
      activeInvestigatorId:
        useStructuredStore.getState().identity.investigatorId,
      members: [
        {
          user_id: "ordinary-player",
          username: "玩家",
          role: "player",
          can_keeper: false,
        },
      ],
    });
    const id = queue();
    const store = useStructuredStore.getState();
    store.applyRequestError(id, "revision_conflict", "世界版本已变化。", false);
    store.setRevision(store.identity.revision + 1);
    expect(resubmitWithFreshRevision(id)).toMatchObject({ ok: true });
    expect(frames).toHaveLength(2);
    expect(frames[1].type).toBe("action_request");
    expect(frames[1].request_id).not.toBe(id);
    expect(frames[1].investigator_id).toBe(frames[0].investigator_id);
    expect(frames[1].action).toEqual(frames[0].action);
  });
  it("a cloud player changed to viewer cannot resubmit or invalidate the original conflict", () => {
    const id = queue();
    const store = useStructuredStore.getState();
    store.applyRequestError(id, "revision_conflict", "世界版本已变化。", false);
    store.setRevision(store.identity.revision + 1);
    useAppStore.setState({ mode: "online" });
    useOnlineStore.setState({
      user: { id: "ordinary-player", username: "玩家" },
      members: [
        {
          user_id: "ordinary-player",
          username: "玩家",
          role: "viewer",
          can_keeper: false,
        },
      ],
    });
    expect(resubmitWithFreshRevision(id)).toMatchObject({
      ok: false,
      reason: expect.stringContaining("旁观模式"),
    });
    expect(frames).toHaveLength(1);
    expect(useStructuredStore.getState().requests[id].errorCode).toBe(
      "revision_conflict",
    );
  });
  it("rejects invalid cancel targets and does not smuggle fields into the frozen schema", () => {
    const identity = useStructuredStore.getState().identity;
    expect(
      buildCancelRequest("", {
        ...identity,
        expectedRevision: identity.revision,
      }).ok,
    ).toBe(false);
    expect(
      cancelRequestSchema.safeParse({ ...frames[0], unexpected: true }).success,
    ).toBe(false);
  });
});
