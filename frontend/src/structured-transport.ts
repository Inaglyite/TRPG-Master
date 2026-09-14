/**
 * structured-transport.ts — 结构化协议的发送/接收适配层。
 *
 * 单品与房间共用这一处：`ws.ts` 与 `room-ws.ts` 只负责把帧送进来
 * （`handleStructuredPayload`）和把帧送出去（它们各自的 `safeSend` 上层），
 * 事件归并、去重、超时与重试全部在这里，避免两套实现走偏。
 *
 * 硬规则：
 * - 协议不可用时不发送、不退回文字（返回 `{ ok:false, reason }` 由 UI 提示）。
 * - 重试重发**同一份载荷与同一个 request_id**，交给服务端幂等去重。
 * - 超时先显示“正在查询原请求状态”，不把“已发送”当成功。
 */

import {
  StructuredEventSequencer,
  actionDigest,
  buildActionRequest,
  buildCheckResponse,
  buildCommandRequest,
  buildFreeRollRequest,
  freeRollReason,
  interactionPath,
  newCommandId,
  buildMemoryQuery,
  newRequestId,
  parseStructuredEvent,
  structuredUnavailableReason,
  type CheckDecision,
  type MemoryQueryFilters,
  type StructuredAction,
  type StructuredEventEnvelope,
} from "./protocol/structured";
import { applyStructuredEffects } from "./structured-effects";
import { useAppStore } from "./state/app-store";
import { useSceneStore } from "./state/scene-store";
import { useOnlineStore } from "./state/online-store";
import {
  requestLabel,
  structuredAvailable,
  useStructuredStore,
  type RequestIntentKind,
} from "./state/structured-store";

/** 等待 action_ack 的窗口；超过则提示“正在查询原请求状态”。 */
export const ACK_TIMEOUT_MS = 8000;
/** 超时后自动重发一次（同 request_id）再等一个窗口。 */
export const RETRY_AFTER_TIMEOUT_MS = 8000;

const sequencer = new StructuredEventSequencer();
const timers = new Map<string, number>();

function nowMs(): number {
  return Date.now();
}

/** 发送原始结构化帧。传输由调用方（ws.ts/room-ws.ts）注入，避免循环依赖。 */
type StructuredSender = (payload: unknown) => boolean;

let sender: StructuredSender | null = null;

/**
 * 注入实际发送函数。`ws.ts` 在模块初始化时注入 `safeSend` 的封装，
 * 房间模式不需要重新注入——`safeSend` 已经会走 activeTransport。
 */
export function setStructuredSender(next: StructuredSender | null): void {
  sender = next;
}

function emit(payload: unknown): boolean {
  if (!sender) return false;
  return sender(payload);
}

function clearTimer(requestId: string): void {
  const timer = timers.get(requestId);
  if (timer !== undefined) {
    globalThis.clearTimeout(timer);
    timers.delete(requestId);
  }
}

/** 超时未收到 ack：标记“正在查询”，并自动同 ID 重发一次。 */
function armAckTimer(requestId: string): void {
  clearTimer(requestId);
  const timer = globalThis.setTimeout(() => {
    timers.delete(requestId);
    const store = useStructuredStore.getState();
    const request = store.requests[requestId];
    if (!request || request.awaitingAck) return;
    store.markAwaitingAck(requestId);
    if (request.sends <= 1) {
      // 同 request_id 重发：服务端按幂等键返回原结果，不会重复结算。
      emit(request.payload);
      store.markSent(requestId);
      armAckTimer(requestId);
    }
  }, ACK_TIMEOUT_MS);
  timers.set(requestId, timer as unknown as number);
}

export type StructuredIdentitySnapshot = {
  worldId: string;
  expectedRevision: number;
  investigatorId: string;
};

/** 服务端投影的身份；缺版本号时 UI 明确提示，不发送 expected_revision=0 顶替。 */
export function currentStructuredIdentity(): StructuredIdentitySnapshot | null {
  const state = useStructuredStore.getState();
  const appWorldId = useAppStore.getState().activeWorldId || "";
  const worldId = state.identity.worldId || appWorldId;
  if (!worldId) return null;
  const online = useOnlineStore.getState();
  // 快照没给出自己的调查员时，用房间成员里“我”认领的 character_key：
  // 它是服务端 REST 的权威投影，与结构化层的标识空间一致（claim 行 id 不是）。
  const ownFromRoom = (() => {
    const uid = online.user?.id;
    if (!uid) return "";
    const member = online.members.find((entry) => entry.user_id === uid);
    return String(member?.investigator?.character_key ?? "");
  })();
  const investigatorId = state.identity.investigatorId || ownFromRoom || "";
  return {
    worldId,
    expectedRevision: state.identity.revision,
    investigatorId,
  };
}

export type StructuredRejection = { ok: false; reason: string };
export type StructuredAcceptance = {
  ok: true;
  requestId: string;
  payload: unknown;
};
export type StructuredSendResult = StructuredAcceptance | StructuredRejection;

function reject(reason: string): StructuredRejection {
  return { ok: false, reason };
}

/**
 * 提交前门禁。`requiresInvestigator=false` 用于主持命令：keeper 的 principal
 * 由服务端按身份解析，人类主持**不需要认领调查员**（规格 §7），
 * 因此不能要求 investigator_id。
 */
function gateReason(requiresInvestigator = true): string | null {
  const state = useStructuredStore.getState();
  const unavailable = structuredUnavailableReason(
    state.capabilities,
    state.protocolNotice,
  );
  if (unavailable) return unavailable;
  const identity = currentStructuredIdentity();
  if (!identity) return "还没有进入世界，无法提交结构化请求。";
  if (requiresInvestigator && !identity.investigatorId)
    return "尚未确定行动调查员，无法提交结构化请求。";
  if (identity.expectedRevision <= 0) {
    return "尚未收到服务端世界版本号，请稍候或刷新后重试。";
  }
  if (useAppStore.getState().connection !== "connected") {
    return "连接已断开，请求未提交。";
  }
  // 不检查 inputEnabled：那是旧回合通道的行动顺序概念。结构化请求进入主持
  // 待办，任何时刻都可以提交，由服务端用 revision 与事务裁决成败。
  return null;
}

function trackAndSend(
  requestId: string,
  kind: RequestIntentKind,
  label: string,
  payload: Record<string, unknown>,
): StructuredSendResult {
  const store = useStructuredStore.getState();
  store.registerOutgoing({
    requestId,
    kind,
    label,
    payload,
    digest: actionDigest(payload),
  });
  if (!emit(payload)) {
    // 发送失败：登记保留为草稿状态，UI 可重试同 ID。
    store.applyRequestError(
      requestId,
      "not_sent",
      "请求未能发出，请重试。",
      true,
    );
    return reject("请求未能发出，请检查连接后重试。");
  }
  store.markSent(requestId);
  armAckTimer(requestId);
  return { ok: true, requestId, payload };
}

/**
 * 自由扮演/交谈/补充做法：结构化世界里它是 `action_request{kind:"freeform"}`，
 * 由主持解释；旧世界里仍然是原来的文字回合。**不把按钮请求拼成文字**，
 * 也不让新模式静默退回旧关键词通道。
 *
 * 返回 null 表示“当前不是结构化世界或协议不可用”，调用方走旧路径。
 */
export function sendFreeformIntent(text: string): StructuredSendResult | null {
  const state = useStructuredStore.getState();
  if (interactionPath(state.capabilities) !== "structured") return null;
  const body = text.trim();
  if (!body) return reject("请输入你想做的事。");
  const action = { kind: "freeform" as const, text: body.slice(0, 2000) };
  // 协议不可用时这里会返回明确的拒绝原因，调用方必须原样展示，不能改走文字。
  return sendStructuredAction(action, `行动：${body.slice(0, 24)}`);
}

/** 提交玩家结构化行动（出示/使用/前往/自由文本补充）。 */
export function sendStructuredAction(
  action: StructuredAction,
  label?: string,
): StructuredSendResult {
  const blocked = gateReason();
  if (blocked) return reject(blocked);
  const identity = currentStructuredIdentity();
  if (!identity) return reject("还没有进入世界，无法提交结构化请求。");
  const built = buildActionRequest(action, identity);
  if (!built.ok) return reject(built.reason);
  const request = built.request;
  return trackAndSend(
    request.request_id,
    action.kind,
    label ?? requestLabel(action.kind),
    request as unknown as Record<string, unknown>,
  );
}

/** 普通掷骰：独立请求，不消耗模型额度、不触发剧情分支。 */
export function sendFreeRoll(spec: string): StructuredSendResult {
  // 受限表达式在本地先校验：非法骰式不该占用一次服务端往返。
  const invalid = freeRollReason(spec);
  if (invalid) return reject(invalid);
  const blocked = gateReason();
  if (blocked) return reject(blocked);
  const identity = currentStructuredIdentity();
  if (!identity) return reject("还没有进入世界，无法掷骰。");
  const built = buildFreeRollRequest(spec.trim(), identity);
  if (!built.ok) return reject(built.reason);
  const request = built.request;
  return trackAndSend(
    request.request_id,
    "free_roll",
    `普通掷骰 ${spec.trim()}`,
    request as unknown as Record<string, unknown>,
  );
}

/**
 * M5：主持侧只读记忆查询。玩家侧不发起（能力与身份双重门槛，服务端仍会拒）；
 * 结果以 `memory_query_result`（keeper 定向）回来，只进主持台，不进玩家视图。
 */
export function sendMemoryQuery(
  filters: MemoryQueryFilters = {},
): StructuredSendResult {
  const state = useStructuredStore.getState();
  if (interactionPath(state.capabilities) !== "structured") {
    return reject("当前世界不使用结构化协议，无法查询记忆。");
  }
  if (!state.capabilities.memoryQuery) return reject("服务端未开放记忆查询。");
  if (
    state.identity.keeperMode === "" ||
    state.identity.keeperMode === undefined
  ) {
    return reject("还没有确定主持身份。");
  }
  const blocked = gateReason(false);
  if (blocked) return reject(blocked);
  const queryId = newRequestId();
  const frame = buildMemoryQuery(queryId, filters);
  // 世界标识由传输层补齐（帧构造器只管业务字段，与其它 builder 一致）。
  const payload: Record<string, unknown> = {
    ...frame,
    world_id:
      state.identity.worldId || useAppStore.getState().activeWorldId || "",
  };
  useStructuredStore
    .getState()
    .beginMemoryQuery(
      queryId,
      (frame.filters as Record<string, unknown>) ?? {},
    );
  if (!emit(payload)) {
    useStructuredStore
      .getState()
      .applyMemoryQueryError("查询未能发出，请检查连接后重试。");
    return reject("查询未能发出，请检查连接后重试。");
  }
  return { ok: true, requestId: queryId, payload };
}

/** 检定回应：参数全部在服务端，这里只提交掷骰/放弃。 */
export function sendCheckResponse(
  checkRequestId: string,
  decision: CheckDecision,
): StructuredSendResult {
  const blocked = gateReason();
  if (blocked) return reject(blocked);
  const identity = currentStructuredIdentity();
  if (!identity) return reject("还没有进入世界。");
  const store = useStructuredStore.getState();
  const resolved = store.resolveCheck(checkRequestId, decision, identity);
  if (!resolved) {
    return reject("该检定不属于你或已结束，无法响应。");
  }
  const built = buildCheckResponse(checkRequestId, decision, identity);
  if (!built.ok) return reject(built.reason);
  const request = built.request;
  return trackAndSend(
    request.request_id,
    "check_response",
    decision === "roll" ? "掷骰" : "放弃检定",
    request as unknown as Record<string, unknown>,
  );
}

/** 主持命令（KeeperConsole）。命令 ID 由调用方决定，便于 harness 重试。 */
export function sendKeeperCommand(
  kind: string,
  payload: Record<string, unknown>,
  commandId?: string,
): StructuredSendResult {
  const blocked = gateReason(false);
  if (blocked) return reject(blocked);
  const identity = currentStructuredIdentity();
  if (!identity) return reject("还没有进入世界。");
  const built = buildCommandRequest(kind, payload, identity, commandId);
  if (!built.ok) return reject(built.reason);
  const request = built.request;
  return trackAndSend(
    request.command_id,
    "command",
    `主持操作：${kind}`,
    request as unknown as Record<string, unknown>,
  );
}

/**
 * `revision_conflict` 的恢复：世界已经前进，同 ID 重发同一份载荷只会再撞一次。
 * 规格要求“刷新后用户重新提交”，所以这里用**当前** revision 与新 request_id
 * 重发同一个意图（原请求随之作废，不产生第二次副作用）。
 * 返回 null 表示没有可取的新版本（仍停留在冲突版本）。
 */
export function resubmitWithFreshRevision(
  requestId: string,
): StructuredSendResult | null {
  const store = useStructuredStore.getState();
  const request = store.requests[requestId];
  if (!request) return reject("没有找到原请求。");
  const identity = currentStructuredIdentity();
  if (!identity) return reject("还没有进入世界。");
  const payload = request.payload as { expected_revision?: number } | undefined;
  const staleRevision =
    payload && typeof payload.expected_revision === "number"
      ? payload.expected_revision
      : undefined;
  if (
    staleRevision === undefined ||
    identity.expectedRevision === staleRevision
  ) {
    // 版本没变：同 ID 重试即可，交给调用方走 resend。
    return null;
  }
  const blocked = gateReason(false);
  if (blocked) return reject(blocked);
  const rebuilt = { ...(request.payload as Record<string, unknown>) };
  rebuilt.expected_revision = identity.expectedRevision;
  if (typeof rebuilt.request_id === "string") {
    rebuilt.request_id = newRequestId();
  }
  if (typeof rebuilt.command_id === "string") {
    rebuilt.command_id = newCommandId();
  }
  const nextId = String(rebuilt.request_id ?? rebuilt.command_id ?? "");
  if (!nextId) return reject("原请求缺少标识，无法重新提交。");
  // 原请求标记为已作废，避免它继续留在待办里误导玩家。
  store.applyRequestError(
    requestId,
    "superseded",
    "已用最新世界版本重新提交。",
    false,
  );
  return trackAndSend(nextId, request.kind, request.label, rebuilt);
}

/**
 * 超时后重新发送原请求：载荷与 request_id 都不变。
 * 服务端按幂等键返回已保存的结果，不会重复结算。
 */
export function resendStructuredRequest(
  requestId: string,
): StructuredSendResult {
  const store = useStructuredStore.getState();
  const request = store.requests[requestId];
  if (!request) return reject("没有找到原请求。");
  if (!emit(request.payload)) return reject("请求未能发出，请检查连接后重试。");
  store.noteRetry(requestId);
  store.markSent(requestId);
  armAckTimer(requestId);
  return { ok: true, requestId, payload: request.payload };
}

/** 丢弃一个未发出去的请求草稿（例如目标已失效）。 */
export function discardStructuredRequest(requestId: string): void {
  clearTimer(requestId);
  const store = useStructuredStore.getState();
  const request = store.requests[requestId];
  if (!request) return;
  store.applyRequestError(requestId, "discarded", "请求已取消。", false);
}

// ---------------------------------------------------------------------------
// 接收
// ---------------------------------------------------------------------------

export type StructuredInbound =
  | { kind: "applied"; envelope: StructuredEventEnvelope }
  | { kind: "duplicate"; envelope: StructuredEventEnvelope }
  | { kind: "foreign_world"; envelope: StructuredEventEnvelope }
  | { kind: "protocol_mismatch" }
  | { kind: "ignored" };

/**
 * 处理一条可能是结构化事件的服务端帧。
 *
 * 顺序：解析 → 世界/事件游标 → 更新 store。
 * 去重只看 event_id；同 revision 的不同事件都会应用。
 */
export function handleStructuredPayload(raw: unknown): StructuredInbound {
  const parsed = parseStructuredEvent(raw);
  if (!parsed) return { kind: "ignored" };
  if ("mismatch" in parsed) {
    useStructuredStore
      .getState()
      .setProtocolNotice(
        "服务端使用不同版本的协议；已停止结构化提交，请更新客户端或服务端。",
      );
    return { kind: "protocol_mismatch" };
  }
  const envelope = parsed.envelope;
  // 快照是世界的权威标识来源：换世界时先重绑游标与地点，
  // 否则新世界的快照会被当成“旧世界迟到事件”丢弃。
  if (
    envelope.type === "session_snapshot" &&
    sequencer.boundWorldId &&
    sequencer.boundWorldId !== envelope.world_id
  ) {
    rebindStructuredWorld(envelope.world_id);
  }
  const verdict = sequencer.accept({
    ...envelope,
    isSnapshot: envelope.type === "session_snapshot",
  });
  if (verdict === "duplicate") return { kind: "duplicate", envelope };
  if (verdict === "foreign_world") return { kind: "foreign_world", envelope };
  if (
    envelope.type === "action_ack" ||
    envelope.type === "action_status" ||
    envelope.type === "request_error"
  ) {
    const requestId =
      (typeof envelope.payload.request_id === "string"
        ? envelope.payload.request_id
        : "") ||
      envelope.cause_request_id ||
      "";
    if (requestId) clearTimer(requestId);
  }
  useStructuredStore.getState().applyEvent(envelope);
  // 通过游标校验后，再把事件投影到既有 UI（消息、骰子动画、场景、角色数值）。
  applyStructuredEffects(envelope);
  return { kind: "applied", envelope };
}

/** 世界切换时重绑游标，旧世界迟到事件随后被判为 foreign_world。 */
export function rebindStructuredWorld(worldId: string): void {
  sequencer.rebindWorld(worldId || null);
  for (const requestId of timers.keys()) clearTimer(requestId);
  useStructuredStore.getState().bindWorld(worldId);
  // 场景指示器同样换绑：新世界的位置不能建立在旧世界的地点之上。
  useSceneStore.getState().setWorld(worldId);
}

/** 测试与登出用：清空游标与计时器。 */
export function resetStructuredTransport(): void {
  sequencer.reset();
  for (const requestId of timers.keys()) clearTimer(requestId);
}

export function structuredCursor(): {
  worldId: string | null;
  eventId: number;
  revision: number;
  sequence: number;
} {
  const cursor = sequencer.cursor;
  return {
    worldId: sequencer.boundWorldId,
    eventId: cursor.eventId,
    revision: cursor.revision,
    sequence: cursor.sequence,
  };
}

/** 供 UI 显示“正在查询原请求状态”用的时间戳（毫秒）。 */
export function transportNow(): number {
  return nowMs();
}
