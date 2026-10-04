import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { z } from "zod";

import {
  ApiError,
  apiFetch,
  apiHttpOrigin,
  getCloudOrigin,
  normalizeOrigin,
  OFFICIAL_CLOUD_ORIGIN,
  onUnauthorized,
  setCloudOrigin,
} from "./client";
import { abandonWorld, acceptInvite, deleteWorld } from "./worlds";
import { fetchMe } from "./auth";
import { invalidateCloudRequests } from "./request-context";

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
    expect(normalizeOrigin("https://user:private@example.com")).toBeNull();
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

  it("官方 origin 固定为 https 裸 origin", () => {
    expect(OFFICIAL_CLOUD_ORIGIN).toBe("https://trpggame.xyz");
    expect(normalizeOrigin(OFFICIAL_CLOUD_ORIGIN)).toBe(OFFICIAL_CLOUD_ORIGIN);
  });

  it("apiHttpOrigin 优先使用云端配置，未配置时回退本地推导", () => {
    expect(apiHttpOrigin()).toBe("http://localhost:8765");
    setCloudOrigin("https://trpg.example.com");
    expect(apiHttpOrigin()).toBe("https://trpg.example.com");
  });
});

describe("apiFetch", () => {
  it("session revocation on the same server invalidates old successful data", async () => {
    let finish!: (response: Response) => void;
    vi.mocked(fetch).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const pending = apiFetch(
      "/api/worlds",
      z.object({ secret: z.string() }),
    ).catch((error) => error);
    invalidateCloudRequests();
    finish(jsonResponse({ secret: "old-account" }));
    expect(await pending).toMatchObject({ code: "request_context_changed" });
  });

  it("same-origin edits do not invalidate a valid in-flight response", async () => {
    setCloudOrigin("https://table.example.com");
    let finish!: (response: Response) => void;
    vi.mocked(fetch).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const pending = apiFetch("/api/worlds", z.object({ ok: z.boolean() }));
    setCloudOrigin("https://table.example.com/path");
    finish(jsonResponse({ ok: true }));
    await expect(pending).resolves.toEqual({ ok: true });
  });

  it("also guards a response whose JSON body arrives after switching", async () => {
    let finish!: (value: unknown) => void;
    const response = jsonResponse({});
    vi.spyOn(response, "json").mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    vi.mocked(fetch).mockResolvedValue(response);
    const pending = apiFetch(
      "/api/worlds",
      z.object({ secret: z.string() }),
    ).catch((error) => error);
    await vi.waitFor(() => expect(finish).toBeTypeOf("function"));
    setCloudOrigin("https://second.example.com");
    finish({ secret: "old-server" });
    expect(await pending).toMatchObject({ code: "request_context_changed" });
  });

  it("local requests survive an unrelated cloud switch", async () => {
    let finish!: (response: Response) => void;
    vi.mocked(fetch).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const pending = apiFetch("/local-assets", z.object({ ok: z.boolean() }), {
      local: true,
    });
    setCloudOrigin("https://second.example.com");
    finish(jsonResponse({ ok: true }));
    await expect(pending).resolves.toEqual({ ok: true });
  });

  it("timed-out session verification has a bounded, actionable failure", async () => {
    vi.useFakeTimers();
    vi.mocked(fetch).mockImplementation(() => new Promise(() => {}));
    try {
      const pending = expect(fetchMe()).rejects.toMatchObject({
        code: "request_timeout",
      });
      await vi.advanceTimersByTimeAsync(15_000);
      await pending;
      expect(vi.mocked(fetch).mock.calls[0][1]?.signal?.aborted).toBe(true);
      expect(vi.getTimerCount()).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it("a credential rejection is returned without a global expiry broadcast", async () => {
    const listener = vi.fn();
    const unsubscribe = onUnauthorized(listener);
    try {
      vi.mocked(fetch).mockResolvedValue(
        jsonResponse({ detail: "已失效" }, 401),
      );
      await expect(
        apiFetch("/api/auth/logout", z.undefined(), { method: "POST" }),
      ).rejects.toMatchObject({ status: 401 });
      expect(listener).not.toHaveBeenCalled();
    } finally {
      unsubscribe();
    }
  });

  it("failed removal preserves the previous address and reports failure", () => {
    setCloudOrigin("https://table.example.com");
    const spy = vi
      .spyOn(Storage.prototype, "removeItem")
      .mockImplementation(() => {
        throw new Error("disabled storage");
      });
    try {
      expect(setCloudOrigin(null)).toBe(false);
      expect(getCloudOrigin()).toBe("https://table.example.com");
    } finally {
      spy.mockRestore();
    }
  });
  it("a local backend 401 never expires the independent cloud session", async () => {
    const listener = vi.fn();
    const unsubscribe = onUnauthorized(listener);
    try {
      setCloudOrigin("https://table.example.com");
      vi.mocked(fetch).mockResolvedValue(
        jsonResponse({ detail: "本地无权限" }, 401),
      );
      await expect(
        apiFetch("/api/worlds/local/keeper-guide", z.looseObject({}), {
          local: true,
        }),
      ).rejects.toMatchObject({ status: 401 });
      expect(listener).not.toHaveBeenCalled();
    } finally {
      unsubscribe();
    }
  });

  it("a late 401 from a previous server cannot log out the current server", async () => {
    let finish!: (response: Response) => void;
    vi.mocked(fetch).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    setCloudOrigin("https://first.example.com");
    const listener = vi.fn();
    const unsubscribe = onUnauthorized(listener);
    try {
      const pending = apiFetch("/api/worlds", z.looseObject({})).catch(
        (error) => error,
      );
      setCloudOrigin("https://second.example.com");
      finish(jsonResponse({ detail: "旧服务器已过期" }, 401));
      expect(await pending).toMatchObject({ code: "request_context_changed" });
      expect(listener).not.toHaveBeenCalled();
    } finally {
      unsubscribe();
    }
  });

  it("switching away and back cannot revive an old successful response", async () => {
    let finish!: (response: Response) => void;
    vi.mocked(fetch).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    setCloudOrigin("https://first.example.com");
    const pending = apiFetch(
      "/api/worlds",
      z.object({ secret: z.string() }),
    ).catch((error) => error);
    setCloudOrigin("https://second.example.com");
    setCloudOrigin("https://first.example.com");
    finish(jsonResponse({ secret: "旧账号私密资料" }));
    expect(await pending).toMatchObject({ code: "request_context_changed" });
  });

  it("failed persistence does not pretend the server address was saved", () => {
    const spy = vi
      .spyOn(Storage.prototype, "setItem")
      .mockImplementation(() => {
        throw new Error("disabled storage");
      });
    try {
      expect(setCloudOrigin("https://table.example.com")).toBe(false);
      expect(getCloudOrigin()).toBeNull();
    } finally {
      spy.mockRestore();
    }
  });
  beforeEach(() => {
    localStorage.clear();
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("local image requests use loopback even when a cloud origin is saved", async () => {
    setCloudOrigin("https://trpg.example.com");
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ ok: true }));
    await apiFetch(
      "/api/worlds/world/handouts/photo",
      z.object({ ok: z.boolean() }),
      { local: true },
    );
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8765/api/worlds/world/handouts/photo",
      expect.objectContaining({ credentials: "include" }),
    );
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

  it("接受邀请只在 JSON 请求体传递 token", async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({ world_id: "world-1", role: "player" }),
    );

    await acceptInvite("invite/secret?not-in-url");

    const [url, init] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://localhost:8765/api/invites/accept");
    expect(url).not.toContain("invite/secret");
    expect(init.body).toBe(
      JSON.stringify({ token: "invite/secret?not-in-url" }),
    );
  });

  it("deleteWorld 以 DELETE 请求归档端点，204 解析为 undefined", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(null, { status: 204 }));

    await expect(deleteWorld("world/1?x")).resolves.toBeUndefined();

    const [url, init] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://localhost:8765/api/worlds/world%2F1%3Fx");
    expect(init.method).toBe("DELETE");
    expect(init.credentials).toBe("include");
    expect(init.body).toBeUndefined();
  });

  it("abandonWorld 以 POST 请求调用单人放弃端点", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(null, { status: 204 }));

    await expect(abandonWorld("world/1?x")).resolves.toBeUndefined();

    const [url, init] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://localhost:8765/api/worlds/world%2F1%3Fx/abandon");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");
    expect(init.body).toBeUndefined();
  });

  it("deleteWorld 透出 409 room_active 错误码", async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({ code: "room_active", error: "房间进行中" }, 409),
    );
    const error = await deleteWorld("world-1").catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(409);
    expect(error.code).toBe("room_active");
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
