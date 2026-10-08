import { z } from "zod";
const id = z.string().min(1).max(160);
export const endingCatalogSchema = z.array(
  z
    .object({
      id,
      title: z.string(),
      ending_type: z.enum(["good", "neutral", "bad", "secret"]),
      description: z.string(),
      trigger: z.string(),
      eligible: z.boolean(),
      can_prepare: z.boolean(),
      blocked_reason: z.string(),
      conditions: z.array(
        z
          .object({
            flag_id: id,
            expected_text: z.string(),
            current_text: z.string(),
            recorded: z.boolean(),
            satisfied: z.boolean(),
          })
          .strict(),
      ),
    })
    .strict(),
);
export type EndingCatalogue = z.infer<typeof endingCatalogSchema>;
export const rulingValueSchema = z.union([
  z.boolean(),
  z.number().int().min(-1000000).max(1000000),
  z.string().max(200),
]);
export const rulingPayloadSchema = z
  .object({
    flag_id: id,
    value: rulingValueSchema,
    expected_before: rulingValueSchema.nullable(),
    basis: z.string().min(1).max(1000),
  })
  .strict();
export const rulingStateSchema = z
  .object({
    ending_catalog: endingCatalogSchema.optional(),
    flags: z.array(
      z
        .object({
          id,
          type: z.enum(["boolean", "integer", "string"]),
          value: rulingValueSchema.nullable(),
        })
        .strict(),
    ),
    recent: z
      .array(
        z
          .object({
            flag_id: id,
            before: rulingValueSchema.nullable(),
            after: rulingValueSchema.nullable(),
            basis: z.string().min(1).max(1000),
            user_id: z.string().max(160),
            revision: z.number().int().min(0),
          })
          .strict(),
      )
      .max(100),
    eligible_endings: z.array(
      z
        .object({
          id,
          title: z.string(),
          ending_type: z.enum(["good", "neutral", "bad", "secret"]),
        })
        .strict(),
    ),
  })
  .strict();
export type RulingState = z.infer<typeof rulingStateSchema>;
export function parseRulingValue(
  value: unknown,
): string | number | boolean | null | undefined {
  try {
    const parsed = typeof value === "string" ? JSON.parse(value) : value;
    const result = rulingValueSchema.nullable().safeParse(parsed);
    return result.success ? result.data : undefined;
  } catch {
    return undefined;
  }
}
