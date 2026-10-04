import { beforeEach, describe, expect, it, vi } from "vitest";
import { sendRoomFrameNow } from "./room-send-now";
import { initialOnlineState, useOnlineStore } from "./state/online-store";

describe("immediate room commands", () => {
  beforeEach(() => {
    useOnlineStore.setState({
      ...initialOnlineState,
      activeWorldId: "current-room",
      roomConnection: "connected",
    });
  });
  function connection() {
    return {
      worldId: "current-room",
      snapshotApplied: true,
      socket: { readyState: WebSocket.OPEN as number, send: vi.fn() },
    };
  }
  it.each(["connecting", "disconnected"] as const)(
    "%s does not send, and reconnect does not replay the refused command",
    (roomConnection) => {
      const live = connection();
      useOnlineStore.setState({ roomConnection });
      expect(sendRoomFrameNow(live, "ready")).toBe(false);
      useOnlineStore.setState({ roomConnection: "connected" });
      expect(live.socket.send).not.toHaveBeenCalled();
      expect(sendRoomFrameNow(live, "manual-ready")).toBe(true);
      expect(live.socket.send).toHaveBeenCalledExactlyOnceWith("manual-ready");
    },
  );
  it("an open socket without authority cannot submit", () => {
    const live = connection();
    live.snapshotApplied = false;
    expect(sendRoomFrameNow(live, "start")).toBe(false);
    expect(live.socket.send).not.toHaveBeenCalled();
  });
  it("a previous room socket cannot receive a new room command", () => {
    const live = connection();
    live.worldId = "old-room";
    expect(sendRoomFrameNow(live, "start")).toBe(false);
    expect(live.socket.send).not.toHaveBeenCalled();
  });
  it("a closed socket wins over a stale connected projection", () => {
    const live = connection();
    live.socket.readyState = WebSocket.CLOSED;
    expect(sendRoomFrameNow(live, "start")).toBe(false);
    expect(live.socket.send).not.toHaveBeenCalled();
  });
  it("a synchronous send failure returns not sent without replay", () => {
    const live = connection();
    live.socket.send.mockImplementation(() => {
      throw new Error("closed");
    });
    expect(sendRoomFrameNow(live, "ready")).toBe(false);
    expect(live.socket.send).toHaveBeenCalledTimes(1);
  });
});
