import { useAppStore } from "./state/app-store";
import { isRoomOwner, useOnlineStore } from "./state/online-store";
import { useStructuredStore } from "./state/structured-store";
import { interactionPath } from "./protocol/structured";

/** Read-only policy hint; the server rechecks actual owner, claims and revision. */
export function saveReadBlockReason(): string | null {
  if (useAppStore.getState().mode !== "online") return null;
  if (!isRoomOwner()) return "仅房主可以读取房间存档。";
  const online = useOnlineStore.getState();
  const structured = useStructuredStore.getState();
  if (
    interactionPath(structured.capabilities) !== "structured" &&
    online.roomMetadata?.execution_profile !== "structured_v1"
  )
    return null;
  if (online.playMode !== "solo")
    return "当前多人房间不支持读档或创建分支，避免回滚其他玩家的进度。";
  if (!structured.capabilities.structuredSoloRestore)
    return "当前服务器未开放本模式的单人读档。可以保存或管理存档点，不能读取。";
  if (
    online.roomConnection !== "connected" ||
    !online.roomSnapshotReady ||
    useAppStore.getState().connection !== "connected"
  )
    return "正在连接并同步当前进度，请同步完成后读取。";
  return null;
}

export function useSaveReadBlockReason(): string | null {
  useAppStore((state) => state.mode);
  useAppStore((state) => state.connection);
  useStructuredStore((state) => state.capabilities);
  useOnlineStore((state) => state.user);
  useOnlineStore((state) => state.members);
  useOnlineStore((state) => state.playMode);
  useOnlineStore((state) => state.roomMetadata);
  useOnlineStore((state) => state.roomConnection);
  useOnlineStore((state) => state.roomSnapshotReady);
  return saveReadBlockReason();
}
