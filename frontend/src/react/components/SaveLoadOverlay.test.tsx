import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { loadSave } from "../../panels";
import { useAppStore } from "../../state/app-store";
import { useStartStore } from "../../state/start-store";
import { failSaveLoad, useSaveLoadStore } from "../../state/save-load-store";
import { handleServerPayload, setActiveTransport } from "../../ws";
import { SaveLoadOverlay } from "./SaveLoadOverlay";

const send = vi.fn();
let frames: FrameRequestCallback[] = [];

beforeEach(() => {
  failSaveLoad("test_reset");
  send.mockClear();
  setActiveTransport({ send });
  useAppStore.setState({ mode: "local", savePanelOpen: true });
  useStartStore.setState({ gameStarted: false, gameStarting: false });
  frames = [];
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    frames.push(callback);
    return frames.length;
  });
  vi.stubGlobal("cancelAnimationFrame", vi.fn());
});

afterEach(() => {
  failSaveLoad("test_cleanup");
  setActiveTransport(null);
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it("点击即显示加载页，慢响应期间持续反馈，画面准备好才关闭并记录总耗时", () => {
  vi.useFakeTimers({
    toFake: [
      "Date",
      "setTimeout",
      "clearTimeout",
      "setInterval",
      "clearInterval",
      "performance",
    ],
  });
  const { container } = render(
    <>
      <button onClick={() => loadSave("slot_001")}>读取</button>
      <div id="start-overlay" />
      <SaveLoadOverlay />
    </>,
  );
  fireEvent.click(screen.getByText("读取"));
  expect(screen.getByRole("dialog")).toHaveTextContent("正在读取存档");
  const requestId = useSaveLoadStore.getState().progress!.requestId;
  act(() => vi.advanceTimersByTime(16_000));
  expect(screen.getByRole("dialog")).toHaveTextContent("读取仍在进行");
  act(() =>
    handleServerPayload({
      type: "save_load_progress",
      request_id: requestId,
      phase: "history",
    }),
  );
  expect(screen.getByRole("status")).toHaveTextContent("正在整理已保存的对话");
  act(() =>
    handleServerPayload({
      type: "loaded",
      ok: true,
      restore_only: true,
      request_id: requestId,
      history: [],
      timings: { server_ms: 15500 },
    }),
  );
  const nextFrame = () => act(() => frames.shift()?.(performance.now()));
  nextFrame();
  expect(screen.getByRole("dialog")).toBeVisible();
  expect(useStartStore.getState().saveLoading).toBe(true);
  // The start screen has finished its real exit; let the game paint.
  container.querySelector("#start-overlay")!.className = "hidden";
  nextFrame();
  nextFrame();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(useStartStore.getState().saveLoading).toBe(false);
  const metrics = send.mock.calls
    .map(([value]) => JSON.parse(value))
    .find((frame) => frame.type === "save_load_timing");
  expect(metrics).toMatchObject({ request_id: requestId, server_ms: 15500 });
  expect(metrics.total_ms).toBeGreaterThanOrEqual(16_000);
  vi.useRealTimers();
});

it.each(["error", "world_switch_failed", "turn_rejected"])(
  "%s 结束等待，保留错误反馈并允许重新读取",
  (type) => {
    render(<SaveLoadOverlay />);
    act(() => loadSave("slot_001"));
    act(() =>
      handleServerPayload({ type, message: "读取失败", reason: "busy" }),
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(useStartStore.getState().saveLoading).toBe(false);
    act(() => loadSave("slot_001"));
    expect(screen.getByRole("dialog")).toBeVisible();
  },
);

it("旧请求的阶段和完成回执不能结束本次读取", () => {
  render(<SaveLoadOverlay />);
  act(() => loadSave("slot_001"));
  act(() =>
    handleServerPayload({
      type: "loaded",
      ok: true,
      restore_only: true,
      request_id: "save-old",
      history: [],
    }),
  );
  expect(useStartStore.getState().gameStarted).toBe(false);
  expect(screen.getByRole("status")).toHaveTextContent("正在恢复存档进度");
});
