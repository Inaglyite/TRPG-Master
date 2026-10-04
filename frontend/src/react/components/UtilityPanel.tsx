import { useEffect, useRef, useState } from "react";

import { sendPlayerText } from "../../options";
import { interactionPath } from "../../protocol/structured";
import { useStructuredStore } from "../../state/structured-store";
import { structuredPlayerRequestReason } from "../../structured-transport";
import { ArchiveFolderPanel } from "./ArchiveFolderPanel";
import { NotebookSections } from "./NotebookSections";
import "../../styles/components/notebook-sections.css";
import { useDialogKeyboard } from "./useDialogKeyboard";
import { closeUtility, requestNotes, saveNotes } from "../../utility";
import { useAppStore } from "../../state/app-store";
import { useOnlineStore } from "../../state/online-store";
import { useDelayedClose } from "./transitions";

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

export function UtilityPanel() {
  const open = useAppStore((state) => state.utilityOpen);
  const { rendered, closing } = useDelayedClose(open);
  const text = useAppStore((state) => state.notesText);
  const dirty = useAppStore((state) => state.notesDirty);
  const saving = useAppStore((state) => state.notesSaving);
  const loading = useAppStore((state) => state.notesLoading);
  const status = useAppStore((state) => state.notesStatus);
  const statusKind = useAppStore((state) => state.notesStatusKind);
  const inputEnabled = useAppStore((state) => state.inputEnabled);
  const mode = useAppStore((state) => state.mode);
  const connection = useAppStore((state) => state.connection);
  const structuredState = useStructuredStore();
  const panel = useRef<HTMLElement>(null);
  const [actionError, setActionError] = useState("");
  const setDraft = useAppStore((state) => state.setNotesDraft);
  const online = useOnlineStore();
  const { roomConnection, roomStatus, currentActorUserId } = online;
  const userId = online.user?.id;
  const myRole = online.members.find(
    (member) => member.user_id === userId,
  )?.role;
  const onlineCanAct =
    roomConnection === "connected" &&
    roomStatus === "playing" &&
    userId != null &&
    currentActorUserId === userId &&
    (myRole === "owner" || myRole === "player");
  const quickActionsEnabled =
    interactionPath(structuredState.capabilities) === "structured"
      ? connection === "connected" &&
        !structuredPlayerRequestReason() &&
        (mode !== "online" ||
          (roomConnection === "connected" &&
            roomStatus === "playing" &&
            online.roomSnapshotReady &&
            online.authStatus === "authenticated" &&
            !!userId &&
            (myRole === "owner" || myRole === "player")))
      : inputEnabled && (mode !== "online" || onlineCanAct);
  const submitQuickAction = (action: string) => {
    const result = sendPlayerText(action);
    if (!result.ok) {
      setActionError(result.reason || "行动未能发出，请检查连接后重试。");
      return;
    }
    setActionError("");
    // Close through the same notes-saving path as the explicit close button.
    closeUtility();
  };
  useDialogKeyboard(panel, open, false, closeUtility);

  useEffect(() => {
    if (open) {
      setActionError("");
      void notesCommand("requestNotes");
    }
  }, [open]);

  if (!rendered) return <div id="utility-overlay" className="hidden" />;
  return (
    <div
      id="utility-overlay"
      className={closing ? "overlay-closing" : undefined}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget)
          void notesCommand("closeUtility");
      }}
    >
      <ArchiveFolderPanel
        id="utility-panel"
        ref={panel}
        tabIndex={-1}
        variant="wide"
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
        <div className="utility-body" data-dialog-scroll>
          <NotebookSections
            open={open}
            actions={
              <section
                className="quick-actions-section"
                aria-labelledby="quick-actions-title"
              >
                <p className="utility-help">
                  提交给主持判断，不代表行动已执行。
                </p>
                <div id="quick-actions" className="quick-actions-grid">
                  {quickActions.map((action, index) => (
                    <button
                      key={action}
                      type="button"
                      disabled={!quickActionsEnabled}
                      onClick={() => void submitQuickAction(action)}
                    >
                      {quickLabels[index]}
                    </button>
                  ))}
                </div>
                {actionError && (
                  <p role="alert" className="utility-help">
                    {actionError}
                  </p>
                )}
              </section>
            }
            notes={
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
                  aria-label="私人笔记"
                  maxLength={20000}
                  spellCheck={false}
                  value={text}
                  disabled={loading}
                  onChange={(event) => setDraft(event.target.value)}
                />
              </section>
            }
          />
        </div>
        <footer className="utility-actions">
          {statusKind === "error" && (
            <button
              type="button"
              className="btn-ghost"
              disabled={loading || saving}
              onClick={() => requestNotes()}
            >
              重新读取
            </button>
          )}
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
      </ArchiveFolderPanel>
    </div>
  );
}
