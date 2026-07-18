import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const diceMock = vi.hoisted(() => ({
  initialize: vi.fn<() => Promise<void>>(),
  roll: vi.fn<(notation: string) => Promise<unknown>>(),
  clearDice: vi.fn(),
  dispose: vi.fn(),
  forceContextLoss: vi.fn(),
}));

vi.mock("@3d-dice/dice-box-threejs", () => ({
  default: class MockDiceBox {
    initialize = diceMock.initialize;
    roll = diceMock.roll;
    clearDice = diceMock.clearDice;
    renderer = {
      dispose: diceMock.dispose,
      forceContextLoss: diceMock.forceContextLoss,
      domElement: document.createElement("canvas"),
    };
  },
}));

import {
  cancelDice3D,
  isDice3DBusy,
  resetDice3DTheme,
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

describe("3D dice controller", () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="chat-panel"></div>';
    vi.stubGlobal("WebGLRenderingContext", class WebGLRenderingContext {});
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(
      {} as RenderingContext,
    );
    diceMock.initialize.mockReset().mockResolvedValue();
    diceMock.roll.mockReset().mockResolvedValue([]);
    diceMock.clearDice.mockReset();
    diceMock.dispose.mockReset();
    diceMock.forceContextLoss.mockReset();
    resetDice3DTheme();
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
    cancelDice3D();
    initialization.resolve();

    await expect(pending).rejects.toThrow("cancelled");
    expect(diceMock.roll).not.toHaveBeenCalled();
    expect(isDice3DBusy()).toBe(false);
  });

  it("disposes renderer resources when the theme changes", async () => {
    await rollDice3D(DICE);
    resetDice3DTheme();
    expect(diceMock.dispose).toHaveBeenCalled();
    expect(diceMock.forceContextLoss).toHaveBeenCalled();
  });
});
