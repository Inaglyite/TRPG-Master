import { z } from "zod";

export const keeperProgressSchema = z
  .object({
    clues: z.array(
      z
        .object({
          id: z.string(),
          category: z.string(),
          text: z.string(),
          discovered: z.boolean(),
          granted_item: z.string(),
          item_id: z.string(),
          holder_id: z.string(),
          related_scenes: z.array(z.string()),
          rules: z.array(
            z
              .object({
                index: z.number().int().nonnegative(),
                intent: z.string(),
                skill: z.string(),
                difficulty: z.string(),
                requires_success: z.boolean(),
                approach: z.string(),
                sanity_note: z.string(),
                conditions: z.array(
                  z
                    .object({
                      flag_id: z.string(),
                      expected_text: z.string(),
                      current_text: z.string(),
                      satisfied: z.boolean(),
                    })
                    .strict(),
                ),
              })
              .strict(),
          ),
        })
        .strict(),
    ),
    clocks: z.array(
      z
        .object({
          id: z.string(),
          title: z.string(),
          value: z.number().int().nullable(),
          max: z.number().int().nonnegative().nullable(),
          level: z.string(),
          next_level: z.string(),
          advance_when: z.array(z.string()),
        })
        .strict(),
    ),
  })
  .strict();
export type KeeperProgress = z.infer<typeof keeperProgressSchema>;
