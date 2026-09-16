import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  compileStructuredAction,
  structuredPathBlockReason,
  submitStructuredEditor,
} from "../../../investigator-structured-actions";
import {
  PRESENTATIONS,
  type PresentationKind,
} from "../../../protocol/structured";
import {
  presentationBlockReason,
  useStructuredEditorStore,
  validateStructuredDraft,
} from "../../../state/structured-editor-store";
import { useStructuredStore } from "../../../state/structured-store";

const PRESENTATION_LABELS: Record<PresentationKind, string> = {
  describe: "说明内容",
  image: "展示图片",
  original: "展示原件",
};

const PRESENTATION_HINTS: Record<PresentationKind, string> = {
  describe: "只把你知道的信息告诉对方，不要求持有实物。",
  image: "把你有权查看的图片给对方看。",
  original: "出示你实际持有的一件实物；不会转交、不会消耗。",
};

const TARGET_KIND_LABELS: Record<string, string> = {
  npc: "人物",
  investigator: "调查员",
  scene_object: "场景物件",
};

/**
 * 结构化「出示 / 使用」编辑器。
 *
 * 只构造结构请求：线索/物品用稳定 ID，方式与用法用枚举，数量用数字；
 * 文本只作为补充做法（approach）与询问（question）。是否成立、是否消耗、
 * 是否需要检定全部由守秘人与服务端决定，这里不做任何本地扣减。
 */
export function StructuredActionDialog() {
  const draft = useStructuredEditorStore((state) => state.draft);
  const sending = useStructuredEditorStore((state) => state.sending);
  const error = useStructuredEditorStore((state) => state.error);
  const update = useStructuredEditorStore((state) => state.update);
  const close = useStructuredEditorStore((state) => state.close);
  const targets = useStructuredStore((state) => state.targets);
  const items = useStructuredStore((state) => state.items);

  const dialogRef = useRef<HTMLDivElement>(null);
  const restoreFocusRef = useRef<Element | null>(null);
  const [closing, setClosing] = useState(false);
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const requestClose = useCallback(() => {
    if (closing) return;
    const reduce =
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) {
      close();
      return;
    }
    setClosing(true);
    closeTimerRef.current = setTimeout(() => {
      closeTimerRef.current = null;
      setClosing(false);
      close();
    }, 150);
  }, [close, closing]);

  useEffect(
    () => () => {
      if (closeTimerRef.current) clearTimeout(closeTimerRef.current);
    },
    [],
  );

  const open = draft !== null;

  // 重新打开（例如「重新编辑」）必须撤销上一次的延迟关闭：提交成功后会先关掉
  // 编辑器，再排一个 150ms 的退出动画定时器；如果玩家在这段窗口里重新打开，
  // 那个遗留定时器会把刚恢复的草稿清掉（机器越慢窗口越大）。定时器只有在
  // 编辑器仍然关着的时候才该真正执行关闭。
  useEffect(() => {
    if (!open) return;
    if (closeTimerRef.current) {
      clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
    setClosing(false);
  }, [open, draft]);

  useEffect(() => {
    if (!open) return;
    restoreFocusRef.current = document.activeElement;
    const dialog = dialogRef.current;
    const first =
      dialog?.querySelector<HTMLElement>(
        'input:not([type="radio"]), select, textarea',
      ) || dialog?.querySelector<HTMLElement>("button");
    first?.focus();
    return () => {
      const restore = restoreFocusRef.current;
      if (restore instanceof HTMLElement) restore.focus();
    };
  }, [open]);

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

  const physicalOptions = useMemo(() => {
    if (!draft) return [];
    const allowed = new Set(draft.allowedPhysicalItemIds);
    return items.filter((item) => allowed.has(item.id));
  }, [draft, items]);

  if (!draft) return null;

  const invalid = validateStructuredDraft(draft);
  const blocked = structuredPathBlockReason();
  const compiled = compileStructuredAction(draft);
  const disabledReason = sending
    ? "已提交，等待服务端确认…"
    : blocked || (!invalid.ok ? invalid.reason : null);
  const previewPayload = "reason" in compiled ? null : compiled;

  const submit = () => {
    if (sending) return;
    const result = submitStructuredEditor();
    if (result.ok) requestClose();
  };

  return (
    <div
      id="structured-action-overlay"
      className={closing ? "closing" : undefined}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) requestClose();
      }}
    >
      <div
        id="structured-action-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="structured-action-title"
        ref={dialogRef}
      >
        <header className="panel-action-header">
          <h3 id="structured-action-title">
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

        <div className="panel-action-body">
          <p className="panel-action-subject">
            {draft.kind === "present" ? "线索：" : "道具："}
            <strong>{draft.subject}</strong>
          </p>

          {draft.kind === "present" ? (
            <>
              <fieldset className="panel-action-field">
                <legend>出示方式</legend>
                <div
                  className="structured-choice-row"
                  role="radiogroup"
                  aria-label="出示方式"
                >
                  {PRESENTATIONS.map((presentation) => {
                    const reason = presentationBlockReason(draft, presentation);
                    const active = draft.presentation === presentation;
                    return (
                      <button
                        key={presentation}
                        type="button"
                        role="radio"
                        aria-checked={active}
                        className={
                          active
                            ? "btn-ghost structured-choice is-active"
                            : "btn-ghost structured-choice"
                        }
                        disabled={reason !== null}
                        title={reason ?? PRESENTATION_HINTS[presentation]}
                        onClick={() => update({ presentation })}
                      >
                        {PRESENTATION_LABELS[presentation]}
                      </button>
                    );
                  })}
                </div>
                <p className="panel-action-note">
                  {PRESENTATION_HINTS[draft.presentation]}
                  出示不等于转交，也不会消耗物品。
                </p>
              </fieldset>

              {draft.presentation === "original" && (
                <label className="panel-action-field">
                  <span>出示哪件原件</span>
                  <select
                    value={draft.physicalItemId}
                    onChange={(event) =>
                      update({
                        physicalItemId: event.target.value,
                        itemId: event.target.value,
                      })
                    }
                  >
                    <option value="">请选择你实际持有的物品</option>
                    {physicalOptions.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.label}（×{item.quantity}）
                      </option>
                    ))}
                  </select>
                </label>
              )}

              <label className="panel-action-field">
                <span>向谁出示 / 说明</span>
                <select
                  value={
                    draft.targetKind === "unresolved"
                      ? "__unresolved__"
                      : draft.targetId
                        ? `${draft.targetKind}:${draft.targetId}`
                        : ""
                  }
                  onChange={(event) => {
                    const value = event.target.value;
                    if (value === "__unresolved__") {
                      update({ targetKind: "unresolved", targetId: "" });
                      return;
                    }
                    const [kind, id] = value.split(":");
                    update({
                      targetKind: (kind as typeof draft.targetKind) || "",
                      targetId: id || "",
                      targetText: "",
                    });
                  }}
                >
                  <option value="">请选择目标</option>
                  {targets.map((target) => (
                    <option
                      key={`${target.kind}:${target.id}`}
                      value={`${target.kind}:${target.id}`}
                    >
                      {TARGET_KIND_LABELS[target.kind] ?? target.kind}·
                      {target.name}
                    </option>
                  ))}
                  <option value="__unresolved__">描述其他对象…</option>
                </select>
              </label>

              {draft.targetKind === "unresolved" && (
                <label className="panel-action-field">
                  <span>描述对象</span>
                  <input
                    type="text"
                    value={draft.targetText}
                    maxLength={160}
                    placeholder="交给守秘人澄清，例如“站在门边的那位”"
                    onChange={(event) =>
                      update({ targetText: event.target.value })
                    }
                  />
                </label>
              )}

              <label className="panel-action-field">
                <span>想询问什么（可选）</span>
                <input
                  type="text"
                  value={draft.question}
                  maxLength={200}
                  placeholder="例如：你认得这份证明吗？"
                  onChange={(event) => update({ question: event.target.value })}
                />
              </label>
            </>
          ) : (
            <>
              <div className="keeper-field">
                <label className="panel-action-field">
                  <span>数量</span>
                  <input
                    type="number"
                    min={1}
                    max={Math.max(1, draft.availableQuantity)}
                    value={draft.quantity}
                    onChange={(event) =>
                      update({ quantity: Number(event.target.value) || 1 })
                    }
                  />
                </label>
                <span className="panel-action-note">
                  可用 ×{draft.availableQuantity}
                  ；提交不会立即扣减，由服务端按规则结算。
                </span>
              </div>

              <label className="panel-action-field">
                <span>常见用法</span>
                <select
                  value={draft.operation}
                  onChange={(event) =>
                    update({ operation: event.target.value })
                  }
                >
                  <option value="">其他做法（自定义）</option>
                  {draft.operations.map((operation) => (
                    <option key={operation} value={operation}>
                      {operation}
                    </option>
                  ))}
                </select>
              </label>

              <label className="panel-action-field">
                <span>目标 / 对象（可选）</span>
                <select
                  value={
                    draft.targetKind === "unresolved"
                      ? "__unresolved__"
                      : draft.targetId
                        ? `${draft.targetKind}:${draft.targetId}`
                        : ""
                  }
                  onChange={(event) => {
                    const value = event.target.value;
                    if (value === "__unresolved__") {
                      update({ targetKind: "unresolved", targetId: "" });
                      return;
                    }
                    const [kind, id] = value.split(":");
                    update({
                      targetKind: (kind as typeof draft.targetKind) || "",
                      targetId: id || "",
                      targetText: "",
                    });
                  }}
                >
                  <option value="">不指定目标</option>
                  {targets.map((target) => (
                    <option
                      key={`${target.kind}:${target.id}`}
                      value={`${target.kind}:${target.id}`}
                    >
                      {TARGET_KIND_LABELS[target.kind] ?? target.kind}·
                      {target.name}
                    </option>
                  ))}
                  <option value="__unresolved__">描述其他对象…</option>
                </select>
              </label>

              {draft.targetKind === "unresolved" && (
                <label className="panel-action-field">
                  <span>描述对象</span>
                  <input
                    type="text"
                    value={draft.targetText}
                    maxLength={160}
                    onChange={(event) =>
                      update({ targetText: event.target.value })
                    }
                  />
                </label>
              )}

              <label className="panel-action-field">
                <span>补充做法（即兴用法）</span>
                <textarea
                  value={draft.approach}
                  maxLength={200}
                  rows={2}
                  placeholder="例如：先把伤口冲洗干净再包扎"
                  onChange={(event) => update({ approach: event.target.value })}
                />
              </label>
            </>
          )}

          <div className="panel-action-preview">
            <span className="panel-action-preview-label">将提交的结构请求</span>
            <code className="panel-action-preview-text">
              {previewPayload
                ? JSON.stringify(previewPayload)
                : "（请先补全必填项）"}
            </code>
            <span className="panel-action-preview-note">
              这是结构化请求，不是行动结果；由守秘人与规则判断是否成立。
            </span>
          </div>

          {error && (
            <p className="panel-action-error" role="alert">
              {error}
            </p>
          )}
        </div>

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
            disabled={disabledReason !== null}
            title={disabledReason ?? "提交结构化请求"}
            aria-disabled={disabledReason !== null}
            onClick={submit}
          >
            {sending ? "提交中…" : "提交请求"}
          </button>
        </footer>
      </div>
    </div>
  );
}
