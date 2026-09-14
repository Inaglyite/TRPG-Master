/**
 * structured.ts — 结构化操作协议 v1 的唯一前端出入口。
 *
 * 契约来源：`docs/STRUCTURED_PLAY_PLATFORM_PLAN_20260913.md` 第 5 节（草案）。
 * 后端 M0 冻结正式 schema 后，**只改这一个文件**与 `structured-fixtures.ts`，
 * 组件与 store 不需要跟着改字段名。
 *
 * 三条硬约束：
 * 1. 只做校验与构造，不判断行动是否成功——那是守秘人和规则层的事。
 * 2. 不发明字段：本文件里的每个字段名都来自主规格 §5.1/§5.2/§5.3 的原文。
 *    已知枚举之外的值一律**容错保留**（未知错误码用服务端文案兜底，未知事件
 *    类型按通用信封透传），这样 M0 用词与猜测不一致时不会解析失败。
 * 3. 不做自然语言往返：action_request 里的文本只出现在 `approach`/`question`
 *    这类补充字段，权威字段始终是 ID、枚举与数量。
 */

import { z } from "zod";

/** 与后端协商的协议版本；服务端不认这个值时前端明确提示，不回退旧协议。 */
export const STRUCTURED_PROTOCOL_VERSION = 1;

export const EXECUTION_PROFILES = ["legacy", "structured_v1"] as const;
export type ExecutionProfile = (typeof EXECUTION_PROFILES)[number];

export const KEEPER_MODES = ["human", "assisted", "agent"] as const;
export type KeeperMode = (typeof KEEPER_MODES)[number];

// ---------------------------------------------------------------------------
// 行动请求（客户端 → 服务端）
// ---------------------------------------------------------------------------

export const PRESENTATIONS = ["describe", "image", "original"] as const;
export type PresentationKind = (typeof PRESENTATIONS)[number];

export const TARGET_KINDS = ["npc", "investigator", "scene_object"] as const;
export type TargetKind = (typeof TARGET_KINDS)[number];

const targetIdSchema = z.object({
  kind: z.enum(TARGET_KINDS),
  id: z.string().min(1).max(160),
});

/** 目标可以是稳定 ID，或“描述其他对象”的未解析文本；后者不直接执行命令。 */
const targetSchema = z.union([
  targetIdSchema,
  z.object({ kind: z.literal("unresolved"), text: z.string().min(1).max(160) }),
]);
export type ActionTarget = z.infer<typeof targetSchema>;

export const presentClueActionSchema = z
  .object({
    kind: z.literal("present_clue"),
    clue_id: z.string().min(1).max(160),
    presentation: z.enum(PRESENTATIONS),
    physical_item_id: z.string().min(1).max(160).nullable(),
    target: targetSchema,
    question: z.string().max(200).optional(),
  })
  // M0 action_request.json 的 allOf：original 必须有实物，其余方式必须为 null。
  .superRefine((value, context) => {
    if (value.presentation === "original" && !value.physical_item_id) {
      context.addIssue({
        code: "custom",
        path: ["physical_item_id"],
        message: "presentation=original 必须给出实际持有的实物 ID",
      });
    }
    if (value.presentation !== "original" && value.physical_item_id !== null) {
      context.addIssue({
        code: "custom",
        path: ["physical_item_id"],
        message: "只有 presentation=original 才能携带实物 ID",
      });
    }
  });

/** M0：operation=custom 为即兴用法，必须同时给出 approach。 */
export const CUSTOM_OPERATION = "custom";

export const useItemActionSchema = z
  .object({
    kind: z.literal("use_item"),
    item_id: z.string().min(1).max(160),
    quantity: z.number().int().min(1).max(999),
    operation: z.string().min(1).max(60),
    target: targetSchema.optional(),
    approach: z.string().max(200).optional(),
  })
  .superRefine((value, context) => {
    if (
      value.operation === CUSTOM_OPERATION &&
      !(value.approach ?? "").trim()
    ) {
      context.addIssue({
        code: "custom",
        path: ["approach"],
        message: "自定义用法必须写明做法",
      });
    }
  });

export const moveActionSchema = z.object({
  kind: z.literal("move"),
  destination_scene_id: z.string().min(1).max(160),
});

export const freeformActionSchema = z.object({
  kind: z.literal("freeform"),
  text: z.string().min(1).max(2000),
});

export const structuredActionSchema = z.discriminatedUnion("kind", [
  presentClueActionSchema,
  useItemActionSchema,
  moveActionSchema,
  freeformActionSchema,
]);
export type StructuredAction = z.infer<typeof structuredActionSchema>;
export type PresentClueAction = z.infer<typeof presentClueActionSchema>;
export type UseItemAction = z.infer<typeof useItemActionSchema>;
export type MoveAction = z.infer<typeof moveActionSchema>;

export const actionRequestSchema = z.object({
  type: z.literal("action_request"),
  protocol_version: z.literal(STRUCTURED_PROTOCOL_VERSION),
  request_id: z.string().min(1).max(160),
  world_id: z.string().min(1).max(160),
  expected_revision: z.number().int().nonnegative(),
  investigator_id: z.string().min(1).max(160),
  action: structuredActionSchema,
});
export type ActionRequest = z.infer<typeof actionRequestSchema>;

/** 普通掷骰走独立请求，不消耗模型额度，也不触发剧情分支。 */
/** M0 common.json 的 dice_spec 正则；服务端另有限幅，前端同样先拦。 */
export const DICE_SPEC_PATTERN = /^(\d{1,2})d(\d{1,3})([+-]\d{1,3})?$/;

export const freeRollRequestSchema = z.object({
  type: z.literal("free_roll_request"),
  protocol_version: z.literal(STRUCTURED_PROTOCOL_VERSION),
  request_id: z.string().min(1).max(160),
  world_id: z.string().min(1).max(160),
  investigator_id: z.string().min(1).max(160),
  spec: z
    .string()
    .min(1)
    .max(40)
    .regex(DICE_SPEC_PATTERN, "骰式必须形如 NdM 或 NdM±K"),
});
export type FreeRollRequest = z.infer<typeof freeRollRequestSchema>;

export const CHECK_DECISIONS = ["roll", "decline"] as const;
export type CheckDecision = (typeof CHECK_DECISIONS)[number];

/** 检定参数已存在服务端；按钮只提交“掷骰/放弃”，不携带技能值或难度。 */
export const checkResponseSchema = z.object({
  type: z.literal("check_response"),
  protocol_version: z.literal(STRUCTURED_PROTOCOL_VERSION),
  request_id: z.string().min(1).max(160),
  world_id: z.string().min(1).max(160),
  check_request_id: z.string().min(1).max(160),
  decision: z.enum(CHECK_DECISIONS),
});
export type CheckResponse = z.infer<typeof checkResponseSchema>;

/**
 * M0 冻结的主持命令集合（command_request.json 的 oneOf）。
 * 不接受表外 kind：`execute_arbitrary_sql` 这类请求必须在前端就被拒绝。
 */
export const KEEPER_COMMAND_KINDS = [
  "publish_message",
  "request_check",
  "resolve_check",
  "present_information",
  "grant_clue",
  "use_item",
  "transfer_item",
  "adjust_stat",
  "advance_time",
  "move_party",
  "resolve_intent",
  "present_handout",
  "set_npc_presence",
  "record_fact",
  // assisted：处理主持草稿（M1 新增；之前前端只能批准、不能拒绝）。
  "resolve_draft",
  // M5：主持显式记录角色记忆（长期记忆层；不是权威世界状态）。
  "record_memory",
] as const;
export type KeeperCommandKind = (typeof KEEPER_COMMAND_KINDS)[number];

/** 主持命令信封（KeeperConsole 用）。command_id 绑定一次副作用。 */
export const commandRequestSchema = z.object({
  type: z.literal("command_request"),
  protocol_version: z.literal(STRUCTURED_PROTOCOL_VERSION),
  command_id: z.string().min(1).max(160),
  world_id: z.string().min(1).max(160),
  expected_revision: z.number().int().nonnegative(),
  kind: z.enum(KEEPER_COMMAND_KINDS),
  payload: z.record(z.string(), z.unknown()),
});
export type CommandRequest = z.infer<typeof commandRequestSchema>;

// ---------------------------------------------------------------------------
// 事件信封（服务端 → 客户端）
// ---------------------------------------------------------------------------

/** M0 speaker 形态：`{kind, id?}`；普通玩家只能以自己的调查员身份发言。 */
export const SPEAKER_KINDS = [
  "keeper",
  "npc",
  "investigator",
  "system",
] as const;
export type SpeakerKind = (typeof SPEAKER_KINDS)[number];

/** M0 audience 形态：public / keeper / investigators。 */
export type Audience =
  | { kind: "public" }
  | { kind: "keeper" }
  | { kind: "investigators"; investigator_ids: string[] };

export const STRUCTURED_EVENT_TYPES = [
  "session_snapshot",
  "action_ack",
  "action_status",
  "check_requested",
  "check_resolved",
  "check_cancelled",
  "roll_resolved",
  "message_started",
  "message_chunk",
  "message_completed",
  "scene_changed",
  "clue_granted",
  "inventory_changed",
  "state_changed",
  "request_error",
  "keeper_draft",
  "keeper_draft_resolved",
  "keeper_control",
  "intent_pending",
  "handout_presented",
  // 上下文与记忆改造（M5）：交互线程、主持侧记忆记录与只读查询结果
  "interaction_updated",
  "memory_recorded",
  "memory_query_result",
] as const;
export type StructuredEventType = (typeof STRUCTURED_EVENT_TYPES)[number];

/**
 * 事件信封只强制协议版本与事件标识；`type` 用字符串而不是枚举，
 * 这样后端新增事件类型时前端只会忽略，不会把整条消息当协议错误丢掉。
 */
export const structuredEventEnvelopeSchema = z.looseObject({
  protocol_version: z.number().int().positive().optional(),
  event_id: z.number().int().nonnegative(),
  world_id: z.string().min(1).max(160),
  sequence: z.number().int().nonnegative().optional(),
  revision: z.number().int().nonnegative(),
  type: z.string().min(1).max(60),
  cause_request_id: z.string().max(160).nullable().optional(),
  payload: z.record(z.string(), z.unknown()).optional(),
});

export type StructuredEventEnvelope = {
  protocol_version?: number;
  event_id: number;
  world_id: string;
  sequence?: number;
  revision: number;
  type: string;
  cause_request_id?: string | null;
  payload: Record<string, unknown>;
};

/**
 * 解析一条可能是结构化事件的消息。
 *
 * 返回 `null` 表示“不是结构化事件”（交给旧协议链路处理）；
 * 协议版本不认识时返回 `{ mismatch: true }`，由调用方显示明确提示，
 * **绝不**退回自然语言发送。
 */
export function parseStructuredEvent(
  raw: unknown,
): { envelope: StructuredEventEnvelope } | { mismatch: true } | null {
  if (!raw || typeof raw !== "object") return null;
  const candidate = raw as Record<string, unknown>;
  // 结构化事件必须有世界与事件标识；旧协议消息没有这两个字段。
  if (typeof candidate.event_id !== "number") return null;
  if (typeof candidate.world_id !== "string") return null;
  if (typeof candidate.type !== "string") return null;
  if (typeof candidate.revision !== "number") return null;
  const version = candidate.protocol_version;
  if (typeof version === "number" && version !== STRUCTURED_PROTOCOL_VERSION) {
    return { mismatch: true };
  }
  const parsed = structuredEventEnvelopeSchema.safeParse(candidate);
  if (!parsed.success) return null;
  const data = parsed.data;
  return {
    envelope: {
      protocol_version: data.protocol_version,
      event_id: data.event_id,
      world_id: data.world_id,
      sequence: data.sequence,
      revision: data.revision,
      type: data.type,
      cause_request_id: data.cause_request_id ?? null,
      payload: (data.payload as Record<string, unknown>) ?? {},
    },
  };
}

// ---------------------------------------------------------------------------
// 状态与结果枚举（§5.2）
// ---------------------------------------------------------------------------

export const ACTION_STATUSES = [
  "queued",
  "processing",
  "awaiting_player",
  "completed",
  "declined",
  "cancelled",
  "paused",
  "failed",
] as const;
export type ActionStatusKind = (typeof ACTION_STATUSES)[number];

/** 领域结果与“服务器收到请求”是两件事；completed 不代表行动成功。 */
export const DOMAIN_OUTCOMES = ["success", "failure", "not_executed"] as const;
export type DomainOutcome = (typeof DOMAIN_OUTCOMES)[number];

/** 终态：不再等待服务端更新。awaiting_player 需要玩家动作，不是终态。 */
export function isTerminalActionStatus(status: ActionStatusKind): boolean {
  return (
    status === "completed" || status === "declined" || status === "cancelled"
  );
}

// ---------------------------------------------------------------------------
// 错误码（§5.1 revision 冲突、§3.5 失效目标）
// ---------------------------------------------------------------------------

/**
 * 已知错误码 → 中文说明。**未列出的码不丢弃**：直接显示服务端 message/detail，
 * 这样 M0 用词与这里不一致时用户仍能看到准确原因。
 */
export const REQUEST_ERROR_TEXTS: Record<string, string> = {
  revision_conflict: "世界状态已更新，请刷新候选后重新提交。",
  duplicate_request_conflict: "同一请求 ID 提交了不同内容，已拒绝。",
  unknown_target: "目标不存在或当前不可交互，请重新选择。",
  stale_target: "目标已失效（场景或状态已变化），请重新选择。",
  unsupported_protocol: "服务端不支持结构化协议，请更新服务端或使用旧模式。",
  not_authorized: "没有执行该操作的权限。",
  not_actor: "还没有轮到你行动。",
  check_not_pending: "该检定已不在等待状态。",
  check_already_resolved: "该检定已被结算，不会重复掷骰。",
  invalid_action: "请求内容不合法，已拒绝。",
  rate_limited: "请求过于频繁，请稍后再试。",
  keeper_unavailable: "守秘人暂时不可用，行动已暂停，可稍后继续。",
  request_not_found: "没有找到原请求，可能尚未提交成功。",
  // M0 冻结的补充错误码
  unknown_world: "找不到这个世界，请刷新后重试。",
  target_unresolved: "目标尚未解析，请先让主持澄清对象。",
  object_not_found: "对象不存在。",
  object_not_held: "你并没有持有该物品。",
  presentation_requires_item: "以“展示原件”出示需要选择你持有的实物。",
  presentation_requires_asset: "这条线索没有你可展示的素材。",
  check_conditions_changed: "相关条件已变化，检定已作废，请等主持重新请求。",
  controller_epoch_stale: "主持控制权已变更，请以当前主持的操作继续。",
  keeper_required: "该操作需要主持权限。",
  not_investigator_controller: "你不是这名调查员的控制者。",
  profile_mismatch: "世界执行模式与请求不一致，请刷新后重试。",
  mode_unavailable: "该模式当前不可用。",
  budget_exceeded: "已达到本次运行预算上限，行动已暂停。",
  commit_failed: "提交失败，未产生任何权威结果，请重试。",
  internal_error: "服务端内部错误，请稍后重试或联系维护者。",
};

export function requestErrorText(
  code: unknown,
  serverMessage?: unknown,
): string {
  const serverText =
    typeof serverMessage === "string" && serverMessage.trim()
      ? serverMessage.trim()
      : "";
  if (typeof code === "string" && code && REQUEST_ERROR_TEXTS[code]) {
    return serverText
      ? `${REQUEST_ERROR_TEXTS[code]}（${serverText}）`
      : REQUEST_ERROR_TEXTS[code];
  }
  if (serverText) return serverText;
  return "请求被拒绝，原因未知。";
}

// ---------------------------------------------------------------------------
// 能力协商（§5.2 server_capabilities / §11 M0）
// ---------------------------------------------------------------------------

/**
 * 前端需要的服务端能力字段（已作为前端契约交给后端 M0）。
 * 读取一律容错：缺字段 = 不支持，绝不用“看起来像”猜。
 */
/** M5：交互线程的公开投影（本人/主持可见；记录而非执行授权）。 */
export type InteractionThread = {
  threadId: string;
  status: "open" | "completed" | "cancelled" | "superseded";
  investigatorId: string;
  pendingAction: {
    kind: string;
    note: string;
    target: string;
    destinationSceneId: string;
  };
  disclosed: string[];
  waitingOn: string;
  note: string;
  originRequestId: string;
  lastRequestId: string;
};

/** M5：主持侧只读记忆查询返回的一条角色记忆。 */
export type MemoryEntry = {
  memoryId: string;
  characterId: string;
  characterKind: "investigator" | "npc";
  knowledgeType: "experienced" | "told" | "rumor" | "belief";
  content: string;
  sceneId: string;
  subjects: string[];
  topics: string[];
  status: "active" | "superseded";
  createdSequence: number;
};

export const INTERACTION_STATUSES = [
  "open",
  "completed",
  "cancelled",
  "superseded",
] as const;

export function readInteractionThread(
  value: unknown,
): InteractionThread | null {
  if (!value || typeof value !== "object") return null;
  const raw = value as Record<string, unknown>;
  const threadId = str(raw.thread_id).trim();
  const status = str(raw.status).trim();
  if (
    !threadId ||
    !(INTERACTION_STATUSES as readonly string[]).includes(status)
  ) {
    return null;
  }
  const action = (
    raw.pending_action && typeof raw.pending_action === "object"
      ? raw.pending_action
      : {}
  ) as Record<string, unknown>;
  return {
    threadId,
    status: status as InteractionThread["status"],
    investigatorId: str(raw.investigator_id),
    pendingAction: {
      kind: str(action.kind) || "other",
      note: str(action.note),
      target: str(action.target),
      destinationSceneId: str(action.destination_scene_id),
    },
    disclosed: asStringList(raw.disclosed),
    waitingOn: str(raw.waiting_on),
    note: str(raw.note),
    originRequestId: str(raw.origin_request_id),
    lastRequestId: str(raw.last_request_id),
  };
}

const KNOWLEDGE_TYPES = ["experienced", "told", "rumor", "belief"] as const;

export function readMemoryEntry(value: unknown): MemoryEntry | null {
  if (!value || typeof value !== "object") return null;
  const raw = value as Record<string, unknown>;
  const memoryId = str(raw.memory_id).trim();
  const content = str(raw.content).trim();
  if (!memoryId || !content) return null;
  const knowledgeType = str(raw.knowledge_type);
  return {
    memoryId,
    characterId: str(raw.character_id),
    characterKind: str(raw.character_kind) === "npc" ? "npc" : "investigator",
    knowledgeType: (KNOWLEDGE_TYPES as readonly string[]).includes(
      knowledgeType,
    )
      ? (knowledgeType as MemoryEntry["knowledgeType"])
      : "belief",
    content,
    sceneId: str(raw.scene_id),
    subjects: asStringList(raw.subjects),
    topics: asStringList(raw.topics),
    status: str(raw.status) === "superseded" ? "superseded" : "active",
    createdSequence: num(raw.created_sequence),
  };
}

export type MemoryQueryFilters = {
  characterId?: string;
  sceneId?: string;
  topics?: string[];
  text?: string;
  limit?: number;
  charBudget?: number;
};

/** M5：主持侧只读记忆查询帧（玩家侧不发送；服务端同样会拒绝）。 */
export function buildMemoryQuery(
  queryId: string,
  filters: MemoryQueryFilters,
): Record<string, unknown> {
  const payload: Record<string, unknown> = {};
  if (filters.characterId) payload.character_id = filters.characterId;
  if (filters.sceneId) payload.scene_id = filters.sceneId;
  if (filters.topics?.length) payload.topics = filters.topics.slice(0, 6);
  if (filters.text) payload.text = filters.text;
  if (filters.limit !== undefined) payload.limit = filters.limit;
  if (filters.charBudget !== undefined)
    payload.char_budget = filters.charBudget;
  return {
    type: "memory_query",
    protocol_version: STRUCTURED_PROTOCOL_VERSION,
    query_id: queryId,
    filters: payload,
  };
}

export type ServerCapabilities = {
  structuredProtocol: boolean;
  protocolVersion: number;
  executionProfile: ExecutionProfile;
  keeperModes: KeeperMode[];
  commands: string[];
  keeperConsole: boolean;
  freeRoll: boolean;
  assistedDraft: boolean;
  agentTakeover: boolean;
  checkRequest: boolean;
  moveAction: boolean;
  presentClue: boolean;
  useItem: boolean;
  /** M5：主持侧只读记忆查询（玩家侧不提供） */
  memoryQuery: boolean;
};

export const NO_STRUCTURED_CAPABILITIES: ServerCapabilities = {
  structuredProtocol: false,
  protocolVersion: 0,
  executionProfile: "legacy",
  keeperModes: [],
  commands: [],
  keeperConsole: false,
  freeRoll: false,
  assistedDraft: false,
  agentTakeover: false,
  checkRequest: false,
  moveAction: false,
  presentClue: false,
  useItem: false,
  memoryQuery: false,
};

function asBoolean(value: unknown): boolean {
  return value === true;
}

/** 协议字段一律按字符串读取：缺失/类型不对都退回空串，由调用方决定默认值。 */
function str(value: unknown): string {
  return typeof value === "string"
    ? value
    : value === undefined || value === null
      ? ""
      : String(value);
}

function num(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function asStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((entry): entry is string => typeof entry === "string");
}

function asKeeperModes(value: unknown): KeeperMode[] {
  return asStringList(value).filter((mode): mode is KeeperMode =>
    (KEEPER_MODES as readonly string[]).includes(mode),
  );
}

/**
 * 从 `server_capabilities` 读能力。字段名来自主规格 §5.2 与 §11，
 * 未提供的字段按不支持处理（fail-closed），因此旧服务端不会误开后门。
 */
export function readServerCapabilities(value: unknown): ServerCapabilities {
  if (!value || typeof value !== "object")
    return { ...NO_STRUCTURED_CAPABILITIES };
  const raw = value as Record<string, unknown>;
  const commands = asStringList(raw.commands);
  const profile = raw.execution_profile;
  const executionProfile: ExecutionProfile =
    typeof profile === "string" &&
    (EXECUTION_PROFILES as readonly string[]).includes(profile)
      ? (profile as ExecutionProfile)
      : "legacy";
  const version =
    typeof raw.protocol_version === "number" ? raw.protocol_version : 0;
  const structured =
    asBoolean(raw.structured_protocol) &&
    executionProfile === "structured_v1" &&
    version === STRUCTURED_PROTOCOL_VERSION;
  return {
    structuredProtocol: structured,
    protocolVersion: version,
    executionProfile,
    keeperModes: asKeeperModes(raw.keeper_modes),
    commands,
    keeperConsole: structured && asBoolean(raw.keeper_console),
    freeRoll: structured && asBoolean(raw.free_roll),
    assistedDraft: structured && asBoolean(raw.assisted_draft),
    agentTakeover: structured && asBoolean(raw.agent_takeover),
    // 行动种类既能由 commands 声明，也能由显式布尔开关声明。
    checkRequest:
      structured &&
      (asBoolean(raw.check_request) || commands.includes("request_check")),
    moveAction:
      structured &&
      (asBoolean(raw.move_action) || commands.includes("move_party")),
    memoryQuery: structured && asBoolean(raw.memory_query),
    presentClue:
      structured &&
      (asBoolean(raw.present_clue) || commands.includes("present_information")),
    useItem:
      structured && (asBoolean(raw.use_item) || commands.includes("use_item")),
  };
}

export function supportsCommand(
  capabilities: ServerCapabilities,
  command: string,
): boolean {
  return capabilities.commands.includes(command);
}

/**
 * 交互路径：世界声明的 execution_profile 决定用结构化还是旧文字通道。
 *
 * 注意两者的区别（主规格 §4）：legacy 世界**完全保留**旧路径，那不算“静默退回”；
 * 而 structured_v1 世界里如果协议不可用（版本不匹配、能力缺失），必须明确报错并
 * 禁止提交，**不允许**改走文字通道，否则结构请求就退化成自然语言往返解析了。
 */
export type InteractionPath = "structured" | "legacy";

export function interactionPath(
  capabilities: ServerCapabilities,
): InteractionPath {
  return capabilities.executionProfile === "structured_v1"
    ? "structured"
    : "legacy";
}

/**
 * 结构化路径下能否提交；不能时必须给出可展示的原因（禁用原因/错误条）。
 * 返回 null 表示可以提交。
 */
export function structuredUnavailableReason(
  capabilities: ServerCapabilities,
  protocolNotice: string | null,
): string | null {
  if (protocolNotice) return protocolNotice;
  if (interactionPath(capabilities) !== "structured") {
    return "当前世界使用旧版交互，未启用结构化协议。";
  }
  if (!capabilities.structuredProtocol) {
    return `服务端能力不完整：需要 protocol_version=${STRUCTURED_PROTOCOL_VERSION}、structured_protocol 与 execution_profile=structured_v1。`;
  }
  return null;
}

// ---------------------------------------------------------------------------
// 事件游标：按 world + event_id 去重，revision 不参与丢弃
// ---------------------------------------------------------------------------

export type StructuredEventVerdict = "apply" | "duplicate" | "foreign_world";

/**
 * 结构化事件排序器。
 *
 * 关键约定（§8）：去重只看 **event_id**，world_id 变了就整体重绑；
 * `revision` 只用于判断状态是否更新，**绝不**用来丢弃事件——同一 revision
 * 下可以有多条不同的聊天事件，全部都必须渲染。
 */
export class StructuredEventSequencer {
  private worldId: string | null = null;
  private lastEventId = -1;
  private lastRevision = -1;
  private lastSequence = -1;

  get boundWorldId(): string | null {
    return this.worldId;
  }

  get cursor(): { eventId: number; revision: number; sequence: number } {
    return {
      eventId: this.lastEventId,
      revision: this.lastRevision,
      sequence: this.lastSequence,
    };
  }

  /** 切换世界：清空游标并重绑；旧世界的事件随后会被判为 foreign_world。 */
  rebindWorld(worldId: string | null): void {
    this.worldId = worldId;
    this.lastEventId = -1;
    this.lastRevision = -1;
    this.lastSequence = -1;
  }

  reset(): void {
    this.rebindWorld(null);
  }

  accept(envelope: {
    world_id: string;
    event_id: number;
    revision: number;
    sequence?: number;
    /** 快照是整份状态投影，不是增量事件：不参与 event_id 去重。 */
    isSnapshot?: boolean;
  }): StructuredEventVerdict {
    if (this.worldId === null) {
      // 尚未绑定世界（首连）：以第一条事件的世界为准。
      this.worldId = envelope.world_id;
    } else if (this.worldId !== envelope.world_id) {
      return "foreign_world";
    }
    // `session_snapshot` 是幂等的整份投影，服务端会复用当前最大 event_id
    // 下发（M1 gateway：快照不是新事件）。按 event_id 去重会把开局后的首份
    // 快照当成重复丢掉，客户端就会一直拿着过期 revision 提交并被拒。快照
    // 因此不参与去重，只推进游标；重复应用同一份快照是幂等的。
    if (!envelope.isSnapshot && envelope.event_id <= this.lastEventId) {
      return "duplicate";
    }
    this.lastEventId = envelope.event_id;
    // revision 与 sequence 只前进，不倒退；不用于丢弃事件。
    this.lastRevision = Math.max(this.lastRevision, envelope.revision);
    if (typeof envelope.sequence === "number") {
      this.lastSequence = Math.max(this.lastSequence, envelope.sequence);
    }
    return "apply";
  }
}

// ---------------------------------------------------------------------------
// 请求构造（按钮 → 结构请求）
// ---------------------------------------------------------------------------

/** 请求 ID：本地生成，重试沿用同一个 ID 以命中服务端幂等去重。 */
export function newRequestId(): string {
  const globalCrypto = globalThis.crypto;
  if (globalCrypto?.randomUUID) return globalCrypto.randomUUID();
  return `req-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/** 主持命令 ID：一次副作用一个 ID；Agent/harness 重试沿用同一个。 */
export function newCommandId(): string {
  const globalCrypto = globalThis.crypto;
  if (globalCrypto?.randomUUID) return `cmd-${globalCrypto.randomUUID()}`;
  return `cmd-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export type StructuredIdentity = {
  worldId: string;
  expectedRevision: number;
  investigatorId: string;
};

export type BuildOutcome<T> =
  { ok: true; request: T } | { ok: false; reason: string };

function identityProblem(identity: StructuredIdentity): string | null {
  if (!identity.worldId) return "还没有进入世界，无法提交结构化请求。";
  if (!identity.investigatorId)
    return "尚未确定行动调查员，无法提交结构化请求。";
  if (
    !Number.isFinite(identity.expectedRevision) ||
    identity.expectedRevision < 0
  ) {
    return "缺少世界版本号，请刷新后重试。";
  }
  return null;
}

export function buildActionRequest(
  action: StructuredAction,
  identity: StructuredIdentity,
  requestId: string = newRequestId(),
): BuildOutcome<ActionRequest> {
  const problem = identityProblem(identity);
  if (problem) return { ok: false, reason: problem };
  const parsed = actionRequestSchema.safeParse({
    type: "action_request",
    protocol_version: STRUCTURED_PROTOCOL_VERSION,
    request_id: requestId,
    world_id: identity.worldId,
    expected_revision: identity.expectedRevision,
    investigator_id: identity.investigatorId,
    action,
  });
  if (!parsed.success) {
    return { ok: false, reason: describeIssue(parsed.error) };
  }
  return { ok: true, request: parsed.data };
}

export function buildFreeRollRequest(
  spec: string,
  identity: Omit<StructuredIdentity, "expectedRevision">,
  requestId: string = newRequestId(),
): BuildOutcome<FreeRollRequest> {
  if (!identity.worldId)
    return { ok: false, reason: "还没有进入世界，无法掷骰。" };
  if (!identity.investigatorId)
    return { ok: false, reason: "尚未确定行动调查员。" };
  const parsed = freeRollRequestSchema.safeParse({
    type: "free_roll_request",
    protocol_version: STRUCTURED_PROTOCOL_VERSION,
    request_id: requestId,
    world_id: identity.worldId,
    investigator_id: identity.investigatorId,
    spec,
  });
  if (!parsed.success)
    return { ok: false, reason: describeIssue(parsed.error) };
  return { ok: true, request: parsed.data };
}

export function buildCheckResponse(
  checkRequestId: string,
  decision: CheckDecision,
  identity: Omit<StructuredIdentity, "expectedRevision">,
  requestId: string = newRequestId(),
): BuildOutcome<CheckResponse> {
  if (!identity.worldId) return { ok: false, reason: "还没有进入世界。" };
  const parsed = checkResponseSchema.safeParse({
    type: "check_response",
    protocol_version: STRUCTURED_PROTOCOL_VERSION,
    request_id: requestId,
    world_id: identity.worldId,
    check_request_id: checkRequestId,
    decision,
  });
  if (!parsed.success)
    return { ok: false, reason: describeIssue(parsed.error) };
  return { ok: true, request: parsed.data };
}

export function buildCommandRequest(
  kind: string,
  payload: Record<string, unknown>,
  identity: Pick<StructuredIdentity, "worldId" | "expectedRevision">,
  commandId: string = newCommandId(),
): BuildOutcome<CommandRequest> {
  if (!identity.worldId) return { ok: false, reason: "还没有进入世界。" };
  const parsed = commandRequestSchema.safeParse({
    type: "command_request",
    protocol_version: STRUCTURED_PROTOCOL_VERSION,
    command_id: commandId,
    world_id: identity.worldId,
    expected_revision: identity.expectedRevision,
    kind,
    payload,
  });
  if (!parsed.success)
    return { ok: false, reason: describeIssue(parsed.error) };
  return { ok: true, request: parsed.data };
}

/** 把 zod 的英文报错收敛成一句可读中文，避免按钮上出现内部字段路径。 */
export function describeIssue(error: z.ZodError): string {
  const issue = error.issues[0];
  if (!issue) return "请求内容不合法。";
  const field = issue.path.join(".") || "请求";
  if (issue.code === "too_small" || issue.code === "too_big") {
    return `${field} 的内容长度不合适。`;
  }
  if (issue.code === "invalid_type" || issue.code === "invalid_union") {
    return `${field} 的取值不合法。`;
  }
  return `${field} 校验失败。`;
}

/**
 * 载荷摘要：服务端用“同 ID 不同载荷必须拒绝”做幂等；
 * 前端在重试前先比摘要，载荷变了就换新 request_id，不覆盖已有请求。
 */
export function actionDigest(request: Record<string, unknown>): string {
  const action = request.action as { kind?: unknown } | undefined;
  return JSON.stringify({
    type: request.type ?? null,
    world_id: request.world_id ?? null,
    kind: action?.kind ?? null,
    action: request.action ?? null,
    check_request_id: request.check_request_id ?? null,
    decision: request.decision ?? null,
    spec: request.spec ?? null,
    command_kind: request.kind ?? null,
    command_payload: request.payload ?? null,
  });
}

/** 普通骰表达式白名单：受限表达式，前端只提供合法输入。 */
export const FREE_ROLL_PRESETS = [
  "1d100",
  "1d20",
  "1d10",
  "1d8",
  "1d6",
  "1d4",
  "2d6",
  "3d6",
] as const;

export function freeRollReason(spec: string): string | null {
  const value = spec.trim();
  if (!value) return "请选择或填写骰子表达式。";
  const match = DICE_SPEC_PATTERN.exec(value);
  if (!match) return "表达式格式应为 NdM 或 NdM±K，例如 1d100。";
  const count = Number(match[1]);
  const sides = Number(match[2]);
  if (count < 1 || count > 10) return "骰子个数需在 1–10 之间。";
  if (sides < 2 || sides > 100) return "骰面需在 2–100 之间。";
  const modifier = match[3] ? Number(match[3]) : 0;
  if (Math.abs(modifier) > 999) return "修正值需在 ±999 以内。";
  return null;
}
