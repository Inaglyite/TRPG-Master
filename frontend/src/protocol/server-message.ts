import { z } from "zod";

export const serverMessageTypes = [
  "narrative_chunk",
  "narrative_segment",
  "narrative_segments",
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
  return result.success ? (result.data as ServerMessage) : null;
}
