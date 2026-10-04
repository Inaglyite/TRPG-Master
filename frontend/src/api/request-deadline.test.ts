import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RequestDeadline } from "./request-deadline";

describe("RequestDeadline", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("bounds a transport that ignores cancellation and aborts it once", async () => {
    const deadline = new RequestDeadline(30000);
    const aborted = vi.fn();
    deadline.controller.signal.addEventListener("abort", aborted);
    const result = deadline.wait(new Promise(() => {})).catch((error) => error);
    await vi.advanceTimersByTimeAsync(30000);
    expect(await result).toMatchObject({ name: "TimeoutError" });
    expect(aborted).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(30000);
    expect(aborted).toHaveBeenCalledTimes(1);
    deadline.dispose();
  });

  it("shares the remaining budget between headers and body", async () => {
    const deadline = new RequestDeadline(30000);
    await vi.advanceTimersByTimeAsync(20000);
    expect(await deadline.wait(Promise.resolve("headers"))).toBe("headers");
    const body = deadline.wait(new Promise(() => {})).catch((error) => error);
    await vi.advanceTimersByTimeAsync(9999);
    expect(deadline.controller.signal.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(await body).toMatchObject({ name: "TimeoutError" });
    deadline.dispose();
  });

  it("late success cannot replace timeout and disposal removes timers", async () => {
    const deadline = new RequestDeadline(10);
    let finish!: (value: string) => void;
    const pending = deadline
      .wait(
        new Promise<string>((resolve) => {
          finish = resolve;
        }),
      )
      .catch((error) => error);
    await vi.advanceTimersByTimeAsync(10);
    finish("late response");
    expect(await pending).toMatchObject({ name: "TimeoutError" });
    deadline.dispose();
    const completed = new RequestDeadline(100);
    expect(await completed.wait(Promise.resolve("done"))).toBe("done");
    completed.dispose();
    await vi.advanceTimersByTimeAsync(100);
    expect(completed.controller.signal.aborted).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
  });

  it.each([0, -1, Infinity, NaN])(
    "does not silently disable the budget for %s",
    (duration) => {
      expect(() => new RequestDeadline(duration)).toThrow(RangeError);
    },
  );
});
