import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAppStore } from "../../state/app-store";
import { returnToStartMenu } from "../../start";
import { useStartStore } from "../../state/start-store";
import { AppHeader } from "./AppHeader";

vi.mock("../../start", () => ({
  returnToStartMenu: vi.fn(),
}));

describe("AppHeader", () => {
  beforeEach(() => {
    vi.mocked(returnToStartMenu).mockReset();
    useAppStore.setState({
      connection: "connecting",
      title: "TRPG Game",
      mode: "local",
    });
    useStartStore.setState({ gameStarting: false });
  });

  it("reacts to connection and module theme state", () => {
    const { container } = render(<AppHeader />);
    expect(screen.getByRole("heading")).toHaveTextContent("TRPG Game");
    expect(container.querySelector("#conn-status")).toHaveClass("connecting");

    act(() => {
      useAppStore.getState().setConnection("connected");
      useAppStore.getState().setTitle("猩红文档");
    });

    expect(screen.getByRole("heading")).toHaveTextContent("猩红文档");
    expect(container.querySelector("#conn-status")).toHaveClass("connected");
  });

  it("通过应用内开局选择开始新游戏，不重载 Electron 页面", () => {
    render(<AppHeader />);

    fireEvent.click(screen.getByLabelText("开始新游戏"));

    expect(returnToStartMenu).toHaveBeenCalledTimes(1);
  });
});

describe("AppHeader 多人房主专属操作", () => {
  beforeEach(async () => {
    const { initialOnlineState, useOnlineStore } =
      await import("../../state/online-store");
    useOnlineStore.setState({
      ...initialOnlineState,
      authStatus: "authenticated",
      user: { id: "u1", username: "alice" },
      members: [{ user_id: "u1", username: "alice", role: "player" }],
    });
    useAppStore.setState({ mode: "online", connection: "connected" });
  });

  it("非房主隐藏快速存档与存档管理", () => {
    render(<AppHeader />);
    expect(screen.queryByLabelText("快速存档")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("打开存档管理")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("打开模型设置")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("开始新游戏")).not.toBeInTheDocument();
  });

  it("房主可见存档操作", async () => {
    const { useOnlineStore } = await import("../../state/online-store");
    useOnlineStore.setState({
      members: [{ user_id: "u1", username: "alice", role: "owner" }],
    });
    render(<AppHeader />);
    expect(screen.getByLabelText("快速存档")).toBeInTheDocument();
    expect(screen.getByLabelText("打开存档管理")).toBeInTheDocument();
  });

  it("单机模式不受成员角色影响", async () => {
    useAppStore.setState({ mode: "local" });
    render(<AppHeader />);
    expect(screen.getByLabelText("快速存档")).toBeInTheDocument();
    expect(screen.getByLabelText("打开模型设置")).toBeInTheDocument();
  });
});
