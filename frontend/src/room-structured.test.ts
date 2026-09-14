/**
 * 云端多人 `/ws/room` 路径的结构化路由与 outgoing payload。
 *
 * 这一层不 mock `ws.ts`：用真实 `handleServerPayload` + 真实结构化传输，
 * 只替换 WebSocket 本体。验证的是：
 * - 房间里的结构化事件按 world/event_id 进入同一套 store 与 UI 投影；
 * - `room_event_id` 游标与结构化 `event_id` 同时生效，同 revision 的多条
 *   聊天事件全部渲染，重复帧只 ACK 不重复分发；
 * - **房间传输不会给结构化信封注入 `action_id`**（M0 是
 *   `additionalProperties: false`，多一个字段就等于协议不兼容）；
 * - 快照前排队、快照后立刻发出；换世界后旧世界事件被丢弃。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  EVENT_FIXTURES,
  STRUCTURED_CAPABILITIES_WIRE,
} from "./protocol/structured-fixtures";
import { connectRoom, disconnectRoom } from "./room-ws";
import { useAppStore } from "./state/app-store";
import { useSceneStore } from "./state/scene-store";
import {
  initialStructuredState,
  useStructuredStore,
} from "./state/structured-store";
import {
  resetStructuredTransport,
  sendStructuredAction,
} from "./structured-transport";
import { flushNarrativeStream } from "./renderer";
import { useMessageStore } from "./state/message-store";

vi.mock("./online", () => ({
  enterLobby: vi.fn(),
  enterSoloLobby: vi.fn(),
  refreshRoom: vi.fn(),
}));

vi.mock("./panels", async () => {
  const actual = await vi.importActual<typeof import("./panels")>("./panels");
  return {
    ...actual,
    clearTransientHandouts: vi.fn(),
    openSavePanel: vi.fn(),
    updateCharPanel: vi.fn(),
    updateCluePanel: vi.fn(),
  };
});

const ROOM_WORLD = "world-room-1";

class FakeWebSocket {
  static readonly OPEN = 1;
  static instances: FakeWebSocket[] = [];

  readonly url: string;
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: ((event: { code: number; reason: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  sent: string[] = [];

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  send(payload: string): void {
    this.sent.push(payload);
  }

  close(): void {
    this.readyState = 3;
  }

  /** 模拟服务端推帧。 */
  deliver(frame: Record<string, unknown>): void {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }

  open(): void {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }

  sentFrames(): Record<string, unknown>[] {
    return this.sent.map((raw) => JSON.parse(raw) as Record<string, unknown>);
  }
}

function roomFullState(latestEventId = 0) {
  return {
    type: "room_full_state",
    latest_event_id: latestEventId,
    status: "playing",
    owner_user_id: "user-owner",
    current_actor_user_id: "user-alice",
    ready_user_ids: ["user-alice"],
    online_user_ids: ["user-alice", "user-bob"],
    history: [],
    investigators: [],
    active_investigator_id: "inv-alice",
    private_state: {
      investigator_id: "inv-alice",
      pc: {
        name: "爱丽丝",
        hp: 12,
        max_hp: 12,
        san: 65,
        max_san: 65,
        inventory: [],
      },
      clues: {},
      player_notes: { text: "", revision: 0 },
    },
  };
}

function structuredFrame(
  eventId: number,
  type: string,
  payload: Record<string, unknown>,
  options: { revision?: number; roomEventId?: number; worldId?: string } = {},
) {
  return {
    protocol_version: 1,
    event_id: eventId,
    world_id: options.worldId ?? ROOM_WORLD,
    sequence: eventId,
    revision: options.revision ?? 12,
    type,
    payload,
    ...(options.roomEventId === undefined
      ? {}
      : { room_event_id: options.roomEventId }),
  };
}

/** 连上房间并完成一次房间快照（房间传输在快照前会排队）。 */
function openRoom() {
  connectRoom(ROOM_WORLD);
  const socket = FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
  socket.open();
  socket.deliver(roomFullState(1));
  return socket;
}

beforeEach(() => {
  vi.stubGlobal("WebSocket", FakeWebSocket);
  FakeWebSocket.instances = [];
  resetStructuredTransport();
  useStructuredStore.setState({ ...initialStructuredState });
  useMessageStore.setState({ messages: [] });
  useSceneStore.getState().reset();
  useAppStore.setState({
    mode: "online",
    connection: "connected",
    inputEnabled: true,
    activeWorldId: ROOM_WORLD,
  });
});

afterEach(() => {
  disconnectRoom();
  vi.unstubAllGlobals();
});

describe("房间路径：结构化事件路由", () => {
  it("房间快照后 session_snapshot 进入结构化 store 并驱动顶栏位置", () => {
    const socket = openRoom();
    socket.deliver(
      structuredFrame(1, "session_snapshot", EVENT_FIXTURES.snapshot.payload, {
        roomEventId: 2,
      }),
    );

    const state = useStructuredStore.getState();
    expect(state.identity.worldId).toBe(ROOM_WORLD);
    expect(state.identity.investigatorId).toBe("inv-alice");
    expect(state.clues.length).toBeGreaterThan(0);
    expect(state.destinations.length).toBeGreaterThan(0);
    // 复用既有场景指示器：位置由已提交快照恢复。
    expect(useSceneStore.getState().name).toBe("密斯卡托尼克大学");
  });

  it("按钮在房间里发出 M0 信封，且不会被注入 action_id", () => {
    const socket = openRoom();
    socket.deliver(
      structuredFrame(1, "session_snapshot", EVENT_FIXTURES.snapshot.payload, {
        roomEventId: 2,
      }),
    );

    const result = sendStructuredAction({
      kind: "move",
      destination_scene_id: "miskatonic_medical",
    });
    expect(result.ok).toBe(true);

    const frames = socket.sentFrames();
    const request = frames.find((frame) => frame.type === "action_request");
    expect(request).toBeDefined();
    expect(request).toMatchObject({
      type: "action_request",
      protocol_version: 1,
      world_id: ROOM_WORLD,
      expected_revision: 12,
      investigator_id: "inv-alice",
      action: { kind: "move", destination_scene_id: "miskatonic_medical" },
    });
    // M0 的信封是 additionalProperties: false：房间传输不得额外注入字段。
    expect(Object.keys(request!).sort()).toEqual([
      "action",
      "expected_revision",
      "investigator_id",
      "protocol_version",
      "request_id",
      "type",
      "world_id",
    ]);
    expect(request).not.toHaveProperty("action_id");
    expect(request).not.toHaveProperty("room_event_id");
  });

  it("拿到能力快照即可提交；房间镜像未到时按房间队列排队，镜像到达后立即发出", () => {
    // 房间镜像（room_full_state）之前不允许往 socket 写：房间传输会排队。
    connectRoom(ROOM_WORLD);
    const socket = FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
    socket.open();

    // 还没有任何服务端投影：能力未知，提交被门禁明确拒绝（不是静默排队）。
    expect(
      sendStructuredAction({
        kind: "move",
        destination_scene_id: "miskatonic_medical",
      }).ok,
    ).toBe(false);

    // 结构化快照先到：能力协商完成，提交被受理但仍在房间队列里（镜像未到）。
    socket.deliver(
      structuredFrame(1, "session_snapshot", EVENT_FIXTURES.snapshot.payload, {
        roomEventId: 2,
      }),
    );
    expect(
      sendStructuredAction({
        kind: "move",
        destination_scene_id: "miskatonic_medical",
      }).ok,
    ).toBe(true);
    expect(
      socket.sentFrames().filter((frame) => frame.type === "action_request"),
    ).toHaveLength(0);

    // 房间镜像到达 → 队列清空 → 结构化请求立刻出现在 socket 上。
    socket.deliver(roomFullState(2));
    expect(
      socket.sentFrames().filter((frame) => frame.type === "action_request"),
    ).toHaveLength(1);
  });

  it("room_event_id 游标与 event_id 同时生效：重复帧只 ACK，不重复分发", () => {
    const socket = openRoom();
    const chat = (eventId: number, roomEventId: number, text: string) =>
      structuredFrame(
        10 + eventId,
        "message_completed",
        {
          message_id: `m-${eventId}`,
          speaker: { kind: "npc", id: "bryce_fallon", name: "法伦" },
          text,
        },
        { roomEventId, revision: 12 },
      );

    socket.deliver(chat(1, 5, "第一句"));
    socket.deliver(chat(2, 6, "第二句"));
    // 重复的 room_event_id：只回 ACK，不再次分发。
    socket.deliver(chat(2, 6, "第二句"));

    flushNarrativeStream();
    const rendered = useMessageStore
      .getState()
      .messages.filter((message) => message.kind === "gm")
      .flatMap((message) => [
        message.text,
        ...(message.segments ?? []).map((s) => s.text),
      ])
      .join("|");
    expect(rendered).toContain("第一句");
    expect(rendered).toContain("第二句");
    const acks = socket
      .sentFrames()
      .filter((frame) => frame.type === "room_ack")
      .map((frame) => frame.event_id);
    expect(acks).toEqual([5, 6, 6]);
    // 重复帧只应用一次：包含“第二句”的消息只有一条（同一消息的 text 与
    // segments 会同时携带该段文本，不能按出现次数断言）。
    const duplicates = useMessageStore
      .getState()
      .messages.filter((message) =>
        [message.text, ...(message.segments ?? []).map((s) => s.text)].some(
          (part) => (part ?? "").includes("第二句"),
        ),
      );
    expect(duplicates).toHaveLength(1);
  });

  it("同 revision 的多条结构化聊天事件全部渲染", () => {
    const socket = openRoom();
    socket.deliver(
      structuredFrame(1, "session_snapshot", EVENT_FIXTURES.snapshot.payload, {
        roomEventId: 2,
        revision: 12,
      }),
    );
    for (const [index, text] of ["甲的话", "乙的话", "丙的话"].entries()) {
      socket.deliver(
        structuredFrame(
          20 + index,
          "message_completed",
          {
            message_id: `same-rev-${index}`,
            speaker: { kind: "npc", id: `npc-${index}`, name: `NPC${index}` },
            text,
          },
          { roomEventId: 10 + index, revision: 12 },
        ),
      );
    }
    flushNarrativeStream();
    const rendered = useMessageStore
      .getState()
      .messages.flatMap((message) => [
        message.text,
        ...(message.segments ?? []).map((s) => s.text),
      ])
      .join("|");
    for (const text of ["甲的话", "乙的话", "丙的话"]) {
      expect(rendered, `${text} 未被渲染`).toContain(text);
    }
  });

  it("换世界（新 session_snapshot）后旧世界事件不污染新世界", () => {
    const socket = openRoom();
    socket.deliver(
      structuredFrame(1, "session_snapshot", EVENT_FIXTURES.snapshot.payload, {
        roomEventId: 2,
      }),
    );
    // 服务端把房间切到另一个世界。
    socket.deliver(
      structuredFrame(
        50,
        "session_snapshot",
        {
          ...EVENT_FIXTURES.snapshot.payload,
          scene: { id: "hobhouse_mansion", name: "霍布豪斯宅邸" },
          destinations: [{ id: "hobhouse_mansion", name: "霍布豪斯宅邸" }],
        },
        { roomEventId: 3, worldId: "world-room-2" },
      ),
    );
    expect(useSceneStore.getState().name).toBe("霍布豪斯宅邸");

    // 旧世界的迟到事件。
    socket.deliver(
      structuredFrame(
        60,
        "scene_changed",
        {
          scene: { id: "miskatonic_medical", name: "旧世界的停尸房" },
        },
        { roomEventId: 4, worldId: ROOM_WORLD },
      ),
    );
    expect(useSceneStore.getState().name).toBe("霍布豪斯宅邸");
    expect(useStructuredStore.getState().identity.worldId).toBe("world-room-2");
  });

  it("房间里的检定卡与状态卡复用同一套 store", () => {
    const socket = openRoom();
    socket.deliver(
      structuredFrame(
        1,
        "session_snapshot",
        {
          ...EVENT_FIXTURES.snapshot.payload,
          server_capabilities: STRUCTURED_CAPABILITIES_WIRE,
        },
        { roomEventId: 2 },
      ),
    );
    socket.deliver(
      structuredFrame(
        2,
        "check_requested",
        {
          check_request_id: "chk-room-1",
          investigator_id: "inv-alice",
          skill: "说服",
          difficulty: "regular",
          attempt: "向医生说明来意。",
          visibility: "public",
        },
        { roomEventId: 3 },
      ),
    );
    expect(useStructuredStore.getState().checks["chk-room-1"].status).toBe(
      "pending",
    );
    expect(
      useStructuredStore.getState().checks["chk-room-1"].attempt,
    ).toContain("说明来意");
  });

  it("房间里的结构化事件不会被当成“无法识别的协议消息”丢弃", () => {
    const socket = openRoom();
    socket.deliver(
      structuredFrame(1, "session_snapshot", EVENT_FIXTURES.snapshot.payload, {
        roomEventId: 2,
      }),
    );
    socket.deliver(
      structuredFrame(
        2,
        "roll_resolved",
        {
          request_id: "req-roll-room",
          expression: "1d100",
          dice: [{ sides: 100, values: [7] }],
          total: 7,
          modifier: 0,
        },
        { roomEventId: 3 },
      ),
    );
    // 骰子结果进入聊天区（服务端结果），且没有产生协议错误气泡。
    flushNarrativeStream();
    const texts = useMessageStore
      .getState()
      .messages.map((message) => message.text)
      .join("|");
    expect(texts).toContain("1d100");
    expect(texts).not.toContain("无法识别的协议消息");
  });
});
