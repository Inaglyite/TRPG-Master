import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "./api/client";
import {
  fetchMe,
  login as apiLogin,
  logout as apiLogout,
  registerAccount,
} from "./api/auth";
import {
  createWorld,
  deleteWorld,
  getInvestigatorOptions,
  getRoomInfo,
  listWorlds,
  removeMember,
} from "./api/worlds";
import { listModules } from "./api/modules";
import {
  assignActor,
  checkSession,
  createRoom,
  createSoloWorld,
  deleteCurrentRoom,
  deleteSoloWorld,
  enterLobby,
  enterRoom,
  enterSoloLobby,
  initOnlineSession,
  leaveRoom,
  login,
  logout,
  refreshRoom,
  register,
  refreshWorlds,
  resumeLastRoom,
  startGame,
  toggleReady,
} from "./online";
import { disconnectRoom, roomSend } from "./room-ws";
import {
  initialOnlineState,
  resetOnlineState,
  useOnlineStore,
} from "./state/online-store";

let triggerUnauthorized: (() => void) | null = null;

vi.mock("./api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/client")>();
  return {
    ...actual,
    onUnauthorized: (listener: () => void) => {
      triggerUnauthorized = listener;
      return () => {
        triggerUnauthorized = null;
      };
    },
  };
});

vi.mock("./api/auth", () => ({
  fetchMe: vi.fn(),
  login: vi.fn(),
  registerAccount: vi.fn(),
  logout: vi.fn(),
}));

vi.mock("./api/modules", () => ({
  listModules: vi.fn(),
}));

vi.mock("./api/worlds", () => ({
  acceptInvite: vi.fn(),
  claimInvestigator: vi.fn(),
  createInvite: vi.fn(),
  createWorld: vi.fn(),
  deleteWorld: vi.fn(),
  getInvestigatorOptions: vi.fn(),
  getRoomInfo: vi.fn(),
  listWorlds: vi.fn(),
  releaseInvestigator: vi.fn(),
  removeMember: vi.fn(),
  revokeInvite: vi.fn(),
  updateMember: vi.fn(),
}));

vi.mock("./room-ws", () => ({
  disconnectRoom: vi.fn(),
  roomSend: vi.fn(),
  newActionId: vi.fn(() => "action-1"),
}));

const alice = { id: "u1", username: "alice" };
const bob = { id: "u2", username: "bob" };

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  useOnlineStore.setState({ ...initialOnlineState });
  triggerUnauthorized = null;
  vi.clearAllMocks();
  localStorage.clear();
});

describe("checkSession", () => {
  it("已登录时进入 authenticated", async () => {
    vi.mocked(fetchMe).mockResolvedValue(alice);
    await checkSession();
    const state = useOnlineStore.getState();
    expect(state.authStatus).toBe("authenticated");
    expect(state.user).toEqual(alice);
  });

  it("401 时进入 anonymous 且不报错", async () => {
    vi.mocked(fetchMe).mockRejectedValue(new ApiError("未登录", 401, null));
    await checkSession();
    const state = useOnlineStore.getState();
    expect(state.authStatus).toBe("anonymous");
    expect(state.user).toBeNull();
    expect(state.authError).toBeNull();
  });

  it("网络错误时进入 anonymous 并携带错误提示", async () => {
    vi.mocked(fetchMe).mockRejectedValue(
      new ApiError("无法连接服务器", 0, "network_error"),
    );
    await checkSession();
    const state = useOnlineStore.getState();
    expect(state.authStatus).toBe("anonymous");
    expect(state.authError).toBe("无法连接服务器");
  });
});

describe("login / register", () => {
  it("登录成功写入用户并返回 true", async () => {
    vi.mocked(apiLogin).mockResolvedValue(alice);
    await expect(login("alice", "secret")).resolves.toBe(true);
    const state = useOnlineStore.getState();
    expect(state.authStatus).toBe("authenticated");
    expect(state.authBusy).toBe(false);
    expect(state.authError).toBeNull();
  });

  it("登录失败展示服务端文案并返回 false", async () => {
    vi.mocked(apiLogin).mockRejectedValue(
      new ApiError("无效的用户名或密码", 401, null),
    );
    await expect(login("alice", "wrong")).resolves.toBe(false);
    const state = useOnlineStore.getState();
    expect(state.authStatus).toBe("anonymous");
    expect(state.authBusy).toBe(false);
    expect(state.authError).toBe("无效的用户名或密码");
  });

  it("限流时给出统一提示", async () => {
    vi.mocked(apiLogin).mockRejectedValue(new ApiError("too many", 429, null));
    await login("alice", "secret");
    expect(useOnlineStore.getState().authError).toBe(
      "尝试过于频繁，请稍后再试",
    );
  });

  it("注册成功同样进入 authenticated", async () => {
    vi.mocked(registerAccount).mockResolvedValue(alice);
    await expect(register("alice", "secret")).resolves.toBe(true);
    expect(useOnlineStore.getState().authStatus).toBe("authenticated");
  });
});

describe("logout 与会话过期", () => {
  it("logout 成功后清空账号状态", async () => {
    useOnlineStore.setState({
      authStatus: "authenticated",
      user: alice,
      view: "lobby",
    });
    vi.mocked(apiLogout).mockResolvedValue();
    await expect(logout()).resolves.toBe(true);
    const state = useOnlineStore.getState();
    expect(state.authStatus).toBe("anonymous");
    expect(state.user).toBeNull();
    expect(state.view).toBe("auth");
    expect(disconnectRoom).toHaveBeenCalled();
  });

  it("服务端撤销失败时保留登录态，避免有效 Cookie 静默存活", async () => {
    useOnlineStore.setState({
      authStatus: "authenticated",
      user: alice,
      view: "lobby",
    });
    vi.mocked(apiLogout).mockRejectedValue(new ApiError("boom", 500, null));
    await expect(logout()).resolves.toBe(false);
    const state = useOnlineStore.getState();
    expect(state.authStatus).toBe("authenticated");
    expect(state.user).toEqual(alice);
    expect(state.authBusy).toBe(false);
    expect(state.authError).toBe("boom");
  });

  it("服务端返回 401 时视为 Session 已失效并完成本地退出", async () => {
    useOnlineStore.setState({
      authStatus: "authenticated",
      user: alice,
      view: "lobby",
    });
    vi.mocked(apiLogout).mockRejectedValue(
      new ApiError("unauthorized", 401, null),
    );
    await expect(logout()).resolves.toBe(true);
    expect(useOnlineStore.getState().authStatus).toBe("anonymous");
  });

  it("已认证时 401 广播降级为会话过期", () => {
    useOnlineStore.setState({
      authStatus: "authenticated",
      user: alice,
      view: "room",
    });
    const unsubscribe = initOnlineSession();
    expect(triggerUnauthorized).not.toBeNull();
    triggerUnauthorized!();
    const state = useOnlineStore.getState();
    expect(state.authStatus).toBe("anonymous");
    expect(state.sessionExpired).toBe(true);
    expect(state.view).toBe("auth");
    unsubscribe();
  });

  it("匿名状态下的 401 广播不误报会话过期", () => {
    useOnlineStore.setState({ authStatus: "anonymous" });
    const unsubscribe = initOnlineSession();
    triggerUnauthorized!();
    expect(useOnlineStore.getState().sessionExpired).toBe(false);
    unsubscribe();
  });
});

describe("上次房间恢复", () => {
  it("无权访问旧房间时清除持久化记录，避免每次启动反复失败", async () => {
    localStorage.setItem("trpg-online-world-id", "world-stale");
    vi.mocked(getRoomInfo).mockRejectedValue(
      new ApiError("forbidden", 403, "forbidden"),
    );

    await resumeLastRoom();

    expect(localStorage.getItem("trpg-online-world-id")).toBeNull();
    expect(useOnlineStore.getState().view).toBe("lobby");
    expect(useOnlineStore.getState().activeWorldId).toBeNull();
  });

  it("旧房间返回 world_not_found 时按资源错误回大厅，而不是误报接口未实现", async () => {
    localStorage.setItem("trpg-online-world-id", "world-deleted");
    vi.mocked(getRoomInfo).mockRejectedValue(
      new ApiError("房间不存在", 404, "world_not_found"),
    );

    await resumeLastRoom();

    expect(localStorage.getItem("trpg-online-world-id")).toBeNull();
    expect(useOnlineStore.getState()).toMatchObject({
      view: "lobby",
      activeWorldId: null,
      membersStatus: "error",
      membersError: "房间不存在",
    });
  });
});

describe("异步 REST 归属隔离", () => {
  it("角色降级后的成员刷新保留活动房间并应用 viewer 权限", async () => {
    useOnlineStore.setState({
      authStatus: "authenticated",
      user: alice,
      view: "room",
      activeWorldId: "world-a",
      members: [
        {
          user_id: alice.id,
          username: alice.username,
          role: "player",
          investigator: { id: "investigator-1", character_key: "detective" },
        },
      ],
    });
    vi.mocked(getRoomInfo).mockResolvedValue({
      world_id: "world-a",
      module: "",
      metadata: { name: "房间 A", room_status: "playing" },
      members: [
        {
          user_id: alice.id,
          username: alice.username,
          role: "viewer",
          investigator: null,
        },
      ],
    });

    await refreshRoom();

    expect(useOnlineStore.getState()).toMatchObject({
      authStatus: "authenticated",
      view: "room",
      activeWorldId: "world-a",
      members: [
        {
          user_id: "u1",
          role: "viewer",
          investigator: null,
        },
      ],
    });
    expect(disconnectRoom).not.toHaveBeenCalled();
  });

  it("候选调查员按名字去重（默认库与模组库同名不重复列出）", async () => {
    useOnlineStore.setState({
      authStatus: "authenticated",
      user: alice,
      view: "room",
      activeWorldId: "world-a",
      charactersStatus: "idle",
    });
    vi.mocked(getRoomInfo).mockResolvedValue({
      world_id: "world-a",
      module: "scarlet_docs",
      metadata: { name: "房间 A", room_status: "lobby" },
      members: [
        {
          user_id: alice.id,
          username: alice.username,
          role: "player",
          investigator: null,
        },
      ],
    });
    vi.mocked(getInvestigatorOptions).mockResolvedValue({
      groups: [
        {
          id: "default",
          title: "默认调查员",
          characters: [
            {
              id: "default:霍华德",
              name: "霍华德",
              occupation: "神秘学家",
              source: "default",
            },
            {
              id: "default:黄千陆",
              name: "黄千陆",
              occupation: "侦探",
              source: "default",
            },
          ],
        },
        {
          id: "module",
          title: "模组调查员",
          characters: [
            {
              id: "module:黄千陆",
              name: "黄千陆",
              occupation: "模组定制侦探",
              source: "module",
            },
          ],
        },
      ],
    } as never);

    await refreshRoom();

    const options = useOnlineStore.getState().characterOptions;
    // 同名不重复，且 default 与 module 同名时最终 ref 来自 module（模组定制版优先）
    expect(options.map((option) => option.name)).toEqual(["霍华德", "黄千陆"]);
    expect(options.map((option) => option.id)).toEqual([
      "default:霍华德",
      "module:黄千陆",
    ]);
    expect(options[1].occupation).toBe("模组定制侦探");
  });

  it("换号后丢弃上一账号迟到的世界列表", async () => {
    useOnlineStore.setState({
      authStatus: "authenticated",
      user: alice,
      view: "lobby",
    });
    const pending = deferred<Awaited<ReturnType<typeof listWorlds>>>();
    vi.mocked(listWorlds).mockReturnValue(pending.promise);
    const oldRefresh = refreshWorlds();

    resetOnlineState();
    useOnlineStore.setState({
      authStatus: "authenticated",
      user: bob,
      view: "lobby",
    });
    pending.resolve([
      {
        world_id: "alice-secret-world",
        module: "mansion_of_madness",
        role: "owner",
      },
    ]);
    await oldRefresh;

    expect(useOnlineStore.getState().worlds).toEqual([]);
  });

  it("切到新房间后丢弃旧房间迟到的成员与元数据", async () => {
    useOnlineStore.setState({
      authStatus: "authenticated",
      user: alice,
      view: "lobby",
      privateState: {
        investigatorId: "old",
        pc: { name: "旧角色" },
        clues: {},
        playerNotes: "旧私密笔记",
        playerNotesRevision: 1,
      },
      privateEvents: [{ kind: "clue", clue: { text: "旧秘密" } }],
    });
    const roomA = deferred<Awaited<ReturnType<typeof getRoomInfo>>>();
    const roomB = deferred<Awaited<ReturnType<typeof getRoomInfo>>>();
    vi.mocked(getRoomInfo).mockImplementation((worldId) =>
      worldId === "world-a" ? roomA.promise : roomB.promise,
    );

    const enteringA = enterRoom("world-a");
    expect(useOnlineStore.getState().privateState).toBeNull();
    expect(useOnlineStore.getState().privateEvents).toEqual([]);
    const enteringB = enterRoom("world-b");
    roomB.resolve({
      world_id: "world-b",
      module: "",
      metadata: { name: "房间 B" },
      members: [
        {
          user_id: "u2",
          username: "bob",
          role: "player",
          investigator: null,
        },
      ],
    });
    await enteringB;
    roomA.resolve({
      world_id: "world-a",
      module: "",
      metadata: { name: "房间 A" },
      members: [
        {
          user_id: "secret-a",
          username: "secret-a",
          role: "player",
          investigator: null,
        },
      ],
    });
    await enteringA;

    expect(useOnlineStore.getState()).toMatchObject({
      activeWorldId: "world-b",
      roomMetadata: { name: "房间 B" },
      members: [
        {
          user_id: "u2",
          username: "bob",
        },
      ],
    });
  });

  it("换号后忽略旧账号迟到的创建房间响应", async () => {
    useOnlineStore.setState({
      authStatus: "authenticated",
      user: alice,
      view: "lobby",
    });
    const pending = deferred<Awaited<ReturnType<typeof createWorld>>>();
    vi.mocked(createWorld).mockReturnValue(pending.promise);
    const creating = createRoom("mansion_of_madness", "Alice 私房", 2);

    resetOnlineState();
    useOnlineStore.setState({
      authStatus: "authenticated",
      user: bob,
      view: "lobby",
    });
    pending.resolve({
      world_id: "alice-created-world",
      module: "mansion_of_madness",
    });
    await creating;

    expect(useOnlineStore.getState()).toMatchObject({
      user: bob,
      view: "lobby",
      activeWorldId: null,
    });
  });
});

describe("roomOpen 生命周期", () => {
  it("进入新房间时复位 roomOpen，playing 不再被管理页覆盖", async () => {
    useOnlineStore.setState({
      authStatus: "authenticated",
      user: alice,
      view: "room",
      activeWorldId: "world-old",
      roomStatus: "playing",
      roomOpen: true,
    });
    vi.mocked(getRoomInfo).mockResolvedValue({
      world_id: "world-new",
      module: "",
      metadata: { name: "新房间" },
      members: [],
    });

    await enterRoom("world-new");

    expect(useOnlineStore.getState()).toMatchObject({
      activeWorldId: "world-new",
      roomOpen: false,
    });
  });

  it("返回大厅时复位 roomOpen", async () => {
    useOnlineStore.setState({
      authStatus: "authenticated",
      user: alice,
      view: "room",
      activeWorldId: "world-old",
      roomStatus: "playing",
      roomOpen: true,
    });
    vi.mocked(listWorlds).mockResolvedValue([]);
    vi.mocked(listModules).mockResolvedValue([]);

    await enterLobby();

    expect(useOnlineStore.getState()).toMatchObject({
      view: "lobby",
      activeWorldId: null,
      roomOpen: false,
    });
  });
});

describe("deleteCurrentRoom", () => {
  it("204 后清掉上次房间记忆并回大厅", async () => {
    useOnlineStore.setState({
      authStatus: "authenticated",
      user: alice,
      view: "room",
      activeWorldId: "world-1",
      roomStatus: "lobby",
    });
    localStorage.setItem("trpg-online-world-id", "world-1");
    vi.mocked(deleteWorld).mockResolvedValue(undefined);
    vi.mocked(listWorlds).mockResolvedValue([]);
    vi.mocked(listModules).mockResolvedValue([]);

    const ok = await deleteCurrentRoom();

    expect(ok).toBe(true);
    expect(deleteWorld).toHaveBeenCalledWith("world-1");
    expect(localStorage.getItem("trpg-online-world-id")).toBeNull();
    expect(useOnlineStore.getState()).toMatchObject({
      view: "lobby",
      activeWorldId: null,
      roomBusy: false,
      roomError: null,
    });
  });

  it("409 room_active 时留在房间并提示游戏进行中", async () => {
    useOnlineStore.setState({
      authStatus: "authenticated",
      user: alice,
      view: "room",
      activeWorldId: "world-1",
      roomStatus: "playing",
    });
    vi.mocked(deleteWorld).mockRejectedValue(
      new ApiError("房间进行中", 409, "room_active"),
    );

    const ok = await deleteCurrentRoom();

    expect(ok).toBe(false);
    expect(useOnlineStore.getState()).toMatchObject({
      view: "room",
      activeWorldId: "world-1",
      roomBusy: false,
    });
    expect(useOnlineStore.getState().roomError).toContain("游戏进行中");
  });

  it("请求期间切房后，迟到的 204 不清新房间记忆也不回大厅", async () => {
    useOnlineStore.setState({
      authStatus: "authenticated",
      user: alice,
      view: "room",
      activeWorldId: "world-1",
      roomStatus: "lobby",
    });
    localStorage.setItem("trpg-online-world-id", "world-1");
    const pending = deferred<void>();
    vi.mocked(deleteWorld).mockReturnValue(pending.promise);

    const deleting = deleteCurrentRoom();
    // 用户在删除请求飞行中切到 world-2（或退出）：旧 204 不得生效
    useOnlineStore.setState({ activeWorldId: "world-2" });
    localStorage.setItem("trpg-online-world-id", "world-2");
    pending.resolve(undefined);
    await deleting;

    expect(localStorage.getItem("trpg-online-world-id")).toBe("world-2");
    expect(useOnlineStore.getState().activeWorldId).toBe("world-2");
    expect(listWorlds).not.toHaveBeenCalled();
  });
});

describe("云端单人", () => {
  it("enterSoloLobby 落在 solo 视图并刷新世界列表", async () => {
    useOnlineStore.setState({
      authStatus: "authenticated",
      user: alice,
      view: "room",
      activeWorldId: "world-1",
    });
    vi.mocked(listWorlds).mockResolvedValue([]);
    vi.mocked(listModules).mockResolvedValue([]);

    await enterSoloLobby();

    expect(listWorlds).toHaveBeenCalled();
    expect(useOnlineStore.getState()).toMatchObject({
      view: "solo",
      activeWorldId: null,
      roomOpen: false,
    });
  });

  it("createSoloWorld 以 play_mode=solo 创建并直接进房", async () => {
    useOnlineStore.setState({
      authStatus: "authenticated",
      user: alice,
      view: "solo",
    });
    vi.mocked(createWorld).mockResolvedValue({
      world_id: "world-solo",
      module: "mansion_of_madness",
    });
    vi.mocked(getRoomInfo).mockResolvedValue({
      world_id: "world-solo",
      module: "mansion_of_madness",
      metadata: { name: "雾中宅邸", play_mode: "solo" },
      members: [],
    });

    await createSoloWorld("mansion_of_madness", "雾中宅邸");

    expect(createWorld).toHaveBeenCalledWith("mansion_of_madness", {
      name: "雾中宅邸",
      max_players: 1,
      play_mode: "solo",
    });
    expect(useOnlineStore.getState()).toMatchObject({
      view: "room",
      activeWorldId: "world-solo",
      createBusy: false,
    });
  });

  it("createSoloWorld 名字留空时不提交 name 字段", async () => {
    useOnlineStore.setState({
      authStatus: "authenticated",
      user: alice,
      view: "solo",
    });
    vi.mocked(createWorld).mockResolvedValue({
      world_id: "world-solo",
      module: "mansion_of_madness",
    });
    vi.mocked(getRoomInfo).mockResolvedValue({
      world_id: "world-solo",
      module: "mansion_of_madness",
      members: [],
    });

    await createSoloWorld("mansion_of_madness", "   ");

    expect(createWorld).toHaveBeenCalledWith("mansion_of_madness", {
      max_players: 1,
      play_mode: "solo",
    });
  });

  it("deleteSoloWorld 成功后留在 solo 视图并刷新列表", async () => {
    useOnlineStore.setState({
      authStatus: "authenticated",
      user: alice,
      view: "solo",
    });
    localStorage.setItem("trpg-online-world-id", "world-solo");
    vi.mocked(deleteWorld).mockResolvedValue(undefined);
    vi.mocked(listWorlds).mockResolvedValue([]);
    vi.mocked(listModules).mockResolvedValue([]);

    const ok = await deleteSoloWorld("world-solo");

    expect(ok).toBe(true);
    expect(deleteWorld).toHaveBeenCalledWith("world-solo");
    expect(listWorlds).toHaveBeenCalled();
    expect(localStorage.getItem("trpg-online-world-id")).toBeNull();
    expect(useOnlineStore.getState().view).toBe("solo");
  });

  it("deleteSoloWorld 409 时提示游戏进行中并留在列表", async () => {
    useOnlineStore.setState({
      authStatus: "authenticated",
      user: alice,
      view: "solo",
    });
    vi.mocked(deleteWorld).mockRejectedValue(
      new ApiError("房间进行中", 409, "room_active"),
    );

    const ok = await deleteSoloWorld("world-solo");

    expect(ok).toBe(false);
    expect(useOnlineStore.getState().createError).toContain("游戏进行中");
    expect(useOnlineStore.getState().view).toBe("solo");
  });
});

describe("房间 WS 动作", () => {
  beforeEach(() => {
    useOnlineStore.setState({ activeWorldId: "world-1" });
  });

  it("toggleReady 通过房间 WS 发送 room_ready", async () => {
    await toggleReady(true);
    expect(roomSend).toHaveBeenCalledWith({ type: "room_ready", ready: true });
  });

  it("startGame 通过房间 WS 发送携带 action_id 的 start", async () => {
    await startGame();
    expect(roomSend).toHaveBeenCalledWith({
      type: "start",
      action_id: "action-1",
    });
  });

  it("assignActor 通过房间 WS 发送 actor_assign", async () => {
    await assignActor("u2");
    expect(roomSend).toHaveBeenCalledWith({
      type: "actor_assign",
      user_id: "u2",
    });
  });

  it("无活动房间时不发送", async () => {
    useOnlineStore.setState({ activeWorldId: null });
    await toggleReady(true);
    await startGame();
    await assignActor("u2");
    expect(roomSend).not.toHaveBeenCalled();
  });
});

describe("退出房间", () => {
  beforeEach(() => {
    localStorage.setItem("trpg-online-world-id", "world-1");
    useOnlineStore.setState({
      authStatus: "authenticated",
      user: alice,
      view: "room",
      activeWorldId: "world-1",
    });
  });

  it("服务端确认退出后才清理本地房间并回到大厅", async () => {
    vi.mocked(removeMember).mockResolvedValue(undefined);
    await expect(leaveRoom()).resolves.toBe(true);
    expect(removeMember).toHaveBeenCalledWith("world-1", "u1");
    expect(localStorage.getItem("trpg-online-world-id")).toBeNull();
    expect(useOnlineStore.getState().view).toBe("lobby");
  });

  it("退出失败时留在房间并展示错误，不清理恢复记录", async () => {
    vi.mocked(removeMember).mockRejectedValue(
      new ApiError("房主需要先移交房主", 409, "owner_cannot_leave"),
    );
    await expect(leaveRoom()).resolves.toBe(false);
    expect(useOnlineStore.getState().view).toBe("room");
    expect(useOnlineStore.getState().roomError).toBe("房主需要先移交房主");
    expect(localStorage.getItem("trpg-online-world-id")).toBe("world-1");
  });
});

describe("dismissInvite 撤销语义", () => {
  beforeEach(() => {
    useOnlineStore.setState({
      activeWorldId: "world-1",
      invite: { invite_id: "inv-1", token: "TOKEN-XYZ" },
      roomError: null,
    });
  });

  it("DELETE 成功后才从 UI 移除邀请码", async () => {
    const { revokeInvite } = await import("./api/worlds");
    const { dismissInvite } = await import("./online");
    vi.mocked(revokeInvite).mockResolvedValue(undefined);
    await dismissInvite();
    expect(revokeInvite).toHaveBeenCalledWith("world-1", "inv-1");
    expect(useOnlineStore.getState().invite).toBeNull();
    expect(useOnlineStore.getState().roomError).toBeNull();
  });

  it("DELETE 失败保留邀请码并显示明确错误", async () => {
    const { revokeInvite } = await import("./api/worlds");
    const { dismissInvite } = await import("./online");
    vi.mocked(revokeInvite).mockRejectedValue(
      new ApiError("网络抖动", 0, "network_error"),
    );
    await dismissInvite();
    const state = useOnlineStore.getState();
    expect(state.invite).toMatchObject({
      invite_id: "inv-1",
      token: "TOKEN-XYZ",
    });
    expect(state.roomError).toBe("网络抖动");
  });
});
