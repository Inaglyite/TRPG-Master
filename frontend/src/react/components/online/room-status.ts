/**
 * 房间/邀请状态协议值 → 中文产品文案。协议值本身不变；
 * 未来新增或未知值原样展示（React 文本转义，安全回退，也便于排查新协议值）。
 */
const ROOM_STATUS_LABELS: Record<string, string> = {
  lobby: "大厅",
  starting: "开场中",
  playing: "游戏中",
};

export function roomStatusLabel(status: string): string {
  return ROOM_STATUS_LABELS[status] ?? status;
}

/** 与 src/multiplayer/service.py list_invites 的状态集合保持一致。 */
const INVITE_STATUS_LABELS: Record<string, string> = {
  active: "有效",
  revoked: "已撤销",
  expired: "已过期",
  exhausted: "已用尽",
};

export function inviteStatusLabel(status: string): string {
  return INVITE_STATUS_LABELS[status] ?? status;
}
