/** Model routing and diagnostics commands for the React settings panel. */

import { useAppStore } from "./state/app-store";
import {
  draftFromView,
  useModelStore,
  type ContextSummary,
  type ModelSettingsView,
  type RoleDraft,
  type TestResult,
  type TurnDiagnostics,
} from "./state/model-store";
import { safeSend } from "./ws";

const modelIdPattern = /^[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,119}$/;

const LOAD_TIMEOUT_MS = 8000;
let loadTimer: ReturnType<typeof setTimeout> | null = null;

function clearLoadTimer() {
  if (loadTimer) {
    clearTimeout(loadTimer);
    loadTimer = null;
  }
}

/** 编辑前等待本次权威视图，避免迟到响应覆盖已经填写的草稿。 */
export function fetchSettings() {
  useModelStore.setState({
    loading: true,
    loadError: null,
    status: "正在读取模型配置…",
    statusKind: "working",
  });
  clearLoadTimer();
  loadTimer = setTimeout(() => {
    const state = useModelStore.getState();
    if (state.loading) {
      useModelStore.setState({
        loading: false,
        // 有缓存视图时保留表单可编辑；超时只是刷新失败，不应把用户
        // 正在编辑的配置替换成错误页。
        loadError: state.view
          ? null
          : "读取配置超时：请检查与守秘人的连接后重试",
        status: state.view ? "读取配置超时，可稍后重试" : "",
        statusKind: state.view ? "error" : "",
      });
    }
  }, LOAD_TIMEOUT_MS);
  safeSend(JSON.stringify({ type: "model_settings_get" }));
}

export function openSettings(tab?: "models" | "context") {
  const state = useModelStore.getState();
  useModelStore.setState({
    open: true,
    tab: tab || state.tab,
    testResult: null,
  });
  fetchSettings();
  requestTurnDiagnostics();
}

export function closeSettings() {
  if (useModelStore.getState().saving) return;
  clearLoadTimer();
  useModelStore.setState({
    open: false,
    status: "",
    statusKind: "",
    loading: false,
    loadError: null,
  });
}

export function requestTurnDiagnostics() {
  useModelStore.setState({ diagnosticsLoading: true });
  safeSend(JSON.stringify({ type: "turn_diagnostics_get" }));
}

export function updateRoleDraft(
  role: "narrative" | "judgement",
  patch: Partial<RoleDraft>,
) {
  const drafts = useModelStore.getState().drafts;
  useModelStore.setState({
    drafts: { ...drafts, [role]: { ...drafts[role], ...patch } },
    testResult: null,
  });
}

export function updateServiceDraft(
  role: "narrative" | "judgement",
  patch: Partial<RoleDraft["service"]>,
) {
  const drafts = useModelStore.getState().drafts;
  useModelStore.setState({
    drafts: {
      ...drafts,
      [role]: {
        ...drafts[role],
        service: { ...drafts[role].service, ...patch },
      },
    },
    testResult: null,
  });
}

function optionalInt(raw: string): number | undefined {
  const trimmed = raw.trim();
  if (!trimmed) return undefined;
  const value = Number(trimmed);
  return Number.isInteger(value) && value > 0 ? value : undefined;
}

function rolePayload(draft: RoleDraft): Record<string, unknown> {
  if (draft.mode !== "custom") return { mode: "default" };
  const service: Record<string, unknown> = {
    label: draft.service.label.trim(),
    provider_kind: draft.service.provider_kind,
    base_url: draft.service.base_url.trim(),
    model_id: draft.service.model_id.trim(),
  };
  // Key 留空 = 沿用已保存的 Key（服务端用 has_key 语义处理）
  if (draft.service.api_key.trim())
    service.api_key = draft.service.api_key.trim();
  const window = optionalInt(draft.service.window_tokens);
  if (window) service.window_tokens = window;
  const maxOutput = optionalInt(draft.service.max_output_tokens);
  if (maxOutput) service.max_output_tokens = maxOutput;
  return { mode: "custom", service };
}

function validateDrafts(): string | null {
  const { drafts } = useModelStore.getState();
  for (const [role, label] of [
    ["narrative", "叙述模型"],
    ["judgement", "裁决模型"],
  ] as const) {
    const draft = drafts[role];
    if (draft.mode !== "custom") continue;
    if (!draft.service.base_url.trim()) return `${label}：请填写 Base URL`;
    if (!/^https?:\/\//i.test(draft.service.base_url.trim()))
      return `${label}：Base URL 必须以 http(s):// 开头`;
    if (!modelIdPattern.test(draft.service.model_id.trim()))
      return `${label}：模型 ID 格式无效`;
    if (
      draft.service.window_tokens.trim() &&
      optionalInt(draft.service.window_tokens) === undefined
    )
      return `${label}：上下文窗口必须是正整数`;
    if (
      draft.service.max_output_tokens.trim() &&
      optionalInt(draft.service.max_output_tokens) === undefined
    )
      return `${label}：最大输出必须是正整数`;
  }
  return null;
}

function hasCustomDraft(): boolean {
  const { drafts } = useModelStore.getState();
  return (
    drafts.narrative.mode === "custom" || drafts.judgement.mode === "custom"
  );
}

export function saveSettings() {
  const current = useModelStore.getState();
  if (current.loading || !current.view) return;
  const invalid = validateDrafts();
  if (invalid) {
    useModelStore.setState({ status: invalid, statusKind: "error" });
    return;
  }
  const state = useModelStore.getState();
  if (hasCustomDraft() && !state.confirmSharing) {
    useModelStore.setState({
      status: "请先勾选数据发送确认：故事与角色上下文将发送到你配置的服务商",
      statusKind: "error",
    });
    return;
  }
  useModelStore.setState({
    saving: true,
    status: "正在保存…",
    statusKind: "working",
  });
  safeSend(
    JSON.stringify({
      type: "model_settings_update",
      narrative: rolePayload(state.drafts.narrative),
      judgement: rolePayload(state.drafts.judgement),
      scope: state.scopeDraft,
      confirm_data_sharing: state.confirmSharing,
    }),
  );
}

export function restoreDefaultSettings() {
  const state = useModelStore.getState();
  useModelStore.setState({
    saving: true,
    status: "正在恢复默认…",
    statusKind: "working",
  });
  safeSend(
    JSON.stringify({
      type: "model_settings_restore_default",
      scope: state.scopeDraft,
    }),
  );
}

export function testConnection(role: "narrative" | "judgement") {
  const state = useModelStore.getState();
  const draft = state.drafts[role];
  const payload: Record<string, unknown> = {
    type: "model_settings_test",
    role,
    scope: state.scopeDraft,
    confirm_data_sharing: state.confirmSharing,
  };
  if (draft.mode === "custom") {
    const invalid = validateDrafts();
    if (invalid) {
      useModelStore.setState({ status: invalid, statusKind: "error" });
      return;
    }
    if (!state.confirmSharing) {
      useModelStore.setState({
        status:
          "请先勾选数据发送确认：测试探针将发送到该服务商（不含故事内容）",
        statusKind: "error",
      });
      return;
    }
    payload.service = (rolePayload(draft) as { service?: unknown }).service;
  }
  useModelStore.setState({
    testingRole: role,
    testResult: null,
    status: "正在测试连接（固定探针，可能消耗少量额度）…",
    statusKind: "working",
  });
  safeSend(JSON.stringify(payload));
}

export function onModelSettings(
  data: Partial<ModelSettingsView> & {
    saved?: boolean;
    notice?: string;
  },
) {
  const state = useModelStore.getState();
  const view = data as ModelSettingsView;
  if (!view || typeof view !== "object" || !view.narrative) {
    // 旧版后端只返回 narrative_model/judgement_model 顶层字段：明确提示重启，
    // 不能静默忽略让面板永远停在"正在读取"。
    if (
      typeof (data as { narrative_model?: unknown }).narrative_model ===
      "string"
    ) {
      clearLoadTimer();
      useModelStore.setState({
        loading: false,
        loadError: "后端版本过旧：模型配置协议已升级，请重启后端服务后重试",
        status: "",
        statusKind: "",
      });
    }
    return;
  }
  clearLoadTimer();
  // 草稿同步：首次/保存后/外部（如房主）改过版本或模式时；用户正在编辑时不动草稿。
  const externalChange =
    state.view !== null &&
    (state.view.revision !== view.revision || state.view.mode !== view.mode);
  const shouldSyncDrafts =
    state.loading ||
    !state.open ||
    data.saved ||
    state.view === null ||
    externalChange;
  useModelStore.setState({
    view,
    loading: false,
    loadError: null,
    saving: false,
    status:
      data.notice ||
      (data.saved
        ? "配置已保存，将从下一回合生效"
        : state.loading
          ? ""
          : state.status),
    statusKind: data.saved ? "success" : state.loading ? "" : state.statusKind,
    ...(shouldSyncDrafts
      ? {
          drafts: {
            narrative: draftFromView(view.narrative),
            judgement: draftFromView(view.judgement),
          },
          confirmSharing: false,
        }
      : {}),
  });
}

export function onModelSettingsError(message: string) {
  clearLoadTimer();
  const hasView = Boolean(useModelStore.getState().view);
  useModelStore.setState({
    saving: false,
    testingRole: null,
    loading: false,
    // 操作/校验失败不能抹掉已有权威视图，否则用户无法修正草稿并重试。
    // 只有首次加载没有视图时才进入整页错误态。
    loadError: hasView ? null : message || "模型设置操作失败",
    status: message || "模型设置操作失败",
    statusKind: "error",
  });
}

export function onModelSettingsTestResult(data: TestResult) {
  useModelStore.setState({
    testingRole: null,
    testResult: data,
    status: data.ok ? "测试通过" : "测试未通过，详见各项检查",
    statusKind: data.ok ? "success" : "error",
  });
}

export function onTurnDiagnostics(payload: TurnDiagnostics | null | undefined) {
  useModelStore.setState({
    diagnosticsLoading: false,
    diagnostics: payload || null,
  });
}

export function onTurnPerformance(
  performance: TurnDiagnostics["performance"],
  contextSummary?: ContextSummary | null,
) {
  const current = useModelStore.getState().diagnostics;
  useModelStore.setState({
    diagnostics: current
      ? { ...current, performance: performance || {} }
      : { performance: performance || {} },
    // 只接受当前世界的摘要：切世界/断线重连后旧世界的数字立即作废
    contextSummary:
      contextSummary &&
      (!contextSummary.world_id ||
        contextSummary.world_id === useAppStore.getState().activeWorldId)
        ? contextSummary
        : useModelStore.getState().contextSummary,
  });
}

export function clearContextSummary() {
  useModelStore.setState({ contextSummary: null });
}
