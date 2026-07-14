/**
 * dom.ts — DOM 元素引用
 *
 * 集中管理所有 document.getElementById 的缓存引用，其他模块通过 import 使用。
 */

export const messagesEl = document.getElementById("messages")!;
export const optionsBar = document.getElementById("options-bar")!;
export const userInput = document.getElementById("user-input") as HTMLInputElement;
export const btnSend = document.getElementById("btn-send") as HTMLButtonElement;
export const btnSave = document.getElementById("btn-save") as HTMLButtonElement;
export const btnLoad = document.getElementById("btn-load") as HTMLButtonElement;
export const btnNew = document.getElementById("btn-new")!;
export const btnPanel = document.getElementById("btn-panel")!;
export const btnModelSettings = document.getElementById("btn-model-settings") as HTMLButtonElement;
export const charPanel = document.getElementById("char-panel")!;
export const connStatus = document.getElementById("conn-status")!;

let connState: "connecting" | "connected" | "disconnected" = "connecting";

export function getConnState() {
  return connState;
}

export function setConn(state: "connecting" | "connected" | "disconnected") {
  connState = state;
  const el = document.getElementById("conn-status") || connStatus;
  if (!el) return;
  el.className = state;
  el.title =
    state === "connected"
      ? "已连接到守秘人"
      : state === "connecting"
        ? "连接中…"
        : "连接已断开，正在重试";
}

export const savePanelOverlay = document.getElementById("save-panel-overlay")!;
export const savePanelTitle = document.getElementById("save-panel-title")!;
export const savePanelClose = document.getElementById("save-panel-close")!;
export const savePanelNew = document.getElementById("save-panel-new")!;
export const savePanelList = document.getElementById("save-panel-list")!;

export const modalOverlay = document.getElementById("modal-overlay")!;
export const modalText = document.getElementById("modal-text")!;
export const modalActions = document.getElementById("modal-actions")!;
export const modalYes = document.getElementById("modal-yes")!;
export const modalNo = document.getElementById("modal-no")!;
export const startOverlay = document.getElementById("start-overlay")!;
export const startMenuView = document.getElementById("start-menu-view")!;
export const characterSelectView = document.getElementById("character-select-view")!;
export const btnStart = document.getElementById("btn-start") as HTMLButtonElement;
export const btnContinue = document.getElementById("btn-continue") as HTMLButtonElement;
export const btnExit = document.getElementById("btn-exit") as HTMLButtonElement;
export const btnSettings = document.getElementById("btn-settings") as HTMLButtonElement;
export const btnCharacterBack = document.getElementById("btn-character-back") as HTMLButtonElement;
export const btnCharacterConfirm = document.getElementById("btn-character-confirm") as HTMLButtonElement;
export const characterChoiceList = document.getElementById("character-choice-list")!;
export const characterSelectedSummary = document.getElementById("character-selected-summary")!;
export const characterDetail = document.getElementById("character-detail")!;
export const characterModuleName = document.getElementById("character-module-name")!;
export const btnImportModule = document.getElementById("btn-import-module") as HTMLButtonElement;
export const moduleFileInput = document.getElementById("module-file-input") as HTMLInputElement;
export const moduleImportStatus = document.getElementById("module-import-status")!;
export const moduleImportOverlay = document.getElementById("module-import-overlay")!;
export const moduleImportClose = document.getElementById("module-import-close") as HTMLButtonElement;
export const moduleImportCancel = document.getElementById("module-import-cancel") as HTMLButtonElement;
export const moduleImportConfirm = document.getElementById("module-import-confirm") as HTMLButtonElement;
export const moduleImportName = document.getElementById("module-import-name")!;
export const moduleImportMeta = document.getElementById("module-import-meta")!;
export const moduleImportDescription = document.getElementById("module-import-description")!;
export const moduleImportWarnings = document.getElementById("module-import-warnings")!;
export const moduleImportWarningList = document.getElementById("module-import-warning-list")!;
export const moduleImportError = document.getElementById("module-import-error")!;
export const modelSettingsOverlay = document.getElementById("model-settings-overlay")!;
export const modelSettingsClose = document.getElementById("model-settings-close") as HTMLButtonElement;
export const modelSettingsCancel = document.getElementById("model-settings-cancel") as HTMLButtonElement;
export const modelSettingsSave = document.getElementById("model-settings-save") as HTMLButtonElement;
export const narrativeModelInput = document.getElementById("narrative-model-input") as HTMLInputElement;
export const judgementModelInput = document.getElementById("judgement-model-input") as HTMLInputElement;
export const modelIdOptions = document.getElementById("model-id-options") as HTMLDataListElement;
export const modelPresetControl = document.getElementById("model-preset-control")!;
export const modelSettingsStatus = document.getElementById("model-settings-status")!;
