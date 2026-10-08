/** Read/write capabilities are separate; keeper authority is not ownership. */
import { loadSave, openSavePanel, quickSave } from "../../../panels";
import { useSaveReadBlockReason } from "../../../save-read-boundary";
import { useAppStore } from "../../../state/app-store";
import { isRoomOwner, useOnlineStore } from "../../../state/online-store";

export function KeeperSaveActions() {
  const mode = useAppStore((s) => s.mode);
  const connection = useAppStore((s) => s.connection);
  useOnlineStore((s) => s.user);
  useOnlineStore((s) => s.members);
  const roomConnection = useOnlineStore((s) => s.roomConnection);
  const snapshotReady = useOnlineStore((s) => s.roomSnapshotReady);
  const readPolicy = useSaveReadBlockReason();
  const writeBlocked =
    mode === "online" && !isRoomOwner()
      ? "存档操作仅房主可用。主持授权不包含房主权限，请由房主保存或管理。"
      : connection !== "connected" ||
          (mode === "online" &&
            (roomConnection !== "connected" || !snapshotReady))
        ? "正在连接并同步当前进度，完成后可操作存档。"
        : null;
  const readBlocked = writeBlocked ?? readPolicy;

  return (
    <section className="keeper-save-actions" aria-label="存档与续团">
      <h4 className="keeper-section-title">存档与续团</h4>
      <p className="keeper-note">
        保存记录当前进度；读取会回到自动存档，未保存进度不会保留。
        {mode === "online" ? "云端存档由房主操作。" : "请先保存再读取。"}
      </p>
      <div className="structured-card-actions">
        <button
          type="button"
          className="btn-ghost structured-btn"
          data-testid="keeper-save"
          disabled={writeBlocked !== null}
          title={writeBlocked ?? undefined}
          onClick={() => quickSave()}
        >
          快速存档
        </button>
        <button
          type="button"
          className="btn-ghost structured-btn"
          data-testid="keeper-load"
          disabled={readBlocked !== null}
          title={readBlocked ?? undefined}
          onClick={() => loadSave("slot_000")}
        >
          读取自动存档
        </button>
        <button
          type="button"
          className="btn-ghost structured-btn"
          data-testid="keeper-save-panel"
          disabled={writeBlocked !== null}
          title={writeBlocked ?? undefined}
          onClick={() => openSavePanel("manage")}
        >
          存档管理
        </button>
      </div>
      {readBlocked && (
        <p
          className="keeper-save-boundary"
          data-testid="keeper-save-reason"
          role="status"
        >
          {readBlocked}
        </p>
      )}
    </section>
  );
}
