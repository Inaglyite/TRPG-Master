import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  checkSession,
  enterLobby,
  enterSoloLobby,
  login,
  register,
} from "../../../online";
import { useAppStore } from "../../../state/app-store";
import {
  initialOnlineState,
  useOnlineStore,
} from "../../../state/online-store";
import { AuthScreen } from "./AuthScreen";

vi.mock("../../../online", () => ({
  checkSession: vi.fn(),
  enterLobby: vi.fn(),
  enterSoloLobby: vi.fn(),
  login: vi.fn(),
  register: vi.fn(),
}));

beforeEach(() => {
  useOnlineStore.setState({ ...initialOnlineState, authStatus: "anonymous" });
  useAppStore.setState({ mode: "online" });
  vi.clearAllMocks();
  localStorage.clear();
  delete window.trpgDesktop;
});

describe("AuthScreen 会话检查", () => {
  it("checking 状态显示加载提示", () => {
    useOnlineStore.setState({ authStatus: "checking" });
    render(<AuthScreen />);
    expect(screen.getByRole("status")).toHaveTextContent("正在检查登录状态");
  });

  it("会话过期时显示提示", () => {
    useOnlineStore.setState({ sessionExpired: true });
    render(<AuthScreen />);
    expect(screen.getByRole("alert")).toHaveTextContent("登录已过期");
  });
});

describe("AuthScreen 登录", () => {
  it("空表单不提交并显示校验错误", async () => {
    render(<AuthScreen />);
    fireEvent.click(screen.getByRole("button", { name: "登录" }));
    expect(login).not.toHaveBeenCalled();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "请输入用户名和密码",
    );
  });

  it("提交成功后进入大厅", async () => {
    vi.mocked(login).mockResolvedValue(true);
    render(<AuthScreen />);
    fireEvent.change(screen.getByLabelText("用户名"), {
      target: { value: "alice" },
    });
    fireEvent.change(screen.getByLabelText("密码"), {
      target: { value: "secret" },
    });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));
    await waitFor(() => expect(login).toHaveBeenCalledWith("alice", "secret"));
    await waitFor(() => expect(enterLobby).toHaveBeenCalled());
    expect(enterSoloLobby).not.toHaveBeenCalled();
  });

  it("solo 意图下登录成功落到我的冒险", async () => {
    vi.mocked(login).mockResolvedValue(true);
    useOnlineStore.setState({ pendingIntent: "solo" });
    render(<AuthScreen />);
    expect(screen.getByText("云端单人")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("用户名"), {
      target: { value: "alice" },
    });
    fireEvent.change(screen.getByLabelText("密码"), {
      target: { value: "secret" },
    });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));
    await waitFor(() => expect(login).toHaveBeenCalledWith("alice", "secret"));
    await waitFor(() => expect(enterSoloLobby).toHaveBeenCalled());
    expect(enterLobby).not.toHaveBeenCalled();
  });

  it("展示 store 中的认证错误", () => {
    useOnlineStore.setState({ authError: "无效的用户名或密码" });
    render(<AuthScreen />);
    expect(screen.getByRole("alert")).toHaveTextContent("无效的用户名或密码");
  });
});

describe("AuthScreen 注册", () => {
  it("两次密码不一致时拦截提交", async () => {
    render(<AuthScreen />);
    fireEvent.click(screen.getByRole("tab", { name: "注册" }));
    fireEvent.change(screen.getByLabelText("用户名"), {
      target: { value: "alice" },
    });
    fireEvent.change(screen.getByLabelText("密码"), {
      target: { value: "secret1" },
    });
    fireEvent.change(screen.getByLabelText("确认密码"), {
      target: { value: "secret2" },
    });
    fireEvent.click(screen.getByRole("button", { name: "注册并登录" }));
    expect(register).not.toHaveBeenCalled();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "两次输入的密码不一致",
    );
  });

  it("注册成功同样进入大厅", async () => {
    vi.mocked(register).mockResolvedValue(true);
    render(<AuthScreen />);
    fireEvent.click(screen.getByRole("tab", { name: "注册" }));
    fireEvent.change(screen.getByLabelText("用户名"), {
      target: { value: "alice" },
    });
    fireEvent.change(screen.getByLabelText("密码"), {
      target: { value: "secret" },
    });
    fireEvent.change(screen.getByLabelText("确认密码"), {
      target: { value: "secret" },
    });
    fireEvent.click(screen.getByRole("button", { name: "注册并登录" }));
    await waitFor(() =>
      expect(register).toHaveBeenCalledWith("alice", "secret"),
    );
    await waitFor(() => expect(enterLobby).toHaveBeenCalled());
  });
});

describe("AuthScreen 服务器地址", () => {
  it("默认展示本地推导的 origin", () => {
    render(<AuthScreen />);
    expect(screen.getByText("http://localhost:8765")).toBeInTheDocument();
  });

  it("非法地址给出错误且不保存", async () => {
    render(<AuthScreen />);
    fireEvent.click(screen.getByRole("button", { name: "修改" }));
    fireEvent.change(screen.getByLabelText("服务器地址"), {
      target: { value: "not a url" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("地址无效");
    expect(checkSession).not.toHaveBeenCalled();
  });

  it("保存合法地址后重新检查会话", async () => {
    render(<AuthScreen />);
    fireEvent.click(screen.getByRole("button", { name: "修改" }));
    fireEvent.change(screen.getByLabelText("服务器地址"), {
      target: { value: "https://trpg.example.com/" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(checkSession).toHaveBeenCalled());
    expect(localStorage.getItem("trpg-cloud-origin")).toBe(
      "https://trpg.example.com",
    );
  });

  it("Electron 云端页隐藏无效的 renderer 内改服务器入口", () => {
    window.trpgDesktop = {
      getOnlineOrigin: vi.fn(),
      selectLocalMode: vi.fn(),
      selectOnlineMode: vi.fn(),
      returnToLauncher: vi.fn(),
      openEditor: vi.fn(),
    };
    render(<AuthScreen />);
    expect(
      screen.queryByRole("button", { name: "修改" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("http://localhost:8765")).toBeInTheDocument();
  });
});
