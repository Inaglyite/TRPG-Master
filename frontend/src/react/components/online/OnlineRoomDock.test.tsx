import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  initialOnlineState,
  useOnlineStore,
} from "../../../state/online-store";
import { OnlineRoomDock } from "./OnlineRoomDock";

vi.mock("../../../online", () => ({
  assignActor: vi.fn(),
}));

const alice = { id: "u1", username: "alice" };

function setupOnline(patch: Record<string, unknown> = {}) {
  useOnlineStore.setState({
    ...initialOnlineState,
    authStatus: "authenticated",
    user: alice,
    view: "room",
    activeWorldId: "world-1",
    roomConnection: "connected",
    roomStatus: "playing",
    roomMetadata: { name: "周五调查夜" },
    onlineUserIds: ["u1", "u2"],
    members: [
      { user_id: "u1", username: "alice", role: "owner", investigator: null },
      { user_id: "u2", username: "bob", role: "player", investigator: null },
    ],
    ...patch,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  setupOnline({ currentActorUserId: "u2" });
});

describe("OnlineRoomDock", () => {
  it("非 playing 或房间管理页打开时不渲染", () => {
    setupOnline({ roomStatus: "lobby" });
    const { container, rerender } = render(<OnlineRoomDock />);
    expect(container.firstChild).toBeNull();

    setupOnline({ roomOpen: true });
    rerender(<OnlineRoomDock />);
    expect(container.firstChild).toBeNull();
  });

  it("收起时是紧凑入口条：房间名、连接状态、轮到谁", () => {
    render(<OnlineRoomDock />);
    const toggle = screen.getByRole("button", { name: /周五调查夜/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText("等待 bob 行动…")).toBeInTheDocument();
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "多人房间状态" }),
    ).not.toBeInTheDocument();
  });

  it("轮到自己时入口条显示轮到你行动", () => {
    setupOnline({ currentActorUserId: "u1" });
    render(<OnlineRoomDock />);
    expect(screen.getByText("轮到你行动")).toBeInTheDocument();
  });

  it("展开卡片：连接徽章、成员摘要，并可收起", () => {
    render(<OnlineRoomDock />);
    fireEvent.click(screen.getByRole("button", { name: /周五调查夜/ }));

    const card = screen.getByRole("region", { name: "多人房间状态" });
    expect(card).toBeInTheDocument();
    expect(screen.getAllByText("已连接").length).toBeGreaterThan(0);
    expect(screen.getByText("alice")).toBeInTheDocument();
    expect(screen.getByText("bob")).toBeInTheDocument();
    expect(screen.getByText("房主")).toBeInTheDocument();
    expect(screen.getByText("行动中")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "收起房间面板" }));
    expect(
      screen.queryByRole("region", { name: "多人房间状态" }),
    ).not.toBeInTheDocument();
  });

  it("Escape 关闭卡片", () => {
    render(<OnlineRoomDock />);
    fireEvent.click(screen.getByRole("button", { name: /周五调查夜/ }));
    const card = screen.getByRole("region", { name: "多人房间状态" });
    fireEvent.keyDown(card, { key: "Escape" });
    expect(
      screen.queryByRole("region", { name: "多人房间状态" }),
    ).not.toBeInTheDocument();
  });

  it("房间管理入口打开完整房间页（roomOpen）", () => {
    render(<OnlineRoomDock />);
    fireEvent.click(screen.getByRole("button", { name: /周五调查夜/ }));
    fireEvent.click(screen.getByRole("button", { name: "房间管理" }));
    expect(useOnlineStore.getState().roomOpen).toBe(true);
  });

  it("房主可以跳过当前行动者（循环指定下一位玩家）", async () => {
    const { assignActor } = await import("../../../online");
    setupOnline({ currentActorUserId: "u1" });
    render(<OnlineRoomDock />);
    fireEvent.click(screen.getByRole("button", { name: /周五调查夜/ }));
    fireEvent.click(screen.getByRole("button", { name: "跳过行动者" }));
    expect(assignActor).toHaveBeenCalledWith("u2");
  });

  it("非房主不显示跳过按钮", () => {
    setupOnline({
      members: [
        {
          user_id: "u1",
          username: "alice",
          role: "player",
          investigator: null,
        },
        { user_id: "u2", username: "bob", role: "owner", investigator: null },
      ],
    });
    render(<OnlineRoomDock />);
    fireEvent.click(screen.getByRole("button", { name: /周五调查夜/ }));
    expect(
      screen.queryByRole("button", { name: "跳过行动者" }),
    ).not.toBeInTheDocument();
  });

  it("solo 房间隐藏房间管理入口（单人也没有跳过行动者）", () => {
    setupOnline({
      roomMetadata: { name: "雾中宅邸", play_mode: "solo" },
      currentActorUserId: "u1",
      onlineUserIds: ["u1"],
      members: [
        { user_id: "u1", username: "alice", role: "owner", investigator: null },
      ],
    });
    render(<OnlineRoomDock />);
    fireEvent.click(screen.getByRole("button", { name: /雾中宅邸/ }));
    expect(
      screen.queryByRole("button", { name: "房间管理" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "跳过行动者" }),
    ).not.toBeInTheDocument();
  });
});
