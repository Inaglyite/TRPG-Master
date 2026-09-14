/**
 * structured-store.ts — 结构化操作的前端状态。
 *
 * 只保存“服务端告诉我什么”和“我提交了什么、还没收到什么”：
 * - 不判断行动成功（领域结果来自服务端事件）；
 * - 不在前端扣减数量、不推断场景变化；
 * - 所有候选（目标、目的地、物品可用操作）都来自服务端公开投影，
 *   前端不自己拼候选，也不从正文里猜。
 *
 * 世界切换（bindWorld）会清空请求与待检定绑定：旧回调不能污染新世界。
 */

import { create } from "zustand";

import {
  ACTION_STATUSES,
  NO_STRUCTURED_CAPABILITIES,
  isTerminalActionStatus,
  parseStructuredEvent,
  readInteractionThread,
  readMemoryEntry,
  readServerCapabilities,
  requestErrorText,
  type ActionStatusKind,
  type DomainOutcome,
  type InteractionThread,
  type MemoryEntry,
  type ServerCapabilities,
  type StructuredAction,
  type StructuredEventEnvelope,
  type TargetKind,
} from "../protocol/structured";

export type PublicTarget = { kind: TargetKind; id: string; name: string };

/**
 * 主持专属资料条目。M0 的 `session_snapshot` **没有定义**这个字段；
 * 前端按可选字段读取：服务端给了就显示，没给就明确说明“未提供”，
 * 绝不把玩家可见投影包装成“主持秘密”。字段名已列入前端契约文档待 M1 确认。
 */
export type KeeperMaterialEntry = { title: string; text: string };
export type PublicDestination = { id: string; name: string };

export type ClueOption = {
  id: string;
  category: string;
  text: string;
  /** 该线索当前允许的出示方式（服务端投影，前端不自行放宽）。 */
  presentation: string[];
  /** 可作为「展示原件」的关联物品 ID；服务端未声明时为空。 */
  allowedPhysicalItemIds: string[];
  assetLabel?: string;
};

export type ItemOption = {
  id: string;
  label: string;
  quantity: number;
  /** 服务端声明支持的用法；即席做法走 custom。 */
  operations: string[];
};

export type RequestIntentKind =
  StructuredAction["kind"] | "free_roll" | "check_response" | "command";

/** awaiting_player 的公开待办：尚未执行的行动 + 已告知条件（不含主持秘密）。 */
export type AwaitingTodo = {
  /** 尚未执行的行动类型（move/freeform/present_clue/use_item/other）。 */
  kind: string;
  /** 尚未执行什么（人话描述）。 */
  note: string;
  target: string;
  destinationSceneId: string;
  /** 本轮已告知玩家的重要条件：主持不应重复劝留。 */
  disclosed: string[];
  /** 为什么在等待（可选）。 */
  reason: string;
};

export type PendingRequest = {
  requestId: string;
  kind: RequestIntentKind;
  label: string;
  status: ActionStatusKind;
  /** 仅 status=awaiting_player 时有值：把「尚未执行」显示清楚，位置不受影响。 */
  awaiting: AwaitingTodo | null;
  outcome: DomainOutcome | null;
  detail: string;
  errorCode: string | null;
  errorMessage: string | null;
  /** 原样保留的请求体：重试必须重发同一份载荷与同一个 request_id。 */
  payload: unknown;
  digest: string;
  sends: number;
  createdAt: number;
  updatedAt: number;
  /** 超时未收到任何服务端事件：UI 显示“正在查询原请求状态”，而不是假装成功。 */
  awaitingAck: boolean;
};

export type MemoryQueryState = {
  status: "idle" | "querying" | "done" | "failed";
  queryId: string;
  entries: MemoryEntry[];
  truncated: boolean;
  error: string;
  /** 查询条件摘要（主持台显示“本轮选用了哪些来源/过滤”）。 */
  filters: Record<string, unknown>;
};

export type CheckRequestState = {
  checkRequestId: string;
  investigatorId: string;
  skill: string;
  difficulty: string;
  bonusPenalty: number;
  attempt: string;
  knownCost: string;
  visibility: string;
  status: "pending" | "resolved" | "declined" | "cancelled" | "expired";
  result: {
    targetValue: number | null;
    roll: number | null;
    outcome: string;
    detail: string;
  } | null;
  updatedAt: number;
};

/** assisted 模式：主持草稿（仅主持可见），人类批准后才执行。 */
export type KeeperDraft = {
  draftId: string;
  summary: string;
  /** 草稿提议的命令（批准即按冻结契约提交这一条命令）。 */
  command: { kind: string; payload: Record<string, unknown> } | null;
  /** 处理草稿时附带的说明（resolve_draft 的 note）。 */
  note: string;
  createdAt: number;
};

/** agent/assisted 控制权与预算状态（暂停、接管、超预算）。 */
export type KeeperControl = {
  state: "active" | "paused" | "takeover" | "budget_exceeded" | "unavailable";
  detail: string;
  /** 是否可申请接管；服务端未声明时为 false。 */
  takeoverAvailable: boolean;
  updatedAt: number;
};

export type Identity = {
  worldId: string;
  revision: number;
  investigatorId: string;
  keeperUserId: string | null;
  keeperMode: string | null;
};

const EMPTY_IDENTITY: Identity = {
  worldId: "",
  revision: 0,
  investigatorId: "",
  keeperUserId: null,
  keeperMode: null,
};

function str(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function num(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function bool(value: unknown): boolean {
  return value === true;
}

export function asStatus(value: unknown): ActionStatusKind {
  return (ACTION_STATUSES as readonly string[]).includes(str(value))
    ? (str(value) as ActionStatusKind)
    : "processing";
}

export function readAwaitingTodo(value: unknown): AwaitingTodo | null {
  if (!value || typeof value !== "object") return null;
  const record = value as Record<string, unknown>;
  const action = (
    record.pending_action && typeof record.pending_action === "object"
      ? record.pending_action
      : {}
  ) as Record<string, unknown>;
  const disclosed = Array.isArray(record.disclosed)
    ? record.disclosed.flatMap((item) => {
        const text = str(item).trim();
        return text ? [text] : [];
      })
    : [];
  const todo: AwaitingTodo = {
    kind: str(action.kind) || "other",
    note: str(action.note),
    target: str(action.target),
    destinationSceneId: str(action.destination_scene_id),
    disclosed,
    reason: str(record.note),
  };
  if (
    !todo.note &&
    !todo.target &&
    !todo.destinationSceneId &&
    !disclosed.length
  )
    return null;
  return todo;
}

export function asOutcome(value: unknown): DomainOutcome | null {
  const text = str(value);
  return text === "success" || text === "failure" || text === "not_executed"
    ? text
    : null;
}

function readTargets(value: unknown): PublicTarget[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry) => {
    if (!entry || typeof entry !== "object") return [];
    const record = entry as Record<string, unknown>;
    const id = str(record.id);
    const kind = str(record.kind);
    if (!id) return [];
    if (kind !== "npc" && kind !== "investigator" && kind !== "scene_object") {
      return [];
    }
    return [{ kind: kind as TargetKind, id, name: str(record.name, id) }];
  });
}

function readDestinations(value: unknown): PublicDestination[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry) => {
    if (!entry || typeof entry !== "object") return [];
    const record = entry as Record<string, unknown>;
    const id = str(record.id);
    if (!id) return [];
    return [{ id, name: str(record.name, id) }];
  });
}

function readClues(value: unknown): ClueOption[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry) => {
    if (!entry || typeof entry !== "object") return [];
    const record = entry as Record<string, unknown>;
    const id = str(record.id);
    if (!id) return [];
    const presentation = Array.isArray(record.presentation)
      ? record.presentation.filter(
          (item): item is string => typeof item === "string",
        )
      : ["describe"];
    const allowedPhysicalItemIds = Array.isArray(
      record.allowed_physical_item_ids,
    )
      ? record.allowed_physical_item_ids.filter(
          (item): item is string => typeof item === "string",
        )
      : [];
    return [
      {
        id,
        category: str(record.category, "investigation"),
        text: str(record.text),
        presentation,
        allowedPhysicalItemIds,
        assetLabel: str(record.asset_label) || undefined,
      },
    ];
  });
}

function readItems(value: unknown): ItemOption[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry) => {
    if (!entry || typeof entry !== "object") return [];
    const record = entry as Record<string, unknown>;
    const id = str(record.id);
    if (!id) return [];
    const operations = Array.isArray(record.operations)
      ? record.operations.filter(
          (item): item is string => typeof item === "string",
        )
      : [];
    return [
      {
        id,
        label: str(record.label, id),
        quantity: Math.max(0, num(record.quantity, 0)),
        operations,
      },
    ];
  });
}

function readKeeperMaterial(value: unknown): KeeperMaterialEntry[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry) => {
    if (!entry || typeof entry !== "object") return [];
    const record = entry as Record<string, unknown>;
    const txt = str(record.text);
    if (!txt) return [];
    return [{ title: str(record.title, "主持资料"), text: txt }];
  });
}

function readCheckRequest(
  payload: Record<string, unknown>,
): CheckRequestState | null {
  const checkRequestId = str(payload.check_request_id);
  if (!checkRequestId) return null;
  return {
    checkRequestId,
    investigatorId: str(payload.investigator_id),
    skill: str(payload.skill),
    difficulty: str(payload.difficulty),
    bonusPenalty: num(payload.bonus_penalty, 0),
    attempt: str(payload.attempt),
    knownCost: str(payload.known_cost),
    visibility: str(payload.visibility, "public"),
    status: "pending",
    result: null,
    updatedAt: Date.now(),
  };
}

function applyRequestUpdate(
  request: PendingRequest,
  payload: Record<string, unknown>,
  now: number,
): PendingRequest {
  const status = asStatus(payload.status);
  const parsed = readAwaitingTodo(payload.awaiting);
  // 终态时清掉待办：已收尾的请求不能留着「尚未执行」的指纹。
  const awaiting =
    parsed ?? (isTerminalActionStatus(status) ? null : request.awaiting);
  return {
    ...request,
    status,
    awaiting,
    outcome: asOutcome(payload.outcome) ?? request.outcome,
    detail: str(payload.detail) || request.detail,
    awaitingAck: false,
    updatedAt: now,
  };
}

type StructuredState = {
  capabilities: ServerCapabilities;
  /** 明确的协议不可用提示；有值时 UI 禁用新入口并禁止走旧文字通道。 */
  protocolNotice: string | null;
  identity: Identity;
  targets: PublicTarget[];
  destinations: PublicDestination[];
  clues: ClueOption[];
  items: ItemOption[];
  /** 主持资料（服务端可选下发；缺省时控制台显示“未提供”）。 */
  keeperMaterial: KeeperMaterialEntry[];
  requests: Record<string, PendingRequest>;
  requestOrder: string[];
  checks: Record<string, CheckRequestState>;
  checkOrder: string[];
  /** assisted 草稿（仅主持可见）。 */
  keeperDraft: KeeperDraft | null;
  /** M5：交互线程（当前讨论中的目标/未执行行动）的公开投影，按 thread_id 归位。 */
  interactions: Record<string, InteractionThread>;
  interactionOrder: string[];
  /** M5：主持侧只读记忆查询的最近一次结果（玩家侧不发起、也收不到）。 */
  memoryQuery: MemoryQueryState;

  /** 未适配的结构化事件类型（服务端先落地的新事件；前端如实提示，而不是静默丢弃）。 */
  unknownEventTypes: string[];

  /** 主持控制权状态。 */
  keeperControl: KeeperControl | null;
  lastEventAt: number;
};

type StructuredActions = {
  applyCapabilities: (value: unknown) => void;
  setProtocolNotice: (notice: string | null) => void;
  bindWorld: (worldId: string, revision?: number) => void;
  setInvestigator: (investigatorId: string) => void;
  setRevision: (revision: number) => void;
  applySnapshot: (payload: Record<string, unknown>, worldId?: string) => void;
  applyEvent: (envelope: StructuredEventEnvelope) => void;
  registerOutgoing: (input: {
    requestId: string;
    kind: RequestIntentKind;
    label: string;
    payload: unknown;
    digest: string;
  }) => void;
  markSent: (requestId: string) => void;
  markAwaitingAck: (requestId: string) => void;
  noteRetry: (requestId: string) => void;
  applyRequestError: (
    requestId: string | null,
    code: string,
    message: string,
    retryable: boolean,
  ) => void;
  resolveCheck: (
    checkRequestId: string,
    decision: "roll" | "decline",
    identity: Pick<Identity, "worldId" | "investigatorId">,
  ) => CheckRequestState | null;
  clearFinished: () => void;
  clearKeeperDraft: () => void;
  noteUnknownEventType: (type: string) => void;
  upsertInteraction: (thread: InteractionThread) => void;
  applyInteractions: (threads: InteractionThread[]) => void;
  beginMemoryQuery: (queryId: string, filters: Record<string, unknown>) => void;
  applyMemoryQueryResult: (payload: Record<string, unknown>) => void;
  applyMemoryQueryError: (message: string) => void;
  reset: () => void;
};

export const initialStructuredState: StructuredState = {
  capabilities: { ...NO_STRUCTURED_CAPABILITIES },
  protocolNotice: null,
  identity: { ...EMPTY_IDENTITY },
  targets: [],
  destinations: [],
  clues: [],
  items: [],
  keeperMaterial: [],
  requests: {},
  requestOrder: [],
  checks: {},
  checkOrder: [],
  keeperDraft: null,
  keeperControl: null,
  interactions: {},
  interactionOrder: [],
  memoryQuery: {
    status: "idle",
    queryId: "",
    entries: [],
    truncated: false,
    error: "",
    filters: {},
  },
  unknownEventTypes: [],
  lastEventAt: 0,
};

function dropRequest(
  state: StructuredState,
  requestId: string,
): Pick<StructuredState, "requests" | "requestOrder"> {
  const requests = { ...state.requests };
  delete requests[requestId];
  return {
    requests,
    requestOrder: state.requestOrder.filter((id) => id !== requestId),
  };
}

export const useStructuredStore = create<StructuredState & StructuredActions>(
  (set, get) => ({
    ...initialStructuredState,

    applyCapabilities: (value) =>
      set({ capabilities: readServerCapabilities(value) }),

    setProtocolNotice: (notice) => set({ protocolNotice: notice }),

    bindWorld: (worldId, revision) =>
      set((state) => {
        const id = str(worldId);
        if (!id || id === state.identity.worldId) {
          return revision === undefined
            ? state
            : { identity: { ...state.identity, revision } };
        }
        // 换世界：清空请求/待检定/候选绑定，旧事件随后由游标拒收。
        return {
          identity: { ...state.identity, worldId: id, revision: revision ?? 0 },
          targets: [],
          destinations: [],
          clues: [],
          items: [],
          keeperMaterial: [],
          requests: {},
          requestOrder: [],
          checks: {},
          checkOrder: [],
          keeperDraft: null,
          keeperControl: null,
          protocolNotice: null,
        };
      }),

    setInvestigator: (investigatorId) =>
      set((state) => ({
        identity: { ...state.identity, investigatorId: str(investigatorId) },
      })),

    setRevision: (revision) =>
      set((state) => ({ identity: { ...state.identity, revision } })),

    applySnapshot: (payload, worldId) =>
      set((state) => {
        const nextWorldId =
          str(worldId) || str(payload.world_id) || state.identity.worldId;
        const revision = num(payload.revision, state.identity.revision);
        const keeper = (payload.keeper ?? {}) as Record<string, unknown>;
        const checks = {
          ...(nextWorldId === state.identity.worldId ? state.checks : {}),
        };
        const checkOrder = [
          ...(nextWorldId === state.identity.worldId ? state.checkOrder : []),
        ];
        const pendingChecks = Array.isArray(payload.pending_checks)
          ? payload.pending_checks
          : [];
        for (const entry of pendingChecks) {
          if (!entry || typeof entry !== "object") continue;
          const parsed = readCheckRequest(entry as Record<string, unknown>);
          if (!parsed) continue;
          if (!checks[parsed.checkRequestId])
            checkOrder.push(parsed.checkRequestId);
          checks[parsed.checkRequestId] = {
            ...checks[parsed.checkRequestId],
            ...parsed,
          };
        }
        // 快照里已不在 pending 列表中的待检定视为已结束（服务端是权威）。
        const pendingIds = new Set(
          pendingChecks.flatMap((entry) => {
            if (!entry || typeof entry !== "object") return [];
            const id = str((entry as Record<string, unknown>).check_request_id);
            return id ? [id] : [];
          }),
        );
        for (const id of Object.keys(checks)) {
          if (checks[id].status === "pending" && !pendingIds.has(id)) {
            checks[id] = {
              ...checks[id],
              status: "expired",
              updatedAt: Date.now(),
            };
          }
        }
        // 快照里的待办（含 awaiting_player 的「尚未执行」）必须恢复：刷新/重连后
        // 玩家仍要知道自己在等的这件事，而不是只剩一个空聊天区。
        const requests =
          nextWorldId === state.identity.worldId ? { ...state.requests } : {};
        const requestOrder =
          nextWorldId === state.identity.worldId ? [...state.requestOrder] : [];
        const snapshotRequests = Array.isArray(payload.requests)
          ? payload.requests
          : [];
        for (const entry of snapshotRequests) {
          if (!entry || typeof entry !== "object") continue;
          const record = entry as Record<string, unknown>;
          const id = str(record.request_id);
          if (!id) continue;
          const status = asStatus(record.status ?? "queued");
          const awaiting = readAwaitingTodo(record.awaiting);
          const existing = requests[id];
          if (!existing) {
            requestOrder.push(id);
            requests[id] = {
              requestId: id,
              kind: snapshotIntentKind(awaiting?.kind),
              label: str(record.summary) || "守秘人待办",
              status,
              awaiting,
              outcome: null,
              detail: "",
              errorCode: null,
              errorMessage: null,
              payload: null,
              digest: "",
              sends: 0,
              createdAt: Date.now(),
              updatedAt: Date.now(),
              awaitingAck: false,
            };
            continue;
          }
          requests[id] = {
            ...existing,
            status,
            awaiting:
              awaiting ??
              (isTerminalActionStatus(status) ? null : existing.awaiting),
            updatedAt: Date.now(),
          };
        }
        // M5：交互线程随快照恢复（刷新后玩家仍能看到「已讨论/尚未执行」）
        const snapshotInteractions = Array.isArray(payload.interactions)
          ? payload.interactions.flatMap((entry) => {
              const thread = readInteractionThread(entry);
              return thread ? [thread] : [];
            })
          : null;
        if (snapshotInteractions !== null) {
          set({ interactions: {}, interactionOrder: [] });
          get().applyInteractions(snapshotInteractions);
        }
        if (payload.server_capabilities !== undefined) {
          set({
            capabilities: readServerCapabilities(payload.server_capabilities),
          });
        }
        return {
          identity: {
            worldId: nextWorldId,
            revision,
            investigatorId:
              str(payload.investigator_id) || state.identity.investigatorId,
            keeperUserId: str(keeper.user_id) || state.identity.keeperUserId,
            // M0 的 keeper_mode 是 payload 必填字段；keeper 本身可为 null
            // （KeeperControl 行缺失时），因此模式优先取 keeper_mode。
            keeperMode:
              str(payload.keeper_mode) ||
              str(keeper.mode) ||
              state.identity.keeperMode,
          },
          targets:
            payload.targets !== undefined
              ? readTargets(payload.targets)
              : state.targets,
          destinations:
            payload.destinations !== undefined
              ? readDestinations(payload.destinations)
              : state.destinations,
          clues:
            payload.clues !== undefined
              ? readClues(payload.clues)
              : state.clues,
          items:
            payload.items !== undefined
              ? readItems(payload.items)
              : state.items,
          keeperMaterial:
            payload.keeper_material !== undefined
              ? readKeeperMaterial(payload.keeper_material)
              : state.keeperMaterial,
          checks,
          checkOrder,
          requests,
          requestOrder,
        };
      }),

    applyEvent: (envelope) =>
      set((state) => {
        const now = Date.now();
        const payload = envelope.payload ?? {};
        // 主持命令的结局帧按 command_id 归位（服务端的 request_error 带
        // command_id、cause_request_id 为 null），玩家请求则用 request_id。
        const byRequest =
          str(payload.request_id) ||
          str(payload.command_id) ||
          envelope.cause_request_id ||
          "";
        // 每个事件信封都带当前世界 revision：必须让它前进，否则冲突后重试
        // 仍然带着旧版本号，重试永远无法收敛。
        const base = {
          lastEventAt: now,
          identity: {
            ...state.identity,
            revision: Math.max(state.identity.revision, envelope.revision),
          },
        };

        const touchRequest = (): Partial<StructuredState> | null => {
          if (!byRequest) return null;
          const current = state.requests[byRequest];
          if (!current) return null;
          return {
            requests: {
              ...state.requests,
              [byRequest]: applyRequestUpdate(current, payload, now),
            },
          };
        };

        switch (envelope.type) {
          case "session_snapshot": {
            get().applySnapshot(payload, envelope.world_id);
            return {
              ...base,
              identity: {
                ...get().identity,
                revision: num(payload.revision, envelope.revision),
              },
            };
          }
          case "action_ack": {
            const updated = touchRequest();
            if (!updated) return base;
            const request = (
              updated.requests as Record<string, PendingRequest>
            )[byRequest];
            return {
              ...base,
              ...updated,
              requests: {
                ...(updated.requests as Record<string, PendingRequest>),
                [byRequest]: {
                  ...request,
                  status: asStatus(payload.status ?? "queued"),
                  awaitingAck: false,
                },
              },
            };
          }
          case "action_status":
            return { ...base, ...(touchRequest() ?? {}) };
          case "request_error": {
            const code = str(payload.code, "unknown");
            const message = str(payload.message);
            const retryable = bool(payload.retryable);
            if (!byRequest || !state.requests[byRequest]) return base;
            const current = state.requests[byRequest];
            return {
              ...base,
              requests: {
                ...state.requests,
                [byRequest]: {
                  ...current,
                  errorCode: code,
                  errorMessage: requestErrorText(code, message),
                  status: retryable ? current.status : "declined",
                  awaitingAck: false,
                  updatedAt: now,
                },
              },
            };
          }
          case "check_requested": {
            const parsed = readCheckRequest(payload);
            if (!parsed) return base;
            const existing = state.checks[parsed.checkRequestId];
            // 同一 check_request_id 重复投递不覆盖已结算结果。
            if (existing && existing.status !== "pending") return base;
            return {
              ...base,
              checks: {
                ...state.checks,
                [parsed.checkRequestId]: { ...existing, ...parsed },
              },
              checkOrder: state.checkOrder.includes(parsed.checkRequestId)
                ? state.checkOrder
                : [...state.checkOrder, parsed.checkRequestId],
            };
          }
          case "check_resolved":
          case "check_cancelled": {
            const checkRequestId = str(payload.check_request_id);
            if (!checkRequestId) return base;
            const existing = state.checks[checkRequestId] ?? {
              checkRequestId,
              investigatorId: str(payload.investigator_id),
              skill: str(payload.skill),
              difficulty: "",
              bonusPenalty: 0,
              attempt: "",
              knownCost: "",
              visibility: "public",
              status: "pending" as const,
              result: null,
              updatedAt: now,
            };
            const resolved =
              envelope.type === "check_resolved"
                ? {
                    ...existing,
                    status: "resolved" as const,
                    result: {
                      targetValue:
                        typeof payload.target_value === "number"
                          ? payload.target_value
                          : null,
                      roll:
                        typeof payload.roll === "number" ? payload.roll : null,
                      outcome: str(payload.outcome),
                      detail: str(payload.detail),
                    },
                    updatedAt: now,
                  }
                : {
                    ...existing,
                    status: "cancelled" as const,
                    result: {
                      targetValue: null,
                      roll: null,
                      outcome: "cancelled",
                      detail: str(payload.reason),
                    },
                    updatedAt: now,
                  };
            return {
              ...base,
              checks: { ...state.checks, [checkRequestId]: resolved },
              checkOrder: state.checkOrder.includes(checkRequestId)
                ? state.checkOrder
                : [...state.checkOrder, checkRequestId],
            };
          }
          case "keeper_draft": {
            const draftId = str(payload.draft_id);
            if (!draftId) return base;
            const command =
              payload.command && typeof payload.command === "object"
                ? (payload.command as Record<string, unknown>)
                : null;
            return {
              ...base,
              keeperDraft: {
                draftId,
                summary: str(payload.summary) || str(payload.text),
                command: command
                  ? {
                      kind: str(command.kind),
                      payload:
                        (command.payload as Record<string, unknown>) ?? {},
                    }
                  : null,
                note: str(payload.note),
                createdAt: now,
              },
            };
          }
          case "keeper_draft_resolved": {
            if (!state.keeperDraft) return base;
            if (
              str(payload.draft_id) &&
              str(payload.draft_id) !== state.keeperDraft.draftId
            ) {
              return base;
            }
            return { ...base, keeperDraft: null };
          }
          case "keeper_control": {
            const rawState = str(payload.state, "active");
            const allowed = [
              "active",
              "paused",
              "takeover",
              "budget_exceeded",
              "unavailable",
            ] as const;
            const controlState = (allowed as readonly string[]).includes(
              rawState,
            )
              ? (rawState as KeeperControl["state"])
              : "active";
            return {
              ...base,
              keeperControl: {
                state: controlState,
                detail: str(payload.detail),
                takeoverAvailable: bool(payload.takeover_available),
                updatedAt: now,
              },
            };
          }
          case "scene_changed":
            // 只有已提交场景事件更新位置；候选目的地同步替换。
            return {
              ...base,
              destinations:
                payload.destinations !== undefined
                  ? readDestinations(payload.destinations)
                  : state.destinations,
              identity: { ...state.identity, revision: envelope.revision },
            };
          case "clue_granted": {
            const clueId = str(payload.clue_id);
            if (!clueId || state.clues.some((clue) => clue.id === clueId)) {
              return {
                ...base,
                identity: { ...state.identity, revision: envelope.revision },
              };
            }
            return {
              ...base,
              clues: [
                ...state.clues,
                {
                  id: clueId,
                  category: str(payload.category, "investigation"),
                  text: str(payload.text),
                  presentation: Array.isArray(payload.presentation)
                    ? (payload.presentation as string[]).filter(
                        (item) => typeof item === "string",
                      )
                    : ["describe"],
                  allowedPhysicalItemIds: Array.isArray(
                    payload.allowed_physical_item_ids,
                  )
                    ? (payload.allowed_physical_item_ids as unknown[]).filter(
                        (item): item is string => typeof item === "string",
                      )
                    : [],
                },
              ],
              identity: { ...state.identity, revision: envelope.revision },
            };
          }
          case "inventory_changed":
            return {
              ...base,
              items: readItems(payload.items),
              identity: { ...state.identity, revision: envelope.revision },
            };
          default:
            return {
              ...base,
              identity: { ...state.identity, revision: envelope.revision },
            };
        }
      }),

    registerOutgoing: ({ requestId, kind, label, payload, digest }) =>
      set((state) => {
        const now = Date.now();
        return {
          requests: {
            ...state.requests,
            [requestId]: {
              requestId,
              kind,
              label,
              awaiting: null,
              status: "queued",
              outcome: null,
              detail: "",
              errorCode: null,
              errorMessage: null,
              payload,
              digest,
              sends: 0,
              createdAt: now,
              updatedAt: now,
              awaitingAck: false,
            },
          },
          requestOrder: state.requestOrder.includes(requestId)
            ? state.requestOrder
            : [...state.requestOrder, requestId],
        };
      }),

    markSent: (requestId) =>
      set((state) => {
        const current = state.requests[requestId];
        if (!current) return state;
        // 注意：不在这里清 awaitingAck。超时后重发时“仍在等待服务端”这个
        // 事实必须保留，否则界面会把已经超时的请求显示成正常处理中。
        // 清除 awaitingAck 的只有服务端事件与用户手动重试。
        return {
          requests: {
            ...state.requests,
            [requestId]: {
              ...current,
              sends: current.sends + 1,
              updatedAt: Date.now(),
            },
          },
        };
      }),

    markAwaitingAck: (requestId) =>
      set((state) => {
        const current = state.requests[requestId];
        if (!current || current.awaitingAck) return state;
        return {
          requests: {
            ...state.requests,
            [requestId]: {
              ...current,
              awaitingAck: true,
              updatedAt: Date.now(),
            },
          },
        };
      }),

    noteRetry: (requestId) =>
      set((state) => {
        const current = state.requests[requestId];
        if (!current) return state;
        return {
          requests: {
            ...state.requests,
            [requestId]: {
              ...current,
              awaitingAck: false,
              errorCode: null,
              errorMessage: null,
              updatedAt: Date.now(),
            },
          },
        };
      }),

    applyRequestError: (requestId, code, message, retryable) =>
      set((state) => {
        if (!requestId || !state.requests[requestId]) return state;
        const current = state.requests[requestId];
        return {
          requests: {
            ...state.requests,
            [requestId]: {
              ...current,
              errorCode: code,
              errorMessage: requestErrorText(code, message),
              status:
                retryable && !isTerminalActionStatus(current.status)
                  ? current.status
                  : "declined",
              awaitingAck: false,
              updatedAt: Date.now(),
            },
          },
        };
      }),

    resolveCheck: (checkRequestId, decision, identity) => {
      const state = get();
      const check = state.checks[checkRequestId];
      if (!check) return null;
      // 只有被指定的玩家能响应，且必须是其控制的调查员。
      if (
        !identity.investigatorId ||
        check.investigatorId !== identity.investigatorId
      ) {
        return null;
      }
      // 已结算/已取消/已过期的检定不再接受响应：返回 null 让调用方明确拒绝，
      // 不能因为“状态不是 pending”就当成本次响应已受理。
      if (check.status !== "pending") return null;
      const next: CheckRequestState =
        decision === "decline"
          ? {
              ...check,
              status: "declined",
              result: {
                targetValue: null,
                roll: null,
                outcome: "declined",
                detail: "你选择放弃这次检定。",
              },
              updatedAt: Date.now(),
            }
          : check;
      set({ checks: { ...state.checks, [checkRequestId]: next } });
      return next;
    },

    clearKeeperDraft: () => set({ keeperDraft: null }),

    upsertInteraction: (thread) =>
      set((state) => ({
        interactions: { ...state.interactions, [thread.threadId]: thread },
        interactionOrder: state.interactionOrder.includes(thread.threadId)
          ? state.interactionOrder
          : [...state.interactionOrder, thread.threadId],
      })),

    applyInteractions: (threads) =>
      set((state) => {
        // 快照是权威投影：整份替换，但保留本地已收到的更新（同 thread_id 取快照值）
        const interactions: Record<string, InteractionThread> = {};
        const order: string[] = [];
        for (const thread of threads) {
          interactions[thread.threadId] = thread;
          order.push(thread.threadId);
        }
        return { ...state, interactions, interactionOrder: order };
      }),

    beginMemoryQuery: (queryId, filters) =>
      set({
        memoryQuery: {
          status: "querying",
          queryId,
          entries: [],
          truncated: false,
          error: "",
          filters,
        },
      }),

    applyMemoryQueryResult: (payload) =>
      set((state) => {
        const queryId = str(payload.query_id);
        // 只接受本次查询的结果：旧查询的迟到结果不能覆盖新结果
        if (!queryId || queryId !== state.memoryQuery.queryId) return state;
        const entries = Array.isArray(payload.memories)
          ? payload.memories.flatMap((item) => {
              const entry = readMemoryEntry(item);
              return entry ? [entry] : [];
            })
          : [];
        return {
          memoryQuery: {
            ...state.memoryQuery,
            status: "done",
            entries,
            truncated: payload.truncated === true,
            error: "",
          },
        };
      }),

    applyMemoryQueryError: (message) =>
      set((state) => ({
        memoryQuery: { ...state.memoryQuery, status: "failed", error: message },
      })),

    noteUnknownEventType: (type) =>
      set((state) =>
        state.unknownEventTypes.includes(type)
          ? state
          : { unknownEventTypes: [...state.unknownEventTypes, type] },
      ),

    clearFinished: () =>
      set((state) => {
        const keep = state.requestOrder.filter((id) => {
          const request = state.requests[id];
          return (
            request &&
            !isTerminalActionStatus(request.status) &&
            !request.errorCode
          );
        });
        const requests: Record<string, PendingRequest> = {};
        for (const id of keep) requests[id] = state.requests[id];
        const keepChecks = state.checkOrder.filter(
          (id) =>
            state.checks[id] &&
            (state.checks[id].status === "pending" ||
              state.checks[id].status === "resolved"),
        );
        const checks: Record<string, CheckRequestState> = {};
        for (const id of keepChecks) checks[id] = state.checks[id];
        return { requests, requestOrder: keep, checks, checkOrder: keepChecks };
      }),

    reset: () => set({ ...initialStructuredState }),
  }),
);

// ---------------------------------------------------------------------------
// 选择器
// ---------------------------------------------------------------------------

/** 结构化协议是否可用（能力协商结果）。不可用时 UI 必须明确说明并禁止提交。 */
export function structuredAvailable(
  state: Pick<StructuredState, "capabilities" | "protocolNotice">,
): boolean {
  return state.capabilities.structuredProtocol && !state.protocolNotice;
}

export function currentIdentity(): Identity {
  return useStructuredStore.getState().identity;
}

/** 尚未结束的请求（倒序，最新在前）。 */
export function activeRequests(
  state: Pick<StructuredState, "requests" | "requestOrder">,
): PendingRequest[] {
  return [...state.requestOrder]
    .reverse()
    .flatMap((id) => (state.requests[id] ? [state.requests[id]] : []))
    .filter(
      (request) => !isTerminalActionStatus(request.status) || request.errorCode,
    );
}

/** 等待本人响应的检定（持久卡片数据源）。 */
export function pendingChecksFor(
  state: Pick<StructuredState, "checks" | "checkOrder">,
  investigatorId: string,
): CheckRequestState[] {
  return state.checkOrder
    .flatMap((id) => (state.checks[id] ? [state.checks[id]] : []))
    .filter(
      (check) =>
        check.status === "pending" &&
        (!investigatorId || check.investigatorId === investigatorId),
    );
}

/**
 * 顶部待办抽屉要显示的卡片：最近窗口内的全部请求（含已完成/被拒绝），
 * 因为验收要求卡片能显示“完成/拒绝/暂停”这几种终态；只保留最近 5 条，
 * 避免长会话里无限堆叠。
 */
export function visibleRequestCards(
  state: Pick<StructuredState, "requests" | "requestOrder">,
  limit = 5,
): PendingRequest[] {
  return [...state.requestOrder]
    .reverse()
    .flatMap((id) => (state.requests[id] ? [state.requests[id]] : []))
    .slice(0, limit);
}

/** 无论是否轮到本人，都要显示为只读的待检定（其他人只看不点）。 */
export function visibleChecks(
  state: Pick<StructuredState, "checks" | "checkOrder">,
): CheckRequestState[] {
  return state.checkOrder
    .flatMap((id) => (state.checks[id] ? [state.checks[id]] : []))
    .filter((check) => check.status !== "cancelled" || check.result !== null);
}

/** 待办里尚未执行的行动类型 → 卡片 intent kind（快照恢复时没有原始 action）。 */
function snapshotIntentKind(kind: string | undefined): RequestIntentKind {
  return kind === "move" ||
    kind === "present_clue" ||
    kind === "use_item" ||
    kind === "freeform"
    ? kind
    : "freeform";
}

/** 仍未结束的交互线程（open）：待办区展示这些，终态不再显示为可继续。 */
export function openInteractions(state: {
  interactions: Record<string, InteractionThread>;
  interactionOrder: string[];
}): InteractionThread[] {
  return state.interactionOrder
    .map((id) => state.interactions[id])
    .filter((thread): thread is InteractionThread => Boolean(thread))
    .filter((thread) => thread.status === "open");
}

export function requestLabel(kind: RequestIntentKind): string {
  switch (kind) {
    case "present_clue":
      return "出示线索";
    case "use_item":
      return "使用道具";
    case "move":
      return "前往";
    case "freeform":
      return "行动";
    case "free_roll":
      return "掷骰";
    case "check_response":
      return "检定回应";
    case "command":
      return "主持操作";
    default:
      return "行动";
  }
}

export { parseStructuredEvent };
