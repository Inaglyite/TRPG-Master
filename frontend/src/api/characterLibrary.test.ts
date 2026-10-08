import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAppStore } from "../state/app-store";
import { useOnlineStore } from "../state/online-store";
import { useStructuredStore } from "../state/structured-store";
import { setCloudOrigin } from "./client";
import { invalidateCloudRequests } from "./request-context";
import {
  createLibraryEntry,
  deleteLibraryEntry,
  duplicateLibraryEntry,
  exportLibraryEntry,
  exportCaseCharacter,
  previewCaseCharacter,
  saveCaseCharacter,
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
    useStructuredStore.getState().reset();
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

  it("结案预览与显式保存沿用本地路由和凭证，不提交原始角色状态", async () => {
    const source = {
      world_id: "w",
      investigator_id: "i",
      case_id: "w:end",
      expected_revision: 8,
    };
    vi.mocked(fetch)
      .mockResolvedValueOnce(
        json({
          ok: true,
          card: envelope.card,
          warnings: [],
          revision: 8,
          receipt_digest: "a".repeat(64),
          saved_entry: null,
        }),
      )
      .mockResolvedValueOnce(
        json({ ok: true, entry, warnings: [], deduplicated: false }),
      );
    const preview = await previewCaseCharacter(source);
    await saveCaseCharacter({
      ...source,
      receipt_digest: preview.receipt_digest,
      name: "结案副本",
    });
    expect(vi.mocked(fetch).mock.calls.map(([url]) => String(url))).toEqual([
      "http://localhost:8765/api/character-library/from-case/preview",
      "http://localhost:8765/api/character-library/from-case",
    ]);
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[1][1]?.body))).toEqual(
      {
        ...source,
        receipt_digest: "a".repeat(64),
        name: "结案副本",
      },
    );
  });

  it.each(["world", "investigator", "receipt", "mode", "account", "session"])(
    "结案导出期间 %s 变化，不下载旧卡",
    async (change) => {
      useAppStore.setState({ mode: "online" });
      useOnlineStore.setState({ user: { id: "A", username: "甲" } });
      useStructuredStore.setState({
        identity: {
          ...useStructuredStore.getState().identity,
          worldId: "w",
          investigatorId: "i",
        },
      });
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
      const pending = exportCaseCharacter(
        {
          world_id: "w",
          investigator_id: "i",
          case_id: "w:end",
          expected_revision: 8,
        },
        "副本",
      ).catch((e) => e);
      if (change === "world" || change === "investigator")
        useStructuredStore.setState({
          identity: {
            ...useStructuredStore.getState().identity,
            ...(change === "world"
              ? { worldId: "other" }
              : { investigatorId: "other" }),
          },
        });
      else if (change === "receipt")
        useStructuredStore.setState({
          caseSettlements: [
            {
              investigator_id: "i",
              character_id: "i",
              case: {
                case_id: "w:end",
                world_id: "w",
                ending_type: "good",
                reputation_delta: 2,
              },
              career: {
                case_history: [],
                reputation: 0,
                completed_modules: [],
              },
            },
          ],
        });
      else if (change === "mode") useAppStore.setState({ mode: "local" });
      else if (change === "account")
        useOnlineStore.setState({ user: { id: "B", username: "乙" } });
      else invalidateCloudRequests();
      finish(json(envelope));
      expect(await pending).toMatchObject({ code: "request_context_changed" });
      expect(createObjectURL).not.toHaveBeenCalled();
    },
  );

  it("畸形结案导出响应不得下载为角色卡", async () => {
    useStructuredStore.setState({
      identity: {
        ...useStructuredStore.getState().identity,
        worldId: "w",
        investigatorId: "i",
      },
    });
    vi.mocked(fetch).mockResolvedValue(json({ ok: true, error: "not a card" }));
    await expect(
      exportCaseCharacter(
        {
          world_id: "w",
          investigator_id: "i",
          case_id: "w:end",
          expected_revision: 8,
        },
        "副本",
      ),
    ).rejects.toThrow();
  });
});
