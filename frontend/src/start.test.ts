import { beforeEach, describe, expect, it } from "vitest";

import { addMsg } from "./renderer";
import { onGmTurnStart, returnToStartMenu } from "./start";
import { useAppStore } from "./state/app-store";
import { useMessageStore } from "./state/message-store";
import { useStartStore } from "./state/start-store";

describe("应用内新游戏导航", () => {
  beforeEach(() => {
    useMessageStore.setState({
      messages: [],
      scrollRequest: 0,
      forceScrollRequest: 0,
    });
    useStartStore.setState({
      gameStarted: true,
      gameStarting: false,
      view: "characters",
      moduleSwitchPending: true,
      hint: "正在切换模组…",
    });
    useAppStore.setState({
      inputEnabled: true,
      choices: [{ label: "检查书桌", isFree: false }],
      dialog: {
        kind: "suggest",
        description: "检定",
      },
      ending: {
        ending_type: "good",
        title: "旧结局",
        summary: "旧局信息",
      },
    });
  });

  it("只回到开局选择，不重载文档或立刻重置服务端世界", () => {
    returnToStartMenu();

    expect(useStartStore.getState()).toMatchObject({
      gameStarted: false,
      gameStarting: false,
      view: "menu",
      moduleSwitchPending: false,
      hint: "",
    });
    expect(useAppStore.getState()).toMatchObject({
      inputEnabled: false,
      choices: [],
      dialog: null,
      ending: null,
    });
  });

  it("在服务端确认新开局后才清空旧局呈现", () => {
    addMsg("player", "上一局的行动");
    returnToStartMenu();
    useStartStore.setState({ gameStarting: true });

    onGmTurnStart();

    expect(useStartStore.getState()).toMatchObject({
      gameStarted: true,
      gameStarting: false,
    });
    expect(useMessageStore.getState().messages).toEqual([
      expect.objectContaining({ kind: "loading", text: "守秘人正在叙述……" }),
    ]);
  });
});
