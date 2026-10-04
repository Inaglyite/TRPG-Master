import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as options from "../../options";

import { useAppStore } from "../../state/app-store";
import { DecisionModal, GameControls } from "./GameControls";
import { STRUCTURED_CAPABILITIES } from "../../protocol/structured-fixtures";
import {
  initialStructuredState,
  useStructuredStore,
} from "../../state/structured-store";
import { useOnlineStore } from "../../state/online-store";

beforeEach(() => useStructuredStore.setState({ ...initialStructuredState }));
afterEach(() => vi.restoreAllMocks());

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

  it("发送被拒时显示原因并原样保留玩家输入", () => {
    vi.spyOn(options, "sendPlayerText").mockReturnValue({
      ok: false,
      reason: "行动文字最多 2000 字，请缩短后再发送。",
    });
    useAppStore.setState({ mode: "local", inputEnabled: true });
    render(<GameControls />);
    const input = screen.getByRole("textbox");
    const draft = "  我先询问医生，再观察门口。  ";
    fireEvent.change(input, { target: { value: draft } });
    fireEvent.click(screen.getByRole("button", { name: "⏎" }));
    expect(input).toHaveValue(draft);
    expect(screen.getByRole("alert")).toHaveTextContent("最多 2000 字");
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

  it("中文输入法确认候选不提交或清空草稿", () => {
    useAppStore.setState({ mode: "local", inputEnabled: true });
    render(<GameControls />);
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "检查门锁" } });
    fireEvent.keyDown(input, { key: "Enter", isComposing: true });
    expect(input).toHaveValue("检查门锁");
    fireEvent.keyDown(input, { key: "Enter", keyCode: 229 });
    expect(input).toHaveValue("检查门锁");
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

  it("输入锁定时仍允许当前行动者选择聊天式预演回复", () => {
    useAppStore.setState({
      mode: "local",
      inputEnabled: false,
      choices: [
        {
          label: "仍然前往",
          isFree: false,
          decisionId: "action-preview-1",
          decisionOptionId: "continue_action",
        },
      ],
    });

    render(<GameControls />);

    expect(screen.getByRole("button", { name: "1. 仍然前往" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "⏎" })).toBeDisabled();
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

  it.each([
    { role: "player", id: "pc", connection: "connected", enabled: true },
    { role: "owner", id: "", connection: "connected", enabled: false },
    { role: "viewer", id: "stale-pc", connection: "connected", enabled: false },
    { role: "player", id: "pc", connection: "disconnected", enabled: false },
  ] as const)(
    "structured $role/$connection: async actions respect control and connection",
    ({ role, id, connection, enabled }) => {
      useStructuredStore.setState({
        capabilities: STRUCTURED_CAPABILITIES,
        identity: { ...initialStructuredState.identity, investigatorId: id },
      });
      useOnlineStore.setState({
        roomConnection: connection,
        members: [
          { user_id: "u1", username: "alice", role, investigator: null },
        ],
      });
      render(<GameControls />);
      const input = screen.getByRole("textbox");
      if (enabled) {
        expect(input).toBeEnabled();
        expect(screen.getByRole("button", { name: "⏎" })).toBeEnabled();
      } else {
        expect(input).toBeDisabled();
        expect(screen.getByRole("button", { name: "⏎" })).toBeDisabled();
      }
      if (role === "viewer")
        expect(input).toHaveAttribute(
          "placeholder",
          "旁观模式：只能查看公开叙事。",
        );
      if (!id)
        expect(input).toHaveAttribute(
          "placeholder",
          "你当前未控制调查员，请使用主持台。",
        );
    },
  );

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

  it("开场中即使收到输入状态也不能抢先行动", async () => {
    const { useOnlineStore } = await import("../../state/online-store");
    useOnlineStore.setState({
      roomStatus: "starting",
      currentActorUserId: "u1",
    });
    render(<GameControls />);
    expect(screen.getByPlaceholderText("你决定做什么？")).toBeDisabled();
    expect(screen.getByRole("button", { name: "1. 检查门锁" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "⏎" })).toBeDisabled();
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
