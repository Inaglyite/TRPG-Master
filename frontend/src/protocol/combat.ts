/** Read only explicit structured combat projections; never parse narration. */
import { z } from "zod";

const id = z.string().min(1).max(160);
export const COMBAT_COMMAND_KINDS = [
  "combat_start",
  "combat_action",
  "combat_decide",
  "combat_roll",
  "combat_end",
  "end_game",
] as const;
export const COMBAT_PLAYER_COMMANDS = ["combat_decide", "combat_roll"] as const;
export const COMBAT_ACTIONS = [
  "melee",
  "firearm",
  "threat",
  "move",
  "other",
] as const;
export const COMBAT_ACTION_LABELS: Record<string, string> = {
  melee: "近战",
  firearm: "射击",
  threat: "威胁",
  move: "战术移动",
  other: "其他动作",
};

export const combatSchema = z
  .object({
    active: z.boolean(),
    encounter_id: id.nullable().optional(),
    round: z.number().int().min(1).nullable().optional(),
    phase: z.string().nullable().optional(),
    turn_order: z.array(id).nullable().optional(),
    current_actor: id.nullable().optional(),
    outcome: z.string().nullable().optional(),
    participants: z
      .array(
        z
          .object({
            id,
            name: z.string(),
            kind: z.enum(["pc", "npc"]),
            hp: z.number(),
            max_hp: z.number(),
            conditions: z.array(z.string()),
          })
          .strict(),
      )
      .optional(),
    awaiting_decision: z.boolean().optional(),
    awaiting_roll: z.boolean().optional(),
  })
  .strict();
export const combatDecisionSchema = z
  .object({
    id,
    kind: z.enum([
      "irreversible_violence",
      "coercive_threat",
      "combat_defense",
      "pvp_consent",
    ]),
    title: z.string(),
    description: z.string(),
    options: z
      .array(
        z
          .object({ id, label: z.string(), description: z.string().optional() })
          .strict(),
      )
      .min(1),
    default_option: id,
    responding_investigator_id: id,
  })
  .strict();
export const combatRollSchema = z
  .object({
    roll_id: id,
    weapon_item_id: id.optional(),
    weapon_label: z.string().optional(),
    investigator_id: id,
    actor_id: id,
    target_id: id.nullable(),
    action_type: z.enum(COMBAT_ACTIONS),
    source: z.enum(["action", "decision", "pvp_defense", "pvp_attack"]),
  })
  .strict();
export const endingSchema = z
  .object({
    id: id.nullable(),
    type: z.enum(["good", "secret", "neutral", "bad"]),
    title: z.string(),
    summary: z.string(),
  })
  .strict();
export const caseSettlementSchema = z
  .object({
    investigator_id: id,
    character_id: id,
    case: z
      .object({
        case_id: z.string().min(1).max(384),
        world_id: id,
        ending_type: z.enum(["good", "secret", "neutral", "bad"]),
        reputation_delta: z.number(),
      })
      .passthrough(),
    career: z
      .object({
        case_history: z.array(z.unknown()),
        reputation: z.number(),
        completed_modules: z.array(z.string()),
      })
      .passthrough(),
    character_snapshot: z.record(z.string(), z.unknown()).optional(),
  })
  .strict();

export type CombatState = z.infer<typeof combatSchema>;
export type CombatDecision = z.infer<typeof combatDecisionSchema>;
export type CombatRoll = z.infer<typeof combatRollSchema>;
export type Ending = z.infer<typeof endingSchema>;
export type CaseSettlement = z.infer<typeof caseSettlementSchema>;
export const combatResultSchema = z
  .object({
    roll_id: id,
    investigator_id: id,
    encounter_id: id,
    round: z.number().int().min(1),
    actor_id: id,
    target_id: id.nullable(),
    action_type: z.enum(COMBAT_ACTIONS),
    response: z.enum(["roll", "cancel"]),
    outcome: z.string().max(80),
    rolls: z
      .array(
        z
          .object({
            actor_id: id,
            role: z.enum(["attack", "defense"]),
            roll: z.number().int().min(1).max(100),
            level: z.string().max(40),
          })
          .strict(),
      )
      .max(2),
    damage: z
      .object({
        target_id: id,
        amount: z.number().int().min(0),
        hp_before: z.number().int(),
        hp_after: z.number().int(),
      })
      .strict()
      .nullable(),
  })
  .strict();
export type CombatResult = z.infer<typeof combatResultSchema>;
export const combatCommandPayloads: Record<string, z.ZodType> = {
  combat_start: z
    .object({
      participants: z
        .array(z.object({ id, ready_firearm: z.boolean().optional() }).strict())
        .min(1)
        .max(32),
      reason: z.string().max(4000).optional(),
    })
    .strict(),
  combat_action: z
    .object({
      actor_id: id,
      target_id: id.optional(),
      action_type: z.enum(COMBAT_ACTIONS),
      description: z.string().max(4000).optional(),
      skill: z.string().min(1).max(4000).optional(),
      weapon: z.string().min(1).max(4000).optional(),
      weapon_item_id: id.optional(),
      damage_spec: z
        .string()
        .regex(
          /^(?:[1-9]|10)?d(?:[2-9]|[1-9][0-9]|100)(?:[+-](?:[0-9]|[1-9][0-9]|100))?$/,
        )
        .optional(),
      damage_mode: z.enum(["normal", "impaling", "blunt"]).optional(),
      defender_choice: z.string().min(1).max(4000).optional(),
      bonus_dice: z.number().int().min(0).max(2).optional(),
      penalty_dice: z.number().int().min(0).max(2).optional(),
    })
    .strict(),
  combat_decide: z.object({ decision_id: id, option_id: id }).strict(),
  combat_roll: z
    .object({ roll_id: id, response: z.enum(["roll", "cancel"]) })
    .strict(),
  combat_end: z.object({ reason: z.string().min(1).max(4000) }).strict(),
  end_game: z
    .object({
      ending_id: id.optional(),
      ending_type: z.enum(["good", "secret", "neutral", "bad"]).optional(),
      title: z.string().min(1).max(4000).optional(),
      summary: z.string().max(4000).optional(),
    })
    .strict(),
};
export function readProjection<T>(
  schema: z.ZodType<T>,
  value: unknown,
): T | null {
  const result = schema.safeParse(value);
  return result.success ? result.data : null;
}
