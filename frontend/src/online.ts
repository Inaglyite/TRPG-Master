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
import { newActionId, roomSend } from "./room-ws";
import { resetOnlineState, useOnlineStore } from "./state/online-store";

function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (error.isNetwork) return error.message;
    if (error.status === 429) return "尝试过于频繁，请稍后再试";
    return error.message;
  }
  return fallback;
}

/** 契约未实现的接口返回 404/405/501 时，界面应进入“等待后端接口”状态而非报错。 */
function isUnsupported(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    (error.status === 404 || error.status === 405 || error.status === 501)
  );
}

// —— 认证状态机 ——

export async function checkSession(): Promise<void> {
  useOnlineStore.setState({ authStatus: "checking", authError: null });
  try {
    const user = await fetchMe();
    useOnlineStore.setState({
      authStatus: "authenticated",
      user,
      sessionExpired: false,
    });
  } catch (error) {
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
  useOnlineStore.setState({
    authBusy: true,
    authError: null,
    sessionExpired: false,
  });
  try {
    const user = await apiLogin(username, password);
    useOnlineStore.setState({
      authBusy: false,
      authStatus: "authenticated",
      user,
    });
    return true;
  } catch (error) {
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
  useOnlineStore.setState({
    authBusy: true,
    authError: null,
    sessionExpired: false,
  });
  try {
    const user = await registerAccount(username, password);
    useOnlineStore.setState({
      authBusy: false,
      authStatus: "authenticated",
      user,
    });
    return true;
  } catch (error) {
    useOnlineStore.setState({
      authStatus: "anonymous",
      authBusy: false,
      authError: errorMessage(error, "注册失败，请重试"),
    });
    return false;
  }
}

export async function logout(): Promise<boolean> {
  useOnlineStore.setState({ authBusy: true, authError: null });
  try {
    await apiLogout();
  } catch (error) {
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
  resetOnlineState();
  return true;
}

/** 订阅云端 API 的全部 401；已认证状态下降级为“会话过期”。返回取消订阅函数。 */
export function initOnlineSession(): () => void {
  return onUnauthorized(() => {
    const state = useOnlineStore.getState();
    if (state.authStatus === "authenticated") {
      resetOnlineState({ sessionExpired: true });
    }
  });
}

// —— 大厅 ——

export async function refreshWorlds(): Promise<void> {
  useOnlineStore.setState({ worldsStatus: "loading", worldsError: null });
  try {
    const worlds = await listWorlds();
    useOnlineStore.setState({ worlds: worlds ?? [], worldsStatus: "ready" });
  } catch (error) {
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
  useOnlineStore.setState({
    view: "lobby",
    activeWorldId: null,
    roomModule: null,
    roomMetadata: null,
    invite: null,
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
  useOnlineStore.setState({ createBusy: true, createError: null });
  try {
    const world = await createWorld(module, {
      ...(name.trim() ? { name: name.trim() } : {}),
      max_players: maxPlayers,
    });
    useOnlineStore.setState({ createBusy: false });
    await enterRoom(world.world_id);
  } catch (error) {
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
  useOnlineStore.setState({ joinBusy: true, joinError: null });
  try {
    const accepted = await acceptInvite(trimmed);
    useOnlineStore.setState({ joinBusy: false });
    await enterRoom(accepted.world_id);
  } catch (error) {
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
  try {
    localStorage.setItem(LAST_ROOM_KEY, worldId);
  } catch {
    /* localStorage 不可用时忽略 */
  }
  useOnlineStore.setState({
    view: "room",
    activeWorldId: worldId,
    membersStatus: "loading",
    membersError: null,
    characterOptions: [],
    charactersStatus: "idle",
    invite: null,
    invites: [],
    privateEvents: [],
    roomError: null,
  });
  await refreshRoom();
}

export async function refreshRoom(): Promise<void> {
  const { activeWorldId } = useOnlineStore.getState();
  if (!activeWorldId) return;
  try {
    const info = await getRoomInfo(activeWorldId);
    useOnlineStore.setState({
      members: info.members ?? [],
      membersStatus: "ready",
      roomModule: info.module ?? null,
      roomMetadata: info.metadata ?? null,
    });
    if (info.module) await loadCharacters(activeWorldId);
    await refreshInvites();
  } catch (error) {
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
async function loadCharacters(worldId: string): Promise<void> {
  const { charactersStatus } = useOnlineStore.getState();
  if (charactersStatus === "loading" || charactersStatus === "ready") return;
  useOnlineStore.setState({ charactersStatus: "loading" });
  try {
    const data = await getInvestigatorOptions(worldId);
    const options = (data.groups ?? []).flatMap(
      (group) => group.characters ?? [],
    );
    useOnlineStore.setState({
      characterOptions: options,
      charactersStatus: "ready",
    });
  } catch (error) {
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
  useOnlineStore.setState({ roomBusy: true, roomError: null });
  try {
    await removeMember(activeWorldId, user.id);
  } catch (error) {
    useOnlineStore.setState({
      roomBusy: false,
      roomError: errorMessage(error, "退出房间失败，请重试"),
    });
    return false;
  }
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
  useOnlineStore.setState({ roomBusy: true, roomError: null });
  try {
    await claimInvestigator(activeWorldId, characterKey);
    useOnlineStore.setState({ roomBusy: false });
    await refreshRoom();
  } catch (error) {
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
  useOnlineStore.setState({ roomBusy: true, roomError: null });
  try {
    await releaseInvestigator(activeWorldId, investigatorId);
    useOnlineStore.setState({ roomBusy: false });
    await refreshRoom();
  } catch (error) {
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
  useOnlineStore.setState({ roomBusy: true, roomError: null });
  try {
    await updateMember(activeWorldId, userId, { role });
    useOnlineStore.setState({ roomBusy: false });
    await refreshRoom();
  } catch (error) {
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
  useOnlineStore.setState({ roomBusy: true, roomError: null });
  try {
    await removeMember(activeWorldId, userId);
    useOnlineStore.setState({ roomBusy: false });
    await refreshRoom();
  } catch (error) {
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
  useOnlineStore.setState({ inviteBusy: true, roomError: null });
  try {
    const invite = await createInvite(activeWorldId, options);
    useOnlineStore.setState({ invite, inviteBusy: false });
    await refreshInvites();
  } catch (error) {
    useOnlineStore.setState({
      inviteBusy: false,
      roomError: errorMessage(error, "创建邀请失败"),
    });
  }
}

/** 刷新邀请元数据列表；非房主无权限时静默置空。 */
async function refreshInvites(): Promise<void> {
  const { activeWorldId, user, members } = useOnlineStore.getState();
  if (!activeWorldId || !user) return;
  const me = members.find((member) => member.user_id === user.id);
  if (me?.role !== "owner") {
    useOnlineStore.setState({ invites: [] });
    return;
  }
  try {
    const invites = await listInvites(activeWorldId);
    useOnlineStore.setState({ invites: invites ?? [] });
  } catch {
    // 邀请列表读取失败不阻塞房间主流程。
  }
}

/** 撤销指定邀请。 */
export async function revokeInviteById(inviteId: string): Promise<void> {
  const { activeWorldId } = useOnlineStore.getState();
  if (!activeWorldId) return;
  useOnlineStore.setState({ roomBusy: true, roomError: null });
  try {
    await revokeInvite(activeWorldId, inviteId);
    useOnlineStore.setState({ roomBusy: false });
    await refreshInvites();
  } catch (error) {
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
  useOnlineStore.setState({ roomBusy: true, roomError: null });
  try {
    await transferOwnership(activeWorldId, userId);
    useOnlineStore.setState({ roomBusy: false });
    await refreshRoom();
  } catch (error) {
    useOnlineStore.setState({
      roomBusy: false,
      roomError: errorMessage(error, "移交房主失败"),
    });
  }
}

export async function dismissInvite(): Promise<void> {
  const { activeWorldId, invite } = useOnlineStore.getState();
  useOnlineStore.setState({ invite: null });
  if (activeWorldId && invite?.invite_id) {
    try {
      await revokeInvite(activeWorldId, invite.invite_id);
    } catch {
      // 撤销失败不阻塞界面；邀请会自行过期。
    }
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
