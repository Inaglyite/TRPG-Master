import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAppStore } from "./state/app-store";
import { initialOnlineState, useOnlineStore } from "./state/online-store";
import { safeSend, sendImmediately } from "./ws";
import {
  closeUtility,
  requestNotes,
  saveNotes,
  onPlayerNotes,
  onNotesWorldChanged,
} from "./utility";

vi.mock("./ws", () => ({ safeSend: vi.fn(), sendImmediately: vi.fn() }));

describe("私人笔记断线保护", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useAppStore.setState({ utilityOpen: false });
    onNotesWorldChanged();
    vi.clearAllMocks();
    vi.mocked(sendImmediately).mockReturnValue(true);
    useOnlineStore.setState({ ...initialOnlineState });
    useAppStore.setState({
      mode: "local",
      connection: "disconnected",
      utilityOpen: true,
      notesText: "未保存的私人备忘",
      notesDirty: true,
      notesLoading: false,
      notesSaving: false,
      notesRevision: 3,
      notesStatus: "未保存",
      notesStatusKind: "",
    });
  });
  afterEach(() => vi.useRealTimers());

  it("保存请求未确认时仍保留未保存标识，超时后可恢复操作", () => {
    useAppStore.setState({ connection: "connected" });
    saveNotes();
    expect(useAppStore.getState().notesDirty).toBe(true);
    vi.advanceTimersByTime(20000);
    expect(useAppStore.getState()).toMatchObject({
      notesSaving: false,
      notesDirty: true,
      notesText: "未保存的私人备忘",
      notesStatusKind: "error",
    });
  });

  it("读取请求不返回时有界结束，原草稿保留", () => {
    useAppStore.setState({ connection: "connected" });
    requestNotes();
    vi.advanceTimersByTime(20000);
    expect(useAppStore.getState()).toMatchObject({
      notesLoading: false,
      notesDirty: true,
      notesText: "未保存的私人备忘",
      notesStatusKind: "error",
    });
  });

  it("确认只清理已保存版本，发送后的继续编辑仍未保存", () => {
    useAppStore.setState({ connection: "connected" });
    saveNotes();
    useAppStore.getState().setNotesDraft("又补了一条线索");
    onPlayerNotes({ saved: true, revision: 4, text: "未保存的私人备忘" });
    expect(useAppStore.getState()).toMatchObject({
      notesSaving: false,
      notesDirty: true,
      notesText: "又补了一条线索",
      notesRevision: 4,
    });
  });

  it("其他窗口的读取回包不能冒充本窗口的保存确认", () => {
    useAppStore.setState({ connection: "connected" });
    saveNotes();
    onPlayerNotes({ revision: 3, text: "旧版本" });
    expect(useAppStore.getState()).toMatchObject({
      notesSaving: true,
      notesDirty: true,
      notesText: "未保存的私人备忘",
    });
  });

  it("错误世界、请求与过期回执不能确认当前草稿", () => {
    useAppStore.setState({ connection: "connected", activeWorldId: "world-a" });
    saveNotes();
    const sent = JSON.parse(vi.mocked(sendImmediately).mock.calls[0][0]);
    for (const fields of [
      { world_id: "world-b", request_id: sent.request_id },
      { world_id: "world-a", request_id: "old-request" },
    ]) {
      onPlayerNotes({
        ...fields,
        saved: true,
        revision: 4,
        text: "未保存的私人备忘",
      });
      expect(useAppStore.getState().notesSaving).toBe(true);
    }
    vi.advanceTimersByTime(16000);
    onPlayerNotes({
      world_id: "world-a",
      request_id: sent.request_id,
      saved: true,
      revision: 4,
      text: "未保存的私人备忘",
    });
    expect(useAppStore.getState().notesDirty).toBe(true);
    requestNotes();
    const read = JSON.parse(vi.mocked(sendImmediately).mock.calls.at(-1)![0]);
    onPlayerNotes({
      world_id: "world-a",
      request_id: read.request_id,
      revision: 4,
      text: "未保存的私人备忘",
    });
    expect(useAppStore.getState()).toMatchObject({
      notesDirty: false,
      notesRevision: 4,
      notesStatusKind: "success",
    });
  });

  it("断线保存不进入自动重发队列，不清掉未保存标识", () => {
    saveNotes();
    expect(safeSend).not.toHaveBeenCalled();
    expect(useAppStore.getState()).toMatchObject({
      notesDirty: true,
      notesSaving: false,
      notesText: "未保存的私人备忘",
      notesStatusKind: "error",
    });
  });

  it("断线关闭保留草稿，不排队写入另一个可能已切换的世界", () => {
    closeUtility();
    expect(safeSend).not.toHaveBeenCalled();
    expect(useAppStore.getState()).toMatchObject({
      utilityOpen: false,
      notesDirty: true,
      notesText: "未保存的私人备忘",
    });
  });

  it("断线读取不一直卡在读取中，也不丢掉草稿", () => {
    requestNotes();
    expect(safeSend).not.toHaveBeenCalled();
    expect(useAppStore.getState()).toMatchObject({
      notesLoading: false,
      notesText: "未保存的私人备忘",
    });
  });

  it("状态显示已连接但实际发送失败，仍不假装已保存或清掉草稿", () => {
    useAppStore.setState({ connection: "connected" });
    vi.mocked(sendImmediately).mockReturnValue(false);
    saveNotes();
    expect(safeSend).not.toHaveBeenCalled();
    expect(useAppStore.getState()).toMatchObject({
      notesDirty: true,
      notesSaving: false,
      notesStatusKind: "error",
    });
  });

  it("已连接时直接发送当前版本，不进入重连队列", () => {
    useAppStore.setState({ connection: "connected" });
    saveNotes();
    expect(
      JSON.parse(vi.mocked(sendImmediately).mock.calls[0][0]),
    ).toMatchObject({
      type: "player_notes_update",
      revision: 3,
      text: "未保存的私人备忘",
    });
    expect(
      JSON.parse(vi.mocked(sendImmediately).mock.calls[0][0]).request_id,
    ).toEqual(expect.any(String));
    expect(safeSend).not.toHaveBeenCalled();
    expect(useAppStore.getState().notesSaving).toBe(true);
  });
});
