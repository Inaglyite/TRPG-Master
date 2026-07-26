import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { z } from "zod";

import {
  ApiError,
  apiFetch,
  apiHttpOrigin,
  getCloudOrigin,
  normalizeOrigin,
  onUnauthorized,
  setCloudOrigin,
} from "./client";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("云端 origin 配置", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("normalizeOrigin 只接受 http(s) 并丢弃路径", () => {
    expect(normalizeOrigin("https://trpg.example.com/")).toBe(
      "https://trpg.example.com",
    );
    expect(normalizeOrigin("https://trpg.example.com:8443/api/v1?x=1")).toBe(
      "https://trpg.example.com:8443",
    );
    expect(normalizeOrigin("http://192.168.1.5:8765")).toBe(
      "http://192.168.1.5:8765",
    );
    expect(normalizeOrigin("ftp://example.com")).toBeNull();
    expect(normalizeOrigin("not a url")).toBeNull();
    expect(normalizeOrigin("   ")).toBeNull();
  });

  it("setCloudOrigin 保存、清除并校验输入", () => {
    expect(getCloudOrigin()).toBeNull();
    expect(setCloudOrigin("https://trpg.example.com/")).toBe(true);
    expect(getCloudOrigin()).toBe("https://trpg.example.com");
    expect(setCloudOrigin("not a url")).toBe(false);
    expect(getCloudOrigin()).toBe("https://trpg.example.com");
    expect(setCloudOrigin(null)).toBe(true);
    expect(getCloudOrigin()).toBeNull();
  });

  it("apiHttpOrigin 优先使用云端配置，未配置时回退本地推导", () => {
    expect(apiHttpOrigin()).toBe("http://localhost:8765");
    setCloudOrigin("https://trpg.example.com");
    expect(apiHttpOrigin()).toBe("https://trpg.example.com");
  });
});

describe("apiFetch", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("解析成功响应并携带 Cookie", async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({ id: "u1", username: "alice" }),
    );
    const schema = z.looseObject({ id: z.string(), username: z.string() });
    const result = await apiFetch("/api/auth/me", schema);
    expect(result).toEqual({ id: "u1", username: "alice" });
    const [url, init] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://localhost:8765/api/auth/me");
    expect(init.credentials).toBe("include");
    expect(init.method).toBe("GET");
  });

  it("POST 请求序列化 JSON body", async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({ id: "u1", username: "alice" }),
    );
    const schema = z.looseObject({ id: z.string() });
    await apiFetch("/api/auth/login", schema, {
      method: "POST",
      body: { username: "alice", password: "secret" },
    });
    const [, init] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
    expect(init.headers).toEqual({ "Content-Type": "application/json" });
    expect(init.body).toBe(
      JSON.stringify({ username: "alice", password: "secret" }),
    );
  });

  it("204 响应对 void 端点解析为 undefined", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(null, { status: 204 }));
    await expect(
      apiFetch("/api/auth/logout", z.undefined()),
    ).resolves.toBeUndefined();
  });

  it("网络错误归一化为 network_error", async () => {
    vi.mocked(fetch).mockRejectedValue(new TypeError("Failed to fetch"));
    const error = await apiFetch("/api/auth/me", z.looseObject({})).catch(
      (e) => e,
    );
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(0);
    expect(error.code).toBe("network_error");
    expect(error.isNetwork).toBe(true);
  });

  it("HTTP 错误提取 error_code 与 error 文案", async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({ error_code: "invite_expired", error: "邀请已过期" }, 410),
    );
    const error = await apiFetch("/api/invites/x/accept", z.looseObject({}), {
      method: "POST",
    }).catch((e) => e);
    expect(error.status).toBe(410);
    expect(error.code).toBe("invite_expired");
    expect(error.message).toBe("邀请已过期");
  });

  it("HTTP 错误回退 detail 字段与通用文案", async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({ detail: "无效的用户名或密码" }, 401),
    );
    const error = await apiFetch("/api/auth/login", z.looseObject({}), {
      method: "POST",
    }).catch((e) => e);
    expect(error.message).toBe("无效的用户名或密码");
    expect(error.isUnauthorized).toBe(true);

    vi.mocked(fetch).mockResolvedValue(
      new Response("gateway timeout", { status: 504 }),
    );
    const fallback = await apiFetch("/api/worlds", z.looseObject({})).catch(
      (e) => e,
    );
    expect(fallback.message).toBe("请求失败（HTTP 504）");
  });

  it("401 触发 onUnauthorized 订阅", async () => {
    const listener = vi.fn();
    const unsubscribe = onUnauthorized(listener);
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ detail: "未登录" }, 401));
    await apiFetch("/api/worlds", z.looseObject({})).catch(() => {});
    expect(listener).toHaveBeenCalledTimes(1);
    unsubscribe();
    await apiFetch("/api/worlds", z.looseObject({})).catch(() => {});
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("响应格式与 schema 不符时报告 invalid_payload", async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({ worlds: "not-an-array" }),
    );
    const error = await apiFetch(
      "/api/worlds",
      z.object({ worlds: z.array(z.string()) }),
    ).catch((e) => e);
    expect(error.code).toBe("invalid_payload");
    expect(error.status).toBe(200);
  });
});
