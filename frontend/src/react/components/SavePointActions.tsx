import type { SaveEntry } from "../../state/app-store";

export type SavePointAction = {
  kind: "load" | "delete";
  save: SaveEntry;
  returnFocus: HTMLButtonElement;
};

export function SavePointActions({
  save,
  manage,
  canOperate,
  onRequest,
  onRename,
}: {
  save: SaveEntry;
  manage: boolean;
  canOperate: boolean;
  onRequest: (action: SavePointAction) => void;
  onRename: () => void;
}) {
  return (
    <>
      <button
        type="button"
        className="save-action-load"
        disabled={!canOperate}
        onClick={(event) =>
          onRequest({ kind: "load", save, returnFocus: event.currentTarget })
        }
      >
        读取
      </button>
      {manage && canOperate && (
        <>
          <button
            type="button"
            className="save-action-rename"
            onClick={onRename}
          >
            重命名
          </button>
          {save.id !== "slot_000" && (
            <button
              type="button"
              className="save-action-del"
              onClick={(event) =>
                onRequest({
                  kind: "delete",
                  save,
                  returnFocus: event.currentTarget,
                })
              }
            >
              删除
            </button>
          )}
        </>
      )}
    </>
  );
}

export function SavePointConfirmation({
  action,
  canOperate,
  online,
  onCancel,
  onConfirm,
}: {
  action: SavePointAction;
  canOperate: boolean;
  online: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const loading = action.kind === "load";
  const name = action.save.label || action.save.scene_name || "此存档点";
  return (
    <div
      className="save-point-confirmation"
      role="group"
      aria-label={`确认${loading ? "读取" : "删除"}${name}`}
      data-dialog-escape
      onKeyDown={(event) => {
        if (event.nativeEvent.isComposing) return;
        if (event.key === "Escape") {
          event.preventDefault();
          event.stopPropagation();
          onCancel();
        }
      }}
    >
      <div className="save-point-confirmation-copy">
        <strong>
          {loading ? "确认读取" : "确认删除存档点"} · {name}
        </strong>
        <p>
          {loading
            ? "读取将恢复到此存档，未保存进度不会保留。"
            : "只删除这个存档点，不删除整条时间线；此操作没有自助撤销入口。"}
          {online && " 房间存档操作仅房主可用，读档会影响房间所有成员。"}
        </p>
      </div>
      <div className="save-point-confirmation-actions">
        <button
          autoFocus
          type="button"
          className="btn-ghost"
          onClick={onCancel}
        >
          取消
        </button>
        <button
          type="button"
          className="btn-primary"
          disabled={!canOperate}
          onClick={onConfirm}
        >
          {loading ? "确认读取" : "确认删除存档点"}
        </button>
      </div>
    </div>
  );
}
