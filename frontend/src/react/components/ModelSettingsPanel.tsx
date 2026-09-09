import { useEffect, useState } from "react";

import {
  getNarrationSpeed,
  NARRATION_SPEED_OPTIONS,
  setNarrationSpeed,
  type NarrationSpeed,
} from "../../narration-speed";
import {
  closeSettings,
  fetchSettings,
  openSettings,
  requestTurnDiagnostics,
  restoreDefaultSettings,
  saveSettings,
  testConnection,
  updateRoleDraft,
  updateServiceDraft,
} from "../../settings";
import { useAppStore } from "../../state/app-store";
import { useOnlineStore } from "../../state/online-store";
import {
  useModelStore,
  type ModelCallDiagnostic,
  type RoleDraft,
  type RoleView,
  type TurnDiagnostics,
} from "../../state/model-store";
import { useDelayedClose } from "./transitions";

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

const ROLE_LABELS: Record<string, string> = {
  story: "叙述",
  rewrite: "改写",
  combat: "战斗",
  adjudication: "裁决",
  audit: "审计",
  narrative_consistency: "一致性",
  summary: "摘要",
};

const WINDOW_SOURCE_LABELS: Record<string, string> = {
  legacy_default: "运行配置默认",
  manual: "按配置估算",
  unknown: "未知",
};

const PROVIDER_LABELS: Record<string, string> = {
  deepseek: "DeepSeek",
  openai_compatible: "OpenAI 兼容",
};

export function ModelSettingsTrigger() {
  return (
    <button
      id="btn-settings"
      className="start-menu-button"
      type="button"
      onClick={() => openSettings()}
    >
      <span className="start-menu-icon" aria-hidden="true">
        ⚙
      </span>
      <span>模型设置</span>
    </button>
  );
}

/** 房间拒绝为"模型未配置/被阻断"时渲染的引导按钮；其余情况不渲染。 */
export function ModelSettingsGateButton() {
  const roomErrorCode = useOnlineStore((state) => state.roomErrorCode);
  if (
    roomErrorCode !== "model_not_configured" &&
    roomErrorCode !== "model_route_blocked" &&
    roomErrorCode !== "model_readiness_unavailable"
  ) {
    return null;
  }
  return (
    <button
      type="button"
      className="btn-ghost model-gate-cta"
      onClick={() => openSettings()}
    >
      打开模型设置
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

function tokens(value?: number | null) {
  return Number.isFinite(value) ? Number(value).toLocaleString() : "--";
}

/* ---------------------------------------------------------------- 模型配置页 */

function RoleCard({
  role,
  title,
  desc,
  view,
  draft,
  disabled,
  byokRequired,
}: {
  role: "narrative" | "judgement";
  title: string;
  desc: string;
  view: RoleView | undefined;
  draft: RoleDraft;
  disabled: boolean;
  byokRequired?: boolean;
}) {
  const testResult = useModelStore((state) => state.testResult);
  const testingRole = useModelStore((state) => state.testingRole);
  const testing = testingRole === role;
  const service = draft.service;
  return (
    <section className="model-role-card" data-role={role}>
      <header className="model-role-card-header">
        <h3>{title}</h3>
        <p>{desc}</p>
      </header>
      <p className="model-role-current">
        当前生效：
        {view?.mode === "custom" && view.service
          ? `${PROVIDER_LABELS[view.service.provider_kind] || view.service.provider_kind} · ${view.service.base_url_host} · ${view.model_id}`
          : byokRequired
            ? "未配置（云端不使用平台默认模型）"
            : `平台默认 · ${view?.model_id ?? "--"}`}
      </p>
      <div
        className="model-preset-control"
        role="group"
        aria-label={`${title}来源`}
      >
        <button
          type="button"
          className={draft.mode === "default" ? "selected" : ""}
          aria-pressed={draft.mode === "default"}
          disabled={disabled}
          onClick={() => updateRoleDraft(role, { mode: "default" })}
        >
          {byokRequired ? "暂不配置" : "跟随默认"}
        </button>
        <button
          type="button"
          className={draft.mode === "custom" ? "selected" : ""}
          aria-pressed={draft.mode === "custom"}
          disabled={disabled}
          onClick={() => updateRoleDraft(role, { mode: "custom" })}
        >
          自定义服务
        </button>
      </div>
      {draft.mode === "custom" ? (
        <div className="model-service-form">
          <label className="model-role-field">
            <span>配置名称（可选）</span>
            <input
              value={service.label}
              disabled={disabled}
              maxLength={40}
              placeholder="例如：我的 DeepSeek"
              onChange={(event) =>
                updateServiceDraft(role, { label: event.target.value })
              }
            />
          </label>
          <label className="model-role-field">
            <span>服务类型</span>
            <select
              value={service.provider_kind}
              disabled={disabled}
              onChange={(event) =>
                updateServiceDraft(role, { provider_kind: event.target.value })
              }
            >
              <option value="deepseek">DeepSeek 官方 / 兼容</option>
              <option value="openai_compatible">OpenAI 兼容服务</option>
            </select>
          </label>
          <label className="model-role-field">
            <span>Base URL</span>
            <input
              value={service.base_url}
              disabled={disabled}
              placeholder="https://api.deepseek.com/v1"
              spellCheck={false}
              onChange={(event) =>
                updateServiceDraft(role, { base_url: event.target.value })
              }
            />
          </label>
          <label className="model-role-field">
            <span>
              API Key
              {view?.service?.has_key ? "（已配置，留空保持不变）" : ""}
            </span>
            <input
              type="password"
              value={service.api_key}
              disabled={disabled}
              placeholder={view?.service?.has_key ? "已配置，不回显" : "sk-…"}
              autoComplete="off"
              spellCheck={false}
              onChange={(event) =>
                updateServiceDraft(role, { api_key: event.target.value })
              }
            />
          </label>
          <label className="model-role-field">
            <span>模型 ID</span>
            <input
              value={service.model_id}
              disabled={disabled}
              placeholder="deepseek-v4-flash"
              spellCheck={false}
              onChange={(event) =>
                updateServiceDraft(role, { model_id: event.target.value })
              }
            />
          </label>
          <div className="model-role-field-pair">
            <label className="model-role-field">
              <span>上下文窗口（高级，可选）</span>
              <input
                value={service.window_tokens}
                disabled={disabled}
                inputMode="numeric"
                placeholder="例如 65536"
                onChange={(event) =>
                  updateServiceDraft(role, {
                    window_tokens: event.target.value,
                  })
                }
              />
            </label>
            <label className="model-role-field">
              <span>最大输出 token（高级，可选）</span>
              <input
                value={service.max_output_tokens}
                disabled={disabled}
                inputMode="numeric"
                placeholder="例如 4096"
                onChange={(event) =>
                  updateServiceDraft(role, {
                    max_output_tokens: event.target.value,
                  })
                }
              />
            </label>
          </div>
          <p className="model-service-hint">
            不填窗口时上下文占比显示为“未知”，不猜厂商规格。云端仅允许公网 https
            地址；本地可选本机 http 推理服务。
          </p>
          <div className="model-service-test-row">
            <button
              type="button"
              className="btn-ghost model-test-btn"
              disabled={disabled || testing}
              onClick={() => testConnection(role)}
            >
              {testing ? "正在测试…" : "测试连接"}
            </button>
            <span className="model-service-hint">
              固定无剧情探针 · 不含故事内容 · 可能消耗少量额度
            </span>
          </div>
          {testResult && testResult.role === role && (
            <div
              className="model-test-result"
              data-state={testResult.ok ? "ok" : "fail"}
            >
              <div className="model-test-result-head">
                {testResult.ok ? "✓ 测试通过" : "✕ 测试未通过"} ·{" "}
                {testResult.target_host} · {testResult.elapsed_ms}ms
              </div>
              {testResult.checks.map((check) => (
                <div
                  key={check.name}
                  className="model-test-check"
                  data-ok={check.ok}
                >
                  <span>{check.ok ? "✓" : "✕"}</span>
                  <span>{check.name}</span>
                  <span>{check.detail}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      ) : (
        <p className="model-service-hint">
          {byokRequired
            ? "未配置：云端为自带 Key 模式，平台不兜底计费。请切换到“自定义服务”完成配置后才能开始游戏。"
            : `使用平台默认模型${view?.model_id ? `（当前 ${view.model_id}）` : ""}，窗口为运行配置默认。`}
        </p>
      )}
    </section>
  );
}

function ModelsTab({ disabled }: { disabled: boolean }) {
  const view = useModelStore((state) => state.view);
  const drafts = useModelStore((state) => state.drafts);
  const scopeDraft = useModelStore((state) => state.scopeDraft);
  const confirmSharing = useModelStore((state) => state.confirmSharing);
  const [narrationSpeed, setNarrationSpeedState] = useState<NarrationSpeed>(
    () => getNarrationSpeed(),
  );
  const loading = useModelStore((state) => state.loading);
  const loadError = useModelStore((state) => state.loadError);
  if (!view) {
    if (loadError) {
      return (
        <div className="model-settings-load-error" role="alert">
          <p>{loadError}</p>
          <button
            type="button"
            className="btn-ghost"
            onClick={() => fetchSettings()}
          >
            重试
          </button>
        </div>
      );
    }
    return (
      <p className="model-settings-loading">
        {loading ? "正在读取配置…" : "尚未读取配置"}
      </p>
    );
  }
  const anyCustom =
    drafts.narrative.mode === "custom" || drafts.judgement.mode === "custom";
  return (
    <div className="model-settings-tab-body">
      <div className="model-scope-line">
        <span className="model-scope-badge">{view.scope_label}</span>
        {view.applied_revision != null &&
          view.applied_revision !== view.revision && (
            <span className="model-scope-pending">
              已保存 v{view.revision} · 当前生效 v{view.applied_revision} ·
              下回合生效
            </span>
          )}
        {view.applied_revision == null && (
          <span className="model-scope-pending">
            尚无回合调用，配置随首个回合生效
          </span>
        )}
      </div>
      {view.blocked && (
        <div className="model-settings-blocked" role="alert">
          {view.blocked}
        </div>
      )}
      {view.byok_required &&
        (view.narrative.mode !== "custom" ||
          view.judgement.mode !== "custom") && (
          <div className="model-settings-blocked" role="alert">
            云端为自带 Key（BYOK）模式：所有模型调用消耗你自己的 API Key
            额度，平台不提供兜底。完成两个模型的配置并保存后才能开始游戏。
          </div>
        )}
      {view.mode === "room" && !view.can_edit && (
        <div className="model-settings-readonly">
          仅房主可修改房间模型配置；你只能查看生效模型与目的地。
          房间使用房主配置的模型服务，产生的模型调用额度由房主的 Key 承担。
        </div>
      )}
      {view.mode === "solo" && !disabled && (
        <div className="model-scope-select">
          <span>保存到：</span>
          <div
            className="model-preset-control"
            role="group"
            aria-label="配置作用域"
          >
            <button
              type="button"
              className={scopeDraft === "account" ? "selected" : ""}
              aria-pressed={scopeDraft === "account"}
              onClick={() => useModelStore.setState({ scopeDraft: "account" })}
            >
              当前账号（所有冒险默认）
            </button>
            <button
              type="button"
              className={scopeDraft === "world" ? "selected" : ""}
              aria-pressed={scopeDraft === "world"}
              onClick={() => useModelStore.setState({ scopeDraft: "world" })}
            >
              仅当前冒险
            </button>
          </div>
        </div>
      )}
      <RoleCard
        role="narrative"
        title="叙述模型"
        desc="负责正文与对话续写。"
        view={view.narrative}
        draft={drafts.narrative}
        disabled={disabled}
        byokRequired={view.byok_required}
      />
      <RoleCard
        role="judgement"
        title="裁决模型"
        desc="负责结构化行动判断与战斗结算叙述；骰点与状态结算仍由规则引擎负责。"
        view={view.judgement}
        draft={drafts.judgement}
        disabled={disabled}
        byokRequired={view.byok_required}
      />
      {anyCustom && !disabled && (
        <label className="model-consent">
          <input
            type="checkbox"
            checked={confirmSharing}
            onChange={(event) =>
              useModelStore.setState({ confirmSharing: event.target.checked })
            }
          />
          <span>
            我知晓：故事、角色与模组上下文将发送到我配置的服务商
            {view.mode === "local"
              ? "（自定义地址）"
              : "；调用额度由我的 API Key 承担"}
            。测试连接只发送固定探针，不含故事内容。
          </span>
        </label>
      )}
      <section
        className="narration-speed-section"
        aria-labelledby="narration-speed-title"
      >
        <h3 id="narration-speed-title">叙述速度</h3>
        <div id="narration-speed-control" className="model-preset-control">
          {NARRATION_SPEED_OPTIONS.map((option) => (
            <button
              key={option.value}
              type="button"
              className={narrationSpeed === option.value ? "selected" : ""}
              aria-pressed={narrationSpeed === option.value}
              onClick={() => {
                setNarrationSpeed(option.value);
                setNarrationSpeedState(option.value);
              }}
            >
              {option.label}
            </button>
          ))}
        </div>
        <p className="narration-speed-hint">
          {
            NARRATION_SPEED_OPTIONS.find(
              (option) => option.value === narrationSpeed,
            )?.hint
          }
          ；立即生效并保存在本机，单机与联机共用。
        </p>
      </section>
    </div>
  );
}

/* ---------------------------------------------------------------- 上下文页 */

function CallCard({
  call,
  index,
}: {
  call: ModelCallDiagnostic;
  index: number;
}) {
  const usage = call.usage || {};
  const cacheHit = usage.prompt_cache_hit_tokens;
  const sourceLabel =
    call.input_source === "provider"
      ? "服务商返回"
      : call.input_source === "estimate"
        ? "本地估算"
        : "未知";
  return (
    <div className="turn-diagnostic-call">
      <div className="turn-diagnostic-call-heading">
        {index + 1}. {ROLE_LABELS[call.role || ""] || call.role || "调用"} ·{" "}
        {call.model || "未知模型"}
        {call.config_revision != null && (
          <span className="turn-diagnostic-rev">v{call.config_revision}</span>
        )}
      </div>
      <div className="turn-diagnostic-call-metrics">
        <Metric label="输入" value={tokens(call.input_tokens)} />
        <Metric label="输出来源" value={sourceLabel} />
        <Metric label="输出" value={tokens(call.output_tokens)} />
        <Metric
          label="缓存命中"
          value={cacheHit != null ? tokens(cacheHit) : "未提供"}
        />
        <Metric label="总耗时" value={seconds(call.elapsed_ms)} />
      </div>
      <div className="turn-diagnostic-call-metrics">
        <Metric
          label="窗口"
          value={
            call.window_tokens
              ? `${tokens(call.window_tokens)}（${WINDOW_SOURCE_LABELS[call.window_source || ""] || "未知"}）`
              : "未知"
          }
        />
        <Metric
          label="输入占比"
          value={
            call.utilization != null
              ? `${Math.round(call.utilization * 100)}%`
              : "--"
          }
        />
        <Metric label="输出预留" value={tokens(call.reserved_output_tokens)} />
        <Metric
          label="容量"
          value={
            call.capacity_state === "compact"
              ? "接近整理阈值"
              : call.capacity_state === "irreducible"
                ? "已达硬上限"
                : call.capacity_state === "within"
                  ? "正常"
                  : "--"
          }
        />
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
  );
}

function ContextTab() {
  const view = useModelStore((state) => state.view);
  const summary = useModelStore((state) => state.contextSummary);
  const diagnostics = useModelStore((state) => state.diagnostics);
  const loading = useModelStore((state) => state.diagnosticsLoading);
  const [roleFilter, setRoleFilter] = useState("all");
  const calls = diagnostics?.model_calls || [];
  const rolesPresent = Array.from(
    new Set(calls.map((call) => call.role || "").filter(Boolean)),
  );
  const visibleCalls =
    roleFilter === "all"
      ? calls
      : calls.filter((call) => call.role === roleFilter);
  const staleForNewModel =
    view &&
    summary?.config_revision != null &&
    summary.config_revision !== view.revision;
  return (
    <div className="model-settings-tab-body">
      <section className="context-overview">
        <h3>最近叙述调用</h3>
        {!summary && <p className="model-service-hint">暂无调用数据</p>}
        {summary && (
          <>
            {staleForNewModel && (
              <p className="model-scope-pending">
                新配置已保存，旧模型最近记录如下
              </p>
            )}
            {summary.from_narrative === false && (
              <p className="model-scope-pending">
                本回合无叙述调用，数据来自其他角色
              </p>
            )}
            <div className="turn-diagnostic-overview">
              <Metric
                label="角色"
                value={ROLE_LABELS[summary.role || ""] || "--"}
              />
              <Metric label="模型" value={summary.model_id || "--"} />
              <Metric
                label="输入"
                value={`${tokens(summary.input_tokens)}（${
                  summary.input_source === "provider"
                    ? "真实"
                    : summary.input_source === "estimate"
                      ? "估算"
                      : "未知"
                }）`}
              />
              <Metric
                label="窗口"
                value={
                  summary.window_tokens
                    ? `${tokens(summary.window_tokens)}（${WINDOW_SOURCE_LABELS[summary.window_source || ""] || "未知"}）`
                    : "未知"
                }
              />
              <Metric
                label="占用"
                value={
                  summary.utilization != null
                    ? `${Math.round(summary.utilization * 100)}%`
                    : "--"
                }
              />
              <Metric
                label="输出预留"
                value={tokens(summary.reserved_output_tokens)}
              />
              <Metric
                label="估算剩余输入"
                value={
                  summary.window_tokens && summary.input_tokens
                    ? tokens(
                        Math.max(
                          0,
                          summary.window_tokens -
                            (summary.reserved_output_tokens || 0) -
                            summary.input_tokens,
                        ),
                      )
                    : "--"
                }
              />
              <Metric
                label="容量状态"
                value={
                  summary.capacity_state === "compact"
                    ? "接近整理阈值"
                    : summary.capacity_state === "irreducible"
                      ? "已达硬上限"
                      : summary.capacity_state === "within"
                        ? "正常"
                        : "--"
                }
              />
            </div>
          </>
        )}
      </section>
      <section>
        <div className="context-calls-header">
          <h3>本回合调用</h3>
          <div
            className="model-preset-control"
            role="group"
            aria-label="角色筛选"
          >
            {["all", ...rolesPresent].map((role) => (
              <button
                key={role}
                type="button"
                className={roleFilter === role ? "selected" : ""}
                aria-pressed={roleFilter === role}
                onClick={() => setRoleFilter(role)}
              >
                {role === "all" ? "全部" : ROLE_LABELS[role] || role}
              </button>
            ))}
          </div>
          <button
            id="turn-diagnostics-refresh"
            disabled={loading}
            onClick={() => requestTurnDiagnostics()}
          >
            ↻
          </button>
        </div>
        {!loading && calls.length === 0 && (
          <p className="model-service-hint">暂无调用数据</p>
        )}
        {visibleCalls.map((call, index) => (
          <CallCard key={`${call.model}-${index}`} call={call} index={index} />
        ))}
      </section>
      {diagnostics?.lorebook && <LoreBlock data={diagnostics} />}
    </div>
  );
}

function LoreBlock({ data }: { data: TurnDiagnostics }) {
  const lore = data.lorebook || {};
  return (
    <div className="turn-diagnostic-lore">
      <div className="turn-diagnostic-call-heading">
        Lorebook · {lore.token_estimate || 0}/{lore.token_budget || 0} tokens
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
    </div>
  );
}

/* ---------------------------------------------------------------- 面板主体 */

export function ModelSettingsPanel() {
  const open = useModelStore((state) => state.open);
  const tab = useModelStore((state) => state.tab);
  const view = useModelStore((state) => state.view);
  const saving = useModelStore((state) => state.saving);
  const status = useModelStore((state) => state.status);
  const statusKind = useModelStore((state) => state.statusKind);
  const mode = useAppStore((state) => state.mode);

  useEffect(() => {
    if (!open) return;
    const listener = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !saving) closeSettings();
    };
    document.addEventListener("keydown", listener);
    return () => document.removeEventListener("keydown", listener);
  }, [open, saving]);

  const { rendered, closing } = useDelayedClose(open);
  if (!rendered) return <div id="model-settings-overlay" className="hidden" />;
  const readOnly = Boolean(view) && !view!.can_edit;
  const blocked = Boolean(view?.blocked);
  return (
    <div
      id="model-settings-overlay"
      className={closing ? "overlay-closing" : undefined}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !saving) closeSettings();
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
          <button id="model-settings-close" onClick={() => closeSettings()}>
            ✕
          </button>
        </header>
        <div className="model-settings-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "models"}
            className={tab === "models" ? "selected" : ""}
            onClick={() => useModelStore.setState({ tab: "models" })}
          >
            模型配置
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "context"}
            className={tab === "context" ? "selected" : ""}
            onClick={() => useModelStore.setState({ tab: "context" })}
          >
            上下文
          </button>
        </div>
        <div className="model-settings-body">
          {tab === "models" ? (
            <ModelsTab disabled={readOnly || saving} />
          ) : (
            <ContextTab />
          )}
        </div>
        <div id="model-settings-status" data-state={statusKind || undefined}>
          {status}
        </div>
        <footer className="model-settings-actions">
          {tab === "models" && !readOnly && (
            <button
              id="model-settings-restore"
              className="btn-ghost"
              disabled={saving || !view}
              onClick={() => restoreDefaultSettings()}
            >
              {view?.byok_required ? "清除配置" : "恢复默认"}
            </button>
          )}
          <span className="model-settings-footer-space" />
          <button id="model-settings-cancel" onClick={() => closeSettings()}>
            {readOnly ? "关闭" : "取消"}
          </button>
          {tab === "models" && !readOnly && (
            <button
              id="model-settings-save"
              disabled={saving || blocked || !view}
              onClick={() => saveSettings()}
            >
              {saving ? "正在保存…" : "保存配置（下回合生效）"}
            </button>
          )}
        </footer>
      </div>
    </div>
  );
}

/** 主界面上下文摘要小组件：输入区上方一行，点击打开上下文页签。 */
export function ContextSummaryButton() {
  const summary = useModelStore((state) => state.contextSummary);
  const view = useModelStore((state) => state.view);
  const worldId = useAppStore((state) => state.activeWorldId);
  const staleWorld =
    summary?.world_id && worldId && summary.world_id !== worldId;
  const staleModel =
    view &&
    summary?.config_revision != null &&
    summary.config_revision !== view.revision;
  let label = "上下文 暂无数据";
  if (summary && !staleWorld) {
    label =
      summary.utilization != null
        ? `上下文 ${Math.round(summary.utilization * 100)}% · ${summary.model_id || ""}`
        : `上下文 未知窗口 · ${summary.model_id || ""}`;
    if (staleModel) label = `${label}（旧模型）`;
  } else if (summary && staleWorld) {
    label = "上下文 旧数据";
  }
  return (
    <button
      id="context-summary-btn"
      type="button"
      title="查看上下文占用详情"
      onClick={() => openSettings("context")}
    >
      {label}
    </button>
  );
}
