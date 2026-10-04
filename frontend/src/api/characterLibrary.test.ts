import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAppStore } from "../state/app-store";
import { useOnlineStore } from "../state/online-store";
import { setCloudOrigin } from "./client";
import { invalidateCloudRequests } from "./request-context";
import {
  createLibraryEntry,
  deleteLibraryEntry,
  duplicateLibraryEntry,
  exportLibraryEntry,
  getLibraryCard,
  inspectLibraryCard,
  listCharacterLibrary,
  updateLibraryEntry,
} from "./characterLibrary";

const entry = { id: "library_test", name: "测试卡" };
const envelope = {
  format: "trpg-character-card",
  format_version: 1,
  card: { name: "测试卡", extension: { retained: true } },
};
const json = (body: unknown, status = 200) =>
  new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

describe("角色库请求路由与导出边界", () => {
  beforeEach(() => {
    localStorage.clear();
    useAppStore.setState({ mode: "local" });
    useOnlineStore.setState({ user: null });
    vi.stubGlobal("fetch", vi.fn());
  });
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("所有本地读写都走设备后端，不把角色卡发到保存的云端地址", async () => {
    setCloudOrigin("https://cloud.example.test");
    vi.mocked(fetch)
      .mockResolvedValueOnce(json({ entries: [entry] }))
      .mockResolvedValueOnce(json({ ok: true, errors: [], warnings: [] }))
      .mockResolvedValueOnce(json({ ok: true, entry }))
      .mockResolvedValueOnce(json({ ok: true, entry }))
      .mockResolvedValueOnce(json({ card: envelope.card }))
      .mockResolvedValueOnce(json({ ok: true, entry }))
      .mockResolvedValueOnce(json(null, 204));
    await listCharacterLibrary();
    await inspectLibraryCard(envelope);
    await createLibraryEntry(envelope);
    await updateLibraryEntry(entry.id, envelope.card);
    await getLibraryCard(entry.id);
    await duplicateLibraryEntry(entry.id);
    await deleteLibraryEntry(entry.id);
    expect(vi.mocked(fetch).mock.calls).toHaveLength(7);
    for (const [url, options] of vi.mocked(fetch).mock.calls) {
      expect(String(url)).toMatch(
        /^http:\/\/localhost:8765\/api\/character-library/,
      );
      expect(options?.signal).toBeDefined();
    }
  });

  it("云端沿用当前服务器与账号 Cookie", async () => {
    useAppStore.setState({ mode: "online" });
    useOnlineStore.setState({ user: { id: "A", username: "甲" } });
    setCloudOrigin("https://cloud.example.test");
    vi.mocked(fetch).mockResolvedValue(json({ entries: [entry] }));
    await listCharacterLibrary();
    expect(fetch).toHaveBeenCalledWith(
      "https://cloud.example.test/api/character-library",
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("本地读取期间切到云端，不返回旧设备卡面", async () => {
    let finish!: (response: Response) => void;
    vi.mocked(fetch).mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const pending = getLibraryCard(entry.id).catch((error) => error);
    useAppStore.setState({ mode: "online" });
    finish(json({ card: envelope.card }));
    expect(await pending).toMatchObject({ code: "request_context_changed" });
  });

  it.each(["server", "session"])(
    "迟到导出遇 %s 变化不得触发下载",
    async (change) => {
      useAppStore.setState({ mode: "online" });
      let finish!: (response: Response) => void;
      vi.mocked(fetch).mockReturnValue(
        new Promise((resolve) => {
          finish = resolve;
        }),
      );
      const OriginalURL = URL;
      const createObjectURL = vi.fn(() => "blob:test");
      vi.stubGlobal(
        "URL",
        class extends OriginalURL {
          static createObjectURL = createObjectURL;
          static revokeObjectURL = vi.fn();
        },
      );
      const pending = exportLibraryEntry(entry.id, entry.name).catch(
        (error) => error,
      );
      if (change === "server") setCloudOrigin("https://other.example.test");
      else invalidateCloudRequests();
      finish(json(envelope));
      expect(await pending).toMatchObject({ code: "request_context_changed" });
      expect(createObjectURL).not.toHaveBeenCalled();
    },
  );

  it("导出保留版本信封和扩展资料，仍走本地路由", async () => {
    setCloudOrigin("https://cloud.example.test");
    vi.mocked(fetch).mockResolvedValue(json(envelope));
    const blobs: Blob[] = [];
    const OriginalURL = URL;
    vi.stubGlobal(
      "URL",
      class extends OriginalURL {
        static createObjectURL = vi.fn((blob: Blob) => {
          blobs.push(blob);
          return "blob:test";
        });
        static revokeObjectURL = vi.fn();
      },
    );
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    await exportLibraryEntry(entry.id, entry.name);
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8765/api/character-library/library_test/export",
      expect.anything(),
    );
    expect(blobs).toHaveLength(1);
    const text = await new Promise<string>((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.readAsText(blobs[0]);
    });
    expect(JSON.parse(text)).toEqual(envelope);
  });
});
