import { z } from "zod";
import { apiFetch } from "./client";

const skill = z.object({
  id: z.string(),
  name: z.string(),
  value: z.number().int().min(1).max(100),
  trained: z.boolean(),
});
export const playerToolsSchema = z.object({
  world_id: z.string(),
  revision: z.number().int().nonnegative(),
  character_name: z.string(),
  skills: z.array(skill),
  can_organize: z.boolean(),
  map: z.object({
    nodes: z.array(
      z.object({ id: z.string(), name: z.string(), current: z.boolean() }),
    ),
    edges: z.array(z.object({ from: z.string(), to: z.string() })),
  }),
});
export type PlayerTools = z.infer<typeof playerToolsSchema>;
export type PlayerSkill = z.infer<typeof skill>;
export const digestSchema = z.object({
  world_id: z.string(),
  source_id: z.string(),
  items: z.array(z.object({ text: z.string(), quote: z.string() })).max(8),
});
export type DigestItem = z.infer<typeof digestSchema>["items"][number];

export function getPlayerTools(worldId: string, local: boolean) {
  return apiFetch(
    `/api/worlds/${encodeURIComponent(worldId)}/player-tools`,
    playerToolsSchema,
    { local },
  );
}

export function organizeTurn(
  worldId: string,
  local: boolean,
  source: { id: string; text: string },
) {
  return apiFetch(
    `/api/worlds/${encodeURIComponent(worldId)}/investigation-digest`,
    digestSchema,
    {
      local,
      method: "POST",
      timeoutMs: 110_000,
      body: { source_id: source.id, source_text: source.text },
    },
  );
}
