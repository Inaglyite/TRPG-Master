import { z } from "zod";

import { backendHttpOrigin } from "../backend-url";
import { RequestDeadline } from "./request-deadline";
import {
  currentCloudRequestGeneration,
  invalidateCloudRequests,
} from "./request-context";

const CLOUD_ORIGIN_KEY = "trpg-cloud-origin";

/**
 * 官方多人服务器 origin（见 docs/ROADMAP.md“中心化多人服务”）。
 * 普通用户无需配置即默认连接；自定义 origin 仅保留
 * 给开发/验收流程。
 */
export const OFFICIAL_CLOUD_ORIGIN = "https://trpggame.xyz";

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
    if (url.username || url.password) return null;
    return url.origin;
  } catch {
    return null;
  }
}

/** 保存或清除云端 origin；输入非法时返回 false 且不改动已保存的值。 */
export function setCloudOrigin(input: string | null): boolean {
  const previous = apiHttpOrigin();
  if (input === null || !input.trim()) {
    try {
      localStorage.removeItem(CLOUD_ORIGIN_KEY);
    } catch {
      return false;
    }
    if (previous !== apiHttpOrigin()) invalidateCloudRequests();
    return true;
  }
  const normalized = normalizeOrigin(input);
  if (!normalized) return false;
  try {
    localStorage.setItem(CLOUD_ORIGIN_KEY, normalized);
  } catch {
    return false;
  }
  if (previous !== normalized) invalidateCloudRequests();
  return true;
}

/** 多人云端 API 的 origin：用户显式配置优先，其次构建变量，最后本地推导。 */
export function apiHttpOrigin(): string {
  return getCloudOrigin() ?? backendHttpOrigin();
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;
  /** 服务端附带的结构化错误明细（如角色库导入的字段级问题列表）。 */
  readonly details: unknown;

  constructor(
    message: string,
    status: number,
    code: string | null,
    details: unknown = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
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
): Promise<{ message: string; code: string | null; details: unknown }> {
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
      // 字段级错误明细（数组）原样透传，供表单类 UI 定位展示
      const details = Array.isArray(record.details) ? record.details : null;
      if (message) return { message, code, details };
    }
  } catch {
    /* 非 JSON 错误体，走通用文案 */
  }
  return {
    message: `请求失败（HTTP ${response.status}）`,
    code: null,
    details: null,
  };
}

export type ApiRequestInit = {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  /** Internal local-mode callers use the loopback backend, not a saved cloud origin. */
  local?: boolean;
  timeoutMs?: number;
};

function timeoutError(error: unknown): ApiError | null {
  // DOMException may come from a different browser realm; instanceof Error
  // does not reliably recognize it (also exercised by the DOM test runner).
  if (
    error &&
    typeof error === "object" &&
    "name" in error &&
    error.name === "TimeoutError"
  ) {
    return new ApiError(
      "服务器未及时响应，请检查网络后重新检查；这不代表服务器上的操作已撤销",
      0,
      "request_timeout",
    );
  }
  return null;
}

/**
 * 云端 API 的统一入口：携带 Session Cookie、JSON 编解码、错误归一化。
 * 业务代码不得绕过本函数直接 fetch 云端接口。
 */
export async function apiFetch<S extends z.ZodTypeAny>(
  path: string,
  schema: S,
  init: ApiRequestInit = {},
): Promise<z.output<S>> {
  const origin = init.local ? backendHttpOrigin() : apiHttpOrigin();
  const generation = currentCloudRequestGeneration();
  function assertCurrent(): void {
    if (
      !init.local &&
      (generation !== currentCloudRequestGeneration() ||
        origin !== apiHttpOrigin())
    ) {
      throw new ApiError(
        "服务器或会话已变化，旧请求已忽略",
        0,
        "request_context_changed",
      );
    }
  }
  const deadline = new RequestDeadline(init.timeoutMs ?? 30_000);
  try {
    let response: Response;
    try {
      response = await deadline.wait(
        fetch(`${origin}${path}`, {
          method: init.method ?? "GET",
          credentials: "include",
          headers:
            init.body !== undefined
              ? { "Content-Type": "application/json" }
              : undefined,
          body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
          signal: deadline.controller.signal,
        }),
      );
    } catch (error) {
      assertCurrent();
      const timedOut = timeoutError(error);
      if (timedOut) throw timedOut;
      throw new ApiError(
        "无法连接服务器，请检查网络连接或服务器地址",
        0,
        "network_error",
      );
    }
    assertCurrent();
    // Credential endpoints own their failure handling. A rejected password (or
    // already-expired logout) is not a new expiry broadcast for another flow.
    if (
      !init.local &&
      response.status === 401 &&
      !["/api/auth/login", "/api/auth/register", "/api/auth/logout"].includes(
        path,
      )
    ) {
      unauthorizedListeners.forEach((listener) => listener());
    }
    if (!response.ok) {
      const { message, code, details } = await deadline.wait(
        readError(response),
      );
      // A current 401 listener intentionally revokes this session generation.
      // Keep the actual 401, rather than relabelling that revocation as stale.
      if (response.status !== 401) assertCurrent();
      throw new ApiError(message, response.status, code, details);
    }
    if (response.status === 204) {
      return schema.parse(undefined);
    }
    let data: unknown;
    try {
      data = await deadline.wait(response.json());
    } catch (error) {
      assertCurrent();
      const timedOut = timeoutError(error);
      if (timedOut) throw timedOut;
      throw new ApiError(
        "服务器返回了无法解析的响应",
        response.status,
        "invalid_json",
      );
    }
    assertCurrent();
    const parsed = schema.safeParse(data);
    if (!parsed.success) {
      throw new ApiError(
        "服务器响应格式与预期不符",
        response.status,
        "invalid_payload",
      );
    }
    return parsed.data;
  } catch (error) {
    const timedOut = timeoutError(error);
    if (timedOut) {
      assertCurrent();
      throw timedOut;
    }
    throw error;
  } finally {
    deadline.dispose();
  }
}
