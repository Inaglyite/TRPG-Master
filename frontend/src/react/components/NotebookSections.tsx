import { useEffect, useId, useState, type ReactNode } from "react";

/** Reading/writing is primary. Expanding shortcuts never submits an action. */
export function NotebookSections({
  open,
  notes,
  actions,
}: {
  open: boolean;
  notes: ReactNode;
  actions: ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);
  const actionsId = useId();

  useEffect(() => {
    if (open) setExpanded(false);
  }, [open]);

  return (
    <div className="notebook-sections">
      {notes}
      <button
        type="button"
        id="quick-actions-title"
        className="btn-ghost notebook-shortcuts-toggle"
        aria-expanded={expanded}
        aria-controls={actionsId}
        onClick={() => setExpanded((value) => !value)}
      >
        <span>快捷行动</span>
        <span aria-hidden="true">{expanded ? "−" : "+"}</span>
      </button>
      <div id={actionsId} className="notebook-shortcuts" hidden={!expanded}>
        {actions}
      </div>
    </div>
  );
}
