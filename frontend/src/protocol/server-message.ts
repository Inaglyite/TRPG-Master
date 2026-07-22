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

export type ServerMessageType = (typeof serverMessageTypes)[number];
// Domain handlers still own payload validation. The transport rejects unknown
// discriminants; payload schemas can be tightened one message family at a time.
export type ServerMessage = { type: ServerMessageType; [key: string]: any };

export function parseServerMessage(raw: unknown): ServerMessage | null {
  let decoded: unknown;
  try {
    decoded = typeof raw === "string" ? JSON.parse(raw) : raw;
  } catch {
    return null;
  }
  const result = serverMessageSchema.safeParse(decoded);
  if (!result.success) return null;
  if (result.data.type === "chat_events") {
    const chatResult = chatEventsMessageSchema.safeParse(decoded);
    return chatResult.success ? (chatResult.data as ServerMessage) : null;
  }
  return result.data as ServerMessage;
}
