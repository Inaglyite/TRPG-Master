import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";

import {
  confirmEnding,
  createSave,
  loadSave,
  openSavePanel,
  quickSave,
} from "./panels";
import { addMsg } from "./renderer";
import { useAppStore } from "./state/app-store";
import { initialOnlineState, useOnlineStore } from "./state/online-store";
import { safeSend } from "./ws";

vi.mock("./ws", () => ({
  safeSend: vi.fn(),
}));

vi.mock("./renderer", () => ({
  addMsg: vi.fn(),
  removeLoading: vi.fn(),
}));

vi.mock("./options", () => ({
  enableInput: vi.fn(),
}));

vi.mock("./start", () => ({
  getGameStarted: vi.fn(() => true),
}));

function setupRoom(
  role: "owner" | "player",
  mode: "online" | "local" = "online",
) {
  useOnlineStore.setState({
    ...initialOnlineState,
    authStatus: "authenticated",
    user: { id: "u1", username: "alice" },
    members: [{ user_id: "u1", username: "alice", role }],
  });
  useAppStore.setState({ mode });
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("房主专属操作门禁（多人）", () => {
  it("非房主不能快速存档/读档/新建存档/结案", () => {
    setupRoom("player");
    quickSave();
    loadSave("slot_001");
    createSave();
    confirmEnding({ ending_type: "good", title: "结局", summary: "…" });
    expect(safeSend).not.toHaveBeenCalled();
    expect(addMsg).toHaveBeenCalledWith(
      "system",
      "多人房间中，存档与结案操作仅房主可用。",
    );
  });

  it("房主可以正常执行", () => {
    setupRoom("owner");
    quickSave();
    expect(safeSend).toHaveBeenCalledWith(
      JSON.stringify({ type: "save", manual: false }),
    );
    // 结束本次快速存档的 pending 窗口，避免影响后续用例。
    vi.advanceTimersByTime(9000);
    loadSave("slot_001");
    expect(safeSend).toHaveBeenCalledWith(
      JSON.stringify({ type: "save_load", slot_id: "slot_001" }),
    );
    createSave();
    expect(safeSend).toHaveBeenCalledWith(
      JSON.stringify({ type: "save_create" }),
    );
  });

  it("单机模式不做房主限制", () => {
    setupRoom("player", "local");
    quickSave();
    expect(safeSend).toHaveBeenCalledWith(
      JSON.stringify({ type: "save", manual: false }),
    );
  });
});

describe("openSavePanel 协议帧", () => {
  it("联机模式不发送 world_list（房间协议无此处理器，会收 protocol_error）", () => {
    setupRoom("owner", "online");
    openSavePanel();
    expect(safeSend).toHaveBeenCalledWith(
      JSON.stringify({ type: "save_list" }),
    );
    expect(safeSend).not.toHaveBeenCalledWith(
      JSON.stringify({ type: "world_list" }),
    );
  });

  it("本地模式同时请求 save_list 与 world_list", () => {
    setupRoom("owner", "local");
    openSavePanel();
    expect(safeSend).toHaveBeenCalledWith(
      JSON.stringify({ type: "save_list" }),
    );
    expect(safeSend).toHaveBeenCalledWith(
      JSON.stringify({ type: "world_list" }),
    );
  });
});
