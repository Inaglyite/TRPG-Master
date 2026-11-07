import { afterEach, expect, it, vi } from "vitest";
import { useAppStore } from "../state/app-store";
import { backendHttpOrigin } from "../backend-url";
import { deleteModule, listManagedModules } from "./moduleManagement";

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});
it("uses the local backend even when a cloud server was saved", async () => {
  useAppStore.setState({ mode: "local" });
  localStorage.setItem("trpg-cloud-origin", "https://cloud.invalid");
  const fetch = vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({
        ok: true,
        world_count: 1,
        save_count: 2,
        cleanup_pending: false,
      }),
    ),
  );
  vi.stubGlobal("fetch", fetch);
  await deleteModule("example.test@1.0.0");
  expect(fetch).toHaveBeenCalledWith(
    `${backendHttpOrigin()}/api/modules/example.test%401.0.0`,
    expect.objectContaining({ method: "DELETE" }),
  );
});
it("never sends management requests from online mode", async () => {
  useAppStore.setState({ mode: "online" });
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  await expect(deleteModule("test")).rejects.toThrow("仅适用于本地模式");
  await expect(listManagedModules()).rejects.toThrow("仅适用于本地模式");
  expect(fetch).not.toHaveBeenCalled();
});
