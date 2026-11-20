import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createSoloWorld, enterRoom, ensureModules } from "../../../online";
import { changePassword, updateProfile } from "../../../api/auth";
import { apiFetch } from "../../../api/client";
import { useAppStore } from "../../../state/app-store";
import {
  initialOnlineState,
  useOnlineStore,
} from "../../../state/online-store";
import { SoloLobbyScreen } from "./SoloLobbyScreen";

vi.mock("../../../online", () => ({
  createSoloWorld: vi.fn(),
  ensureModules: vi.fn(),
  enterRoom: vi.fn(),
  errorMessage: (error: unknown, fallback: string) =>
    error instanceof Error ? error.message : fallback,
  logout: vi.fn(),
  refreshWorlds: vi.fn(),
}));

vi.mock("../../../api/auth", () => ({
  changePassword: vi.fn(),
  updateProfile: vi.fn(),
}));

vi.mock("../../../api/client", () => ({
  apiFetch: vi.fn(),
  apiHttpOrigin: () => "http://test",
}));

vi.mock("../../../desktop", () => ({
  desktopBridge: vi.fn(() => null),
}));

const soloWorld = {
  world_id: "w-solo",
  module: "mod-1",
  role: "owner",
  updated_at: "2026-08-01T12:00:00Z",
  metadata: { name: "雾中宅邸", play_mode: "solo", room_status: "playing" },
};

const multiWorld = {
  world_id: "w-multi",
  module: "mod-2",
  role: "owner",
  metadata: { name: "周五调查夜", play_mode: "multiplayer" },
};

function setupOnline(patch: Record<string, unknown> = {}) {
  useOnlineStore.setState({
    ...initialOnlineState,
    authStatus: "authenticated",
    user: { id: "u1", username: "alice" },
    view: "solo",
    worldsStatus: "ready",
    worlds: [soloWorld, multiWorld],
    modulesStatus: "ready",
    modules: [
      { id: "mod-1", title: "猩红文档" },
      { id: "mod-2", title: "疯狂公馆" },
    ],
    ...patch,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(ensureModules).mockResolvedValue(undefined);
  vi.mocked(apiFetch).mockResolvedValue({ ok: true });
  localStorage.clear();
  useAppStore.setState({ mode: "online" });
  setupOnline();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("SoloLobbyScreen 从存档开始", () => {
  it("打开当前模组的存档列表，排除较新的未开局世界，读取前不进房", async () => {
    const branch = {
      ...soloWorld,
      world_id: "branched",
      resume_world_id: "current-branch",
    };
    const draft = {
      ...soloWorld,
      world_id: "draft",
      metadata: { play_mode: "solo", room_status: "lobby" },
      updated_at: "2026-10-01T12:00:00Z",
    };
    const worlds = [
      draft,
      soloWorld,
      branch,
      { ...soloWorld, module: "mod-2" },
      multiWorld,
    ];
    setupOnline({ worlds });
    vi.mocked(apiFetch).mockImplementation(async (url) => {
      if (url === "/api/worlds") return { worlds };
      if (url === "/api/worlds/current-branch/members")
        return { metadata: branch.metadata };
      for (const [root, target] of [
        ["w-solo", "w-solo"],
        ["branched", "current-branch"],
      ]) {
        if (url === `/api/worlds/${root}/timelines`)
          return {
            active_world_id: target,
            worlds: [{ world_id: target, scene_name: "档案馆" }],
          };
      }
      throw Error("unexpected endpoint " + url);
    });
    render(<SoloLobbyScreen />);
    fireEvent.click(screen.getByRole("button", { name: "从存档开始" }));
    await waitFor(() =>
      expect(useAppStore.getState().savePanelOpen).toBe(true),
    );
    expect(useAppStore.getState().savePanelMode).toBe("load");
    expect(
      useAppStore.getState().adventures.map((s) => s.resume_world_id),
    ).toEqual(["w-solo", "current-branch"]);
    expect(enterRoom).not.toHaveBeenCalled();
  });

  it("没有 solo 存档时禁用读档入口", () => {
    setupOnline({
      worlds: [
        multiWorld,
        { ...soloWorld, metadata: { play_mode: "solo", room_status: "lobby" } },
      ],
    });
    render(<SoloLobbyScreen />);
    expect(screen.getByRole("button", { name: "从存档开始" })).toBeDisabled();
    expect(enterRoom).not.toHaveBeenCalled();
  });
});

describe("SoloLobbyScreen 操作", () => {
  it("从首页直接按所选模组准备选角", async () => {
    render(<SoloLobbyScreen />);
    fireEvent.click(screen.getByRole("button", { name: "当前模组" }));
    fireEvent.click(screen.getByRole("option", { name: "疯狂公馆" }));
    await screen.findByRole("heading", { name: "疯狂公馆" });
    fireEvent.click(screen.getByRole("button", { name: "开始新冒险" }));
    expect(createSoloWorld).toHaveBeenCalledWith("mod-2", "疯狂公馆");
    expect(screen.queryByLabelText("冒险名称")).toBeNull();
    expect(screen.queryByRole("button", { name: /^游玩方式/ })).toBeNull();
  });

  it("六个游戏工具可直接打开，个人中心只处理账号，模组选择用于新建冒险", async () => {
    render(<SoloLobbyScreen />);
    expect(
      screen
        .getByRole("navigation", { name: "游戏工具" })
        .querySelectorAll("button"),
    ).toHaveLength(6);
    fireEvent.click(screen.getByRole("button", { name: "个人中心" }));
    expect(screen.getByText("alice")).toBeVisible();
    expect(
      screen.getByRole("dialog", { name: "个人中心" }).textContent,
    ).not.toContain("角色库");
    fireEvent.click(screen.getByRole("button", { name: "关闭个人中心" }));
    fireEvent.click(screen.getByRole("button", { name: "当前模组" }));
    fireEvent.click(screen.getByRole("option", { name: "疯狂公馆" }));
    expect(
      await screen.findByRole("heading", { name: "疯狂公馆" }),
    ).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "开始新冒险" }));
    expect(createSoloWorld).toHaveBeenCalledWith("mod-2", "疯狂公馆");
  });

  it("模组排序不改变当前模组，也不关闭管理面板", async () => {
    setupOnline({ worlds: [] });
    vi.mocked(apiFetch).mockResolvedValue({ ok: true });
    render(<SoloLobbyScreen />);
    fireEvent.click(screen.getByRole("button", { name: "模组管理" }));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "将猩红文档下移" }),
      ).toBeEnabled(),
    );
    vi.mocked(ensureModules).mockImplementation(async () => {
      useOnlineStore.setState({
        modulesStatus: "ready",
        modules: [
          { id: "mod-2", title: "疯狂公馆" },
          { id: "mod-1", title: "猩红文档" },
        ],
      });
    });
    fireEvent.click(screen.getByRole("button", { name: "将猩红文档下移" }));

    await waitFor(() => expect(ensureModules).toHaveBeenCalled());
    expect(screen.getByRole("dialog", { name: "模组管理" })).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "猩红文档", level: 1 }),
    ).toBeVisible();
    expect(document.querySelector("#start-overlay.module-flipping")).toBeNull();
  });

  it("删除当前预览模组后管理面板保持打开并显示真实结果", async () => {
    setupOnline({
      worlds: [],
      modules: [
        { id: "mod-1", title: "猩红文档" },
        {
          id: "mod-2",
          title: "疯狂公馆",
          source: "account",
          world_count: 1,
          save_count: 1,
        },
      ],
    });
    vi.mocked(apiFetch).mockResolvedValue({
      ok: true,
      world_count: 1,
      save_count: 1,
      cleanup_pending: false,
    });
    render(<SoloLobbyScreen />);
    fireEvent.click(screen.getByRole("button", { name: "当前模组" }));
    fireEvent.click(screen.getByRole("option", { name: "疯狂公馆" }));
    await screen.findByRole("heading", { name: "疯狂公馆", level: 1 });
    await waitFor(() =>
      expect(document.querySelector("#start-overlay")?.className).toBe(
        "solo-home",
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "模组管理" }));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "从模组库移除疯狂公馆" }),
      ).toBeEnabled(),
    );
    vi.mocked(ensureModules).mockImplementation(async () => {
      useOnlineStore.setState({
        modulesStatus: "ready",
        modules: [{ id: "mod-1", title: "猩红文档" }],
      });
    });
    fireEvent.click(
      screen.getByRole("button", { name: "从模组库移除疯狂公馆" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "确认从库中移除" }));

    await waitFor(() => expect(ensureModules).toHaveBeenCalled());
    expect(screen.getByRole("dialog", { name: "模组管理" })).toBeVisible();
    expect(
      within(screen.getByRole("dialog", { name: "模组管理" })).getByRole(
        "status",
      ),
    ).toHaveTextContent("已删除「疯狂公馆」及其 1 条时间线、1 个存档点");
  });

  it("首页切换模组播放翻页动画，旧标题冻结到新页入场", async () => {
    vi.useFakeTimers();
    render(<SoloLobbyScreen />);
    fireEvent.click(screen.getByRole("button", { name: "当前模组" }));
    fireEvent.click(screen.getByRole("option", { name: "疯狂公馆" }));

    expect(
      document.querySelector("#start-overlay.module-flipping"),
    ).not.toBeNull();
    expect(document.querySelector(".module-page-flip")).not.toBeNull();
    expect(screen.getByRole("heading", { name: "猩红文档" })).toBeVisible();

    await act(async () => {
      vi.advanceTimersByTime(800);
    });
    expect(screen.getByRole("heading", { name: "疯狂公馆" })).toBeVisible();
    expect(document.querySelector("#start-overlay.module-flipping")).toBeNull();
  });

  it("个人中心可以设置并修改昵称", async () => {
    vi.mocked(updateProfile).mockResolvedValue({
      id: "u1",
      username: "alice",
      display_name: "调查员小爱",
    });
    render(<SoloLobbyScreen />);
    fireEvent.click(screen.getByRole("button", { name: "个人中心" }));
    fireEvent.change(screen.getByLabelText("昵称"), {
      target: { value: "  调查员小爱  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存昵称" }));

    await waitFor(() =>
      expect(updateProfile).toHaveBeenCalledWith("调查员小爱"),
    );
    expect(useOnlineStore.getState().user?.display_name).toBe("调查员小爱");
    expect(await screen.findByText("昵称已保存。")).toBeVisible();
  });

  it("个人中心验证当前密码并修改新密码", async () => {
    vi.mocked(changePassword).mockResolvedValue(undefined);
    render(<SoloLobbyScreen />);
    fireEvent.click(screen.getByRole("button", { name: "个人中心" }));
    fireEvent.change(screen.getByLabelText("当前密码"), {
      target: { value: "old password 123" },
    });
    fireEvent.change(screen.getByLabelText("新密码"), {
      target: { value: "new password 456" },
    });
    fireEvent.change(screen.getByLabelText("确认新密码"), {
      target: { value: "new password 456" },
    });
    fireEvent.click(screen.getByRole("button", { name: "修改密码" }));

    await waitFor(() =>
      expect(changePassword).toHaveBeenCalledWith(
        "old password 123",
        "new password 456",
      ),
    );
    expect(await screen.findByText(/密码已修改/)).toBeVisible();
  });

  it("浏览器环境返回模式选择", () => {
    render(<SoloLobbyScreen />);
    fireEvent.click(screen.getByRole("button", { name: "个人中心" }));
    fireEvent.click(screen.getByRole("button", { name: "返回模式选择" }));
    expect(useAppStore.getState().mode).toBe("select");
  });
});
