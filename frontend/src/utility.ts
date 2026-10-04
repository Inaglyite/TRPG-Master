/** Player-owned notes commands backed by the React application store. */

import { useAppStore } from "./state/app-store";
import { sendImmediately } from "./ws";
import { useOnlineStore } from "./state/online-store";
import { NotesRequests, type NotesScope } from "./notes-requests";

function notesScope(): NotesScope {
  const app = useAppStore.getState();
  const online = useOnlineStore.getState();
  return {
    worldId:
      (app.mode === "online" ? online.activeWorldId : app.activeWorldId) || "",
    ownerKey:
      app.mode === "online"
        ? `${online.authOrigin}/${online.user?.id}`
        : "local",
  };
}

const requests = new NotesRequests((operation) => {
  const scope = notesScope();
  if (
    scope.worldId !== operation.scope.worldId ||
    scope.ownerKey !== operation.scope.ownerKey
  )
    return;
  useAppStore.getState().setNotesProgress({
    notesLoading: false,
    notesSaving: false,
    notesStatus:
      "尚未收到笔记确认，草稿保留在此窗口。请重新读取核对保存结果，或连接后重试。",
    notesStatusKind: "error",
  });
});

function requestFields(operation: NonNullable<NotesRequests["current"]>) {
  return {
    request_id: operation.requestId,
    ...(operation.scope.worldId ? { world_id: operation.scope.worldId } : {}),
  };
}

function notesConnected() {
  const state = useAppStore.getState();
  if (state.connection !== "connected") return false;
  if (state.mode !== "online") return true;
  const online = useOnlineStore.getState();
  return (
    online.authStatus === "authenticated" &&
    online.roomConnection === "connected" &&
    online.roomSnapshotReady
  );
}

function notesNotSent() {
  requests.cancel();
  useAppStore.getState().setNotesProgress({
    notesLoading: false,
    notesSaving: false,
    notesStatus: "连接尚未就绪，笔记未发送。草稿保留在此窗口，请连接后重试。",
    notesStatusKind: "error",
  });
}

export function requestNotes() {
  if (
    requests.current &&
    requests.current.scope.worldId === notesScope().worldId &&
    requests.current.scope.ownerKey === notesScope().ownerKey
  )
    return;
  if (!notesConnected()) {
    notesNotSent();
    return;
  }
  useAppStore.getState().setNotesProgress({
    notesLoading: true,
    notesSaving: false,
    notesStatus: "正在读取…",
    notesStatusKind: "working",
  });
  const operation = requests.begin(notesScope(), "read");
  if (
    !sendImmediately(
      JSON.stringify({ type: "player_notes_get", ...requestFields(operation) }),
    )
  )
    notesNotSent();
}

export function saveNotes() {
  const state = useAppStore.getState();
  if (state.notesSaving || state.notesLoading) return;
  if (!notesConnected()) {
    notesNotSent();
    return;
  }
  const operation = requests.begin(notesScope(), "save", state.notesText);
  if (
    !sendImmediately(
      JSON.stringify({
        type: "player_notes_update",
        ...requestFields(operation),
        revision: state.notesRevision,
        text: state.notesText,
      }),
    )
  ) {
    notesNotSent();
    return;
  }
  state.setNotesProgress({
    notesSaving: true,
    notesStatus: "正在保存…",
    notesStatusKind: "working",
  });
}

export function closeUtility() {
  const state = useAppStore.getState();
  if (state.notesDirty && !state.notesSaving) saveNotes();
  state.setUtilityOpen(false);
}

export function onPlayerNotes(data: any) {
  const scope = notesScope();
  if (data.world_id !== undefined && data.world_id !== scope.worldId) return;
  const state = useAppStore.getState();
  if (!Number.isInteger(data.revision) || data.revision < state.notesRevision)
    return;
  if (!requests.current) {
    // A late tagged reply cannot complete an expired/replaced operation.
    if (data.request_id !== undefined) return;
    state.applyNotes(data);
    return;
  }
  if (!requests.matches(scope, data)) return;
  const operation = requests.finish()!;
  const serverText = String(data.text || "");
  const draftConfirmed =
    state.notesText.replace(/\r\n?/g, "\n") === serverText &&
    (operation.kind === "read" ||
      operation.submittedText?.replace(/\r\n?/g, "\n") === serverText);
  const dirty = state.notesDirty && !draftConfirmed;
  useAppStore.setState({
    notesText: dirty ? state.notesText : serverText,
    notesRevision: data.revision,
    notesSaving: false,
    notesLoading: false,
    notesDirty: dirty,
    notesStatus: dirty ? "未保存：保留此窗口的草稿" : "已保存",
    notesStatusKind: dirty ? "" : "success",
  });
}

export function onPlayerNotesConflict(data: any) {
  if (!requests.matches(notesScope(), data, true)) return;
  requests.finish();
  useAppStore.setState((state) => ({
    notesRevision: Number(data.revision || state.notesRevision),
    notesSaving: false,
    notesLoading: false,
    notesDirty: true,
    notesStatus: "笔记已在其他窗口更新，请再次保存以覆盖",
    notesStatusKind: "error",
  }));
}

export function onPlayerNotesError(message: string) {
  onNotesFailure({ message });
}

export function onNotesFailure(data: any) {
  if (!requests.matches(notesScope(), data, true)) return;
  requests.finish();
  useAppStore.getState().setNotesProgress({
    notesSaving: false,
    notesLoading: false,
    notesDirty: true,
    notesStatus: data.message || "笔记访问失败，草稿保留",
    notesStatusKind: "error",
  });
}

export function onNotesWorldChanged() {
  requests.cancel();
  useAppStore.setState({
    notesRevision: 0,
    notesDirty: false,
    notesSaving: false,
    notesLoading: false,
    notesText: "",
    notesStatus: "",
    notesStatusKind: "",
  });
  if (useAppStore.getState().utilityOpen) requestNotes();
}
