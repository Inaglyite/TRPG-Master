import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { useAppStore } from "../../state/app-store";
import { AppHeader } from "./AppHeader";

describe("AppHeader", () => {
  beforeEach(() => {
    useAppStore.setState({ connection: "connecting", title: "疯狂宅邸" });
  });

  it("reacts to connection and module theme state", () => {
    const { container } = render(<AppHeader />);
    expect(screen.getByRole("heading")).toHaveTextContent("疯狂宅邸");
    expect(container.querySelector("#conn-status")).toHaveClass("connecting");

    act(() => {
      useAppStore.getState().setConnection("connected");
      useAppStore.getState().setTitle("猩红文档");
    });

    expect(screen.getByRole("heading")).toHaveTextContent("猩红文档");
    expect(container.querySelector("#conn-status")).toHaveClass("connected");
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
