import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./ws", () => ({ safeSend: vi.fn() }));
vi.mock("./state/app-store", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./state/app-store")>();
  return actual;
});

import { safeSend } from "./ws";
import {
  fetchSettings,
  onModelSettings,
  onModelSettingsError,
  onModelSettingsTestResult,
  onTurnPerformance,
  restoreDefaultSettings,
  saveSettings,
  testConnection,
} from "./settings";
import {
  draftFromView,
  useModelStore,
  type ModelSettingsView,
} from "./state/model-store";
import { useAppStore } from "./state/app-store";

const safeSendMock = vi.mocked(safeSend);

const view: ModelSettingsView = {
  mode: "local",
  can_edit: true,
  scope_label: "本地默认（本机所有冒险共用）",
  world_override: false,
  revision: 1,
  applied_revision: 1,
  blocked: null,
  narrative: {
    mode: "custom",
    model_id: "qwen3:32b",
    window_tokens: 32768,
    window_source: "manual",
    max_output_tokens: 2048,
    capabilities: null,
    service: {
      label: "本机",
      provider_kind: "openai_compatible",
      base_url: "http://127.0.0.1:11434/v1",
      base_url_host: "127.0.0.1",
      has_key: true,
    },
  },
  judgement: {
    mode: "default",
    model_id: "deepseek-flash",
    window_tokens: 65536,
    window_source: "legacy_default",
    max_output_tokens: 4096,
    capabilities: null,
    service: null,
  },
  server_defaults: {
    narrative_model: "deepseek-flash",
    judgement_model: "deepseek-flash",
    available_models: [],
    window_tokens: 65536,
    window_source: "legacy_default",
    max_output_tokens: 4096,
  },
};

describe("settings commands", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useModelStore.setState({
      open: true,
      tab: "models",
      view,
      drafts: {
        narrative: draftFromView(view.narrative),
        judgement: draftFromView(view.judgement),
      },
      scopeDraft: "account",
      confirmSharing: false,
      saving: false,
      testingRole: null,
      testResult: null,
      status: "",
      statusKind: "",
      diagnostics: null,
      diagnosticsLoading: false,
      contextSummary: null,
      loading: false,
      loadError: null,
    });
  });

  it("旧协议响应（仅 narrative_model）明确报后端过旧，不再静默卡加载", () => {
    useModelStore.setState({ loading: true, view: null });
    onModelSettings({ narrative_model: "deepseek-flash" } as never);
    const state = useModelStore.getState();
    expect(state.loading).toBe(false);
    expect(state.view).toBeNull();
    expect(state.loadError).toContain("后端版本过旧");
  });

  it("加载超时给出可读错误；重试重新拉取并恢复加载态", () => {
    vi.useFakeTimers();
    try {
      useModelStore.setState({ view: null }); // 首次加载才算 loading
      fetchSettings();
      expect(useModelStore.getState().loading).toBe(true);
      expect(safeSendMock).toHaveBeenCalledWith(
        JSON.stringify({ type: "model_settings_get" }),
      );
      vi.advanceTimersByTime(8100);
      expect(useModelStore.getState().loading).toBe(false);
      expect(useModelStore.getState().loadError).toContain("超时");
      // 重试：重新发送并回到加载态
      fetchSettings();
      expect(useModelStore.getState().loading).toBe(true);
      expect(useModelStore.getState().loadError).toBeNull();
      expect(safeSendMock).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("缓存视图刷新也锁住保存，迟到响应同步后才允许填写", () => {
    vi.useFakeTimers();
    try {
      fetchSettings();
      expect(useModelStore.getState().loading).toBe(true);
      safeSendMock.mockClear();
      useModelStore.setState({ confirmSharing: true });
      saveSettings();
      expect(safeSendMock).not.toHaveBeenCalled();
      onModelSettings(view);
      expect(useModelStore.getState().loading).toBe(false);
      expect(useModelStore.getState().confirmSharing).toBe(false);
      expect(useModelStore.getState().status).toBe("");
    } finally {
      vi.useRealTimers();
    }
  });

  it("保存载荷：custom 带服务字段，空 Key 不覆盖已保存值", () => {
    useModelStore.setState({ confirmSharing: true });
    saveSettings();
    const payload = JSON.parse(safeSendMock.mock.calls[0][0]);
    expect(payload.type).toBe("model_settings_update");
    expect(payload.narrative.mode).toBe("custom");
    expect(payload.narrative.service.model_id).toBe("qwen3:32b");
    expect(payload.narrative.service.base_url).toBe(
      "http://127.0.0.1:11434/v1",
    );
    expect(payload.narrative.service.window_tokens).toBe(32768);
    expect(payload.narrative.service.api_key).toBeUndefined();
    expect(payload.judgement).toEqual({ mode: "default" });
    expect(payload.confirm_data_sharing).toBe(true);
  });

  it("自定义但未勾选确认：拒绝发送并提示", () => {
    saveSettings();
    expect(safeSendMock).not.toHaveBeenCalled();
    expect(useModelStore.getState().statusKind).toBe("error");
    expect(useModelStore.getState().status).toContain("确认");
  });

  it("缺 Base URL 时校验失败", () => {
    useModelStore.setState((state) => ({
      confirmSharing: true,
      drafts: {
        ...state.drafts,
        narrative: {
          ...state.drafts.narrative,
          service: { ...state.drafts.narrative.service, base_url: "" },
        },
      },
    }));
    saveSettings();
    expect(safeSendMock).not.toHaveBeenCalled();
    expect(useModelStore.getState().status).toContain("Base URL");
  });

  it("恢复默认发送 restore_default 且不带确认字段", () => {
    restoreDefaultSettings();
    const payload = JSON.parse(safeSendMock.mock.calls[0][0]);
    expect(payload.type).toBe("model_settings_restore_default");
    expect(payload.scope).toBe("account");
  });

  it("测试连接：custom 表单随附服务字段与确认", () => {
    useModelStore.setState({ confirmSharing: true });
    testConnection("narrative");
    const payload = JSON.parse(safeSendMock.mock.calls[0][0]);
    expect(payload.type).toBe("model_settings_test");
    expect(payload.role).toBe("narrative");
    expect(payload.service.model_id).toBe("qwen3:32b");
    expect(payload.confirm_data_sharing).toBe(true);
    expect(useModelStore.getState().testingRole).toBe("narrative");
  });

  it("测试连接：默认模式不带服务字段", () => {
    testConnection("judgement");
    const payload = JSON.parse(safeSendMock.mock.calls[0][0]);
    expect(payload.role).toBe("judgement");
    expect(payload.service).toBeUndefined();
  });

  it("onModelSettings：saved 后同步草稿并清空 Key 输入", () => {
    useModelStore.setState((state) => ({
      drafts: {
        ...state.drafts,
        narrative: {
          mode: "custom",
          service: { ...state.drafts.narrative.service, api_key: "sk-typed" },
        },
      },
    }));
    onModelSettings({
      ...view,
      saved: true,
      notice: "配置已保存，将从下一回合生效",
    });
    const state = useModelStore.getState();
    expect(state.saving).toBe(false);
    expect(state.status).toContain("下一回合");
    expect(state.drafts.narrative.service.api_key).toBe("");
    expect(state.confirmSharing).toBe(false);
  });

  it("onModelSettingsError 复位 saving/testing", () => {
    useModelStore.setState({ saving: true, testingRole: "narrative" });
    onModelSettingsError("保存失败：端口被拒绝");
    const state = useModelStore.getState();
    expect(state.saving).toBe(false);
    expect(state.testingRole).toBeNull();
    expect(state.status).toContain("保存失败");
  });

  it("onModelSettingsTestResult 写入结果", () => {
    onModelSettingsTestResult({
      role: "narrative",
      ok: false,
      target_host: "api.deepseek.com",
      checks: [
        {
          name: "connectivity",
          ok: false,
          detail: "无法连接到服务（地址不可达或被拒绝）",
        },
      ],
      capabilities: null,
      elapsed_ms: 803,
    });
    const state = useModelStore.getState();
    expect(state.testResult?.ok).toBe(false);
    expect(state.statusKind).toBe("error");
  });

  it("onTurnPerformance：其他世界的摘要不覆盖当前", () => {
    useAppStore.setState({ activeWorldId: "w1" });
    useModelStore.setState({
      contextSummary: {
        world_id: "w1",
        utilization: 0.2,
        from_narrative: true,
      },
    });
    onTurnPerformance({}, { world_id: "w-other", utilization: 0.9 });
    expect(useModelStore.getState().contextSummary?.utilization).toBe(0.2);
    onTurnPerformance(
      {},
      { world_id: "w1", utilization: 0.5, from_narrative: true },
    );
    expect(useModelStore.getState().contextSummary?.utilization).toBe(0.5);
  });
});
