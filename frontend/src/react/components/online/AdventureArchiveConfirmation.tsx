import { useEffect, useId, useRef } from "react";

/** Shared consequence summary; confirmation never implies physical deletion. */
export function AdventureArchiveConfirmation({
  title,
  busy,
  error,
  phase,
  onCancel,
  onConfirm,
}: {
  title: string;
  busy: boolean;
  error?: string | null;
  /** 大厅抽屉动画相位；不传（如离开冒险浮层）则无进出动画。 */
  phase?: "open" | "closing";
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const id = useId();
  const keepButton = useRef<HTMLButtonElement | null>(null);
  const unavailable = busy || phase === "closing";
  useEffect(() => {
    if (phase !== "closing") keepButton.current?.focus();
  }, [phase]);

  return (
    <section
      className="adventure-archive-confirm"
      aria-labelledby={id}
      data-testid="adventure-archive-confirm"
      data-phase={phase}
      onKeyDown={(event) => {
        if (event.key === "Escape" && !unavailable) {
          event.preventDefault();
          event.stopPropagation();
          onCancel();
        }
      }}
    >
      <span className="adventure-archive-tab">冒险档案 / ARCHIVE</span>
      <h3 id={id}>归档这场冒险？</h3>
      <div className="adventure-archive-body">
        <div className="adventure-archive-summary">
          <span>即将归档</span>
          <strong>{title}</strong>
        </div>
        <ul className="adventure-archive-consequences">
          <li>整场冒险及其分支将从“我的冒险”列表移除。</li>
          <li>正在处理的回合会中断；归档不代表结案或通关。</li>
          <li>数据不会被物理删除，但当前界面没有恢复入口。</li>
        </ul>
        {error && (
          <p className="online-notice online-notice--error" role="alert">
            {error}
          </p>
        )}
      </div>
      <div className="adventure-archive-actions">
        <button
          ref={keepButton}
          type="button"
          className="btn-ghost"
          disabled={unavailable}
          onClick={onCancel}
        >
          继续保留
        </button>
        <button
          type="button"
          className="btn-ghost adventure-archive-submit"
          disabled={unavailable}
          onClick={onConfirm}
        >
          {busy ? "正在归档…" : "确认归档"}
        </button>
      </div>
    </section>
  );
}
