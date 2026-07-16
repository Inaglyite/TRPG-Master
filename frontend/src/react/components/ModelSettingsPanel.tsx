import { useEffect } from "react";

import {
  closeSettings,
  openSettings,
  requestTurnDiagnostics,
  saveSettings,
} from "../../settings";
import { useModelStore, type TurnDiagnostics } from "../../state/model-store";

const reasons: Record<string, string> = {
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

function settingsCommand(
  command: "closeSettings" | "saveSettings" | "requestTurnDiagnostics",
) {
  if (command === "closeSettings") closeSettings();
  else if (command === "saveSettings") saveSettings();
  else requestTurnDiagnostics();
}

export function ModelSettingsTrigger() {
  const open = () => {
    const state = useModelStore.getState();
    useModelStore.setState({
      open: true,
      narrativeDraft: state.narrativeModel,
      judgementDraft: state.judgementModel,
    });
    openSettings();
  };
  return (
    <button
      id="btn-settings"
      className="start-menu-button"
      type="button"
      onClick={open}
    >
      <span className="start-menu-icon" aria-hidden="true">
        ⚙
      </span>
      <span>模型设置</span>
    </button>
  );
}

function seconds(value?: number | null) {
  return Number.isFinite(value)
    ? `${(Number(value) / 1000).toFixed(2)}s`
    : "--";
}
function Metric({ label, value }: { label: string; value: string }) {
  return (
    <span className="turn-diagnostic-metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </span>
  );
}

function Diagnostics({ data }: { data: TurnDiagnostics }) {
  const lore = data.lorebook || {};
  return (
    <div id="turn-diagnostics-content">
      <div className="turn-diagnostic-overview">
        <Metric label="回合耗时" value={seconds(data.duration_ms)} />
        <Metric
          label="模型调用"
          value={String(data.model_calls?.length || 0)}
        />
        <Metric label="消息" value={String(data.message_count ?? "--")} />
        <Metric label="世界版本" value={String(data.world_revision ?? "--")} />
      </div>
      {(data.model_calls || []).map((call, index) => (
        <div className="turn-diagnostic-call" key={`${call.model}-${index}`}>
          <div className="turn-diagnostic-call-heading">
            {index + 1}. {call.role === "combat" ? "战斗" : "叙述"} ·{" "}
            {call.model || "未知模型"}
          </div>
          <div className="turn-diagnostic-call-metrics">
            <Metric label="首字" value={seconds(call.first_token_ms)} />
            <Metric label="总耗时" value={seconds(call.elapsed_ms)} />
            <Metric
              label="输入"
              value={String(call.usage?.prompt_tokens ?? "--")}
            />
            <Metric label="工具" value={String(call.tool_count || 0)} />
          </div>
          <div className="turn-diagnostic-context">
            <Metric
              label="System"
              value={`~${call.context_sections?.system?.estimated_tokens || 0}`}
            />
            <Metric
              label="历史"
              value={`~${call.context_sections?.history?.estimated_tokens || 0}`}
            />
            <Metric
              label="工具定义"
              value={`~${call.context_sections?.tool_schema?.estimated_tokens || 0}`}
            />
          </div>
        </div>
      ))}
      <div className="turn-diagnostic-lore">
        <div className="turn-diagnostic-call-heading">
          Lorebook · {lore.token_estimate || 0}/{lore.token_budget || 0} tokens
        </div>
        <div className="turn-diagnostic-tags">
          {lore.selected?.length
            ? lore.selected.map((entry) => (
                <code key={entry.entry_id}>
                  {entry.entry_id || "entry"} · {entry.kind || "fact"}
                </code>
              ))
            : "本轮未注入条目"}
        </div>
        <div className="turn-diagnostic-reasons">
          {Object.entries(lore.reason_counts || {}).map(([reason, count]) => (
            <Metric
              key={reason}
              label={reasons[reason] || reason}
              value={String(count)}
            />
          ))}
        </div>
        {Boolean(lore.trace?.length) && (
          <details className="turn-diagnostic-lore-trace">
            <summary>条目判定 · {lore.trace?.length}</summary>
            <div className="turn-diagnostic-trace-list">
              {lore.trace?.map((entry, index) => (
                <div
                  className="turn-diagnostic-trace-row"
                  data-reason={entry.reason || "unknown"}
                  key={`${entry.entry_id}-${index}`}
                >
                  <code>
                    {entry.name
                      ? `${entry.entry_id || "entry"} · ${entry.name}`
                      : entry.entry_id || "entry"}
                  </code>
                  <span>
                    {reasons[entry.reason || ""] || entry.reason || "未知"}
                  </span>
                  <span className="turn-diagnostic-trace-keys">
                    {entry.matched_keys?.length
                      ? `命中：${entry.matched_keys.join(" · ")}`
                      : `${entry.kind || "fact"} · ~${entry.token_estimate || 0} tokens`}
                  </span>
                </div>
              ))}
            </div>
          </details>
        )}
      </div>
      {Boolean(data.tool_names?.length) && (
        <div className="turn-diagnostic-tools">
          工具：{data.tool_names?.join(" · ")}
        </div>
      )}
    </div>
  );
}

export function ModelSettingsPanel() {
  const state = useModelStore();
  const flash =
    state.availableModels.find((option) => option.label === "Flash")?.id || "";
  const pro =
    state.availableModels.find((option) => option.label === "Pro")?.id || "";
  useEffect(() => {
    if (state.open && !state.diagnostics && !state.diagnosticsLoading)
      void settingsCommand("requestTurnDiagnostics");
  }, [state.open]);
  useEffect(() => {
    if (!state.open) return;
    const listener = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !state.saving)
        void settingsCommand("closeSettings");
    };
    document.addEventListener("keydown", listener);
    return () => document.removeEventListener("keydown", listener);
  }, [state.open, state.saving]);
  if (!state.open)
    return <div id="model-settings-overlay" className="hidden" />;
  const preset =
    state.narrativeDraft === pro && state.judgementDraft === pro
      ? "pro"
      : state.narrativeDraft === flash && state.judgementDraft === pro
        ? "balanced"
        : state.narrativeDraft === flash && state.judgementDraft === flash
          ? "flash"
          : "";
  const setPreset = (name: string) => {
    if (!flash || !pro) return;
    useModelStore.setState(
      name === "pro"
        ? { narrativeDraft: pro, judgementDraft: pro }
        : name === "balanced"
          ? { narrativeDraft: flash, judgementDraft: pro }
          : { narrativeDraft: flash, judgementDraft: flash },
    );
  };
  return (
    <div
      id="model-settings-overlay"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !state.saving)
          void settingsCommand("closeSettings");
      }}
    >
      <div
        id="model-settings-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="model-settings-title"
      >
        <header className="model-settings-header">
          <div>
            <div className="model-settings-eyebrow">KEEPER / MODEL ROUTING</div>
            <h2 id="model-settings-title">模型设置</h2>
          </div>
          <button
            id="model-settings-close"
            onClick={() => void settingsCommand("closeSettings")}
          >
            ✕
          </button>
        </header>
        <div className="model-settings-body">
          <div id="model-preset-control" className="model-preset-control">
            {[
              ["pro", "全 Pro"],
              ["balanced", "均衡"],
              ["flash", "全 Flash"],
            ].map(([id, label]) => (
              <button
                key={id}
                className={preset === id ? "selected" : ""}
                aria-pressed={preset === id}
                onClick={() => setPreset(id)}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="model-role-grid">
            <label className="model-role-field">
              <span>叙述模型</span>
              <input
                id="narrative-model-input"
                list="model-id-options"
                value={state.narrativeDraft}
                onChange={(event) =>
                  useModelStore.setState({ narrativeDraft: event.target.value })
                }
              />
            </label>
            <label className="model-role-field">
              <span>判定模型</span>
              <input
                id="judgement-model-input"
                list="model-id-options"
                value={state.judgementDraft}
                onChange={(event) =>
                  useModelStore.setState({ judgementDraft: event.target.value })
                }
              />
            </label>
            <datalist id="model-id-options">
              {state.availableModels.map((option) => (
                <option
                  key={option.id}
                  value={option.id}
                  label={option.label || option.id}
                />
              ))}
            </datalist>
          </div>
          <div
            id="model-settings-status"
            data-state={state.statusKind || undefined}
          >
            {state.status}
          </div>
          <section
            id="turn-diagnostics-section"
            className="turn-diagnostics-section"
          >
            <header className="turn-diagnostics-header">
              <h3>上一回合诊断</h3>
              <button
                id="turn-diagnostics-refresh"
                disabled={state.diagnosticsLoading}
                onClick={() => void settingsCommand("requestTurnDiagnostics")}
              >
                ↻
              </button>
            </header>
            <div id="turn-diagnostics-status">
              {state.diagnosticsLoading
                ? "正在读取…"
                : state.diagnostics?.turn_id || "暂无完整回合数据"}
            </div>
            {state.diagnostics && <Diagnostics data={state.diagnostics} />}
          </section>
        </div>
        <footer className="model-settings-actions">
          <button
            id="model-settings-cancel"
            onClick={() => void settingsCommand("closeSettings")}
          >
            取消
          </button>
          <button
            id="model-settings-save"
            disabled={state.saving}
            onClick={() => void settingsCommand("saveSettings")}
          >
            应用设置
          </button>
        </footer>
      </div>
    </div>
  );
}
