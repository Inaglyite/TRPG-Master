import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ReasoningPanel } from "./ReasoningPanel";
import { ModelSettingsPanel } from "./ModelSettingsPanel";
import {
  useGamePreferences,
  readReasoningPreference,
} from "../../state/game-preferences";
import {
  useReasoningStore,
  type ReasoningFrame,
} from "../../state/reasoning-store";
import { useMessageStore } from "../../state/message-store";
import { useAppStore } from "../../state/app-store";
import { useModelStore } from "../../state/model-store";
import { handleServerPayload } from "../../ws";
import { parseServerMessage } from "../../protocol/server-message";

const base = {
  type: "model_reasoning",
  request_id: "request-1",
  model: "test-model",
  role: "story",
  world_id: "world-a",
} as const;
function frame(
  phase: ReasoningFrame["phase"],
  rest: Partial<ReasoningFrame> = {},
) {
  return { ...base, phase, ...rest };
}
beforeEach(() => {
  localStorage.clear();
  useAppStore.setState({ activeWorldId: "world-a", connection: "connected" });
  useGamePreferences.setState({ showReasoning: false, showActionHints: true });
  useReasoningStore.getState().reset();
  useMessageStore.setState({ messages: [] });
  useModelStore.setState({
    open: true,
    tab: "general",
    view: null,
    loading: false,
  });
});

describe("private model reasoning", () => {
  it("defaults off, persists and clears the transient content when switched off", () => {
    render(
      <>
        <ModelSettingsPanel />
        <ReasoningPanel />
      </>,
    );
    expect(readReasoningPreference()).toBe(false);
    expect(
      screen.queryByRole("region", { name: "模型思考内容" }),
    ).not.toBeInTheDocument();
    const toggle = screen.getByRole("switch", { name: "显示思维链" });
    fireEvent.click(toggle);
    expect(readReasoningPreference()).toBe(true);
    act(() => {
      handleServerPayload(frame("start"));
      handleServerPayload(frame("delta", { text: "模型的私有分析" }));
    });
    expect(screen.getByText("模型的私有分析")).toBeVisible();
    expect(useMessageStore.getState().messages).toEqual([]);
    fireEvent.click(toggle);
    expect(useReasoningStore.getState().entries).toEqual([]);
    expect(screen.queryByText("模型的私有分析")).not.toBeInTheDocument();
  });
  it("drops incoming thinking while off and does not persist raw text", () => {
    handleServerPayload(frame("start"));
    handleServerPayload(frame("delta", { text: "不缓存" }));
    expect(useReasoningStore.getState().entries).toEqual([]);
    useGamePreferences.getState().setShowReasoning(true);
    handleServerPayload(frame("start"));
    handleServerPayload(frame("delta", { text: "只存内存" }));
    expect(JSON.stringify(localStorage)).not.toContain("只存内存");
  });
  it("starts at the next delta when enabled midway and clears on disconnect", () => {
    handleServerPayload(frame("start"));
    handleServerPayload(frame("delta", { text: "关闭时的片段" }));
    useGamePreferences.getState().setShowReasoning(true);
    handleServerPayload(frame("delta", { text: "开启后的片段" }));
    expect(useReasoningStore.getState().entries[0].text).toBe("开启后的片段");
    useAppStore.setState({ connection: "disconnected" });
    expect(useReasoningStore.getState().entries).toEqual([]);
  });
  it("renders plain text, ends interrupted streams and ignores late/foreign frames", () => {
    useGamePreferences.getState().setShowReasoning(true);
    render(<ReasoningPanel />);
    act(() => {
      handleServerPayload(frame("start"));
      handleServerPayload(frame("delta", { text: "<script>秘密</script>" }));
      handleServerPayload(frame("end", { status: "interrupted" }));
      handleServerPayload(frame("delta", { text: "迟到" }));
      handleServerPayload(
        frame("start", { request_id: "foreign", world_id: "other" }),
      );
    });
    expect(screen.getByText("<script>秘密</script>")).toBeVisible();
    expect(document.querySelector(".model-reasoning-panel script")).toBeNull();
    expect(screen.getByText("已中断")).toBeVisible();
    expect(useReasoningStore.getState().entries).toHaveLength(1);
    expect(useReasoningStore.getState().entries[0].text).not.toContain("迟到");
    act(() => useAppStore.setState({ activeWorldId: "other" }));
    expect(useReasoningStore.getState().entries).toEqual([]);
  });
  it("bounds frames and memory and retains disabled-storage session control", () => {
    useGamePreferences.getState().setShowReasoning(true);
    expect(
      parseServerMessage(frame("delta", { text: "a".repeat(4097) })),
    ).toBeNull();
    expect(parseServerMessage({ ...frame("start"), phase: "bad" })).toBeNull();
    for (let index = 0; index < 10; index++)
      handleServerPayload(frame("start", { request_id: String(index) }));
    expect(useReasoningStore.getState().entries).toHaveLength(8);
    const spy = vi
      .spyOn(Storage.prototype, "setItem")
      .mockImplementation(() => {
        throw Error("blocked");
      });
    useGamePreferences.getState().setShowReasoning(false);
    expect(useGamePreferences.getState().showReasoning).toBe(false);
    spy.mockRestore();
  });
});
