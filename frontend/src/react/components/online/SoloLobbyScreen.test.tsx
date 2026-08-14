import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  createSoloWorld,
  deleteSoloWorld,
  enterRoom,
  refreshWorlds,
} from "../../../online";
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
  logout: vi.fn(),
  refreshWorlds: vi.fn(),
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
  it("继续冒险进入对应房间", () => {
    render(<SoloLobbyScreen />);
    fireEvent.click(screen.getByText("雾中宅邸"));
    expect(enterRoom).toHaveBeenCalledWith("w-solo");
  });

  it("新建冒险：展开表单后使用选中的模组与名字", () => {
    render(<SoloLobbyScreen />);
    // 新建表单默认收起，由黄铜主 CTA 展开
    expect(screen.queryByLabelText("冒险名称")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "开始新冒险" }));
    fireEvent.change(screen.getByLabelText("冒险名称"), {
      target: { value: "新的调查" },
    });
    fireEvent.change(screen.getByLabelText("选择模组"), {
      target: { value: "mod-2" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建冒险" }));
    expect(createSoloWorld).toHaveBeenCalledWith("mod-2", "新的调查");
  });

  it("删除存档需要行内二次确认", async () => {
    vi.mocked(deleteSoloWorld).mockResolvedValue(true);
    render(<SoloLobbyScreen />);
    fireEvent.click(screen.getByRole("button", { name: "删除存档" }));
    expect(deleteSoloWorld).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(deleteSoloWorld).toHaveBeenCalledWith("w-solo"));
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
