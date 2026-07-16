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
});
