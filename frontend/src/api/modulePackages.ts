import { z } from "zod";

import { backendHttpOrigin } from "../backend-url";
import { useAppStore } from "../state/app-store";

const summarySchema = z.object({
  module_key: z.string(),
  package_id: z.string(),
  version: z.string(),
  title: z.string(),
  author: z.string(),
  description: z.string(),
  system: z.string(),
  capabilities: z.array(z.string()),
  file_count: z.number().int().nonnegative(),
  warnings: z.array(z.string()),
});
export type ModulePackageSummary = z.infer<typeof summarySchema>;
const installedSchema = z.object({
  id: z.string().min(1),
  title: z.string(),
  version: z.string(),
});
const inspectSchema = z.object({ ok: z.literal(true), module: summarySchema });
const importSchema = z.object({
  ok: z.literal(true),
  already_installed: z.boolean(),
  module: installedSchema,
});

/** Local-only binary upload. It never routes to a saved cloud origin. */
export async function uploadModulePackage<T extends "inspect" | "import">(
  endpoint: T,
  file: File,
  signal: AbortSignal,
): Promise<
  T extends "inspect"
    ? z.infer<typeof inspectSchema>
    : z.infer<typeof importSchema>
> {
  if (useAppStore.getState().mode !== "local")
    throw new Error("模组导入仅适用于本地模式");
  const controller = new AbortController();
  let rejectAbort!: (reason: DOMException) => void;
  const cancelled = new Promise<never>((_, reject) => {
    rejectAbort = reject;
  });
  const abort = () => {
    controller.abort();
    rejectAbort(new DOMException("操作已停止等待", "AbortError"));
  };
  signal.addEventListener("abort", abort, { once: true });
  if (signal.aborted) abort();
  let timedOut = false;
  // Installation may still commit after a timeout: report uncertainty, not rollback.
  const timeoutMessage =
    endpoint === "inspect"
      ? "检查模组包超时，请检查连接后重试。"
      : "安装等待超时，尚未确认结果。模组可能已安装，请重新选择该文件检查并核对模组列表。";
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(
      () => {
        timedOut = true;
        controller.abort();
        reject(new Error(timeoutMessage));
      },
      endpoint === "inspect" ? 30_000 : 120_000,
    );
  });
  try {
    return await Promise.race([
      timeout,
      cancelled,
      (async () => {
        if (signal.aborted)
          throw new DOMException("操作已停止等待", "AbortError");
        const response = await fetch(
          `${backendHttpOrigin()}/api/modules/${endpoint}`,
          {
            method: "POST",
            signal: controller.signal,
            headers: {
              "Content-Type": "application/vnd.trpg-master.module+zip",
              "X-Module-Filename": encodeURIComponent(file.name),
            },
            body: file,
          },
        );
        let payload: unknown;
        try {
          payload = await response.json();
        } catch {
          throw new Error(
            `模组服务返回了无法解析的响应（HTTP ${response.status}）`,
          );
        }
        if (
          !response.ok ||
          !payload ||
          typeof payload !== "object" ||
          !("ok" in payload) ||
          !payload.ok
        ) {
          const data =
            payload && typeof payload === "object"
              ? (payload as Record<string, unknown>)
              : {};
          const message =
            typeof data.error === "string"
              ? data.error
              : `模组操作失败（HTTP ${response.status}）`;
          const details = Array.isArray(data.details)
            ? data.details.filter(
                (item): item is string => typeof item === "string",
              )
            : [];
          throw new Error([message, ...details].join("\n"));
        }
        const parsed = (
          endpoint === "inspect" ? inspectSchema : importSchema
        ).safeParse(payload);
        if (!parsed.success)
          throw new Error("模组服务返回的预览或安装信息不完整，请重新检查。");
        return parsed.data as T extends "inspect"
          ? z.infer<typeof inspectSchema>
          : z.infer<typeof importSchema>;
      })(),
    ]);
  } catch (error) {
    if (timedOut) throw new Error(timeoutMessage);
    if (error instanceof TypeError)
      throw new Error(
        endpoint === "inspect"
          ? "未收到模组检查结果。可能是连接中断或文件无法读取，请检查连接并重新选择原文件。"
          : "未能确认安装结果。模组可能已安装，请检查连接，重新选择原文件并核对模组列表。",
        { cause: error },
      );
    throw error;
  } finally {
    clearTimeout(timer);
    signal.removeEventListener("abort", abort);
  }
}
