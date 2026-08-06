import { useEffect, useRef } from "react";

import {
  checkSession,
  enterLobby,
  enterSoloLobby,
  initOnlineSession,
  resumeLastRoom,
  startGame,
} from "../../../online";
import { connectRoom, disconnectRoom } from "../../../room-ws";
import { useOnlineStore } from "../../../state/online-store";
import { AuthScreen } from "./AuthScreen";
import { LobbyScreen } from "./LobbyScreen";
import { RoomScreen } from "./RoomScreen";
import { SoloLobbyScreen } from "./SoloLobbyScreen";

/**
 * 联机模式外壳：认证 → 大厅/我的冒险 → 房间（等待页）→ 游戏进行中。
 * roomStatus 变为 playing 后不再用覆盖层遮挡：游戏区顶部的 OnlineRoomDock
 * （由 GameShell 挂载在聊天面板内）承载房间状态；这里只在 roomOpen 时
 * 显示完整房间管理页（成员/邀请/移交等管理）。
 *
 * 云端单人（play_mode=solo）走同一 /ws/room 房间引擎，但不经过多人等待页：
 * 连接后自动开局（免 ready/claim），RoomScreen 永不用于 solo 房间。
 */
export function OnlineShell() {
  const view = useOnlineStore((state) => state.view);
  const authStatus = useOnlineStore((state) => state.authStatus);
  const activeWorldId = useOnlineStore((state) => state.activeWorldId);
  const roomStatus = useOnlineStore((state) => state.roomStatus);
  const roomOpen = useOnlineStore((state) => state.roomOpen);
  const roomConnection = useOnlineStore((state) => state.roomConnection);
  const roomMetadata = useOnlineStore((state) => state.roomMetadata);
  const pendingIntent = useOnlineStore((state) => state.pendingIntent);
  const roomError = useOnlineStore((state) => state.roomError);

  // solo 房间每个世界只自动开局一次，避免 room_state 重放导致重复 start。
  const soloAutoStartRef = useRef<string | null>(null);

  useEffect(() => {
    const unsubscribe = initOnlineSession();
    // Electron 联机入口的落点意图经 URL 传入（?intent=solo，由主进程拼接）。
    try {
      if (
        new URLSearchParams(window.location.search).get("intent") === "solo"
      ) {
        useOnlineStore.setState({ pendingIntent: "solo" });
      }
    } catch {
      /* URL 解析失败时按多人大厅处理 */
    }
    void (async () => {
      await checkSession();
      // 已有有效 Session（刷新/重启后）直接进入大厅，并尝试回到上次的房间。
      const state = useOnlineStore.getState();
      if (state.authStatus === "authenticated") {
        if (state.pendingIntent === "solo") {
          await enterSoloLobby();
        } else {
          await enterLobby();
          await resumeLastRoom();
        }
      }
    })();
    return unsubscribe;
  }, []);

  // 房间连接属于联机外壳，而不是等待页。等待页与游戏条切换时必须保持
  // 同一个权威 /ws/room；只有离开房间、退出登录或卸载联机模式才断开。
  useEffect(() => {
    if (authStatus !== "authenticated" || view !== "room" || !activeWorldId) {
      return;
    }
    connectRoom(activeWorldId);
    return () => disconnectRoom();
  }, [activeWorldId, authStatus, view]);

  const isSoloRoom = roomMetadata?.play_mode === "solo";
  // 从“我的冒险”进房、元数据尚未加载时也按 solo 流程处理，避免闪现多人等待页。
  const soloRoomFlow =
    view === "room" &&
    (isSoloRoom || (pendingIntent === "solo" && roomMetadata === null));

  // solo 房间免 ready/claim：连接且确认 lobby 状态后直接由房主（即本人）开局。
  useEffect(() => {
    if (!isSoloRoom || view !== "room" || !activeWorldId) return;
    if (roomConnection !== "connected" || roomStatus !== "lobby") return;
    if (soloAutoStartRef.current === activeWorldId) return;
    soloAutoStartRef.current = activeWorldId;
    void startGame();
  }, [activeWorldId, isSoloRoom, roomConnection, roomStatus, view]);

  // solo 房间没有多人管理页（邀请/移交均不可用）；兜底关掉，永不渲染 RoomScreen。
  useEffect(() => {
    if (isSoloRoom && roomOpen) {
      useOnlineStore.setState({ roomOpen: false });
    }
  }, [isSoloRoom, roomOpen]);

  const playing = view === "room" && roomStatus === "playing";

  // playing 且未打开房间管理页时，联机外壳不渲染任何覆盖层；
  // 房间状态由聊天面板内的 OnlineRoomDock 呈现，房间连接由上面的 effect 保持。
  if (playing && (!roomOpen || isSoloRoom)) {
    return null;
  }

  return (
    <div className="online-overlay" data-testid="online-shell">
      {view === "auth" && <AuthScreen />}
      {view === "lobby" && <LobbyScreen />}
      {view === "solo" && <SoloLobbyScreen />}
      {view === "room" && soloRoomFlow && (
        <div
          className="online-box online-card solo-start-screen"
          data-testid="solo-start-screen"
        >
          <h1 className="online-title online-title--small">
            正在准备你的冒险…
          </h1>
          <p className="online-loading" role="status">
            {roomConnection === "connected" ? "正在开局……" : "正在连接房间……"}
          </p>
          {roomError && (
            <p className="online-notice online-notice--error" role="alert">
              {roomError}
            </p>
          )}
          <button
            type="button"
            className="btn-ghost"
            onClick={() => void enterSoloLobby()}
          >
            ← 返回我的冒险
          </button>
        </div>
      )}
      {view === "room" && !soloRoomFlow && (
        <RoomScreen
          onClose={
            playing
              ? () => useOnlineStore.setState({ roomOpen: false })
              : undefined
          }
        />
      )}
    </div>
  );
}
