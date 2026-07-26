import { assignActor } from "../../../online";
import { useOnlineStore } from "../../../state/online-store";

const CONNECTION_LABELS: Record<string, string> = {
  connecting: "连接中…",
  connected: "已连接",
  disconnected: "已断开，重连中…",
};

/**
 * 多人游戏进行中的顶部房间条：不遮挡游戏界面，显示当前行动者与等待状态，
 * 房主可跳过行动者，任何成员可回到房间页（成员/邀请/移交等管理）。
 */
export function GameRoomBar({ onOpenRoom }: { onOpenRoom: () => void }) {
  const user = useOnlineStore((state) => state.user);
  const members = useOnlineStore((state) => state.members);
  const currentActorUserId = useOnlineStore(
    (state) => state.currentActorUserId,
  );
  const roomConnection = useOnlineStore((state) => state.roomConnection);
  const roomMetadata = useOnlineStore((state) => state.roomMetadata);
  const roomModule = useOnlineStore((state) => state.roomModule);
  const modules = useOnlineStore((state) => state.modules);
  const roomError = useOnlineStore((state) => state.roomError);

  const me = members.find((member) => member.user_id === user?.id);
  const isOwner = me?.role === "owner";
  const actor = members.find((member) => member.user_id === currentActorUserId);
  const myTurn = currentActorUserId != null && currentActorUserId === user?.id;
  const players = members.filter((member) => member.role !== "viewer");
  const roomTitle =
    roomMetadata?.name ||
    modules.find((module) => module.id === roomModule)?.title ||
    "房间";

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
    <div className="game-room-bar" data-testid="game-room-bar">
      <span className="game-room-bar-title">{roomTitle}</span>
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
      <span
        className={
          myTurn
            ? "game-room-bar-actor game-room-bar-actor--me"
            : "game-room-bar-actor"
        }
      >
        {myTurn
          ? "轮到你行动"
          : actor
            ? `等待 ${actor.username} 行动…`
            : "等待分配行动者…"}
      </span>
      {roomError && (
        <span className="online-badge online-badge--error" role="alert">
          {roomError}
        </span>
      )}
      <span className="game-room-bar-actions">
        {isOwner && players.length > 1 && (
          <button type="button" className="btn-ghost" onClick={skipActor}>
            跳过行动者
          </button>
        )}
        <button type="button" className="btn-ghost" onClick={onOpenRoom}>
          房间
        </button>
      </span>
    </div>
  );
}
