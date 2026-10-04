import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { LibraryEntry } from "../../api/characterLibrary";
import { useAppStore } from "../../state/app-store";
import { useOnlineStore } from "../../state/online-store";
import { setCloudOrigin } from "../../api/client";
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
  getLibraryCard: vi.fn(),
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
    localStorage.clear();
    useOnlineStore.setState({
      user: null,
      authOrigin: null,
      authStatus: "anonymous",
    });
    useAppStore.setState({ characterLibraryOpen: true, mode: "local" });
    useStartStore.setState({ pendingLibraryCharacterId: null });
    api.listCharacterLibrary.mockResolvedValue([makeEntry()]);
    api.getLibraryCard.mockResolvedValue(makeEntry());
  });

  it("列出角色并预览详情", async () => {
    render(<CharacterLibraryPanel />);
    const row = await screen.findByText("测试调查员");
    fireEvent.click(row);
    expect(await screen.findByText(/跑社会新闻|短发/)).toBeInTheDocument();
  });

  it("只改姓名时完整保留扩展资料与对象型物品", async () => {
    const original = {
      ...makeEntry(),
      career: { cases: 2 },
      portrait: "portrait.png",
      psychological_profile: { fears: ["高处"] },
      inventory: [
        { id: "kit", name: "急救箱", quantity: 3 },
        { label: "手枪", ammo: 6 },
      ],
      backstory: { description: "短发", beliefs: "相信证据" },
    };
    api.listCharacterLibrary.mockResolvedValue([original]);
    api.getLibraryCard.mockResolvedValue(original);
    api.updateLibraryEntry.mockResolvedValue({ entry: original, warnings: [] });
    render(<CharacterLibraryPanel />);
    fireEvent.click(await screen.findByText("测试调查员"));
    fireEvent.click(await screen.findByRole("button", { name: "编辑" }));
    fireEvent.change(screen.getByLabelText(/姓名/), {
      target: { value: "新名字" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
    await waitFor(() => expect(api.updateLibraryEntry).toHaveBeenCalled());
    expect(api.updateLibraryEntry.mock.calls[0][1]).toMatchObject({
      name: "新名字",
      inventory: original.inventory,
      career: original.career,
      portrait: original.portrait,
      psychological_profile: original.psychological_profile,
      backstory: original.backstory,
    });
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
    fireEvent.click(screen.getByText("测试调查员"));
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

  it("切换账号立即清空旧列表，迟到列表不得回填", async () => {
    useAppStore.setState({ mode: "online" });
    useOnlineStore.setState({
      authStatus: "authenticated",
      user: { id: "A", username: "甲" },
    });
    let resolveOld!: (entries: LibraryEntry[]) => void;
    api.listCharacterLibrary.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveOld = resolve;
      }),
    );
    render(<CharacterLibraryPanel />);
    act(() => useOnlineStore.setState({ user: { id: "B", username: "乙" } }));
    await screen.findByText("测试调查员");
    await act(async () => resolveOld([makeEntry({ name: "甲的私密角色" })]));
    expect(screen.queryByText("甲的私密角色")).not.toBeInTheDocument();
  });

  it("读取完整卡面期间换账号，不得继续发保存命令", async () => {
    useAppStore.setState({ mode: "online" });
    useOnlineStore.setState({
      authStatus: "authenticated",
      user: { id: "A", username: "甲" },
    });
    let resolveCard!: (card: Record<string, unknown>) => void;
    api.getLibraryCard.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveCard = resolve;
      }),
    );
    render(<CharacterLibraryPanel />);
    fireEvent.click(await screen.findByText("测试调查员"));
    fireEvent.click(await screen.findByRole("button", { name: "编辑" }));
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
    act(() => useOnlineStore.setState({ user: { id: "B", username: "乙" } }));
    await act(async () => resolveCard(makeEntry()));
    expect(api.updateLibraryEntry).not.toHaveBeenCalled();
    expect(safeSend).not.toHaveBeenCalled();
  });

  it("同账号会话复核保留草稿；退出登录立即移除内容", async () => {
    useAppStore.setState({ mode: "online" });
    useOnlineStore.setState({
      authStatus: "authenticated",
      user: { id: "A", username: "甲" },
    });
    render(<CharacterLibraryPanel />);
    fireEvent.click(await screen.findByText("测试调查员"));
    fireEvent.click(await screen.findByRole("button", { name: "编辑" }));
    fireEvent.change(screen.getByLabelText(/姓名/), {
      target: { value: "未保存的草稿" },
    });
    act(() => useOnlineStore.setState({ authStatus: "checking" }));
    act(() => useOnlineStore.setState({ authStatus: "authenticated" }));
    expect(screen.getByLabelText(/姓名/)).toHaveValue("未保存的草稿");
    act(() => useOnlineStore.setState({ authStatus: "anonymous", user: null }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(useAppStore.getState().characterLibraryOpen).toBe(false);
  });

  it("弹窗接管焦点与 Tab，输入法 Escape 不关闭，关闭后归还入口", async () => {
    const trigger = document.createElement("button");
    document.body.append(trigger);
    trigger.focus();
    const { unmount } = render(<CharacterLibraryPanel />);
    const dialog = screen.getByRole("dialog");
    const close = within(dialog).getByRole("button", { name: "关闭" });
    expect(close).toHaveFocus();
    fireEvent.keyDown(close, { key: "Tab", shiftKey: true });
    expect(dialog.contains(document.activeElement)).toBe(true);
    expect(close).not.toHaveFocus();
    fireEvent.keyDown(document.activeElement!, {
      key: "Escape",
      isComposing: true,
    });
    expect(useAppStore.getState().characterLibraryOpen).toBe(true);
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    expect(useAppStore.getState().characterLibraryOpen).toBe(false);
    expect(trigger).toHaveFocus();
    unmount();
    trigger.remove();
  });

  it("关闭重开后，旧列表不得覆盖新的读取结果", async () => {
    let finish!: (entries: LibraryEntry[]) => void;
    api.listCharacterLibrary.mockReturnValueOnce(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    render(<CharacterLibraryPanel />);
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    act(() => useAppStore.getState().setCharacterLibraryOpen(true));
    await screen.findByText("测试调查员");
    await act(async () => finish([makeEntry({ name: "旧窗口迟到卡" })]));
    expect(screen.queryByText("旧窗口迟到卡")).not.toBeInTheDocument();
  });

  it("换服务器立即隐藏旧账号资料，返回原地址不能复活旧保存", async () => {
    setCloudOrigin("https://one.example.test");
    useAppStore.setState({ mode: "online" });
    useOnlineStore.setState({
      authStatus: "authenticated",
      authOrigin: "https://one.example.test",
      user: { id: "A", username: "甲" },
    });
    let finish!: (card: Record<string, unknown>) => void;
    api.getLibraryCard.mockReturnValueOnce(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    render(<CharacterLibraryPanel />);
    fireEvent.click(await screen.findByText("测试调查员"));
    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
    act(() => {
      setCloudOrigin("https://two.example.test");
    });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    act(() => {
      setCloudOrigin("https://one.example.test");
    });
    await act(async () => finish(makeEntry()));
    expect(api.updateLibraryEntry).not.toHaveBeenCalled();
  });

  it("文件读取期间换账号，不把文件继续送去新账号校验", async () => {
    useAppStore.setState({ mode: "online" });
    useOnlineStore.setState({
      authStatus: "authenticated",
      user: { id: "A", username: "甲" },
    });
    let finish!: (text: string) => void;
    const file = new File(["{}"], "card.json", { type: "application/json" });
    Object.defineProperty(file, "text", {
      value: () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    });
    render(<CharacterLibraryPanel />);
    fireEvent.click(screen.getByRole("button", { name: "导入角色卡" }));
    fireEvent.change(document.querySelector('input[type="file"]')!, {
      target: { files: [file] },
    });
    act(() => useOnlineStore.setState({ user: { id: "B", username: "乙" } }));
    await act(async () => finish("{}"));
    expect(api.inspectLibraryCard).not.toHaveBeenCalled();
    expect(api.createLibraryEntry).not.toHaveBeenCalled();
  });

  it("返回列表后迟到的文件校验不能变成新导入预览", async () => {
    let finish!: (result: unknown) => void;
    api.inspectLibraryCard.mockReturnValueOnce(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    render(<CharacterLibraryPanel />);
    fireEvent.click(screen.getByRole("button", { name: "导入角色卡" }));
    fireEvent.change(document.querySelector('input[type="file"]')!, {
      target: { files: [new File(["{}"], "card.json")] },
    });
    await waitFor(() => expect(api.inspectLibraryCard).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "返回" }));
    fireEvent.click(screen.getByRole("button", { name: "导入角色卡" }));
    await act(async () =>
      finish({
        ok: true,
        errors: [],
        warnings: [],
        preview: makeEntry({ name: "旧文件的预览" }),
      }),
    );
    expect(screen.queryByText("旧文件的预览")).not.toBeInTheDocument();
  });

  it("按姓名或职业搜索，不丢失已选档案，空结果有说明", async () => {
    api.listCharacterLibrary.mockResolvedValue([
      makeEntry(),
      makeEntry({ id: "doctor", name: "王医生", occupation: "医生" }),
    ]);
    render(<CharacterLibraryPanel />);
    fireEvent.click(await screen.findByText("测试调查员"));
    fireEvent.change(screen.getByLabelText("查找档案"), {
      target: { value: "医生" },
    });
    expect(screen.getByText("王医生")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "测试调查员" }),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("查找档案"), {
      target: { value: "查无此人" },
    });
    expect(screen.getByText(/没有匹配的档案/)).toBeInTheDocument();
    expect(api.listCharacterLibrary).toHaveBeenCalledTimes(1);
  });

  it("读取失败可以原地重试，不把故障显示成空库", async () => {
    const { ApiError } = await import("../../api/client");
    api.listCharacterLibrary.mockRejectedValueOnce(
      new ApiError("服务器未及时响应", 0, "request_timeout"),
    );
    render(<CharacterLibraryPanel />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "服务器未及时响应",
    );
    expect(screen.queryByText("角色库还是空的")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
    expect(await screen.findByText("测试调查员")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("保存期间所有操作禁用时，焦点留在弹窗而不是回到背景", async () => {
    let finish!: (value: unknown) => void;
    api.createLibraryEntry.mockReturnValueOnce(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    render(<CharacterLibraryPanel />);
    fireEvent.click(screen.getByRole("button", { name: "新建角色" }));
    fireEvent.change(screen.getByLabelText(/姓名/), {
      target: { value: "保存中的卡" },
    });
    fireEvent.change(screen.getByLabelText(/职业/), {
      target: { value: "记者" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建角色" }));
    const panel = screen.getByRole("dialog");
    expect(panel).toHaveFocus();
    fireEvent.keyDown(panel, { key: "Tab" });
    expect(panel).toHaveFocus();
    fireEvent.keyDown(panel, { key: "Escape" });
    expect(useAppStore.getState().characterLibraryOpen).toBe(true);
    await act(async () => finish({ entry: makeEntry(), warnings: [] }));
    expect(panel.contains(document.activeElement)).toBe(true);
  });
});
