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
import { parseRulingValue, type RulingState } from "./rulings";
import { CONDITION_KINDS } from "./conditions";
import { TIME_ACTIVITIES } from "./time-activity";
import { keeperDiceProblem } from "./keeper-dice";
import { readHolder } from "./item-holders";

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
  | "speaker"
  | "primitive";

/** 候选来源：全部来自服务端公开投影，前端不自己编候选。 */
export type CandidateSource =
  | "flags"
  | "holders"
  | "combatants"
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
  /** 展示元数据，不改变协议字段类型。 */
  multiline?: boolean;
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
    kind: "keeper_roll",
    label: "主持普通骰",
    group: "检定",
    help: "不需要认领调查员。只产生随机数，不结算技能、伤害或剧情；未指定接收范围时仅主持可见。不能代掷玩家待检定。",
    fields: [
      {
        name: "spec",
        label: "骰式",
        kind: "text",
        required: true,
        maxLength: 40,
      },
      {
        name: "visibility",
        label: "接收范围",
        kind: "enum",
        required: false,
        enumValues: ["keeper", "public"],
      },
    ],
  },
  {
    kind: "record_condition",
    label: "记录人物状态",
    group: "结算与事实",
    help: "仅人类主持使用。只调整状态标记，不增加 HP。解除昏迷或濒死前须已恢复生命；死亡不能解除。已有战斗准备会失效，需重新批准。",
    fields: [
      INVESTIGATOR,
      {
        name: "condition",
        label: "状态",
        kind: "enum",
        required: true,
        enumValues: CONDITION_KINDS,
      },
      {
        name: "operation",
        label: "变更",
        kind: "enum",
        required: true,
        enumValues: ["add", "remove"],
      },
      {
        name: "expected_present",
        label: "本次核对",
        kind: "bool",
        required: true,
        help: "只读前状态；实际记录有变化时，请核对后再提交。",
      },
      {
        name: "basis",
        label: "裁定依据",
        kind: "text",
        required: true,
        multiline: true,
        minLength: 1,
        maxLength: 1000,
      },
    ],
  },
  {
    kind: "record_ruling",
    label: "裁定剧情条件",
    group: "结算与事实",
    help: "仅人类主持使用。裁定只改变已有剧情条件，不代替叙事，也不立即结算结局；请先核实并写明依据。",
    fields: [
      {
        name: "flag_id",
        label: "剧情条件（模组编号）",
        kind: "id",
        candidate: "flags",
        required: true,
      },
      {
        name: "expected_before",
        label: "裁定前的状态",
        kind: "primitive",
        required: true,
      },
      {
        name: "value",
        label: "裁定后的状态",
        kind: "primitive",
        required: true,
      },
      {
        name: "basis",
        label: "裁定依据",
        kind: "text",
        multiline: true,
        minLength: 1,
        maxLength: 1000,
        required: true,
      },
    ],
  },
  {
    kind: "combat_start",
    label: "开始遭遇",
    group: "结算与事实",
    help: "从当前场景选参战者；角色属性由服务端读取，不能临时覆盖。",
    fields: [
      {
        name: "participants",
        label: "参战者",
        kind: "id_list",
        required: true,
        candidate: "combatants",
      },
      {
        name: "reason",
        label: "发生原因",
        kind: "text",
        required: false,
        maxLength: 4000,
        multiline: true,
      },
    ],
  },
  {
    kind: "combat_action",
    label: "批准战斗动作",
    group: "结算与事实",
    help: "玩家动作批准后仍要等本人选择或掷骰；主持不能代选玩家的防御。",
    fields: [
      {
        name: "actor_id",
        label: "行动者",
        kind: "id",
        required: true,
        candidate: "combatants",
      },
      {
        name: "action_type",
        label: "动作",
        kind: "enum",
        required: true,
        enumValues: ["melee", "firearm", "threat", "move", "other"],
      },
      {
        name: "target_id",
        label: "目标",
        kind: "id",
        required: false,
        candidate: "combatants",
      },
      {
        name: "description",
        label: "动作说明",
        kind: "text",
        required: false,
        maxLength: 4000,
        multiline: true,
      },
      {
        name: "skill",
        label: "技能键",
        kind: "text",
        required: false,
        maxLength: 160,
      },
      {
        name: "weapon",
        label: "武器",
        kind: "text",
        required: false,
        maxLength: 160,
        help: "未指定物品编号的旧命令兼容描述；指定编号时请留空。",
      },
      {
        name: "weapon_item_id",
        label: "武器物品",
        kind: "id",
        required: false,
        candidate: "items",
        help: "从行动者持有物品中选择；编号绑定本件，不能换另一把同名武器。",
      },
      {
        name: "damage_spec",
        label: "伤害骰",
        kind: "text",
        required: false,
        maxLength: 20,
      },
      {
        name: "damage_mode",
        label: "伤害模式",
        kind: "enum",
        required: false,
        enumValues: ["normal", "impaling", "blunt"],
      },
      {
        name: "defender_choice",
        label: "NPC 防御选择",
        kind: "text",
        required: false,
        maxLength: 160,
      },
      {
        name: "bonus_dice",
        label: "奖励骰",
        kind: "int",
        required: false,
        min: 0,
        max: 2,
      },
      {
        name: "penalty_dice",
        label: "惩罚骰",
        kind: "int",
        required: false,
        min: 0,
        max: 2,
      },
    ],
  },
  {
    kind: "combat_end",
    label: "结束遭遇",
    group: "结算与事实",
    fields: [
      {
        name: "reason",
        label: "结束原因",
        kind: "text",
        required: true,
        maxLength: 4000,
        multiline: true,
      },
    ],
  },
  {
    kind: "end_game",
    label: "结算案件",
    group: "结算与事实",
    help: "模组有结局时按结局 ID 校验前置事实；先结束战斗。奖励不会自动写回个人角色库。",
    fields: [
      {
        name: "ending_id",
        label: "模组结局 ID",
        kind: "text",
        required: false,
        maxLength: 160,
      },
      {
        name: "ending_type",
        label: "结局类型",
        kind: "enum",
        required: false,
        enumValues: ["good", "secret", "neutral", "bad"],
      },
      {
        name: "title",
        label: "结局标题",
        kind: "text",
        required: false,
        maxLength: 4000,
      },
      {
        name: "summary",
        label: "结局叙述",
        kind: "text",
        required: false,
        maxLength: 4000,
        multiline: true,
      },
    ],
  },
  {
    kind: "control_keeper",
    label: "主持控制权",
    group: "结算与事实",
    help: "接管会停止旧 AI 运行；归还后新行动交给 AI；重试只恢复选定的暂停请求。",
    fields: [
      {
        name: "action",
        label: "操作",
        kind: "enum",
        required: true,
        enumValues: ["take", "release", "retry"],
      },
      {
        name: "request_id",
        label: "暂停请求（重试时必填）",
        kind: "id",
        required: false,
        candidate: "requests",
      },
    ],
  },
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
    help: "只发信息不会取得物品。结算实际发现须选择作者规则和发现者；勾选取得实物后才落入该发现者背包。需要检定时关联同目标已成功检定。",
    fields: [
      {
        name: "discovery_rule_index",
        label: "发现规则编号（可选，从 0 开始）",
        kind: "int",
        required: false,
        min: 0,
        max: 999,
      },
      {
        ...INVESTIGATOR,
        name: "discovery_investigator_id",
        label: "发现者／实物持有人",
        required: false,
      },
      {
        name: "check_request_id",
        label: "已成功检定编号（需要检定时）",
        kind: "text",
        required: false,
        maxLength: 160,
      },
      {
        name: "acquire_item",
        label: "确认取得作者声明的实物（不是仅阅读）",
        kind: "bool",
        required: false,
      },
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
    help: "准备表单不执行行动。只有勾选「扣减物品」并提交才会消耗；结算物品后，玩家请求还需在「准备裁定」中收尾。",
    fields: [
      INVESTIGATOR,
      {
        name: "effect_clue_id",
        label: "作者使用效果（可选）",
        kind: "id",
        required: false,
        candidate: "clues",
      },
      {
        name: "effect_rule_index",
        label: "使用规则编号（从 0 开始）",
        kind: "int",
        required: false,
        min: 0,
        max: 999,
      },
      {
        name: "basis",
        label: "效果适用依据（选择作者效果时必填）",
        kind: "text",
        required: false,
        maxLength: 500,
        multiline: true,
      },
      {
        name: "check_request_id",
        label: "已成功检定编号（需要检定时）",
        kind: "text",
        required: false,
        maxLength: 160,
      },
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
    help: "按实际持有物选择来源和去向；可以转交给调查员、NPC或放在场景中。只有服务端提交成功才改变归属；转交不等于告知线索内容。",
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
        name: "from_holder",
        label: "来源",
        kind: "id",
        required: true,
        candidate: "holders",
      },
      {
        name: "to_holder",
        label: "去向",
        kind: "id",
        required: true,
        candidate: "holders",
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
    help: "按活动类型计入模组的时间规则；未指定时按等待计时。原因只作说明，不改变活动类型。这里只计时，不代替移动、检定或战斗；不要重复结算已扣除的时间。",
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
        name: "activity",
        label: "活动类型",
        kind: "enum",
        required: false,
        enumValues: TIME_ACTIVITIES,
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
        multiline: true,
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
        maxLength: 1607,
        multiline: true,
        help: "最多 8 条，每条 200 字；一行一条，留空不记录条件。只记录已告知内容，不执行行动。",
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
        multiline: true,
      },
      {
        name: "remaining_steps",
        label: "剩余步骤",
        kind: "text",
        required: false,
        maxLength: 500,
        multiline: true,
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
    help: "批准会执行原草稿；拒绝不会执行；edited 仅关闭原草稿，修改内容需另行提交。",
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
  holders?: { id: string; name: string }[];
  combatants?: { id: string; name: string }[];
  flags?: (RulingState["flags"][number] & { name: string })[];
  investigators: { id: string; name: string }[];
  npcs: { id: string; name: string }[];
  objects?: { id: string; name: string }[];
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
  if (source === "flags") return candidates.flags || [];
  if (source === "combatants")
    return (
      candidates.combatants || [...candidates.investigators, ...candidates.npcs]
    ).filter(
      (entry, index, all) =>
        all.findIndex((other) => other.id === entry.id) === index,
    );
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
  if (spec.kind === "record_condition") {
    return {
      investigator_id: String(values.investigator_id || ""),
      condition: String(values.condition || ""),
      operation: String(values.operation || ""),
      // Required false is meaningful; never omit or coerce an unverified value.
      expected_present:
        typeof values.expected_present === "boolean"
          ? values.expected_present
          : null,
      basis: String(values.basis || "").trim(),
    };
  }
  if (spec.kind === "record_ruling") {
    return {
      flag_id: String(values.flag_id || ""),
      value: parseRulingValue(values.value),
      expected_before: parseRulingValue(values.expected_before),
      basis: String(values.basis || "").trim(),
    };
  }
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
    if (
      field.name.startsWith("audience_") ||
      (field.name.startsWith("target_") && spec.kind !== "combat_action")
    )
      continue;
    if (
      field.name === "from_investigator_id" ||
      field.name === "to_investigator_id" ||
      field.name === "from_holder" ||
      field.name === "to_holder"
    )
      continue;
    const value = values[field.name];
    if (field.kind === "bool") {
      if (value === true) payload[field.name] = true;
      continue;
    }
    if (field.kind === "id_list") {
      const list = idListFrom(values, field.name);
      if (list.length)
        payload[field.name] =
          spec.kind === "combat_start" && field.name === "participants"
            ? list.map((id) => ({ id }))
            : list;
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
    payload.from = readHolder(values.from_holder) || {
      kind: "investigator",
      id: String(values.from_investigator_id ?? ""),
    };
    payload.to = readHolder(values.to_holder) || {
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
  if (spec.kind === "grant_clue") {
    const rule = String(values.discovery_rule_index ?? "").trim();
    const actor = String(values.discovery_investigator_id ?? "").trim();
    if (rule && !actor) errors.push("结算发现时请选择发现者／实物持有人。");
    if (
      !rule &&
      (actor ||
        values.acquire_item === true ||
        String(values.check_request_id ?? "").trim())
    )
      errors.push("请选择明确的模组发现规则，不能只勾选取得物品。");
    if (
      actor &&
      !idListFrom(values, "recipient_investigator_ids").includes(actor)
    )
      errors.push("发现者必须包含在线索接收调查员中。");
  }
  if (spec.kind === "use_item") {
    const effect = String(values.effect_clue_id ?? "").trim();
    const rule = String(values.effect_rule_index ?? "").trim();
    const basis = String(values.basis ?? "").trim();
    if (
      (effect ||
        rule ||
        basis ||
        String(values.check_request_id ?? "").trim()) &&
      (!effect || !rule || !basis)
    )
      errors.push("结算作者使用效果时，请选择效果、规则并填写适用依据。");
  }
  for (const field of spec.fields) {
    const isAudiencePart = field.name.startsWith("audience_");
    const isTargetPart = field.name.startsWith("target_");
    const blank = (() => {
      if (field.kind === "bool") return false;
      // Composite target controls populate target_kind/target_id, not a fictitious
      // scalar "target" field. Keep the required check, but validate its real value.
      if (field.kind === "target") return targetFrom(values) === undefined;
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
    if (field.kind === "primitive" && (!blank || field.required)) {
      const value = parseRulingValue(values[field.name]);
      if (value === undefined || (field.name === "value" && value === null))
        errors.push(`请先选择剧情条件并填写「${field.label}」。`);
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
    if (!readHolder(values.from_holder)) errors.push("请选择实际来源持有者。");
    if (!readHolder(values.to_holder)) errors.push("请选择实际去向持有者。");
  }
  if (spec.kind === "present_information") {
    const target = targetFrom(values);
    if (!target) errors.push("请选择目标（或填写未解析目标）。");
  }
  if (spec.kind === "resolve_intent") {
    if (
      String(values.resolution ?? "").trim() === "awaiting_player" ||
      String(values.thread_action ?? "").trim()
    ) {
      const conditions = String(values.disclosed ?? "")
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean);
      if (conditions.length > 8)
        errors.push("已告知条件最多 8 条。请合并或删减，不会自动截断。");
      if (conditions.some((line) => line.length > 200))
        errors.push("已告知条件每条不能超过 200 字。");
    }
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
  if (spec.kind === "record_condition") {
    if (typeof values.expected_present !== "boolean")
      errors.push("请先核对当前人物状态记录。");
    if (values.condition === "dead" && values.operation === "remove")
      errors.push("死亡不能通过本工具解除；如需回滚请读档或创建分支。");
  }
  if (spec.kind === "keeper_roll") {
    const problem = keeperDiceProblem(String(values.spec ?? ""));
    if (problem) errors.push(problem);
  }
  return errors;
}

export function emptyKeeperValues(spec: KeeperCommandSpec): FieldValues {
  const values: FieldValues = {};
  for (const field of spec.fields) {
    if (spec.kind === "record_condition" && field.name === "expected_present")
      values[field.name] = "";
    else if (field.kind === "bool") values[field.name] = false;
    else if (field.kind === "enum" && field.enumValues?.length) {
      // 必填枚举预选第一项；**可选枚举保持空**——否则「可选」会被当成已选择，
      // 表单会凭空多提交一个字段（例如 resolve_intent 的 thread.action）。
      values[field.name] = field.required ? field.enumValues[0] : "";
    } else values[field.name] = "";
  }
  return values;
}
