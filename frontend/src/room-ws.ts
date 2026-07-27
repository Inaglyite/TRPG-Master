import { apiHttpOrigin } from "./api/client";
import {
  clearTransientHandouts,
  updateCharPanel,
  updateCluePanel,
} from "./panels";
import { parseServerMessage } from "./protocol/server-message";
import { useAppStore } from "./state/app-store";
import {
  bumpOnlineRequestEpoch,
  PrivateState,
  resetOnlineState,
  useOnlineStore,
} from "./state/online-store";
import { useStartStore } from "./state/start-store";
import {
  displayWorldHistory,
  handleServerPayload,
  recoverRejectedRoomAction,
  resetRoomGameSession,
  setActiveTransport,
} from "./ws";

/**
 * 多人房间 WebSocket（/ws/room?world_id=<id>）连接管理。
 *
 * 职责：连接生命周期（复用登录 Cookie）、有界指数退避重连、房间事件序号
 * （room_event_id，JSON number）跟踪与 room_ack、断线后 room_sync 增量恢复、
 * room_event_gap 后应用服务端发来的 room_full_state（重置游标）。
 * room_* 控制事件在此消费；其余游戏事件（narrative/dice/choices/state_data…）
 * 交给 ws.ts 的统一 dispatcher（handleServerPayload），与单机共享同一套渲染。
 * 发送侧经 ws.ts 的 transport adapter 指向本连接，start/action/continue/
 * save_load/turn_rewrite 自动注入幂等 action_id。单机 /ws 在联机模式不启用。
 */

const RECONNECT_DELAYS_MS = [1000, 2000, 5000, 10000, 30000];

let socket: WebSocket | null = null;
let activeWorldId: string | null = null;
let reconnectAttempt = 0;
let reconnectTimer: number | null = null;
let manuallyClosed = false;
let lastEventId: number | null = null;
let roomSnapshotApplied = false;
const sendQueue: string[] = [];
const MAX_SEND_QUEUE = 64;
const LAST_ROOM_KEY = "trpg-online-world-id";

/** 清除仅属于上一位玩家/上一房间的本地展示数据，避免切房或串号泄露。 */
function clearPrivatePresentationState(): void {
  clearTransientHandouts();
  useAppStore.setState({
    character: null,
    clues: {},
    notesText: "",
    notesRevision: 0,
    notesDirty: false,
    notesSaving: false,
    notesLoading: false,
    notesStatus: "",
    notesStatusKind: "",
    characterPanelOpen: false,
    handouts: [],
    clueToast: null,
    choices: [],
    dialog: null,
    ending: null,
    utilityOpen: false,
    inputEnabled: false,
    inputPlaceholder: "等待守秘人叙述……",
    savePanelOpen: false,
    saves: [],
    quickSaveState: "idle",
  });
  useStartStore.setState({
    gameStarted: false,
    gameStarting: false,
  });
}

/** 房间 WS 地址使用云端 origin（apiHttpOrigin），与单机本地后端隔离。 */
export function roomWsUrl(worldId: string): string {
  const origin = new URL(apiHttpOrigin());
  origin.protocol = origin.protocol === "https:" ? "wss:" : "ws:";
  origin.pathname = "/ws/room";
  origin.search = `?world_id=${encodeURIComponent(worldId)}`;
  return origin.toString();
}

function setConnectionState(
  roomConnection: "connecting" | "connected" | "disconnected",
) {
  useOnlineStore.setState({ roomConnection });
  // 游戏界面的连接指示（AppHeader/ConnectionNotice）与房间连接同步。
  useAppStore.setState({ connection: roomConnection });
}

/** 连接指定房间；重复连接同一房间是幂等的。 */
export function connectRoom(worldId: string): void {
  if (socket && activeWorldId === worldId) return;
  disconnectRoom();
  activeWorldId = worldId;
  manuallyClosed = false;
  lastEventId = null;
  roomSnapshotApplied = false;
  setActiveTransport({ send: (payload) => sendRaw(injectActionId(payload)) });
  open();
}

function open(): void {
  if (!activeWorldId) return;
  setConnectionState("connecting");
  const next = new WebSocket(roomWsUrl(activeWorldId));
  socket = next;

  next.onopen = () => {
    if (socket !== next) return;
    reconnectAttempt = 0;
    setConnectionState("connected");
    if (lastEventId !== null) {
      // 断线恢复：按最后的序号增量补发缺失的房间事件。
      next.send(
        JSON.stringify({ type: "room_sync", after_event_id: lastEventId }),
      );
    }
  };

  next.onmessage = (event) => {
    if (socket !== next) return;
    handleRoomMessage(event.data);
  };

  next.onclose = (event) => {
    if (socket !== next) return;
    socket = null;
    roomSnapshotApplied = false;
    setConnectionState("disconnected");
    if (event.code === 4401) {
      // Session 已失效：清空账号与房间数据并停掉重连，认证页显示过期提示。
      disconnectRoom();
      resetOnlineState({ sessionExpired: true });
      return;
    }
    if (event.code === 4403) {
      // 成员资格被撤销/被踢：仍保留登录态，但不再尝试连接无权访问的房间。
      disconnectRoom();
      bumpOnlineRequestEpoch();
      try {
        localStorage.removeItem(LAST_ROOM_KEY);
      } catch {
        /* localStorage 不可用时仍可回到大厅 */
      }
      useOnlineStore.setState({
        view: "lobby",
        activeWorldId: null,
        worldsStatus: "loading",
        worldsError: null,
      });
      void import("./online").then(({ enterLobby }) => enterLobby());
      return;
    }
    scheduleReconnect();
  };

  next.onerror = () => {
    next.close();
  };
}

function scheduleReconnect(): void {
  if (manuallyClosed || !activeWorldId || reconnectTimer !== null) return;
  const delay =
    RECONNECT_DELAYS_MS[
      Math.min(reconnectAttempt, RECONNECT_DELAYS_MS.length - 1)
    ];
  reconnectAttempt += 1;
  reconnectTimer = window.setTimeout(() => {
    reconnectTimer = null;
    open();
  }, delay);
}

/** 主动断开（离开房间/退出登录/切换模式）；不触发重连。 */
export function disconnectRoom(): void {
  manuallyClosed = true;
  roomSnapshotApplied = false;
  if (reconnectTimer !== null) {
    window.clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  const current = socket;
  socket = null;
  activeWorldId = null;
  lastEventId = null;
  sendQueue.length = 0;
  setActiveTransport(null);
  resetRoomGameSession();
  current?.close();
  // 公共叙事也属于房间；退出、切房和换号时不能短暂显示上一房间内容。
  displayWorldHistory([]);
  // displayWorldHistory([]) 会复用单机 onDone；最后再次关闭输入并清私态。
  clearPrivatePresentationState();
  useOnlineStore.setState({
    roomConnection: "idle",
    roomStatus: null,
    ownerUserId: null,
    currentActorUserId: null,
    readyUserIds: [],
    onlineUserIds: [],
    roomInvestigators: [],
    activeInvestigatorId: null,
    privateEvents: [],
    privateState: null,
  });
}

function sendRaw(data: string): void {
  if (roomSnapshotApplied && socket && socket.readyState === WebSocket.OPEN) {
    socket.send(data);
  } else {
    if (sendQueue.length >= MAX_SEND_QUEUE) sendQueue.shift();
    sendQueue.push(data);
  }
}

function flushSendQueue(): void {
  if (!socket || socket.readyState !== WebSocket.OPEN) return;
  while (sendQueue.length > 0) {
    socket.send(sendQueue.shift() as string);
  }
}

function sendProtocolFrame(payload: Record<string, unknown>): void {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(payload));
  }
}

/** 发送房间消息（对象形式）；未连接时排队，重连成功后按序补发。 */
export function roomSend(payload: Record<string, unknown>): void {
  sendRaw(JSON.stringify(payload));
}

let actionCounter = 0;

/** start/action 等房间操作需要的幂等 action_id。 */
export function newActionId(): string {
  if (
    typeof crypto !== "undefined" &&
    typeof crypto.randomUUID === "function"
  ) {
    return crypto.randomUUID();
  }
  actionCounter += 1;
  return `act_${Date.now()}_${actionCounter}_${Math.random().toString(36).slice(2, 10)}`;
}

const ACTION_ID_TYPES = new Set([
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
]);

/** 经 transport 外发的客户端消息按需注入幂等 action_id（重复补发保持同一 id）。 */
export function injectActionId(payload: string): string {
  try {
    const data = JSON.parse(payload);
    if (
      data &&
      typeof data === "object" &&
      ACTION_ID_TYPES.has(String(data.type)) &&
      typeof data.action_id !== "string"
    ) {
      data.action_id = newActionId();
      return JSON.stringify(data);
    }
    return payload;
  } catch {
    return payload;
  }
}

const REJECTION_TEXTS: Record<string, string> = {
  room_not_ready: "还有玩家未选择调查员或未准备",
  investigator_required: "当前行动者需要先选择调查员",
  not_actor: "还没有轮到你行动",
  not_current_actor: "还没有轮到你行动",
  owner_cannot_leave: "房主需要先移交房主才能退出房间",
  invalid_actor: "旁观者不能被指定为行动者",
};

async function refreshRoomMembers(): Promise<void> {
  // 延迟import避免循环依赖：online.ts 导入 room-ws.ts 的发送能力。
  const { refreshRoom } = await import("./online");
  await refreshRoom();
}

/** room_state 与 room_full_state 共用的房间控制字段应用（字段存在才覆盖）。 */
function applyRoomStateFields(message: Record<string, any>): void {
  useOnlineStore.setState((state) => ({
    roomStatus:
      typeof message.status === "string" ? message.status : state.roomStatus,
    ownerUserId:
      typeof message.owner_user_id === "string"
        ? message.owner_user_id
        : message.owner_user_id === null
          ? null
          : state.ownerUserId,
    currentActorUserId:
      typeof message.current_actor_user_id === "string"
        ? message.current_actor_user_id
        : message.current_actor_user_id === null
          ? null
          : state.currentActorUserId,
    readyUserIds: Array.isArray(message.ready_user_ids)
      ? message.ready_user_ids.filter(
          (id: unknown): id is string => typeof id === "string",
        )
      : state.readyUserIds,
    onlineUserIds: Array.isArray(message.online_user_ids)
      ? message.online_user_ids.filter(
          (id: unknown): id is string => typeof id === "string",
        )
      : state.onlineUserIds,
  }));
}

/** private_state.clues 是按 category 分组的对象；清洗时保留原字段，不丢数据。 */
function sanitizeClueGroups(raw: unknown): PrivateState["clues"] {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  const groups: PrivateState["clues"] = {};
  for (const [category, items] of Object.entries(raw)) {
    if (!Array.isArray(items)) continue;
    groups[category] = items.flatMap((item) => {
      if (!item || typeof item !== "object") return [];
      const record = item as Record<string, unknown>;
      if (typeof record.text !== "string") return [];
      return [{ ...record, text: record.text, category }];
    });
  }
  return groups;
}

function handleRoomMessage(raw: unknown): void {
  const message = parseServerMessage(raw);
  if (!message) return;

  // 房间事件序号是 JSON number：跟踪并回执，重连时据此增量恢复。
  // live/replay 中重复或倒序的序号不重复分发、不回退游标；
  // room_full_state 是唯一允许重置游标的路径，自身不受此守卫限制。
  const rawEventId = message.room_event_id;
  if (
    message.type !== "room_full_state" &&
    typeof rawEventId === "number" &&
    Number.isInteger(rawEventId) &&
    rawEventId >= 0
  ) {
    // ACK 重复帧，避免服务端继续补发；但不要再次分发，也不能让游标倒退。
    if (lastEventId !== null && rawEventId <= lastEventId) {
      sendProtocolFrame({ type: "room_ack", event_id: rawEventId });
      return;
    }
    lastEventId = rawEventId;
    sendProtocolFrame({ type: "room_ack", event_id: rawEventId });
  }

  switch (message.type) {
    case "room_state":
      applyRoomStateFields(message);
      break;
    case "room_full_state": {
      // latest_event_id 必须重置游标（包括服务重启后旧游标大于最新序号的情况）。
      if (typeof message.latest_event_id === "number") {
        lastEventId = message.latest_event_id;
      }
      applyRoomStateFields(message);
      const roomPlaying =
        (typeof message.status === "string"
          ? message.status
          : useOnlineStore.getState().roomStatus) === "playing";
      useStartStore.setState({
        gameStarted: roomPlaying,
        gameStarting: false,
      });
      useOnlineStore.setState({
        roomInvestigators: Array.isArray(message.investigators)
          ? message.investigators.filter(
              (item): item is Record<string, unknown> =>
                !!item && typeof item === "object",
            )
          : [],
        activeInvestigatorId:
          typeof message.active_investigator_id === "string"
            ? message.active_investigator_id
            : null,
      });
      // 公共叙事历史走与单机相同的渲染链，缺口/服务重启后恢复完整公共叙事；
      // private_state 绝不进入这条公共链路。
      if (Array.isArray(message.history)) {
        displayWorldHistory(message.history);
      }
      const rawPrivate = message.private_state;
      if (rawPrivate && typeof rawPrivate === "object") {
        const clueGroups = sanitizeClueGroups(rawPrivate.clues);
        const rawNotes = rawPrivate.player_notes;
        const notesText =
          rawNotes &&
          typeof rawNotes === "object" &&
          typeof rawNotes.text === "string"
            ? rawNotes.text
            : "";
        const notesRevision =
          rawNotes &&
          typeof rawNotes === "object" &&
          typeof rawNotes.revision === "number"
            ? rawNotes.revision
            : 0;
        useOnlineStore.setState({
          privateState: {
            investigatorId:
              typeof rawPrivate.investigator_id === "string"
                ? rawPrivate.investigator_id
                : null,
            pc: rawPrivate.pc ?? null,
            clues: clueGroups,
            playerNotes: notesText,
            playerNotesRevision: notesRevision,
          },
        });
        // 复用现有入口把私有状态落到角色面板与调查笔记（均为本地展示状态）。
        if (rawPrivate.pc && typeof rawPrivate.pc === "object") {
          updateCharPanel(JSON.stringify(rawPrivate.pc));
        }
        updateCluePanel(JSON.stringify(clueGroups));
        if (rawNotes && typeof rawNotes === "object") {
          useAppStore.getState().applyNotes({
            text: notesText,
            revision: notesRevision,
            saved: true,
          });
        }
      } else {
        useOnlineStore.setState({ privateState: null });
        clearPrivatePresentationState();
      }
      // 只有权威快照落地后才发送断线期间积压的动作，避免基于旧行动者或旧存档
      // 状态抢先提交。ACK/sync 使用直连路径，不受该队列影响。
      roomSnapshotApplied = true;
      flushSendQueue();
      break;
    }
    case "member_joined":
    case "member_left":
    case "member_removed":
    case "investigator_claimed":
    case "investigator_released":
      void refreshRoomMembers();
      break;
    case "owner_changed":
      if (typeof message.owner_user_id === "string") {
        useOnlineStore.setState({ ownerUserId: message.owner_user_id });
      }
      void refreshRoomMembers();
      break;
    case "actor_changed": {
      // 权威字段是 user_id；current_actor_user_id 仅作兼容回退。
      const actor =
        typeof message.user_id === "string"
          ? message.user_id
          : typeof message.current_actor_user_id === "string"
            ? message.current_actor_user_id
            : null;
      useOnlineStore.setState({ currentActorUserId: actor });
      break;
    }
    case "investigator_roster":
      useOnlineStore.setState({
        roomInvestigators: message.investigators,
        activeInvestigatorId:
          typeof message.active_investigator_id === "string"
            ? message.active_investigator_id
            : null,
      });
      break;
    case "room_action_rejected": {
      const code = typeof message.code === "string" ? message.code : "";
      const detail =
        typeof message.detail === "string"
          ? message.detail
          : typeof message.message === "string"
            ? message.message
            : null;
      useOnlineStore.setState({
        roomError: REJECTION_TEXTS[code] ?? detail ?? "操作被服务器拒绝",
      });
      recoverRejectedRoomAction();
      break;
    }
    case "protocol_error":
      useOnlineStore.setState({
        roomError:
          typeof message.message === "string"
            ? message.message
            : "房间协议消息未被服务器接受",
      });
      break;
    case "room_error": {
      const detail =
        typeof message.message === "string"
          ? message.message
          : typeof message.detail === "string"
            ? message.detail
            : null;
      useOnlineStore.setState({ roomError: detail ?? "房间发生错误" });
      break;
    }
    case "room_event_gap":
      // 服务端紧接着发送个性化 room_full_state；此处不再发起第二次 room_sync，
      // 等待并应用 full_state（其 latest_event_id 会重置游标）。
      void refreshRoomMembers();
      break;
    case "private_event": {
      // 私密事件只进入独立的私密收件箱，绝不写入公共消息历史。
      const clue =
        message.clue && typeof message.clue.text === "string"
          ? {
              id:
                typeof message.clue.id === "string"
                  ? message.clue.id
                  : undefined,
              text: message.clue.text,
              category:
                typeof message.clue.category === "string"
                  ? message.clue.category
                  : undefined,
            }
          : undefined;
      const roomEventId =
        typeof message.room_event_id === "number"
          ? message.room_event_id
          : undefined;
      useOnlineStore.setState((state) => {
        if (
          roomEventId !== undefined &&
          state.privateEvents.some((event) => event.roomEventId === roomEventId)
        ) {
          return state;
        }
        return {
          privateEvents: [
            ...state.privateEvents,
            {
              kind: typeof message.kind === "string" ? message.kind : "unknown",
              clue,
              roomEventId,
            },
          ].slice(-50),
        };
      });
      break;
    }
    default:
      // 游戏事件（narrative/gm_turn_start/dice/choices/state_data/handout/
      // decision/save 等）与单机共用统一 dispatcher 与渲染链路。
      handleServerPayload(raw);
      break;
  }
}
