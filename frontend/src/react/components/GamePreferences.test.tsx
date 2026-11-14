import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  useGamePreferences,
  readActionHintsPreference,
} from "../../state/game-preferences";
import { useAppStore } from "../../state/app-store";
import { useMessageStore } from "../../state/message-store";
import { useModelStore } from "../../state/model-store";
import { ModelSettingsPanel } from "./ModelSettingsPanel";
import { MessageList } from "./MessageList";
import { GameControls } from "./GameControls";
import { withoutActionHints } from "../../action-hints";

beforeEach(() => {
  localStorage.clear();
  useGamePreferences.setState({ showActionHints: true, showReasoning: false });
  useAppStore.setState({
    mode: "local",
    inputEnabled: true,
    ending: null,
    choices: [{ label: "翻看剪报", isFree: false }],
  });
  useMessageStore.setState({ messages: [], actionReset: 0 });
  useModelStore.setState({
    open: true,
    tab: "general",
    view: null,
    loading: true,
    saving: false,
    status: "",
  });
});

describe("game preferences", () => {
  it("is usable without model configuration and persists immediately", () => {
    render(<ModelSettingsPanel />);
    expect(screen.getByRole("dialog", { name: "游戏设置" })).toBeVisible();
    const toggle = screen.getByRole("switch", { name: "显示行动提示" });
    expect(toggle).toBeChecked();
    fireEvent.click(toggle);
    expect(toggle).not.toBeChecked();
    expect(readActionHintsPreference()).toBe(false);
    expect(
      screen.queryByRole("button", { name: /保存配置/ }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "模型配置" }));
    fireEvent.click(screen.getByRole("tab", { name: "游戏偏好" }));
    expect(
      screen.getByRole("switch", { name: "显示行动提示" }),
    ).not.toBeChecked();
    fireEvent.click(screen.getByRole("switch", { name: "显示行动提示" }));
    expect(readActionHintsPreference()).toBe(true);
  });

  it("hides menus in history and segments reactively without altering stored text or NPC speech", () => {
    const menu = "**你可以——**\n1. 翻看剪报\n2. 继续追问";
    const messages = [
      { id: "history", kind: "gm" as const, text: `旧回合叙述。\n\n${menu}` },
      {
        id: "current",
        kind: "gm" as const,
        text: "",
        segments: [
          { kind: "narration" as const, text: "窗外下雨。" },
          {
            kind: "speech" as const,
            text: "你可以——",
            speaker: { type: "npc" as const, name: "门房" },
          },
          { kind: "narration" as const, text: menu },
        ],
      },
      { id: "player", kind: "player" as const, text: menu },
    ];
    useMessageStore.setState({ messages });
    const { container } = render(
      <>
        <MessageList />
        <GameControls />
      </>,
    );
    expect(screen.getByRole("button", { name: "1. 翻看剪报" })).toBeVisible();
    act(() => useGamePreferences.getState().setShowActionHints(false));
    expect(container.querySelector("#history")).toHaveTextContent(
      "旧回合叙述。",
    );
    expect(container.querySelector("#history")).not.toHaveTextContent("你可以");
    expect(container.querySelector("#current")).toHaveTextContent("窗外下雨。");
    expect(container.querySelectorAll("#current .keeper-bubble")).toHaveLength(
      1,
    );
    expect(container.querySelector("#current .speech-text")).toHaveTextContent(
      "你可以——",
    );
    expect(container.querySelector("#player")).toHaveTextContent("翻看剪报");
    expect(
      screen.queryByRole("button", { name: "1. 翻看剪报" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("textbox")).toBeEnabled();
    expect(useMessageStore.getState().messages).toBe(messages);
    act(() => useGamePreferences.getState().setShowActionHints(true));
    expect(container.querySelector("#history")).toHaveTextContent("你可以——");
    expect(screen.getByRole("button", { name: "1. 翻看剪报" })).toBeVisible();
  });

  it("retains necessary chat decision buttons while suggestions are hidden", () => {
    useGamePreferences.setState({ showActionHints: false });
    useAppStore.setState({
      inputEnabled: false,
      choices: [
        {
          label: "确认前往",
          isFree: false,
          decisionId: "preview",
          decisionOptionId: "continue",
        },
      ],
    });
    render(<GameControls />);
    expect(screen.getByRole("button", { name: "1. 确认前往" })).toBeEnabled();
  });

  it("continues to work when browser persistence is blocked", () => {
    const spy = vi
      .spyOn(Storage.prototype, "setItem")
      .mockImplementation(() => {
        throw new Error("blocked");
      });
    useGamePreferences.getState().setShowActionHints(false);
    expect(useGamePreferences.getState().showActionHints).toBe(false);
    spy.mockRestore();
    expect(readActionHintsPreference()).toBe(true);
  });

  it("hides old menu variants while retaining following prose and ordinary numbered documents", () => {
    expect(
      withoutActionHints(
        "叙述。\n### **你可以——**\n1） 观察窗户\n2、 仔细倾听\n随后门开了。",
      ),
    ).toBe("叙述。\n随后门开了。");
    expect(
      withoutActionHints("资料清单\n1. 日期\n2. 地址\n她告诉你可以先坐下。"),
    ).toBe("资料清单\n1. 日期\n2. 地址\n她告诉你可以先坐下。");
  });
});
