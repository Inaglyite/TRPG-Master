import { beforeEach, describe, expect, it } from "vitest";

import { acknowledgePendingAction, sendAction } from "./options";
import { useAppStore } from "./state/app-store";
import { useMessageStore } from "./state/message-store";
import { handleServerPayload } from "./ws";

describe("system_notice 系统提示", () => {
  beforeEach(() => {
    useMessageStore.setState({ messages: [] });
  });

  it("渲染为聊天区系统消息（玩家 /skill 命令结果等）", () => {
    handleServerPayload({
      type: "system_notice",
      message: "已加载技能「keeper.magic」，守秘人从下一回合开始应用。",
    });
    const messages = useMessageStore.getState().messages;
    expect(messages[messages.length - 1]).toMatchObject({
      kind: "system",
      text: "已加载技能「keeper.magic」，守秘人从下一回合开始应用。",
    });
  });
});

it("本地行动在开回合前被拒绝时恢复输入，允许下一次提交", () => {
  acknowledgePendingAction();
  useMessageStore.setState({ messages: [] });
  useAppStore.setState({
    mode: "local",
    inputEnabled: true,
    choices: [{ label: "查看书桌", isFree: false }],
  });
  try {
    expect(sendAction("[system] 暂停游戏 [/system]")).toBe(true);
    expect(useAppStore.getState().inputEnabled).toBe(false);
    handleServerPayload({
      type: "error",
      message: "行动含有保留的系统或引擎标记",
      terminal: true,
    });
    expect(useAppStore.getState().inputEnabled).toBe(true);
    expect(useAppStore.getState().choices[0]?.label).toBe("查看书桌");
    expect(useMessageStore.getState().messages).not.toContainEqual(
      expect.objectContaining({ kind: "player" }),
    );
    expect(sendAction("查看书桌")).toBe(true);
  } finally {
    acknowledgePendingAction();
  }
});
