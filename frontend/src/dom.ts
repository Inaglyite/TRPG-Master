/**
 * dom.ts — DOM 元素引用
 *
 * 集中管理所有 document.getElementById 的缓存引用，其他模块通过 import 使用。
 */

export const messagesEl = document.getElementById("messages")!;
export const optionsBar = document.getElementById("options-bar")!;
export const userInput = document.getElementById("user-input") as HTMLInputElement;
export const btnSend = document.getElementById("btn-send") as HTMLButtonElement;
export const btnSave = document.getElementById("btn-save")!;
export const btnLoad = document.getElementById("btn-load")!;
export const btnNew = document.getElementById("btn-new")!;
export const btnPanel = document.getElementById("btn-panel")!;
export const charPanel = document.getElementById("char-panel")!;
export const connStatus = document.getElementById("conn-status")!;

export function setConn(state: "connecting" | "connected" | "disconnected") {
  connStatus.className = state;
  connStatus.title =
    state === "connected"
      ? "已连接到守秘人"
      : state === "connecting"
        ? "连接中…"
        : "连接已断开，正在重试";
}

export const savePanelOverlay = document.getElementById("save-panel-overlay")!;
export const savePanelClose = document.getElementById("save-panel-close")!;
export const savePanelNew = document.getElementById("save-panel-new")!;
export const savePanelList = document.getElementById("save-panel-list")!;

export const modalOverlay = document.getElementById("modal-overlay")!;
export const modalText = document.getElementById("modal-text")!;
export const modalYes = document.getElementById("modal-yes")!;
export const modalNo = document.getElementById("modal-no")!;
export const startOverlay = document.getElementById("start-overlay")!;
export const btnStart = document.getElementById("btn-start") as HTMLButtonElement;
export const btnContinue = document.getElementById("btn-continue") as HTMLButtonElement;
export const characterChoiceList = document.getElementById("character-choice-list")!;
export const characterSelectedSummary = document.getElementById("character-selected-summary")!;
