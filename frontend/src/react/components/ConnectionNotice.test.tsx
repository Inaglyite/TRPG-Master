import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAppStore } from "../../state/app-store";
import { ConnectionNotice } from "./ConnectionNotice";

vi.mock("../../ws", () => ({ recoverLatestTurn: vi.fn() }));

describe("ConnectionNotice", () => {
  beforeEach(() => useAppStore.getState().setConnectionNotice(null));

  it("only offers recovery when the connection service permits it", async () => {
    const { rerender } = render(<ConnectionNotice />);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    useAppStore.getState().setConnectionNotice("连接已断开");
    rerender(<ConnectionNotice />);
    expect(screen.getByRole("status")).toHaveTextContent("连接已断开");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();

    useAppStore.getState().setConnectionNotice("可以恢复", true);
    rerender(<ConnectionNotice />);
    fireEvent.click(screen.getByRole("button", { name: "恢复最近自动存档" }));
    const { recoverLatestTurn } = await import("../../ws");
    await waitFor(() => expect(recoverLatestTurn).toHaveBeenCalledOnce());
  });

  it("多人模式下非房主不显示恢复按钮，房主显示", async () => {
    const { initialOnlineState, useOnlineStore } =
      await import("../../state/online-store");
    useOnlineStore.setState({
      ...initialOnlineState,
      authStatus: "authenticated",
      user: { id: "u1", username: "alice" },
      members: [{ user_id: "u1", username: "alice", role: "player" }],
    });
    useAppStore.setState({ mode: "online" });
    useAppStore.getState().setConnectionNotice("可以恢复", true);
    const { rerender } = render(<ConnectionNotice />);
    expect(
      screen.queryByRole("button", { name: "恢复最近自动存档" }),
    ).not.toBeInTheDocument();

    useOnlineStore.setState({
      members: [{ user_id: "u1", username: "alice", role: "owner" }],
    });
    rerender(<ConnectionNotice />);
    expect(
      screen.getByRole("button", { name: "恢复最近自动存档" }),
    ).toBeInTheDocument();

    useAppStore.setState({ mode: "select" });
    useAppStore.getState().setConnectionNotice(null);
  });
});
