import { z } from "zod";

export const serverMessageTypes = [
  "narrative_chunk",
  "narrative_segment",
  "narrative_segments",
  "chat_events",
  "tension",
  "dice_result",
  "glm_summary",
  "handout",
  "error",
  "choices",
  "pong",
  "gm_turn_start",
  "turn_phase",
  "suggest_check",
  "decision_request",
  "decision_resolved",
  "done",
  "turn_rewritten",
  "turn_rewrite_failed",
  "turn_recovery",
  "world_context",
  "world_list",
  "turn_branched",
  "turn_branch_failed",
  "world_switched",
  "world_switch_failed",
  "player_notes",
  "player_notes_conflict",
  "player_notes_error",
  "turn_rejected",
  "saved",
  "save_deleted",
  "save_renamed",
  "quit_ok",
  "game_over",
  "module_list",
  "character_list",
  "theme",
  "model_settings",
  "model_settings_error",
  "turn_diagnostics",
  "turn_performance",
  "save_list",
  "save_available",
  "loaded",
  "case_settled",
  "character_state",
  "state_data",
  "room_state",
  "room_full_state",
  "member_joined",
  "member_left",
  "member_removed",
  "owner_changed",
  "investigator_claimed",
  "investigator_released",
  "actor_changed",
  "room_action_rejected",
  "room_error",
  "room_event_gap",
  "private_event",
  "investigator_roster",
  "protocol_error",
] as const;

const serverMessageSchema = z.looseObject({
  type: z.enum(serverMessageTypes),
});

const avatarSchema = z.object({
  asset_url: z.string().max(2048).optional(),
  asset_data_uri: z.string().max(4_000_000).optional(),
  alt: z.string().max(160).optional(),
});

const speakerSchema = z.object({
  type: z.enum(["keeper", "npc", "investigator", "system"]),
  id: z.string().max(160).optional(),
  name: z.string().min(1).max(160),
  avatar: avatarSchema.optional(),
});

const investigatorActorSchema = z.object({
  type: z.literal("investigator"),
  user_id: z.string().min(1).max(160),
  investigator_id: z.string().max(160).nullable().optional(),
  name: z.string().min(1).max(160),
  avatar: avatarSchema.optional(),
});

const playerActionSchema = z.union([
  z.string().max(20_000),
  z.looseObject({
    content: z.string().max(20_000).optional(),
    text: z.string().max(20_000).optional(),
    actor: investigatorActorSchema.optional(),
  }),
]);

const gmTurnStartMessageSchema = z
  .looseObject({
    type: z.literal("gm_turn_start"),
    turn_id: z.string().min(1).max(160),
    seq: z.number().int().nonnegative().optional(),
    player_input: z.string().max(20_000).optional(),
    actor: investigatorActorSchema.optional(),
    player_action: playerActionSchema.optional(),
  })
  .superRefine((message, context) => {
    const hasPlayerInput =
      typeof message.player_input === "string" ||
      typeof message.player_action === "string" ||
      (message.player_action &&
        typeof message.player_action === "object" &&
        (typeof message.player_action.content === "string" ||
          typeof message.player_action.text === "string"));
    const nestedActor =
      message.player_action &&
      typeof message.player_action === "object" &&
      message.player_action.actor;
    if (hasPlayerInput && !message.actor && !nestedActor) {
      context.addIssue({
        code: "custom",
        message: "player action requires an authoritative actor",
      });
    }
  });

const chatEventSchema = z.object({
  event_id: z.string().max(160).optional(),
  kind: z.enum(["narration", "speech"]),
  text: z.string().max(200_000),
  npc_id: z.string().max(160).optional(),
  speaker: speakerSchema.optional(),
});

const chatEventsMessageSchema = z.object({
  type: z.literal("chat_events"),
  events: z.array(chatEventSchema).max(512),
});

const roomStateMessageSchema = z.looseObject({
  type: z.literal("room_state"),
  status: z.string(),
  owner_user_id: z.string().nullable().optional(),
  current_actor_user_id: z.string().nullable().optional(),
  ready_user_ids: z.array(z.string()).max(64),
  online_user_ids: z.array(z.string()).max(64),
  room_event_id: z.number().int().nonnegative().optional(),
});

const publicHistoryTurnSchema = z.looseObject({
  turn_id: z.string().min(1).max(160),
  player_input: z.string().max(20_000).nullable().optional(),
  actor: investigatorActorSchema.nullable().optional(),
});

// 首次连接与 gap 恢复时服务端发送的个性化快照；latest_event_id 是房间事件
// 序号（JSON number），客户端必须用它重置同步游标。history 为公共叙事
// （与单机世界历史同构），private_state 仅含当前用户的私有数据。
const roomFullStateMessageSchema = z.looseObject({
  type: z.literal("room_full_state"),
  latest_event_id: z.number().int().nonnegative(),
  status: z.string().optional(),
  owner_user_id: z.string().nullable().optional(),
  current_actor_user_id: z.string().nullable().optional(),
  ready_user_ids: z.array(z.string()).max(64).optional(),
  online_user_ids: z.array(z.string()).max(64).optional(),
  history: z.array(publicHistoryTurnSchema).max(10_000).optional(),
  investigators: z.array(z.unknown()).max(64).optional(),
  active_investigator_id: z.string().nullable().optional(),
  private_state: z
    .looseObject({
      investigator_id: z.string().nullable().optional(),
      pc: z.unknown().optional(),
      clues: z.record(z.string(), z.array(z.unknown()).max(512)).optional(),
      player_notes: z
        .looseObject({
          text: z.string().optional(),
          revision: z.number().optional(),
        })
        .nullable()
        .optional(),
    })
    .nullable()
    .optional(),
});

// 服务端按可见性过滤后定向投递的私密事件；不携带 target_user_id，
// 客户端只渲染、不转发、不写入公共消息历史。房间序号为 JSON number。
const privateEventMessageSchema = z.looseObject({
  type: z.literal("private_event"),
  kind: z.string().max(60),
  clue: z
    .looseObject({
      id: z.string().max(160).optional(),
      text: z.string().max(20_000),
      category: z.string().max(60).optional(),
    })
    .optional(),
  room_event_id: z.number().int().nonnegative().optional(),
  world_id: z.string().max(160).optional(),
});

const investigatorRosterMessageSchema = z.looseObject({
  type: z.literal("investigator_roster"),
  investigators: z.array(z.record(z.string(), z.unknown())).max(64),
  active_investigator_id: z.string().nullable().optional(),
});

export type ServerMessageType = (typeof serverMessageTypes)[number];
// Domain handlers still own payload validation. The transport rejects unknown
// discriminants; payload schemas can be tightened one message family at a time.
export type ServerMessage = { type: ServerMessageType; [key: string]: any };

const dsmlProtocol = /<\s*(?:[|｜]\s*)+DSML(?:\s*[|｜])+\s*\w+/iu;

function containsToolProtocol(value: unknown): boolean {
  if (typeof value === "string") return dsmlProtocol.test(value);
  if (Array.isArray(value)) return value.some(containsToolProtocol);
  if (value && typeof value === "object") {
    return Object.values(value as Record<string, unknown>).some(
      containsToolProtocol,
    );
  }
  return false;
}

export function parseServerMessage(raw: unknown): ServerMessage | null {
  let decoded: unknown;
  try {
    decoded = typeof raw === "string" ? JSON.parse(raw) : raw;
  } catch {
    return null;
  }
  const result = serverMessageSchema.safeParse(decoded);
  if (!result.success) return null;
  // Last-resort display boundary: a backend regression must still never render
  // textual tool calls or their private arguments in Electron.
  if (containsToolProtocol(decoded)) return null;
  if (result.data.type === "chat_events") {
    const chatResult = chatEventsMessageSchema.safeParse(decoded);
    return chatResult.success ? (chatResult.data as ServerMessage) : null;
  }
  if (result.data.type === "gm_turn_start") {
    const turnStartResult = gmTurnStartMessageSchema.safeParse(decoded);
    return turnStartResult.success
      ? (turnStartResult.data as ServerMessage)
      : null;
  }
  if (result.data.type === "room_state") {
    const roomResult = roomStateMessageSchema.safeParse(decoded);
    return roomResult.success ? (roomResult.data as ServerMessage) : null;
  }
  if (result.data.type === "room_full_state") {
    const fullResult = roomFullStateMessageSchema.safeParse(decoded);
    return fullResult.success ? (fullResult.data as ServerMessage) : null;
  }
  if (result.data.type === "private_event") {
    const privateResult = privateEventMessageSchema.safeParse(decoded);
    return privateResult.success ? (privateResult.data as ServerMessage) : null;
  }
  if (result.data.type === "investigator_roster") {
    const rosterResult = investigatorRosterMessageSchema.safeParse(decoded);
    return rosterResult.success ? (rosterResult.data as ServerMessage) : null;
  }
  return result.data as ServerMessage;
}
