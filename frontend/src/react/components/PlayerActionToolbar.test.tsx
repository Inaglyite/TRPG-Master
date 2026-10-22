import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PlayerActionToolbar } from "./PlayerActionToolbar";
import { useAppStore } from "../../state/app-store";
import { initialOnlineState, useOnlineStore } from "../../state/online-store";
import {
  initialStructuredState,
  useStructuredStore,
} from "../../state/structured-store";
import { useMessageStore } from "../../state/message-store";
import { useSceneStore } from "../../state/scene-store";
import {
  getPlayerTools,
  organizeTurn,
  type PlayerTools,
} from "../../api/playerTools";
import { sendPlayerText } from "../../options";
import { sendStructuredAction } from "../../structured-transport";
import { requestNotes, saveNotes } from "../../utility";
import { STRUCTURED_CAPABILITIES } from "../../protocol/structured-fixtures";

vi.mock("../../api/playerTools", () => ({
  getPlayerTools: vi.fn(),
  organizeTurn: vi.fn(),
}));
vi.mock("../../options", () => ({ sendPlayerText: vi.fn() }));
vi.mock("../../structured-transport", () => ({
  sendStructuredAction: vi.fn(),
}));
vi.mock("../../utility", () => ({ requestNotes: vi.fn(), saveNotes: vi.fn() }));

const data: PlayerTools = {
  world_id: "w",
  revision: 3,
  character_name: "调查员",
  can_organize: true,
  skills: [
    { id: "spot_hidden", name: "侦查", value: 73, trained: true },
    { id: "history", name: "历史", value: 40, trained: true },
  ],
  map: {
    nodes: [
      { id: "study", name: "书房", current: true },
      { id: "street", name: "街口", current: false },
    ],
    edges: [{ from: "study", to: "street" }],
  },
};
function mount(canAct = true) {
  return render(
    <PlayerActionToolbar
      canAct={canAct}
      draft="检查桌边划痕"
      onActionSent={vi.fn()}
    />,
  );
}
beforeEach(() => {
  vi.clearAllMocks();
  useOnlineStore.setState({ ...initialOnlineState });
  useStructuredStore.setState({ ...initialStructuredState });
  useSceneStore.getState().reset();
  useAppStore.setState({
    mode: "local",
    activeWorldId: "w",
    connection: "connected",
    notesText: "已有私人笔记",
    notesRevision: 2,
    notesDirty: false,
    notesLoading: false,
    notesLoaded: true,
    notesSaving: false,
    notesStatusKind: "",
    notesStatus: "",
  });
  useMessageStore.getState().replaceMessages([
    {
      id: "gm1",
      turnId: "turn1",
      kind: "gm",
      text: "门房说昨夜听见了钟声。",
    },
  ]);
  vi.mocked(getPlayerTools).mockResolvedValue(data);
  vi.mocked(organizeTurn).mockResolvedValue({
    world_id: "w",
    source_id: "turn1",
    items: [{ text: "门房声称听见钟声，待核实。", quote: "昨夜听见了钟声" }],
  });
  vi.mocked(sendPlayerText).mockReturnValue({ ok: true });
  vi.mocked(sendStructuredAction).mockReturnValue({
    ok: true,
    requestId: "req",
    payload: {},
  });
});

describe("PlayerActionToolbar", () => {
  it("opening and selecting a skill only edits intent; explicit submit sends the described action", async () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: "使用技能" }));
    fireEvent.click(await screen.findByRole("button", { name: "侦查 73%" }));
    expect(sendPlayerText).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "提交技能行动" }));
    expect(sendPlayerText).toHaveBeenCalledWith(
      "我尝试使用「侦查」，检查桌边划痕",
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("a rejected action preserves the editor and its purpose", async () => {
    vi.mocked(sendPlayerText).mockReturnValue({
      ok: false,
      reason: "断线，请重试",
    });
    mount();
    fireEvent.click(screen.getByRole("button", { name: "使用技能" }));
    fireEvent.click(await screen.findByRole("button", { name: "侦查 73%" }));
    fireEvent.click(screen.getByRole("button", { name: "提交技能行动" }));
    expect(screen.getByRole("alert")).toHaveTextContent("断线");
    expect(screen.getByLabelText("行动目标与做法")).toHaveValue("检查桌边划痕");
  });

  it("the visited graph sends a structured move only after confirmation", async () => {
    useStructuredStore.setState({ capabilities: STRUCTURED_CAPABILITIES });
    mount();
    fireEvent.click(screen.getByRole("button", { name: "场景移动" }));
    expect(
      await screen.findByRole("button", { name: "书房 当前位置" }),
    ).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "街口 已抵达" }));
    expect(sendStructuredAction).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "提出移动" }));
    expect(sendStructuredAction).toHaveBeenCalledWith(
      { kind: "move", destination_scene_id: "street" },
      "前往街口",
    );
    expect(sendPlayerText).not.toHaveBeenCalled();
  });

  it("legacy travel uses the existing action channel without changing the current scene", async () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: "场景移动" }));
    fireEvent.click(await screen.findByRole("button", { name: "街口 已抵达" }));
    fireEvent.click(screen.getByRole("button", { name: "提出移动" }));
    expect(sendPlayerText).toHaveBeenCalledWith(
      expect.stringContaining("我准备前往「街口」"),
    );
    expect(useSceneStore.getState().status).toBe("syncing");
  });

  it("digest is a preview until approved and appends without overwriting existing notes", async () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: "整理线索" }));
    expect(requestNotes).toHaveBeenCalledTimes(1);
    expect(await screen.findByLabelText("摘录1内容")).toHaveValue(
      "门房声称听见钟声，待核实。",
    );
    expect(useAppStore.getState().notesText).toBe("已有私人笔记");
    expect(sendPlayerText).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "加入调查笔记" }));
    expect(useAppStore.getState().notesText).toContain(
      "已有私人笔记\n\n### 探案摘录",
    );
    expect(useAppStore.getState().notesText).toContain("原文：昨夜听见了钟声");
    const saved = useAppStore.getState().notesText;
    fireEvent.click(screen.getByRole("button", { name: "重试保存笔记" }));
    expect(useAppStore.getState().notesText).toBe(saved);
    expect(saveNotes).toHaveBeenCalledTimes(2);
  });

  it("when no model is available, the player can still manually excerpt the turn", async () => {
    vi.mocked(organizeTurn).mockRejectedValue(new Error("暂时无法自动整理"));
    mount();
    fireEvent.click(screen.getByRole("button", { name: "整理线索" }));
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: "手动摘录本轮文字" }));
    expect(screen.getByLabelText("摘录1内容")).toHaveValue(
      "门房说昨夜听见了钟声。",
    );
    expect(screen.getByRole("button", { name: "加入调查笔记" })).toBeEnabled();
  });

  it("never saves an excerpt before the existing notes have been read", async () => {
    useAppStore.setState({ notesLoaded: false });
    mount();
    fireEvent.click(screen.getByRole("button", { name: "整理线索" }));
    await screen.findByLabelText("摘录1内容");
    expect(screen.getByRole("button", { name: "加入调查笔记" })).toBeDisabled();
    expect(saveNotes).not.toHaveBeenCalled();
    act(() =>
      useAppStore.getState().applyNotes({ text: "服务器旧笔记", revision: 8 }),
    );
    fireEvent.click(screen.getByRole("button", { name: "加入调查笔记" }));
    expect(useAppStore.getState().notesText).toContain("服务器旧笔记");
  });

  it("world changes close the editor and discard late projection and digest replies", async () => {
    let resolve!: (value: PlayerTools) => void;
    vi.mocked(getPlayerTools).mockReturnValue(
      new Promise((done) => {
        resolve = done;
      }),
    );
    mount();
    fireEvent.click(screen.getByRole("button", { name: "使用技能" }));
    act(() => useAppStore.setState({ activeWorldId: "other" }));
    await act(async () => resolve(data));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(sendPlayerText).not.toHaveBeenCalled();
  });

  it("busy players can read the map but cannot submit; lost connection disables all entries", async () => {
    const view = mount(false);
    expect(screen.getByRole("button", { name: "使用技能" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "整理线索" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "场景移动" }));
    fireEvent.click(await screen.findByRole("button", { name: "街口 已抵达" }));
    expect(screen.getByRole("button", { name: "提出移动" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "关闭场景移动" }));
    act(() => useAppStore.setState({ connection: "disconnected" }));
    view.rerender(
      <PlayerActionToolbar canAct={true} draft="" onActionSent={vi.fn()} />,
    );
    expect(screen.getByRole("button", { name: "场景移动" })).toBeDisabled();
  });

  it("Escape returns focus and opening a tool never submits an action", async () => {
    mount();
    const trigger = screen.getByRole("button", { name: "使用技能" });
    trigger.focus();
    fireEvent.click(trigger);
    await screen.findByRole("button", { name: "侦查 73%" });
    fireEvent.keyDown(document.activeElement!, {
      key: "Escape",
      isComposing: true,
    });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    await waitFor(() => expect(trigger).toHaveFocus());
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(sendPlayerText).not.toHaveBeenCalled();
  });
});
