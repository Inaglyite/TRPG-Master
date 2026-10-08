import { z } from "zod";

import { ApiError, apiFetch, type ApiRequestInit } from "./client";
import { useAppStore } from "../state/app-store";
import { useOnlineStore } from "../state/online-store";
import { currentCloudRequestGeneration } from "./request-context";
import { useStructuredStore } from "../state/structured-store";

async function libraryFetch<S extends z.ZodTypeAny>(
  path: string,
  schema: S,
  init: ApiRequestInit = {},
) {
  const mode = useAppStore.getState().mode;
  const userId = useOnlineStore.getState().user?.id;
  const result = await apiFetch(path, schema, {
    ...init,
    local: mode === "local",
    timeoutMs: 20000,
  });
  if (
    mode !== useAppStore.getState().mode ||
    (mode === "online" && userId !== useOnlineStore.getState().user?.id)
  ) {
    throw new ApiError(
      "模式或账号已变化，旧角色库请求已忽略",
      0,
      "request_context_changed",
    );
  }
  return result;
}

/**
 * 角色库 API：本地与云端共用同一组端点（本地走回环信任、云端走 Session）。
 * 服务端生成条目 id；导入/创建/编辑共用同一份卡面校验（服务端权威，
 * 前端只做展示与基础格式读取）。
 */

export const libraryEntrySchema = z.looseObject({
  id: z.string(),
  name: z.string(),
  occupation: z.string().optional().default(""),
  age: z.number().int().nullish(),
  era: z.string().optional(),
  source: z.string().optional(),
  source_label: z.string().optional(),
  hp: z.number().optional().default(0),
  max_hp: z.number().optional().default(0),
  san: z.number().optional().default(0),
  max_san: z.number().optional().default(0),
  reputation: z.number().optional(),
  completed_modules: z.number().optional(),
  credit_rating: z.number().optional(),
  attributes: z.record(z.string(), z.number()).optional(),
  derived: z.record(z.string(), z.union([z.number(), z.string()])).optional(),
  inventory: z.array(z.unknown()).optional(),
  backstory: z.record(z.string(), z.unknown()).optional(),
  top_skills: z
    .array(z.looseObject({ id: z.string(), value: z.number() }))
    .optional(),
  skills: z.record(z.string(), z.number()).optional(),
  description: z.string().optional(),
  created_at: z.string().optional(),
  updated_at: z.string().optional(),
});
export type LibraryEntry = z.infer<typeof libraryEntrySchema>;

export type CaseCharacterSource = {
  world_id: string;
  investigator_id: string;
  case_id: string;
  expected_revision: number;
  name?: string;
  receipt_digest?: string;
};
const casePreviewSchema = z.object({
  ok: z.boolean(),
  card: z.record(z.string(), z.unknown()),
  warnings: z.array(z.string()),
  revision: z.number().int().positive(),
  receipt_digest: z.string().regex(/^[a-f0-9]{64}$/),
  saved_entry: libraryEntrySchema.nullable(),
});
const caseSaveSchema = z.object({
  ok: z.boolean(),
  entry: libraryEntrySchema,
  warnings: z.array(z.string()),
  deduplicated: z.boolean(),
});
export async function previewCaseCharacter(source: CaseCharacterSource) {
  return libraryFetch(
    "/api/character-library/from-case/preview",
    casePreviewSchema,
    { method: "POST", body: source },
  );
}
export async function saveCaseCharacter(source: CaseCharacterSource) {
  return libraryFetch("/api/character-library/from-case", caseSaveSchema, {
    method: "POST",
    body: source,
  });
}
export async function exportCaseCharacter(
  source: CaseCharacterSource,
  name: string,
) {
  const mode = useAppStore.getState().mode;
  const generation = currentCloudRequestGeneration();
  const liveCase = () =>
    useStructuredStore
      .getState()
      .caseSettlements.find(
        (r) =>
          r.investigator_id === source.investigator_id &&
          r.case.case_id === source.case_id,
      )?.case;
  const receiptScope = JSON.stringify(liveCase() || null);
  const data = await libraryFetch(
    "/api/character-library/from-case/export",
    z.looseObject({
      format: z.literal("trpg-character-card"),
      format_version: z.literal(1),
      card: z.record(z.string(), z.unknown()),
    }),
    { method: "POST", body: source },
  );
  const identity = useStructuredStore.getState().identity;
  if (
    mode !== useAppStore.getState().mode ||
    identity.worldId !== source.world_id ||
    identity.investigatorId !== source.investigator_id ||
    receiptScope !== JSON.stringify(liveCase() || null) ||
    (mode === "online" && generation !== currentCloudRequestGeneration())
  )
    throw new ApiError(
      "会话、角色或案件已变化，旧结案卡未下载",
      0,
      "request_context_changed",
    );
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
  );
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `character-${name || "card"}.json`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export const libraryIssueSchema = z.looseObject({
  field: z.string(),
  message: z.string(),
});
export type LibraryIssue = z.infer<typeof libraryIssueSchema>;

export const inspectResultSchema = z.looseObject({
  ok: z.boolean(),
  errors: z.array(libraryIssueSchema),
  warnings: z.array(z.string()),
  preview: libraryEntrySchema.nullable().optional(),
});
export type InspectResult = z.infer<typeof inspectResultSchema>;

const writeResultSchema = z.looseObject({
  ok: z.boolean(),
  entry: libraryEntrySchema,
  warnings: z.array(z.string()).optional().default([]),
});

/** 本地模式无 Session，但同一组端点由回环/启动凭证信任校验放行。 */
export async function listCharacterLibrary(): Promise<LibraryEntry[]> {
  const data = await libraryFetch(
    "/api/character-library",
    z.looseObject({ entries: z.array(libraryEntrySchema) }),
  );
  return data.entries;
}

/** 导入预览：只校验不落库。服务不可达等网络错误照常抛 ApiError。 */
export async function inspectLibraryCard(
  payload: unknown,
): Promise<InspectResult> {
  return libraryFetch("/api/character-library/inspect", inspectResultSchema, {
    method: "POST",
    body: payload,
  });
}

export async function createLibraryEntry(
  payload: unknown,
): Promise<{ entry: LibraryEntry; warnings: string[] }> {
  return libraryFetch("/api/character-library", writeResultSchema, {
    method: "POST",
    body: payload,
  });
}

export async function updateLibraryEntry(
  id: string,
  payload: unknown,
): Promise<{ entry: LibraryEntry; warnings: string[] }> {
  return libraryFetch(
    `/api/character-library/${encodeURIComponent(id)}`,
    writeResultSchema,
    { method: "PUT", body: payload },
  );
}

/** 编辑保存须读取完整卡面，列表投影不能用来覆盖原卡。 */
export async function getLibraryCard(
  id: string,
): Promise<Record<string, unknown>> {
  const data = await libraryFetch(
    `/api/character-library/${encodeURIComponent(id)}`,
    z.object({ card: z.record(z.string(), z.unknown()) }),
  );
  return data.card;
}

export async function duplicateLibraryEntry(id: string): Promise<LibraryEntry> {
  const data = await libraryFetch(
    `/api/character-library/${encodeURIComponent(id)}/duplicate`,
    writeResultSchema,
    { method: "POST" },
  );
  return data.entry;
}

export async function deleteLibraryEntry(id: string): Promise<void> {
  await libraryFetch(
    `/api/character-library/${encodeURIComponent(id)}`,
    z.undefined(),
    { method: "DELETE" },
  );
}

/** 导出版本化信封并触发浏览器下载；只包含角色资料。 */
export async function exportLibraryEntry(
  id: string,
  name: string,
): Promise<void> {
  const mode = useAppStore.getState().mode;
  const generation = currentCloudRequestGeneration();
  const data = await libraryFetch(
    `/api/character-library/${encodeURIComponent(id)}/export`,
    z.record(z.string(), z.unknown()),
  );
  // Download is itself an effect: recheck after the final await, not only
  // while reading HTTP data. Local exports ignore unrelated cloud changes.
  if (
    mode !== useAppStore.getState().mode ||
    (mode === "online" && generation !== currentCloudRequestGeneration())
  ) {
    throw new ApiError(
      "会话已变化，旧角色卡未下载",
      0,
      "request_context_changed",
    );
  }
  const blob = new Blob([JSON.stringify(data, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `character-${name || "card"}.json`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
