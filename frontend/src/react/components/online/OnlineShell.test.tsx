import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  initialOnlineState,
  useOnlineStore,
} from "../../../state/online-store";
import { OnlineShell } from "./OnlineShell";

vi.mock("../../../online", () => ({
  assignActor: vi.fn(),
  checkSession: vi.fn().mockResolvedValue(undefined),
  enterLobby: vi.fn(),
  initOnlineSession: vi.fn(() => () => {}),
  resumeLastRoom: vi.fn(),
}));

vi.mock("../../../room-ws", () => ({
  connectRoom: vi.fn(),
  disconnectRoom: vi.fn(),
}));

vi.mock("./AuthScreen", () => ({
  AuthScreen: () => <div data-testid="auth-screen" />,
}));

vi.mock("./LobbyScreen", () => ({
  LobbyScreen: () => <div data-testid="lobby-screen" />,
}));

vi.mock("./RoomScreen", () => ({
  RoomScreen: ({ onClose }: { onClose?: () => void }) => (
    <div data-testid="room-screen">
      {onClose && (
        <button type="button" onClick={onClose}>
          返回游戏
        </button>
      )}
    </div>
  ),
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
    roomMetadata: { name: "周五调查夜" },
    members: [
      { user_id: "u1", username: "alice", role: "owner", investigator: null },
      { user_id: "u2", username: "bob", role: "player", investigator: null },
    ],
    ...patch,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  setupOnline({ view: "auth", activeWorldId: null, roomStatus: null });
});

describe("OnlineShell 界面切换", () => {
  it("等待阶段渲染房间等待页（覆盖层）", () => {
    setupOnline({ roomStatus: "waiting" });
    render(<OnlineShell />);
    expect(screen.getByTestId("room-screen")).toBeInTheDocument();
    expect(screen.queryByTestId("game-room-bar")).not.toBeInTheDocument();
  });

  it("playing 后切换到游戏房间条，不再覆盖游戏界面", () => {
    setupOnline({ roomStatus: "playing", currentActorUserId: "u2" });
    render(<OnlineShell />);
    expect(screen.getByTestId("game-room-bar")).toBeInTheDocument();
    expect(screen.queryByTestId("room-screen")).not.toBeInTheDocument();
    expect(screen.getByText("等待 bob 行动…")).toBeInTheDocument();
  });

  it("playing + 轮到自己时显示轮到你行动", () => {
    setupOnline({ roomStatus: "playing", currentActorUserId: "u1" });
    render(<OnlineShell />);
    expect(screen.getByText("轮到你行动")).toBeInTheDocument();
  });

  it("从房间条打开房间页，再返回游戏", () => {
    setupOnline({ roomStatus: "playing", currentActorUserId: "u2" });
    render(<OnlineShell />);
    fireEvent.click(screen.getByRole("button", { name: "房间" }));
    expect(screen.getByTestId("room-screen")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "返回游戏" }));
    expect(screen.getByTestId("game-room-bar")).toBeInTheDocument();
    expect(screen.queryByTestId("room-screen")).not.toBeInTheDocument();
  });

  it("等待页、playing、房间管理与返回游戏共用一个房间连接", async () => {
    const { connectRoom, disconnectRoom } = await import("../../../room-ws");
    setupOnline({ roomStatus: "lobby", currentActorUserId: "u1" });
    const { unmount } = render(<OnlineShell />);

    expect(connectRoom).toHaveBeenCalledTimes(1);
    expect(connectRoom).toHaveBeenCalledWith("world-1");
    expect(disconnectRoom).not.toHaveBeenCalled();

    act(() => useOnlineStore.setState({ roomStatus: "playing" }));
    expect(screen.getByTestId("game-room-bar")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "房间" }));
    fireEvent.click(screen.getByRole("button", { name: "返回游戏" }));

    expect(connectRoom).toHaveBeenCalledTimes(1);
    expect(disconnectRoom).not.toHaveBeenCalled();

    act(() => useOnlineStore.setState({ view: "lobby", activeWorldId: null }));
    expect(disconnectRoom).toHaveBeenCalledTimes(1);
    unmount();
  });

  it("房主可以跳过当前行动者（循环指定下一位玩家）", async () => {
    const { assignActor } = await import("../../../online");
    setupOnline({ roomStatus: "playing", currentActorUserId: "u1" });
    render(<OnlineShell />);
    fireEvent.click(screen.getByRole("button", { name: "跳过行动者" }));
    expect(assignActor).toHaveBeenCalledWith("u2");
  });

  it("非房主不显示跳过按钮", () => {
    setupOnline({
      roomStatus: "playing",
      currentActorUserId: "u2",
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
    render(<OnlineShell />);
    expect(
      screen.queryByRole("button", { name: "跳过行动者" }),
    ).not.toBeInTheDocument();
  });
});
