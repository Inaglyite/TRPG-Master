import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadSave, resumeTimeline } from "./panels";
import { returnToStartMenu, switchModule } from "./start";
import { useAppStore } from "./state/app-store";
import { useMessageStore } from "./state/message-store";
import { useStartStore } from "./state/start-store";
import { failSaveLoad, useSaveLoadStore } from "./state/save-load-store";
import { handleServerPayload, setActiveTransport } from "./ws";
import { invokeTurnBranch } from "./renderer";

describe("本地存档直接恢复", () => {
  const send = vi.fn();
  beforeEach(() => {
    failSaveLoad("test_reset");
    send.mockClear();
    setActiveTransport({ send });
    useAppStore.setState({
      mode: "local",
      inputEnabled: false,
      savePanelOpen: true,
      latestBranchTurnId: null,
    });
    useStartStore.setState({
      gameStarted: false,
      gameStarting: false,
      saveLoading: false,
      moduleSwitchPending: false,
      activeModule: "old",
      hint: "",
    });
    useMessageStore.getState().replaceMessages([]);
  });
  afterEach(() => {
    failSaveLoad("test_cleanup");
    setActiveTransport(null);
  });

  it("连续点击只发送一次读取，服务端确认后直接显示存档并启用行动", () => {
    loadSave("slot_001");
    loadSave("slot_001");
    switchModule("other");
    expect(send.mock.calls.map(([value]) => JSON.parse(value).type)).toEqual([
      "save_load",
    ]);
    expect(useStartStore.getState()).toMatchObject({
      saveLoading: true,
      hint: "正在读取存档…",
      gameStarted: false,
    });
    handleServerPayload({
      type: "loaded",
      ok: true,
      restore_only: true,
      world_id: "saved",
      module_name: "old",
      history: [
        {
          turn_id: "saved-message-1",
          narrative: "你站在存档中的大厅。",
          choices: [{ label: "检查书桌", isFree: false }],
        },
      ],
    });
    expect(useStartStore.getState()).toMatchObject({
      saveLoading: true,
      gameStarted: true,
    });
    expect(useSaveLoadStore.getState().progress?.phase).toBe("presenting");
    expect(
      useMessageStore
        .getState()
        .messages.some((m) => m.text === "你站在存档中的大厅。"),
    ).toBe(true);
    expect(
      useMessageStore.getState().messages.some((m) => m.kind === "loading"),
    ).toBe(false);
    expect(useAppStore.getState()).toMatchObject({
      inputEnabled: true,
      choices: [{ label: "检查书桌", isFree: false }],
    });
    // The overlay releases this lock once React has painted the saved game.
    failSaveLoad("test_presented");
    returnToStartMenu();
    switchModule("other");
    expect(JSON.parse(send.mock.calls.at(-1)![0]).type).toBe("switch_module");
  });

  it("跨时间线读取只切换一次，收到确认后才读自动存档", () => {
    resumeTimeline("saved", false);
    const requestId = useSaveLoadStore.getState().progress?.requestId;
    resumeTimeline("saved", false);
    expect(send).toHaveBeenCalledTimes(1);
    handleServerPayload({
      type: "world_switched",
      world_id: "saved",
      module_name: "old",
      history: [],
    });
    expect(send.mock.calls.map(([value]) => JSON.parse(value).type)).toEqual([
      "world_switch",
      "save_load",
      "state",
    ]);
    expect(useStartStore.getState().saveLoading).toBe(true);
    expect(useSaveLoadStore.getState().progress?.requestId).toBe(requestId);
  });

  it("读档后恢复每轮分支按钮，点击仍回到该行动之前且不启用重写", () => {
    handleServerPayload({
      type: "loaded",
      ok: true,
      restore_only: true,
      history_source: "turn_journal",
      history: [
        { turn_id: "opening", narrative: "门仍关着。" },
        {
          turn_id: "open-door",
          parent_turn_id: "opening",
          player_input: "推开门",
          narrative: "门开了。",
        },
      ],
    });
    const replies = useMessageStore
      .getState()
      .messages.filter((m) => m.kind === "gm");
    expect(replies.map((m) => m.canBranch)).toEqual([true, true]);
    expect(replies.some((m) => m.canRewrite)).toBe(false);
    expect(useAppStore.getState().latestBranchTurnId).toBe("opening");
    send.mockClear();
    invokeTurnBranch("open-door");
    expect(send).toHaveBeenCalledExactlyOnceWith(
      JSON.stringify({ type: "turn_branch_create", turn_id: "opening" }),
    );
  });

  it.each([undefined, "saved_messages"])(
    "只有文本的旧存档不伪造分支点：%s",
    (source) => {
      handleServerPayload({
        type: "loaded",
        ok: true,
        restore_only: true,
        history_source: source,
        history: [
          { turn_id: "saved-message-1", narrative: "旧版保存的文字。" },
        ],
      });
      expect(useMessageStore.getState().messages.some((m) => m.canBranch)).toBe(
        false,
      );
      expect(useAppStore.getState().latestBranchTurnId).toBeNull();
      send.mockClear();
      invokeTurnBranch("saved-message-1");
      expect(send).not.toHaveBeenCalled();
    },
  );

  it.each(["world_switch_failed", "error", "turn_rejected"])(
    "%s 后可重新读取",
    (type) => {
      loadSave("slot_001");
      handleServerPayload({
        type,
        message: "读取失败",
        reason: "turn_in_progress",
      });
      expect(useStartStore.getState().saveLoading).toBe(false);
      send.mockClear();
      loadSave("slot_001");
      expect(send).toHaveBeenCalledTimes(1);
    },
  );
});
