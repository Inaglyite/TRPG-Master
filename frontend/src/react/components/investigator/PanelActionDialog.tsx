import { useCallback, useEffect, useRef, useState } from "react";

import {
  buildPanelActionText,
  panelActionBlockReason,
  submitPanelAction,
  validatePanelDraft,
} from "../../../investigator-actions";
import { useAppStore } from "../../../state/app-store";
import { useInvestigatorPanelStore } from "../../../state/investigator-panel-store";

// selector 必须返回稳定引用：无库存时复用同一空数组，避免无限重渲染。
const EMPTY_INVENTORY: string[] = [];

/**
 * 「出示 / 使用」行动编辑器：编辑意图 → 预览 → 提交一次普通行动回合。
 * 取消/Escape 不产生任何消息或回合；提交走现有 action 入口，
 * 按钮只发文本，不直接执行消耗、治疗、检定或任何状态修改。
 */
export function PanelActionDialog() {
  const editor = useInvestigatorPanelStore((state) => state.editor);
  const closeEditor = useInvestigatorPanelStore((state) => state.closeEditor);
  const updateDraft = useInvestigatorPanelStore(
    (state) => state.updateEditorDraft,
  );
  const setError = useInvestigatorPanelStore((state) => state.setEditorError);
  const setSending = useInvestigatorPanelStore(
    (state) => state.setEditorSending,
  );
  const inventory = useAppStore(
    (state) => state.character?.inventory || EMPTY_INVENTORY,
  );
  const dialogRef = useRef<HTMLDivElement>(null);
  const restoreFocusRef = useRef<Element | null>(null);

  // 退场动画：closing 期间保持渲染，计时结束才真正清空编辑器状态。
  const [closing, setClosing] = useState(false);
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // 会话身份用 draft 引用：setSending/setError 换 editor 对象但保留 draft；
  // 只有 openEditor 会换新 draft。
  const closingDraftRef = useRef<unknown>(null);

  const requestClose = useCallback(() => {
    if (closing) return;
    const reduce =
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) {
      closeEditor();
      return;
    }
    closingDraftRef.current = editor?.draft ?? null;
    setClosing(true);
    closeTimerRef.current = setTimeout(() => {
      closeTimerRef.current = null;
      setClosing(false);
      closeEditor();
    }, 150);
  }, [closing, closeEditor, editor]);

  // 关闭动画期间若（程序化）打开了另一个编辑器，取消挂起的关闭。
  useEffect(() => {
    if (editor && closing && editor.draft !== closingDraftRef.current) {
      if (closeTimerRef.current) clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
      setClosing(false);
    }
  }, [editor, closing]);

  useEffect(
    () => () => {
      if (closeTimerRef.current) clearTimeout(closeTimerRef.current);
    },
    [],
  );

  const open = editor !== null;

  // 打开时记录触发者并聚焦第一个输入；关闭后焦点返回触发者。
  useEffect(() => {
    if (!open) return;
    restoreFocusRef.current = document.activeElement;
    const dialog = dialogRef.current;
    const first =
      dialog?.querySelector<HTMLElement>("input, textarea, select") ||
      dialog?.querySelector<HTMLElement>("button");
    first?.focus();
    return () => {
      const restore = restoreFocusRef.current;
      if (restore instanceof HTMLElement) restore.focus();
    };
  }, [open]);

  // Escape 取消；Tab 焦点锁定在对话框内。
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        requestClose();
        return;
      }
      if (event.key !== "Tab") return;
      const dialog = dialogRef.current;
      if (!dialog) return;
      const focusables = Array.from(
        dialog.querySelectorAll<HTMLElement>(
          'button, input, textarea, select, [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((element) => !element.hasAttribute("disabled"));
      if (!focusables.length) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  }, [open, requestClose]);

  if (!editor) return null;
  const { draft } = editor;
  const preview = buildPanelActionText(draft);
  const invalid = validatePanelDraft(draft);
  const blocked = panelActionBlockReason();
  const disabledReason = editor.sending
    ? "已发送，等待裁决…"
    : invalid || blocked;

  const submit = () => {
    if (editor.sending) return;
    setSending(true);
    try {
      const result = submitPanelAction(draft, editor.worldId);
      if (result.ok) {
        requestClose();
      } else {
        setError(result.reason);
      }
    } finally {
      setSending(false);
    }
  };

  return (
    <div
      id="panel-action-overlay"
      className={closing ? "closing" : undefined}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) requestClose();
      }}
    >
      <div
        id="panel-action-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="panel-action-title"
        ref={dialogRef}
      >
        <header className="panel-action-header">
          <h3 id="panel-action-title">
            {draft.kind === "present" ? "出示线索" : "使用道具"}
          </h3>
          <button
            type="button"
            className="btn-ghost panel-action-close"
            aria-label="取消并关闭"
            onClick={requestClose}
          >
            ✕
          </button>
        </header>

        {draft.kind === "present" ? (
          <div className="panel-action-body">
            <div className="panel-action-subject">
              线索：<strong>{draft.clueSummary}</strong>
            </div>
            <label className="panel-action-field">
              <span>向谁出示/说明（必填）</span>
              <input
                type="text"
                value={draft.target}
                maxLength={40}
                placeholder="例如：惠特克罗夫特医生"
                onChange={(event) =>
                  updateDraft({ target: event.target.value })
                }
              />
            </label>
            <label className="panel-action-field">
              <span>同时出示随身实物（可选）</span>
              <select
                value={draft.physicalItem || ""}
                onChange={(event) =>
                  updateDraft({ physicalItem: event.target.value || null })
                }
              >
                <option value="">仅说明已知信息</option>
                {inventory.map((item, index) => (
                  <option key={`${item}-${index}`} value={item}>
                    {item}
                  </option>
                ))}
              </select>
            </label>
            <label className="panel-action-field">
              <span>想询问什么（可选）</span>
              <input
                type="text"
                value={draft.question}
                maxLength={120}
                placeholder="例如：他是否见过这张便签"
                onChange={(event) =>
                  updateDraft({ question: event.target.value })
                }
              />
            </label>
          </div>
        ) : (
          <div className="panel-action-body">
            <div className="panel-action-subject">
              道具：<strong>{draft.itemLabel}</strong>
            </div>
            <label className="panel-action-field">
              <span>如何使用（必填）</span>
              <input
                type="text"
                value={draft.usage}
                maxLength={120}
                placeholder="例如：照亮床底，检查是否有可见物品"
                onChange={(event) => updateDraft({ usage: event.target.value })}
              />
            </label>
            <label className="panel-action-field">
              <span>目标/对象（可选）</span>
              <input
                type="text"
                value={draft.target}
                maxLength={40}
                placeholder="例如：自己、房门、惠特克罗夫特"
                onChange={(event) =>
                  updateDraft({ target: event.target.value })
                }
              />
            </label>
          </div>
        )}

        <div className="panel-action-preview">
          <div className="panel-action-preview-label">行动预览</div>
          <div className="panel-action-preview-text">{preview}</div>
          <div className="panel-action-preview-note">
            这是一次行动请求，结果由守秘人与规则裁决。
          </div>
        </div>

        {(editor.error || disabledReason) && (
          <div className="panel-action-error" role="alert">
            {editor.error || disabledReason}
          </div>
        )}

        <footer className="panel-action-footer">
          <button
            type="button"
            className="btn-ghost panel-action-cancel"
            onClick={requestClose}
          >
            取消
          </button>
          <button
            type="button"
            className="btn-primary panel-action-confirm"
            disabled={Boolean(disabledReason)}
            onClick={submit}
          >
            {editor.sending
              ? "已发送，等待裁决…"
              : draft.kind === "present"
                ? "确认出示"
                : "确认使用"}
          </button>
        </footer>
      </div>
    </div>
  );
}
