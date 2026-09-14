import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  EVENT_FIXTURES,
  STRUCTURED_CAPABILITIES_WIRE,
  WORLD_ID,
} from "./protocol/structured-fixtures";
import { sendStructuredAction } from "./structured-transport";
import { resetStructuredTransport } from "./structured-transport";
import { useAppStore } from "./state/app-store";
import { useMessageStore } from "./state/message-store";
import { useSceneStore } from "./state/scene-store";
import {
  initialStructuredState,
  useStructuredStore,
} from "./state/structured-store";
import { handleServerPayload, setActiveTransport } from "./ws";

vi.mock("./renderer", async () => {
  const actual =
    await vi.importActual<typeof import("./renderer")>("./renderer");
  return {
    ...actual,
    onDice: vi.fn(),
    onNarrativeChunk: vi.fn(),
    onNarrativeSegment: vi.fn(),
  };
});

function lastSystemMessage(): string {
  const messages = useMessageStore.getState().messages;
  return String(messages[messages.length - 1]?.text ?? "");
}

beforeEach(() => {
  resetStructuredTransport();
  useStructuredStore.setState({ ...initialStructuredState });
  useMessageStore.setState({ messages: [] });
  useSceneStore.getState().reset();
  useAppStore.setState({
    mode: "local",
    connection: "connected",
    inputEnabled: true,
  });
});

describe("结构化帧的路由", () => {
  it("session_snapshot 写入 store 且不进入旧回合链路", () => {
    handleServerPayload(EVENT_FIXTURES.snapshot);
    const state = useStructuredStore.getState();
    expect(state.identity.worldId).toBe(WORLD_ID);
    expect(state.identity.revision).toBe(12);
    expect(state.clues.length).toBeGreaterThan(0);
    expect(state.destinations.length).toBeGreaterThan(0);
    // 旧链路不会因此产生任何聊天消息。
    expect(useMessageStore.getState().messages).toHaveLength(0);
  });

  it("scene_changed 复用场景指示器更新位置（只有已提交事件会改）", () => {
    handleServerPayload(EVENT_FIXTURES.snapshot);
    expect(useSceneStore.getState().name).toBe("密斯卡托尼克大学");
    handleServerPayload(EVENT_FIXTURES.sceneChanged);
    expect(useSceneStore.getState().name).toBe("密斯卡托尼克大学医学院");
    expect(useSceneStore.getState().status).toBe("known");
  });

  it("action_status 更新待办状态，不产生“行动成功”的聊天断言", () => {
    handleServerPayload(EVENT_FIXTURES.snapshot);
    handleServerPayload(EVENT_FIXTURES.actionAck);
    handleServerPayload(EVENT_FIXTURES.actionCompleted);
    expect(useMessageStore.getState().messages).toHaveLength(0);
  });

  it("协议版本不同：给出明确提示，不回退成文字发送", () => {
    handleServerPayload({ ...EVENT_FIXTURES.snapshot, protocol_version: 7 });
    expect(useStructuredStore.getState().protocolNotice).toContain(
      "不同版本的协议",
    );
    expect(lastSystemMessage()).toContain("已停止结构化提交");
  });

  it("重复投递同一事件只应用一次（按 event_id 去重）", () => {
    handleServerPayload(EVENT_FIXTURES.snapshot);
    const firstCount = useStructuredStore.getState().clues.length;
    handleServerPayload(EVENT_FIXTURES.snapshot);
    expect(useStructuredStore.getState().clues.length).toBe(firstCount);
  });

  it("旧协议消息仍走旧链路（结构化路由不吞掉它们）", () => {
    handleServerPayload({ type: "case_settled", ok: true });
    expect(useMessageStore.getState().messages.length).toBeGreaterThan(0);
  });

  it("未知的普通消息类型仍然被明确拒绝，不会静默丢弃", () => {
    handleServerPayload({ type: "future_client_message" });
    expect(lastSystemMessage()).toContain("无法识别的协议消息");
  });

  it("结构化事件缺少世界标识时不会被误判为结构化帧", () => {
    handleServerPayload({
      type: "action_status",
      event_id: 3,
      revision: 1,
      payload: {},
    });
    // 没有 world_id：不能当作结构化事件处理，也不能崩掉。
    expect(useStructuredStore.getState().requests).toEqual({});
  });

  it("连接建立后结构化发送有出口（发送器已接线，不会静默失败）", () => {
    // 连接建立（或房间 transport 装配）时会接线；这里直接复用同一入口。
    setActiveTransport({ send: () => undefined });
    useStructuredStore
      .getState()
      .applyCapabilities(STRUCTURED_CAPABILITIES_WIRE);
    useStructuredStore
      .getState()
      .applySnapshot(
        EVENT_FIXTURES.snapshot.payload as Record<string, unknown>,
        WORLD_ID,
      );
    const result = sendStructuredAction({
      kind: "move",
      destination_scene_id: "miskasstonic_medical",
    });
    // 未连接时 safeSend 会排队而不是丢弃，因此必须算“已受理”。
    expect(result.ok).toBe(true);
  });

  it("世界切换后旧世界的结构化事件不污染新世界", () => {
    handleServerPayload(EVENT_FIXTURES.snapshot);
    handleServerPayload(EVENT_FIXTURES.sceneChanged);
    expect(useSceneStore.getState().name).toBe("密斯卡托尼克大学医学院");

    // 模拟切世界：rememberWorld 会重绑结构化游标。
    handleServerPayload({
      type: "world_context",
      world_id: "world-next",
      module_name: "猩红文档",
      scene: { name: "入口大厅" },
    });
    useSceneStore.getState().applyScene("world-next", { name: "入口大厅" });

    handleServerPayload({ ...EVENT_FIXTURES.sceneChanged, event_id: 99 });
    expect(useSceneStore.getState().name).toBe("入口大厅");
    expect(useStructuredStore.getState().identity.worldId).toBe("world-next");
  });
});
