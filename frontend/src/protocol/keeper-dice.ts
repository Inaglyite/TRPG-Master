import { z } from "zod";

const dicePattern = /^(\d{1,2})d(\d{1,3})([+-]\d{1,3})?$/;
const specSchema = z
  .string()
  .min(1)
  .max(40)
  .superRefine((spec, ctx) => {
    const match = dicePattern.exec(spec);
    if (
      !match ||
      Number(match[1]) < 1 ||
      Number(match[1]) > 10 ||
      Number(match[2]) < 2 ||
      Number(match[2]) > 100
    ) {
      ctx.addIssue({
        code: "custom",
        message: "骰式需为 NdM 或 NdM±K：1–10颗、2–100面。",
      });
    }
  });

export const keeperRollPayloadSchema = z
  .object({
    spec: specSchema,
    visibility: z.enum(["keeper", "public"]).optional(),
  })
  .strict();

export const keeperRollReceiptSchema = z
  .object({
    command_id: z.string().min(1).max(160),
    visibility: z.enum(["keeper", "public"]),
    expression: specSchema,
    dice: z
      .array(
        z
          .object({
            sides: z.number().int().min(2).max(100),
            values: z.array(z.number().int().min(1).max(100)).min(1).max(10),
          })
          .strict(),
      )
      .length(1),
    total: z.number().safe().int(),
    modifier: z.number().int().min(-999).max(999),
  })
  .strict()
  .superRefine((receipt, ctx) => {
    const die = receipt.dice[0];
    const match = dicePattern.exec(receipt.expression);
    if (
      match &&
      (die.values.length !== Number(match[1]) ||
        die.sides !== Number(match[2]) ||
        receipt.modifier !== Number(match[3] || 0) ||
        die.values.some((v) => v > die.sides) ||
        receipt.total !==
          die.values.reduce((a, b) => a + b, 0) + receipt.modifier)
    ) {
      ctx.addIssue({ code: "custom", message: "骰点记录不完整，请重新同步。" });
    }
  });

export type KeeperRollReceipt = z.infer<typeof keeperRollReceiptSchema>;

export function keeperDiceProblem(spec: string): string | null {
  const parsed = keeperRollPayloadSchema.safeParse({ spec });
  return parsed.success
    ? null
    : "骰式需为 NdM 或 NdM±K：1–10颗、2–100面、最多±999。";
}
