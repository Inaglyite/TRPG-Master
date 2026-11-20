import { create } from "zustand";

import type { ModelSettingsView, TestResult } from "../api/modelSettings";
export type { ModelSettingsView, TestResult } from "../api/modelSettings";
export type ReasoningEffort = NonNullable<
  ModelSettingsView["reasoning_effort"]
>;
export type ModelOption =
  ModelSettingsView["server_defaults"]["available_models"][number];
export type ContextSection = { chars?: number; estimated_tokens?: number };
export type ModelCallDiagnostic = {
  model?: string;
  role?: string;
  status?: string;
  elapsed_ms?: number;
  first_token_ms?: number | null;
  tool_count?: number;
  context_sections?: Record<string, ContextSection>;
  usage?: Record<string, number>;
  prompt_profile?: string;
  // 数字合同（§4）：真实/估算口径与窗口来源
  config_revision?: number;
  binding_id?: string;
  provider_kind?: string;
  window_tokens?: number | null;
  window_source?: string;
  reserved_output_tokens?: number;
  input_tokens?: number | null;
  input_source?: "provider" | "estimate" | "unknown";
  output_tokens?: number | null;
  utilization?: number | null;
  capacity_state?: string;
};
export type LoreTrace = {
  entry_id?: string;
  name?: string | null;
  kind?: string;
  group?: string | null;
  reason?: string;
  token_estimate?: number;
  matched_keys?: string[];
};
export type TurnDiagnostics = {
  turn_id?: string;
  duration_ms?: number;
  message_count?: number;
  world_revision?: number;
  model_calls?: ModelCallDiagnostic[];
  lorebook?: {
    token_estimate?: number;
    token_budget?: number;
    selected?: Array<{
      entry_id?: string;
      kind?: string;
      token_estimate?: number;
    }>;
    reason_counts?: Record<string, number>;
    trace?: LoreTrace[];
  };
  tool_names?: string[];
  performance?: {
    turn_total_ms?: number;
    first_visible_ms?: number | null;
    phases_ms?: Record<string, number>;
    counters?: Record<string, number>;
  };
  mutations?: Array<{ source?: string; name?: string; success?: boolean }>;
};

// ---- 模型配置视图（服务端权威，永不包含密钥） ----

export type RoleView = ModelSettingsView["narrative"];
export type ServiceView = NonNullable<RoleView["service"]>;

// ---- 编辑草稿（api_key 只进不出：提交后清空，永不回显） ----

export type ServiceDraft = {
  label: string;
  provider_kind: string;
  api_format?: string;
  base_url: string;
  api_key: string;
  model_id: string;
  window_tokens: string;
  max_output_tokens: string;
};

export type RoleDraft = {
  mode: "default" | "custom";
  service: ServiceDraft;
  defaultOutput?: string;
};

export type ProviderPreset = NonNullable<
  ModelSettingsView["provider_presets"]
>[number];
export type TestCheck = TestResult["checks"][number];

/** 主界面上下文摘要（turn_performance 附带，纯数字）。 */
export type ContextSummary = {
  world_id?: string;
  role?: string;
  model_id?: string;
  input_tokens?: number | null;
  input_source?: string;
  window_tokens?: number | null;
  window_source?: string;
  reserved_output_tokens?: number | null;
  utilization?: number | null;
  capacity_state?: string;
  config_revision?: number | null;
  from_narrative?: boolean;
};

export function emptyServiceDraft(): ServiceDraft {
  return {
    label: "",
    provider_kind: "deepseek",
    api_format: "chat_completions",
    base_url: "",
    api_key: "",
    model_id: "",
    window_tokens: "",
    max_output_tokens: "",
  };
}

export function draftFromView(role: RoleView | undefined): RoleDraft {
  if (!role || role.mode !== "custom" || !role.service) {
    return {
      mode: "default",
      service: emptyServiceDraft(),
      defaultOutput: String(role?.configured_max_output_tokens ?? ""),
    };
  }
  return {
    mode: "custom",
    service: {
      label: role.service.label || "",
      provider_kind: role.service.provider_kind || "deepseek",
      api_format: role.service.api_format || "chat_completions",
      base_url: role.service.base_url || "",
      api_key: "",
      model_id: role.model_id || "",
      window_tokens: role.window_tokens ? String(role.window_tokens) : "",
      max_output_tokens: String(
        (role.configured_max_output_tokens === undefined
          ? role.max_output_tokens
          : role.configured_max_output_tokens) ?? "",
      ),
    },
  };
}

type ModelState = {
  open: boolean;
  tab: "general" | "models" | "context";
  view: ModelSettingsView | null;
  drafts: { narrative: RoleDraft; judgement: RoleDraft };
  scopeDraft: "account" | "world";
  confirmSharing: boolean;
  saving: boolean;
  testingRole: "narrative" | "judgement" | null;
  testResult: TestResult | null;
  /** 等待本次权威配置；缓存视图不可在此期间编辑。 */
  loading: boolean;
  /** 加载失败/超时/旧协议的可读原因；非空时面板给重试入口。 */
  loadError: string | null;
  status: string;
  statusKind: string;
  diagnostics: TurnDiagnostics | null;
  diagnosticsLoading: boolean;
  diagnosticsError: string | null;
  contextSummary: ContextSummary | null;
};

export const useModelStore = create<ModelState>(() => ({
  open: false,
  tab: "models",
  view: null,
  drafts: {
    narrative: draftFromView(undefined),
    judgement: draftFromView(undefined),
  },
  scopeDraft: "account",
  confirmSharing: false,
  saving: false,
  testingRole: null,
  testResult: null,
  loading: false,
  loadError: null,
  status: "",
  statusKind: "",
  diagnostics: null,
  diagnosticsLoading: false,
  diagnosticsError: null,
  contextSummary: null,
}));
