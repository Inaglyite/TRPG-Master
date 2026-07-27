import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { useAppStore } from "../../state/app-store";
import { DecisionModal, GameControls } from "./GameControls";

describe("game interaction components", () => {
  beforeEach(() => {
    useAppStore.setState({
      inputEnabled: false,
      inputPlaceholder: "等待守秘人叙述……",
      choices: [],
      dialog: null,
      ending: null,
    });
  });

  it("renders choices and input state from the store", () => {
    render(<GameControls />);
    act(() => {
      useAppStore.getState().setChoices([{ label: "检查门锁", isFree: false }]);
      useAppStore.getState().setInput(true, "你决定做什么？");
    });

    expect(
      screen.getByRole("button", { name: "1. 检查门锁" }),
    ).toBeInTheDocument();
    expect(screen.getByPlaceholderText("你决定做什么？")).toBeEnabled();
  });

  it("renders a structured decision without injecting HTML", () => {
    render(<DecisionModal />);
    act(() => {
      useAppStore.getState().setDialog({
        kind: "decision",
        id: "defense-1",
        title: "如何防御？",
        description: "选择本轮反应",
        options: [{ id: "dodge", label: "闪避", description: "尝试避开攻击" }],
      });
    });

    expect(screen.getByRole("dialog")).toHaveTextContent("如何防御？");
    expect(screen.getByRole("button", { name: /闪避/ })).toBeInTheDocument();
  });
});

describe("GameControls 多人行动门禁", () => {
  beforeEach(async () => {
    const { useAppStore } = await import("../../state/app-store");
    const { initialOnlineState, useOnlineStore } =
      await import("../../state/online-store");
    useOnlineStore.setState({
      ...initialOnlineState,
      authStatus: "authenticated",
      user: { id: "u1", username: "alice" },
      roomConnection: "connected",
      roomStatus: "playing",
      currentActorUserId: "u2",
      members: [
        {
          user_id: "u1",
          username: "alice",
          role: "player",
          investigator: null,
        },
        { user_id: "u2", username: "bob", role: "player", investigator: null },
      ],
    });
    useAppStore.setState({
      mode: "online",
      inputEnabled: true,
      inputPlaceholder: "你决定做什么？",
      choices: [{ label: "检查门锁", isFree: false }],
      dialog: null,
      ending: null,
    });
  });

  it("非当前行动者：输入与选项禁用并显示等待", () => {
    render(<GameControls />);
    expect(screen.getByPlaceholderText("等待 bob 行动……")).toBeDisabled();
    expect(screen.getByRole("button", { name: "1. 检查门锁" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "⏎" })).toBeDisabled();
  });

  it("当前行动者：正常启用", async () => {
    const { useOnlineStore } = await import("../../state/online-store");
    useOnlineStore.setState({ currentActorUserId: "u1" });
    render(<GameControls />);
    expect(screen.getByPlaceholderText("你决定做什么？")).toBeEnabled();
    expect(screen.getByRole("button", { name: "1. 检查门锁" })).toBeEnabled();
  });

  it("单机模式不受房间状态影响", async () => {
    const { useAppStore } = await import("../../state/app-store");
    useAppStore.setState({ mode: "local" });
    render(<GameControls />);
    expect(screen.getByPlaceholderText("你决定做什么？")).toBeEnabled();
  });
});

describe("GameControls 结案按钮的房主门禁", () => {
  beforeEach(async () => {
    const { useAppStore } = await import("../../state/app-store");
    const { initialOnlineState, useOnlineStore } =
      await import("../../state/online-store");
    useOnlineStore.setState({
      ...initialOnlineState,
      authStatus: "authenticated",
      user: { id: "u1", username: "alice" },
      roomConnection: "connected",
      roomStatus: "playing",
      currentActorUserId: "u1",
      members: [{ user_id: "u1", username: "alice", role: "player" }],
    });
    useAppStore.setState({
      mode: "online",
      inputEnabled: true,
      inputPlaceholder: "你决定做什么？",
      choices: [],
      dialog: null,
      ending: {
        ending_type: "good",
        title: "手稿归档",
        summary: "低语终于停止。",
      },
    });
  });

  it("非房主不显示确认结束，仅保留继续探索", () => {
    render(<GameControls />);
    expect(
      screen.queryByRole("button", { name: /确认结束/ }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /继续探索/ })).toBeEnabled();
  });

  it("非当前行动者不能点击继续探索", async () => {
    const { useOnlineStore } = await import("../../state/online-store");
    useOnlineStore.setState({ currentActorUserId: "u2" });
    render(<GameControls />);
    expect(screen.getByRole("button", { name: /继续探索/ })).toBeDisabled();
  });

  it("房主可见确认结束", async () => {
    const { useOnlineStore } = await import("../../state/online-store");
    useOnlineStore.setState({
      members: [{ user_id: "u1", username: "alice", role: "owner" }],
    });
    render(<GameControls />);
    expect(
      screen.getByRole("button", { name: /确认结束/ }),
    ).toBeInTheDocument();
  });
});
