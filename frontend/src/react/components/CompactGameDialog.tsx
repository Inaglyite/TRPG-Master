import { useEffect, useRef, type KeyboardEvent, type ReactNode } from "react";

import { ArchiveFolderPanel } from "./ArchiveFolderPanel";
import { focusableControls, trapDialogTab } from "./dialogFocus";

/** Small request dialogs: skin is decorative, body scrolls, actions stay visible. */
export function CompactGameDialog({
  id,
  title,
  closeLabel,
  onClose,
  children,
  footer,
  context,
}: {
  id: string;
  title: string;
  closeLabel: string;
  onClose: () => void;
  children: ReactNode;
  footer: ReactNode;
  context?: ReactNode;
}) {
  const root = useRef<HTMLElement>(null);
  useEffect(() => {
    const previous =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    const panel = root.current;
    if (panel) {
      const controls = focusableControls(panel);
      const input = controls.find((control) =>
        control.matches("input,select,textarea"),
      );
      (input ?? controls[0] ?? panel).focus();
    }
    return () => {
      if (previous?.isConnected) previous.focus();
    };
  }, []);

  const keyboard = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      onClose();
    } else if (event.key === "Tab" && root.current) {
      trapDialogTab(event, root.current);
    }
  };

  return (
    <div
      id={`${id}-overlay`}
      className="structured-overlay-inline"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <ArchiveFolderPanel
        ref={root}
        variant="wide"
        id={id}
        className="compact-game-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${id}-title`}
        tabIndex={-1}
        onKeyDown={keyboard}
      >
        <header className="panel-action-header">
          <h3 id={`${id}-title`}>{title}</h3>
          <button
            type="button"
            className="btn-ghost panel-action-close"
            aria-label={closeLabel}
            onClick={onClose}
          >
            ✕
          </button>
        </header>
        {context}
        <div className="panel-action-body">{children}</div>
        <footer className="panel-action-footer">{footer}</footer>
      </ArchiveFolderPanel>
    </div>
  );
}
