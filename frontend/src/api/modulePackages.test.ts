import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAppStore } from "../state/app-store";
import { uploadModulePackage } from "./modulePackages";

const file = new File(["test"], "测试.trpgmod");
describe("local module upload transport", () => {
  beforeEach(() => useAppStore.setState({ mode: "local" }));
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("explains unconfirmed inspection after a network or file read error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
    );
    await expect(
      uploadModulePackage("inspect", file, new AbortController().signal),
    ).rejects.toThrow("重新选择原文件");
  });

  it("does not claim an installation was rolled back after a network error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("Load failed")),
    );
    await expect(
      uploadModulePackage("import", file, new AbortController().signal),
    ).rejects.toThrow("模组可能已安装");
  });

  it("has a bounded inspect wait, even when fetch never settles", async () => {
    vi.useFakeTimers();
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockImplementation(() => new Promise<Response>(() => {}));
    vi.stubGlobal("fetch", fetch);
    const result = uploadModulePackage(
      "inspect",
      file,
      new AbortController().signal,
    );
    const checked = expect(result).rejects.toThrow("检查模组包超时");
    await vi.advanceTimersByTimeAsync(30_001);
    await checked;
    expect(fetch.mock.calls[0][1]?.signal?.aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("installation timeout does not claim rollback or success", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise<Response>(() => {})),
    );
    const checked = expect(
      uploadModulePackage("import", file, new AbortController().signal),
    ).rejects.toThrow("模组可能已安装");
    await vi.advanceTimersByTimeAsync(120_001);
    await checked;
  });

  it("external abort clears the deadline and cannot later produce a result", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise<Response>(() => {})),
    );
    const controller = new AbortController();
    const checked = expect(
      uploadModulePackage("inspect", file, controller.signal),
    ).rejects.toMatchObject({ name: "AbortError" });
    controller.abort();
    await checked;
    expect(vi.getTimerCount()).toBe(0);
  });

  it("does not send a package to a cloud service", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    useAppStore.setState({ mode: "online" });
    await expect(
      uploadModulePackage("import", file, new AbortController().signal),
    ).rejects.toThrow("仅适用于本地模式");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("rejects malformed successful envelopes and preserves server validation details", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ok: true, module: { title: "缺字段" } })),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            ok: false,
            error: "格式无效",
            details: ["缺少 manifest.json", { not: "text" }],
          }),
          { status: 400 },
        ),
      );
    vi.stubGlobal("fetch", fetch);
    await expect(
      uploadModulePackage("inspect", file, new AbortController().signal),
    ).rejects.toThrow("信息不完整");
    await expect(
      uploadModulePackage("inspect", file, new AbortController().signal),
    ).rejects.toThrow("格式无效\n缺少 manifest.json");
  });
});
