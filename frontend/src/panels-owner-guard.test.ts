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

/** 结构化能力协商开/关（用例之间必须复位，否则互相污染）。 */
async function setStructuredWorld(on: boolean, revision = 0) {
  const { initialStructuredState, useStructuredStore } =
    await import("./state/structured-store");
  const { STRUCTURED_CAPABILITIES_WIRE } =
    await import("./protocol/structured-fixtures");
  useStructuredStore.setState({ ...initialStructuredState });
  if (on) {
    useStructuredStore
      .getState()
      .applyCapabilities(STRUCTURED_CAPABILITIES_WIRE);
    useStructuredStore.getState().setRevision(revision);
  }
}

function lastSent(): string {
  const calls = vi.mocked(safeSend).mock.calls;
  return String(calls[calls.length - 1]?.[0] ?? "");
}

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

  it("结构化世界没有 turn 时不编造 turn_id（本地与云端都只发当前进度）", async () => {
    await setStructuredWorld(true, 12);
    // 结构化世界没有旧回合：latestBranchTurnId 保持为空
    useAppStore.setState({ latestBranchTurnId: null });

    setupRoom("owner", "local", null);
    createBranchFromCurrentTurn("分支");
    const localFrame = lastSent();
    expect(JSON.parse(localFrame)).toEqual({
      type: "turn_branch_create",
      // 分叉点钉在当前已提交 revision（服务端不一致时拒绝，而不是静默分叉旧状态）
      expected_revision: 12,
      label: "分支",
    });
    expect(localFrame).not.toContain("turn_id");

    setupRoom("owner", "online", "solo");
    createBranchFromCurrentTurn("分支");
    const onlineFrame = lastSent();
    expect(JSON.parse(onlineFrame)).toEqual({
      type: "solo_branch_create",
      expected_revision: 12,
      label: "分支",
    });
    expect(onlineFrame, "结构化分支不得伪造 turn_id").not.toContain("turn_id");

    // 权限契约不变：多人房间一律不发（服务端也会拒 solo_* 消息）
    setupRoom("owner", "online", "multiplayer");
    vi.mocked(safeSend).mockClear();
    createBranchFromCurrentTurn("分支");
    expect(safeSend).not.toHaveBeenCalled();
    await setStructuredWorld(false);
  });

  it("旧世界仍然要求最近完成回合（没有 turn 就不发分支）", async () => {
    await setStructuredWorld(false);
    setupRoom("owner", "local", null);
    useAppStore.setState({ latestBranchTurnId: null });
    createBranchFromCurrentTurn("分支");
    expect(safeSend).not.toHaveBeenCalled();
  });

  it("旧世界分支仍带真实 turn_id，且不混入结构化字段", async () => {
    await setStructuredWorld(false);
    setupRoom("owner", "local", null);
    useAppStore.setState({ latestBranchTurnId: "turn-7" });
    createBranchFromCurrentTurn("分支");
    const frame = JSON.parse(lastSent()) as Record<string, unknown>;
    expect(frame).toEqual({
      type: "turn_branch_create",
      turn_id: "turn-7",
      label: "分支",
    });
    expect(lastSent()).not.toContain("expected_revision");
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
