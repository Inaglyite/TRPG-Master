import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { enterLobby, refreshRoom } from "./online";
import {
  clearTransientHandouts,
  updateCharPanel,
  updateCluePanel,
} from "./panels";
import {
  connectRoom,
  disconnectRoom,
  injectActionId,
  newActionId,
  roomSend,
  roomWsUrl,
} from "./room-ws";
import { useAppStore } from "./state/app-store";
import { initialOnlineState, useOnlineStore } from "./state/online-store";
import { useStartStore } from "./state/start-store";
import {
  displayWorldHistory,
  handleServerPayload,
  recoverRejectedRoomAction,
  resetRoomGameSession,
  setActiveTransport,
} from "./ws";

vi.mock("./online", () => ({
  enterLobby: vi.fn(),
  refreshRoom: vi.fn(),
}));

vi.mock("./panels", () => ({
  clearTransientHandouts: vi.fn(),
  updateCharPanel: vi.fn(),
  updateCluePanel: vi.fn(),
}));

vi.mock("./ws", () => ({
  displayWorldHistory: vi.fn(),
  handleRoomTurnRecovery: vi.fn(),
  handleServerPayload: vi.fn(),
  markRoomConnectionLost: vi.fn(),
  markRoomConnectionRestored: vi.fn(),
  recoverRejectedRoomAction: vi.fn(),
  resetRoomGameSession: vi.fn(),
  setActiveTransport: vi.fn(),
}));

class FakeWebSocket {
  static readonly OPEN = 1;
  static instances: FakeWebSocket[] = [];

  readonly url: string;
  readyState = 0;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(code = 1000): void {
    this.readyState = 3;
    this.onclose?.({ code });
  }

  open(): void {
    this.readyState = 1;
    this.onopen?.();
  }

  message(data: unknown): void {
    this.onmessage?.({ data });
  }

  static latest(): FakeWebSocket {
    return this.instances[this.instances.length - 1];
  }
}

beforeEach(() => {
  useOnlineStore.setState({ ...initialOnlineState });
  useAppStore.setState({
    character: null,
    clues: {},
    notesText: "",
    notesRevision: 0,
    notesDirty: false,
    handouts: [],
    dialog: null,
    ending: null,
    choices: [],
  });
  useStartStore.setState({ gameStarted: false, gameStarting: false });
  FakeWebSocket.instances = [];
  vi.clearAllMocks();
  vi.stubGlobal("WebSocket", FakeWebSocket);
  localStorage.clear();
});

afterEach(() => {
  disconnectRoom();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("connectRoom", () => {
  it("连接 /ws/room 并携带 world_id，同时接管发送 transport", () => {
    connectRoom("world-1");
    expect(roomWsUrl("world-1")).toBe(
      "ws://localhost:8765/ws/room?world_id=world-1",
    );
    expect(FakeWebSocket.latest().url).toBe(
      "ws://localhost:8765/ws/room?world_id=world-1",
    );
    expect(useOnlineStore.getState().roomConnection).toBe("connecting");
    expect(setActiveTransport).toHaveBeenCalledWith(
      expect.objectContaining({ send: expect.any(Function) }),
    );
    FakeWebSocket.latest().open();
    expect(useOnlineStore.getState().roomConnection).toBe("connected");
  });

  it("重复连接同一房间是幂等的", () => {
    connectRoom("world-1");
    connectRoom("world-1");
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it("断开后归还 transport 并重置房间状态", () => {
    connectRoom("world-1");
    FakeWebSocket.latest().open();
    useAppStore.setState({
      character: { name: "上一位调查员" },
      clues: { investigation: [{ text: "上一位玩家的秘密" }] },
      notesText: "上一位玩家的笔记",
      notesRevision: 9,
      notesDirty: true,
      handouts: [
        {
          id: "secret-handout",
          file: "secret.png",
          label: "上一房间秘密",
          asset_data_uri: "data:image/png;base64,AA==",
          asset_url: "",
          entity_type: "clue",
          entity_id: "secret",
        },
      ],
      dialog: {
        kind: "decision",
        id: "old-dialog",
        options: [],
      },
    });
    useOnlineStore.setState({
      privateEvents: [{ kind: "clue", clue: { text: "私密事件" } }],
    });
    disconnectRoom();
    expect(setActiveTransport).toHaveBeenLastCalledWith(null);
    expect(resetRoomGameSession).toHaveBeenCalled();
    expect(useOnlineStore.getState().roomConnection).toBe("idle");
    expect(useAppStore.getState()).toMatchObject({
      character: null,
      clues: {},
      notesText: "",
      notesRevision: 0,
      notesDirty: false,
      handouts: [],
      dialog: null,
      inputEnabled: false,
    });
    expect(useOnlineStore.getState().privateEvents).toEqual([]);
    expect(clearTransientHandouts).toHaveBeenCalled();
    expect(displayWorldHistory).toHaveBeenLastCalledWith([]);
  });
});

describe("终止性关闭码", () => {
  it("1011 内部故障保留房间恢复记录并自动重连", () => {
    vi.useFakeTimers();
    localStorage.setItem("trpg-online-world-id", "world-1");
    useOnlineStore.setState({
      authStatus: "authenticated",
      user: { id: "u1", username: "alice" },
      view: "room",
      activeWorldId: "world-1",
    });
    connectRoom("world-1");
    FakeWebSocket.latest().open();

    FakeWebSocket.latest().close(1011);

    expect(useOnlineStore.getState()).toMatchObject({
      authStatus: "authenticated",
      view: "room",
      activeWorldId: "world-1",
      roomConnection: "disconnected",
    });
    expect(localStorage.getItem("trpg-online-world-id")).toBe("world-1");
    vi.advanceTimersByTime(1100);
    expect(FakeWebSocket.instances).toHaveLength(2);
  });

  it("1012 服务重启保留房间恢复记录并自动重连", () => {
    vi.useFakeTimers();
    localStorage.setItem("trpg-online-world-id", "world-1");
    useOnlineStore.setState({
      authStatus: "authenticated",
      user: { id: "u1", username: "alice" },
      view: "room",
      activeWorldId: "world-1",
    });
    connectRoom("world-1");
    FakeWebSocket.latest().open();

    FakeWebSocket.latest().close(1012);

    expect(useOnlineStore.getState()).toMatchObject({
      authStatus: "authenticated",
      view: "room",
      activeWorldId: "world-1",
      roomConnection: "disconnected",
    });
    expect(localStorage.getItem("trpg-online-world-id")).toBe("world-1");
    vi.advanceTimersByTime(1100);
    expect(FakeWebSocket.instances).toHaveLength(2);
  });

  it("4401 进入登录页并停止重连", () => {
    vi.useFakeTimers();
    useOnlineStore.setState({
      authStatus: "authenticated",
      user: { id: "u1", username: "alice" },
      view: "room",
      activeWorldId: "world-1",
    });
    connectRoom("world-1");
    FakeWebSocket.latest().open();

    FakeWebSocket.latest().close(4401);

    expect(useOnlineStore.getState()).toMatchObject({
      authStatus: "anonymous",
      view: "auth",
      user: null,
      sessionExpired: true,
    });
    vi.advanceTimersByTime(60_000);
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it("4403 清除房间恢复记录、回大厅并停止重连", async () => {
    vi.useFakeTimers();
    localStorage.setItem("trpg-online-world-id", "world-1");
    useOnlineStore.setState({
      authStatus: "authenticated",
      user: { id: "u1", username: "alice" },
      view: "room",
      activeWorldId: "world-1",
    });
    connectRoom("world-1");
    FakeWebSocket.latest().open();

    FakeWebSocket.latest().close(4403);

    expect(useOnlineStore.getState()).toMatchObject({
      authStatus: "authenticated",
      view: "lobby",
      activeWorldId: null,
    });
    expect(localStorage.getItem("trpg-online-world-id")).toBeNull();
    await vi.waitFor(() => expect(enterLobby).toHaveBeenCalled());
    vi.advanceTimersByTime(60_000);
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it.each([4400, 4404])(
    "%s 房间不可用时清除恢复记录、回大厅并停止重连",
    async (closeCode) => {
      vi.useFakeTimers();
      localStorage.setItem("trpg-online-world-id", "world-1");
      useOnlineStore.setState({
        authStatus: "authenticated",
        user: { id: "u1", username: "alice" },
        view: "room",
        activeWorldId: "world-1",
      });
      connectRoom("world-1");
      FakeWebSocket.latest().open();

      FakeWebSocket.latest().close(closeCode);

      expect(useOnlineStore.getState()).toMatchObject({
        authStatus: "authenticated",
        view: "lobby",
        activeWorldId: null,
      });
      expect(localStorage.getItem("trpg-online-world-id")).toBeNull();
      await vi.waitFor(() => expect(enterLobby).toHaveBeenCalled());
      vi.advanceTimersByTime(60_000);
      expect(FakeWebSocket.instances).toHaveLength(1);
    },
  );

  it("4409 保留登录与活动房间、清除旧玩家私态并以 viewer 重连", async () => {
    vi.useFakeTimers();
    localStorage.setItem("trpg-online-world-id", "world-1");
    useOnlineStore.setState({
      authStatus: "authenticated",
      user: { id: "u1", username: "alice" },
      view: "room",
      activeWorldId: "world-1",
      members: [
        {
          user_id: "u1",
          username: "alice",
          role: "player",
          investigator: null,
        },
      ],
      privateEvents: [{ kind: "clue", clue: { text: "旧玩家秘密" } }],
      privateState: {
        investigatorId: "investigator-1",
        pc: { name: "旧调查员" },
        clues: { private: [{ text: "旧玩家秘密" }] },
        playerNotes: "旧私人笔记",
        playerNotesRevision: 2,
      },
    });
    useAppStore.setState({
      character: { name: "旧调查员" },
      clues: { private: [{ text: "旧玩家秘密" }] },
      notesText: "旧私人笔记",
    });
    vi.mocked(refreshRoom).mockImplementation(async () => {
      useOnlineStore.setState({
        members: [
          {
            user_id: "u1",
            username: "alice",
            role: "viewer",
            investigator: null,
          },
        ],
      });
    });
    connectRoom("world-1");
    FakeWebSocket.latest().open();

    FakeWebSocket.latest().close(4409);
    await vi.waitFor(() => expect(refreshRoom).toHaveBeenCalled());

    expect(useOnlineStore.getState()).toMatchObject({
      authStatus: "authenticated",
      view: "room",
      activeWorldId: "world-1",
      members: [{ user_id: "u1", role: "viewer" }],
      privateEvents: [],
      privateState: null,
    });
    expect(useAppStore.getState()).toMatchObject({
      character: null,
      clues: {},
      notesText: "",
      inputEnabled: false,
    });
    expect(localStorage.getItem("trpg-online-world-id")).toBe("world-1");

    vi.advanceTimersByTime(1100);
    expect(FakeWebSocket.instances).toHaveLength(2);
    FakeWebSocket.latest().open();
    expect(useOnlineStore.getState().roomConnection).toBe("connected");
    expect(useOnlineStore.getState().roomError).toBe(
      "房间角色已更新，正在重新连接……",
    );
    FakeWebSocket.latest().message(
      JSON.stringify({
        type: "room_full_state",
        latest_event_id: 0,
        status: "playing",
        history: [],
        private_state: null,
      }),
    );
    expect(useOnlineStore.getState().roomError).toBeNull();
  });
});

describe("发送队列", () => {
  it("权威快照前的队列最多保留 64 条", () => {
    connectRoom("world-1");
    const ws = FakeWebSocket.latest();
    ws.open();
    for (let index = 0; index < 70; index += 1) {
      roomSend({ type: "player_notes_update", text: String(index) });
    }
    ws.message(
      JSON.stringify({
        type: "room_full_state",
        latest_event_id: 0,
        status: "waiting",
        history: [],
        private_state: null,
      }),
    );
    const queuedFrames = ws.sent.map((frame) => JSON.parse(frame));
    expect(queuedFrames).toHaveLength(64);
    expect(queuedFrames[0].text).toBe("6");
    expect(queuedFrames[63].text).toBe("69");
  });
});

describe("数字事件序号（room_event_id）", () => {
  it("room_state 携带数字序号时按数字回执 room_ack", () => {
    connectRoom("world-1");
    const ws = FakeWebSocket.latest();
    ws.open();
    ws.message(
      JSON.stringify({
        type: "room_state",
        room_event_id: 7,
        status: "waiting",
        owner_user_id: "u1",
        current_actor_user_id: "u2",
        ready_user_ids: ["u1"],
        online_user_ids: ["u1", "u2"],
      }),
    );
    const state = useOnlineStore.getState();
    expect(state.roomStatus).toBe("waiting");
    expect(state.readyUserIds).toEqual(["u1"]);
    expect(state.onlineUserIds).toEqual(["u1", "u2"]);
    expect(ws.sent).toContain(
      JSON.stringify({ type: "room_ack", event_id: 7 }),
    );
  });

  it("重连后按最后的数字序号发起 room_sync", () => {
    vi.useFakeTimers();
    connectRoom("world-1");
    const ws = FakeWebSocket.latest();
    ws.open();
    ws.message(
      JSON.stringify({
        type: "room_state",
        room_event_id: 42,
        status: "active",
        ready_user_ids: [],
        online_user_ids: [],
      }),
    );
    ws.close();
    vi.advanceTimersByTime(1100);
    const reopened = FakeWebSocket.latest();
    reopened.open();
    expect(reopened.sent).toContain(
      JSON.stringify({ type: "room_sync", after_event_id: 42 }),
    );
  });
});

describe("room_full_state", () => {
  it("latest_event_id 重置游标并落地 private_state（真实后端形状）", () => {
    vi.useFakeTimers();
    connectRoom("world-1");
    const ws = FakeWebSocket.latest();
    ws.open();
    // 模拟服务重启：旧游标 999 大于最新序号 5，full_state 直接重置为 5。
    ws.message(
      JSON.stringify({
        type: "room_state",
        room_event_id: 999,
        status: "waiting",
        ready_user_ids: [],
        online_user_ids: [],
      }),
    );
    ws.message(
      JSON.stringify({
        type: "room_full_state",
        latest_event_id: 5,
        private_state: {
          investigator_id: "claim-1",
          pc: { name: "霍华德", hp: 12, san: 60 },
          clues: {
            investigation: [
              {
                id: "c1",
                text: "只有你知道的暗格",
                tier: 2,
                asset: { file: "a.png" },
              },
            ],
            task: [{ id: "c2", text: "私人委托" }],
          },
          player_notes: { text: "别忘了暗格", revision: 7 },
        },
      }),
    );
    const state = useOnlineStore.getState();
    expect(state.privateState).toEqual({
      investigatorId: "claim-1",
      pc: { name: "霍华德", hp: 12, san: 60 },
      clues: {
        investigation: [
          {
            id: "c1",
            text: "只有你知道的暗格",
            tier: 2,
            asset: { file: "a.png" },
            category: "investigation",
          },
        ],
        task: [{ id: "c2", text: "私人委托", category: "task" }],
      },
      playerNotes: "别忘了暗格",
      playerNotesRevision: 7,
    });
    // pc/线索/笔记复用现有面板入口（本地展示），不丢扩展字段。
    expect(updateCharPanel).toHaveBeenCalledWith(
      JSON.stringify({ name: "霍华德", hp: 12, san: 60 }),
    );
    const cluePayload = JSON.parse(vi.mocked(updateCluePanel).mock.calls[0][0]);
    expect(cluePayload.investigation[0]).toMatchObject({
      id: "c1",
      tier: 2,
      asset: { file: "a.png" },
    });
    expect(useAppStore.getState().notesText).toBe("别忘了暗格");
    expect(useAppStore.getState().notesRevision).toBe(7);
    ws.close();
    vi.advanceTimersByTime(1100);
    const reopened = FakeWebSocket.latest();
    reopened.open();
    expect(reopened.sent).toContain(
      JSON.stringify({ type: "room_sync", after_event_id: 5 }),
    );
  });

  it("应用全部房间控制字段、调查员摘要与公共 history", () => {
    connectRoom("world-1");
    const ws = FakeWebSocket.latest();
    ws.open();
    const history = [
      {
        turn_id: "turn_1",
        kind: "completed",
        messages: [{ role: "keeper", text: "公共叙事一" }],
      },
    ];
    ws.message(
      JSON.stringify({
        type: "room_full_state",
        latest_event_id: 20,
        status: "playing",
        owner_user_id: "u1",
        current_actor_user_id: "u2",
        ready_user_ids: ["u1", "u2"],
        online_user_ids: ["u1"],
        investigators: [
          {
            id: "claim-1",
            character_key: "default:霍华德",
            controller_user_id: "u1",
          },
        ],
        active_investigator_id: "claim-2",
        history,
        private_state: {
          investigator_id: "claim-1",
          clues: { investigation: [{ id: "s1", text: "秘密线索" }] },
        },
      }),
    );
    const state = useOnlineStore.getState();
    expect(state.roomStatus).toBe("playing");
    expect(useStartStore.getState().gameStarted).toBe(true);
    expect(state.ownerUserId).toBe("u1");
    expect(state.currentActorUserId).toBe("u2");
    expect(state.readyUserIds).toEqual(["u1", "u2"]);
    expect(state.onlineUserIds).toEqual(["u1"]);
    expect(state.roomInvestigators).toHaveLength(1);
    expect(state.activeInvestigatorId).toBe("claim-2");
    // 公共叙事走既有渲染链，且只收到 history——私有字段不进入公共链路。
    expect(displayWorldHistory).toHaveBeenCalledWith(history);
    const historyArg = vi.mocked(displayWorldHistory).mock.calls[0][0];
    expect(JSON.stringify(historyArg)).not.toContain("秘密线索");
  });

  it("private_state 缺省字段被安全清洗", () => {
    connectRoom("world-1");
    const ws = FakeWebSocket.latest();
    ws.open();
    ws.message(
      JSON.stringify({
        type: "room_full_state",
        latest_event_id: 0,
        private_state: {
          clues: { investigation: [{ no_text: true }, { text: "有效线索" }] },
        },
      }),
    );
    expect(useOnlineStore.getState().privateState).toEqual({
      investigatorId: null,
      pc: null,
      clues: {
        investigation: [{ text: "有效线索", category: "investigation" }],
      },
      playerNotes: "",
      playerNotesRevision: 0,
    });
    expect(updateCharPanel).not.toHaveBeenCalled();
  });

  it("旁观者快照没有 private_state 时清除上一账号的私密面板", () => {
    connectRoom("world-1");
    const ws = FakeWebSocket.latest();
    ws.open();
    useAppStore.setState({
      character: { name: "不应残留" },
      clues: { investigation: [{ text: "不应残留的秘密" }] },
      notesText: "不应残留的笔记",
      notesRevision: 4,
      notesDirty: true,
    });

    ws.message(
      JSON.stringify({
        type: "room_full_state",
        latest_event_id: 1,
        history: [],
        private_state: null,
      }),
    );

    expect(useOnlineStore.getState().privateState).toBeNull();
    expect(useAppStore.getState()).toMatchObject({
      character: null,
      clues: {},
      notesText: "",
      notesRevision: 0,
      notesDirty: false,
    });
  });
});

describe("房间控制事件", () => {
  it("room_state 显式 null 会清除当前行动者", () => {
    connectRoom("world-1");
    const ws = FakeWebSocket.latest();
    ws.open();
    useOnlineStore.setState({
      ownerUserId: "u1",
      currentActorUserId: "u2",
    });
    ws.message(
      JSON.stringify({
        type: "room_state",
        status: "lobby",
        owner_user_id: null,
        current_actor_user_id: null,
        ready_user_ids: [],
        online_user_ids: [],
      }),
    );
    expect(useOnlineStore.getState().ownerUserId).toBeNull();
    expect(useOnlineStore.getState().currentActorUserId).toBeNull();
  });

  it("actor_changed 以 user_id 为权威字段", () => {
    connectRoom("world-1");
    const ws = FakeWebSocket.latest();
    ws.open();
    ws.message(JSON.stringify({ type: "actor_changed", user_id: "u3" }));
    expect(useOnlineStore.getState().currentActorUserId).toBe("u3");
  });

  it("combat_actor_changed 不被协议边界丢弃并更新战斗行动者", () => {
    connectRoom("world-1");
    const ws = FakeWebSocket.latest();
    ws.open();
    ws.message(
      JSON.stringify({
        type: "combat_actor_changed",
        user_id: "u3",
        investigator_id: "inv-3",
        skipped_actor_ids: ["inv-2"],
        round: 4,
        room_event_id: 18,
      }),
    );
    expect(useOnlineStore.getState()).toMatchObject({
      currentActorUserId: "u3",
      activeInvestigatorId: "inv-3",
    });
    expect(ws.sent).toContain(
      JSON.stringify({ type: "room_ack", event_id: 18 }),
    );
  });

  it("investigator_roster 实时刷新公开调查员状态", () => {
    connectRoom("world-1");
    const ws = FakeWebSocket.latest();
    ws.open();
    ws.message(
      JSON.stringify({
        type: "investigator_roster",
        investigators: [{ id: "inv-1", name: "黄千陆", hp: 8, san: 53 }],
        active_investigator_id: "inv-1",
      }),
    );
    expect(useOnlineStore.getState()).toMatchObject({
      roomInvestigators: [{ id: "inv-1", name: "黄千陆", hp: 8, san: 53 }],
      activeInvestigatorId: "inv-1",
    });
  });

  it("member_joined 触发成员刷新", async () => {
    connectRoom("world-1");
    const ws = FakeWebSocket.latest();
    ws.open();
    ws.message(JSON.stringify({ type: "member_joined", user_id: "u2" }));
    await vi.waitFor(() => expect(refreshRoom).toHaveBeenCalled());
  });

  it("member_removed 与 owner_changed 立即刷新成员和房主", async () => {
    connectRoom("world-1");
    const ws = FakeWebSocket.latest();
    ws.open();
    ws.message(JSON.stringify({ type: "member_removed", user_id: "u2" }));
    await vi.waitFor(() => expect(refreshRoom).toHaveBeenCalledOnce());
    vi.mocked(refreshRoom).mockClear();
    ws.message(
      JSON.stringify({
        type: "owner_changed",
        previous_owner_user_id: "u1",
        owner_user_id: "u3",
      }),
    );
    expect(useOnlineStore.getState().ownerUserId).toBe("u3");
    await vi.waitFor(() => expect(refreshRoom).toHaveBeenCalledOnce());
  });

  it("调查员认领与释放事件会让所有客户端刷新成员", async () => {
    connectRoom("world-1");
    const ws = FakeWebSocket.latest();
    ws.open();
    ws.message(
      JSON.stringify({
        type: "investigator_claimed",
        user_id: "u2",
        investigator_id: "inv-2",
        character_key: "default:黄千陆",
      }),
    );
    await vi.waitFor(() => expect(refreshRoom).toHaveBeenCalledOnce());
    vi.mocked(refreshRoom).mockClear();
    ws.message(
      JSON.stringify({
        type: "investigator_released",
        user_id: "u2",
        investigator_id: "inv-2",
        character_key: "default:黄千陆",
      }),
    );
    await vi.waitFor(() => expect(refreshRoom).toHaveBeenCalledOnce());
  });

  it("room_action_rejected 按 code 映射错误文案", () => {
    connectRoom("world-1");
    const ws = FakeWebSocket.latest();
    ws.open();
    ws.message(
      JSON.stringify({ type: "room_action_rejected", code: "room_not_ready" }),
    );
    expect(useOnlineStore.getState().roomError).toBe(
      "还有玩家未选择调查员或未准备",
    );
    ws.message(
      JSON.stringify({
        type: "room_action_rejected",
        code: "owner_cannot_leave",
      }),
    );
    expect(useOnlineStore.getState().roomError).toBe(
      "房主需要先移交房主才能退出房间",
    );
    expect(recoverRejectedRoomAction).toHaveBeenCalledTimes(2);
  });

  it("room_error 展示服务端错误", () => {
    connectRoom("world-1");
    const ws = FakeWebSocket.latest();
    ws.open();
    ws.message(JSON.stringify({ type: "room_error", message: "房间已关闭" }));
    expect(useOnlineStore.getState().roomError).toBe("房间已关闭");
  });

  it("protocol_error 不再被静默丢弃", () => {
    connectRoom("world-1");
    const ws = FakeWebSocket.latest();
    ws.open();
    ws.message(
      JSON.stringify({
        type: "protocol_error",
        code: "invalid_room_ack",
        message: "确认序号无效",
      }),
    );
    expect(useOnlineStore.getState().roomError).toBe("确认序号无效");
  });

  it("room_event_gap 不再发起第二次 room_sync，等待 full_state", async () => {
    connectRoom("world-1");
    const ws = FakeWebSocket.latest();
    ws.open();
    ws.message(
      JSON.stringify({
        type: "room_state",
        room_event_id: 9,
        status: "active",
        ready_user_ids: [],
        online_user_ids: [],
      }),
    );
    ws.sent.length = 0;
    ws.message(JSON.stringify({ type: "room_event_gap" }));
    expect(ws.sent).toHaveLength(0);
    await vi.waitFor(() => expect(refreshRoom).toHaveBeenCalled());
  });
});

describe("private_event", () => {
  it("进入私密收件箱、按数字序号去重并回执", () => {
    connectRoom("world-1");
    const ws = FakeWebSocket.latest();
    ws.open();
    const payload = JSON.stringify({
      type: "private_event",
      kind: "clue",
      clue: {
        id: "c1",
        text: "只有你知道：书房地板下有暗格。",
        category: "investigation",
      },
      room_event_id: 88,
      world_id: "world-1",
    });
    ws.message(payload);
    ws.message(payload);
    const state = useOnlineStore.getState();
    expect(state.privateEvents).toHaveLength(1);
    expect(state.privateEvents[0]).toMatchObject({
      kind: "clue",
      clue: { id: "c1", text: "只有你知道：书房地板下有暗格。" },
      roomEventId: 88,
    });
    expect(ws.sent).toContain(
      JSON.stringify({ type: "room_ack", event_id: 88 }),
    );
  });
});

describe("游戏事件桥接", () => {
  it("非 room_* 事件交给统一 dispatcher", () => {
    connectRoom("world-1");
    const ws = FakeWebSocket.latest();
    ws.open();
    const raw = JSON.stringify({
      type: "narrative_chunk",
      text: "雨声",
      room_event_id: 12,
    });
    ws.message(raw);
    expect(handleServerPayload).toHaveBeenCalledWith(raw);
    // 游戏事件同样参与房间序号跟踪与回执。
    expect(ws.sent).toContain(
      JSON.stringify({ type: "room_ack", event_id: 12 }),
    );
  });

  it("重复或倒序事件只回执，不重复分发且不回退同步游标", () => {
    vi.useFakeTimers();
    connectRoom("world-1");
    const ws = FakeWebSocket.latest();
    ws.open();
    ws.message(
      JSON.stringify({
        type: "narrative_chunk",
        text: "新事件",
        room_event_id: 12,
      }),
    );
    ws.message(
      JSON.stringify({
        type: "narrative_chunk",
        text: "重复事件",
        room_event_id: 12,
      }),
    );
    ws.message(
      JSON.stringify({
        type: "narrative_chunk",
        text: "倒序事件",
        room_event_id: 11,
      }),
    );
    expect(handleServerPayload).toHaveBeenCalledTimes(1);

    ws.close();
    vi.advanceTimersByTime(1100);
    const reopened = FakeWebSocket.latest();
    reopened.open();
    expect(reopened.sent).toContain(
      JSON.stringify({ type: "room_sync", after_event_id: 12 }),
    );
  });

  it("room_full_state 不受重复守卫限制：低序号快照仍被应用并重置游标", () => {
    vi.useFakeTimers();
    connectRoom("world-1");
    const ws = FakeWebSocket.latest();
    ws.open();
    ws.message(
      JSON.stringify({
        type: "narrative_chunk",
        text: "新事件",
        room_event_id: 99,
      }),
    );
    // 服务重启后事件序号回退：携带低序号的 full_state 必须仍然被应用。
    ws.message(
      JSON.stringify({
        type: "room_full_state",
        room_event_id: 3,
        latest_event_id: 3,
        status: "playing",
        ready_user_ids: ["u1"],
        online_user_ids: ["u1"],
        history: [],
        private_state: null,
      }),
    );
    const state = useOnlineStore.getState();
    expect(state.roomStatus).toBe("playing");
    expect(state.readyUserIds).toEqual(["u1"]);
    ws.close();
    vi.advanceTimersByTime(1100);
    const reopened = FakeWebSocket.latest();
    reopened.open();
    expect(reopened.sent).toContain(
      JSON.stringify({ type: "room_sync", after_event_id: 3 }),
    );
  });

  it("roomSend 未连接时排队，重连后按序补发", () => {
    vi.useFakeTimers();
    connectRoom("world-1");
    const ws = FakeWebSocket.latest();
    ws.open();
    ws.close();
    roomSend({ type: "room_ready", ready: true });
    vi.advanceTimersByTime(1100);
    const reopened = FakeWebSocket.latest();
    reopened.open();
    expect(reopened.sent).not.toContain(
      JSON.stringify({ type: "room_ready", ready: true }),
    );
    reopened.message(
      JSON.stringify({
        type: "room_full_state",
        latest_event_id: 0,
        history: [],
        private_state: null,
      }),
    );
    expect(reopened.sent).toContain(
      JSON.stringify({ type: "room_ready", ready: true }),
    );
  });
});

describe("injectActionId", () => {
  it("为 start/action/continue/save_load/turn_rewrite 与房主存档操作注入稳定 action_id", () => {
    for (const type of [
      "start",
      "action",
      "continue",
      "save_load",
      "turn_rewrite",
      "save",
      "save_create",
      "save_delete",
      "save_rename",
      "settle_case",
    ]) {
      const injected = JSON.parse(injectActionId(JSON.stringify({ type })));
      expect(typeof injected.action_id).toBe("string");
      expect(injected.action_id.length).toBeGreaterThan(0);
    }
  });

  it("已有 action_id 保持不变，其他类型不注入", () => {
    const kept = JSON.parse(
      injectActionId(JSON.stringify({ type: "action", action_id: "fixed-1" })),
    );
    expect(kept.action_id).toBe("fixed-1");
    expect(
      JSON.parse(injectActionId(JSON.stringify({ type: "ping" }))),
    ).toEqual({
      type: "ping",
    });
    expect(injectActionId("not json")).toBe("not json");
  });

  it("newActionId 生成非空且互不相同的 id", () => {
    const a = newActionId();
    const b = newActionId();
    expect(a).toBeTruthy();
    expect(a).not.toBe(b);
  });
});
