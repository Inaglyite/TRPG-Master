/** Runtime model routing settings shared by the start menu and game toolbar. */

import {
  btnModelSettings,
  btnSettings,
  judgementModelInput,
  modelIdOptions,
  modelPresetControl,
  modelSettingsCancel,
  modelSettingsClose,
  modelSettingsOverlay,
  modelSettingsSave,
  modelSettingsStatus,
  narrativeModelInput,
} from "./dom";
import { safeSend } from "./ws";

type ModelOption = { id: string; label?: string };
type ModelSettingsMessage = {
  narrative_model?: string;
  judgement_model?: string;
  available_models?: ModelOption[];
  saved?: boolean;
};

const MODEL_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,119}$/;

let narrativeModel = "";
let judgementModel = "";
let flashModel = "";
let proModel = "";
let saving = false;

function presetButtons(): HTMLButtonElement[] {
  return Array.from(
    modelPresetControl.querySelectorAll<HTMLButtonElement>("[data-model-preset]"),
  );
}

function updatePresetSelection() {
  const narrative = narrativeModelInput.value.trim();
  const judgement = judgementModelInput.value.trim();
  let active = "";
  if (proModel && narrative === proModel && judgement === proModel) active = "pro";
  if (flashModel && narrative === flashModel && judgement === proModel) active = "balanced";
  if (flashModel && narrative === flashModel && judgement === flashModel) active = "flash";
  presetButtons().forEach((button) => {
    const selected = button.dataset.modelPreset === active;
    button.classList.toggle("selected", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
}

function setDraft(narrative: string, judgement: string) {
  narrativeModelInput.value = narrative;
  judgementModelInput.value = judgement;
  updatePresetSelection();
}

function setStatus(message: string, state = "") {
  modelSettingsStatus.textContent = message;
  if (state) modelSettingsStatus.dataset.state = state;
  else delete modelSettingsStatus.dataset.state;
}

function openSettings() {
  setDraft(narrativeModel, judgementModel);
  const hasSettings = Boolean(narrativeModel && judgementModel);
  setStatus(hasSettings ? "" : "正在读取模型配置…", hasSettings ? "" : "working");
  modelSettingsOverlay.classList.remove("hidden");
  if (!hasSettings) safeSend(JSON.stringify({ type: "model_settings_get" }));
  requestAnimationFrame(() => narrativeModelInput.focus());
}

function closeSettings() {
  if (saving) return;
  modelSettingsOverlay.classList.add("hidden");
  setStatus("");
}

function applyPreset(preset: string) {
  if (!flashModel || !proModel) return;
  if (preset === "pro") setDraft(proModel, proModel);
  if (preset === "balanced") setDraft(flashModel, proModel);
  if (preset === "flash") setDraft(flashModel, flashModel);
}

function saveSettings() {
  const narrative = narrativeModelInput.value.trim();
  const judgement = judgementModelInput.value.trim();
  if (!MODEL_ID_PATTERN.test(narrative) || !MODEL_ID_PATTERN.test(judgement)) {
    setStatus("模型 ID 格式无效", "error");
    return;
  }
  saving = true;
  modelSettingsSave.disabled = true;
  setStatus("正在应用…", "working");
  safeSend(JSON.stringify({
    type: "model_settings_update",
    narrative_model: narrative,
    judgement_model: judgement,
  }));
}

export function onModelSettings(data: ModelSettingsMessage) {
  const options = Array.isArray(data.available_models) ? data.available_models : [];
  flashModel = options.find((option) => option.label === "Flash")?.id || flashModel;
  proModel = options.find((option) => option.label === "Pro")?.id || proModel;
  narrativeModel = data.narrative_model || narrativeModel;
  judgementModel = data.judgement_model || judgementModel;

  modelIdOptions.replaceChildren(...options.map((option) => {
    const element = document.createElement("option");
    element.value = option.id;
    element.label = option.label || option.id;
    return element;
  }));
  setDraft(narrativeModel, judgementModel);

  if (data.saved) {
    saving = false;
    modelSettingsSave.disabled = false;
    setStatus("已应用，将从下一回合生效", "success");
  }
}

export function onModelSettingsError(message: string) {
  saving = false;
  modelSettingsSave.disabled = false;
  setStatus(message || "模型设置保存失败", "error");
}

btnSettings.onclick = openSettings;
btnModelSettings.onclick = openSettings;
modelSettingsClose.onclick = closeSettings;
modelSettingsCancel.onclick = closeSettings;
modelSettingsSave.onclick = saveSettings;
modelPresetControl.addEventListener("click", (event) => {
  const button = (event.target as HTMLElement).closest<HTMLButtonElement>("[data-model-preset]");
  if (button) applyPreset(button.dataset.modelPreset || "");
});
narrativeModelInput.addEventListener("input", updatePresetSelection);
judgementModelInput.addEventListener("input", updatePresetSelection);
modelSettingsOverlay.addEventListener("click", (event) => {
  if (event.target === modelSettingsOverlay) closeSettings();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !modelSettingsOverlay.classList.contains("hidden")) {
    closeSettings();
  }
});
