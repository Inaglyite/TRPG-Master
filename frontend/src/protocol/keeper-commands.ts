/**
 * keeper-commands.ts — 主持命令的字段表，逐项对照
 * `schemas/structured-play/v1/command_request.json`（M0 冻结）。
 *
 * 设计要点：**表单不手写字段**，而是由这张表生成输入控件与校验。
 * 契约变了只改这一张表，KeeperConsole 不会悄悄偏离 schema。
 * 表里每个字段都标了它在 schema 里的 required / 边界，便于对账。
 */

import {
  KEEPER_COMMAND_KINDS,
  SPEAKER_KINDS,
  type Audience,
  type ActionTarget,
  type KeeperCommandKind,
  type SpeakerKind,
} from "./structured";

/** 字段表必须覆盖且不超过 M0 的命令集合（测试会断言两边一致）。 */
export const KEEPER_COMMAND_KIND_SET: readonly string[] = KEEPER_COMMAND_KINDS;
export type { KeeperCommandKind };

export type FieldKind =
  | "id"
  | "id_list"
  | "enum"
  | "int"
  | "text"
  | "bool"
  | "target"
  | "audience"
  | "speaker";

/** 候选来源：全部来自服务端公开投影，前端不自己编候选。 */
export type CandidateSource =
  | "investigators"
  | "npcs"
  | "scenes"
  | "clues"
  | "items"
  | "assets"
  | "requests"
  | "threads";

export type CommandField = {
  name: string;
  label: string;
  kind: FieldKind;
  required: boolean;
  enumValues?: readonly string[];
  min?: number;
  max?: number;
  maxLength?: number;
  minLength?: number;
  candidate?: CandidateSource;
  help?: string;
};

export type KeeperCommandSpec = {
  kind: string;
  label: string;
  group: "发言与线索" | "检定" | "角色与物品" | "时间与移动" | "结算与事实";
  fields: CommandField[];
  help?: string;
};

/** investigator_id 字段在所有命令里都是必填的调查员 ID。 */
const INVESTIGATOR: CommandField = {
  name: "investigator_id",
  label: "调查员",
  kind: "id",
  required: true,
  candidate: "investigators",
};

const TARGET_FIELD: CommandField = {
  name: "target",
  label: "目标（可选）",
  kind: "target",
  required: false,
  help: "未解析目标服务端不会直接执行，可留空。",
};

export const KEEPER_COMMANDS: KeeperCommandSpec[] = [
  {
    kind: "publish_message",
    label: "以某个身份发言",
    group: "发言与线索",
    help: "普通玩家只能以自己的调查员身份发言；keeper/npc/system 身份用于主持旁白与 NPC。",
    fields: [
      {
        name: "speaker_kind",
        label: "发言身份",
        kind: "enum",
        required: true,
        enumValues: SPEAKER_KINDS,
      },
      {
        name: "speaker_id",
        label: "身份 ID",
        kind: "id",
        required: false,
        candidate: "npcs",
      },
      {
        name: "audience_kind",
        label: "接收范围",
        kind: "enum",
        required: true,
        enumValues: ["public", "keeper", "investigators"],
      },
      {
        name: "audience_investigator_ids",
        label: "接收调查员",
        kind: "id_list",
        required: false,
        candidate: "investigators",
      },
      {
        name: "text",
        label: "内容",
        kind: "text",
        required: true,
        minLength: 1,
        maxLength: 2000,
      },
      {
        name: "in_character",
        label: "以角色口吻",
        kind: "bool",
        required: false,
      },
    ],
  },
  {
    kind: "present_information",
    label: "出示信息",
    group: "发言与线索",
    help: "主持人代为出示：说明内容 / 展示图片 / 展示原件。",
    fields: [
      {
        name: "clue_id",
        label: "线索",
        kind: "id",
        required: true,
        candidate: "clues",
      },
      {
        name: "presentation",
        label: "出示方式",
        kind: "enum",
        required: true,
        enumValues: ["describe", "image", "original"],
      },
      { name: "target", label: "目标", kind: "target", required: true },
      {
        name: "note",
        label: "备注",
        kind: "text",
        required: false,
        maxLength: 500,
      },
    ],
  },
  {
    kind: "grant_clue",
    label: "定向发放线索",
    group: "发言与线索",
    help: "知情授权按接收者显式记录，不是全局开关；可同时展示授权图片。",
    fields: [
      {
        name: "clue_id",
        label: "线索",
        kind: "id",
        required: true,
        candidate: "clues",
      },
      {
        name: "recipient_investigator_ids",
        label: "接收调查员",
        kind: "id_list",
        required: true,
        candidate: "investigators",
      },
      {
        name: "basis",
        label: "依据",
        kind: "text",
        required: true,
        minLength: 1,
        maxLength: 500,
      },
      {
        name: "present_asset_id",
        label: "同时展示素材",
        kind: "id",
        required: false,
        candidate: "assets",
      },
      {
        name: "note",
        label: "备注",
        kind: "text",
        required: false,
        maxLength: 500,
      },
    ],
  },
  {
    kind: "present_handout",
    label: "展示素材",
    group: "发言与线索",
    fields: [
      {
        name: "asset_id",
        label: "素材",
        kind: "id",
        required: true,
        candidate: "assets",
      },
      {
        name: "recipient_investigator_ids",
        label: "接收调查员",
        kind: "id_list",
        required: true,
        candidate: "investigators",
      },
      {
        name: "caption",
        label: "说明",
        kind: "text",
        required: false,
        maxLength: 200,
      },
    ],
  },
  {
    kind: "request_check",
    label: "请求检定",
    group: "检定",
    help: "创建持久待办；玩家点击后才由服务端结算一次。",
    fields: [
      INVESTIGATOR,
      {
        name: "skill",
        label: "技能",
        kind: "text",
        required: true,
        minLength: 1,
        maxLength: 60,
      },
      {
        name: "difficulty",
        label: "难度",
        kind: "enum",
        required: true,
        enumValues: ["regular", "hard", "extreme"],
      },
      {
        name: "bonus_penalty",
        label: "奖惩骰",
        kind: "int",
        required: false,
        min: -2,
        max: 2,
        help: "正数=奖励骰，负数=惩罚骰。",
      },
      {
        name: "attempt",
        label: "尝试描述",
        kind: "text",
        required: true,
        minLength: 1,
        maxLength: 300,
      },
      {
        name: "known_cost",
        label: "已知代价",
        kind: "text",
        required: false,
        maxLength: 200,
      },
      TARGET_FIELD,
      {
        name: "visibility",
        label: "可见性",
        kind: "enum",
        required: true,
        enumValues: ["public", "keeper"],
      },
      {
        name: "related_request_id",
        label: "关联请求",
        kind: "id",
        required: false,
        candidate: "requests",
      },
      {
        name: "time_cost_minutes",
        label: "额外耗时（分钟）",
        kind: "int",
        required: false,
        min: 0,
        max: 10080,
      },
    ],
  },
  {
    kind: "resolve_check",
    label: "结算检定",
    group: "检定",
    help: "服务端掷骰一次并保存结果；重复调用不会重掷。",
    fields: [
      {
        name: "check_request_id",
        label: "检定请求",
        kind: "id",
        required: true,
        candidate: "requests",
      },
      { name: "push", label: "孤注一掷", kind: "bool", required: false },
    ],
  },
  {
    kind: "adjust_stat",
    label: "调整 HP / SAN",
    group: "角色与物品",
    fields: [
      INVESTIGATOR,
      {
        name: "field",
        label: "字段",
        kind: "enum",
        required: true,
        enumValues: ["hp", "san", "max_hp", "max_san"],
      },
      {
        name: "delta",
        label: "变化值",
        kind: "int",
        required: true,
        min: -99,
        max: 99,
      },
      {
        name: "reason",
        label: "原因",
        kind: "text",
        required: true,
        minLength: 1,
        maxLength: 300,
      },
    ],
  },
  {
    kind: "use_item",
    label: "代为使用物品",
    group: "角色与物品",
    fields: [
      INVESTIGATOR,
      {
        name: "item_id",
        label: "物品",
        kind: "id",
        required: true,
        candidate: "items",
      },
      {
        name: "quantity",
        label: "数量",
        kind: "int",
        required: true,
        min: 1,
        max: 999,
      },
      {
        name: "operation",
        label: "用法",
        kind: "text",
        required: true,
        minLength: 1,
        maxLength: 60,
      },
      TARGET_FIELD,
      {
        name: "approach",
        label: "补充做法",
        kind: "text",
        required: false,
        maxLength: 200,
      },
      { name: "consume", label: "扣减物品", kind: "bool", required: false },
      {
        name: "result_note",
        label: "结果说明",
        kind: "text",
        required: false,
        maxLength: 500,
      },
    ],
  },
  {
    kind: "transfer_item",
    label: "转移物品",
    group: "角色与物品",
    fields: [
      {
        name: "item_id",
        label: "物品",
        kind: "id",
        required: true,
        candidate: "items",
      },
      {
        name: "quantity",
        label: "数量",
        kind: "int",
        required: true,
        min: 1,
        max: 999,
      },
      {
        name: "from_investigator_id",
        label: "来源",
        kind: "id",
        required: true,
        candidate: "investigators",
      },
      {
        name: "to_investigator_id",
        label: "去向",
        kind: "id",
        required: true,
        candidate: "investigators",
      },
      {
        name: "note",
        label: "备注",
        kind: "text",
        required: false,
        maxLength: 500,
      },
    ],
  },
  {
    kind: "advance_time",
    label: "推进时间",
    group: "时间与移动",
    fields: [
      {
        name: "minutes",
        label: "分钟",
        kind: "int",
        required: true,
        min: 0,
        max: 10080,
      },
      {
        name: "reason",
        label: "原因",
        kind: "text",
        required: true,
        maxLength: 200,
      },
      {
        name: "related_request_id",
        label: "关联请求",
        kind: "id",
        required: false,
        candidate: "requests",
      },
    ],
  },
  {
    kind: "move_party",
    label: "整队移动",
    group: "时间与移动",
    help: "只有提交成功的移动才更新公开场景；抵达不等于调查。",
    fields: [
      {
        name: "destination_scene_id",
        label: "目的地",
        kind: "id",
        required: true,
        candidate: "scenes",
      },
      {
        name: "investigator_ids",
        label: "限定调查员",
        kind: "id_list",
        required: false,
        candidate: "investigators",
      },
      {
        name: "travel_minutes",
        label: "路程（分钟）",
        kind: "int",
        required: false,
        min: 0,
        max: 1440,
      },
      {
        name: "transition_note",
        label: "过渡说明",
        kind: "text",
        required: false,
        maxLength: 500,
      },
    ],
  },
  {
    kind: "set_npc_presence",
    label: "NPC 出入场",
    group: "时间与移动",
    fields: [
      {
        name: "npc_id",
        label: "NPC",
        kind: "id",
        required: true,
        candidate: "npcs",
      },
      {
        name: "scene_id",
        label: "场景",
        kind: "id",
        required: true,
        candidate: "scenes",
      },
      {
        name: "presence",
        label: "动作",
        kind: "enum",
        required: true,
        enumValues: ["enter", "leave"],
      },
    ],
  },
  {
    kind: "resolve_intent",
    label: "处理玩家意图",
    group: "结算与事实",
    help: "明确结束或挂起一个玩家请求，避免无限等待。",
    fields: [
      {
        name: "request_id",
        label: "玩家请求",
        kind: "id",
        required: true,
        candidate: "requests",
      },
      {
        name: "resolution",
        label: "处理结果",
        kind: "enum",
        required: true,
        enumValues: [
          "completed",
          "declined",
          "cancelled",
          "paused",
          "awaiting_player",
        ],
      },
      {
        name: "thread_action",
        label: "交互线程操作（可选）",
        kind: "enum",
        required: false,
        enumValues: ["open", "continue", "close", "replace"],
      },
      {
        name: "thread_id",
        label: "线程 ID（continue/close/replace 时填）",
        kind: "id",
        required: false,
        candidate: "threads",
      },
      {
        name: "waiting_on",
        label: "在等谁回应（调查员 ID，可选）",
        kind: "id",
        required: false,
      },
      {
        name: "pending_action_kind",
        label: "尚未执行（等待玩家时填写）",
        kind: "enum",
        required: false,
        enumValues: ["move", "freeform", "present_clue", "use_item", "other"],
      },
      {
        name: "pending_action_note",
        label: "尚未执行什么",
        kind: "text",
        required: false,
        maxLength: 300,
      },
      {
        name: "pending_action_destination",
        label: "待前往目的地（未出发）",
        kind: "text",
        required: false,
        maxLength: 160,
      },
      {
        name: "disclosed",
        label: "已告知条件（一行一条，避免重复劝留）",
        kind: "text",
        required: false,
        maxLength: 800,
      },
      {
        name: "outcome",
        label: "领域结果",
        kind: "enum",
        required: false,
        enumValues: ["success", "failure", "not_executed"],
      },
      {
        name: "note",
        label: "说明",
        kind: "text",
        required: false,
        maxLength: 500,
      },
      {
        name: "remaining_steps",
        label: "剩余步骤",
        kind: "text",
        required: false,
        maxLength: 500,
      },
    ],
  },
  {
    kind: "record_memory",
    label: "记录角色记忆",
    group: "结算与事实",
    help: "把角色经历/被告知/传闻/推测记为长期记忆（主持显式记录；不是权威世界状态）。",
    fields: [
      {
        name: "character_id",
        label: "角色",
        kind: "id",
        required: true,
        candidate: "investigators",
      },
      {
        name: "knowledge_type",
        label: "知识类型",
        kind: "enum",
        required: true,
        enumValues: ["experienced", "told", "rumor", "belief"],
      },
      {
        name: "character_kind",
        label: "角色类型",
        kind: "enum",
        required: false,
        enumValues: ["investigator", "npc"],
      },
      {
        name: "content",
        label: "内容（≤500 字）",
        kind: "text",
        required: true,
        maxLength: 500,
      },
      {
        name: "scene_id",
        label: "场景（可选）",
        kind: "text",
        required: false,
        maxLength: 160,
      },
      {
        name: "topics",
        label: "主题（逗号分隔，≤6）",
        kind: "text",
        required: false,
        maxLength: 240,
      },
      {
        name: "subjects",
        label: "涉及对象（逗号分隔）",
        kind: "text",
        required: false,
        maxLength: 240,
      },
      {
        name: "supersedes",
        label: "替代的记忆 ID（可选）",
        kind: "text",
        required: false,
        maxLength: 160,
      },
    ],
  },
  {
    kind: "resolve_draft",
    label: "处理主持草稿",
    group: "结算与事实",
    help: "assisted 模式：批准 / 拒绝 / 修改后执行 Agent 提出的草稿。",
    fields: [
      { name: "draft_id", label: "草稿", kind: "id", required: true },
      {
        name: "decision",
        label: "处理",
        kind: "enum",
        required: true,
        enumValues: ["approved", "rejected", "edited"],
      },
      {
        name: "note",
        label: "说明",
        kind: "text",
        required: false,
        maxLength: 500,
      },
    ],
  },
  {
    kind: "record_fact",
    label: "记录即兴事实",
    group: "结算与事实",
    fields: [
      {
        name: "text",
        label: "事实",
        kind: "text",
        required: true,
        minLength: 1,
        maxLength: 1000,
      },
      {
        name: "audience_kind",
        label: "知情范围",
        kind: "enum",
        required: true,
        enumValues: ["public", "keeper", "investigators"],
      },
      {
        name: "audience_investigator_ids",
        label: "知情调查员",
        kind: "id_list",
        required: false,
        candidate: "investigators",
      },
      {
        name: "source",
        label: "来源",
        kind: "enum",
        required: false,
        enumValues: ["keeper", "module", "ruling"],
      },
    ],
  },
];

export function findKeeperCommand(kind: string): KeeperCommandSpec | null {
  return KEEPER_COMMANDS.find((command) => command.kind === kind) ?? null;
}

/** 控制台里显示的候选 ID：全部来自服务端公开投影。 */
export type KeeperCandidates = {
  investigators: { id: string; name: string }[];
  npcs: { id: string; name: string }[];
  scenes: { id: string; name: string }[];
  clues: { id: string; name: string }[];
  items: { id: string; name: string }[];
  assets: { id: string; name: string }[];
  requests: { id: string; name: string }[];
  threads: { id: string; name: string }[];
};

export function candidatesFor(
  source: CandidateSource | undefined,
  candidates: KeeperCandidates,
): { id: string; name: string }[] {
  if (!source) return [];
  return candidates[source] ?? [];
}

export type FieldValues = Record<string, string | number | boolean>;

function idListFrom(values: FieldValues, name: string): string[] {
  const raw = values[name];
  if (typeof raw !== "string") return [];
  return raw
    .split(",")
    .map((entry) => entry.trim())
    .filter(Boolean);
}

function audienceFrom(values: FieldValues): Audience {
  const kind = String(values.audience_kind ?? "public");
  if (kind === "investigators") {
    return {
      kind: "investigators",
      investigator_ids: idListFrom(values, "audience_investigator_ids"),
    };
  }
  return { kind: kind === "keeper" ? "keeper" : "public" };
}

function targetFrom(values: FieldValues): ActionTarget | undefined {
  const id = String(values.target_id ?? "").trim();
  const kind = String(values.target_kind ?? "").trim();
  if (!id || !kind) return undefined;
  if (kind === "unresolved") return { kind: "unresolved", text: id };
  if (kind !== "npc" && kind !== "investigator" && kind !== "scene_object") {
    return undefined;
  }
  return { kind, id };
}

/** 主持表单 → M0 payload（只输出 schema 声明过的字段）。 */
export function buildKeeperPayload(
  spec: KeeperCommandSpec,
  values: FieldValues,
): Record<string, unknown> {
  const payload: Record<string, unknown> = {};
  for (const field of spec.fields) {
    switch (field.name) {
      case "speaker_kind":
      case "speaker_id":
        break;
      case "audience_kind":
      case "audience_investigator_ids":
        break;
      case "target_id":
      case "target_kind":
        break;
      case "from_investigator_id":
        payload.from = {
          kind: "investigator",
          id: String(values.from_investigator_id ?? ""),
        };
        break;
      case "to_investigator_id":
        payload.to = {
          kind: "investigator",
          id: String(values.to_investigator_id ?? ""),
        };
        break;
    }
  }
  if (spec.kind === "publish_message") {
    const speakerId = String(values.speaker_id ?? "").trim();
    payload.speaker = {
      kind: String(values.speaker_kind ?? "keeper") as SpeakerKind,
      ...(speakerId ? { id: speakerId } : {}),
    };
    payload.audience = audienceFrom(values);
    payload.text = String(values.text ?? "");
    if (values.in_character === true) payload.in_character = true;
    return payload;
  }
  if (spec.kind === "record_memory") {
    for (const field of spec.fields) {
      if (field.name === "topics" || field.name === "subjects") continue;
      const text = String(values[field.name] ?? "").trim();
      if (text) payload[field.name] = text;
    }
    for (const name of ["topics", "subjects"]) {
      const list = idListFrom(values, name);
      if (list.length) {
        payload[name] = name === "topics" ? list.slice(0, 6) : list;
      }
    }
    return payload;
  }
  if (spec.kind === "resolve_intent") {
    const resolutionValue = String(values.resolution ?? "").trim();
    // 复合字段不进顶层：thread 的 action/thread_id/waiting_on 归 payload.thread，
    // pending_action_* 与 disclosed 归它自己的位置（顶层或 thread 内）。
    const composite = new Set([
      "thread_action",
      "thread_id",
      "waiting_on",
      "disclosed",
    ]);
    for (const field of spec.fields) {
      if (
        field.name.startsWith("pending_action_") ||
        composite.has(field.name)
      ) {
        continue;
      }
      // 等待中的请求没有领域结果：不要把表单默认的 outcome 一起发出去。
      if (field.name === "outcome" && resolutionValue === "awaiting_player") {
        continue;
      }
      const text = String(values[field.name] ?? "").trim();
      if (text) payload[field.name] = text;
    }
    const threadAction = String(values.thread_action ?? "").trim();
    if (threadAction) {
      const threadId = String(values.thread_id ?? "").trim();
      const threadKind =
        String(values.pending_action_kind ?? "other").trim() || "other";
      const threadNote = String(values.pending_action_note ?? "").trim();
      const threadDestination = String(
        values.pending_action_destination ?? "",
      ).trim();
      const threadDisclosed = String(values.disclosed ?? "")
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean);
      payload.thread = {
        action: threadAction,
        ...(threadId ? { thread_id: threadId } : {}),
        ...(threadNote || threadDestination
          ? {
              pending_action: {
                kind: threadKind,
                ...(threadNote ? { note: threadNote } : {}),
                ...(threadDestination
                  ? { destination_scene_id: threadDestination }
                  : {}),
              },
            }
          : {}),
        ...(threadDisclosed.length ? { disclosed: threadDisclosed } : {}),
        ...(String(values.waiting_on ?? "").trim()
          ? { waiting_on: String(values.waiting_on).trim() }
          : {}),
      };
    }
    if (resolutionValue === "awaiting_player") {
      const kind =
        String(values.pending_action_kind ?? "other").trim() || "other";
      const note = String(values.pending_action_note ?? "").trim();
      const destination = String(
        values.pending_action_destination ?? "",
      ).trim();
      payload.pending_action = {
        kind,
        ...(note ? { note } : {}),
        ...(destination ? { destination_scene_id: destination } : {}),
      };
      const disclosed = String(values.disclosed ?? "")
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean);
      if (disclosed.length) payload.disclosed = disclosed;
    }
    return payload;
  }
  if (spec.kind === "record_fact") {
    const source = String(values.source ?? "").trim();
    return {
      text: String(values.text ?? ""),
      audience: audienceFrom(values),
      ...(source ? { source } : {}),
    };
  }
  for (const field of spec.fields) {
    if (field.name.startsWith("audience_") || field.name.startsWith("target_"))
      continue;
    if (
      field.name === "from_investigator_id" ||
      field.name === "to_investigator_id"
    )
      continue;
    const value = values[field.name];
    if (field.kind === "bool") {
      if (value === true) payload[field.name] = true;
      continue;
    }
    if (field.kind === "id_list") {
      const list = idListFrom(values, field.name);
      if (list.length) payload[field.name] = list;
      continue;
    }
    if (field.kind === "int") {
      const text = String(value ?? "").trim();
      if (text === "") continue;
      const parsed = Number(text);
      if (Number.isFinite(parsed)) payload[field.name] = Math.trunc(parsed);
      continue;
    }
    if (field.kind === "target") {
      const target = targetFrom(values);
      if (target) payload.target = target;
      continue;
    }
    const text = String(value ?? "").trim();
    if (text) payload[field.name] = text;
  }
  if (spec.kind === "transfer_item") {
    payload.from = {
      kind: "investigator",
      id: String(values.from_investigator_id ?? ""),
    };
    payload.to = {
      kind: "investigator",
      id: String(values.to_investigator_id ?? ""),
    };
  }
  return payload;
}

/** 表单校验：只拦“本地就能确定的错误”，语义判断留给服务端。 */
export function validateKeeperFields(
  spec: KeeperCommandSpec,
  values: FieldValues,
): string[] {
  const errors: string[] = [];
  for (const field of spec.fields) {
    const isAudiencePart = field.name.startsWith("audience_");
    const isTargetPart = field.name.startsWith("target_");
    const blank = (() => {
      if (
        field.name === "from_investigator_id" ||
        field.name === "to_investigator_id"
      ) {
        return true;
      }
      if (field.kind === "bool") return false;
      if (field.kind === "int")
        return String(values[field.name] ?? "").trim() === "";
      if (field.kind === "id_list")
        return idListFrom(values, field.name).length === 0;
      return String(values[field.name] ?? "").trim() === "";
    })();
    if (isAudiencePart) {
      if (field.name === "audience_investigator_ids") {
        if (
          String(values.audience_kind) === "investigators" &&
          idListFrom(values, "audience_investigator_ids").length === 0
        ) {
          errors.push("接收范围选择“指定调查员”时必须选择至少一名调查员。");
        }
      }
      continue;
    }
    if (isTargetPart) continue;
    if (field.required && blank) {
      errors.push(`请填写「${field.label}」。`);
      continue;
    }
    if (field.kind === "int" && !blank) {
      const parsed = Number(values[field.name]);
      if (!Number.isFinite(parsed) || !Number.isInteger(parsed)) {
        errors.push(`「${field.label}」需要是整数。`);
      } else if (
        (field.min !== undefined && parsed < field.min) ||
        (field.max !== undefined && parsed > field.max)
      ) {
        errors.push(`「${field.label}」需在 ${field.min}–${field.max} 之间。`);
      }
    }
    if (field.kind === "enum" && !blank && field.enumValues?.length) {
      const value = String(values[field.name]).trim();
      if (!field.enumValues.includes(value)) {
        errors.push(
          `「${field.label}」只能是 ${field.enumValues.join(" / ")}。`,
        );
      }
    }
    if (field.kind === "text" && !blank) {
      const text = String(values[field.name]).trim();
      if (field.minLength !== undefined && text.length < field.minLength) {
        errors.push(`「${field.label}」太短。`);
      }
      if (field.maxLength !== undefined && text.length > field.maxLength) {
        errors.push(`「${field.label}」不能超过 ${field.maxLength} 字。`);
      }
    }
    if (field.name === "clue_id" && blank) errors.push("请选择线索。");
  }
  if (spec.kind === "transfer_item") {
    if (!String(values.from_investigator_id ?? "").trim())
      errors.push("请选择来源调查员。");
    if (!String(values.to_investigator_id ?? "").trim())
      errors.push("请选择去向调查员。");
  }
  if (spec.kind === "present_information") {
    const target = targetFrom(values);
    if (!target) errors.push("请选择目标（或填写未解析目标）。");
  }
  if (spec.kind === "resolve_intent") {
    // 等待玩家自由回应：必须写清「尚未执行什么」，否则待办对玩家没有意义。
    if (String(values.resolution ?? "").trim() === "awaiting_player") {
      const note = String(values.pending_action_note ?? "").trim();
      const destination = String(
        values.pending_action_destination ?? "",
      ).trim();
      if (!note && !destination) {
        errors.push(
          "选择「等待玩家回应」时，请填写尚未执行什么或待前往目的地。",
        );
      }
    }
  }
  return errors;
}

export function emptyKeeperValues(spec: KeeperCommandSpec): FieldValues {
  const values: FieldValues = {};
  for (const field of spec.fields) {
    if (field.kind === "bool") values[field.name] = false;
    else if (field.kind === "enum" && field.enumValues?.length) {
      // 必填枚举预选第一项；**可选枚举保持空**——否则「可选」会被当成已选择，
      // 表单会凭空多提交一个字段（例如 resolve_intent 的 thread.action）。
      values[field.name] = field.required ? field.enumValues[0] : "";
    } else values[field.name] = "";
  }
  return values;
}
