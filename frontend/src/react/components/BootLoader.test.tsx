import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { BootLoader } from "./BootLoader";

vi.mock("../../boot/preload", () => ({
  isBuildChanged: () => true,
  recordBuildBooted: vi.fn(),
  loadBootManifest: vi.fn().mockResolvedValue(["assets/a.webp"]),
  preloadImages: vi.fn(
    async (_files: string[], onProgress?: (l: number, t: number) => void) => {
      onProgress?.(1, 1);
    },
  ),
  waitForConnection: vi.fn().mockResolvedValue(undefined),
  waitForModuleBgUrl: vi.fn().mockResolvedValue(null),
}));

describe("BootLoader 退场", () => {
  it("预载完成后先进入 leaving 态再卸载，而不是硬切", async () => {
    const { container } = render(<BootLoader />);
    const overlay = screen.getByRole("status");
    expect(overlay.className).toBe("boot-loader");

    // 预载完成 → 250ms 停留 → leaving
    await waitFor(
      () => {
        expect(screen.getByRole("status").className).toContain(
          "boot-loader--leaving",
        );
      },
      { timeout: 2000 },
    );

    // 退场动画播完后卸载
    await waitFor(
      () => {
        expect(container.querySelector(".boot-loader")).toBeNull();
      },
      { timeout: 3000 },
    );
  });
});
