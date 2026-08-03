import { ApiError, onUnauthorized } from "./api/client";
import {
  fetchMe,
  login as apiLogin,
  logout as apiLogout,
  registerAccount,
} from "./api/auth";
import { listModules } from "./api/modules";
import {
  acceptInvite,
  claimInvestigator,
  createInvite,
  createWorld,
  getInvestigatorOptions,
  getRoomInfo,
  listInvites,
  listWorlds,
  releaseInvestigator,
  removeMember,
  revokeInvite,
  transferOwnership,
  updateMember,
} from "./api/worlds";
import { disconnectRoom, newActionId, roomSend } from "./room-ws";
import {
  bumpOnlineRequestEpoch,
  currentOnlineRequestEpoch,
  resetOnlineState,
  useOnlineStore,
} from "./state/online-store";

function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (error.isNetwork) return error.message;
    if (error.status === 429) return "尝试过于频繁，请稍后再试";
    return error.message;
  }
  return fallback;
}

/**
 * 契约未实现的接口返回 404/405/501 时，界面应进入“等待后端接口”状态而非报错。
 * 后端已实现的资源错误也可能是 404（例如 world_not_found、invite_invalid）；
 * 这类错误必须交给业务流程处理，不能伪装成“接口暂不可用”。
 */
function isUnsupported(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false;
  if (error.status === 405 || error.status === 501) return true;
  return error.status === 404 && error.code === null;
}

let worldsRequestSerial = 0;
let roomRequestSerial = 0;
let characterRequestSerial = 0;
let inviteRequestSerial = 0;

type RequestScope = {
  epoch: number;
  userId: string | null;
  worldId?: string | null;
};

function captureRequestScope(worldId?: string | null): RequestScope {
  const state = useOnlineStore.getState();
  return {
    epoch: currentOnlineRequestEpoch(),
    userId: state.user?.id ?? null,
    ...(worldId !== undefined ? { worldId } : {}),
  };
}

function requestScopeIsCurrent(scope: RequestScope): boolean {
  const state = useOnlineStore.getState();
  return (
    scope.epoch === currentOnlineRequestEpoch() &&
    scope.userId === (state.user?.id ?? null) &&
    (scope.worldId === undefined || scope.worldId === state.activeWorldId)
  );
}

// —— 认证状态机 ——

export async function checkSession(): Promise<void> {
  const epoch = bumpOnlineRequestEpoch();
  useOnlineStore.setState({ authStatus: "checking", authError: null });
  try {
    const user = await fetchMe();
    if (epoch !== currentOnlineRequestEpoch()) return;
    useOnlineStore.setState({
      authStatus: "authenticated",
      user,
      sessionExpired: false,
    });
  } catch (error) {
    if (epoch !== currentOnlineRequestEpoch()) return;
    if (error instanceof ApiError && error.isUnauthorized) {
      useOnlineStore.setState({ authStatus: "anonymous", user: null });
    } else {
      useOnlineStore.setState({
        authStatus: "anonymous",
        user: null,
        authError: errorMessage(error, "无法连接服务器"),
      });
    }
  }
}

export async function login(
  username: string,
  password: string,
): Promise<boolean> {
  const epoch = bumpOnlineRequestEpoch();
  useOnlineStore.setState({
    authBusy: true,
    authError: null,
    sessionExpired: false,
  });
  try {
    const user = await apiLogin(username, password);
    if (epoch !== currentOnlineRequestEpoch()) return false;
    useOnlineStore.setState({
      authBusy: false,
      authStatus: "authenticated",
      user,
    });
    return true;
  } catch (error) {
    if (epoch !== currentOnlineRequestEpoch()) return false;
    useOnlineStore.setState({
      authStatus: "anonymous",
      authBusy: false,
      authError: errorMessage(error, "登录失败，请重试"),
    });
    return false;
  }
}

export async function register(
  username: string,
  password: string,
): Promise<boolean> {
  const epoch = bumpOnlineRequestEpoch();
  useOnlineStore.setState({
    authBusy: true,
    authError: null,
    sessionExpired: false,
  });
  try {
    const user = await registerAccount(username, password);
    if (epoch !== currentOnlineRequestEpoch()) return false;
    useOnlineStore.setState({
      authBusy: false,
      authStatus: "authenticated",
      user,
    });
    return true;
  } catch (error) {
    if (epoch !== currentOnlineRequestEpoch()) return false;
    useOnlineStore.setState({
      authStatus: "anonymous",
      authBusy: false,
      authError: errorMessage(error, "注册失败，请重试"),
    });
    return false;
  }
}

export async function logout(): Promise<boolean> {
  const epoch = bumpOnlineRequestEpoch();
  useOnlineStore.setState({ authBusy: true, authError: null });
  try {
    await apiLogout();
  } catch (error) {
    if (epoch !== currentOnlineRequestEpoch()) return false;
    // 401 表示 Session 本就已失效，可安全完成本地退出。其他失败必须保留
    // 登录态并明确报错，不能让仍有效的 HttpOnly Cookie 在后台悄悄存活。
    if (!(error instanceof ApiError && error.isUnauthorized)) {
      useOnlineStore.setState({
        authBusy: false,
        authError: errorMessage(error, "退出登录失败，请重试"),
      });
      return false;
    }
  }
  if (epoch !== currentOnlineRequestEpoch()) return false;
  disconnectRoom();
  resetOnlineState();
  return true;
}

/** 订阅云端 API 的全部 401；已认证状态下降级为“会话过期”。返回取消订阅函数。 */
export function initOnlineSession(): () => void {
  return onUnauthorized(() => {
    const state = useOnlineStore.getState();
    if (state.authStatus === "authenticated") {
      disconnectRoom();
      resetOnlineState({ sessionExpired: true });
    }
  });
}

// —— 大厅 ——

export async function refreshWorlds(): Promise<void> {
  const scope = captureRequestScope();
  const requestSerial = ++worldsRequestSerial;
  useOnlineStore.setState({ worldsStatus: "loading", worldsError: null });
  try {
    const worlds = await listWorlds();
    if (
      requestSerial !== worldsRequestSerial ||
      !requestScopeIsCurrent(scope)
    ) {
      return;
    }
    useOnlineStore.setState({ worlds: worlds ?? [], worldsStatus: "ready" });
  } catch (error) {
    if (
      requestSerial !== worldsRequestSerial ||
      !requestScopeIsCurrent(scope)
    ) {
      return;
    }
    useOnlineStore.setState({
      worldsStatus: "error",
      worldsError: errorMessage(error, "无法读取房间列表"),
    });
  }
}

export async function ensureModules(): Promise<void> {
  const { modulesStatus } = useOnlineStore.getState();
  if (modulesStatus === "loading" || modulesStatus === "ready") return;
  useOnlineStore.setState({ modulesStatus: "loading" });
  try {
    const modules = await listModules();
    useOnlineStore.setState({ modules: modules ?? [], modulesStatus: "ready" });
  } catch {
    useOnlineStore.setState({ modulesStatus: "error" });
  }
}

export async function enterLobby(): Promise<void> {
  disconnectRoom();
  bumpOnlineRequestEpoch();
  useOnlineStore.setState({
    view: "lobby",
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
    privateEvents: [],
    privateState: null,
    roomBusy: false,
    roomError: null,
    joinError: null,
    createError: null,
  });
  await Promise.all([refreshWorlds(), ensureModules()]);
}

export async function createRoom(
  module: string,
  name: string,
  maxPlayers: number,
): Promise<void> {
  const scope = captureRequestScope();
  useOnlineStore.setState({ createBusy: true, createError: null });
  try {
    const world = await createWorld(module, {
      ...(name.trim() ? { name: name.trim() } : {}),
      max_players: maxPlayers,
    });
    if (!requestScopeIsCurrent(scope)) return;
    useOnlineStore.setState({ createBusy: false });
    await enterRoom(world.world_id);
  } catch (error) {
    if (!requestScopeIsCurrent(scope)) return;
    useOnlineStore.setState({
      createBusy: false,
      createError: errorMessage(error, "创建房间失败，请重试"),
    });
  }
}

export async function joinWithToken(token: string): Promise<void> {
  const trimmed = token.trim();
  if (!trimmed) {
    useOnlineStore.setState({ joinError: "请输入邀请码" });
    return;
  }
  const scope = captureRequestScope();
  useOnlineStore.setState({ joinBusy: true, joinError: null });
  try {
    const accepted = await acceptInvite(trimmed);
    if (!requestScopeIsCurrent(scope)) return;
    useOnlineStore.setState({ joinBusy: false });
    await enterRoom(accepted.world_id);
  } catch (error) {
    if (!requestScopeIsCurrent(scope)) return;
    useOnlineStore.setState({
      joinBusy: false,
      joinError: isUnsupported(error)
        ? "邀请加入接口暂不可用，请稍后再试"
        : errorMessage(error, "无法加入房间，请检查邀请码"),
    });
  }
}

// —— 房间 ——

const LAST_ROOM_KEY = "trpg-online-world-id";

export async function enterRoom(worldId: string): Promise<void> {
  disconnectRoom();
  bumpOnlineRequestEpoch();
  try {
    localStorage.setItem(LAST_ROOM_KEY, worldId);
  } catch {
    /* localStorage 不可用时忽略 */
  }
  useOnlineStore.setState({
    view: "room",
    activeWorldId: worldId,
    roomModule: null,
    roomMetadata: null,
    members: [],
    membersStatus: "loading",
    membersError: null,
    characterOptions: [],
    charactersStatus: "idle",
    invite: null,
    invites: [],
    inviteBusy: false,
    privateEvents: [],
    privateState: null,
    roomConnection: "idle",
    roomStatus: null,
    ownerUserId: null,
    currentActorUserId: null,
    readyUserIds: [],
    onlineUserIds: [],
    roomInvestigators: [],
    activeInvestigatorId: null,
    roomBusy: false,
    roomError: null,
  });
  await refreshRoom();
}

export async function refreshRoom(): Promise<void> {
  const { activeWorldId } = useOnlineStore.getState();
  if (!activeWorldId) return;
  const scope = captureRequestScope(activeWorldId);
  const requestSerial = ++roomRequestSerial;
  try {
    const info = await getRoomInfo(activeWorldId);
    if (requestSerial !== roomRequestSerial || !requestScopeIsCurrent(scope)) {
      return;
    }
    useOnlineStore.setState({
      members: info.members ?? [],
      membersStatus: "ready",
      roomModule: info.module ?? null,
      roomMetadata: info.metadata ?? null,
    });
    if (info.module) await loadCharacters(activeWorldId, scope);
    if (!requestScopeIsCurrent(scope)) return;
    await refreshInvites(scope);
  } catch (error) {
    if (requestSerial !== roomRequestSerial || !requestScopeIsCurrent(scope)) {
      return;
    }
    if (isUnsupported(error)) {
      useOnlineStore.setState({ membersStatus: "unsupported" });
    } else {
      useOnlineStore.setState({
        membersStatus: "error",
        membersError: errorMessage(error, "无法读取成员列表"),
      });
    }
  }
}

/** 加载房间模组的候选调查员（按世界查询）；加载成功后不重复请求。 */
async function loadCharacters(
  worldId: string,
  expectedScope = captureRequestScope(worldId),
): Promise<void> {
  const { charactersStatus } = useOnlineStore.getState();
  if (charactersStatus === "loading" || charactersStatus === "ready") return;
  const requestSerial = ++characterRequestSerial;
  useOnlineStore.setState({ charactersStatus: "loading" });
  try {
    const data = await getInvestigatorOptions(worldId);
    if (
      requestSerial !== characterRequestSerial ||
      !requestScopeIsCurrent(expectedScope)
    ) {
      return;
    }
    const options = (data.groups ?? []).flatMap(
      (group) => group.characters ?? [],
    );
    useOnlineStore.setState({
      characterOptions: options,
      charactersStatus: "ready",
    });
  } catch (error) {
    if (
      requestSerial !== characterRequestSerial ||
      !requestScopeIsCurrent(expectedScope)
    ) {
      return;
    }
    useOnlineStore.setState({
      characterOptions: [],
      charactersStatus: isUnsupported(error) ? "unsupported" : "error",
    });
  }
}

/** 大厅加载时尝试回到上次的房间；房间已不可进入时静默留在大厅。 */
export async function resumeLastRoom(): Promise<void> {
  let worldId: string | null = null;
  try {
    worldId = localStorage.getItem(LAST_ROOM_KEY);
  } catch {
    return;
  }
  if (!worldId) return;
  try {
    await enterRoom(worldId);
    const { membersStatus } = useOnlineStore.getState();
    if (membersStatus === "error") {
      try {
        localStorage.removeItem(LAST_ROOM_KEY);
      } catch {
        /* localStorage 不可用时仍可返回大厅 */
      }
      useOnlineStore.setState({ view: "lobby", activeWorldId: null });
    }
  } catch {
    try {
      localStorage.removeItem(LAST_ROOM_KEY);
    } catch {
      /* localStorage 不可用时仍可返回大厅 */
    }
    useOnlineStore.setState({ view: "lobby", activeWorldId: null });
  }
}

export async function leaveRoom(): Promise<boolean> {
  const { activeWorldId, user } = useOnlineStore.getState();
  if (!activeWorldId || !user) {
    await enterLobby();
    return true;
  }
  const scope = captureRequestScope(activeWorldId);
  useOnlineStore.setState({ roomBusy: true, roomError: null });
  try {
    await removeMember(activeWorldId, user.id);
  } catch (error) {
    if (!requestScopeIsCurrent(scope)) return false;
    useOnlineStore.setState({
      roomBusy: false,
      roomError: errorMessage(error, "退出房间失败，请重试"),
    });
    return false;
  }
  if (!requestScopeIsCurrent(scope)) return true;
  try {
    localStorage.removeItem(LAST_ROOM_KEY);
  } catch {
    /* localStorage 不可用不影响服务端已经完成的退出 */
  }
  useOnlineStore.setState({ roomBusy: false });
  await enterLobby();
  return true;
}

/** 认领调查员（按角色列表条目的 id，即 character_key）。 */
export async function claimByKey(characterKey: string): Promise<void> {
  const { activeWorldId } = useOnlineStore.getState();
  if (!activeWorldId) return;
  const scope = captureRequestScope(activeWorldId);
  useOnlineStore.setState({ roomBusy: true, roomError: null });
  try {
    await claimInvestigator(activeWorldId, characterKey);
    if (!requestScopeIsCurrent(scope)) return;
    useOnlineStore.setState({ roomBusy: false });
    await refreshRoom();
  } catch (error) {
    if (!requestScopeIsCurrent(scope)) return;
    useOnlineStore.setState({
      roomBusy: false,
      roomError: errorMessage(error, "认领失败，请重试"),
    });
  }
}

/** 释放已认领的调查员（investigator_id 为认领记录 id）。 */
export async function releaseClaim(investigatorId: string): Promise<void> {
  const { activeWorldId } = useOnlineStore.getState();
  if (!activeWorldId) return;
  const scope = captureRequestScope(activeWorldId);
  useOnlineStore.setState({ roomBusy: true, roomError: null });
  try {
    await releaseInvestigator(activeWorldId, investigatorId);
    if (!requestScopeIsCurrent(scope)) return;
    useOnlineStore.setState({ roomBusy: false });
    await refreshRoom();
  } catch (error) {
    if (!requestScopeIsCurrent(scope)) return;
    useOnlineStore.setState({
      roomBusy: false,
      roomError: errorMessage(error, "释放失败，请重试"),
    });
  }
}

/** 房主修改成员角色。 */
export async function changeMemberRole(
  userId: string,
  role: "player" | "viewer",
): Promise<void> {
  const { activeWorldId } = useOnlineStore.getState();
  if (!activeWorldId) return;
  const scope = captureRequestScope(activeWorldId);
  useOnlineStore.setState({ roomBusy: true, roomError: null });
  try {
    await updateMember(activeWorldId, userId, { role });
    if (!requestScopeIsCurrent(scope)) return;
    useOnlineStore.setState({ roomBusy: false });
    await refreshRoom();
  } catch (error) {
    if (!requestScopeIsCurrent(scope)) return;
    useOnlineStore.setState({
      roomBusy: false,
      roomError: errorMessage(error, "修改失败，请重试"),
    });
  }
}

/** 房主移除成员。 */
export async function kickMember(userId: string): Promise<void> {
  const { activeWorldId } = useOnlineStore.getState();
  if (!activeWorldId) return;
  const scope = captureRequestScope(activeWorldId);
  useOnlineStore.setState({ roomBusy: true, roomError: null });
  try {
    await removeMember(activeWorldId, userId);
    if (!requestScopeIsCurrent(scope)) return;
    useOnlineStore.setState({ roomBusy: false });
    await refreshRoom();
  } catch (error) {
    if (!requestScopeIsCurrent(scope)) return;
    useOnlineStore.setState({
      roomBusy: false,
      roomError: errorMessage(error, "移除失败，请重试"),
    });
  }
}

/** 通过房间 WS 发送准备状态；结果以下一次 room_state 广播为准。 */
export async function toggleReady(ready: boolean): Promise<void> {
  const { activeWorldId } = useOnlineStore.getState();
  if (!activeWorldId) return;
  useOnlineStore.setState({ roomError: null });
  roomSend({ type: "room_ready", ready });
}

export async function newInvite(options: {
  role?: "player" | "viewer";
  expires_in_hours?: number;
  max_uses?: number;
}): Promise<void> {
  const { activeWorldId } = useOnlineStore.getState();
  if (!activeWorldId) return;
  const scope = captureRequestScope(activeWorldId);
  useOnlineStore.setState({ inviteBusy: true, roomError: null });
  try {
    const invite = await createInvite(activeWorldId, options);
    if (!requestScopeIsCurrent(scope)) return;
    useOnlineStore.setState({ invite, inviteBusy: false });
    await refreshInvites(scope);
  } catch (error) {
    if (!requestScopeIsCurrent(scope)) return;
    useOnlineStore.setState({
      inviteBusy: false,
      roomError: errorMessage(error, "创建邀请失败"),
    });
  }
}

/** 刷新邀请元数据列表；非房主无权限时静默置空。 */
async function refreshInvites(expectedScope?: RequestScope): Promise<void> {
  const { activeWorldId, user, members } = useOnlineStore.getState();
  if (!activeWorldId || !user) return;
  const scope = expectedScope ?? captureRequestScope(activeWorldId);
  if (!requestScopeIsCurrent(scope)) return;
  const me = members.find((member) => member.user_id === user.id);
  if (me?.role !== "owner") {
    useOnlineStore.setState({ invites: [] });
    return;
  }
  const requestSerial = ++inviteRequestSerial;
  try {
    const invites = await listInvites(activeWorldId);
    if (
      requestSerial !== inviteRequestSerial ||
      !requestScopeIsCurrent(scope)
    ) {
      return;
    }
    useOnlineStore.setState({ invites: invites ?? [] });
  } catch {
    // 邀请列表读取失败不阻塞房间主流程。
  }
}

/** 撤销指定邀请。 */
export async function revokeInviteById(inviteId: string): Promise<void> {
  const { activeWorldId } = useOnlineStore.getState();
  if (!activeWorldId) return;
  const scope = captureRequestScope(activeWorldId);
  useOnlineStore.setState({ roomBusy: true, roomError: null });
  try {
    await revokeInvite(activeWorldId, inviteId);
    if (!requestScopeIsCurrent(scope)) return;
    useOnlineStore.setState({ roomBusy: false });
    await refreshInvites(scope);
  } catch (error) {
    if (!requestScopeIsCurrent(scope)) return;
    useOnlineStore.setState({
      roomBusy: false,
      roomError: errorMessage(error, "撤销邀请失败"),
    });
  }
}

/** 移交房主。 */
export async function handOverOwnership(userId: string): Promise<void> {
  const { activeWorldId } = useOnlineStore.getState();
  if (!activeWorldId) return;
  const scope = captureRequestScope(activeWorldId);
  useOnlineStore.setState({ roomBusy: true, roomError: null });
  try {
    await transferOwnership(activeWorldId, userId);
    if (!requestScopeIsCurrent(scope)) return;
    useOnlineStore.setState({ roomBusy: false });
    await refreshRoom();
  } catch (error) {
    if (!requestScopeIsCurrent(scope)) return;
    useOnlineStore.setState({
      roomBusy: false,
      roomError: errorMessage(error, "移交房主失败"),
    });
  }
}

export async function dismissInvite(): Promise<void> {
  const { activeWorldId, invite } = useOnlineStore.getState();
  if (!invite?.invite_id) {
    useOnlineStore.setState({ invite: null });
    return;
  }
  const scope = captureRequestScope(activeWorldId);
  useOnlineStore.setState({ roomBusy: true, roomError: null });
  try {
    if (activeWorldId) {
      await revokeInvite(activeWorldId, invite.invite_id);
    }
    if (!requestScopeIsCurrent(scope)) return;
    // 只有 DELETE 成功后才从 UI 移除邀请码。
    useOnlineStore.setState({ invite: null, roomBusy: false });
  } catch (error) {
    if (!requestScopeIsCurrent(scope)) return;
    // 失败保留邀请码并给出明确错误，不吞异常。
    useOnlineStore.setState({
      roomBusy: false,
      roomError: errorMessage(error, "撤销邀请失败，邀请码仍然有效"),
    });
  }
}

/** 房主开局：房间 WS 发送 start（携带幂等 action_id）；拒绝原因由 room_action_rejected 呈现。 */
export async function startGame(): Promise<void> {
  const { activeWorldId } = useOnlineStore.getState();
  if (!activeWorldId) return;
  useOnlineStore.setState({ roomError: null });
  roomSend({ type: "start", action_id: newActionId() });
}

/** 房主指定当前行动者（actor_assign，仅房主）。 */
export async function assignActor(userId: string): Promise<void> {
  const { activeWorldId } = useOnlineStore.getState();
  if (!activeWorldId) return;
  useOnlineStore.setState({ roomError: null });
  roomSend({ type: "actor_assign", user_id: userId });
}
