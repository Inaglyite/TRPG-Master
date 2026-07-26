import { z } from "zod";

import { apiFetch } from "./client";

/**
 * 世界/房间相关的云端 API。
 *
 * 契约状态说明（与 docs/MULTIPLAYER_PLAN.md §6.1 对齐）：
 * - 已实现（后端多人接口）：worlds 创建/列表、成员列表/改角色/移除、邀请
 *   创建/列表/撤销/接受、调查员 options/认领/释放、房主移交。
 *   ready/start/actor 走 /ws/room 房间 WS（room_ready、start{action_id}、
 *   actor_assign），无 HTTP 路由。
 */

export const worldMetadataSchema = z.looseObject({
  name: z.string().optional(),
  room_status: z.string().optional(),
  max_players: z.number().int().optional(),
});
export type WorldMetadata = z.infer<typeof worldMetadataSchema>;

export const worldSummarySchema = z.looseObject({
  world_id: z.string(),
  module: z.string(),
  role: z.string(),
  updated_at: z.string().optional(),
  metadata: worldMetadataSchema.optional(),
  member_count: z.number().int().optional(),
});
export type WorldSummary = z.infer<typeof worldSummarySchema>;

export async function listWorlds(): Promise<WorldSummary[]> {
  const data = await apiFetch(
    "/api/worlds",
    z.object({ worlds: z.array(worldSummarySchema) }),
  );
  return data.worlds;
}

export const createdWorldSchema = z.looseObject({
  world_id: z.string(),
  module: z.string(),
});
export type CreatedWorld = z.infer<typeof createdWorldSchema>;

export function createWorld(
  module: string,
  options: { name?: string; max_players?: number } = {},
): Promise<CreatedWorld> {
  return apiFetch("/api/worlds", createdWorldSchema, {
    method: "POST",
    body: { module, ...options },
  });
}

// —— 成员 ——

export const memberInvestigatorSchema = z.looseObject({
  id: z.string(),
  character_key: z.string(),
  status: z.string().optional(),
});
export type MemberInvestigator = z.infer<typeof memberInvestigatorSchema>;

export const roomMemberSchema = z.looseObject({
  user_id: z.string(),
  username: z.string(),
  role: z.enum(["owner", "player", "viewer"]),
  investigator: memberInvestigatorSchema.nullable().optional(),
  // 在线/准备状态契约尚未落地；字段存在与否决定界面是否展示对应徽章。
  online: z.boolean().optional(),
  ready: z.boolean().optional(),
});
export type RoomMember = z.infer<typeof roomMemberSchema>;

export const roomInfoSchema = z.looseObject({
  world_id: z.string(),
  module: z.string(),
  metadata: worldMetadataSchema.optional(),
  members: z.array(roomMemberSchema),
});
export type RoomInfo = z.infer<typeof roomInfoSchema>;

export function getRoomInfo(worldId: string): Promise<RoomInfo> {
  return apiFetch(
    `/api/worlds/${encodeURIComponent(worldId)}/members`,
    roomInfoSchema,
  );
}

export const memberRolePatchResultSchema = z.looseObject({
  user_id: z.string(),
  role: z.string(),
});

/** 房主修改成员角色（player/viewer）；返回 {user_id, role}。 */
export function updateMember(
  worldId: string,
  userId: string,
  patch: { role?: "player" | "viewer" },
): Promise<z.infer<typeof memberRolePatchResultSchema>> {
  return apiFetch(
    `/api/worlds/${encodeURIComponent(worldId)}/members/${encodeURIComponent(userId)}`,
    memberRolePatchResultSchema,
    { method: "PATCH", body: patch },
  );
}

/** 移除成员（房主）或自己退出房间。 */
export function removeMember(worldId: string, userId: string): Promise<void> {
  return apiFetch(
    `/api/worlds/${encodeURIComponent(worldId)}/members/${encodeURIComponent(userId)}`,
    z.undefined(),
    { method: "DELETE" },
  );
}

/** 移交房主；请求体 {"user_id":"目标账号 ID"}。房主必须先移交才能退出房间。 */
export function transferOwnership(
  worldId: string,
  userId: string,
): Promise<unknown> {
  return apiFetch(
    `/api/worlds/${encodeURIComponent(worldId)}/owner`,
    z.looseObject({}),
    { method: "POST", body: { user_id: userId } },
  );
}

// —— 邀请 ——

export const inviteMetaSchema = z.looseObject({
  invite_id: z.string(),
  role: z.string().optional(),
  max_uses: z.number().int().nullable().optional(),
  used_count: z.number().int().optional(),
  expires_at: z.string().optional(),
  status: z.string().optional(),
});
export type InviteMeta = z.infer<typeof inviteMetaSchema>;

/** 房主列出邀请（仅元数据，不含明文 token）。 */
export async function listInvites(worldId: string): Promise<InviteMeta[]> {
  const data = await apiFetch(
    `/api/worlds/${encodeURIComponent(worldId)}/invites`,
    z.looseObject({ invites: z.array(inviteMetaSchema) }),
  );
  return data.invites;
}

export const roomInviteSchema = z.looseObject({
  invite_id: z.string(),
  token: z.string(),
  world_id: z.string().optional(),
  role: z.string().optional(),
  expires_at: z.string().optional(),
  max_uses: z.number().int().optional(),
});
export type RoomInvite = z.infer<typeof roomInviteSchema>;

/** 创建邀请；明文 token 只在创建响应中返回一次。 */
export function createInvite(
  worldId: string,
  options: {
    role?: "player" | "viewer";
    expires_in_hours?: number;
    max_uses?: number;
  } = {},
): Promise<RoomInvite> {
  return apiFetch(
    `/api/worlds/${encodeURIComponent(worldId)}/invites`,
    roomInviteSchema,
    {
      method: "POST",
      body: { role: options.role ?? "player", ...options },
    },
  );
}

export function revokeInvite(worldId: string, inviteId: string): Promise<void> {
  return apiFetch(
    `/api/worlds/${encodeURIComponent(worldId)}/invites/${encodeURIComponent(inviteId)}`,
    z.undefined(),
    { method: "DELETE" },
  );
}

export const acceptedInviteSchema = z.looseObject({
  world_id: z.string(),
  role: z.string().optional(),
  already_member: z.boolean().optional(),
});
export type AcceptedInvite = z.infer<typeof acceptedInviteSchema>;

export function acceptInvite(token: string): Promise<AcceptedInvite> {
  return apiFetch(
    `/api/invites/${encodeURIComponent(token)}/accept`,
    acceptedInviteSchema,
    { method: "POST" },
  );
}

// —— 调查员绑定 ——

export const characterOptionSchema = z.looseObject({
  id: z.string(),
  name: z.string(),
  occupation: z.string().optional(),
  era: z.string().optional(),
  source_label: z.string().optional(),
});
export type CharacterOption = z.infer<typeof characterOptionSchema>;

export const investigatorOptionsSchema = z.looseObject({
  module: z.string().optional(),
  groups: z.array(
    z.looseObject({
      id: z.string().optional(),
      label: z.string().optional(),
      characters: z.array(characterOptionSchema),
    }),
  ),
});
export type InvestigatorOptions = z.infer<typeof investigatorOptionsSchema>;

/** 房间模组对应的候选调查员列表（groups 结构与 character_list 相同）。 */
export function getInvestigatorOptions(
  worldId: string,
): Promise<InvestigatorOptions> {
  return apiFetch(
    `/api/worlds/${encodeURIComponent(worldId)}/investigators/options`,
    investigatorOptionsSchema,
  );
}

export const claimResultSchema = z.looseObject({
  id: z.string(),
  character_key: z.string(),
  user_id: z.string(),
});
export type ClaimResult = z.infer<typeof claimResultSchema>;

/** 按 character_key（角色列表条目的 id，如 "default:黄千陆"）认领调查员。 */
export function claimInvestigator(
  worldId: string,
  characterKey: string,
): Promise<ClaimResult> {
  return apiFetch(
    `/api/worlds/${encodeURIComponent(worldId)}/investigators/claim`,
    claimResultSchema,
    { method: "POST", body: { character_key: characterKey } },
  );
}

/** 释放调查员；investigator_id 为认领记录 id（见成员列表 investigator.id）。 */
export function releaseInvestigator(
  worldId: string,
  investigatorId: string,
): Promise<void> {
  return apiFetch(
    `/api/worlds/${encodeURIComponent(worldId)}/investigators/${encodeURIComponent(investigatorId)}/claim`,
    z.undefined(),
    { method: "DELETE" },
  );
}
