import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
const transport = vi.hoisted(() => ({ immediate: vi.fn(), queued: vi.fn() }));
vi.mock("./ws", () => ({
  sendImmediately: transport.immediate,
  safeSend: transport.queued,
}));
import {
  onLocalStartResult,
  onStartTurnRejected,
  returnToStartMenu,
  startGame,
} from "./start";
import { useAppStore } from "./state/app-store";
import { useStartStore } from "./state/start-store";
import { useStructuredStore } from "./state/structured-store";

describe("explicit local creation receipts", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useStartStore.setState({ gameStarting: false });
    returnToStartMenu();
    vi.clearAllMocks();
    transport.immediate.mockReturnValue(true);
    useAppStore.setState({ mode: "local", activeWorldId: "original-world" });
    useStructuredStore.setState({ capabilities: undefined });
    useStartStore.setState({
      executionProfile: "structured_v1",
      keeperMode: "human",
      selectedCharacterRef: { source: "module", id: "pc" },
    });
  });
  afterEach(() => {
    useStartStore.setState({ gameStarting: false });
    returnToStartMenu();
    vi.useRealTimers();
  });
  it("does not queue an offline creation for automatic replay", () => {
    transport.immediate.mockReturnValue(false);
    startGame();
    expect(transport.queued).not.toHaveBeenCalled();
    expect(useStartStore.getState()).toMatchObject({
      gameStarting: false,
      gameStarted: false,
    });
    expect(useStartStore.getState().hint).toContain("尚未发送");
  });
  it("does not automatically replay a busy creation", () => {
    startGame();
    expect(onStartTurnRejected("忙碌，请重试", true)).toBe(true);
    vi.advanceTimersByTime(60000);
    expect(transport.immediate).toHaveBeenCalledTimes(1);
    expect(useStartStore.getState().gameStarting).toBe(false);
  });
  it("retries the identical nonce and original source after context arrives but receipt is lost", () => {
    startGame();
    const original = transport.immediate.mock.calls[0][0];
    useAppStore.setState({ activeWorldId: "new-committed-world" });
    vi.advanceTimersByTime(15000);
    expect(useStartStore.getState().gameStarting).toBe(false);
    startGame();
    expect(transport.immediate.mock.calls[1][0]).toBe(original);
    expect(JSON.parse(original).source_world_id).toBe("original-world");
  });
  it("cannot silently change an unconfirmed creation's character or mode", () => {
    startGame();
    vi.advanceTimersByTime(15000);
    useStartStore.setState({ keeperMode: "agent" });
    startGame();
    expect(transport.immediate).toHaveBeenCalledTimes(1);
    expect(useStartStore.getState().hint).toContain("尚未确认");
  });
  it("ignores foreign receipts and accepts only the matching committed world", () => {
    startGame();
    const frame = JSON.parse(transport.immediate.mock.calls[0][0]);
    onLocalStartResult({
      request_id: "foreign",
      ok: true,
      world_id: "original-world",
    });
    onLocalStartResult({
      request_id: frame.request_id,
      ok: true,
      world_id: "wrong-world",
    });
    expect(useStartStore.getState().gameStarted).toBe(false);
    useAppStore.setState({ activeWorldId: "new-world" });
    onLocalStartResult({
      request_id: frame.request_id,
      ok: true,
      world_id: "new-world",
    });
    expect(useStartStore.getState()).toMatchObject({
      gameStarted: true,
      gameStarting: false,
    });
  });
  it("a failure does not clear the original request, while an explicit return cancels it", () => {
    startGame();
    const original = transport.immediate.mock.calls[0][0];
    const frame = JSON.parse(original);
    onLocalStartResult({
      request_id: frame.request_id,
      ok: false,
      message: "拒绝",
    });
    startGame();
    expect(transport.immediate.mock.calls[1][0]).toBe(original);
    useStartStore.setState({ gameStarting: false });
    returnToStartMenu();
    onLocalStartResult({
      request_id: frame.request_id,
      ok: true,
      world_id: "original-world",
    });
    expect(useStartStore.getState().gameStarted).toBe(false);
    startGame();
    expect(
      JSON.parse(transport.immediate.mock.calls[2][0]).request_id,
    ).not.toBe(frame.request_id);
  });
});
