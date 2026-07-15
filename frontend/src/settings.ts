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
  turnDiagnosticsContent,
  turnDiagnosticsRefresh,
  turnDiagnosticsStatus,
} from "./dom";
import { safeSend } from "./ws";

type ModelOption = { id: string; label?: string };
type ModelSettingsMessage = {
  narrative_model?: string;
  judgement_model?: string;
  available_models?: ModelOption[];
  saved?: boolean;
};

type ContextSection = { chars?: number; estimated_tokens?: number };
type ModelCallDiagnostic = {
  model?: string;
  role?: string;
  status?: string;
  elapsed_ms?: number;
  first_token_ms?: number | null;
  tool_count?: number;
  context_sections?: Record<string, ContextSection>;
  usage?: Record<string, number>;
  prompt_profile?: string;
};
type TurnDiagnostics = {
  turn_id?: string;
  duration_ms?: number;
  message_count?: number;
  world_revision?: number;
  model_calls?: ModelCallDiagnostic[];
  lorebook?: {
    token_estimate?: number;
    token_budget?: number;
    selected?: Array<{ entry_id?: string; kind?: string; token_estimate?: number }>;
    reason_counts?: Record<string, number>;
    trace?: Array<{
      entry_id?: string;
      name?: string | null;
      kind?: string;
      group?: string | null;
      reason?: string;
      token_estimate?: number;
      matched_keys?: string[];
    }>;
  };
  tool_names?: string[];
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
  requestTurnDiagnostics();
  requestAnimationFrame(() => narrativeModelInput.focus());
}

function requestTurnDiagnostics() {
  turnDiagnosticsRefresh.disabled = true;
  turnDiagnosticsStatus.textContent = "正在读取…";
  safeSend(JSON.stringify({ type: "turn_diagnostics_get" }));
}

function metric(label: string, value: string): HTMLElement {
  const item = document.createElement("span");
  item.className = "turn-diagnostic-metric";
  const name = document.createElement("span");
  name.textContent = label;
  const data = document.createElement("strong");
  data.textContent = value;
  item.append(name, data);
  return item;
}

function milliseconds(value: number | null | undefined): string {
  if (!Number.isFinite(value)) return "--";
  return `${(Number(value) / 1000).toFixed(2)}s`;
}

const LORE_REASONS: Record<string, string> = {
  selected: "已选",
  disabled: "停用",
  regex_unsupported: "正则未执行",
  scene_gate: "场景不符",
  npc_gate: "人物不在场",
  required_flag_gate: "flag 未满足",
  forbidden_flag_gate: "被 flag 阻止",
  required_clue_gate: "线索未获得",
  cooldown: "冷却中",
  primary_key_miss: "主关键词未命中",
  secondary_key_miss: "次关键词未命中",
  group_not_selected: "同组未选",
  token_budget: "超出预算",
  entry_limit: "超出条数",
};

export function onTurnDiagnostics(payload: TurnDiagnostics | null | undefined) {
  turnDiagnosticsRefresh.disabled = false;
  turnDiagnosticsContent.replaceChildren();
  if (!payload) {
    turnDiagnosticsStatus.textContent = "暂无完整回合数据";
    return;
  }
  turnDiagnosticsStatus.textContent = payload.turn_id || "最近完整回合";

  const overview = document.createElement("div");
  overview.className = "turn-diagnostic-overview";
  overview.append(
    metric("回合耗时", milliseconds(payload.duration_ms)),
    metric("模型调用", String(payload.model_calls?.length || 0)),
    metric("消息", String(payload.message_count ?? "--")),
    metric("世界版本", String(payload.world_revision ?? "--")),
  );
  turnDiagnosticsContent.appendChild(overview);

  (payload.model_calls || []).forEach((call, index) => {
    const row = document.createElement("div");
    row.className = "turn-diagnostic-call";
    const heading = document.createElement("div");
    heading.className = "turn-diagnostic-call-heading";
    const role = call.role === "combat" ? "战斗" : "叙述";
    heading.textContent = `${index + 1}. ${role} · ${call.model || "未知模型"}`;
    const metrics = document.createElement("div");
    metrics.className = "turn-diagnostic-call-metrics";
    metrics.append(
      metric("首字", milliseconds(call.first_token_ms)),
      metric("总耗时", milliseconds(call.elapsed_ms)),
      metric("输入", String(call.usage?.prompt_tokens ?? "--")),
      metric("工具", String(call.tool_count || 0)),
    );
    const context = document.createElement("div");
    context.className = "turn-diagnostic-context";
    const sections = call.context_sections || {};
    context.append(
      metric("System", `~${sections.system?.estimated_tokens || 0}`),
      metric("历史", `~${sections.history?.estimated_tokens || 0}`),
      metric("工具定义", `~${sections.tool_schema?.estimated_tokens || 0}`),
    );
    row.append(heading, metrics, context);
    turnDiagnosticsContent.appendChild(row);
  });

  const lore = payload.lorebook || {};
  const loreBlock = document.createElement("div");
  loreBlock.className = "turn-diagnostic-lore";
  const loreHeading = document.createElement("div");
  loreHeading.className = "turn-diagnostic-call-heading";
  loreHeading.textContent = `Lorebook · ${lore.token_estimate || 0}/${lore.token_budget || 0} tokens`;
  const selected = document.createElement("div");
  selected.className = "turn-diagnostic-tags";
  for (const entry of lore.selected || []) {
    const tag = document.createElement("code");
    tag.textContent = `${entry.entry_id || "entry"} · ${entry.kind || "fact"}`;
    selected.appendChild(tag);
  }
  if (!selected.childElementCount) selected.textContent = "本轮未注入条目";
  const reasons = document.createElement("div");
  reasons.className = "turn-diagnostic-reasons";
  for (const [reason, count] of Object.entries(lore.reason_counts || {})) {
    reasons.appendChild(metric(LORE_REASONS[reason] || reason, String(count)));
  }
  loreBlock.append(loreHeading, selected, reasons);
  if (lore.trace?.length) {
    const details = document.createElement("details");
    details.className = "turn-diagnostic-lore-trace";
    const summary = document.createElement("summary");
    summary.textContent = `条目判定 · ${lore.trace.length}`;
    const list = document.createElement("div");
    list.className = "turn-diagnostic-trace-list";
    lore.trace.forEach((entry) => {
      const row = document.createElement("div");
      row.className = "turn-diagnostic-trace-row";
      row.dataset.reason = entry.reason || "unknown";
      const identity = document.createElement("code");
      identity.textContent = entry.name
        ? `${entry.entry_id || "entry"} · ${entry.name}`
        : entry.entry_id || "entry";
      const outcome = document.createElement("span");
      outcome.textContent = LORE_REASONS[entry.reason || ""] || entry.reason || "未知";
      const keys = document.createElement("span");
      keys.className = "turn-diagnostic-trace-keys";
      keys.textContent = entry.matched_keys?.length
        ? `命中：${entry.matched_keys.join(" · ")}`
        : `${entry.kind || "fact"} · ~${entry.token_estimate || 0} tokens`;
      row.append(identity, outcome, keys);
      list.appendChild(row);
    });
    details.append(summary, list);
    loreBlock.appendChild(details);
  }
  turnDiagnosticsContent.appendChild(loreBlock);

  if (payload.tool_names?.length) {
    const tools = document.createElement("div");
    tools.className = "turn-diagnostic-tools";
    tools.textContent = `工具：${payload.tool_names.join(" · ")}`;
    turnDiagnosticsContent.appendChild(tools);
  }
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
turnDiagnosticsRefresh.onclick = requestTurnDiagnostics;
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
