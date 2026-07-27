import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { desktopBridge } from "../../desktop";
import { useAppStore, detectInitialMode } from "../../state/app-store";
import { ModeSelectScreen } from "./ModeSelectScreen";

vi.mock("../../desktop", () => ({
  desktopBridge: vi.fn(() => null),
}));

const bridge = {
  getOnlineOrigin: vi.fn(),
  selectLocalMode: vi.fn(),
  selectOnlineMode: vi.fn(),
  returnToLauncher: vi.fn(),
};

beforeEach(() => {
  useAppStore.setState({ mode: "select" });
  vi.clearAllMocks();
  vi.mocked(desktopBridge).mockReturnValue(null);
  bridge.getOnlineOrigin.mockResolvedValue({ ok: true, origin: null });
  localStorage.clear();
  window.history.replaceState({}, "", "/");
});

describe("ModeSelectScreen（浏览器流程）", () => {
  it("展示单机与多人两个入口", () => {
    render(<ModeSelectScreen />);
    expect(screen.getByText("单机游戏")).toBeInTheDocument();
    expect(screen.getByText("多人游戏")).toBeInTheDocument();
  });

  it("选择单机进入 local 模式", () => {
    render(<ModeSelectScreen />);
    fireEvent.click(screen.getByText("单机游戏"));
    expect(useAppStore.getState().mode).toBe("local");
  });

  it("选择多人进入 online 模式", () => {
    render(<ModeSelectScreen />);
    fireEvent.click(screen.getByText("多人游戏"));
    expect(useAppStore.getState().mode).toBe("online");
  });

  it("浏览器环境不显示云端地址输入框", () => {
    render(<ModeSelectScreen />);
    expect(screen.queryByLabelText("云端服务器地址")).not.toBeInTheDocument();
  });
});

describe("ModeSelectScreen（Electron 单机）", () => {
  beforeEach(() => {
    vi.mocked(desktopBridge).mockReturnValue(bridge);
  });

  it("经主进程启动本地后端成功后进入 local", async () => {
    bridge.selectLocalMode.mockResolvedValue({ ok: true });
    render(<ModeSelectScreen />);
    fireEvent.click(screen.getByText("单机游戏"));
    await waitFor(() => expect(bridge.selectLocalMode).toHaveBeenCalled());
    await waitFor(() => expect(useAppStore.getState().mode).toBe("local"));
  });

  it("用户取消配置时静默返回，不报错也不切模式", async () => {
    bridge.selectLocalMode.mockResolvedValue({ ok: false, cancelled: true });
    render(<ModeSelectScreen />);
    fireEvent.click(screen.getByText("单机游戏"));
    await waitFor(() => expect(bridge.selectLocalMode).toHaveBeenCalled());
    expect(useAppStore.getState().mode).toBe("select");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("本地后端启动失败时展示错误", async () => {
    bridge.selectLocalMode.mockResolvedValue({
      ok: false,
      error: "后端启动超时",
    });
    render(<ModeSelectScreen />);
    fireEvent.click(screen.getByText("单机游戏"));
    expect(await screen.findByRole("alert")).toHaveTextContent("后端启动超时");
    expect(useAppStore.getState().mode).toBe("select");
  });
});

describe("ModeSelectScreen（Electron 联机）", () => {
  beforeEach(() => {
    vi.mocked(desktopBridge).mockReturnValue(bridge);
  });

  it("未输入 https 地址时给出提示", () => {
    render(<ModeSelectScreen />);
    fireEvent.click(screen.getByText("多人游戏"));
    expect(screen.getByRole("alert")).toHaveTextContent("https");
    expect(bridge.selectOnlineMode).not.toHaveBeenCalled();
  });

  it("从主进程读取上次保存的地址", async () => {
    bridge.getOnlineOrigin.mockResolvedValue({
      ok: true,
      origin: "https://saved.example",
    });
    render(<ModeSelectScreen />);
    await waitFor(() =>
      expect(screen.getByLabelText("云端服务器地址")).toHaveValue(
        "https://saved.example",
      ),
    );
  });

  it("校验通过后交由主进程持久化并同源加载", async () => {
    bridge.selectOnlineMode.mockResolvedValue({ ok: true });
    render(<ModeSelectScreen />);
    fireEvent.change(screen.getByLabelText("云端服务器地址"), {
      target: { value: "https://trpg.example.com/" },
    });
    fireEvent.click(screen.getByText("多人游戏"));
    await waitFor(() =>
      expect(bridge.selectOnlineMode).toHaveBeenCalledWith(
        "https://trpg.example.com",
      ),
    );
    expect(localStorage.getItem("trpg-cloud-origin")).toBeNull();
  });

  it("主进程拒绝时展示错误", async () => {
    bridge.selectOnlineMode.mockResolvedValue({
      ok: false,
      error: "invalid-origin",
    });
    render(<ModeSelectScreen />);
    fireEvent.change(screen.getByLabelText("云端服务器地址"), {
      target: { value: "https://trpg.example.com" },
    });
    fireEvent.click(screen.getByText("多人游戏"));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "服务器地址无效",
    );
  });
});

describe("detectInitialMode", () => {
  it("无标记时为 select", () => {
    expect(detectInitialMode()).toBe("select");
  });

  it("?mode=online 直接进入联机", () => {
    window.history.replaceState({}, "", "/?mode=online");
    expect(detectInitialMode()).toBe("online");
  });

  it("?mode=local 直接进入单机", () => {
    window.history.replaceState({}, "", "/?mode=local");
    expect(detectInitialMode()).toBe("local");
  });

  it("非法标记按 select 处理", () => {
    window.history.replaceState({}, "", "/?mode=hack");
    expect(detectInitialMode()).toBe("select");
  });
});
