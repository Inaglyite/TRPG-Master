import { useOnlineStore } from "./state/online-store";

/** No replay queue: only a live socket for the currently restored room can send. */
export function sendRoomFrameNow(
  connection: {
    worldId: string | null;
    snapshotApplied: boolean;
    socket: Pick<WebSocket, "readyState" | "send"> | null;
  },
  payload: string,
): boolean {
  const online = useOnlineStore.getState();
  const socket = connection.socket;
  if (
    !connection.worldId ||
    connection.worldId !== online.activeWorldId ||
    online.roomConnection !== "connected" ||
    !connection.snapshotApplied ||
    !socket ||
    socket.readyState !== WebSocket.OPEN
  )
    return false;
  try {
    socket.send(payload);
    return true;
  } catch {
    return false;
  }
}
