import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../settings", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../settings")>();
  return {
    ...actual,
    closeSettings: vi.fn(),
    saveSettings: vi.fn(),
    restoreDefaultSettings: vi.fn(),
    testConnection: vi.fn(),
    requestTurnDiagnostics: vi.fn(),
    openSettings: vi.fn(),
  };
});

import {
  draftFromView,
  useModelStore,
  type ModelSettingsView,
} from "../../state/model-store";
import { useAppStore } from "../../state/app-store";
import { useOnlineStore } from "../../state/online-store";
import {
  ContextSummaryButton,
  ModelSettingsGateButton,
  ModelSettingsPanel,
} from "./ModelSettingsPanel";

function makeView(patch: Partial<ModelSettingsView> = {}): ModelSettingsView {
  return {
    mode: "local",
    can_edit: true,
    scope_label: "本地默认（本机所有冒险共用）",
    world_override: false,
    revision: 2,
    applied_revision: 1,
    blocked: null,
    narrative: {
      mode: "default",
      model_id: "deepseek-v4-flash",
      window_tokens: 65536,
      window_source: "legacy_default",
      max_output_tokens: 4096,
      capabilities: null,
      service: null,
    },
    judgement: {
      mode: "custom",
      model_id: "qwen3:32b",
      window_tokens: 32768,
      window_source: "manual",
      max_output_tokens: 2048,
      capabilities: { streaming: true, tool_calling: true },
      service: {
        label: "本机推理",
        provider_kind: "openai_compatible",
        base_url: "http://127.0.0.1:11434/v1",
        base_url_host: "127.0.0.1",
        has_key: true,
      },
    },
    server_defaults: {
      narrative_model: "deepseek-v4-flash",
      judgement_model: "deepseek-v4-flash",
      available_models: [{ id: "deepseek-v4-flash", label: "Flash" }],
      window_tokens: 65536,
      window_source: "legacy_default",
      max_output_tokens: 4096,
    },
    ...patch,
  };
}

function seed(view: ModelSettingsView | null) {
  useModelStore.setState({
    open: true,
    tab: "models",
    view,
    drafts: view
      ? {
          narrative: draftFromView(view.narrative),
          judgement: draftFromView(view.judgement),
        }
      : undefined,
    scopeDraft: "account",
    confirmSharing: false,
    saving: false,
    loading: false,
    loadError: null,
    testingRole: null,
    testResult: null,
    status: "",
    statusKind: "",
    diagnostics: null,
    diagnosticsLoading: false,
    contextSummary: null,
  });
}

describe("ModelSettingsPanel", () => {
  beforeEach(() => {
    localStorage.clear();
    seed(makeView());
  });

  it("有缓存时仍等待刷新，加载和失败期间不能编辑或保存", () => {
    useModelStore.setState({ loading: true });
    render(<ModelSettingsPanel />);
    expect(screen.getByText("正在读取配置…")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "自定义服务" })).toBeNull();
    expect(screen.getByRole("button", { name: /保存配置/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: "恢复默认" })).toBeDisabled();
    act(() =>
      useModelStore.setState({ loading: false, loadError: "读取配置超时" }),
    );
    expect(screen.getByRole("button", { name: "重试" })).toBeVisible();
    expect(screen.getByRole("button", { name: /保存配置/ })).toBeDisabled();
    act(() => useModelStore.setState({ loadError: null }));
    expect(screen.getByRole("button", { name: /保存配置/ })).toBeEnabled();
  });

  it("两页签 + 角色卡片 + 版本三态行", () => {
    render(<ModelSettingsPanel />);
    expect(
      screen.getByRole("dialog", { name: "模型设置" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "模型配置" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("tab", { name: "上下文" })).toBeInTheDocument();
    // 叙述默认 + 裁决自定义
    expect(screen.getByText(/当前 deepseek-v4-flash/)).toBeInTheDocument();
    expect(screen.getByDisplayValue("qwen3:32b")).toBeInTheDocument();
    expect(
      screen.getByDisplayValue("http://127.0.0.1:11434/v1"),
    ).toBeInTheDocument();
    // API Key 不回显
    const keyInput = screen.getByPlaceholderText("已配置，不回显");
    expect(keyInput).toHaveValue("");
    // 版本三态：已保存 v2 · 当前生效 v1 · 下回合生效
    expect(
      screen.getByText(/已保存 v2 · 当前生效 v1 · 下回合生效/),
    ).toBeInTheDocument();
    // 作用域徽标
    expect(
      screen.getByText("本地默认（本机所有冒险共用）"),
    ).toBeInTheDocument();
  });

  it("切换到自定义服务展开表单并要求数据确认", () => {
    seed(makeView({ mode: "solo", scope_label: "当前账号" }));
    render(<ModelSettingsPanel />);
    const narrativeCard = document.querySelector('[data-role="narrative"]')!;
    fireEvent.click(
      Array.from(narrativeCard.querySelectorAll("button")).find(
        (button) => button.textContent === "自定义服务",
      )!,
    );
    // 勾选确认出现且初始未勾
    const consent = screen.getByRole("checkbox");
    expect(consent).not.toBeChecked();
    expect(
      screen.getByText(/故事、角色与模组上下文将发送/),
    ).toBeInTheDocument();
    fireEvent.click(consent);
    expect(screen.getByRole("checkbox")).toBeChecked();
  });

  it("只读成员视图：全部禁用且无保存按钮", () => {
    const memberView = makeView({
      mode: "room",
      can_edit: false,
      scope_label: "当前房间（房主配置，成员只读）",
    });
    // 成员投影不携带完整 URL（只有目的地主机名）
    memberView.judgement.service = {
      ...memberView.judgement.service!,
      base_url: null,
    };
    seed(memberView);
    render(<ModelSettingsPanel />);
    expect(screen.getByText(/仅房主可修改/)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /保存配置/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "恢复默认" }),
    ).not.toBeInTheDocument();
    // 自定义表单输入禁用
    expect(screen.getByDisplayValue("qwen3:32b")).toBeDisabled();
    // 成员看不到完整 URL（base_url 为 null → 输入框为空）
    expect(
      screen.getByPlaceholderText("https://api.deepseek.com/v1"),
    ).toHaveValue("");
  });

  it("blocked 状态显示阻断原因并禁用保存", () => {
    seed(
      makeView({
        blocked:
          "原房主的模型配置已随房主更换失效，需要当前房主重新绑定后再继续",
      }),
    );
    render(<ModelSettingsPanel />);
    expect(screen.getByRole("alert")).toHaveTextContent(/重新绑定/);
    expect(screen.getByRole("button", { name: /保存配置/ })).toBeDisabled();
  });

  it("上下文页签：无数据与摘要/调用列表", () => {
    render(<ModelSettingsPanel />);
    fireEvent.click(screen.getByRole("tab", { name: "上下文" }));
    expect(screen.getAllByText("暂无调用数据").length).toBeGreaterThan(0);

    act(() => {
      useModelStore.setState({
        contextSummary: {
          world_id: "w1",
          role: "story",
          model_id: "deepseek-v4-flash",
          input_tokens: 18400,
          input_source: "provider",
          window_tokens: 65536,
          window_source: "legacy_default",
          reserved_output_tokens: 4096,
          utilization: 0.28,
          capacity_state: "within",
          config_revision: 2,
          from_narrative: true,
        },
        diagnostics: {
          turn_id: "t1",
          model_calls: [
            {
              model: "deepseek-v4-flash",
              role: "story",
              status: "completed",
              input_tokens: 18400,
              input_source: "provider",
              output_tokens: 620,
              window_tokens: 65536,
              utilization: 0.28,
              usage: { prompt_tokens: 18400, prompt_cache_hit_tokens: 11000 },
              context_sections: {
                system: { chars: 100, estimated_tokens: 40 },
              },
            },
            {
              model: "deepseek-v4-flash",
              role: "audit",
              status: "completed",
              input_tokens: 900,
              input_source: "estimate",
            },
          ],
        },
      });
    });
    expect(screen.getByText("18,400（真实）")).toBeInTheDocument();
    expect(screen.getAllByText("28%").length).toBeGreaterThan(0);
    // 角色标签真实：审计不是"叙述"
    expect(screen.getByText(/2\. 审计/)).toBeInTheDocument();
    // 角色筛选
    fireEvent.click(screen.getByRole("button", { name: "审计" }));
    expect(screen.queryByText(/1\. 叙述/)).not.toBeInTheDocument();
    expect(screen.getByText(/1\. 审计/)).toBeInTheDocument();
  });

  it("配置未加载：保存/恢复默认禁用，错误态给重试入口", () => {
    seed(null);
    render(<ModelSettingsPanel />);
    expect(screen.getByText("尚未读取配置")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /保存配置/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: "恢复默认" })).toBeDisabled();

    // 加载失败：可读原因 + 重试按钮
    act(() => {
      useModelStore.setState({
        loadError: "后端版本过旧：模型配置协议已升级，请重启后端服务后重试",
      });
    });
    expect(screen.getByRole("alert")).toHaveTextContent(/后端版本过旧/);
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
  });

  it("offers three narration speed tiers and persists the choice locally", () => {
    render(<ModelSettingsPanel />);
    expect(screen.getByRole("button", { name: "标准" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    fireEvent.click(screen.getByRole("button", { name: "快" }));
    expect(localStorage.getItem("trpg-narration-speed")).toBe("fast");
    fireEvent.click(screen.getByRole("button", { name: "慢" }));
    expect(localStorage.getItem("trpg-narration-speed")).toBe("slow");
  });
});

describe("ContextSummaryButton", () => {
  beforeEach(() => {
    useModelStore.setState({ contextSummary: null, view: null });
    useAppStore.setState({ activeWorldId: "w1" });
  });

  it("无调用数据时显示占位", () => {
    render(<ContextSummaryButton />);
    expect(
      screen.getByRole("button", { name: /暂无数据/ }),
    ).toBeInTheDocument();
  });

  it("显示占用百分比与模型，旧世界数据标记作废", () => {
    useModelStore.setState({
      contextSummary: {
        world_id: "w1",
        model_id: "deepseek-v4-flash",
        utilization: 0.28,
        from_narrative: true,
      },
    });
    const { rerender } = render(<ContextSummaryButton />);
    expect(screen.getByRole("button", { name: /28%/ })).toBeInTheDocument();
    useModelStore.setState({
      contextSummary: { world_id: "w0", model_id: "m", utilization: 0.5 },
    });
    rerender(<ContextSummaryButton />);
    expect(screen.getByRole("button", { name: /旧数据/ })).toBeInTheDocument();
  });
});

describe("BYOK-only 云端语义", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("未配置时显示 BYOK 警示、未配置生效行与清除配置按钮", () => {
    seed(
      makeView({
        mode: "solo",
        scope_label: "当前账号",
        byok_required: true,
        judgement: {
          mode: "default",
          model_id: "deepseek-v4-flash",
          window_tokens: 65536,
          window_source: "legacy_default",
          max_output_tokens: 4096,
          capabilities: null,
          service: null,
        },
      }),
    );
    render(<ModelSettingsPanel />);
    // 顶部警示：两个角色任一未配置即提示
    expect(screen.getByText(/云端为自带 Key（BYOK）模式/)).toBeInTheDocument();
    // 生效行不再写"平台默认"
    expect(
      screen.getAllByText(/未配置（云端不使用平台默认模型）/),
    ).toHaveLength(2);
    // 云端切换器与恢复按钮文案
    expect(screen.getAllByRole("button", { name: "暂不配置" })).toHaveLength(2);
    expect(
      screen.getByRole("button", { name: "清除配置" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "恢复默认" }),
    ).not.toBeInTheDocument();
  });

  it("云端配置齐全后不显示 BYOK 警示", () => {
    seed(
      makeView({
        mode: "room",
        can_edit: true,
        scope_label: "当前房间（房主配置）",
        byok_required: true,
        narrative: {
          mode: "custom",
          model_id: "deepseek-v4-flash",
          window_tokens: null,
          window_source: "unknown",
          max_output_tokens: 4096,
          capabilities: null,
          service: {
            label: "我的服务",
            provider_kind: "deepseek",
            base_url: "https://api.deepseek.com/v1",
            base_url_host: "api.deepseek.com",
            has_key: true,
          },
        },
      }),
    );
    render(<ModelSettingsPanel />);
    expect(
      screen.queryByText(/云端为自带 Key（BYOK）模式/),
    ).not.toBeInTheDocument();
  });

  it("房间成员只读视图说明费用由房主 Key 承担", () => {
    seed(
      makeView({
        mode: "room",
        can_edit: false,
        scope_label: "当前房间（房主配置，成员只读）",
        byok_required: true,
      }),
    );
    render(<ModelSettingsPanel />);
    expect(screen.getByText(/仅房主可修改/)).toBeInTheDocument();
    expect(screen.getByText(/额度由房主的 Key 承担/)).toBeInTheDocument();
  });
});

describe("ModelSettingsGateButton", () => {
  beforeEach(() => {
    useOnlineStore.setState({ roomErrorCode: null });
  });

  it("模型未配置拒绝时渲染 CTA 并打开面板", async () => {
    const { openSettings } = await import("../../settings");
    useOnlineStore.setState({ roomErrorCode: "model_not_configured" });
    render(<ModelSettingsGateButton />);
    const cta = screen.getByRole("button", { name: "打开模型设置" });
    fireEvent.click(cta);
    expect(openSettings).toHaveBeenCalled();
  });

  it("房主更换阻断时也渲染 CTA", () => {
    useOnlineStore.setState({ roomErrorCode: "model_route_blocked" });
    render(<ModelSettingsGateButton />);
    expect(
      screen.getByRole("button", { name: "打开模型设置" }),
    ).toBeInTheDocument();
  });

  it("其他拒绝码不渲染", () => {
    useOnlineStore.setState({ roomErrorCode: "room_not_ready" });
    const { container } = render(<ModelSettingsGateButton />);
    expect(container).toBeEmptyDOMElement();
  });
});
