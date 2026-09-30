import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { LibraryEntry } from "../../api/characterLibrary";
import { useAppStore } from "../../state/app-store";
import { useStartStore } from "../../state/start-store";
import { CharacterLibraryPanel } from "./CharacterLibraryPanel";

const api = vi.hoisted(() => ({
  listCharacterLibrary: vi.fn(),
  inspectLibraryCard: vi.fn(),
  createLibraryEntry: vi.fn(),
  updateLibraryEntry: vi.fn(),
  duplicateLibraryEntry: vi.fn(),
  deleteLibraryEntry: vi.fn(),
  exportLibraryEntry: vi.fn(),
}));

vi.mock("../../api/characterLibrary", () => api);

const safeSend = vi.hoisted(() => vi.fn());
vi.mock("../../ws", () => ({ safeSend }));

function makeEntry(overrides: Partial<LibraryEntry> = {}): LibraryEntry {
  return {
    id: "chlib_1",
    name: "测试调查员",
    occupation: "记者",
    age: 30,
    era: "1920年代",
    source: "library",
    source_label: "角色库",
    hp: 10,
    max_hp: 10,
    san: 65,
    max_san: 65,
    attributes: { STR: 50 },
    derived: { HP: 10, SAN: 65 },
    skills: { library_use: 60 },
    inventory: ["笔记本"],
    backstory: { description: "短发" },
    top_skills: [{ id: "library_use", value: 60 }],
    created_at: "2026-09-23T10:00:00",
    updated_at: "2026-09-23T10:00:00",
    ...overrides,
  } as LibraryEntry;
}

describe("CharacterLibraryPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAppStore.setState({ characterLibraryOpen: true, mode: "local" });
    useStartStore.setState({ pendingLibraryCharacterId: null });
    api.listCharacterLibrary.mockResolvedValue([makeEntry()]);
  });

  it("列出角色并预览详情", async () => {
    render(<CharacterLibraryPanel />);
    const row = await screen.findByText("测试调查员");
    fireEvent.click(row);
    expect(await screen.findByText(/跑社会新闻|短发/)).toBeInTheDocument();
  });

  it("空状态引导新建/导入", async () => {
    api.listCharacterLibrary.mockResolvedValue([]);
    render(<CharacterLibraryPanel />);
    expect(await screen.findByText("角色库还是空的")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "新建角色" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "导入角色卡" }),
    ).toBeInTheDocument();
  });

  it("删除需要二次确认，且说明不影响已开局世界", async () => {
    render(<CharacterLibraryPanel />);
    await screen.findByText("测试调查员");
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    expect(api.deleteLibraryEntry).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    expect(
      screen.getByText(/已开局世界与历史存档中的该角色不受影响/),
    ).toBeInTheDocument();
    api.deleteLibraryEntry.mockResolvedValue(undefined);
    api.listCharacterLibrary.mockResolvedValue([]);
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() =>
      expect(api.deleteLibraryEntry).toHaveBeenCalledWith("chlib_1"),
    );
    // 删除后重推选角列表
    await waitFor(() => expect(safeSend).toHaveBeenCalled());
  });

  it("新建角色：保存后刷新并请求选角选中", async () => {
    api.listCharacterLibrary.mockResolvedValue([]);
    api.createLibraryEntry.mockResolvedValue({
      entry: makeEntry({ id: "chlib_new", name: "新角色" }),
      warnings: [],
    });
    api.listCharacterLibrary
      .mockResolvedValueOnce([])
      .mockResolvedValue([makeEntry({ id: "chlib_new", name: "新角色" })]);
    render(<CharacterLibraryPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "新建角色" }));
    fireEvent.change(screen.getByLabelText(/姓名/), {
      target: { value: "新角色" },
    });
    fireEvent.change(screen.getByLabelText(/职业/), {
      target: { value: "医生" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建角色" }));
    await waitFor(() => expect(api.createLibraryEntry).toHaveBeenCalled());
    const payload = api.createLibraryEntry.mock.calls[0][0] as {
      name: string;
      attributes: Record<string, number>;
    };
    expect(payload.name).toBe("新角色");
    expect(payload.attributes.STR).toBe(50);
    await waitFor(() =>
      expect(useStartStore.getState().pendingLibraryCharacterId).toBe(
        "chlib_new",
      ),
    );
  });

  it("保存失败保留输入并展示字段级错误", async () => {
    api.listCharacterLibrary.mockResolvedValue([]);
    const { ApiError } = await import("../../api/client");
    api.createLibraryEntry.mockRejectedValue(
      new ApiError("角色卡校验未通过", 400, "invalid_card", [
        { field: "attributes.STR", message: "属性 STR 必须是整数" },
      ]),
    );
    render(<CharacterLibraryPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "新建角色" }));
    fireEvent.change(screen.getByLabelText(/姓名/), {
      target: { value: "坏卡" },
    });
    fireEvent.change(screen.getByLabelText(/职业/), {
      target: { value: "记者" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建角色" }));
    expect(await screen.findByText(/attributes.STR/)).toBeInTheDocument();
    // 仍在编辑视图且输入保留
    expect(screen.getByLabelText(/姓名/)).toHaveValue("坏卡");
  });

  it("导入：文件校验预览 → 确认导入 → 列表可见", async () => {
    api.listCharacterLibrary.mockResolvedValue([]);
    api.inspectLibraryCard.mockResolvedValue({
      ok: true,
      errors: [],
      warnings: ["未提供幸运（LUCK），按 50 处理"],
      preview: makeEntry({ id: "(preview)" }),
    });
    api.createLibraryEntry.mockResolvedValue({
      entry: makeEntry({ id: "chlib_imported" }),
      warnings: [],
    });
    api.listCharacterLibrary
      .mockResolvedValueOnce([])
      .mockResolvedValue([makeEntry({ id: "chlib_imported" })]);
    render(<CharacterLibraryPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "导入角色卡" }));
    const file = new File(
      [JSON.stringify({ name: "测试调查员" })],
      "card.json",
      {
        type: "application/json",
      },
    );
    const input = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });
    // 预览（含告警）出现
    expect(await screen.findByText(/按 50 处理/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认导入" }));
    await waitFor(() => expect(api.createLibraryEntry).toHaveBeenCalled());
    await waitFor(() =>
      expect(useStartStore.getState().pendingLibraryCharacterId).toBe(
        "chlib_imported",
      ),
    );
  });

  it("导入校验失败：展示字段级错误且不创建", async () => {
    api.listCharacterLibrary.mockResolvedValue([]);
    api.inspectLibraryCard.mockResolvedValue({
      ok: false,
      errors: [{ field: "attributes", message: "缺少属性表" }],
      warnings: [],
      preview: null,
    });
    render(<CharacterLibraryPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "导入角色卡" }));
    const input = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    fireEvent.change(input, {
      target: {
        files: [new File(["{}"], "bad.json", { type: "application/json" })],
      },
    });
    expect(await screen.findByText(/缺少属性表/)).toBeInTheDocument();
    expect(screen.getByText("attributes")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "确认导入" }),
    ).not.toBeInTheDocument();
    expect(api.createLibraryEntry).not.toHaveBeenCalled();
  });

  it("超大文件直接拒绝，不发请求", async () => {
    api.listCharacterLibrary.mockResolvedValue([]);
    render(<CharacterLibraryPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "导入角色卡" }));
    const big = new File([new ArrayBuffer(300 * 1024)], "big.json", {
      type: "application/json",
    });
    const input = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    fireEvent.change(input, { target: { files: [big] } });
    expect(await screen.findByText(/文件过大/)).toBeInTheDocument();
    expect(api.inspectLibraryCard).not.toHaveBeenCalled();
  });
});
