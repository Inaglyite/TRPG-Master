import { z } from "zod";
import { apiFetch } from "./client";

export const narrativeHistorySchema = z.object({
  messages: z
    .array(
      z
        .object({
          message_id: z.string(),
          sequence: z.number().int().nonnegative(),
          text: z.string(),
          entry_kind: z.enum(["narrative", "action_request"]).optional(),
          speaker: z.object({
            kind: z.enum(["keeper", "npc", "investigator", "system"]),
            id: z.string().optional(),
            name: z.string(),
          }),
        })
        .refine(
          (message) =>
            message.entry_kind !== "action_request" ||
            message.speaker.kind === "investigator",
          "行动申报必须归属于调查员",
        ),
    )
    .max(50),
  next_before_sequence: z.number().int().positive().nullable(),
});
export type NarrativeHistoryPage = z.infer<typeof narrativeHistorySchema>;

export function loadNarrativeHistory(
  worldId: string,
  beforeSequence: number,
  local: boolean,
  scope: "current" | "inherited" = "current",
) {
  return apiFetch(
    `/api/worlds/${encodeURIComponent(worldId)}/narrative-history?before_sequence=${beforeSequence}${scope === "inherited" ? "&scope=inherited" : ""}`,
    narrativeHistorySchema,
    { local, timeoutMs: 15_000 },
  );
}
