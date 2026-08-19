import { useEffect } from "react";

import { useAppStore } from "../state/app-store";
import { connect } from "../ws";
import { BootLoader } from "./components/BootLoader";
import { GameShell } from "./GameShell";

export function App() {
  const mode = useAppStore((state) => state.mode);

  useEffect(() => {
    // 仅单机模式连接本地游戏 WebSocket；多人模式的 /ws/room 由房间流程
    // （room-ws.ts）负责，登录前不建立任何游戏连接。
    if (mode === "local") connect();
  }, [mode]);

  return (
    <>
      <GameShell />
      <BootLoader />
    </>
  );
}
