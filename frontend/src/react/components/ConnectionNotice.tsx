import { useAppStore } from "../../state/app-store";
import { recoverLatestTurn } from "../../ws";

export function ConnectionNotice() {
  const message = useAppStore((state) => state.connectionNotice);
  const canRecover = useAppStore((state) => state.connectionRecoveryAvailable);

  if (!message) return null;

  return (
    <div className="connection-notice" role="status" aria-live="polite">
      <span>{message}</span>
      {canRecover && (
        <button type="button" onClick={recoverLatestTurn}>
          恢复最近自动存档
        </button>
      )}
    </div>
  );
}
