import { z } from "zod";

import { apiFetch } from "./client";

export const moduleInfoSchema = z.looseObject({
  id: z.string(),
  title: z.string(),
  package_id: z.string().optional(),
  version: z.string().optional(),
  source: z.string().optional(),
});
export type ModuleInfo = z.infer<typeof moduleInfoSchema>;

/** 模组列表（服务端已存在，见 docs/API.md §2.6）。 */
export async function listModules(): Promise<ModuleInfo[]> {
  const data = await apiFetch(
    "/api/modules",
    z.object({ modules: z.array(moduleInfoSchema) }),
  );
  return data.modules;
}
