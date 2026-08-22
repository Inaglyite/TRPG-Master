import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  createSoloWorld,
  deleteSoloWorld,
  enterRoom,
  refreshWorlds,
} from "../../../online";
import { fetchSoloTimelines } from "../../../api/worlds";
import { useAppStore } from "../../../state/app-store";
import {
  initialOnlineState,
  useOnlineStore,
} from "../../../state/online-store";
import { SoloLobbyScreen } from "./SoloLobbyScreen";

vi.mock("../../../online", () => ({
  createSoloWorld: vi.fn(),
  deleteSoloWorld: vi.fn(),
  enterRoom: vi.fn(),
  errorMessage: (error: unknown, fallback: string) =>
    error instanceof Error ? error.message : fallback,
  logout: vi.fn(),
  refreshWorlds: vi.fn(),
}));

vi.mock("../../../api/worlds", () => ({
  archiveSoloTimeline: vi.fn(),
  fetchSoloTimelines: vi.fn(),
  renameSoloTimeline: vi.fn(),
  switchSoloTimeline: vi.fn(),
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
  vi.mocked(fetchSoloTimelines).mockResolvedValue({
    root_world_id: "w-solo",
    active_world_id: "w-solo",
    worlds: [
      {
        world_id: "w-solo",
        label: "",
        is_branch: false,
        active: true,
        resumable: true,
        scene_name: "门厅",
        character_name: "黄千陆",
        updated_at: "2026-08-16T12:00:00Z",
      },
    ],
  });
  useAppStore.setState({ mode: "online" });
  setupOnline();
});

describe("SoloLobbyScreen 冒险列表", () => {
  it("复用大厅的舒展布局样式", () => {
    render(<SoloLobbyScreen />);
    expect(screen.getByTestId("solo-lobby")).toHaveClass("lobby-screen");
  });

  it("只列出 play_mode=solo 的世界（名称、模组标题、云端存档标识）", () => {
    render(<SoloLobbyScreen />);
    expect(screen.getByText("雾中宅邸")).toBeInTheDocument();
    expect(screen.getAllByText("猩红文档").length).toBeGreaterThan(0);
    expect(screen.getByText("云端存档")).toBeInTheDocument();
    expect(screen.queryByText("周五调查夜")).not.toBeInTheDocument();
  });

  it("没有 solo 世界时显示空态文案", () => {
    setupOnline({ worlds: [multiWorld] });
    render(<SoloLobbyScreen />);
    expect(screen.getByText(/还没有云端单人冒险/)).toBeInTheDocument();
  });

  it("读取失败时显示错误并可重试", () => {
    setupOnline({ worlds: [], worldsStatus: "error", worldsError: "网络错误" });
    render(<SoloLobbyScreen />);
    expect(screen.getByRole("alert")).toHaveTextContent("网络错误");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(refreshWorlds).toHaveBeenCalled();
  });
});

describe("SoloLobbyScreen 操作", () => {
  it("点击存档卡主体打开时间线面板，不进入房间", async () => {
    render(<SoloLobbyScreen />);
    fireEvent.click(screen.getByText("雾中宅邸"));
    expect(enterRoom).not.toHaveBeenCalled();
    expect(fetchSoloTimelines).toHaveBeenCalledWith("w-solo");
    expect(
      await screen.findByRole("dialog", { name: "雾中宅邸" }),
    ).toBeInTheDocument();
  });

  it("继续冒险优先连接 resume_world_id（当前时间线）", () => {
    setupOnline({
      worlds: [{ ...soloWorld, resume_world_id: "w-timeline-2" }, multiWorld],
    });
    render(<SoloLobbyScreen />);
    fireEvent.click(screen.getByRole("button", { name: "继续冒险" }));
    expect(enterRoom).toHaveBeenCalledWith("w-timeline-2");
  });

  it("playing 的存档位显示“管理时间线”，点击后在大厅打开面板（不进房）", async () => {
    setupOnline({
      worlds: [{ ...soloWorld, resume_world_id: "w-timeline-2" }, multiWorld],
    });
    render(<SoloLobbyScreen />);
    fireEvent.click(screen.getByRole("button", { name: "管理时间线" }));
    expect(enterRoom).not.toHaveBeenCalled();
    expect(useOnlineStore.getState().pendingTimelinePanel).toBe(false);
    expect(fetchSoloTimelines).toHaveBeenCalledWith("w-solo");
    expect(
      await screen.findByRole("dialog", { name: "雾中宅邸" }),
    ).toBeInTheDocument();
  });

  it("非 playing 的存档位同样显示“管理时间线”（纯 HTTP 控制面，不进房）", async () => {
    setupOnline({
      worlds: [
        {
          ...soloWorld,
          metadata: { ...soloWorld.metadata, room_status: "lobby" },
        },
        multiWorld,
      ],
    });
    render(<SoloLobbyScreen />);
    fireEvent.click(screen.getByRole("button", { name: "管理时间线" }));
    expect(enterRoom).not.toHaveBeenCalled();
    expect(fetchSoloTimelines).toHaveBeenCalledWith("w-solo");
    expect(
      await screen.findByRole("dialog", { name: "雾中宅邸" }),
    ).toBeInTheDocument();
  });

  it("新建冒险：展开表单后使用选中的模组与名字", async () => {
    render(<SoloLobbyScreen />);
    // 新建表单默认收起，由木牌次级 CTA 经换场动画展开
    expect(screen.queryByLabelText("冒险名称")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "开始新冒险" }));
    fireEvent.change(await screen.findByLabelText("冒险名称"), {
      target: { value: "新的调查" },
    });
    // 模组选择是自绘 listbox（ModuleSelect），不是原生 select
    fireEvent.click(screen.getByRole("button", { name: "选择模组" }));
    fireEvent.click(screen.getByRole("option", { name: "疯狂公馆" }));
    fireEvent.click(screen.getByRole("button", { name: "创建冒险" }));
    expect(createSoloWorld).toHaveBeenCalledWith("mod-2", "新的调查");
  });

  it("开始新冒险换场：CTA 先播 leaving，创建卡再播 entering，收起反向播回", () => {
    vi.useFakeTimers();
    try {
      render(<SoloLobbyScreen />);
      const swap = () => document.querySelector(".solo-lobby-create-swap");
      expect(swap()).toHaveAttribute("data-phase", "idle");

      fireEvent.click(screen.getByRole("button", { name: "开始新冒险" }));
      // leaving 阶段仍展示旧 CTA
      expect(swap()).toHaveAttribute("data-phase", "leaving");
      expect(
        screen.getByRole("button", { name: "开始新冒险" }),
      ).toBeInTheDocument();

      act(() => vi.advanceTimersByTime(160));
      expect(swap()).toHaveAttribute("data-phase", "entering");
      expect(screen.getByLabelText("冒险名称")).toBeInTheDocument();

      act(() => vi.advanceTimersByTime(260));
      expect(swap()).toHaveAttribute("data-phase", "idle");

      fireEvent.click(screen.getByRole("button", { name: "收起" }));
      expect(swap()).toHaveAttribute("data-phase", "leaving");
      act(() => vi.advanceTimersByTime(160));
      expect(
        screen.getByRole("button", { name: "开始新冒险" }),
      ).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("删除存档需要行内二次确认", async () => {
    vi.mocked(deleteSoloWorld).mockResolvedValue(null);
    render(<SoloLobbyScreen />);
    fireEvent.click(screen.getByRole("button", { name: "删除存档" }));
    expect(deleteSoloWorld).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(deleteSoloWorld).toHaveBeenCalledWith("w-solo"));
  });

  it("删除失败时报错内联挂在对应冒险卡上，而不是创建卡片", async () => {
    vi.mocked(deleteSoloWorld).mockResolvedValue("删除存档失败，请重试");
    render(<SoloLobbyScreen />);
    fireEvent.click(screen.getByRole("button", { name: "删除存档" }));
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("删除存档失败");
    expect(alert.closest('[data-world="w-solo"]')).not.toBeNull();
    // 恢复可重试态
    expect(
      screen.getByRole("button", { name: "删除存档" }),
    ).toBeInTheDocument();
  });

  it("取消二次确认不删除", () => {
    render(<SoloLobbyScreen />);
    fireEvent.click(screen.getByRole("button", { name: "删除存档" }));
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(deleteSoloWorld).not.toHaveBeenCalled();
    expect(
      screen.getByRole("button", { name: "删除存档" }),
    ).toBeInTheDocument();
  });

  it("浏览器环境返回模式选择", () => {
    render(<SoloLobbyScreen />);
    fireEvent.click(screen.getByRole("button", { name: "← 返回模式选择" }));
    expect(useAppStore.getState().mode).toBe("select");
  });
});
