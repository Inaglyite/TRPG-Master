import { useEffect, useState } from "react";

import {
  assignActor,
  changeMemberRole,
  claimByKey,
  deleteCurrentRoom,
  dismissInvite,
  enterLobby,
  handOverOwnership,
  kickMember,
  leaveRoom,
  newInvite,
  refreshRoom,
  releaseClaim,
  revokeInviteById,
  startGame,
  toggleReady,
} from "../../../online";
import { useOnlineStore } from "../../../state/online-store";

const ROLE_LABELS: Record<string, string> = {
  owner: "房主",
  player: "玩家",
  viewer: "旁观者",
};

const CONNECTION_LABELS: Record<string, string> = {
  connecting: "连接中…",
  connected: "已连接",
  disconnected: "已断开，重连中…",
};

/** character_key 形如 "default:黄千陆"，展示最后一段作为名字。 */
function characterKeyName(key: string): string {
  const parts = key.split(":");
  return parts[parts.length - 1] || key;
}

/** 房间页：成员与准备状态、调查员绑定、邀请、退出与房主开局。 */
export function RoomScreen({ onClose }: { onClose?: () => void }) {
  const user = useOnlineStore((state) => state.user);
  const activeWorldId = useOnlineStore((state) => state.activeWorldId);
  const roomModule = useOnlineStore((state) => state.roomModule);
  const roomMetadata = useOnlineStore((state) => state.roomMetadata);
  const modules = useOnlineStore((state) => state.modules);
  const members = useOnlineStore((state) => state.members);
  const membersStatus = useOnlineStore((state) => state.membersStatus);
  const membersError = useOnlineStore((state) => state.membersError);
  const characterOptions = useOnlineStore((state) => state.characterOptions);
  const charactersStatus = useOnlineStore((state) => state.charactersStatus);
  const roomConnection = useOnlineStore((state) => state.roomConnection);
  const roomStatus = useOnlineStore((state) => state.roomStatus);
  const currentActorUserId = useOnlineStore(
    (state) => state.currentActorUserId,
  );
  const readyUserIds = useOnlineStore((state) => state.readyUserIds);
  const onlineUserIds = useOnlineStore((state) => state.onlineUserIds);
  const invite = useOnlineStore((state) => state.invite);
  const invites = useOnlineStore((state) => state.invites);
  const inviteBusy = useOnlineStore((state) => state.inviteBusy);
  const privateEvents = useOnlineStore((state) => state.privateEvents);
  const privateState = useOnlineStore((state) => state.privateState);
  const roomBusy = useOnlineStore((state) => state.roomBusy);
  const roomError = useOnlineStore((state) => state.roomError);

  const [confirmingLeave, setConfirmingLeave] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [confirmingKick, setConfirmingKick] = useState<string | null>(null);
  const [confirmingTransfer, setConfirmingTransfer] = useState<string | null>(
    null,
  );
  const [inviteRole, setInviteRole] = useState<"player" | "viewer">("player");
  const [inviteHours, setInviteHours] = useState("72");
  const [inviteUses, setInviteUses] = useState("5");
  const [copied, setCopied] = useState(false);

  // 开局后旁观者仍可加入，但不能再升级为玩家或房主；避免保留一个
  // 服务端必然返回 room_already_started 的 player 选项。
  useEffect(() => {
    if (roomStatus === "playing" && inviteRole === "player") {
      setInviteRole("viewer");
    }
  }, [inviteRole, roomStatus]);

  useEffect(() => {
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") void refreshRoom();
    }, 4000);
    return () => clearInterval(timer);
  }, []);

  const moduleTitle = roomModule
    ? (modules.find((module) => module.id === roomModule)?.title ?? roomModule)
    : null;
  const roomTitle = roomMetadata?.name || moduleTitle || "房间";
  const me = members.find((member) => member.user_id === user?.id);
  const isOwner = me?.role === "owner";
  const players = members.filter((member) => member.role !== "viewer");
  const myReady = me ? readyUserIds.includes(me.user_id) : false;
  // 开局门禁：全员在线 + 全员准备 + 全员选角 + 房间连接正常（服务端 room_not_ready 兜底）。
  const startBlockers: string[] = [];
  for (const member of players) {
    if (!onlineUserIds.includes(member.user_id)) {
      startBlockers.push(`${member.username} 离线`);
    }
    if (!readyUserIds.includes(member.user_id)) {
      startBlockers.push(`${member.username} 未准备`);
    }
    if (!member.investigator) {
      startBlockers.push(`${member.username} 未选择调查员`);
    }
  }
  if (roomConnection !== "connected") {
    startBlockers.push("房间连接已断开");
  }
  const canStart = isOwner && startBlockers.length === 0;
  const myInvestigator = me?.investigator ?? null;
  // 规则：服务端拒绝房主退出（含独处）；房主需先移交，关闭房间另做。
  const ownerBlockedFromLeaving = isOwner;
  // 游戏进行中换绑调查员必然被后端拒绝，控件直接禁用。
  const claimsLocked = roomStatus === "playing";
  const privateClues = privateState
    ? Object.values(privateState.clues)
        .flat()
        .filter((clue) => clue.visibility === "private")
    : [];

  async function copyInvite() {
    if (!invite) return;
    try {
      await navigator.clipboard.writeText(invite.token);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className="online-box online-card online-card--wide online-room-screen">
      <header className="online-header">
        <div>
          <h1 className="online-title online-title--small">{roomTitle}</h1>
          <p className="online-subtitle">
            {moduleTitle && roomMetadata?.name ? `${moduleTitle} · ` : ""}
            {activeWorldId ? `房间号 ${activeWorldId}` : ""}
          </p>
        </div>
        <div className="online-header-side">
          <span
            className={
              roomConnection === "connected"
                ? "online-badge online-badge--online"
                : "online-badge"
            }
            role="status"
          >
            {CONNECTION_LABELS[roomConnection] ?? roomConnection}
          </span>
          {roomStatus && <span className="online-badge">{roomStatus}</span>}
          {me && (
            <span className="online-badge">
              {ROLE_LABELS[me.role] ?? me.role}
            </span>
          )}
          {onClose && (
            <button type="button" className="btn-ghost" onClick={onClose}>
              返回游戏
            </button>
          )}
          <button
            type="button"
            className="btn-ghost"
            onClick={() => void enterLobby()}
          >
            ← 大厅
          </button>
        </div>
      </header>

      {roomError && (
        <p className="online-notice online-notice--error" role="alert">
          {roomError}
        </p>
      )}

      <section className="online-section" aria-labelledby="room-members-title">
        <div className="online-section-head">
          <h2 id="room-members-title">成员</h2>
          <button
            type="button"
            className="btn-ghost"
            onClick={() => void refreshRoom()}
            disabled={membersStatus === "loading"}
          >
            {membersStatus === "loading" ? "刷新中……" : "刷新"}
          </button>
        </div>
        {membersStatus === "loading" && members.length === 0 && (
          <p className="online-loading" role="status">
            正在读取成员……
          </p>
        )}
        {membersStatus === "unsupported" && (
          <p className="online-empty">成员列表接口暂不可用，请稍后重试。</p>
        )}
        {membersStatus === "error" && (
          <p className="online-notice online-notice--error" role="alert">
            {membersError ?? "无法读取成员列表"}
          </p>
        )}
        {members.length > 0 && (
          <ul className="member-list">
            {members.map((member) => {
              const manageable =
                isOwner &&
                member.user_id !== user?.id &&
                member.role !== "owner";
              const admissionLocked =
                roomStatus === "playing" && member.role === "viewer";
              const online = onlineUserIds.includes(member.user_id);
              const ready = readyUserIds.includes(member.user_id);
              const isActor = currentActorUserId === member.user_id;
              return (
                <li key={member.user_id} className="member-row">
                  <span className="member-name">
                    {member.username}
                    {member.user_id === user?.id && (
                      <span className="member-me">（我）</span>
                    )}
                  </span>
                  <span className="member-badges">
                    <span className="online-badge">
                      {ROLE_LABELS[member.role] ?? member.role}
                    </span>
                    {isActor && (
                      <span className="online-badge online-badge--ready">
                        行动中
                      </span>
                    )}
                    <span
                      className={
                        online
                          ? "online-badge online-badge--online"
                          : "online-badge"
                      }
                    >
                      {online ? "在线" : "离线"}
                    </span>
                    {member.role !== "viewer" && (
                      <span
                        className={
                          ready
                            ? "online-badge online-badge--ready"
                            : "online-badge"
                        }
                      >
                        {ready ? "已准备" : "未准备"}
                      </span>
                    )}
                    {member.investigator && (
                      <span className="online-badge online-badge--investigator">
                        {characterKeyName(member.investigator.character_key)}
                      </span>
                    )}
                  </span>
                  {isOwner && member.user_id !== user?.id && (
                    <span className="member-actions">
                      {member.role !== "viewer" && (
                        <button
                          type="button"
                          className="btn-ghost"
                          disabled={isActor}
                          onClick={() => void assignActor(member.user_id)}
                        >
                          指定行动
                        </button>
                      )}
                      {manageable && (
                        <>
                          <button
                            type="button"
                            className="btn-ghost"
                            disabled={roomBusy || admissionLocked}
                            title={
                              admissionLocked
                                ? "游戏进行中不能将旁观者提升为玩家"
                                : undefined
                            }
                            onClick={() =>
                              void changeMemberRole(
                                member.user_id,
                                member.role === "viewer" ? "player" : "viewer",
                              )
                            }
                          >
                            {member.role === "viewer" ? "设为玩家" : "设为旁观"}
                          </button>
                          {confirmingTransfer === member.user_id ? (
                            <span className="member-action-group">
                              <button
                                type="button"
                                className="btn-ghost"
                                disabled={roomBusy || admissionLocked}
                                title={
                                  admissionLocked
                                    ? "游戏进行中不能将旁观者设为房主"
                                    : undefined
                                }
                                onClick={() => {
                                  setConfirmingTransfer(null);
                                  void handOverOwnership(member.user_id);
                                }}
                              >
                                确认移交房主
                              </button>
                              <button
                                type="button"
                                className="btn-ghost"
                                onClick={() => setConfirmingTransfer(null)}
                              >
                                取消
                              </button>
                            </span>
                          ) : (
                            <button
                              type="button"
                              className="btn-ghost"
                              disabled={roomBusy || admissionLocked}
                              title={
                                admissionLocked
                                  ? "游戏进行中不能将旁观者设为房主"
                                  : undefined
                              }
                              onClick={() =>
                                setConfirmingTransfer(member.user_id)
                              }
                            >
                              移交
                            </button>
                          )}
                          {confirmingKick === member.user_id ? (
                            <span className="member-action-group">
                              <button
                                type="button"
                                className="btn-ghost online-danger"
                                disabled={roomBusy}
                                onClick={() => {
                                  setConfirmingKick(null);
                                  void kickMember(member.user_id);
                                }}
                              >
                                确认移除
                              </button>
                              <button
                                type="button"
                                className="btn-ghost"
                                onClick={() => setConfirmingKick(null)}
                              >
                                取消
                              </button>
                            </span>
                          ) : (
                            <button
                              type="button"
                              className="btn-ghost"
                              disabled={roomBusy}
                              onClick={() => setConfirmingKick(member.user_id)}
                            >
                              移除
                            </button>
                          )}
                        </>
                      )}
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {me?.role !== "viewer" && (
        <section
          className="online-section"
          aria-labelledby="room-investigators-title"
        >
          <h2 id="room-investigators-title">调查员</h2>
          {charactersStatus === "loading" && (
            <p className="online-loading" role="status">
              正在读取调查员……
            </p>
          )}
          {charactersStatus === "unsupported" && (
            <p className="online-empty">调查员选项接口暂不可用，请稍后重试。</p>
          )}
          {charactersStatus === "error" && (
            <p className="online-notice online-notice--error" role="alert">
              无法读取调查员列表
            </p>
          )}
          {charactersStatus === "ready" && characterOptions.length === 0 && (
            <p className="online-empty">该模组暂无可选调查员。</p>
          )}
          {characterOptions.length > 0 && (
            <ul className="member-list">
              {characterOptions.map((option) => {
                const mine = myInvestigator?.character_key === option.id;
                const holder = members.find(
                  (member) => member.investigator?.character_key === option.id,
                );
                return (
                  <li key={option.id} className="member-row">
                    <span className="member-name">
                      {option.name}
                      {option.occupation && (
                        <span className="member-me">
                          （{option.occupation}）
                        </span>
                      )}
                    </span>
                    <span className="member-badges">
                      {mine ? (
                        <button
                          type="button"
                          className="btn-ghost"
                          disabled={roomBusy || !myInvestigator || claimsLocked}
                          title={
                            claimsLocked
                              ? "游戏进行中不能更换调查员"
                              : undefined
                          }
                          onClick={() => void releaseClaim(myInvestigator!.id)}
                        >
                          释放
                        </button>
                      ) : (
                        <button
                          type="button"
                          className="btn-ghost"
                          disabled={roomBusy || holder != null || claimsLocked}
                          title={
                            claimsLocked
                              ? "游戏进行中不能更换调查员"
                              : undefined
                          }
                          onClick={() => void claimByKey(option.id)}
                        >
                          {holder ? "已被占用" : "选择"}
                        </button>
                      )}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      )}

      {isOwner && (
        <section className="online-section" aria-labelledby="room-invite-title">
          <h2 id="room-invite-title">邀请</h2>
          {invite ? (
            <div className="invite-box">
              <code className="invite-token">{invite.token}</code>
              <div className="online-server-actions">
                <button
                  type="button"
                  className="btn-primary"
                  onClick={() => void copyInvite()}
                >
                  {copied ? "已复制" : "复制邀请码"}
                </button>
                <button
                  type="button"
                  className="btn-ghost"
                  onClick={() => void dismissInvite()}
                >
                  撤销
                </button>
              </div>
              <p className="room-card-time">
                {invite.expires_at &&
                  `有效期至 ${new Date(invite.expires_at).toLocaleString()}`}
                {invite.expires_at && invite.max_uses != null && " · "}
                {invite.max_uses != null && `最多使用 ${invite.max_uses} 次`}
              </p>
            </div>
          ) : (
            <div className="invite-create">
              <div className="online-inline-form">
                <select
                  value={inviteRole}
                  onChange={(event) =>
                    setInviteRole(event.target.value as "player" | "viewer")
                  }
                  aria-label="邀请角色"
                  disabled={inviteBusy}
                >
                  {roomStatus !== "playing" && (
                    <option value="player">玩家</option>
                  )}
                  <option value="viewer">旁观者</option>
                </select>
                <input
                  value={inviteHours}
                  onChange={(event) => setInviteHours(event.target.value)}
                  aria-label="有效期（小时）"
                  inputMode="numeric"
                  disabled={inviteBusy}
                />
                <input
                  value={inviteUses}
                  onChange={(event) => setInviteUses(event.target.value)}
                  aria-label="使用次数"
                  inputMode="numeric"
                  disabled={inviteBusy}
                />
                <button
                  type="button"
                  className="btn-ghost"
                  disabled={inviteBusy}
                  onClick={() =>
                    void newInvite({
                      role: inviteRole,
                      expires_in_hours: Number(inviteHours) || 72,
                      max_uses: Number(inviteUses) || 5,
                    })
                  }
                >
                  {inviteBusy ? "生成中……" : "生成邀请码"}
                </button>
              </div>
              <p className="online-hint">角色 / 有效期（小时）/ 使用次数</p>
            </div>
          )}
          {invites.length > 0 && (
            <ul className="member-list">
              {invites.map((item) => (
                <li key={item.invite_id} className="member-row">
                  <span className="member-name">
                    邀请 {item.invite_id.slice(0, 8)}…
                    {item.role && (
                      <span className="member-me">
                        （{ROLE_LABELS[item.role] ?? item.role}）
                      </span>
                    )}
                  </span>
                  <span className="member-badges">
                    {item.status && (
                      <span className="online-badge">{item.status}</span>
                    )}
                    {item.used_count != null && (
                      <span className="online-badge">
                        已用 {item.used_count}
                        {item.max_uses != null ? `/${item.max_uses}` : ""} 次
                      </span>
                    )}
                    {(!item.status || item.status === "active") && (
                      <button
                        type="button"
                        className="btn-ghost"
                        disabled={roomBusy}
                        onClick={() => void revokeInviteById(item.invite_id)}
                      >
                        撤销
                      </button>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {isOwner && (
        <section className="online-section" aria-labelledby="room-danger-title">
          <h2 id="room-danger-title">房间处置</h2>
          {roomStatus === "lobby" ? (
            confirmingDelete ? (
              <span className="online-server-actions">
                <button
                  type="button"
                  className="btn-primary online-danger"
                  disabled={roomBusy}
                  onClick={() => {
                    setConfirmingDelete(false);
                    void deleteCurrentRoom();
                  }}
                >
                  确认删除房间
                </button>
                <button
                  type="button"
                  className="btn-ghost"
                  onClick={() => setConfirmingDelete(false)}
                >
                  取消
                </button>
              </span>
            ) : (
              <button
                type="button"
                className="btn-ghost"
                onClick={() => setConfirmingDelete(true)}
              >
                删除房间
              </button>
            )
          ) : (
            <button
              type="button"
              className="btn-ghost"
              disabled
              title="游戏进行中无法删除房间"
            >
              删除房间
            </button>
          )}
          <p className="online-hint">
            {roomStatus === "lobby"
              ? "删除为逻辑归档：房间将从普通房间列表消失，当前无法从列表恢复；全部邀请码立即失效。服务端会断开所有成员连接。"
              : "游戏进行中无法删除房间，请先结束当前游戏（服务端仍会最终校验）。"}
          </p>
        </section>
      )}

      {(privateClues.length > 0 || privateEvents.length > 0) && (
        <section
          className="online-section"
          aria-labelledby="room-private-title"
        >
          <h2 id="room-private-title">私密线索</h2>
          <ul className="member-list">
            {privateClues.map((clue, index) => (
              <li
                key={clue.id ?? `private-state-${index}`}
                className="member-row private-clue"
              >
                <span className="member-name">{clue.text}</span>
                <span className="member-badges">
                  <span className="online-badge online-badge--private">
                    仅你可见
                  </span>
                </span>
              </li>
            ))}
            {privateEvents.map((event, index) => (
              <li
                key={event.roomEventId ?? `private-event-${index}`}
                className="member-row private-clue"
              >
                <span className="member-name">
                  {event.clue?.text ?? "私密事件"}
                </span>
                <span className="member-badges">
                  <span className="online-badge online-badge--private">
                    仅你可见
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="online-section online-actions">
        {roomStatus === "lobby" && me && me.role !== "viewer" && (
          <button
            type="button"
            className="btn-primary"
            onClick={() => void toggleReady(!myReady)}
          >
            {myReady ? "取消准备" : "准备"}
          </button>
        )}
        {roomStatus === "lobby" && isOwner && (
          <button
            type="button"
            className="btn-primary"
            disabled={!canStart}
            title={
              canStart ? "开始游戏" : `尚不能开始：${startBlockers.join("；")}`
            }
            onClick={() => void startGame()}
          >
            开始游戏
          </button>
        )}
        {confirmingLeave ? (
          <span className="online-server-actions">
            <button
              type="button"
              className="btn-primary online-danger"
              onClick={() => void leaveRoom()}
            >
              确认退出房间
            </button>
            <button
              type="button"
              className="btn-ghost"
              onClick={() => setConfirmingLeave(false)}
            >
              取消
            </button>
          </span>
        ) : (
          <button
            type="button"
            className="btn-ghost"
            disabled={ownerBlockedFromLeaving}
            title={
              ownerBlockedFromLeaving ? "房主需先移交房主才能退出" : undefined
            }
            onClick={() => setConfirmingLeave(true)}
          >
            退出房间
          </button>
        )}
      </section>
      {roomStatus === "lobby" &&
        isOwner &&
        !canStart &&
        startBlockers.length > 0 && (
          <p className="online-hint">尚不能开始：{startBlockers.join("；")}</p>
        )}
      {ownerBlockedFromLeaving && (
        <p className="online-hint">
          你是房主：服务端不允许房主直接退出房间。请先把房主移交给其他成员。
        </p>
      )}
    </div>
  );
}
