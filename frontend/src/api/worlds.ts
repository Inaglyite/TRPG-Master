import { z } from "zod";

import { apiFetch } from "./client";

/**
 * 世界/房间相关的云端 API。
 *
 * 契约状态说明（与 docs/API.md 的多人房间控制面对齐）：
 * - 已实现（后端多人接口）：worlds 创建/列表、成员列表/改角色/移除、邀请
 *   创建/列表/撤销/接受、调查员 options/认领/释放、房主移交。
 *   ready/start/actor 走 /ws/room 房间 WS（room_ready、start{action_id}、
 *   actor_assign），无 HTTP 路由。
 */

export const worldMetadataSchema = z.looseObject({
  name: z.string().optional(),
  room_status: z.string().optional(),
  max_players: z.number().int().optional(),
  // 云端私密单人世界为 "solo"；缺省按多人处理。
  play_mode: z.string().optional(),
});
export type WorldMetadata = z.infer<typeof worldMetadataSchema>;

export const worldSummarySchema = z.looseObject({
  world_id: z.string(),
  module: z.string(),
  role: z.string(),
  updated_at: z.string().optional(),
  metadata: worldMetadataSchema.optional(),
  member_count: z.number().int().optional(),
  // solo 世界的“继续冒险”目标：树根指针指向的当前时间线；其他世界为自身。
  resume_world_id: z.string().optional(),
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
  options: { name?: string; max_players?: number; play_mode?: string } = {},
): Promise<CreatedWorld> {
  return apiFetch("/api/worlds", createdWorldSchema, {
    method: "POST",
    body: { module, ...options },
  });
}

/**
 * 房主删除房间（逻辑归档，契约见 docs/API.md）：
 * 204 幂等成功；活动房间 409 room_active；非房主 403 owner_required。
 */
export function deleteWorld(worldId: string): Promise<void> {
  return apiFetch(`/api/worlds/${encodeURIComponent(worldId)}`, z.undefined(), {
    method: "DELETE",
  });
}

/**
 * 放弃一份云端私密单人冒险并将其逻辑归档。
 *
 * 与 DELETE 不同：该专用路径不要求先结束游戏——单人世界的房主可以在
 * 回合进行中放弃，服务端会截断进行中回合并把整棵分支时间线树一起归档；
 * 不会触发案件结算、角色成长或奖励。
 */
export function abandonWorld(worldId: string): Promise<void> {
  return apiFetch(
    `/api/worlds/${encodeURIComponent(worldId)}/abandon`,
    z.undefined(),
    { method: "POST" },
  );
}

// —— 单人时间线控制面（大厅就地管理，不建立房间连接） ——

/** 存档位树根下的一条时间线；active 已按树根指针（active_world_id）计算。 */
export const soloTimelineSchema = z.looseObject({
  world_id: z.string(),
  label: z.string().optional(),
  is_branch: z.boolean().optional(),
  parent_world_id: z.string().nullable().optional(),
  depth: z.number().int().optional(),
  active: z.boolean().optional(),
  resumable: z.boolean().optional(),
  scene_name: z.string().optional(),
  character_name: z.string().optional(),
  updated_at: z.string().optional(),
});
export type SoloTimeline = z.infer<typeof soloTimelineSchema>;

export const soloTimelinesSchema = z.looseObject({
  root_world_id: z.string(),
  active_world_id: z.string(),
  worlds: z.array(soloTimelineSchema),
});
export type SoloTimelines = z.infer<typeof soloTimelinesSchema>;

/** 列出单人存档位（world_id 为树根）下的全部时间线与当前指针。 */
export function fetchSoloTimelines(worldId: string): Promise<SoloTimelines> {
  return apiFetch(
    `/api/worlds/${encodeURIComponent(worldId)}/timelines`,
    soloTimelinesSchema,
  );
}

export const soloTimelineSwitchResultSchema = z.looseObject({
  root_world_id: z.string(),
  active_world_id: z.string(),
});

/** 把存档位指针切到目标时间线；target==current 时幂等 200。 */
export function switchSoloTimeline(
  worldId: string,
  targetWorldId: string,
): Promise<z.infer<typeof soloTimelineSwitchResultSchema>> {
  return apiFetch(
    `/api/worlds/${encodeURIComponent(worldId)}/timelines/switch`,
    soloTimelineSwitchResultSchema,
    { method: "POST", body: { target_world_id: targetWorldId } },
  );
}

export const soloTimelineRenameResultSchema = z.looseObject({
  world_id: z.string(),
  label: z.string(),
});

/** 重命名时间线；返回 {world_id, label}。 */
export function renameSoloTimeline(
  worldId: string,
  targetWorldId: string,
  label: string,
): Promise<z.infer<typeof soloTimelineRenameResultSchema>> {
  return apiFetch(
    `/api/worlds/${encodeURIComponent(worldId)}/timelines/rename`,
    soloTimelineRenameResultSchema,
    { method: "POST", body: { target_world_id: targetWorldId, label } },
  );
}

export const soloTimelineArchiveResultSchema = z.looseObject({
  world_id: z.string(),
  fallback_world_id: z.string().optional(),
});

/** 归档（删除）分支时间线；当前/主时间线由服务端 409 拒绝。 */
export function archiveSoloTimeline(
  worldId: string,
  targetWorldId: string,
): Promise<z.infer<typeof soloTimelineArchiveResultSchema>> {
  return apiFetch(
    `/api/worlds/${encodeURIComponent(worldId)}/timelines/archive`,
    soloTimelineArchiveResultSchema,
    { method: "POST", body: { target_world_id: targetWorldId } },
  );
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

/** 接受邀请（token 经请求体提交）。 */
export function acceptInvite(token: string): Promise<AcceptedInvite> {
  return apiFetch(`/api/invites/accept`, acceptedInviteSchema, {
    method: "POST",
    body: { token },
  });
}

// —— 调查员绑定 ——

export const characterOptionSchema = z.looseObject({
  id: z.string(),
  name: z.string(),
  occupation: z.string().optional(),
  era: z.string().optional(),
  source: z.string().optional(),
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
