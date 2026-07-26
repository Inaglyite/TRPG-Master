import { useAppStore } from "../../state/app-store";
import { useOnlineStore } from "../../state/online-store";
import { recoverLatestTurn } from "../../ws";

export function ConnectionNotice() {
  const message = useAppStore((state) => state.connectionNotice);
  const canRecover = useAppStore((state) => state.connectionRecoveryAvailable);
  const mode = useAppStore((state) => state.mode);
  // save_load 在多人房间是房主控制操作；非房主隐藏恢复按钮（服务端仍会拒绝）。
  const isOwner = useOnlineStore((state) => {
    const uid = state.user?.id;
    return (
      uid != null &&
      state.members.some(
        (member) => member.user_id === uid && member.role === "owner",
      )
    );
  });
  const recoverVisible = canRecover && (mode !== "online" || isOwner);

  if (!message) return null;

  return (
    <div className="connection-notice" role="status" aria-live="polite">
      <span>{message}</span>
      {recoverVisible && (
        <button type="button" onClick={recoverLatestTurn}>
          恢复最近自动存档
        </button>
      )}
    </div>
  );
}
