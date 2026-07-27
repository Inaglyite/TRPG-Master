import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  acknowledgePendingAction,
  rollbackPendingAction,
  sendAction,
} from "./options";
import { useAppStore } from "./state/app-store";
import { useMessageStore } from "./state/message-store";
import { initialOnlineState, useOnlineStore } from "./state/online-store";
import { safeSend } from "./ws";

vi.mock("./ws", () => ({
  safeSend: vi.fn(),
}));

beforeEach(() => {
  acknowledgePendingAction();
  vi.clearAllMocks();
  useMessageStore.setState({ messages: [] });
  useOnlineStore.setState({ ...initialOnlineState });
  useAppStore.setState({
    mode: "local",
    inputEnabled: true,
    inputPlaceholder: "你决定做什么？",
    choices: [{ label: "检查门锁", isFree: false }],
    dialog: null,
    ending: {
      ending_type: "neutral",
      title: "尚未结束",
      summary: "",
    },
  });
});

describe("行动乐观 UI", () => {
  it("服务端拒绝后移除临时气泡和等待动画，并恢复输入、选项与结局", () => {
    sendAction("继续探索");
    expect(safeSend).toHaveBeenCalled();
    expect(useAppStore.getState()).toMatchObject({
      inputEnabled: false,
      choices: [],
      ending: null,
    });
    expect(
      useMessageStore
        .getState()
        .messages.some((message) => message.kind === "player"),
    ).toBe(true);

    expect(rollbackPendingAction()).toBe(true);

    expect(useAppStore.getState()).toMatchObject({
      inputEnabled: true,
      inputPlaceholder: "你决定做什么？",
      choices: [{ label: "检查门锁", isFree: false }],
      ending: {
        ending_type: "neutral",
        title: "尚未结束",
        summary: "",
      },
    });
    expect(
      useMessageStore
        .getState()
        .messages.some(
          (message) => message.kind === "player" || message.kind === "loading",
        ),
    ).toBe(false);
  });

  it("多人非当前行动者在发送函数入口也会被阻断", () => {
    useAppStore.setState({ mode: "online" });
    useOnlineStore.setState({
      authStatus: "authenticated",
      user: { id: "u1", username: "alice" },
      roomConnection: "connected",
      roomStatus: "playing",
      currentActorUserId: "u2",
      members: [{ user_id: "u1", username: "alice", role: "player" }],
    });

    sendAction("越权行动");

    expect(safeSend).not.toHaveBeenCalled();
    expect(useMessageStore.getState().messages).toEqual([]);
    expect(useOnlineStore.getState().roomError).toBe("还没有轮到你行动");
  });
});
