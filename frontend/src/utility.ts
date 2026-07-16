/** Player-owned notes commands backed by the React application store. */

import { useAppStore } from "./state/app-store";
import { safeSend } from "./ws";

export function requestNotes() {
  useAppStore.getState().setNotesProgress({
    notesLoading: true,
    notesSaving: false,
    notesStatus: "正在读取…",
    notesStatusKind: "working",
  });
  safeSend(JSON.stringify({ type: "player_notes_get" }));
}

export function saveNotes() {
  const state = useAppStore.getState();
  if (state.notesSaving || state.notesLoading) return;
  state.setNotesProgress({
    notesSaving: true,
    notesDirty: false,
    notesStatus: "正在保存…",
    notesStatusKind: "working",
  });
  safeSend(
    JSON.stringify({
      type: "player_notes_update",
      revision: state.notesRevision,
      text: state.notesText,
    }),
  );
}

export function closeUtility() {
  const state = useAppStore.getState();
  if (state.notesDirty && !state.notesSaving) saveNotes();
  state.setUtilityOpen(false);
}

export function onPlayerNotes(data: any) {
  useAppStore.getState().applyNotes(data);
}

export function onPlayerNotesConflict(data: any) {
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
  useAppStore.getState().setNotesProgress({
    notesSaving: false,
    notesLoading: false,
    notesDirty: true,
    notesStatus: message || "笔记保存失败",
    notesStatusKind: "error",
  });
}

export function onNotesWorldChanged() {
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
