/**
 * structured-fixtures.ts — 结构化协议 v1 的测试夹具。
 *
 * **来源标注**：这些夹具是照 `docs/STRUCTURED_PLAY_PLATFORM_PLAN_20260913.md`
 * 第 5 节的草案字段手写的 **spec-derived** 夹具，用于在后端 M0 正式 schema 与
 * fixtures 发布之前开发并测试组件。它们**不是**后端产物，也不能当作联调证据。
 *
 * M0 发布后：把本文件替换为后端 fixtures（或让它从后端 fixtures 生成），
 * 只运行 `structured-fixtures.test.ts` 与 `structured.test.ts` 即可发现差异。
 */

import {
  STRUCTURED_PROTOCOL_VERSION,
  type ServerCapabilities,
  readServerCapabilities,
} from "./structured";

const V = STRUCTURED_PROTOCOL_VERSION;

/**
 * 服务端 `server_capabilities` 的原始线上形态（snake_case）。
 * store 只接受这种形态：`readServerCapabilities` 故意不认已经转换过的
 * camelCase 对象，避免用看起来像能力的对象绕过版本校验。
 */
export const STRUCTURED_CAPABILITIES_WIRE = {
  protocol_version: V,
  structured_protocol: true,
  execution_profile: "structured_v1",
  keeper_modes: ["human", "assisted", "agent"],
  keeper_console: true,
  free_roll: true,
  assisted_draft: true,
  agent_takeover: true,
  check_request: true,
  move_action: true,
  present_clue: true,
  use_item: true,
  commands: [
    "move_party",
    "request_check",
    "resolve_check",
    "present_information",
    "grant_clue",
    "use_item",
    "transfer_item",
    "adjust_stat",
    "advance_time",
    "publish_message",
    "resolve_intent",
  ],
} as const;

/** 只支持 human 主持、没有 assisted/agent 的服务端能力（线上形态）。 */
export const HUMAN_ONLY_CAPABILITIES_WIRE = {
  protocol_version: V,
  structured_protocol: true,
  execution_profile: "structured_v1",
  keeper_modes: ["human"],
  keeper_console: true,
  free_roll: true,
  check_request: true,
  move_action: true,
  present_clue: true,
  use_item: true,
  commands: ["move_party", "request_check", "resolve_check", "grant_clue"],
} as const;

/** 支持结构化 v1 的服务端能力（已解析形态，供断言使用）。 */
export const STRUCTURED_CAPABILITIES: ServerCapabilities =
  readServerCapabilities(STRUCTURED_CAPABILITIES_WIRE);

export const HUMAN_ONLY_CAPABILITIES: ServerCapabilities =
  readServerCapabilities(HUMAN_ONLY_CAPABILITIES_WIRE);

/** 旧服务端：没有 server_capabilities。 */
export const LEGACY_CAPABILITIES: ServerCapabilities =
  readServerCapabilities(undefined);

export const WORLD_ID = "world-fixture-1";

export const PUBLIC_TARGETS = [
  { kind: "npc", id: "bryce_fallon", name: "布莱斯·法伦" },
  { kind: "npc", id: "john_whitcroft", name: "约翰·惠特克罗夫特" },
  { kind: "investigator", id: "inv-alice", name: "爱丽丝" },
  { kind: "investigator", id: "inv-bob", name: "鲍勃" },
];

export const PUBLIC_DESTINATIONS = [
  { id: "miskatonic_university", name: "密斯卡托尼克大学" },
  { id: "miskatonic_medical", name: "密斯卡托尼克大学医学院" },
  { id: "wright_office", name: "莱特的办公室" },
];

export const KNOWN_CLUES = [
  {
    id: "clue_death_certificate",
    category: "investigation",
    text: "莱特的死亡证明由惠特克罗夫特医生签署。",
    presentation: ["describe", "image"],
  },
  {
    id: "clue_burned_letters",
    category: "investigation",
    text: "壁炉里残留着匆忙焚烧的信件碎片。",
    presentation: ["describe"],
  },
];

export const KNOWN_ITEMS = [
  {
    id: "item_press_pass",
    label: "记者证",
    quantity: 1,
    operations: ["show", "inspect"],
  },
  {
    id: "item_bandage",
    label: "绷带",
    quantity: 3,
    operations: ["apply", "give"],
  },
];

function event(
  eventId: number,
  revision: number,
  type: string,
  payload: Record<string, unknown>,
  sequence = eventId,
  causeRequestId: string | null = null,
) {
  return {
    protocol_version: V,
    event_id: eventId,
    world_id: WORLD_ID,
    sequence,
    revision,
    type,
    cause_request_id: causeRequestId,
    payload,
  };
}

/** 事件夹具：覆盖状态机、检定、掷骰、场景、线索、物品与错误码。 */
export const EVENT_FIXTURES = {
  snapshot: event(1, 12, "session_snapshot", {
    revision: 12,
    execution_profile: "structured_v1",
    server_capabilities: {
      protocol_version: V,
      structured_protocol: true,
      execution_profile: "structured_v1",
      keeper_modes: ["human", "assisted", "agent"],
      keeper_console: true,
      free_roll: true,
      assisted_draft: true,
      agent_takeover: true,
      commands: STRUCTURED_CAPABILITIES.commands,
    },
    keeper: { user_id: "user-keeper", mode: "human" },
    scene: { id: "miskatonic_university", name: "密斯卡托尼克大学" },
    destinations: PUBLIC_DESTINATIONS,
    investigator_id: "inv-alice",
    targets: PUBLIC_TARGETS,
    clues: KNOWN_CLUES,
    items: KNOWN_ITEMS,
    requests: [],
    pending_checks: [],
    cursor: { event_id: 1, revision: 12 },
  }),
  actionAck: event(2, 12, "action_ack", {
    request_id: "req-present-1",
    status: "queued",
  }),
  actionProcessing: event(3, 12, "action_status", {
    request_id: "req-present-1",
    status: "processing",
  }),
  actionAwaitingCheck: event(4, 12, "action_status", {
    request_id: "req-present-1",
    status: "awaiting_player",
    detail: "守秘人请你先做一次检定。",
  }),
  actionCompleted: event(9, 13, "action_status", {
    request_id: "req-present-1",
    status: "completed",
    outcome: "success",
    detail: "惠特克罗夫特医生认出了自己的签名。",
  }),
  actionDeclined: event(10, 13, "action_status", {
    request_id: "req-declined-1",
    status: "declined",
    outcome: "not_executed",
    detail: "你手上没有可以出示的实物。",
  }),
  actionPaused: event(11, 13, "action_status", {
    request_id: "req-paused-1",
    status: "paused",
    detail: "守秘人暂时离线，你的请求已保留。",
  }),
  checkRequested: event(5, 12, "check_requested", {
    check_request_id: "chk-1",
    investigator_id: "inv-alice",
    skill: "说服",
    difficulty: "regular",
    bonus_penalty: 0,
    attempt: "向医生说明你想查看遗体的理由。",
    known_cost: "可能需要出示证件",
    visibility: "public",
  }),
  checkResolved: event(8, 12, "check_resolved", {
    check_request_id: "chk-1",
    investigator_id: "inv-alice",
    skill: "说服",
    target_value: 55,
    roll: 23,
    outcome: "success",
    detail: "23 ≤ 55，说服成功。",
  }),
  checkCancelled: event(12, 13, "check_cancelled", {
    check_request_id: "chk-1",
    reason: "相关条件已变化，守秘人撤回检定。",
  }),
  rollResolved: event(6, 12, "roll_resolved", {
    request_id: "req-roll-1",
    expression: "1d100",
    dice: [{ sides: 100, values: [42] }],
    total: 42,
    modifier: 0,
  }),
  sceneChanged: event(7, 13, "scene_changed", {
    scene: { id: "miskatonic_medical", name: "密斯卡托尼克大学医学院" },
    destinations: [
      { id: "miskatonic_university", name: "密斯卡托尼克大学" },
      { id: "miskatonic_history", name: "密斯卡托尼克大学历史系研究生自习室" },
    ],
  }),
  clueGranted: event(13, 13, "clue_granted", {
    investigator_id: "inv-alice",
    clue_id: "clue_autopsy_note",
    category: "investigation",
    text: "惠特克罗夫特医生承认死亡证明是在压力下签署的。",
  }),
  inventoryChanged: event(14, 13, "inventory_changed", {
    investigator_id: "inv-alice",
    items: [
      { id: "item_press_pass", label: "记者证", quantity: 1 },
      { id: "item_bandage", label: "绷带", quantity: 2 },
    ],
  }),
  stateChanged: event(15, 13, "state_changed", {
    investigator_id: "inv-alice",
    hp: 11,
    max_hp: 12,
    san: 58,
    max_san: 65,
    conditions: [],
  }),
  revisionConflict: event(16, 14, "request_error", {
    request_id: "req-present-1",
    code: "revision_conflict",
    message: "世界版本已从 12 变为 13",
    retryable: true,
  }),
  staleTarget: event(17, 13, "request_error", {
    request_id: "req-use-1",
    code: "stale_target",
    message: "目标 NPC 已离开当前场景",
    retryable: false,
  }),
  /** M0 之后可能出现的未知错误码：前端必须用服务端文案兜底，不能空白。 */
  unknownCode: event(18, 13, "request_error", {
    request_id: "req-x",
    code: "code_from_future",
    message: "服务端新增的拒绝原因",
    retryable: false,
  }),
} as const;

export type StructuredFixtureEvent =
  (typeof EVENT_FIXTURES)[keyof typeof EVENT_FIXTURES];
