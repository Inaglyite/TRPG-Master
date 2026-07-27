import { create } from "zustand";

import type { AuthUser } from "../api/auth";
import type { ModuleInfo } from "../api/modules";
import type {
  CharacterOption,
  InviteMeta,
  RoomInvite,
  RoomMember,
  WorldMetadata,
  WorldSummary,
} from "../api/worlds";

export type AuthStatus = "checking" | "anonymous" | "authenticated";
export type OnlineView = "auth" | "lobby" | "room";
/** 异步数据的读取状态；unsupported 表示接口意外返回 404/405/501。 */
export type AsyncStatus =
  "idle" | "loading" | "ready" | "error" | "unsupported";
/** 房间 WebSocket 的连接状态。 */
export type RoomConnection =
  "idle" | "connecting" | "connected" | "disconnected";

/** 服务端定向投递的私密事件（仅本人连接可见）。绝不写入公共消息历史。 */
export type PrivateEvent = {
  kind: string;
  clue?: { id?: string; text: string; category?: string };
  roomEventId?: number;
};

/** room_full_state.private_state：仅属于当前用户的私有状态快照。 */
export type PrivateClue = {
  id?: string;
  text: string;
  category?: string;
  [key: string]: unknown;
};

export type PrivateState = {
  investigatorId: string | null;
  pc: unknown;
  /** 按 category 分组的线索（与角色面板 ClueState 同构）。 */
  clues: Record<string, PrivateClue[]>;
  playerNotes: string;
  playerNotesRevision: number;
};

export type OnlineState = {
  // 认证状态机
  authStatus: AuthStatus;
  user: AuthUser | null;
  authBusy: boolean;
  authError: string | null;
  sessionExpired: boolean;
  // 联机外壳内的界面
  view: OnlineView;
  // 大厅
  worlds: WorldSummary[];
  worldsStatus: AsyncStatus;
  worldsError: string | null;
  modules: ModuleInfo[];
  modulesStatus: AsyncStatus;
  createBusy: boolean;
  createError: string | null;
  joinBusy: boolean;
  joinError: string | null;
  // 房间
  activeWorldId: string | null;
  roomModule: string | null;
  roomMetadata: WorldMetadata | null;
  members: RoomMember[];
  membersStatus: AsyncStatus;
  membersError: string | null;
  characterOptions: CharacterOption[];
  charactersStatus: AsyncStatus;
  // 房间 WebSocket 状态（room_state 事件驱动）
  roomConnection: RoomConnection;
  roomStatus: string | null;
  ownerUserId: string | null;
  currentActorUserId: string | null;
  readyUserIds: string[];
  onlineUserIds: string[];
  /** room_full_state 下发的房间调查员公开摘要与当前行动调查员。 */
  roomInvestigators: Record<string, unknown>[];
  activeInvestigatorId: string | null;
  invite: RoomInvite | null;
  invites: InviteMeta[];
  inviteBusy: boolean;
  privateEvents: PrivateEvent[];
  privateState: PrivateState | null;
  roomBusy: boolean;
  roomError: string | null;
};

export const initialOnlineState: OnlineState = {
  authStatus: "checking",
  user: null,
  authBusy: false,
  authError: null,
  sessionExpired: false,
  view: "auth",
  worlds: [],
  worldsStatus: "idle",
  worldsError: null,
  modules: [],
  modulesStatus: "idle",
  createBusy: false,
  createError: null,
  joinBusy: false,
  joinError: null,
  activeWorldId: null,
  roomModule: null,
  roomMetadata: null,
  members: [],
  membersStatus: "idle",
  membersError: null,
  characterOptions: [],
  charactersStatus: "idle",
  roomConnection: "idle",
  roomStatus: null,
  ownerUserId: null,
  currentActorUserId: null,
  readyUserIds: [],
  onlineUserIds: [],
  roomInvestigators: [],
  activeInvestigatorId: null,
  invite: null,
  invites: [],
  inviteBusy: false,
  privateEvents: [],
  privateState: null,
  roomBusy: false,
  roomError: null,
};

export const useOnlineStore = create<OnlineState>(() => ({
  ...initialOnlineState,
}));

// REST 请求不能仅凭“请求完成”就写回 store：用户可能已经退出、换号或
// 切换房间。所有会改变认证/页面归属的流程先递增代次，异步调用在提交前
// 比较捕获值即可丢弃迟到响应。
let onlineRequestEpoch = 0;

export function bumpOnlineRequestEpoch(): number {
  onlineRequestEpoch += 1;
  return onlineRequestEpoch;
}

export function currentOnlineRequestEpoch(): number {
  return onlineRequestEpoch;
}

/** 退出登录或会话过期时清空账号相关数据，避免串号。 */
export function resetOnlineState(patch: Partial<OnlineState> = {}): void {
  bumpOnlineRequestEpoch();
  useOnlineStore.setState({
    ...initialOnlineState,
    authStatus: "anonymous",
    ...patch,
  });
}

/**
 * 当前用户是否为房间房主。多人房主专属操作（存档管理、结案等）的
 * 前端门禁；服务端仍会再次校验，此处只决定 UI 展示与快捷阻断。
 */
export function isRoomOwner(): boolean {
  const { user, members } = useOnlineStore.getState();
  if (!user) return false;
  return members.some(
    (member) => member.user_id === user.id && member.role === "owner",
  );
}

/** 多人游戏行动门禁；服务端仍是最终权限边界。 */
export function canCurrentUserAct(): boolean {
  const { user, members, roomConnection, roomStatus, currentActorUserId } =
    useOnlineStore.getState();
  if (
    !user ||
    roomConnection !== "connected" ||
    roomStatus !== "playing" ||
    currentActorUserId !== user.id
  ) {
    return false;
  }
  const role = members.find((member) => member.user_id === user.id)?.role;
  return role === "owner" || role === "player";
}
