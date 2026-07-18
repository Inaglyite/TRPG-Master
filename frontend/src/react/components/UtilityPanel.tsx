import { useEffect } from "react";

import { sendAction } from "../../options";
import { closeUtility, requestNotes, saveNotes } from "../../utility";
import { useAppStore } from "../../state/app-store";

const quickActions = [
  "观察当前环境",
  "检查随身物品",
  "梳理目前已知的线索",
  "与当前在场的人物交谈",
];
const quickLabels = ["观察环境", "检查物品", "梳理线索", "与人物交谈"];

function notesCommand(command: "requestNotes" | "saveNotes" | "closeUtility") {
  if (command === "requestNotes") requestNotes();
  else if (command === "saveNotes") saveNotes();
  else closeUtility();
}

function submitQuickAction(action: string) {
  useAppStore.getState().setUtilityOpen(false);
  sendAction(action);
}

export function UtilityPanel() {
  const open = useAppStore((state) => state.utilityOpen);
  const text = useAppStore((state) => state.notesText);
  const dirty = useAppStore((state) => state.notesDirty);
  const saving = useAppStore((state) => state.notesSaving);
  const loading = useAppStore((state) => state.notesLoading);
  const status = useAppStore((state) => state.notesStatus);
  const statusKind = useAppStore((state) => state.notesStatusKind);
  const inputEnabled = useAppStore((state) => state.inputEnabled);
  const setDraft = useAppStore((state) => state.setNotesDraft);

  useEffect(() => {
    if (open) void notesCommand("requestNotes");
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") void notesCommand("closeUtility");
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open]);

  if (!open) return <div id="utility-overlay" className="hidden" />;
  return (
    <div
      id="utility-overlay"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget)
          void notesCommand("closeUtility");
      }}
    >
      <div
        id="utility-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="utility-title"
      >
        <header className="utility-header">
          <div>
            <div className="panel-eyebrow">INVESTIGATOR / FIELD NOTES</div>
            <h2 id="utility-title">调查笔记</h2>
          </div>
          <button
            id="utility-close"
            type="button"
            title="关闭"
            aria-label="关闭调查笔记"
            onClick={() => void notesCommand("closeUtility")}
          >
            ✕
          </button>
        </header>
        <div className="utility-body">
          <section
            className="quick-actions-section"
            aria-labelledby="quick-actions-title"
          >
            <h3 id="quick-actions-title">快捷行动</h3>
            <div id="quick-actions" className="quick-actions-grid">
              {quickActions.map((action, index) => (
                <button
                  key={action}
                  type="button"
                  disabled={!inputEnabled}
                  onClick={() => void submitQuickAction(action)}
                >
                  {quickLabels[index]}
                </button>
              ))}
            </div>
          </section>
          <section
            className="player-notes-section"
            aria-labelledby="player-notes-title"
          >
            <div className="player-notes-heading">
              <h3 id="player-notes-title">私人笔记</h3>
              <span
                id="player-notes-status"
                data-state={statusKind || undefined}
                aria-live="polite"
              >
                {status}
              </span>
            </div>
            <textarea
              id="player-notes-input"
              maxLength={20000}
              spellCheck={false}
              value={text}
              disabled={loading}
              onChange={(event) => setDraft(event.target.value)}
            />
          </section>
        </div>
        <footer className="utility-actions">
          <button
            id="player-notes-cancel"
            type="button"
            onClick={() => void notesCommand("closeUtility")}
          >
            关闭
          </button>
          <button
            id="player-notes-save"
            type="button"
            disabled={loading || saving || !dirty}
            onClick={() => void notesCommand("saveNotes")}
          >
            {saving ? "保存中…" : "保存笔记"}
          </button>
        </footer>
      </div>
    </div>
  );
}
