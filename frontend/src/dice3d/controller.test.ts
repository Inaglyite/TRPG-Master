import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const diceMock = vi.hoisted(() => ({
  initialize: vi.fn<() => Promise<void>>(),
  roll: vi.fn<(notation: string) => Promise<unknown>>(),
  clearDice: vi.fn(),
  dispose: vi.fn(),
  forceContextLoss: vi.fn(),
  importError: null as unknown,
}));

vi.mock("@3d-dice/dice-box-threejs", () => {
  class MockDiceBox {
    initialize = diceMock.initialize;
    roll = diceMock.roll;
    clearDice = diceMock.clearDice;
    renderer = {
      dispose: diceMock.dispose,
      forceContextLoss: diceMock.forceContextLoss,
      domElement: document.createElement("canvas"),
    };
  }
  return {
    // getter 让单次测试可以模拟 chunk 加载失败
    get default() {
      if (diceMock.importError) throw diceMock.importError;
      return MockDiceBox;
    },
  };
});

import {
  cancelDice3D,
  dice3DDiagnostics,
  INIT_TIMEOUT_MS,
  isDice3DBusy,
  resetDice3DTheme,
  ROLL_TIMEOUT_MS,
  rollDice3D,
} from "./controller";

const DICE = [{ min: 1, max: 20, final: 17, label: "d20" }];

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function overlay(): HTMLElement {
  return document.querySelector(".dice-3d-overlay") as HTMLElement;
}

describe("3D dice controller", () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="chat-panel"></div>';
    vi.stubGlobal("WebGLRenderingContext", class WebGLRenderingContext {});
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(
      {} as RenderingContext,
    );
    // 先重建控制器（会清掉上一个测试遗留的 box），再重置计数
    resetDice3DTheme();
    diceMock.initialize.mockReset().mockResolvedValue();
    diceMock.roll.mockReset().mockResolvedValue([]);
    diceMock.clearDice.mockReset();
    diceMock.dispose.mockReset();
    diceMock.forceContextLoss.mockReset();
    diceMock.importError = null;
  });

  afterEach(() => {
    cancelDice3D();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("reserves the controller while initialization is pending", async () => {
    const initialization = deferred<void>();
    diceMock.initialize.mockReturnValueOnce(initialization.promise);
    const first = rollDice3D(DICE);

    expect(isDice3DBusy()).toBe(true);
    await expect(rollDice3D(DICE)).rejects.toThrow("unavailable");

    initialization.resolve();
    await first;
    expect(diceMock.roll).toHaveBeenCalledWith("1d20@17");
    expect(isDice3DBusy()).toBe(false);
  });

  it("clears active physics when cancelled", async () => {
    const rolling = deferred<unknown>();
    diceMock.roll.mockReturnValueOnce(rolling.promise);
    const pending = rollDice3D(DICE);
    await vi.waitFor(() => expect(diceMock.roll).toHaveBeenCalled());

    cancelDice3D();
    await pending;
    expect(diceMock.clearDice).toHaveBeenCalled();
    expect(isDice3DBusy()).toBe(false);
  });

  it("does not resurrect a roll cancelled during initialization", async () => {
    const initialization = deferred<void>();
    diceMock.initialize.mockReturnValueOnce(initialization.promise);
    const pending = rollDice3D(DICE);
    // 等构造完成、initialize 挂起后再取消，模拟 init 迟到 resolve
    await vi.waitFor(() => expect(diceMock.initialize).toHaveBeenCalled());
    cancelDice3D();

    await expect(pending).rejects.toThrow("cancelled");
    expect(diceMock.roll).not.toHaveBeenCalled();
    expect(isDice3DBusy()).toBe(false);
    // 迟到的 init resolve 不得重新显示遮罩
    initialization.resolve();
    await vi.waitFor(() => expect(diceMock.initialize).toHaveReturned());
    expect(overlay()).toHaveClass("hidden");
  });

  it("abandons an init cancelled before construction and retries next roll", async () => {
    const pending = rollDice3D(DICE);
    // import 尚未完成即取消：初始化作废但不记为失败
    cancelDice3D();
    await expect(pending).rejects.toThrow("cancelled");
    await new Promise<void>((resolve) => {
      setTimeout(resolve, 0);
    });

    await rollDice3D(DICE);
    expect(diceMock.roll).toHaveBeenCalledWith("1d20@17");
  });

  it("disposes renderer resources when the theme changes", async () => {
    await rollDice3D(DICE);
    resetDice3DTheme();
    expect(diceMock.dispose).toHaveBeenCalled();
    expect(diceMock.forceContextLoss).toHaveBeenCalled();
  });

  it("settles within the init budget when initialization never resolves", async () => {
    vi.useFakeTimers();
    try {
      diceMock.initialize.mockReturnValueOnce(new Promise<void>(() => {}));
      const pending = rollDice3D(DICE, "msg-init-hang");
      const assertion = expect(pending).rejects.toThrow("init failed");
      await vi.advanceTimersByTimeAsync(INIT_TIMEOUT_MS);
      await assertion;

      expect(isDice3DBusy()).toBe(false);
      expect(overlay()).toHaveClass("hidden");
      expect(diceMock.roll).not.toHaveBeenCalled();
      // 初始化超时后永久降级：后续投掷直接拒绝，走权威文字结果
      await expect(rollDice3D(DICE)).rejects.toThrow("init failed");
    } finally {
      vi.useRealTimers();
    }
  });

  it("falls back when initialization rejects", async () => {
    diceMock.initialize.mockRejectedValueOnce(new Error("webgl context lost"));
    await expect(rollDice3D(DICE)).rejects.toThrow("init failed");

    expect(isDice3DBusy()).toBe(false);
    expect(overlay()).toHaveClass("hidden");
    expect(diceMock.roll).not.toHaveBeenCalled();
    await expect(rollDice3D(DICE)).rejects.toThrow("init failed");
  });

  it("falls back when the dice chunk fails to load", async () => {
    diceMock.importError = new Error("chunk load failed");
    await expect(rollDice3D(DICE)).rejects.toThrow("init failed");

    expect(isDice3DBusy()).toBe(false);
    expect(overlay()).toHaveClass("hidden");
    expect(diceMock.roll).not.toHaveBeenCalled();
  });

  it("settles via timeout when the physics roll never resolves", async () => {
    vi.useFakeTimers();
    try {
      diceMock.roll.mockReturnValueOnce(new Promise<unknown>(() => {}));
      const pending = rollDice3D(DICE);
      await vi.advanceTimersByTimeAsync(ROLL_TIMEOUT_MS);
      // 超时兜底 resolve：调用方 finally 落定服务端权威文字结果
      await pending;

      expect(diceMock.clearDice).toHaveBeenCalled();
      expect(isDice3DBusy()).toBe(false);
      expect(overlay()).toHaveClass("hidden");
    } finally {
      vi.useRealTimers();
    }
  });

  it("settles when the physics roll rejects", async () => {
    diceMock.roll.mockRejectedValueOnce(new Error("physics exploded"));
    await rollDice3D(DICE);

    expect(diceMock.clearDice).toHaveBeenCalled();
    expect(isDice3DBusy()).toBe(false);
    expect(overlay()).toHaveClass("hidden");
  });

  it("exits immediately when skipped during initialization", async () => {
    const initialization = deferred<void>();
    diceMock.initialize.mockReturnValueOnce(initialization.promise);
    const pending = rollDice3D(DICE);
    await vi.waitFor(() => expect(diceMock.initialize).toHaveBeenCalled());
    expect(overlay()).not.toHaveClass("hidden");

    overlay().click();
    await expect(pending).rejects.toThrow("cancelled");
    expect(overlay()).toHaveClass("hidden");
    expect(isDice3DBusy()).toBe(false);

    // 迟到的 init resolve 不复活遮罩、不启动物理投掷
    initialization.resolve();
    await vi.waitFor(() => expect(diceMock.initialize).toHaveReturned());
    expect(overlay()).toHaveClass("hidden");
    expect(diceMock.roll).not.toHaveBeenCalled();
  });

  it("a skipped first roll does not clean up the next roll", async () => {
    const firstRoll = deferred<unknown>();
    diceMock.roll.mockReturnValueOnce(firstRoll.promise);
    const first = rollDice3D(DICE);
    await vi.waitFor(() => expect(diceMock.roll).toHaveBeenCalledTimes(1));

    overlay().click();
    await first;
    expect(diceMock.clearDice).toHaveBeenCalledTimes(1);

    const secondRoll = deferred<unknown>();
    diceMock.roll.mockReturnValueOnce(secondRoll.promise);
    const second = rollDice3D([{ min: 1, max: 6, final: 4, label: "d6" }]);
    await vi.waitFor(() => expect(diceMock.roll).toHaveBeenCalledTimes(2));
    expect(diceMock.roll).toHaveBeenLastCalledWith("1d6@4");
    expect(overlay()).not.toHaveClass("hidden");

    // 第一次投掷迟到的物理结果不得清理第二次投掷的场景
    firstRoll.resolve([]);
    await new Promise<void>((resolve) => {
      setTimeout(resolve, 0);
    });
    expect(diceMock.clearDice).toHaveBeenCalledTimes(1);
    expect(overlay()).not.toHaveClass("hidden");

    secondRoll.resolve([]);
    await second;
    expect(isDice3DBusy()).toBe(false);
    expect(overlay()).toHaveClass("hidden");
  });

  it("maps d100=100 onto two digit dice showing 0,0", async () => {
    await rollDice3D([{ min: 1, max: 100, final: 100, label: "d100" }]);
    expect(diceMock.roll).toHaveBeenCalledWith("2d10@0,0");
  });

  it("maps zero tens and zero ones onto the 0 face", async () => {
    await rollDice3D([{ min: 1, max: 100, final: 7, label: "d100" }]);
    expect(diceMock.roll).toHaveBeenCalledWith("2d10@0,7");

    resetDice3DTheme();
    await rollDice3D([{ min: 1, max: 100, final: 30, label: "d100" }]);
    expect(diceMock.roll).toHaveBeenLastCalledWith("2d10@3,0");
  });

  it("keeps bonus/penalty digit dice in authoritative order", async () => {
    await rollDice3D([
      { min: 0, max: 9, final: 5, label: "十位", formatter: "tens" },
      { min: 0, max: 9, final: 2, label: "奖励十位", formatter: "tens" },
      { min: 0, max: 9, final: 7, label: "个位" },
    ]);
    expect(diceMock.roll).toHaveBeenCalledWith("3d10@5,2,7");
  });

  it("rejects mixed die types so the caller uses the text fallback", async () => {
    await expect(
      rollDice3D([
        { min: 1, max: 20, final: 17, label: "d20" },
        { min: 1, max: 6, final: 4, label: "d6" },
      ]),
    ).rejects.toThrow("unavailable");
    expect(isDice3DBusy()).toBe(false);
    expect(diceMock.roll).not.toHaveBeenCalled();
  });

  it("exposes GPU and controller diagnostics without throwing", () => {
    const snapshot = dice3DDiagnostics();
    expect(snapshot).toMatchObject({
      boxFailed: false,
      rolling: false,
    });
  });

  it("disposes initialization that completes after its timeout", async () => {
    vi.useFakeTimers();
    try {
      const init = deferred<void>();
      diceMock.initialize.mockReturnValueOnce(init.promise);
      const pending = rollDice3D(DICE);
      const rejected = expect(pending).rejects.toThrow("init failed");
      await vi.advanceTimersByTimeAsync(INIT_TIMEOUT_MS);
      await rejected;
      const disposals = diceMock.dispose.mock.calls.length;
      init.resolve();
      await vi.advanceTimersByTimeAsync(0);
      expect(diceMock.dispose.mock.calls.length).toBeGreaterThan(disposals);
      expect(diceMock.roll).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("isolates immediate cancel/retry before the old import completes", async () => {
    const old = rollDice3D(DICE);
    const rejected = expect(old).rejects.toThrow("cancelled");
    cancelDice3D();
    const replacement = rollDice3D(DICE);
    await rejected;
    await replacement;
    expect(diceMock.initialize).toHaveBeenCalledTimes(1);
    expect(diceMock.roll).toHaveBeenCalledTimes(1);
  });

  it("isolates late init from a new theme's initialization", async () => {
    const oldInit = deferred<void>();
    diceMock.initialize.mockReturnValueOnce(oldInit.promise);
    const old = rollDice3D(DICE);
    const rejected = expect(old).rejects.toThrow("cancelled");
    await vi.waitFor(() =>
      expect(diceMock.initialize).toHaveBeenCalledTimes(1),
    );
    resetDice3DTheme();
    const newRoll = deferred<unknown>();
    diceMock.roll.mockReturnValueOnce(newRoll.promise);
    const current = rollDice3D(DICE);
    await rejected;
    await vi.waitFor(() => expect(diceMock.roll).toHaveBeenCalledTimes(1));
    oldInit.resolve();
    await Promise.resolve();
    expect(overlay()).not.toHaveClass("hidden");
    expect(isDice3DBusy()).toBe(true);
    newRoll.resolve([]);
    await current;
  });

  it("releases the renderer even if clearing dice throws", async () => {
    await rollDice3D(DICE);
    diceMock.clearDice.mockImplementation(() => {
      throw new Error("cleanup");
    });
    resetDice3DTheme();
    expect(diceMock.dispose).toHaveBeenCalled();
    expect(diceMock.forceContextLoss).toHaveBeenCalled();
  });

  it("settles synchronous physics exceptions without leaving a timer", async () => {
    diceMock.roll.mockImplementationOnce(() => {
      throw new Error("sync physics");
    });
    await rollDice3D(DICE);
    expect(isDice3DBusy()).toBe(false);
    expect(overlay()).toHaveClass("hidden");
  });
});
