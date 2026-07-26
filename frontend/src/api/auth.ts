import { z } from "zod";

import { apiFetch } from "./client";

export const authUserSchema = z.looseObject({
  id: z.string(),
  username: z.string(),
  status: z.string().optional(),
});
export type AuthUser = z.infer<typeof authUserSchema>;

const credentialsResponseSchema = z.looseObject({
  id: z.string(),
  username: z.string(),
});

/** 查询当前登录账号；未登录时抛出 401 ApiError。 */
export function fetchMe(): Promise<AuthUser> {
  return apiFetch("/api/auth/me", authUserSchema);
}

export function login(username: string, password: string): Promise<AuthUser> {
  return apiFetch("/api/auth/login", credentialsResponseSchema, {
    method: "POST",
    body: { username, password },
  });
}

export function registerAccount(
  username: string,
  password: string,
): Promise<AuthUser> {
  return apiFetch("/api/auth/register", credentialsResponseSchema, {
    method: "POST",
    body: { username, password },
  });
}

/** 撤销服务端 Session；成功返回 204。 */
export function logout(): Promise<void> {
  return apiFetch("/api/auth/logout", z.undefined(), { method: "POST" });
}
