/** Player-owned notes and visible, ordinary-action shortcuts. */

import {
  btnNotes,
  playerNotesCancel,
  playerNotesInput,
  playerNotesSave,
  playerNotesStatus,
  quickActions,
  userInput,
  utilityClose,
  utilityOverlay,
} from "./dom";
import { sendAction } from "./options";
import { getGameStarted } from "./start";
import { safeSend } from "./ws";

let notesRevision = 0;
let notesDirty = false;
let notesSaving = false;

function setNotesStatus(message: string, state = "") {
  playerNotesStatus.textContent = message;
  if (state) playerNotesStatus.dataset.state = state;
  else delete playerNotesStatus.dataset.state;
}

function setQuickActionsEnabled() {
  const enabled = getGameStarted() && !userInput.disabled;
  quickActions.querySelectorAll<HTMLButtonElement>("button").forEach((button) => {
    button.disabled = !enabled;
  });
}

function requestNotes() {
  playerNotesInput.disabled = true;
  playerNotesSave.disabled = true;
  setNotesStatus("正在读取…", "working");
  safeSend(JSON.stringify({ type: "player_notes_get" }));
}

function openUtility() {
  notesDirty = false;
  notesSaving = false;
  utilityOverlay.classList.remove("hidden");
  setQuickActionsEnabled();
  requestNotes();
}

function saveNotes() {
  if (notesSaving || playerNotesInput.disabled) return;
  notesSaving = true;
  notesDirty = false;
  playerNotesSave.disabled = true;
  setNotesStatus("正在保存…", "working");
  safeSend(JSON.stringify({
    type: "player_notes_update",
    revision: notesRevision,
    text: playerNotesInput.value,
  }));
}

function closeUtility() {
  if (notesDirty && !notesSaving) saveNotes();
  utilityOverlay.classList.add("hidden");
}

export function onPlayerNotes(data: any) {
  const wasSaved = Boolean(data.saved);
  notesRevision = Number(data.revision || 0);
  if (!notesDirty) {
    playerNotesInput.value = String(data.text || "");
  }
  playerNotesInput.disabled = false;
  playerNotesSave.disabled = false;
  notesSaving = false;
  setNotesStatus(
    notesDirty ? "未保存" : (wasSaved ? "已保存" : ""),
    notesDirty ? "" : (wasSaved ? "success" : ""),
  );
}

export function onPlayerNotesConflict(data: any) {
  notesRevision = Number(data.revision || notesRevision);
  notesSaving = false;
  notesDirty = true;
  playerNotesInput.disabled = false;
  playerNotesSave.disabled = false;
  setNotesStatus("笔记已在其他窗口更新，请再次保存以覆盖", "error");
}

export function onPlayerNotesError(message: string) {
  notesSaving = false;
  notesDirty = true;
  playerNotesInput.disabled = false;
  playerNotesSave.disabled = false;
  setNotesStatus(message || "笔记保存失败", "error");
}

export function onNotesWorldChanged() {
  notesRevision = 0;
  notesDirty = false;
  notesSaving = false;
  if (!utilityOverlay.classList.contains("hidden")) requestNotes();
}

btnNotes.onclick = openUtility;
utilityClose.onclick = closeUtility;
playerNotesCancel.onclick = closeUtility;
playerNotesSave.onclick = saveNotes;
playerNotesInput.addEventListener("input", () => {
  notesDirty = true;
  setNotesStatus("未保存");
});
quickActions.addEventListener("click", (event) => {
  const button = (event.target as HTMLElement).closest<HTMLButtonElement>("[data-quick-action]");
  if (!button || button.disabled || userInput.disabled || !getGameStarted()) {
    setQuickActionsEnabled();
    return;
  }
  const action = button.dataset.quickAction || "";
  if (!action) return;
  utilityOverlay.classList.add("hidden");
  sendAction(action);
});
utilityOverlay.addEventListener("click", (event) => {
  if (event.target === utilityOverlay) closeUtility();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !utilityOverlay.classList.contains("hidden")) {
    closeUtility();
  }
});
