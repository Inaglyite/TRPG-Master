import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiHttpOrigin } from "./api/client";
import {
  createRoom,
  createSoloWorld,
  joinWithToken,
  login,
  newInvite,
} from "./online";
import { initialOnlineState, useOnlineStore } from "./state/online-store";

vi.mock("./room-ws", () => ({
  disconnectRoom: vi.fn(),
  clearPendingSoloSwitch: vi.fn(),
  newActionId: () => "test-action",
  roomSend: vi.fn(),
}));

describe("Online flows with real API deadline", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise(() => {})),
    );
    useOnlineStore.setState({
      ...initialOnlineState,
      view: "lobby",
      authStatus: "authenticated",
      authOrigin: apiHttpOrigin(),
      user: { id: "deadline-user", username: "test-only" },
    });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it.each([
    {
      name: "创建多人房间",
      run: () =>
        createRoom("test-module", "测试房间", 3, {
          structured: true,
          keeperMode: "human",
        }),
      busy: "createBusy",
      error: "createError",
    },
    {
      name: "创建云端单人",
      run: () =>
        createSoloWorld("test-module", "测试冒险", {
          structured: true,
          keeperMode: "human",
        }),
      busy: "createBusy",
      error: "createError",
    },
    {
      name: "邀请加入",
      run: () => joinWithToken("test-token"),
      busy: "joinBusy",
      error: "joinError",
    },
  ])(
    "$name 不无限忙碌，不自动重发或伪造新世界",
    async ({ run, busy, error }) => {
      const pending = run();
      expect(useOnlineStore.getState()[busy as "createBusy" | "joinBusy"]).toBe(
        true,
      );
      await vi.advanceTimersByTimeAsync(30000);
      await pending;
      expect(useOnlineStore.getState()[busy as "createBusy" | "joinBusy"]).toBe(
        false,
      );
      expect(
        useOnlineStore.getState()[error as "createError" | "joinError"],
      ).toContain("不代表");
      expect(useOnlineStore.getState().activeWorldId).toBeNull();
      expect(fetch).toHaveBeenCalledTimes(1);
    },
  );

  it("登录恢复操作，不把超时当作密码错误或已登录", async () => {
    useOnlineStore.setState({ authStatus: "anonymous", user: null });
    const pending = login("test-only", "not-a-real-password");
    expect(useOnlineStore.getState().authBusy).toBe(true);
    await vi.advanceTimersByTimeAsync(30000);
    expect(await pending).toBe(false);
    expect(useOnlineStore.getState()).toMatchObject({
      authBusy: false,
      authStatus: "anonymous",
      authErrorCode: "request_timeout",
      user: null,
    });
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("创建邀请超时不展示虚假的邀请码", async () => {
    useOnlineStore.setState({ activeWorldId: "world-test" });
    const pending = newInvite({ role: "player" });
    await vi.advanceTimersByTimeAsync(30000);
    await pending;
    expect(useOnlineStore.getState()).toMatchObject({
      inviteBusy: false,
      invite: null,
      activeWorldId: "world-test",
    });
    expect(useOnlineStore.getState().roomError).toContain("不代表");
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
