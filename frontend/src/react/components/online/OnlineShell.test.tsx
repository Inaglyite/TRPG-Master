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

vi.mock("./SoloCharacterSelectScreen", () => ({
  SoloCharacterSelectScreen: () => (
    <div data-testid="solo-character-select-screen" />
  ),
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

  it("开场中拿到权威快照后立即露出游戏区，不遮挡守秘人叙事", () => {
    setupOnline({ roomStatus: "starting", roomSnapshotReady: true });
    const { container } = render(<OnlineShell />);
    expect(container.firstChild).toBeNull();
    expect(screen.queryByTestId("online-shell")).not.toBeInTheDocument();
    expect(screen.queryByTestId("room-screen")).not.toBeInTheDocument();
  });

  it("开场中尚未收到权威快照时保留房间页，避免空白首屏", () => {
    setupOnline({ roomStatus: "starting", roomSnapshotReady: false });
    render(<OnlineShell />);
    expect(screen.getByTestId("online-shell")).toBeInTheDocument();
    expect(screen.getByTestId("room-screen")).toBeInTheDocument();
  });

  it("playing + roomOpen 时渲染房间管理页，返回游戏后淡出关闭", () => {
    vi.useFakeTimers();
    try {
      setupOnline({
        roomStatus: "playing",
        currentActorUserId: "u2",
        roomOpen: true,
      });
      render(<OnlineShell />);
      expect(screen.getByTestId("room-screen")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "返回游戏" }));
      expect(useOnlineStore.getState().roomOpen).toBe(false);
      // 整幕淡出期间外壳仍在场，播完 360ms 后才卸载
      expect(screen.getByTestId("online-shell")).toHaveClass("online-closing");
      act(() => vi.advanceTimersByTime(400));
      expect(screen.queryByTestId("room-screen")).not.toBeInTheDocument();
      expect(screen.queryByTestId("online-shell")).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("已挂载外壳在进入游戏画面时先播整幕淡出再卸载", () => {
    vi.useFakeTimers();
    try {
      setupOnline({ roomStatus: "lobby", currentActorUserId: "u1" });
      render(<OnlineShell />);
      expect(screen.getByTestId("online-shell")).toBeInTheDocument();

      act(() => useOnlineStore.setState({ roomStatus: "playing" }));
      expect(screen.getByTestId("online-shell")).toHaveClass("online-closing");
      expect(screen.getByTestId("online-shell")).toHaveAttribute(
        "aria-hidden",
        "true",
      );

      act(() => vi.advanceTimersByTime(400));
      expect(screen.queryByTestId("online-shell")).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
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

  it("solo 房间 lobby 状态先渲染角色卡选择页而非 RoomScreen", () => {
    setupOnline({
      roomStatus: "lobby",
      roomMetadata: { name: "雾中宅邸", play_mode: "solo" },
      members: [
        { user_id: "u1", username: "alice", role: "owner", investigator: null },
      ],
    });
    render(<OnlineShell />);
    expect(
      screen.getByTestId("solo-character-select-screen"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("room-screen")).not.toBeInTheDocument();
  });

  it("solo 房间连接且 lobby 时自动开局（进房已有角色卡，每个世界一次）", async () => {
    const { startGame } = await import("../../../online");
    setupOnline({
      roomStatus: "lobby",
      membersStatus: "ready",
      roomMetadata: { name: "雾中宅邸", play_mode: "solo" },
      members: [
        {
          user_id: "u1",
          username: "alice",
          role: "owner",
          investigator: {
            id: "investigator-1",
            character_key: "default:alice",
          },
        },
      ],
    });
    render(<OnlineShell />);
    expect(startGame).toHaveBeenCalledTimes(1);

    // room_state 重放 lobby 不重复发送 start。
    act(() => useOnlineStore.setState({ readyUserIds: ["u1"] }));
    expect(startGame).toHaveBeenCalledTimes(1);
  });

  it("进房后才认领角色卡不自动开局（确认按钮由玩家显式点击）", async () => {
    const { startGame } = await import("../../../online");
    setupOnline({
      roomStatus: "lobby",
      membersStatus: "ready",
      roomMetadata: { name: "雾中宅邸", play_mode: "solo" },
      members: [
        { user_id: "u1", username: "alice", role: "owner", investigator: null },
      ],
    });
    render(<OnlineShell />);
    expect(startGame).not.toHaveBeenCalled();

    // 玩家在角色选择页点卡认领：成员更新带上了 investigator，仍不得自动开局
    act(() =>
      useOnlineStore.setState({
        members: [
          {
            user_id: "u1",
            username: "alice",
            role: "owner",
            investigator: {
              id: "investigator-1",
              character_key: "default:alice",
            },
          },
        ],
      }),
    );
    expect(startGame).not.toHaveBeenCalled();
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

  it("solo 房间开场中拿到权威快照后不再显示正在开局页", () => {
    setupOnline({
      roomStatus: "starting",
      roomSnapshotReady: true,
      roomMetadata: { name: "雾中宅邸", play_mode: "solo" },
    });
    const { container } = render(<OnlineShell />);
    expect(container.firstChild).toBeNull();
    expect(screen.queryByTestId("solo-start-screen")).not.toBeInTheDocument();
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
