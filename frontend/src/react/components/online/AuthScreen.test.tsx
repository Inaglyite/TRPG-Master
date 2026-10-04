import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
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
  it("network failures use the illustrated folder without hiding recovery actions in a bitmap", () => {
    useOnlineStore.setState({
      authError: "无法连接服务器",
      authErrorCode: "network_error",
    });
    render(<AuthScreen />);
    expect(screen.getByRole("region", { name: "无法连接服务器" })).toHaveClass(
      "archive-folder-panel--portrait",
    );
    expect(screen.getByRole("button", { name: "重新检查" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "修改服务器" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "返回模式选择" })).toBeEnabled();
    expect(
      screen.queryByRole("button", { name: "登录" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("无法连接服务器");
  });

  it("invalid credentials are not misrepresented as a network failure", () => {
    useOnlineStore.setState({
      authError: "无效的用户名或密码",
      authErrorCode: "invalid_credentials",
    });
    render(<AuthScreen />);
    expect(
      screen.queryByRole("heading", { name: "无法连接服务器" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "登录" })).toBeEnabled();
    expect(screen.getByRole("alert")).toHaveTextContent("无效的用户名或密码");
  });

  it("editing from recovery focuses the address and hides irrelevant login controls", () => {
    useOnlineStore.setState({
      authError: "服务器未及时响应",
      authErrorCode: "request_timeout",
    });
    render(<AuthScreen />);
    fireEvent.click(screen.getByRole("button", { name: "修改服务器" }));
    expect(screen.getByLabelText("服务器地址")).toHaveFocus();
    expect(
      screen.queryByRole("button", { name: "登录" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "保存并重新检查" }),
    ).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(
      screen.getByRole("heading", { name: "无法连接服务器" }),
    ).toBeInTheDocument();
  });
  it("checking 状态显示加载提示", () => {
    useOnlineStore.setState({ authStatus: "checking" });
    render(<AuthScreen />);
    expect(screen.getByRole("status")).toHaveTextContent("正在检查登录状态");
    expect(
      screen.getByRole("button", { name: /返回模式选择/ }),
    ).toBeInTheDocument();
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
  it("cancel preserves typed credentials and makes no session request", () => {
    render(<AuthScreen />);
    fireEvent.change(screen.getByLabelText("用户名"), {
      target: { value: "alice" },
    });
    fireEvent.change(screen.getByLabelText("密码"), {
      target: { value: "private" },
    });
    fireEvent.click(screen.getByRole("button", { name: "修改服务器" }));
    fireEvent.change(screen.getByLabelText("服务器地址"), {
      target: { value: "https://second.example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.getByLabelText("密码")).toHaveValue("private");
    expect(localStorage.getItem("trpg-cloud-origin")).toBeNull();
    expect(checkSession).not.toHaveBeenCalled();
  });

  it("a storage failure remains in the editor without pretending to reconnect", () => {
    render(<AuthScreen />);
    fireEvent.click(screen.getByRole("button", { name: "修改服务器" }));
    fireEvent.change(screen.getByLabelText("服务器地址"), {
      target: { value: "https://second.example.com" },
    });
    const spy = vi
      .spyOn(Storage.prototype, "setItem")
      .mockImplementation(() => {
        throw new Error("storage disabled");
      });
    try {
      fireEvent.click(screen.getByRole("button", { name: "保存并重新检查" }));
      expect(screen.getByRole("alert")).toHaveTextContent("未能保存");
      expect(screen.getByLabelText("服务器地址")).toBeInTheDocument();
      expect(checkSession).not.toHaveBeenCalled();
    } finally {
      spy.mockRestore();
    }
  });

  it("rechecking a valid session uses the selected solo entry, not a stale room", async () => {
    useOnlineStore.setState({
      pendingIntent: "solo",
      authError: "无法连接服务器",
    });
    vi.mocked(checkSession).mockResolvedValue(true);
    render(<AuthScreen />);
    fireEvent.click(screen.getByRole("button", { name: "重新检查" }));
    await waitFor(() => expect(enterSoloLobby).toHaveBeenCalledTimes(1));
    expect(enterLobby).not.toHaveBeenCalled();
    expect(login).not.toHaveBeenCalled();
  });

  it("failed recheck does not navigate and duplicate clicks do not start two checks", async () => {
    useOnlineStore.setState({ authError: "无法连接服务器" });
    let finish!: (result: boolean) => void;
    vi.mocked(checkSession).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    render(<AuthScreen />);
    const retry = screen.getByRole("button", { name: "重新检查" });
    fireEvent.click(retry);
    fireEvent.click(retry);
    expect(checkSession).toHaveBeenCalledTimes(1);
    await act(async () => {
      finish(false);
    });
    expect(enterLobby).not.toHaveBeenCalled();
    expect(enterSoloLobby).not.toHaveBeenCalled();
  });

  it("changing server discards typed credentials rather than carrying them to another destination", () => {
    render(<AuthScreen />);
    fireEvent.change(screen.getByLabelText("用户名"), {
      target: { value: "private-user" },
    });
    fireEvent.change(screen.getByLabelText("密码"), {
      target: { value: "private-password" },
    });
    fireEvent.click(screen.getByRole("button", { name: "修改服务器" }));
    fireEvent.change(screen.getByLabelText("服务器地址"), {
      target: { value: "https://second.example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^保存/ }));
    expect(screen.getByLabelText("用户名")).toHaveValue("");
    expect(screen.getByLabelText("密码")).toHaveValue("");
    expect(login).not.toHaveBeenCalled();
  });
  it("默认展示本地推导的 origin", () => {
    render(<AuthScreen />);
    expect(screen.getByText("http://localhost:8765")).toBeInTheDocument();
  });

  it("非法地址给出错误且不保存", async () => {
    render(<AuthScreen />);
    fireEvent.click(screen.getByRole("button", { name: "修改服务器" }));
    fireEvent.change(screen.getByLabelText("服务器地址"), {
      target: { value: "not a url" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存并重新检查" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("地址无效");
    expect(checkSession).not.toHaveBeenCalled();
  });

  it("保存合法地址后重新检查会话", async () => {
    render(<AuthScreen />);
    fireEvent.click(screen.getByRole("button", { name: "修改服务器" }));
    fireEvent.change(screen.getByLabelText("服务器地址"), {
      target: { value: "https://trpg.example.com/" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存并重新检查" }));
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
      screen.queryByRole("button", { name: "修改服务器" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("http://localhost:8765")).toBeInTheDocument();
  });
});
