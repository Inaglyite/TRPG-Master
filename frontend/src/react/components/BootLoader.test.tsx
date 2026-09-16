import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useAppStore } from "../../state/app-store";
import { BootLoader, SPLASH_BUDGET_MS } from "./BootLoader";

/**
 * 启动屏的放行条件必须**有界**：应用已可交互（清单 + local 首连/背景图）即放行，
 * 资源预载在后台继续。三种资源状态都要验证——
 * 正常、延迟（仍在加载）、失败/超时——任何一种都不能把交互一直锁住。
 */

const preload = vi.fn();

vi.mock("../../boot/preload", () => ({
  isBuildChanged: () => true,
  recordBuildBooted: vi.fn(),
  loadBootManifest: vi.fn(async () => ["assets/a.webp", "assets/b.webp"]),
  preloadImages: (...args: unknown[]) =>
    (preload as unknown as (...a: unknown[]) => Promise<unknown>)(...args),
  waitForConnection: vi.fn().mockResolvedValue(undefined),
  waitForModuleBgUrl: vi.fn().mockResolvedValue(null),
}));

function setLocalMode() {
  useAppStore.setState({ mode: "local", connection: "connected" });
}

async function expectDetached(container: HTMLElement) {
  await waitFor(
    () => {
      expect(container.querySelector(".boot-loader")).toBeNull();
    },
    { timeout: 4_000 },
  );
}

afterEach(() => {
  preload.mockReset();
  useAppStore.setState({ mode: "local" });
});

describe("BootLoader 放行条件", () => {
  it("正常加载：预载完成即退场（先 leaving 再卸载，不硬切）", async () => {
    preload.mockImplementation(
      async (
        _files: string[],
        onProgress?: (l: number, t: number, f: number) => void,
      ) => {
        onProgress?.(1, 2, 0);
        onProgress?.(2, 2, 0);
        return { failed: 0 };
      },
    );
    setLocalMode();
    const { container } = render(<BootLoader />);
    expect(screen.getByRole("status").className).toBe("boot-loader");

    await waitFor(
      () => {
        expect(screen.getByRole("status").className).toContain(
          "boot-loader--leaving",
        );
      },
      { timeout: 2_000 },
    );
    await expectDetached(container);
    expect(screen.queryByText(/仍在后台加载/)).toBeNull();
  });

  it("延迟加载：资源仍在预载时，应用可交互后按宽限放行，并说明资源在后台", async () => {
    const holder: { release?: () => void } = {};
    preload.mockImplementation(
      () =>
        new Promise((resolve) => {
          holder.release = () => resolve({ failed: 0 });
        }),
    );
    setLocalMode();
    const { container } = render(<BootLoader />);

    // 预载一直没结束：仍然要退场（宽限 1.2s 之后），并且给出可见说明。
    // 说明出现在退场动画期间（约 1.15s 后才卸载），所以先等说明再等卸载。
    await expect(
      screen.findByText(/界面资源仍在后台加载/, {}, { timeout: 3_000 }),
    ).resolves.toBeTruthy();
    await expectDetached(container);
    holder.release?.();
  });

  it("资源失败：给出可见说明，同时照常放行（不静默卡住）", async () => {
    preload.mockImplementation(
      async (
        _files: string[],
        onProgress?: (l: number, t: number, f: number) => void,
      ) => {
        onProgress?.(1, 2, 1);
        onProgress?.(2, 2, 1);
        return { failed: 1 };
      },
    );
    setLocalMode();
    const { container } = render(<BootLoader />);

    await expect(
      screen.findByText(/部分界面资源未能加载（1 项）/),
    ).resolves.toBeTruthy();
    await expectDetached(container);
  });

  it("兜底预算：清单迟迟不回来也不会一直挡着交互", async () => {
    // 清单永不返回：旧实现会一直等，启动屏无限挡交互
    preload.mockImplementation(() => new Promise(() => {}));
    vi.useFakeTimers();
    try {
      setLocalMode();
      const { container } = render(<BootLoader />);
      act(() => {
        vi.advanceTimersByTime(SPLASH_BUDGET_MS + 400);
      });
      expect(container.querySelector(".boot-loader")).not.toBeNull();
      // 预算到点后进入 leaving（随后 CSS 动画结束才卸载）
      expect(screen.getByRole("status").className).toContain(
        "boot-loader--leaving",
      );
    } finally {
      vi.useRealTimers();
    }
  });
});
