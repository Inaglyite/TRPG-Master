import { useEffect, useRef, useState } from "react";

import { assignActor } from "../../../online";
import { useOnlineStore } from "../../../state/online-store";

const CONNECTION_LABELS: Record<string, string> = {
  connecting: "连接中…",
  connected: "已连接",
  disconnected: "已断开，重连中…",
};

const ROLE_LABELS: Record<string, string> = {
  owner: "房主",
  player: "玩家",
  viewer: "旁观者",
};

/**
 * 多人游戏进行中的房间坞：位于聊天面板顶部的正常布局区域（不是浮层/模态），
 * 收起时是一个紧凑入口条（状态点 + 房间名 + 轮到谁），展开后是房间管理卡片
 * （连接状态、当前行动者、成员摘要、跳过行动者、进入完整房间管理页）。
 * 不遮挡顶部导航、叙事正文、输入框或右侧场景提示。
 */
export function OnlineRoomDock() {
  const user = useOnlineStore((state) => state.user);
  const view = useOnlineStore((state) => state.view);
  const roomStatus = useOnlineStore((state) => state.roomStatus);
  const roomOpen = useOnlineStore((state) => state.roomOpen);
  const members = useOnlineStore((state) => state.members);
  const currentActorUserId = useOnlineStore(
    (state) => state.currentActorUserId,
  );
  const roomConnection = useOnlineStore((state) => state.roomConnection);
  const roomMetadata = useOnlineStore((state) => state.roomMetadata);
  const roomModule = useOnlineStore((state) => state.roomModule);
  const modules = useOnlineStore((state) => state.modules);
  const roomError = useOnlineStore((state) => state.roomError);
  const onlineUserIds = useOnlineStore((state) => state.onlineUserIds);

  const [expanded, setExpanded] = useState(false);
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);

  // 云端单人房间没有多人管理需求（邀请/移交/跳过行动者均被服务端拒绝），
  // 整个 dock 都不显示，单人界面不出现任何“房间”概念。
  const isSoloRoom = roomMetadata?.play_mode === "solo";
  const visible =
    view === "room" && roomStatus === "playing" && !roomOpen && !isSoloRoom;

  // 展开后把焦点移入卡片，键盘用户立即可达；收起不强制回焦（入口条仍在原位）。
  useEffect(() => {
    if (expanded) closeButtonRef.current?.focus();
  }, [expanded]);

  if (!visible) return null;

  const me = members.find((member) => member.user_id === user?.id);
  const isOwner = me?.role === "owner";
  const actor = members.find((member) => member.user_id === currentActorUserId);
  const myTurn = currentActorUserId != null && currentActorUserId === user?.id;
  const players = members.filter((member) => member.role !== "viewer");
  const roomTitle =
    roomMetadata?.name ||
    modules.find((module) => module.id === roomModule)?.title ||
    "房间";
  const turnText = myTurn
    ? "轮到你行动"
    : actor
      ? `等待 ${actor.username} 行动…`
      : "等待分配行动者…";

  function skipActor() {
    if (players.length < 2) return;
    const index = players.findIndex(
      (member) => member.user_id === currentActorUserId,
    );
    const next = players[(index + 1) % players.length];
    if (next && next.user_id !== currentActorUserId) {
      void assignActor(next.user_id);
    }
  }

  return (
    <div className="online-room-dock" data-testid="online-room-dock">
      <button
        type="button"
        className="online-room-dock-toggle"
        aria-expanded={expanded}
        aria-controls="online-room-dock-card"
        onClick={() => setExpanded((value) => !value)}
      >
        <span
          className={`online-status-dot online-status-dot--${roomConnection}`}
          aria-label={CONNECTION_LABELS[roomConnection] ?? roomConnection}
          role="status"
        />
        <span className="online-room-dock-title">{roomTitle}</span>
        <span
          className={
            myTurn
              ? "online-room-dock-turn online-room-dock-turn--me"
              : "online-room-dock-turn"
          }
        >
          {turnText}
        </span>
        <span className="online-room-dock-chevron" aria-hidden="true">
          {expanded ? "▴" : "▾"}
        </span>
      </button>

      {expanded && (
        <div
          id="online-room-dock-card"
          className="online-room-dock-card"
          role="region"
          aria-label="多人房间状态"
          onKeyDown={(event) => {
            if (event.key === "Escape") setExpanded(false);
          }}
        >
          <div className="online-room-dock-card-head">
            <strong className="online-room-dock-card-title">{roomTitle}</strong>
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
            <button
              type="button"
              className="btn-ghost online-room-dock-close"
              aria-label="收起房间面板"
              ref={closeButtonRef}
              onClick={() => setExpanded(false)}
            >
              收起
            </button>
          </div>

          <p
            className={
              myTurn
                ? "online-room-dock-actor online-room-dock-actor--me"
                : "online-room-dock-actor"
            }
          >
            {turnText}
          </p>

          {roomError && (
            <p className="online-notice online-notice--error" role="alert">
              {roomError}
            </p>
          )}

          <ul className="member-list online-room-dock-members">
            {members.map((member) => {
              const online = onlineUserIds.includes(member.user_id);
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
                  </span>
                </li>
              );
            })}
          </ul>

          <div className="online-room-dock-actions">
            {isOwner && players.length > 1 && (
              <button type="button" className="btn-ghost" onClick={skipActor}>
                跳过行动者
              </button>
            )}
            {!isSoloRoom && (
              <button
                type="button"
                className="btn-ghost"
                onClick={() => useOnlineStore.setState({ roomOpen: true })}
              >
                房间管理
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
