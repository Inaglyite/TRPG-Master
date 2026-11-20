import { z } from "zod";

import { apiFetch } from "./client";

export const authUserSchema = z.looseObject({
  id: z.string(),
  username: z.string(),
  display_name: z.string().optional(),
  status: z.string().optional(),
});
export type AuthUser = z.infer<typeof authUserSchema>;

/** 查询当前登录账号；未登录时抛出 401 ApiError。 */
export function fetchMe(): Promise<AuthUser> {
  return apiFetch("/api/auth/me", authUserSchema, { timeoutMs: 15_000 });
}

export function login(username: string, password: string): Promise<AuthUser> {
  return apiFetch("/api/auth/login", authUserSchema, {
    method: "POST",
    body: { username, password },
  });
}

export function registerAccount(
  username: string,
  password: string,
): Promise<AuthUser> {
  return apiFetch("/api/auth/register", authUserSchema, {
    method: "POST",
    body: { username, password },
  });
}

export function updateProfile(displayName: string): Promise<AuthUser> {
  return apiFetch("/api/auth/profile", authUserSchema, {
    method: "PATCH",
    body: { display_name: displayName },
  });
}

export function changePassword(
  currentPassword: string,
  newPassword: string,
): Promise<void> {
  return apiFetch("/api/auth/password", z.undefined(), {
    method: "POST",
    body: { current_password: currentPassword, new_password: newPassword },
  });
}

/** 撤销服务端 Session；成功返回 204。 */
export function logout(): Promise<void> {
  return apiFetch("/api/auth/logout", z.undefined(), { method: "POST" });
}
