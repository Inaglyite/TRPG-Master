import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  acknowledgePendingAction,
  onDecision,
  rollbackPendingAction,
  sendAction,
  sendDecisionReply,
} from "./options";
import { useAppStore } from "./state/app-store";
import { useMessageStore } from "./state/message-store";
import { initialOnlineState, useOnlineStore } from "./state/online-store";
import {
  finishNarrativeStream,
  flushNarrativeStream,
  onNarrativeChunk,
  onNarrativeSegments,
} from "./renderer";
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
  it("聊天式行动预演使用底部选项而不打开弹窗", () => {
    onDecision({
      id: "action-preview-1",
      kind: "action_preview",
      presentation: "chat",
      title: "前往之前",
      options: [
        { id: "continue_action", label: "仍然前往" },
        { id: "cancel_action", label: "暂时不去" },
      ],
    });

    expect(useAppStore.getState().dialog).toBeNull();
    expect(useAppStore.getState().inputEnabled).toBe(false);
    expect(useAppStore.getState().choices).toEqual([
      {
        label: "仍然前往",
        isFree: false,
        description: "",
        decisionId: "action-preview-1",
        decisionOptionId: "continue_action",
      },
      {
        label: "暂时不去",
        isFree: false,
        description: "",
        decisionId: "action-preview-1",
        decisionOptionId: "cancel_action",
      },
    ]);

    sendDecisionReply("action-preview-1", "continue_action", "仍然前往");

    expect(safeSend).toHaveBeenCalledWith(
      JSON.stringify({
        type: "decision_reply",
        decision_id: "action-preview-1",
        option_id: "continue_action",
      }),
    );
    expect(useAppStore.getState().choices).toEqual([]);
    expect(
      useMessageStore
        .getState()
        .messages.some(
          (message) => message.kind === "player" && message.text === "仍然前往",
        ),
    ).toBe(true);
  });

  it("攻击无辜者确认也使用底部聊天选项", () => {
    onDecision({
      id: "violence-preview-1",
      kind: "irreversible_violence",
      presentation: "chat",
      options: [
        { id: "cancel_violence", label: "收起武器" },
        { id: "confirm_violence", label: "仍然攻击" },
      ],
    });

    expect(useAppStore.getState().dialog).toBeNull();
    expect(
      useAppStore.getState().choices.map((choice) => choice.decisionOptionId),
    ).toEqual(["cancel_violence", "confirm_violence"]);

    sendDecisionReply("violence-preview-1", "cancel_violence", "收起武器");

    expect(
      useMessageStore
        .getState()
        .messages.some(
          (message) => message.kind === "player" && message.text === "收起武器",
        ),
    ).toBe(true);
  });

  it("聊天决定会封口提醒气泡，确认后的叙事另起气泡", () => {
    onNarrativeChunk("想看遗体？我先替你知会医生。", "bryce_fallon");
    onNarrativeChunk("『你可以立即前往，也可以先向法伦了解更多情况。』");
    onDecision({
      id: "preview-boundary",
      kind: "action_preview",
      presentation: "chat",
      options: [
        { id: "continue_action", label: "仍然前往" },
        { id: "cancel_action", label: "暂时不去" },
      ],
    });
    sendDecisionReply("preview-boundary", "continue_action", "仍然前往");
    onNarrativeChunk("你随后抵达停尸房。");
    onNarrativeChunk("“法伦主任说你想亲眼看看。”", "john_whitcroft");
    onNarrativeSegments([
      {
        kind: "speech",
        text: "想看遗体？我先替你知会医生。",
        npc_id: "bryce_fallon",
        speaker: { type: "npc", id: "bryce_fallon", name: "布莱斯·法伦" },
      },
      {
        kind: "narration",
        text: "『你可以立即前往，也可以先向法伦了解更多情况。』",
      },
      { kind: "narration", text: "你随后抵达停尸房。" },
      {
        kind: "speech",
        text: "“法伦主任说你想亲眼看看。”",
        npc_id: "john_whitcroft",
        speaker: {
          type: "npc",
          id: "john_whitcroft",
          name: "约翰·惠特克罗夫特医生",
        },
      },
    ]);
    flushNarrativeStream();

    const messages = useMessageStore.getState().messages;
    expect(messages.map((message) => message.kind)).toEqual([
      "gm",
      "player",
      "gm",
    ]);
    expect(messages[0].segments?.map((segment) => segment.kind)).toEqual([
      "speech",
      "narration",
    ]);
    expect(messages[0].segments?.[1].speaker).toBeUndefined();
    expect(messages[2].segments?.map((segment) => segment.kind)).toEqual([
      "narration",
      "speech",
    ]);
    expect(messages[2].segments?.[1].speaker?.id).toBe("john_whitcroft");
    expect(messages[2].text).not.toContain("想看遗体");
    expect(messages[2].text).not.toContain("你可以立即前往");
    finishNarrativeStream();
  });

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
