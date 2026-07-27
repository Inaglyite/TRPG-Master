/** React state adapters for character, handout, save and world panels. */

import { enableInput } from "./options";
import { addMsg, removeLoading } from "./renderer";
import {
  useAppStore,
  type ClueItem,
  type Handout,
  type SaveEntry,
  type WorldEntry,
} from "./state/app-store";
import { isRoomOwner } from "./state/online-store";
import { escapeHtml } from "./text";
import { getGameStarted } from "./start";
import { safeSend } from "./ws";

const clueCategories = ["investigation", "event", "task", "npc"] as const;
let knownClueKeys: Set<string> | null = null;
let handoutCounter = 0;
let quickSavePending = false;
let quickSaveTimeout: number | undefined;
let quickSaveFeedbackTimeout: number | undefined;
let clueToastTimeout: number | undefined;

/**
 * 多人房间的房主专属操作（存档管理、读档、结案等）前端门禁；
 * 服务端按 Session 再次校验，此处只是提前阻断并给出可读提示。
 */
function roomOwnerOpsAllowed(): boolean {
  if (useAppStore.getState().mode !== "online") return true;
  return isRoomOwner();
}

function denyRoomOwnerOp(): void {
  addMsg("system", "多人房间中，存档与结案操作仅房主可用。");
}

function parseJson<T>(raw: string, fallback: T): T {
  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

export function updateCharPanel(raw: string) {
  const data = parseJson<any>(raw, null);
  if (data) useAppStore.getState().setCharacter(data);
}

export function updateCluePanel(raw: string) {
  const clues = parseJson<Record<string, ClueItem[]>>(raw, {});
  const current = collectClueKeys(clues);
  if (knownClueKeys) {
    let added = 0;
    current.forEach((key) => {
      if (!knownClueKeys?.has(key)) added++;
    });
    if (added) {
      window.clearTimeout(clueToastTimeout);
      useAppStore.setState({
        clueToast: added > 1 ? `${added} 条线索已加入` : "线索已加入",
      });
      clueToastTimeout = window.setTimeout(
        () => useAppStore.setState({ clueToast: null }),
        2700,
      );
    }
  }
  knownClueKeys = current;
  useAppStore.getState().setClues(clues);
}

function collectClueKeys(clues: Record<string, ClueItem[]>) {
  const keys = new Set<string>();
  if (!clues || typeof clues !== "object" || Array.isArray(clues)) return keys;
  clueCategories.forEach((category) => {
    (clues[category] || []).forEach((item, index) => {
      if (item.type === "profile") return;
      keys.add(`${category}:${item.id || item.text || index}`);
    });
  });
  return keys;
}

export function loadState() {
  safeSend(JSON.stringify({ type: "state" }));
}

export function showHandout(data: Omit<Handout, "id">) {
  const source = data.asset_data_uri || data.asset_url;
  if (!source) return;
  const id = `${data.entity_type || "asset"}:${data.entity_id || data.file}:${++handoutCounter}`;
  useAppStore.getState().addHandout({ ...data, id });
}

export function clearTransientHandouts() {
  window.clearTimeout(clueToastTimeout);
  knownClueKeys = null;
  useAppStore.setState({ handouts: [], clueToast: null });
}

export function showEnding(data: any) {
  removeLoading();
  useAppStore.getState().setChoices([]);
  useAppStore.getState().setEnding(data);
}

export function confirmEnding(data: any) {
  if (!roomOwnerOpsAllowed()) {
    denyRoomOwnerOp();
    return;
  }
  const emoji =
    data.ending_type === "good"
      ? "🏆"
      : data.ending_type === "bad"
        ? "💀"
        : "🌫";
  safeSend(
    JSON.stringify({
      type: "settle_case",
      ending_type: data.ending_type,
      title: data.title,
      summary: data.summary,
    }),
  );
  addMsg(
    "ending",
    [
      '<div class="ending-box">',
      `<div class="ending-emoji">${emoji}</div>`,
      `<div class="ending-title">${escapeHtml(data.title)}</div>`,
      `<div class="ending-summary">${escapeHtml(data.summary)}</div>`,
      "</div>",
    ].join(""),
  );
  useAppStore.getState().setEnding(null);
  enableInput(false);
}

export function openSavePanel(mode: "load" | "manage" = "manage") {
  useAppStore.getState().setSavePanel(true, mode);
  safeSend(JSON.stringify({ type: "save_list" }));
  safeSend(JSON.stringify({ type: "world_list" }));
}

export function closeSavePanel() {
  useAppStore.getState().setSavePanel(false);
}

export function renderSavePanel(saves: SaveEntry[]) {
  useAppStore.getState().setSaveData(Array.isArray(saves) ? saves : []);
}

export function renderWorldPanel(worlds: WorldEntry[], activeWorldId: string) {
  useAppStore
    .getState()
    .setWorldData(Array.isArray(worlds) ? worlds : [], activeWorldId || "");
}

export function loadSave(slotId: string) {
  if (!roomOwnerOpsAllowed()) {
    denyRoomOwnerOp();
    return;
  }
  closeSavePanel();
  addMsg("system", "正在读档…");
  safeSend(JSON.stringify({ type: "save_load", slot_id: slotId }));
}

export function deleteSave(slotId: string) {
  if (!roomOwnerOpsAllowed()) {
    denyRoomOwnerOp();
    return;
  }
  safeSend(JSON.stringify({ type: "save_delete", slot_id: slotId }));
}

export function renameSave(slotId: string, label: string) {
  if (!roomOwnerOpsAllowed()) {
    denyRoomOwnerOp();
    return;
  }
  safeSend(
    JSON.stringify({
      type: "save_rename",
      slot_id: slotId,
      label: label.trim(),
    }),
  );
}

export function createSave() {
  if (!roomOwnerOpsAllowed()) {
    denyRoomOwnerOp();
    return;
  }
  safeSend(JSON.stringify({ type: "save_create" }));
  addMsg("system", "正在保存…");
}

export function switchWorld(worldId: string) {
  safeSend(JSON.stringify({ type: "world_switch", world_id: worldId }));
}

export function quickSave() {
  if (!roomOwnerOpsAllowed()) {
    denyRoomOwnerOp();
    return;
  }
  if (quickSavePending || !getGameStarted()) return;
  window.clearTimeout(quickSaveFeedbackTimeout);
  quickSavePending = true;
  useAppStore.setState({ quickSaveState: "saving" });
  safeSend(JSON.stringify({ type: "save", manual: false }));
  quickSaveTimeout = window.setTimeout(() => finishQuickSave(false), 8000);
}

export function finishQuickSave(ok: boolean) {
  if (!quickSavePending) return;
  quickSavePending = false;
  window.clearTimeout(quickSaveTimeout);
  useAppStore.setState({ quickSaveState: ok ? "success" : "failed" });
  quickSaveFeedbackTimeout = window.setTimeout(
    () => useAppStore.setState({ quickSaveState: "idle" }),
    1600,
  );
}
