import { z } from "zod";
import { apiFetch } from "./client";
import { moduleInfoSchema } from "./modules";
import { useAppStore } from "../state/app-store";

const managedModule = moduleInfoSchema.extend({
  world_count: z.number(),
  save_count: z.number(),
  delete_block: z.string(),
});
export type ManagedModule = z.infer<typeof managedModule>;
function localOnly() {
  if (useAppStore.getState().mode !== "local")
    throw new Error("模组管理仅适用于本地模式");
}
export async function listManagedModules() {
  localOnly();
  return apiFetch(
    "/api/modules/manage",
    z.object({ modules: z.array(managedModule) }),
    { local: true },
  );
}
export async function deleteModule(id: string) {
  localOnly();
  return apiFetch(
    `/api/modules/${encodeURIComponent(id)}`,
    z.object({
      ok: z.literal(true),
      world_count: z.number(),
      save_count: z.number(),
      cleanup_pending: z.boolean(),
    }),
    { method: "DELETE", local: true },
  );
}
