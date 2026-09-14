/**
 * 与后端联合验收的六个协作行为，前端这一侧的契约断言：
 *   1. 追问（正常回答并等待）不打断原交互：线程仍 open，不新建线程卡；
 *   2. 原动作完成/取消/替换才关闭「对应那一条」线程；
 *   3. 多线程互相独立：关掉 A 不影响 B（含别人的线程）；
 *   4. 刷新（快照恢复）与实时事件投影一致：不复活终态线程、不丢字段；
 *   5. 暂停/失败要离开「处理中」，且不留下悬空的公开待办；
 *   6. 线程只是记录：卡片不提供任何「执行」入口（不是执行授权）。
 *
 * 这里只验前端投影，不代替后端语义；后端语义由真实后端 E2E 与后端测试覆盖。
 */

import { beforeEach, describe, expect, it } from "vitest";

import { readInteractionThread } from "../protocol/structured";
import {
  activeRequests,
  initialStructuredState,
  openInteractions,
  useStructuredStore,
} from "./structured-store";

function envelope(type: string, payload: Record<string, unknown>, seq: number) {
  return {
    protocol_version: 1,
    event_id: seq,
    world_id: "world-1",
    sequence: seq,
    revision: seq,
    type,
    cause_request_id: null,
    payload,
  } as never;
}

async function apply(
  type: string,
  payload: Record<string, unknown>,
  seq: number,
) {
  const effectsModule = await import("../structured-effects");
  effectsModule.applyStructuredEffects(envelope(type, payload, seq));
}

/** 请求状态类事件由传输层直接进 store（effects 只做上下文投影）。 */
function applyRequestEvent(
  type: string,
  payload: Record<string, unknown>,
  seq: number,
) {
  useStructuredStore.getState().applyEvent(envelope(type, payload, seq));
}

function thread(overrides: Record<string, unknown>): Record<string, unknown> {
  return {
    thread_id: "thr_a",
    status: "open",
    investigator_id: "pc",
    pending_action: {
      kind: "move",
      destination_scene_id: "miskatonic_medical",
      note: "尚未出发前往医学院",
    },
    disclosed: ["遗体已移交家属或殡葬方"],
    waiting_on: "pc",
    note: "",
    origin_request_id: "req-wish",
    last_request_id: "req-wish",
    ...overrides,
  };
}

beforeEach(() => {
  useStructuredStore.setState({ ...initialStructuredState });
});

describe("行为 1：追问不打断原交互", () => {
  it("追问被正常回答后，原线程仍 open 且只有一张卡", async () => {
    await apply("interaction_updated", thread({}), 2);
    // 追问：玩家问「停尸间在哪一层」——主持只回答，不执行原动作
    await apply("interaction_updated", thread({}), 3);
    const state = useStructuredStore.getState();
    const open = openInteractions(state);
    expect(open).toHaveLength(1);
    expect(open[0].threadId).toBe("thr_a");
    expect(open[0].status).toBe("open");
    // 尚未执行这件事仍然写着（追问不该把它清掉）
    expect(open[0].pendingAction.note).toBe("尚未出发前往医学院");
    expect(open[0].disclosed).toEqual(["遗体已移交家属或殡葬方"]);
  });

  it("追问后主持显式 continue：同一线程换 last_request_id，不产生第二条线程", async () => {
    await apply("interaction_updated", thread({}), 2);
    await apply(
      "interaction_updated",
      thread({ last_request_id: "req-ask", revision: 3 }),
      3,
    );
    const open = openInteractions(useStructuredStore.getState());
    expect(open.map((t) => t.threadId)).toEqual(["thr_a"]);
    expect(open[0].lastRequestId).toBe("req-ask");
    expect(open[0].originRequestId).toBe("req-wish");
  });
});

describe("行为 2：原动作终态只关对应那一条线程", () => {
  it.each([
    ["completed", 4],
    ["cancelled", 5],
    ["superseded", 6],
  ])("线程终态 %s 不再出现在「当前交互」", async (status, seq) => {
    await apply("interaction_updated", thread({}), 2);
    expect(openInteractions(useStructuredStore.getState())).toHaveLength(1);
    await apply("interaction_updated", thread({ status, revision: seq }), seq);
    expect(openInteractions(useStructuredStore.getState())).toHaveLength(0);
  });
});

describe("行为 3：线程之间互相独立（含其他玩家的线程）", () => {
  it("关掉 A 不影响 B，也不影响别人的线程 C", async () => {
    await apply("interaction_updated", thread({ thread_id: "thr_a" }), 2);
    await apply(
      "interaction_updated",
      thread({
        thread_id: "thr_b",
        investigator_id: "pc",
        pending_action: { kind: "inspect", note: "尚未查看档案柜" },
        origin_request_id: "req-b",
      }),
      3,
    );
    await apply(
      "interaction_updated",
      thread({
        thread_id: "thr_other",
        investigator_id: "pc-2",
        waiting_on: "pc-2",
        pending_action: { kind: "move", note: "尚未出发前往图书馆" },
        origin_request_id: "req-other",
      }),
      4,
    );
    expect(openInteractions(useStructuredStore.getState())).toHaveLength(3);

    await apply(
      "interaction_updated",
      thread({ thread_id: "thr_a", status: "completed" }),
      5,
    );
    const left = openInteractions(useStructuredStore.getState());
    expect(left.map((t) => t.threadId).sort()).toEqual(["thr_b", "thr_other"]);
    // 别人的线程是服务端按受众下发的：前端不因为本地动作把它删掉
    expect(left.find((t) => t.threadId === "thr_other")?.investigatorId).toBe(
      "pc-2",
    );
  });

  it("同一线程的重复事件只留一张卡（去重后顺序稳定）", async () => {
    await apply("interaction_updated", thread({}), 2);
    await apply("interaction_updated", thread({ revision: 3 }), 3);
    await apply("interaction_updated", thread({ revision: 4 }), 4);
    const state = useStructuredStore.getState();
    expect(openInteractions(state).map((t) => t.threadId)).toEqual(["thr_a"]);
    expect(state.interactionOrder).toEqual(["thr_a"]);
  });
});

describe("行为 4：刷新与实时事件一致", () => {
  it("快照恢复的投影与实时事件投影逐字段相同", async () => {
    const threads = [
      thread({ thread_id: "thr_a" }),
      thread({
        thread_id: "thr_b",
        pending_action: { kind: "move", note: "尚未出发前往图书馆" },
        disclosed: [],
        last_request_id: "req-b",
      }),
    ];
    for (const [index, entry] of threads.entries()) {
      await apply("interaction_updated", entry, index + 2);
    }
    const live = openInteractions(useStructuredStore.getState());

    useStructuredStore.setState({ ...initialStructuredState });
    useStructuredStore
      .getState()
      .applySnapshot({ revision: 9, interactions: threads }, "world-1");
    const restored = openInteractions(useStructuredStore.getState());

    expect(restored).toEqual(live);
    expect(restored).toHaveLength(2);
  });

  it("刷新不会复活终态线程，也不会留下幽灵待办", async () => {
    const snapshot = [
      thread({ thread_id: "thr_a" }),
      thread({ thread_id: "thr_done", status: "completed" }),
      thread({ thread_id: "thr_gone", status: "cancelled" }),
    ];
    useStructuredStore.getState().applySnapshot(
      {
        revision: 9,
        interactions: snapshot,
        requests: [
          {
            request_id: "req-wish",
            status: "completed",
            outcome: "success",
            detail: "",
          },
        ],
      },
      "world-1",
    );
    const state = useStructuredStore.getState();
    expect(openInteractions(state).map((t) => t.threadId)).toEqual(["thr_a"]);
    // 已收尾的请求不得再长出「尚未执行」的公开待办
    expect(activeRequests(state).filter((r) => r.awaiting)).toHaveLength(0);
  });
});

describe("行为 5：暂停/失败离开「处理中」", () => {
  it("paused 状态终止等待并清掉公开待办", async () => {
    useStructuredStore.getState().registerOutgoing({
      requestId: "req-wish",
      kind: "freeform",
      label: "行动",
      payload: { text: "我想先看看尸体。" },
      digest: "d1",
    });
    applyRequestEvent(
      "action_status",
      {
        request_id: "req-wish",
        status: "awaiting_player",
        awaiting: {
          pending_action: { kind: "move", note: "尚未出发前往医学院" },
          disclosed: ["遗体已移交家属或殡葬方"],
          note: "等待玩家决定是否现在出发",
        },
      },
      2,
    );
    const before = activeRequests(useStructuredStore.getState())[0];
    expect(before.status).toBe("awaiting_player");
    expect(before.awaiting?.note).toBe("尚未出发前往医学院");

    applyRequestEvent(
      "action_status",
      {
        request_id: "req-wish",
        status: "paused",
        detail: "守秘人助手未配置模型服务（BYOK）。",
      },
      3,
    );
    const after = activeRequests(useStructuredStore.getState())[0];
    expect(after.status).toBe("paused");
    // 暂停不是「等你回应」：不能再显示成待办在等玩家执行
    expect(after.awaiting).toBeNull();
  });

  it("失败给出可读原因与重试入口，不假装成功也不永久转圈", async () => {
    useStructuredStore.getState().registerOutgoing({
      requestId: "req-wish",
      kind: "freeform",
      label: "行动",
      payload: { text: "我想先看看尸体。" },
      digest: "d1",
    });
    applyRequestEvent(
      "action_status",
      { request_id: "req-wish", status: "processing" },
      2,
    );
    expect(useStructuredStore.getState().requests["req-wish"].status).toBe(
      "processing",
    );

    // 服务端的失败通道是 request_error（拒绝也落 outbox）：retryable 时保留
    // 请求、给出原因与重试入口；不可重试时按 declined 收尾。
    applyRequestEvent(
      "request_error",
      {
        request_id: "req-wish",
        code: "model_unavailable",
        message: "模型服务不可用",
        retryable: true,
      },
      3,
    );
    const retryable = activeRequests(useStructuredStore.getState())[0];
    expect(retryable.errorCode).toBe("model_unavailable");
    expect(retryable.errorMessage).toBeTruthy();
    expect(retryable.awaitingAck).toBe(false);

    applyRequestEvent(
      "request_error",
      {
        request_id: "req-wish",
        code: "invalid_action",
        message: "动作不合法",
        retryable: false,
      },
      4,
    );
    const final = useStructuredStore.getState().requests["req-wish"];
    expect(final.errorCode).toBe("invalid_action");
    expect(final.status).toBe("declined");
    expect(final.awaitingAck).toBe(false);
  });
});

describe("行为 6：线程是记录，不是执行授权", () => {
  it("线程卡没有任何可点击的执行入口", async () => {
    await apply("interaction_updated", thread({}), 2);
    const parsed = readInteractionThread(thread({}));
    expect(parsed?.status).toBe("open");
    // 公开投影里没有「执行」这类字段：卡片只能显示，不能发起动作
    expect(Object.keys(parsed ?? {})).not.toContain("execute");
    expect(Object.keys(parsed ?? {})).not.toContain("authorized");
  });
});

describe("行为 7：快照里的可操作原因（paused/failed）不丢", () => {
  it("刷新后暂停原因仍在（快照 requests[].detail）", () => {
    useStructuredStore.getState().applySnapshot(
      {
        revision: 12,
        requests: [
          {
            request_id: "req-byok",
            status: "paused",
            summary: "行动",
            detail:
              "守秘人助手未配置模型服务（BYOK），请求已暂停；请房主配置模型或改由人类主持。",
          },
        ],
      },
      "world-1",
    );
    const request = useStructuredStore.getState().requests["req-byok"];
    expect(request.status).toBe("paused");
    expect(request.detail).toContain("BYOK");
    expect(request.awaitingAck).toBe(false);
  });

  it("快照里的终态请求不会复活「尚未执行」明细", () => {
    useStructuredStore.getState().applySnapshot(
      {
        revision: 13,
        requests: [
          {
            request_id: "req-done",
            status: "completed",
            summary: "行动",
            awaiting: {
              pending_action: { kind: "move", note: "尚未出发前往医学院" },
            },
          },
        ],
      },
      "world-1",
    );
    expect(
      useStructuredStore.getState().requests["req-done"].awaiting,
    ).toBeNull();
  });
});
