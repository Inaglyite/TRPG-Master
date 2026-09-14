/**
 * 过渡回合的等待待办在客户端的落点：
 * - `action_status{status: awaiting_player, awaiting}` 进入卡片状态（尚未执行/已告知）；
 * - 终态（completed/cancelled/declined）清掉待办指纹，旧待办不会留在屏幕上；
 * - 刷新/重连时由 `session_snapshot.requests[]` 恢复公开待办；
 * - 内部等待命令（command_id 形式的 request_id）不该在玩家侧生成幽灵卡片。
 */

import { beforeEach, describe, expect, it } from "vitest";

import {
  initialStructuredState,
  readAwaitingTodo,
  useStructuredStore,
} from "./structured-store";

const AWAITING = {
  pending_action: {
    kind: "move",
    destination_scene_id: "morgue",
    note: "尚未出发前往停尸房",
  },
  disclosed: ["停尸房需要值班医生放行"],
  note: "等待玩家决定是否现在联系医生",
};

function statusEvent(
  requestId: string,
  status: string,
  extra: Record<string, unknown> = {},
) {
  return {
    protocol_version: 1,
    event_id: 10,
    world_id: "world-1",
    sequence: 3,
    revision: 1,
    type: "action_status",
    cause_request_id: null,
    payload: { request_id: requestId, status, ...extra },
  } as never;
}

beforeEach(() => {
  useStructuredStore.setState({ ...initialStructuredState });
});

describe("readAwaitingTodo", () => {
  it("解析服务端的待办结构", () => {
    const todo = readAwaitingTodo(AWAITING);
    expect(todo?.kind).toBe("move");
    expect(todo?.destinationSceneId).toBe("morgue");
    expect(todo?.disclosed).toEqual(["停尸房需要值班医生放行"]);
  });

  it("空对象不构成待办", () => {
    expect(readAwaitingTodo({})).toBeNull();
    expect(readAwaitingTodo(null)).toBeNull();
  });
});

describe("等待态在 store 里的生命周期", () => {
  it("本地请求登记后收到 awaiting_player 事件即进入等待态", () => {
    const store = useStructuredStore.getState();
    store.registerOutgoing({
      requestId: "req-1",
      kind: "freeform",
      label: "行动",
      payload: { text: "我想先看看尸体。" },
      digest: "d1",
    });
    useStructuredStore
      .getState()
      .applyEvent(
        statusEvent("req-1", "awaiting_player", { awaiting: AWAITING }),
      );
    const request = useStructuredStore.getState().requests["req-1"];
    expect(request.status).toBe("awaiting_player");
    expect(request.awaiting?.note).toBe("尚未出发前往停尸房");
    expect(request.awaiting?.disclosed).toEqual(["停尸房需要值班医生放行"]);
  });

  it("收尾后清掉待办指纹，旧待办不会再显示", () => {
    const store = useStructuredStore.getState();
    store.registerOutgoing({
      requestId: "req-1",
      kind: "freeform",
      label: "行动",
      payload: {},
      digest: "d1",
    });
    useStructuredStore
      .getState()
      .applyEvent(
        statusEvent("req-1", "awaiting_player", { awaiting: AWAITING }),
      );
    useStructuredStore.getState().applyEvent(statusEvent("req-1", "cancelled"));
    const request = useStructuredStore.getState().requests["req-1"];
    expect(request.status).toBe("cancelled");
    expect(request.awaiting).toBeNull();
  });

  it("内部等待命令的 request_id 不会生成玩家幽灵卡片", () => {
    useStructuredStore
      .getState()
      .applyEvent(
        statusEvent("await-req-1", "completed", { outcome: "success" }),
      );
    expect(
      useStructuredStore.getState().requests["await-req-1"],
    ).toBeUndefined();
  });
});

describe("刷新恢复", () => {
  it("快照里的 requests[] 恢复等待待办（含摘要与已告知条件）", () => {
    useStructuredStore.getState().applySnapshot(
      {
        revision: 4,
        execution_profile: "structured_v1",
        keeper_mode: "human",
        requests: [
          {
            request_id: "req-1",
            status: "awaiting_player",
            summary: "行动：我想先看看尸体。",
            awaiting: AWAITING,
          },
        ],
        pending_checks: [],
      },
      "world-1",
    );
    const request = useStructuredStore.getState().requests["req-1"];
    expect(request).toBeDefined();
    expect(request.status).toBe("awaiting_player");
    expect(request.label).toBe("行动：我想先看看尸体。");
    expect(request.awaiting?.note).toBe("尚未出发前往停尸房");
    expect(request.awaiting?.disclosed).toEqual(["停尸房需要值班医生放行"]);
  });

  it("快照里已收尾的请求不会保留待办", () => {
    useStructuredStore.getState().applySnapshot(
      {
        revision: 5,
        requests: [
          {
            request_id: "req-1",
            status: "awaiting_player",
            awaiting: AWAITING,
          },
        ],
        pending_checks: [],
      },
      "world-1",
    );
    useStructuredStore.getState().applySnapshot(
      {
        revision: 6,
        requests: [{ request_id: "req-1", status: "completed" }],
        pending_checks: [],
      },
      "world-1",
    );
    const request = useStructuredStore.getState().requests["req-1"];
    expect(request.status).toBe("completed");
    expect(request.awaiting).toBeNull();
  });
});
