import { beforeEach, describe, expect, it } from "vitest";

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
