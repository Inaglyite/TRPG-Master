import { z } from "zod";

import { backendHttpOrigin } from "../backend-url";

const CLOUD_ORIGIN_KEY = "trpg-cloud-origin";

/** 读取用户保存的云端服务器 origin；未设置时返回 null。 */
export function getCloudOrigin(): string | null {
  try {
    return localStorage.getItem(CLOUD_ORIGIN_KEY)?.trim() || null;
  } catch {
    return null;
  }
}

/** 规范化用户输入的服务器地址；只接受 http(s)，丢弃路径与查询参数。 */
export function normalizeOrigin(input: string): string | null {
  const trimmed = input.trim();
  if (!trimmed) return null;
  try {
    const url = new URL(trimmed);
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    return url.origin;
  } catch {
    return null;
  }
}

/** 保存或清除云端 origin；输入非法时返回 false 且不改动已保存的值。 */
export function setCloudOrigin(input: string | null): boolean {
  if (input === null || !input.trim()) {
    try {
      localStorage.removeItem(CLOUD_ORIGIN_KEY);
    } catch {
      /* localStorage 不可用时忽略 */
    }
    return true;
  }
  const normalized = normalizeOrigin(input);
  if (!normalized) return false;
  try {
    localStorage.setItem(CLOUD_ORIGIN_KEY, normalized);
  } catch {
    /* localStorage 不可用时忽略 */
  }
  return true;
}

/** 多人云端 API 的 origin：用户显式配置优先，其次构建变量，最后本地推导。 */
export function apiHttpOrigin(): string {
  return getCloudOrigin() ?? backendHttpOrigin();
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;

  constructor(message: string, status: number, code: string | null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }

  get isUnauthorized(): boolean {
    return this.status === 401;
  }

  get isNetwork(): boolean {
    return this.status === 0;
  }
}

type UnauthorizedListener = () => void;
const unauthorizedListeners = new Set<UnauthorizedListener>();

/** 任何云端 API 响应 401 时触发；认证状态机据此降级为“会话过期”。 */
export function onUnauthorized(listener: UnauthorizedListener): () => void {
  unauthorizedListeners.add(listener);
  return () => {
    unauthorizedListeners.delete(listener);
  };
}

async function readError(
  response: Response,
): Promise<{ message: string; code: string | null }> {
  try {
    const data: unknown = await response.json();
    if (data && typeof data === "object") {
      const record = data as Record<string, unknown>;
      const code =
        typeof record.error_code === "string"
          ? record.error_code
          : typeof record.code === "string"
            ? record.code
            : null;
      const message =
        typeof record.error === "string"
          ? record.error
          : typeof record.message === "string"
            ? record.message
            : typeof record.detail === "string"
              ? record.detail
              : null;
      if (message) return { message, code };
    }
  } catch {
    /* 非 JSON 错误体，走通用文案 */
  }
  return { message: `请求失败（HTTP ${response.status}）`, code: null };
}

export type ApiRequestInit = {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
};

/**
 * 云端 API 的统一入口：携带 Session Cookie、JSON 编解码、错误归一化。
 * 业务代码不得绕过本函数直接 fetch 云端接口。
 */
export async function apiFetch<S extends z.ZodTypeAny>(
  path: string,
  schema: S,
  init: ApiRequestInit = {},
): Promise<z.output<S>> {
  let response: Response;
  try {
    response = await fetch(`${apiHttpOrigin()}${path}`, {
      method: init.method ?? "GET",
      credentials: "include",
      headers:
        init.body !== undefined
          ? { "Content-Type": "application/json" }
          : undefined,
      body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
    });
  } catch {
    throw new ApiError(
      "无法连接服务器，请检查网络连接或服务器地址",
      0,
      "network_error",
    );
  }
  if (response.status === 401) {
    unauthorizedListeners.forEach((listener) => listener());
  }
  if (!response.ok) {
    const { message, code } = await readError(response);
    throw new ApiError(message, response.status, code);
  }
  if (response.status === 204) {
    return schema.parse(undefined);
  }
  let data: unknown;
  try {
    data = await response.json();
  } catch {
    throw new ApiError(
      "服务器返回了无法解析的响应",
      response.status,
      "invalid_json",
    );
  }
  const parsed = schema.safeParse(data);
  if (!parsed.success) {
    throw new ApiError(
      "服务器响应格式与预期不符",
      response.status,
      "invalid_payload",
    );
  }
  return parsed.data;
}
