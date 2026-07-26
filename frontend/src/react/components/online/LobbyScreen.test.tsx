import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  createRoom,
  enterRoom,
  joinWithToken,
  logout,
  refreshWorlds,
} from "../../../online";
import { useAppStore } from "../../../state/app-store";
import {
  initialOnlineState,
  useOnlineStore,
} from "../../../state/online-store";
import { LobbyScreen } from "./LobbyScreen";

vi.mock("../../../online", () => ({
  createRoom: vi.fn(),
  enterRoom: vi.fn(),
  joinWithToken: vi.fn(),
  logout: vi.fn(),
  refreshWorlds: vi.fn(),
}));

const alice = { id: "u1", username: "alice" };
const modules = [
  { id: "mansion_of_madness", title: "疯狂宅邸" },
  { id: "example.whispering-archive@1.0.0", title: "低语档案馆" },
];

beforeEach(() => {
  useOnlineStore.setState({
    ...initialOnlineState,
    authStatus: "authenticated",
    user: alice,
    view: "lobby",
    modules,
    modulesStatus: "ready",
  });
  useAppStore.setState({ mode: "online" });
  vi.clearAllMocks();
});

describe("LobbyScreen 房间列表", () => {
  it("加载中显示提示", () => {
    useOnlineStore.setState({ worldsStatus: "loading", worlds: [] });
    render(<LobbyScreen />);
    expect(screen.getByRole("status")).toHaveTextContent("正在读取房间列表");
  });

  it("失败时显示错误与重试", () => {
    useOnlineStore.setState({
      worldsStatus: "error",
      worldsError: "无法连接服务器",
    });
    render(<LobbyScreen />);
    expect(screen.getByRole("alert")).toHaveTextContent("无法连接服务器");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(refreshWorlds).toHaveBeenCalled();
  });

  it("空列表显示引导文案", () => {
    useOnlineStore.setState({ worldsStatus: "ready", worlds: [] });
    render(<LobbyScreen />);
    expect(screen.getByText(/还没有房间/)).toBeInTheDocument();
  });

  it("渲染房间卡片并进入房间", () => {
    useOnlineStore.setState({
      worldsStatus: "ready",
      worlds: [
        {
          world_id: "world-1",
          module: "mansion_of_madness",
          role: "owner",
          member_count: 3,
          metadata: {
            name: "周五调查夜",
            max_players: 4,
            room_status: "waiting",
          },
          updated_at: "2026-07-22T10:00:00+00:00",
        },
      ],
    });
    render(<LobbyScreen />);
    const card = screen.getByRole("button", { name: /周五调查夜/ });
    expect(card).toHaveTextContent("房主");
    expect(card).toHaveTextContent("3/4 人");
    expect(card).toHaveTextContent("waiting");
    fireEvent.click(card);
    expect(enterRoom).toHaveBeenCalledWith("world-1");
  });

  it("无房间名称时回退模组标题", () => {
    useOnlineStore.setState({
      worldsStatus: "ready",
      worlds: [
        {
          world_id: "world-1",
          module: "mansion_of_madness",
          role: "player",
          metadata: {},
        },
      ],
    });
    render(<LobbyScreen />);
    expect(
      screen.getByRole("button", { name: /疯狂宅邸/ }),
    ).toBeInTheDocument();
  });
});

describe("LobbyScreen 创建与加入", () => {
  it("创建房间使用选中的模组、名称与人数上限", () => {
    render(<LobbyScreen />);
    fireEvent.change(screen.getByLabelText("房间名称"), {
      target: { value: "周末跑团" },
    });
    fireEvent.change(screen.getByLabelText("选择模组"), {
      target: { value: "example.whispering-archive@1.0.0" },
    });
    fireEvent.change(screen.getByLabelText("人数上限"), {
      target: { value: "2" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建房间" }));
    expect(createRoom).toHaveBeenCalledWith(
      "example.whispering-archive@1.0.0",
      "周末跑团",
      2,
    );
  });

  it("创建失败展示错误", () => {
    useOnlineStore.setState({ createError: "创建房间失败，请重试" });
    render(<LobbyScreen />);
    expect(screen.getByRole("alert")).toHaveTextContent("创建房间失败");
  });

  it("空邀请码时加入按钮禁用", () => {
    render(<LobbyScreen />);
    expect(screen.getByRole("button", { name: "加入房间" })).toBeDisabled();
  });

  it("输入邀请码后加入房间", () => {
    render(<LobbyScreen />);
    fireEvent.change(screen.getByLabelText("邀请码"), {
      target: { value: "ABC-123" },
    });
    fireEvent.click(screen.getByRole("button", { name: "加入房间" }));
    expect(joinWithToken).toHaveBeenCalledWith("ABC-123");
  });

  it("退出登录调用 logout", () => {
    render(<LobbyScreen />);
    fireEvent.click(screen.getByRole("button", { name: "退出登录" }));
    expect(logout).toHaveBeenCalled();
  });
});
