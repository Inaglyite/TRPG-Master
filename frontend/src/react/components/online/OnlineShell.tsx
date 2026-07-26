import { useEffect, useState } from "react";

import {
  checkSession,
  enterLobby,
  initOnlineSession,
  resumeLastRoom,
} from "../../../online";
import { connectRoom, disconnectRoom } from "../../../room-ws";
import { useOnlineStore } from "../../../state/online-store";
import { AuthScreen } from "./AuthScreen";
import { GameRoomBar } from "./GameRoomBar";
import { LobbyScreen } from "./LobbyScreen";
import { RoomScreen } from "./RoomScreen";

/**
 * 多人模式外壳：认证 → 大厅 → 房间（等待页）→ 游戏进行中。
 * roomStatus 变为 playing 后不再用覆盖层遮挡，而是显示 GameRoomBar 并
 * 复用底层真实游戏界面（MessageList/GameControls/角色栏）。
 */
export function OnlineShell() {
  const view = useOnlineStore((state) => state.view);
  const authStatus = useOnlineStore((state) => state.authStatus);
  const activeWorldId = useOnlineStore((state) => state.activeWorldId);
  const roomStatus = useOnlineStore((state) => state.roomStatus);
  const [roomOpen, setRoomOpen] = useState(false);

  useEffect(() => {
    const unsubscribe = initOnlineSession();
    void (async () => {
      await checkSession();
      // 已有有效 Session（刷新/重启后）直接进入大厅，并尝试回到上次的房间。
      if (useOnlineStore.getState().authStatus === "authenticated") {
        await enterLobby();
        await resumeLastRoom();
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

  const playing = view === "room" && roomStatus === "playing";

  if (playing && !roomOpen) {
    return <GameRoomBar onOpenRoom={() => setRoomOpen(true)} />;
  }

  return (
    <div className="online-overlay" data-testid="online-shell">
      {view === "auth" && <AuthScreen />}
      {view === "lobby" && <LobbyScreen />}
      {view === "room" && (
        <RoomScreen onClose={playing ? () => setRoomOpen(false) : undefined} />
      )}
    </div>
  );
}
