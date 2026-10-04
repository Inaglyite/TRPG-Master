import { beforeEach, describe, expect, it } from "vitest";
import { initialStructuredState, useStructuredStore } from "./structured-store";

function event(type: string, payload: Record<string, unknown>) {
  return {
    type,
    payload,
    world_id: "world-receipt",
    protocol_version: 1,
    event_id: 1,
    sequence: 1,
    revision: 1,
    cause_request_id: null,
  } as never;
}

beforeEach(() => {
  useStructuredStore.setState({ ...initialStructuredState });
  useStructuredStore.getState().bindWorld("world-receipt");
});

function outgoing() {
  useStructuredStore.getState().registerOutgoing({
    requestId: "move-request",
    kind: "move",
    label: "前往医院",
    payload: { action: { kind: "move", destination_scene_id: "hospital" } },
    digest: "test-digest",
  });
  useStructuredStore.getState().markSent("move-request");
}

describe("收件证据与行动结果分开", () => {
  it("校验拒绝不是进入待办；收到错误后不能声称守秘人在排队处理", () => {
    outgoing();
    useStructuredStore.getState().applyEvent(
      event("request_error", {
        request_id: "move-request",
        code: "revision_conflict",
        message: "世界已变化",
        retryable: true,
      }),
    );
    expect(
      useStructuredStore.getState().requests["move-request"],
    ).toMatchObject({
      serverReceived: false,
      errorCode: "revision_conflict",
      outcome: null,
    });
  });
  it("同一幂等请求的手动重发不抹掉已经获得的收件证据", () => {
    outgoing();
    useStructuredStore
      .getState()
      .applyEvent(
        event("action_ack", { request_id: "move-request", status: "queued" }),
      );
    useStructuredStore.getState().noteRetry("move-request");
    expect(
      useStructuredStore.getState().requests["move-request"].serverReceived,
    ).toBe(true);
  });
  it("发送和别的请求回执都不能确认这条请求已收件", () => {
    outgoing();
    useStructuredStore
      .getState()
      .applyEvent(
        event("action_ack", { request_id: "someone-else", status: "queued" }),
      );
    expect(
      useStructuredStore.getState().requests["move-request"],
    ).toMatchObject({ serverReceived: false, status: "queued", outcome: null });
  });
  it("对应 ack 表示已收件而非已执行；重发不篡改已有收件证据", () => {
    outgoing();
    useStructuredStore
      .getState()
      .applyEvent(
        event("action_ack", { request_id: "move-request", status: "queued" }),
      );
    useStructuredStore.getState().markSent("move-request");
    useStructuredStore.getState().markAwaitingAck("move-request");
    expect(
      useStructuredStore.getState().requests["move-request"],
    ).toMatchObject({
      serverReceived: true,
      status: "queued",
      outcome: null,
      awaitingAck: false,
    });
  });
  it("权威状态即使 ack 丢失也可确认收件，不用修成完成", () => {
    outgoing();
    useStructuredStore.getState().applyEvent(
      event("action_status", {
        request_id: "move-request",
        status: "processing",
      }),
    );
    expect(
      useStructuredStore.getState().requests["move-request"],
    ).toMatchObject({
      serverReceived: true,
      status: "processing",
      outcome: null,
    });
  });
  it("刷新快照确认既有待办，清掉超时查询态，行动仍 queued", () => {
    outgoing();
    useStructuredStore.getState().markAwaitingAck("move-request");
    useStructuredStore
      .getState()
      .applySnapshot(
        { requests: [{ request_id: "move-request", status: "queued" }] },
        "world-receipt",
      );
    expect(
      useStructuredStore.getState().requests["move-request"],
    ).toMatchObject({
      serverReceived: true,
      status: "queued",
      awaitingAck: false,
      outcome: null,
    });
  });
  it("首次快照与主持 intent_pending 都带显式收件证据", () => {
    useStructuredStore
      .getState()
      .applySnapshot(
        { requests: [{ request_id: "restored", status: "queued" }] },
        "world-receipt",
      );
    useStructuredStore.getState().applyEvent(
      event("intent_pending", {
        request_id: "host-pending",
        summary: "玩家想走",
        action: { kind: "move", destination_scene_id: "hospital" },
      }),
    );
    expect(useStructuredStore.getState().requests.restored.serverReceived).toBe(
      true,
    );
    expect(
      useStructuredStore.getState().requests["host-pending"].serverReceived,
    ).toBe(true);
  });
});
