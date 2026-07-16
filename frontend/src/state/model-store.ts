import { create } from "zustand";

export type ModelOption = { id: string; label?: string };
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
};

type ModelState = {
  open: boolean;
  narrativeModel: string;
  judgementModel: string;
  narrativeDraft: string;
  judgementDraft: string;
  availableModels: ModelOption[];
  saving: boolean;
  status: string;
  statusKind: string;
  diagnostics: TurnDiagnostics | null;
  diagnosticsLoading: boolean;
};

export const useModelStore = create<ModelState>(() => ({
  open: false,
  narrativeModel: "",
  judgementModel: "",
  narrativeDraft: "",
  judgementDraft: "",
  availableModels: [],
  saving: false,
  status: "",
  statusKind: "",
  diagnostics: null,
  diagnosticsLoading: false,
}));
