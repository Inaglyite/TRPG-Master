import { z } from "zod";

/** Explicit human records; these labels never parse narrative or cause healing. */
export const CONDITION_KINDS = [
  "major_wound",
  "prone",
  "unconscious",
  "dying",
  "dead",
] as const;
export const CONDITION_LABELS: Record<string, string> = {
  major_wound: "重伤",
  prone: "倒地",
  unconscious: "昏迷",
  dying: "濒死",
  dead: "死亡",
};
export const conditionPayloadSchema = z
  .object({
    investigator_id: z.string().min(1).max(160),
    condition: z.enum(CONDITION_KINDS),
    operation: z.enum(["add", "remove"]),
    expected_present: z.boolean(),
    basis: z
      .string()
      .min(1)
      .max(1000)
      .refine((value) => value.trim().length > 0),
  })
  .strict();
