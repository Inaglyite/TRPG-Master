/** Model routing and diagnostics commands for the React settings panel. */

import {
  useModelStore,
  type ModelOption,
  type TurnDiagnostics,
} from "./state/model-store";
import { safeSend } from "./ws";

const modelIdPattern = /^[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,119}$/;

export function openSettings() {
  const state = useModelStore.getState();
  useModelStore.setState({
    open: true,
    narrativeDraft: state.narrativeModel,
    judgementDraft: state.judgementModel,
    status:
      state.narrativeModel && state.judgementModel ? "" : "正在读取模型配置…",
    statusKind: state.narrativeModel && state.judgementModel ? "" : "working",
  });
  if (!state.narrativeModel || !state.judgementModel) {
    safeSend(JSON.stringify({ type: "model_settings_get" }));
  }
  requestTurnDiagnostics();
}

export function closeSettings() {
  if (useModelStore.getState().saving) return;
  useModelStore.setState({ open: false, status: "", statusKind: "" });
}

export function requestTurnDiagnostics() {
  useModelStore.setState({ diagnosticsLoading: true });
  safeSend(JSON.stringify({ type: "turn_diagnostics_get" }));
}

export function saveSettings() {
  const { narrativeDraft, judgementDraft } = useModelStore.getState();
  const narrative = narrativeDraft.trim();
  const judgement = judgementDraft.trim();
  if (!modelIdPattern.test(narrative) || !modelIdPattern.test(judgement)) {
    useModelStore.setState({ status: "模型 ID 格式无效", statusKind: "error" });
    return;
  }
  useModelStore.setState({
    saving: true,
    status: "正在应用…",
    statusKind: "working",
  });
  safeSend(
    JSON.stringify({
      type: "model_settings_update",
      narrative_model: narrative,
      judgement_model: judgement,
    }),
  );
}

export function onModelSettings(data: {
  narrative_model?: string;
  judgement_model?: string;
  available_models?: ModelOption[];
  saved?: boolean;
}) {
  const state = useModelStore.getState();
  const narrativeModel = data.narrative_model || state.narrativeModel;
  const judgementModel = data.judgement_model || state.judgementModel;
  useModelStore.setState({
    narrativeModel,
    judgementModel,
    narrativeDraft: narrativeModel,
    judgementDraft: judgementModel,
    availableModels: Array.isArray(data.available_models)
      ? data.available_models
      : state.availableModels,
    saving: false,
    status: data.saved ? "已应用，将从下一回合生效" : state.status,
    statusKind: data.saved ? "success" : state.statusKind,
  });
}

export function onModelSettingsError(message: string) {
  useModelStore.setState({
    saving: false,
    status: message || "模型设置保存失败",
    statusKind: "error",
  });
}

export function onTurnDiagnostics(payload: TurnDiagnostics | null | undefined) {
  useModelStore.setState({
    diagnosticsLoading: false,
    diagnostics: payload || null,
  });
}

export function onTurnPerformance(performance: TurnDiagnostics["performance"]) {
  const current = useModelStore.getState().diagnostics;
  useModelStore.setState({
    diagnostics: current
      ? { ...current, performance: performance || {} }
      : { performance: performance || {} },
  });
}
