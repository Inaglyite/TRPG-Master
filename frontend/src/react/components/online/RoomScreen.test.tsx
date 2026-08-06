import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  assignActor,
  changeMemberRole,
  claimByKey,
  deleteCurrentRoom,
  enterLobby,
  handOverOwnership,
  kickMember,
  leaveRoom,
  newInvite,
  releaseClaim,
  revokeInviteById,
  startGame,
  toggleReady,
} from "../../../online";
import { useMessageStore } from "../../../state/message-store";
import {
  initialOnlineState,
  useOnlineStore,
} from "../../../state/online-store";
import { RoomScreen } from "./RoomScreen";

vi.mock("../../../online", () => ({
  assignActor: vi.fn(),
  changeMemberRole: vi.fn(),
  claimByKey: vi.fn(),
  deleteCurrentRoom: vi.fn(),
  dismissInvite: vi.fn(),
  enterLobby: vi.fn(),
  handOverOwnership: vi.fn(),
  kickMember: vi.fn(),
  leaveRoom: vi.fn(),
  newInvite: vi.fn(),
  refreshRoom: vi.fn(),
  releaseClaim: vi.fn(),
  revokeInviteById: vi.fn(),
  startGame: vi.fn(),
  toggleReady: vi.fn(),
}));

const alice = { id: "u1", username: "alice" };

function setupRoom(patch: Record<string, unknown> = {}) {
  useOnlineStore.setState({
    ...initialOnlineState,
    authStatus: "authenticated",
    user: alice,
    view: "room",
    activeWorldId: "world-1",
    roomModule: "mansion_of_madness",
    roomMetadata: { name: "周五调查夜" },
    roomConnection: "connected",
    roomStatus: "lobby",
    worlds: [
      { world_id: "world-1", module: "mansion_of_madness", role: "owner" },
    ],
    modules: [{ id: "mansion_of_madness", title: "疯狂宅邸" }],
    modulesStatus: "ready",
    membersStatus: "ready",
    members: [
      { user_id: "u1", username: "alice", role: "owner", investigator: null },
      {
        user_id: "u2",
        username: "bob",
        role: "player",
        investigator: {
          id: "claim-2",
          character_key: "default:黄千陆",
          status: "claimed",
        },
      },
    ],
    onlineUserIds: ["u1"],
    readyUserIds: ["u2"],
    ...patch,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("RoomScreen 连接状态", () => {
  it("显示连接状态徽章", () => {
    setupRoom();
    render(<RoomScreen />);
    expect(screen.getByRole("status")).toHaveTextContent("已连接");
  });

  it("断线时提示重连中", () => {
    setupRoom({ roomConnection: "disconnected" });
    render(<RoomScreen />);
    expect(screen.getByRole("status")).toHaveTextContent("已断开，重连中");
  });
});

describe("RoomScreen 成员列表", () => {
  it("渲染角色、在线、准备与调查员徽章", () => {
    setupRoom();
    render(<RoomScreen />);
    const aliceRow = screen.getByText("alice").closest("li")!;
    expect(aliceRow).toHaveTextContent("（我）");
    expect(aliceRow).toHaveTextContent("房主");
    expect(aliceRow).toHaveTextContent("在线");
    expect(aliceRow).toHaveTextContent("未准备");
    const bobRow = screen.getByText("bob").closest("li")!;
    expect(bobRow).toHaveTextContent("离线");
    expect(bobRow).toHaveTextContent("已准备");
    expect(bobRow).toHaveTextContent("黄千陆");
  });

  it("当前行动者显示行动中徽章", () => {
    setupRoom({ currentActorUserId: "u2" });
    render(<RoomScreen />);
    expect(screen.getByText("bob").closest("li")).toHaveTextContent("行动中");
  });

  it("成员接口不可用时显示提示", () => {
    setupRoom({ membersStatus: "unsupported", members: [] });
    render(<RoomScreen />);
    expect(screen.getByText(/成员列表接口暂不可用/)).toBeInTheDocument();
  });
});

describe("RoomScreen 房主管理", () => {
  it("房主可将成员设为旁观/玩家", () => {
    setupRoom();
    render(<RoomScreen />);
    fireEvent.click(screen.getByRole("button", { name: "设为旁观" }));
    expect(changeMemberRole).toHaveBeenCalledWith("u2", "viewer");
  });

  it("普通玩家和旁观者都看不到邀请管理，旁观者也不能选调查员", () => {
    setupRoom({
      members: [
        {
          user_id: "u1",
          username: "alice",
          role: "viewer",
          investigator: null,
        },
        { user_id: "u2", username: "bob", role: "owner", investigator: null },
      ],
      characterOptions: [
        { id: "default:霍华德", name: "霍华德", occupation: "教授" },
      ],
      charactersStatus: "ready",
    });
    render(<RoomScreen />);
    expect(
      screen.queryByRole("heading", { name: "邀请" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "调查员" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "生成邀请码" }),
    ).not.toBeInTheDocument();
  });

  it("房主移除成员需要二次确认", () => {
    setupRoom();
    render(<RoomScreen />);
    fireEvent.click(screen.getByRole("button", { name: "移除" }));
    expect(kickMember).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认移除" }));
    expect(kickMember).toHaveBeenCalledWith("u2");
  });

  it("房主可为其他玩家指定行动者", () => {
    setupRoom();
    render(<RoomScreen />);
    fireEvent.click(screen.getByRole("button", { name: "指定行动" }));
    expect(assignActor).toHaveBeenCalledWith("u2");
  });

  it("已是行动者的成员不再显示可点的指定行动", () => {
    setupRoom({ currentActorUserId: "u2" });
    render(<RoomScreen />);
    expect(screen.getByRole("button", { name: "指定行动" })).toBeDisabled();
  });

  it("非房主不显示管理、指定行动与开始按钮", () => {
    setupRoom({
      members: [
        {
          user_id: "u1",
          username: "alice",
          role: "player",
          investigator: null,
        },
        { user_id: "u2", username: "bob", role: "owner", investigator: null },
      ],
    });
    render(<RoomScreen />);
    expect(
      screen.queryByRole("button", { name: "设为旁观" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "移除" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "指定行动" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "开始游戏" }),
    ).not.toBeInTheDocument();
  });
});

describe("RoomScreen 准备与开始", () => {
  it("准备按钮按 readyUserIds 切换文案", () => {
    setupRoom();
    render(<RoomScreen />);
    fireEvent.click(screen.getByRole("button", { name: "准备" }));
    expect(toggleReady).toHaveBeenCalledWith(true);
  });

  it("游戏开始后不再显示准备和再次开局按钮", () => {
    setupRoom({ roomStatus: "playing" });
    render(<RoomScreen />);
    expect(
      screen.queryByRole("button", { name: "准备" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "开始游戏" }),
    ).not.toBeInTheDocument();
  });

  it("已准备时显示取消准备", () => {
    setupRoom({ readyUserIds: ["u1", "u2"] });
    render(<RoomScreen />);
    fireEvent.click(screen.getByRole("button", { name: "取消准备" }));
    expect(toggleReady).toHaveBeenCalledWith(false);
  });

  it("列出全部开局缺项（离线/未准备/未选角）", () => {
    setupRoom();
    render(<RoomScreen />);
    expect(screen.getByRole("button", { name: "开始游戏" })).toBeDisabled();
    const hint = screen.getByText(/尚不能开始/);
    expect(hint).toHaveTextContent("alice 未准备");
    expect(hint).toHaveTextContent("alice 未选择调查员");
    expect(hint).toHaveTextContent("bob 离线");
  });

  it("房间连接断开时禁用并提示", () => {
    setupRoom({
      roomConnection: "disconnected",
      readyUserIds: ["u1", "u2"],
      onlineUserIds: ["u1", "u2"],
      members: [
        {
          user_id: "u1",
          username: "alice",
          role: "owner",
          investigator: { id: "claim-1", character_key: "default:霍华德" },
        },
        {
          user_id: "u2",
          username: "bob",
          role: "player",
          investigator: { id: "claim-2", character_key: "default:黄千陆" },
        },
      ],
    });
    render(<RoomScreen />);
    expect(screen.getByRole("button", { name: "开始游戏" })).toBeDisabled();
    expect(screen.getByText(/尚不能开始/)).toHaveTextContent("房间连接已断开");
  });

  it("全员在线、准备且选角后房主可以开始", () => {
    setupRoom({
      readyUserIds: ["u1", "u2"],
      onlineUserIds: ["u1", "u2"],
      members: [
        {
          user_id: "u1",
          username: "alice",
          role: "owner",
          investigator: { id: "claim-1", character_key: "default:霍华德" },
        },
        {
          user_id: "u2",
          username: "bob",
          role: "player",
          investigator: { id: "claim-2", character_key: "default:黄千陆" },
        },
      ],
    });
    render(<RoomScreen />);
    fireEvent.click(screen.getByRole("button", { name: "开始游戏" }));
    expect(startGame).toHaveBeenCalled();
  });
});

describe("RoomScreen 调查员认领", () => {
  const options = [
    { id: "default:霍华德", name: "霍华德", occupation: "神秘学家" },
    { id: "default:黄千陆", name: "黄千陆", occupation: "侦探/警方顾问" },
  ];

  it("可用角色可认领，被占用角色禁用", () => {
    setupRoom({ charactersStatus: "ready", characterOptions: options });
    render(<RoomScreen />);
    fireEvent.click(screen.getByRole("button", { name: "选择" }));
    expect(claimByKey).toHaveBeenCalledWith("default:霍华德");
    expect(screen.getByRole("button", { name: "已被占用" })).toBeDisabled();
  });

  it("自己已认领时显示释放", () => {
    setupRoom({
      charactersStatus: "ready",
      characterOptions: options,
      members: [
        {
          user_id: "u1",
          username: "alice",
          role: "owner",
          investigator: { id: "claim-1", character_key: "default:霍华德" },
        },
      ],
    });
    render(<RoomScreen />);
    fireEvent.click(screen.getByRole("button", { name: "释放" }));
    expect(releaseClaim).toHaveBeenCalledWith("claim-1");
  });

  it("选项接口不可用时显示提示", () => {
    setupRoom({ charactersStatus: "unsupported", characterOptions: [] });
    render(<RoomScreen />);
    expect(screen.getByText(/调查员选项接口暂不可用/)).toBeInTheDocument();
  });
});

describe("RoomScreen 邀请、退出与标题", () => {
  it("按选择的角色/有效期/次数生成邀请码", () => {
    setupRoom();
    render(<RoomScreen />);
    fireEvent.change(screen.getByLabelText("邀请角色"), {
      target: { value: "viewer" },
    });
    fireEvent.change(screen.getByLabelText("有效期（小时）"), {
      target: { value: "24" },
    });
    fireEvent.change(screen.getByLabelText("使用次数"), {
      target: { value: "3" },
    });
    fireEvent.click(screen.getByRole("button", { name: "生成邀请码" }));
    expect(newInvite).toHaveBeenCalledWith({
      role: "viewer",
      expires_in_hours: 24,
      max_uses: 3,
    });
  });

  it("展示邀请码并可复制", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    setupRoom({
      invite: {
        invite_id: "inv-1",
        token: "INV-XYZ-123",
        expires_at: "2026-07-25T10:00:00+00:00",
        max_uses: 5,
      },
    });
    render(<RoomScreen />);
    expect(screen.getByText("INV-XYZ-123")).toBeInTheDocument();
    expect(screen.getByText(/最多使用 5 次/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "复制邀请码" }));
    expect(writeText).toHaveBeenCalledWith("INV-XYZ-123");
    expect(
      await screen.findByRole("button", { name: "已复制" }),
    ).toBeInTheDocument();
  });

  it("退出房间需要二次确认", () => {
    setupRoom({
      members: [
        {
          user_id: "u1",
          username: "alice",
          role: "player",
          investigator: null,
        },
        { user_id: "u2", username: "bob", role: "owner", investigator: null },
      ],
    });
    render(<RoomScreen />);
    fireEvent.click(screen.getByRole("button", { name: "退出房间" }));
    expect(leaveRoom).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认退出房间" }));
    expect(leaveRoom).toHaveBeenCalled();
  });

  it("优先显示房间名称", () => {
    setupRoom();
    render(<RoomScreen />);
    expect(
      screen.getByRole("heading", { name: "周五调查夜" }),
    ).toBeInTheDocument();
  });

  it("无房间名称时回退模组标题", () => {
    setupRoom({ roomMetadata: {} });
    render(<RoomScreen />);
    expect(
      screen.getByRole("heading", { name: "疯狂宅邸" }),
    ).toBeInTheDocument();
  });
});

describe("RoomScreen 邀请列表与房主移交", () => {
  it("展示邀请元数据并可撤销", () => {
    setupRoom({
      invites: [
        {
          invite_id: "inv1",
          role: "player",
          status: "active",
          used_count: 1,
          max_uses: 5,
        },
      ],
    });
    render(<RoomScreen />);
    expect(screen.getByText(/inv1/)).toBeInTheDocument();
    expect(screen.getByText("active")).toBeInTheDocument();
    expect(screen.getByText(/已用 1\/5 次/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "撤销" }));
    expect(revokeInviteById).toHaveBeenCalledWith("inv1");
  });

  it("已撤销/过期的邀请不再显示撤销按钮", () => {
    setupRoom({
      invites: [
        { invite_id: "invite-old-999", role: "player", status: "expired" },
      ],
    });
    render(<RoomScreen />);
    expect(screen.getByText("expired")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "撤销" }),
    ).not.toBeInTheDocument();
  });

  it("房主移交需要二次确认", () => {
    setupRoom();
    render(<RoomScreen />);
    fireEvent.click(screen.getByRole("button", { name: "移交" }));
    expect(handOverOwnership).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认移交房主" }));
    expect(handOverOwnership).toHaveBeenCalledWith("u2");
  });
});

describe("RoomScreen 房间处置（删除）", () => {
  it("房主在 lobby 删除房间需要二次确认", () => {
    setupRoom();
    render(<RoomScreen />);
    fireEvent.click(screen.getByRole("button", { name: "删除房间" }));
    expect(deleteCurrentRoom).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认删除房间" }));
    expect(deleteCurrentRoom).toHaveBeenCalled();
  });

  it("取消删除不调用删除并收起确认组", () => {
    setupRoom();
    render(<RoomScreen />);
    fireEvent.click(screen.getByRole("button", { name: "删除房间" }));
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(deleteCurrentRoom).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("button", { name: "确认删除房间" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "删除房间" }),
    ).toBeInTheDocument();
  });

  it("非房主看不到房间处置区", () => {
    setupRoom({
      members: [
        {
          user_id: "u1",
          username: "alice",
          role: "player",
          investigator: null,
        },
        { user_id: "u2", username: "bob", role: "owner", investigator: null },
      ],
    });
    render(<RoomScreen />);
    expect(
      screen.queryByRole("heading", { name: "房间处置" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "删除房间" }),
    ).not.toBeInTheDocument();
  });

  it("游戏进行中删除按钮禁用并给出说明", () => {
    setupRoom({ roomStatus: "playing" });
    render(<RoomScreen />);
    expect(screen.getByRole("button", { name: "删除房间" })).toBeDisabled();
    expect(screen.getByText(/游戏进行中无法删除房间/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "删除房间" }));
    expect(deleteCurrentRoom).not.toHaveBeenCalled();
  });

  it("lobby 的删除说明提示逻辑归档且不可恢复", () => {
    setupRoom();
    render(<RoomScreen />);
    expect(screen.getByText(/逻辑归档/)).toBeInTheDocument();
  });
});

describe("RoomScreen 房主退出限制", () => {
  it("房间内有其他成员时房主不能退出", () => {
    setupRoom();
    render(<RoomScreen />);
    expect(screen.getByRole("button", { name: "退出房间" })).toBeDisabled();
    expect(screen.getByText(/服务端不允许房主直接退出/)).toBeInTheDocument();
  });

  it("房主即使独处也不能直接退出（关闭房间另做）", () => {
    setupRoom({
      members: [
        { user_id: "u1", username: "alice", role: "owner", investigator: null },
      ],
    });
    render(<RoomScreen />);
    expect(screen.getByRole("button", { name: "退出房间" })).toBeDisabled();
    expect(screen.getByText(/服务端不允许房主直接退出/)).toBeInTheDocument();
  });

  it("普通成员不受移交规则限制", () => {
    setupRoom({
      members: [
        {
          user_id: "u1",
          username: "alice",
          role: "player",
          investigator: null,
        },
        { user_id: "u2", username: "bob", role: "owner", investigator: null },
      ],
    });
    render(<RoomScreen />);
    expect(screen.getByRole("button", { name: "退出房间" })).toBeEnabled();
  });
});

describe("RoomScreen 私密线索", () => {
  it("以“仅你可见”渲染，且不进入公共消息历史", () => {
    setupRoom({
      privateEvents: [
        {
          kind: "clue",
          clue: {
            id: "c1",
            text: "只有你知道：书房地板下有暗格。",
            category: "investigation",
          },
          roomEventId: 88,
        },
      ],
    });
    render(<RoomScreen />);
    expect(
      screen.getByText("只有你知道：书房地板下有暗格。"),
    ).toBeInTheDocument();
    expect(screen.getByText("仅你可见")).toBeInTheDocument();
    expect(useMessageStore.getState().messages).toHaveLength(0);
  });

  it("渲染 room_full_state 的 private_state 线索", () => {
    setupRoom({
      privateState: {
        investigatorId: "claim-1",
        pc: null,
        clues: {
          investigation: [
            {
              id: "c9",
              text: "私人线索：怀表里的字条",
              visibility: "private",
            },
          ],
        },
        playerNotes: "",
        playerNotesRevision: 0,
      },
    });
    render(<RoomScreen />);
    expect(screen.getByText("私人线索：怀表里的字条")).toBeInTheDocument();
    expect(screen.getByText("仅你可见")).toBeInTheDocument();
  });

  it("没有私密事件时不渲染该区域", () => {
    setupRoom();
    render(<RoomScreen />);
    expect(screen.queryByText("私密线索")).not.toBeInTheDocument();
  });

  it("公开线索只进入线索面板，不在房间里误标为仅你可见", () => {
    setupRoom({
      privateState: {
        investigatorId: "claim-1",
        pc: null,
        clues: {
          investigation: [
            {
              id: "public-1",
              text: "所有人都看到的脚印",
              visibility: "public",
            },
          ],
        },
        playerNotes: "",
        playerNotesRevision: 0,
      },
    });
    render(<RoomScreen />);
    expect(screen.queryByText("所有人都看到的脚印")).not.toBeInTheDocument();
    expect(screen.queryByText("仅你可见")).not.toBeInTheDocument();
  });
});

describe("RoomScreen 游戏进行中锁定调查员换绑", () => {
  const claimOptions = [
    { id: "default:霍华德", name: "霍华德" },
    { id: "default:黄千陆", name: "黄千陆" },
  ];

  function setupClaimRoom(roomStatus: string) {
    setupRoom({
      roomStatus,
      charactersStatus: "ready",
      characterOptions: claimOptions,
      members: [
        {
          user_id: "u1",
          username: "alice",
          role: "owner",
          investigator: { id: "claim-1", character_key: "default:霍华德" },
        },
      ],
    });
  }

  it("playing 时选择与释放按钮均禁用", () => {
    setupClaimRoom("playing");
    render(<RoomScreen />);
    expect(screen.getByRole("button", { name: "释放" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "选择" })).toBeDisabled();
  });

  it("lobby 时按钮保持可用", () => {
    setupClaimRoom("lobby");
    render(<RoomScreen />);
    expect(screen.getByRole("button", { name: "释放" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "选择" })).toBeEnabled();
  });
});
