import { z } from "zod";
import { apiFetch } from "./client";

const previewSchema = z.object({
  asset_id: z.string(),
  label: z.string(),
  asset_data_uri: z
    .string()
    .regex(/^data:image\/(png|jpeg|webp|gif|avif);base64,/),
});

/** Reads one authorized image. Preview never grants it or emits a story event. */
export function loadStructuredAsset(
  worldId: string,
  assetId: string,
  local: boolean,
) {
  return apiFetch(
    `/api/worlds/${encodeURIComponent(worldId)}/handouts/${encodeURIComponent(assetId)}`,
    previewSchema,
    { local, timeoutMs: 15_000 },
  );
}

const guideSchema = z.object({
  module_title: z.string(),
  source_version: z.string(),
  documents: z.array(
    z.object({ id: z.string(), title: z.string(), text: z.string() }),
  ),
  warnings: z.array(z.string()),
});
export type KeeperGuide = z.infer<typeof guideSchema>;

/** Author references, fetched only when an authorized keeper opens the manual. */
export function loadKeeperGuide(worldId: string, local: boolean) {
  return apiFetch(
    `/api/worlds/${encodeURIComponent(worldId)}/keeper-guide`,
    guideSchema,
    { local, timeoutMs: 15_000 },
  );
}
