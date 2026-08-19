import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  isBuildChanged,
  loadBootManifest,
  preloadImages,
  recordBuildBooted,
  waitForConnection,
  waitForModuleBgUrl,
} from "./preload";
import { useAppStore } from "../state/app-store";

function fakeStorage(initial: Record<string, string> = {}) {
  const data = new Map(Object.entries(initial));
  return {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => void data.set(key, value),
    data,
  };
}

describe("loadBootManifest", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("返回清单中的字符串文件列表", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: () =>
          Promise.resolve({ files: ["assets/a.png", 42, "assets/b.woff2"] }),
      }),
    );
    await expect(loadBootManifest()).resolves.toEqual([
      "assets/a.png",
      "assets/b.woff2",
    ]);
  });

  it("404 / 非法 JSON / fetch 失败时返回空清单", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false }));
    await expect(loadBootManifest()).resolves.toEqual([]);

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ nope: true }),
      }),
    );
    await expect(loadBootManifest()).resolves.toEqual([]);

    // Electron file:// 下相对路径 fetch 直接抛错
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("file://")));
    await expect(loadBootManifest()).resolves.toEqual([]);
  });
});

describe("构建版本标记", () => {
  it("存储值与当前构建不同则视为版本变化", () => {
    expect(isBuildChanged(fakeStorage())).toBe(true);
    expect(isBuildChanged(fakeStorage({ "trpg-boot-build": "other" }))).toBe(
      true,
    );
    const storage = fakeStorage();
    recordBuildBooted(storage);
    expect(storage.data.get("trpg-boot-build")).toBe(__APP_BUILD_ID__);
    expect(isBuildChanged(storage)).toBe(false);
  });
});

describe("preloadImages", () => {
  class FakeImage {
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    static fail = new Set<string>();
    set src(url: string) {
      queueMicrotask(() => {
        if (FakeImage.fail.has(url)) this.onerror?.();
        else this.onload?.();
      });
    }
  }

  beforeEach(() => {
    FakeImage.fail.clear();
    vi.stubGlobal("Image", FakeImage);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("逐个加载并汇报进度", async () => {
    const progress: Array<[number, number]> = [];
    await preloadImages(["a.png", "b.png", "c.png"], (loaded, total) =>
      progress.push([loaded, total]),
    );
    expect(progress).toEqual([
      [1, 3],
      [2, 3],
      [3, 3],
    ]);
  });

  it("单个资源失败计入进度且不阻塞", async () => {
    FakeImage.fail.add("bad.png");
    const progress: Array<[number, number]> = [];
    await preloadImages(["bad.png", "ok.png"], (loaded, total) =>
      progress.push([loaded, total]),
    );
    expect(progress).toHaveLength(2);
  });

  it("空清单立即完成", async () => {
    const onProgress = vi.fn();
    await preloadImages([], onProgress);
    expect(onProgress).not.toHaveBeenCalled();
  });
});

describe("waitForModuleBgUrl", () => {
  afterEach(() => {
    document.documentElement.style.removeProperty("--module-bg-image");
  });

  it("已写入背景变量时立即返回 URL", async () => {
    document.documentElement.style.setProperty(
      "--module-bg-image",
      'url("http://x/api/assets/m/bg.png?v=1")',
    );
    await expect(waitForModuleBgUrl()).resolves.toBe(
      "http://x/api/assets/m/bg.png?v=1",
    );
  });

  it("主题稍后写入时通过 MutationObserver 拿到 URL", async () => {
    const pending = waitForModuleBgUrl();
    setTimeout(() => {
      document.documentElement.style.setProperty(
        "--module-bg-image",
        'url("http://x/bg.png")',
      );
    }, 10);
    await expect(pending).resolves.toBe("http://x/bg.png");
  });

  it("无背景模组超时返回 null", async () => {
    await expect(waitForModuleBgUrl(30)).resolves.toBeNull();
  });
});

describe("waitForConnection", () => {
  it("已连接时立即返回；未连接时等待状态变更", async () => {
    useAppStore.setState({ connection: "connected" });
    await expect(waitForConnection()).resolves.toBeUndefined();

    useAppStore.setState({ connection: "connecting" });
    const pending = waitForConnection();
    setTimeout(() => useAppStore.setState({ connection: "connected" }), 10);
    await expect(pending).resolves.toBeUndefined();
  });

  it("连接迟迟不建立时超时放行", async () => {
    useAppStore.setState({ connection: "connecting" });
    await expect(waitForConnection(30)).resolves.toBeUndefined();
    useAppStore.setState({ connection: "disconnected" });
  });
});
