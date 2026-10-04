import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { z } from "zod";
import { ApiError, apiFetch } from "./client";
import { invalidateCloudRequests } from "./request-context";

describe("API whole-response deadline", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("normal mutations without an explicit timeout do not remain pending forever", async () => {
    vi.mocked(fetch).mockReturnValue(new Promise(() => {}));
    const result = apiFetch("/api/worlds", z.object({ world_id: z.string() }), {
      method: "POST",
      body: { module: "test" },
    }).catch((error) => error);
    await vi.advanceTimersByTimeAsync(30000);
    expect(await result).toMatchObject({ code: "request_timeout" });
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(vi.mocked(fetch).mock.calls[0][1]?.signal?.aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("custom budget covers a body that never finishes after headers arrive", async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      status: 200,
      json: () => new Promise(() => {}),
    } as Response);
    const result = apiFetch("/api/worlds", z.unknown(), {
      timeoutMs: 1000,
    }).catch((error) => error);
    await vi.advanceTimersByTimeAsync(1000);
    expect(await result).toMatchObject({ code: "request_timeout" });
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("an error body that hangs is also bounded without claiming rollback", async () => {
    vi.mocked(fetch).mockResolvedValue({
      ok: false,
      status: 503,
      json: () => new Promise(() => {}),
    } as Response);
    const result = apiFetch("/api/worlds/test", z.unknown(), {
      method: "DELETE",
      timeoutMs: 1000,
    }).catch((error) => error);
    await vi.advanceTimersByTimeAsync(1000);
    const error = await result;
    if (!(error instanceof ApiError)) throw error;
    expect(error.code).toBe("request_timeout");
    expect(error.message).toContain("不代表");
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("a switched account wins over the old request timing out", async () => {
    vi.mocked(fetch).mockReturnValue(new Promise(() => {}));
    const result = apiFetch("/api/worlds", z.unknown(), {
      timeoutMs: 1000,
    }).catch((error) => error);
    invalidateCloudRequests();
    await vi.advanceTimersByTimeAsync(1000);
    expect(await result).toMatchObject({ code: "request_context_changed" });
  });

  it("successful requests leave no deadline or abort for later operations", async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), { status: 200 }),
    );
    await expect(
      apiFetch("/api/worlds", z.object({ ok: z.boolean() })),
    ).resolves.toEqual({ ok: true });
    expect(vi.getTimerCount()).toBe(0);
    await vi.advanceTimersByTimeAsync(30000);
    expect(vi.mocked(fetch).mock.calls[0][1]?.signal?.aborted).toBe(false);
  });
});
