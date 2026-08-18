import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";

import {
  archiveWorld,
  confirmEnding,
  createBranchFromCurrentTurn,
  createSave,
  loadSave,
  openSavePanel,
  quickSave,
  renameWorld,
  resumeTimeline,
} from "./panels";
import { addMsg } from "./renderer";
import { useAppStore } from "./state/app-store";
import {
  initialOnlineState,
  timelineCapabilities,
  useOnlineStore,
} from "./state/online-store";
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
  playMode: string | null = null,
) {
  useOnlineStore.setState({
    ...initialOnlineState,
    authStatus: "authenticated",
    user: { id: "u1", username: "alice" },
    members: [{ user_id: "u1", username: "alice", role }],
    playMode,
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

describe("本地时间线归档协议帧", () => {
  it("只在本地模式发送 world_archive", () => {
    setupRoom("owner", "local");
    archiveWorld("branch-a");
    expect(safeSend).toHaveBeenCalledWith(
      JSON.stringify({ type: "world_archive", world_id: "branch-a" }),
    );
  });

  it("联机模式不发送本地时间线归档命令", () => {
    setupRoom("owner", "online");
    archiveWorld("branch-a");
    expect(safeSend).not.toHaveBeenCalled();
  });
});

describe("云端单人时间线能力", () => {
  it("solo 房主能力全 true；多人房间与非房主全 false", () => {
    setupRoom("owner", "online", "solo");
    expect(timelineCapabilities()).toEqual({
      canList: true,
      canCreateBranch: true,
      canSwitch: true,
      canRename: true,
      canArchive: true,
    });
    setupRoom("owner", "online", "multiplayer");
    expect(timelineCapabilities()).toEqual({
      canList: false,
      canCreateBranch: false,
      canSwitch: false,
      canRename: false,
      canArchive: false,
    });
    setupRoom("player", "online", "solo");
    expect(timelineCapabilities().canList).toBe(false);
  });

  it("online solo 房间发送 solo_* 时间线消息", () => {
    setupRoom("owner", "online", "solo");
    renameWorld("branch-a", "另一条路");
    expect(safeSend).toHaveBeenCalledWith(
      JSON.stringify({
        type: "solo_world_rename",
        world_id: "branch-a",
        label: "另一条路",
      }),
    );
    archiveWorld("branch-a");
    expect(safeSend).toHaveBeenCalledWith(
      JSON.stringify({ type: "solo_world_archive", world_id: "branch-a" }),
    );
    resumeTimeline("branch-b", false);
    expect(safeSend).toHaveBeenCalledWith(
      JSON.stringify({ type: "solo_world_switch", world_id: "branch-b" }),
    );
    useAppStore.setState({ latestBranchTurnId: "turn-9" });
    createBranchFromCurrentTurn("分支");
    expect(safeSend).toHaveBeenCalledWith(
      JSON.stringify({
        type: "solo_branch_create",
        turn_id: "turn-9",
        label: "分支",
      }),
    );
    // 绝不退化为本地时间线消息名
    expect(safeSend).not.toHaveBeenCalledWith(
      expect.stringContaining('"world_rename"'),
    );
    expect(safeSend).not.toHaveBeenCalledWith(
      expect.stringContaining('"turn_branch_create"'),
    );
  });

  it("多人房间所有 solo_* 操作都被阻断", () => {
    setupRoom("owner", "online", "multiplayer");
    useAppStore.setState({ latestBranchTurnId: "turn-9" });
    renameWorld("branch-a", "x");
    archiveWorld("branch-a");
    resumeTimeline("branch-b", false);
    createBranchFromCurrentTurn("分支");
    expect(safeSend).not.toHaveBeenCalled();
  });

  it("online solo 打开存档面板时加发 solo_world_list（save_list 保持）", () => {
    setupRoom("owner", "online", "solo");
    openSavePanel();
    expect(safeSend).toHaveBeenCalledWith(
      JSON.stringify({ type: "save_list" }),
    );
    expect(safeSend).toHaveBeenCalledWith(
      JSON.stringify({ type: "solo_world_list" }),
    );
    expect(safeSend).not.toHaveBeenCalledWith(
      JSON.stringify({ type: "world_list" }),
    );
  });

  it("online 当前时间线只关闭面板，不读 slot_000", () => {
    setupRoom("owner", "online", "solo");
    useAppStore.setState({ savePanelOpen: true });
    resumeTimeline("world-active", true);
    expect(useAppStore.getState().savePanelOpen).toBe(false);
    expect(safeSend).not.toHaveBeenCalled();
  });
});
