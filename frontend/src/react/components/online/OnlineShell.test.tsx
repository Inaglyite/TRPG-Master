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
  enterSoloLobby: vi.fn(),
  initOnlineSession: vi.fn(() => () => {}),
  resumeLastRoom: vi.fn(),
  startGame: vi.fn(),
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

vi.mock("./SoloLobbyScreen", () => ({
  SoloLobbyScreen: () => <div data-testid="solo-lobby-screen" />,
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
  window.history.replaceState({}, "", "/");
  setupOnline({ view: "auth", activeWorldId: null, roomStatus: null });
});

describe("OnlineShell 界面切换", () => {
  it("等待阶段渲染房间等待页（覆盖层）", () => {
    setupOnline({ roomStatus: "waiting" });
    render(<OnlineShell />);
    expect(screen.getByTestId("room-screen")).toBeInTheDocument();
    expect(screen.getByTestId("online-shell")).toBeInTheDocument();
  });

  it("playing 后不渲染任何覆盖层（房间状态由 OnlineRoomDock 呈现）", () => {
    setupOnline({ roomStatus: "playing", currentActorUserId: "u2" });
    const { container } = render(<OnlineShell />);
    expect(container.firstChild).toBeNull();
    expect(screen.queryByTestId("online-shell")).not.toBeInTheDocument();
    expect(screen.queryByTestId("room-screen")).not.toBeInTheDocument();
  });

  it("playing + roomOpen 时渲染房间管理页，返回游戏后关闭", () => {
    setupOnline({
      roomStatus: "playing",
      currentActorUserId: "u2",
      roomOpen: true,
    });
    render(<OnlineShell />);
    expect(screen.getByTestId("room-screen")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "返回游戏" }));
    expect(useOnlineStore.getState().roomOpen).toBe(false);
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
    act(() => useOnlineStore.setState({ roomOpen: true }));
    expect(screen.getByTestId("room-screen")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "返回游戏" }));

    expect(connectRoom).toHaveBeenCalledTimes(1);
    expect(disconnectRoom).not.toHaveBeenCalled();

    act(() => useOnlineStore.setState({ view: "lobby", activeWorldId: null }));
    expect(disconnectRoom).toHaveBeenCalledTimes(1);
    unmount();
  });
});

describe("OnlineShell 云端单人", () => {
  it("view=solo 渲染我的冒险", () => {
    setupOnline({ view: "solo", activeWorldId: null, roomStatus: null });
    render(<OnlineShell />);
    expect(screen.getByTestId("solo-lobby-screen")).toBeInTheDocument();
    expect(screen.queryByTestId("room-screen")).not.toBeInTheDocument();
  });

  it("solo 房间 lobby 状态渲染自动开局页而非 RoomScreen", () => {
    setupOnline({
      roomStatus: "lobby",
      roomMetadata: { name: "雾中宅邸", play_mode: "solo" },
      members: [
        { user_id: "u1", username: "alice", role: "owner", investigator: null },
      ],
    });
    render(<OnlineShell />);
    expect(screen.getByTestId("solo-start-screen")).toBeInTheDocument();
    expect(screen.queryByTestId("room-screen")).not.toBeInTheDocument();
  });

  it("solo 房间连接且 lobby 时自动开局（每个世界一次）", async () => {
    const { startGame } = await import("../../../online");
    setupOnline({
      roomStatus: "lobby",
      roomMetadata: { name: "雾中宅邸", play_mode: "solo" },
      members: [
        { user_id: "u1", username: "alice", role: "owner", investigator: null },
      ],
    });
    render(<OnlineShell />);
    expect(startGame).toHaveBeenCalledTimes(1);

    // room_state 重放 lobby 不重复发送 start。
    act(() => useOnlineStore.setState({ readyUserIds: ["u1"] }));
    expect(startGame).toHaveBeenCalledTimes(1);
  });

  it("多人房间不自动开局", async () => {
    const { startGame } = await import("../../../online");
    setupOnline({ roomStatus: "lobby" });
    render(<OnlineShell />);
    expect(startGame).not.toHaveBeenCalled();
  });

  it("solo 房间 playing 后不渲染覆盖层，roomOpen 被兜底关闭", () => {
    setupOnline({
      roomStatus: "playing",
      roomOpen: true,
      roomMetadata: { name: "雾中宅邸", play_mode: "solo" },
      members: [
        { user_id: "u1", username: "alice", role: "owner", investigator: null },
      ],
    });
    const { container } = render(<OnlineShell />);
    expect(container.firstChild).toBeNull();
    expect(screen.queryByTestId("room-screen")).not.toBeInTheDocument();
    expect(useOnlineStore.getState().roomOpen).toBe(false);
  });

  it("从 solo 大厅进房、元数据未加载时不闪现多人等待页", () => {
    setupOnline({
      pendingIntent: "solo",
      roomStatus: null,
      roomMetadata: null,
      roomConnection: "connecting",
    });
    render(<OnlineShell />);
    expect(screen.getByTestId("solo-start-screen")).toBeInTheDocument();
    expect(screen.queryByTestId("room-screen")).not.toBeInTheDocument();
  });
});
