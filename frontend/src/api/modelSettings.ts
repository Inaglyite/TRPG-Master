import { z } from "zod";
import { apiFetch } from "./client";

const capabilities = z
  .object({
    streaming: z.boolean().optional(),
    tool_calling: z.boolean().optional(),
  })
  .nullable();

const role = z.object({
  mode: z.enum(["default", "custom"]),
  model_id: z.string(),
  window_tokens: z.number().nullable(),
  window_source: z.string(),
  max_output_tokens: z.number(),
  configured_max_output_tokens: z.number().nullish(),
  capabilities,
  service: z
    .object({
      label: z.string(),
      provider_kind: z.string(),
      api_format: z.string().optional(),
      base_url: z.string().nullable(),
      base_url_host: z.string(),
      has_key: z.boolean(),
    })
    .nullable(),
});

const settings = z.object({
  mode: z.enum(["local", "solo", "room", "account"]),
  can_edit: z.boolean(),
  scope_label: z.string(),
  world_override: z.boolean(),
  byok_required: z.boolean().optional(),
  revision: z.number(),
  applied_revision: z.number().nullable(),
  blocked: z.string().nullable(),
  narrative: role,
  judgement: role,
  reasoning_effort: z.enum(["low", "high", "max"]).optional(),
  thinking_mode: z.enum(["auto", "enabled", "disabled"]).optional(),
  thinking_enabled: z.boolean().optional(),
  thinking_supported: z.boolean().optional(),
  provider_presets: z
    .array(
      z.object({
        id: z.string(),
        label: z.string(),
        base_url: z.string(),
        model_id: z.string(),
        hint: z.string(),
      }),
    )
    .optional(),
  server_defaults: z.object({
    narrative_model: z.string(),
    judgement_model: z.string(),
    available_models: z.array(
      z.object({ id: z.string(), label: z.string().optional() }),
    ),
    window_tokens: z.number(),
    window_source: z.string(),
    max_output_tokens: z.number(),
  }),
});
export type ModelSettingsView = z.infer<typeof settings>;

const accountSettings = settings.extend({
  type: z.literal("model_settings"),
  mode: z.literal("account"),
  saved: z.boolean().optional(),
  thinking_only: z.boolean().optional(),
  notice: z.string().optional(),
});
const probe = z.object({
  role: z.string(),
  ok: z.boolean(),
  target_host: z.string(),
  checks: z.array(
    z.object({
      name: z.string(),
      ok: z.boolean(),
      detail: z.string(),
      optional: z.boolean().optional(),
    }),
  ),
  capabilities,
  elapsed_ms: z.number(),
  model_options: z.array(z.string()).optional(),
});
export type TestResult = z.infer<typeof probe>;
const probeResult = probe.extend({
  type: z.literal("model_settings_test_result"),
});

const endpoint = "/api/account/model-settings";
export type SettingsCommand =
  | "model_settings_get"
  | "model_settings_update"
  | "model_settings_restore_default"
  | "model_settings_test";

export function requestAccountSettings(
  command: SettingsCommand,
  body: Record<string, unknown>,
) {
  switch (command) {
    case "model_settings_get":
      return apiFetch(endpoint, accountSettings, { timeoutMs: 8_000 });
    case "model_settings_update":
      return apiFetch(endpoint, accountSettings, {
        method: "PUT",
        body,
        timeoutMs: 15_000,
      });
    case "model_settings_restore_default":
      return apiFetch(endpoint + "/restore", accountSettings, {
        method: "POST",
        body,
        timeoutMs: 15_000,
      });
    case "model_settings_test":
      return apiFetch(endpoint + "/test", probeResult, {
        method: "POST",
        body,
        timeoutMs: 90_000,
      });
  }
}
