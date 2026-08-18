/**
 * 模组选择下拉：自绘 listbox 替代原生 <select>。原生弹层由 OS 渲染，
 * 无法主题化（选中项是系统蓝）也无法加开关动画。动效遵循
 * ui-animation skill「Menu dropdown」：开 180ms / 关 140ms、
 * 只动 transform+opacity、origin 在触发器、reduced-motion 直接落定。
 */

import { useEffect, useRef, useState } from "react";

import { useDelayedClose } from "./transitions";

export interface ModuleSelectOption {
  id: string;
  title: string;
}

export function ModuleSelect({
  options,
  value,
  disabled,
  labelledBy,
  listLabel = "当前模组",
  onSelect,
}: {
  options: ModuleSelectOption[];
  value: string;
  disabled?: boolean;
  /** 外部可见标签的 id；提供后触发器的可访问名取自该标签 */
  labelledBy?: string;
  /** 弹出列表的可访问名（不同页面的语义不同，如“选择模组”） */
  listLabel?: string;
  onSelect: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const panel = useDelayedClose(open, 140);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const active = options.find((option) => option.id === value) || options[0];

  // 打开后焦点落到当前选中项，键盘可直接上下移动。
  useEffect(() => {
    if (!panel.rendered || panel.closing) return;
    const root = rootRef.current;
    if (!root) return;
    const selected =
      root.querySelector<HTMLElement>(
        '.module-select-option[aria-selected="true"]',
      ) || root.querySelector<HTMLElement>(".module-select-option");
    selected?.focus();
  }, [panel.rendered, panel.closing]);

  // 点击组件外任意处关闭。
  useEffect(() => {
    if (!open) return;
    const listener = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", listener);
    return () => document.removeEventListener("pointerdown", listener);
  }, [open]);

  const choose = (id: string) => {
    setOpen(false);
    triggerRef.current?.focus();
    if (id !== active?.id) onSelect(id);
  };

  const onTriggerKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      setOpen(true);
    }
  };

  const onOptionKeyDown = (event: React.KeyboardEvent, id: string) => {
    const items = Array.from(
      rootRef.current?.querySelectorAll<HTMLElement>(".module-select-option") ||
        [],
    );
    const index = items.indexOf(event.currentTarget as HTMLElement);
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const delta = event.key === "ArrowDown" ? 1 : -1;
      items[(index + delta + items.length) % items.length]?.focus();
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      choose(id);
    } else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      setOpen(false);
      triggerRef.current?.focus();
    } else if (event.key === "Tab") {
      setOpen(false);
    }
  };

  return (
    <div className={`module-select${open ? " open" : ""}`} ref={rootRef}>
      <button
        type="button"
        id="module-select"
        className="module-select-trigger"
        ref={triggerRef}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-labelledby={labelledBy}
        disabled={disabled}
        onClick={() => setOpen((current) => !current)}
        onKeyDown={onTriggerKeyDown}
      >
        <span className="module-select-value">{active?.title || "…"}</span>
        <span className="module-select-chevron" aria-hidden="true" />
      </button>
      {panel.rendered && (
        <div
          className={`module-select-dropdown${panel.closing ? " closing" : ""}`}
          role="listbox"
          aria-label={listLabel}
        >
          {options.map((option) => (
            <button
              type="button"
              role="option"
              aria-selected={option.id === active?.id}
              className={`module-select-option${option.id === active?.id ? " selected" : ""}`}
              key={option.id}
              onClick={() => choose(option.id)}
              onKeyDown={(event) => onOptionKeyDown(event, option.id)}
            >
              {option.title}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
