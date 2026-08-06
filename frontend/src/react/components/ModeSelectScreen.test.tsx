import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { desktopBridge } from "../../desktop";
import { useAppStore, detectInitialMode } from "../../state/app-store";
import { useOnlineStore } from "../../state/online-store";
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
  useOnlineStore.setState({ pendingIntent: "lobby" });
  vi.clearAllMocks();
  vi.mocked(desktopBridge).mockReturnValue(null);
  bridge.getOnlineOrigin.mockResolvedValue({ ok: true, origin: null });
  localStorage.clear();
  window.history.replaceState({}, "", "/");
});

describe("ModeSelectScreen（浏览器流程）", () => {
  it("展示本地单人、云端单人与多人三个入口", () => {
    render(<ModeSelectScreen />);
    expect(screen.getByText("本地单人")).toBeInTheDocument();
    expect(screen.getByText("云端单人")).toBeInTheDocument();
    expect(screen.getByText("多人游戏")).toBeInTheDocument();
  });

  it("浏览器禁用本地单人并注明使用桌面版", () => {
    render(<ModeSelectScreen />);
    const localButton = screen.getByRole("button", { name: /本地单人/ });
    expect(localButton).toBeDisabled();
    expect(screen.getAllByText("本地单人请使用桌面版").length).toBeGreaterThan(
      0,
    );
    fireEvent.click(localButton);
    expect(useAppStore.getState().mode).toBe("select");
  });

  it("选择云端单人进入 online 模式并记录 solo 意图", () => {
    render(<ModeSelectScreen />);
    fireEvent.click(screen.getByRole("button", { name: /云端单人/ }));
    expect(useAppStore.getState().mode).toBe("online");
    expect(useOnlineStore.getState().pendingIntent).toBe("solo");
  });

  it("选择多人进入 online 模式", () => {
    render(<ModeSelectScreen />);
    fireEvent.click(screen.getByText("多人游戏"));
    expect(useAppStore.getState().mode).toBe("online");
    expect(useOnlineStore.getState().pendingIntent).toBe("lobby");
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
    fireEvent.click(screen.getByRole("button", { name: /本地单人/ }));
    await waitFor(() => expect(bridge.selectLocalMode).toHaveBeenCalled());
    await waitFor(() => expect(useAppStore.getState().mode).toBe("local"));
  });

  it("用户取消配置时静默返回，不报错也不切模式", async () => {
    bridge.selectLocalMode.mockResolvedValue({ ok: false, cancelled: true });
    render(<ModeSelectScreen />);
    fireEvent.click(screen.getByRole("button", { name: /本地单人/ }));
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
    fireEvent.click(screen.getByRole("button", { name: /本地单人/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("后端启动超时");
    expect(useAppStore.getState().mode).toBe("select");
  });
});

describe("ModeSelectScreen（Electron 联机）", () => {
  beforeEach(() => {
    vi.mocked(desktopBridge).mockReturnValue(bridge);
  });

  it("首次进入默认连接官方服务器，无需手填地址", async () => {
    bridge.selectOnlineMode.mockResolvedValue({ ok: true });
    render(<ModeSelectScreen />);
    expect(screen.queryByLabelText("云端服务器地址")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("多人游戏"));
    await waitFor(() =>
      expect(bridge.selectOnlineMode).toHaveBeenCalledWith(
        "https://trpggame.xyz",
        "lobby",
      ),
    );
  });

  it("云端单人入口把 solo 意图交给主进程", async () => {
    bridge.selectOnlineMode.mockResolvedValue({ ok: true });
    render(<ModeSelectScreen />);
    fireEvent.click(screen.getByRole("button", { name: /云端单人/ }));
    await waitFor(() =>
      expect(bridge.selectOnlineMode).toHaveBeenCalledWith(
        "https://trpggame.xyz",
        "solo",
      ),
    );
  });

  it("自定义配置默认折叠，展开后留空仍走官方服务器", async () => {
    bridge.selectOnlineMode.mockResolvedValue({ ok: true });
    render(<ModeSelectScreen />);
    fireEvent.click(screen.getByText("自定义服务器（开发/验收）"));
    const input = screen.getByLabelText("云端服务器地址");
    expect(input).toHaveValue("https://trpggame.xyz");
    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.click(screen.getByText("多人游戏"));
    await waitFor(() =>
      expect(bridge.selectOnlineMode).toHaveBeenCalledWith(
        "https://trpggame.xyz",
        "lobby",
      ),
    );
  });

  it("自定义地址非 https 时给出提示，不调用主进程", () => {
    render(<ModeSelectScreen />);
    fireEvent.click(screen.getByText("自定义服务器（开发/验收）"));
    fireEvent.change(screen.getByLabelText("云端服务器地址"), {
      target: { value: "http://insecure.example" },
    });
    fireEvent.click(screen.getByText("多人游戏"));
    expect(screen.getByRole("alert")).toHaveTextContent("https");
    expect(bridge.selectOnlineMode).not.toHaveBeenCalled();
  });

  it("从主进程读取已保存的自定义地址并自动展开", async () => {
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

  it("已保存官方地址时保持折叠，不展示自定义配置", async () => {
    bridge.getOnlineOrigin.mockResolvedValue({
      ok: true,
      origin: "https://trpggame.xyz",
    });
    render(<ModeSelectScreen />);
    await waitFor(() => expect(bridge.getOnlineOrigin).toHaveBeenCalled());
    expect(screen.queryByLabelText("云端服务器地址")).not.toBeInTheDocument();
  });

  it("校验通过后交由主进程持久化并同源加载", async () => {
    bridge.selectOnlineMode.mockResolvedValue({ ok: true });
    render(<ModeSelectScreen />);
    fireEvent.click(screen.getByText("自定义服务器（开发/验收）"));
    fireEvent.change(screen.getByLabelText("云端服务器地址"), {
      target: { value: "https://trpg.example.com/" },
    });
    fireEvent.click(screen.getByText("多人游戏"));
    await waitFor(() =>
      expect(bridge.selectOnlineMode).toHaveBeenCalledWith(
        "https://trpg.example.com",
        "lobby",
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
    fireEvent.click(screen.getByText("自定义服务器（开发/验收）"));
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
