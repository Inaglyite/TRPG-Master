import { z } from "zod";

export const holderSchema = z
  .object({
    kind: z.enum(["investigator", "npc", "scene"]),
    id: z.string().min(1).max(160),
  })
  .strict();
export type ItemHolder = z.infer<typeof holderSchema>;
export const holdingsSchema = z
  .object({
    holders: z.array(holderSchema.extend({ name: z.string() }).strict()),
    items: z.array(
      z
        .object({
          id: z.string(),
          label: z.string(),
          quantity: z.number().int().positive(),
          holder: holderSchema,
        })
        .strict(),
    ),
  })
  .strict();
export function holderValue(holder: ItemHolder): string {
  return `${holder.kind}/${holder.id}`;
}
export function readHolder(value: unknown): ItemHolder | null {
  if (typeof value !== "string") return null;
  const slash = value.indexOf("/");
  if (slash < 0) return null;
  const result = holderSchema.safeParse({
    kind: value.slice(0, slash),
    id: value.slice(slash + 1),
  });
  return result.success ? result.data : null;
}
export const HOLDER_LABEL = {
  investigator: "调查员",
  npc: "NPC",
  scene: "场景",
} as const;
