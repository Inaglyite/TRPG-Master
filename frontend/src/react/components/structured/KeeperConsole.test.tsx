import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  STRUCTURED_CAPABILITIES_WIRE,
  EVENT_FIXTURES,
  WORLD_ID,
} from "../../../protocol/structured-fixtures";
import { setStructuredSender } from "../../../structured-transport";
import { useAppStore } from "../../../state/app-store";
import { useOnlineStore } from "../../../state/online-store";
import {
  initialStructuredState,
  useStructuredStore,
} from "../../../state/structured-store";
import {
  KeeperConsole,
  keeperAuthorized,
  actionBody,
  actionTitle,
} from "./KeeperConsole";

describe("主持请求摘要按授权候选解释对象", () => {
  const candidates = {
    scenes: [{ id: "medical", name: "医学院" }],
    npcs: [{ id: "shared", name: "医生" }],
    investigators: [{ id: "shared", name: "调查员甲" }],
    clues: [{ id: "letter", name: "密封信件" }],
    items: [{ id: "kit", name: "急救包" }],
  };
  it("折叠标题显示已知对象名称，缺资料不猜名称，自由行动保留原标题", () => {
    expect(
      actionTitle(
        { kind: "move", destination_scene_id: "medical" },
        "原始编号标题",
        candidates,
      ),
    ).toBe("申请前往：医学院");
    expect(
      actionTitle(
        { kind: "move", destination_scene_id: "unknown" },
        "原始编号标题",
        candidates,
      ),
    ).toBe("申请前往：unknown");
    expect(
      actionTitle(
        { kind: "freeform", text: "我想了解medical" },
        "玩家自由行动",
        candidates,
      ),
    ).toBe("玩家自由行动");
    expect(actionTitle(undefined, "尚未同步的请求", candidates)).toBe(
      "尚未同步的请求",
    );
    expect(
      actionTitle(
        {
          kind: "use_item",
          item_id: "kit",
          quantity: 2,
          operation: "custom",
          approach: "包扎",
        },
        "使用物品 kit",
        candidates,
      ),
    ).toBe("申请使用：急救包 ×2");
  });
  it("即兴用法为人类可读说明，未知操作保留原值", () => {
    expect(
      actionBody(
        {
          kind: "use_item",
          item_id: "kit",
          quantity: 1,
          operation: "custom",
          approach: "包扎",
        },
        candidates,
      ),
    ).toContain("用法：即兴用法（custom）");
    expect(
      actionBody(
        {
          kind: "use_item",
          item_id: "kit",
          quantity: 1,
          operation: "unfamiliar",
        },
        candidates,
      ),
    ).toContain("用法：unfamiliar");
  });
  it("移动同时显示场景名与原编号，不解析自由文字", () => {
    expect(
      actionBody({ kind: "move", destination_scene_id: "medical" }, candidates),
    ).toBe("申请前往：医学院（medical）");
    expect(
      actionBody({ kind: "freeform", text: "去medical看看" }, candidates),
    ).toBe("去medical看看");
  });
  it("出示按目标类型解释姓名，保留方式、问题和线索编号", () => {
    expect(
      actionBody(
        {
          kind: "present_clue",
          clue_id: "letter",
          presentation: "describe",
          physical_item_id: null,
          target: { kind: "investigator", id: "shared" },
          question: "认得吗？",
        },
        candidates,
      ),
    ).toContain("目标：调查员甲（shared）");
    const body = actionBody(
      {
        kind: "present_clue",
        clue_id: "letter",
        presentation: "describe",
        physical_item_id: null,
        target: { kind: "npc", id: "shared" },
        question: "认得吗？",
      },
      candidates,
    );
    expect(body).toContain("线索：密封信件（letter）");
    expect(body).toContain("目标：医生（shared）");
    expect(body).toContain("认得吗？");
  });
  it("使用数量不与库存数量混为一谈；未知对象保留编号", () => {
    const body = actionBody(
      {
        kind: "use_item",
        item_id: "kit",
        quantity: 1,
        operation: "custom",
        target: { kind: "npc", id: "missing" },
        approach: "包扎伤口",
      },
      candidates,
    );
    expect(body).toContain("物品：急救包（kit）");
    expect(body).toContain("本次申请数量：1");
    expect(body).toContain("目标：missing");
    expect(body).toContain("包扎伤口");
  });
  it("出示原件摘要包含绑定的实物，未解析目标保留玩家原文", () => {
    const body = actionBody(
      {
        kind: "present_clue",
        clue_id: "letter",
        presentation: "original",
        physical_item_id: "sealed-letter",
        target: { kind: "unresolved", text: "门口的来客" },
      },
      candidates,
    );
    expect(body).toContain("出示实物：sealed-letter");
    expect(body).toContain("目标：门口的来客");
    expect(body).not.toContain("目标：医生");
  });
});

let sent: Record<string, unknown>[] = [];

function enableStructured(keeper: {
  user_id: string | null;
  mode: string | null;
}) {
  useStructuredStore.getState().applyCapabilities(STRUCTURED_CAPABILITIES_WIRE);
  const enrollment = {
    ...EVENT_FIXTURES.snapshot,
    payload: {
      ...EVENT_FIXTURES.snapshot.payload,
      keeper: keeper.user_id ? keeper : { mode: keeper.mode },
    },
  };
  useStructuredStore
    .getState()
    .applySnapshot(enrollment.payload as Record<string, unknown>, WORLD_ID);
}

beforeEach(() => {
  useStructuredStore.setState({ ...initialStructuredState });
  useAppStore.setState({
    mode: "local",
    connection: "connected",
    inputEnabled: true,
    activeWorldId: WORLD_ID,
  });
  useOnlineStore.setState({ activeInvestigatorId: "inv-alice", user: null });
  sent = [];
  setStructuredSender((payload) => {
    sent.push(payload as Record<string, unknown>);
    return true;
  });
});

describe("keeper 授权", () => {
  it("真实持有物候选含NPC/场景，选物品自动带入来源，提交只发既有嵌套协议", () => {
    enableStructured({ user_id: null, mode: "human" });
    useStructuredStore.setState({
      keeperProgress: {
        clues: [],
        clocks: [],
        holdings: {
          holders: [
            { kind: "npc", id: "hidden-npc", name: "未公开的看守" },
            { kind: "scene", id: "library", name: "图书馆" },
          ],
          items: [
            {
              id: "hidden-key",
              label: "库房钥匙",
              quantity: 1,
              holder: { kind: "npc", id: "hidden-npc" },
            },
          ],
        },
      },
    });
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    fireEvent.click(screen.getByTestId("keeper-cmd-transfer_item"));
    expect(
      screen.getByRole("option", { name: /库房钥匙.*未公开的看守/ }),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("物品"), {
      target: { value: "hidden-key" },
    });
    expect(screen.getByLabelText("来源")).toHaveValue("npc/hidden-npc");
    fireEvent.change(screen.getByLabelText("去向"), {
      target: { value: "scene/library" },
    });
    fireEvent.change(screen.getByLabelText("数量"), { target: { value: "1" } });
    fireEvent.click(screen.getByTestId("keeper-submit"));
    expect(sent).toHaveLength(1);
    expect(sent[0].payload).toEqual({
      item_id: "hidden-key",
      quantity: 1,
      from: { kind: "npc", id: "hidden-npc" },
      to: { kind: "scene", id: "library" },
    });
  });
  it("主持作者目录补全未发现线索候选，当前场景的发现对象可用于正式检定", () => {
    enableStructured({ user_id: null, mode: "human" });
    useStructuredStore.setState({
      currentSceneId: "study",
      keeperProgress: {
        clocks: [],
        clues: [
          {
            id: "diary",
            category: "investigation",
            text: "未发现的私人日记",
            discovered: false,
            granted_item: "私人日记",
            item_id: "",
            holder_id: "",
            related_scenes: ["study"],
            rules: [
              {
                index: 0,
                intent: "search",
                skill: "spot_hidden",
                difficulty: "regular",
                requires_success: true,
                approach: "检查暗格",
                sanity_note: "",
                conditions: [],
              },
            ],
          },
        ],
      },
    });
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    fireEvent.click(screen.getByTestId("keeper-cmd-grant_clue"));
    expect(
      document.querySelector('[data-field="clue_id"] option[value="diary"]'),
    ).not.toBeNull();
    expect(
      useStructuredStore.getState().clues.some((c) => c.id === "diary"),
    ).toBe(false);
    fireEvent.click(screen.getByTestId("keeper-cmd-request_check"));
    expect(
      document.querySelector(
        '[data-field="target"] option[value="scene_object:diary"]',
      ),
    ).not.toBeNull();
    act(() => useStructuredStore.setState({ currentSceneId: "library" }));
    expect(
      document.querySelector(
        '[data-field="target"] option[value="scene_object:diary"]',
      ),
    ).toBeNull();
    expect(sent).toEqual([]);
  });
  it("Tab 排除关闭 details、隐藏祖先与 disabled fieldset；其他浮层的 Escape 不关闭主持台", () => {
    enableStructured({ user_id: null, mode: "human" });
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    const dialog = screen.getByRole("dialog", { name: "主持工作台" });
    const probe = document.createElement("div");
    probe.innerHTML =
      '<button>可见末尾</button><details><summary>收起</summary><input aria-label="不可见输入" /></details><div style="display:none"><button>隐藏末尾</button></div><fieldset disabled><button>禁用末尾</button></fieldset>';
    dialog.append(probe);
    const summary = probe.querySelector("summary")!;
    summary.focus();
    fireEvent.keyDown(summary, { key: "Tab" });
    expect(screen.getByRole("button", { name: "关闭主持台" })).toHaveFocus();
    const other = document.createElement("button");
    document.body.append(other);
    other.focus();
    fireEvent.keyDown(other, { key: "Escape" });
    expect(
      screen.getByRole("dialog", { name: "主持工作台" }),
    ).toBeInTheDocument();
    other.remove();
    probe.remove();
  });
  it("房主不等于 keeper：没有 keeper 投影就不授权", () => {
    expect(keeperAuthorized(null, null, "user-owner")).toBe(false);
  });

  it("服务端把当前用户标为 keeper 才授权", () => {
    expect(keeperAuthorized("user-keeper", "human", "user-keeper")).toBe(true);
    expect(keeperAuthorized("user-keeper", "human", "user-owner")).toBe(false);
  });

  it("本地单机没有账号身份时：服务端标出 keeper 即为本机操作者", () => {
    expect(keeperAuthorized(null, "human", null, true)).toBe(true);
    expect(keeperAuthorized("local-operator", "human", null, true)).toBe(true);
    // 云端（非本地）没有身份时不能凭 keeper 投影自封主持。
    expect(keeperAuthorized("someone-else", "human", null, false)).toBe(false);
  });
});

describe("KeeperConsole", () => {
  it("推进时间显示默认等待并显式选择类型，编辑不提交且不改变当前时间", () => {
    enableStructured({ user_id: null, mode: "human" });
    useStructuredStore.setState({ clockMinutes: 60 });
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    fireEvent.click(screen.getByTestId("keeper-cmd-advance_time"));
    const activity = screen.getByLabelText("活动类型");
    expect(activity).toHaveValue("");
    expect(
      screen.getByRole("option", { name: "未指定（按等待计时）" }),
    ).toBeInTheDocument();
    fireEvent.change(activity, { target: { value: "travel" } });
    fireEvent.change(screen.getByLabelText("分钟"), {
      target: { value: "20" },
    });
    fireEvent.change(screen.getByLabelText("原因"), {
      target: { value: "wait 等待（只作说明）" },
    });
    expect(sent).toHaveLength(0);
    expect(useStructuredStore.getState().clockMinutes).toBe(60);
    fireEvent.click(screen.getByTestId("keeper-submit"));
    expect(sent[0]).toMatchObject({
      kind: "advance_time",
      payload: {
        minutes: 20,
        activity: "travel",
        reason: "wait 等待（只作说明）",
      },
    });
    expect(useStructuredStore.getState().clockMinutes).toBe(60);
  });
  it("人物状态仅核对真实角色卡，保留false，提交前不改变角色", () => {
    enableStructured({ user_id: null, mode: "human" });
    useStructuredStore.setState((s) => ({
      capabilities: {
        ...s.capabilities,
        commands: [...s.capabilities.commands, "record_condition"],
      },
      keeperInvestigators: [
        {
          investigatorId: "inv-alice",
          name: "调查员甲",
          occupation: "记者",
          hp: 1,
          maxHp: 10,
          san: 50,
          maxSan: 99,
          attributes: {},
          skills: {},
          conditions: [],
          inventory: [],
        },
      ],
    }));
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    fireEvent.click(screen.getByTestId("keeper-cmd-record_condition"));
    expect(screen.getByLabelText("本次核对的状态")).toHaveTextContent("待核对");
    expect(sent).toHaveLength(0);
    fireEvent.change(screen.getByLabelText("调查员"), {
      target: { value: "inv-alice" },
    });
    fireEvent.change(screen.getByLabelText("状态"), {
      target: { value: "unconscious" },
    });
    expect(screen.getByLabelText("本次核对的状态")).toHaveTextContent("未记录");
    expect(screen.getByLabelText("人物状态参考")).toHaveTextContent(
      "HP 1 / 10",
    );
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("裁定依据"), {
      target: { value: "主持根据已发生事件记录昏迷。" },
    });
    fireEvent.click(screen.getByRole("button", { name: "记录变更" }));
    expect(sent[0]).toMatchObject({
      kind: "record_condition",
      payload: {
        investigator_id: "inv-alice",
        condition: "unconscious",
        operation: "add",
        expected_present: false,
      },
    });
    expect(
      useStructuredStore.getState().keeperInvestigators[0].conditions,
    ).toEqual([]);
  });

  it("真实前状态变化不自动改草稿，手动重新核对；HP0不能解除昏迷", () => {
    enableStructured({ user_id: null, mode: "human" });
    useStructuredStore.setState((s) => ({
      capabilities: {
        ...s.capabilities,
        commands: [...s.capabilities.commands, "record_condition"],
      },
      keeperInvestigators: [
        {
          investigatorId: "inv-alice",
          name: "调查员甲",
          occupation: "记者",
          hp: 0,
          maxHp: 10,
          san: 50,
          maxSan: 99,
          attributes: {},
          skills: {},
          conditions: [],
          inventory: [],
        },
      ],
    }));
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    fireEvent.click(screen.getByTestId("keeper-cmd-record_condition"));
    fireEvent.change(screen.getByLabelText("调查员"), {
      target: { value: "inv-alice" },
    });
    fireEvent.change(screen.getByLabelText("状态"), {
      target: { value: "unconscious" },
    });
    fireEvent.change(screen.getByLabelText("变更"), {
      target: { value: "remove" },
    });
    fireEvent.change(screen.getByLabelText("裁定依据"), {
      target: { value: "希望解除昏迷，但仍须核对生命值。" },
    });
    act(() =>
      useStructuredStore.setState((s) => ({
        keeperInvestigators: s.keeperInvestigators.map((sheet) => ({
          ...sheet,
          conditions: ["unconscious", "major_wound"],
        })),
      })),
    );
    expect(screen.getByLabelText("本次核对的状态")).toHaveTextContent("未记录");
    expect(screen.getByText("记录已变化，请重新核对")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "记录变更" }));
    expect(sent).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "核对当前记录" }));
    expect(screen.getByLabelText("本次核对的状态")).toHaveTextContent("存在");
    fireEvent.click(screen.getByRole("button", { name: "记录变更" }));
    expect(screen.getByRole("alert")).toHaveTextContent("HP 大于 0");
    expect(sent).toHaveLength(0);
    act(() =>
      useStructuredStore.setState((s) => ({
        keeperInvestigators: s.keeperInvestigators.map((sheet) => ({
          ...sheet,
          hp: 1,
        })),
      })),
    );
    fireEvent.click(screen.getByRole("button", { name: "记录变更" }));
    expect(sent[0]).toMatchObject({
      kind: "record_condition",
      payload: { expected_present: true, operation: "remove" },
    });
    expect(
      useStructuredStore.getState().keeperInvestigators[0].conditions,
    ).toEqual(["unconscious", "major_wound"]);
  });

  it("人类裁定使用真实类型与冻结旧值，提交不乐观改变状态", () => {
    enableStructured({ user_id: null, mode: "human" });
    useStructuredStore.setState((s) => ({
      capabilities: {
        ...s.capabilities,
        commands: [...s.capabilities.commands, "record_ruling"],
      },
      keeperRulings: {
        flags: [{ id: "sealed", type: "boolean", value: false }],
        recent: [],
        eligible_endings: [],
      },
    }));
    const sent = vi.fn((_frame: unknown) => true);
    setStructuredSender(sent);
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    fireEvent.click(screen.getByTestId("keeper-cmd-record_ruling"));
    fireEvent.change(screen.getByLabelText("剧情条件（模组编号）"), {
      target: { value: "sealed" },
    });
    fireEvent.click(screen.getByRole("checkbox", { name: /裁定后的状态/ }));
    fireEvent.change(screen.getByLabelText("裁定依据"), {
      target: { value: "仪式完成，主持确认。" },
    });
    fireEvent.click(screen.getByTestId("keeper-submit"));
    expect(sent.mock.calls[0][0]).toMatchObject({
      kind: "record_ruling",
      payload: {
        flag_id: "sealed",
        expected_before: false,
        value: true,
        basis: "仪式完成，主持确认。",
      },
    });
    expect(useStructuredStore.getState().keeperRulings?.flags[0].value).toBe(
      false,
    );
  });
  it("结局资格只准备表单，不偷偷结束游戏", () => {
    enableStructured({ user_id: null, mode: "human" });
    useStructuredStore.setState((s) => ({
      capabilities: {
        ...s.capabilities,
        commands: [...s.capabilities.commands, "end_game"],
      },
      keeperRulings: {
        flags: [],
        recent: [],
        eligible_endings: [
          { id: "seal", title: "封印完成", ending_type: "good" },
        ],
      },
    }));
    const sent = vi.fn((_frame: unknown) => true);
    setStructuredSender(sent);
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    fireEvent.click(screen.getByText("剧情裁定与结局资格"));
    fireEvent.click(screen.getByRole("button", { name: "准备结算" }));
    expect(screen.getByLabelText("模组结局 ID")).toHaveValue("seal");
    expect(sent).not.toHaveBeenCalled();
  });
  it("记忆查询完整发送超过20字的合法文本，接受中文逗号", () => {
    enableStructured({ user_id: null, mode: "human" });
    useStructuredStore.setState((state) => ({
      capabilities: { ...state.capabilities, memoryQuery: true },
    }));
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    const text = "调查员此前在停尸间确认的旧伤以及医生解释的证据来源";
    expect(screen.getByLabelText("文本（最多200字）").tagName).toBe("TEXTAREA");
    fireEvent.change(screen.getByLabelText("文本（最多200字）"), {
      target: { value: text },
    });
    fireEvent.change(
      screen.getByLabelText("主题（逗号分隔，最多6个，每个40字）"),
      { target: { value: "医生，旧伤" } },
    );
    fireEvent.click(screen.getByTestId("keeper-memory-submit"));
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({
      type: "memory_query",
      filters: { text, topics: ["医生", "旧伤"] },
    });
  });
  it("超过六个记忆主题不截断或发送，保留原输入", () => {
    enableStructured({ user_id: null, mode: "human" });
    useStructuredStore.setState((state) => ({
      capabilities: { ...state.capabilities, memoryQuery: true },
    }));
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    const topics = screen.getByLabelText("主题（逗号分隔，最多6个，每个40字）");
    fireEvent.change(topics, { target: { value: "一,二,三,四,五,六,七" } });
    fireEvent.click(screen.getByTestId("keeper-memory-submit"));
    expect(screen.getByRole("alert")).toHaveTextContent("主题最多 6 个");
    expect(topics).toHaveValue("一,二,三,四,五,六,七");
    expect(sent).toHaveLength(0);
    act(() => useAppStore.setState({ connection: "connecting" }));
    expect(screen.getByTestId("keeper-memory-submit")).toBeDisabled();
  });
  it("撤销云端 can_keeper 后隐藏旧主持资料，重新授权不重开旧表单", () => {
    enableStructured({ user_id: "keeper-user", mode: "human" });
    useAppStore.setState({ mode: "online" });
    useOnlineStore.setState({
      user: { id: "keeper-user" } as any,
      members: [{ user_id: "keeper-user", can_keeper: true }] as any,
    });
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    expect(
      screen.getByRole("dialog", { name: "主持工作台" }),
    ).toBeInTheDocument();
    act(() =>
      useOnlineStore.setState({
        members: [{ user_id: "keeper-user", can_keeper: false }] as any,
      }),
    );
    expect(screen.queryByRole("dialog", { name: "主持工作台" })).toBeNull();
    expect(screen.queryByTestId("btn-keeper-console")).toBeNull();
    act(() =>
      useOnlineStore.setState({
        members: [{ user_id: "keeper-user", can_keeper: true }] as any,
      }),
    );
    expect(screen.getByTestId("btn-keeper-console")).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "主持工作台" })).toBeNull();
    expect(sent).toHaveLength(0);
  });
  it("命令能力撤回禁用已打开的表单，草稿保留且恢复后可编辑", () => {
    enableStructured({ user_id: null, mode: "human" });
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    act(() =>
      useStructuredStore.setState((state) => ({
        capabilities: { ...state.capabilities, commands: [] },
      })),
    );
    expect(screen.getByTestId("keeper-submit")).toBeDisabled();
    expect(screen.getByTestId("keeper-cmd-grant_clue")).toBeDisabled();
    expect(screen.getByText(/服务端未开放该主持操作/)).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("keeper-submit"));
    expect(sent).toHaveLength(0);
    act(() =>
      useStructuredStore.setState((state) => ({
        capabilities: { ...state.capabilities, commands: ["publish_message"] },
      })),
    );
    expect(screen.getByTestId("keeper-submit")).toBeEnabled();
  });
  it("断线与同步中不展示可提交表单，恢复连接后恢复", () => {
    enableStructured({ user_id: null, mode: "human" });
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    for (const connection of ["disconnected", "connecting"] as const) {
      act(() => useAppStore.setState({ connection }));
      expect(screen.queryByTestId("keeper-submit")).toBeNull();
      expect(
        screen.getByText(
          connection === "connecting"
            ? "正在连接并同步权威状态，请求未提交。"
            : "连接已断开，请求未提交。",
        ),
      ).toBeInTheDocument();
      expect(screen.getByTestId("keeper-cmd-publish_message")).toBeDisabled();
    }
    act(() => useAppStore.setState({ connection: "connected" }));
    expect(screen.getByTestId("keeper-submit")).toBeEnabled();
    expect(sent).toHaveLength(0);
  });
  it("renders waiting conditions and ruling notes as genuine multi-line fields", () => {
    enableStructured({ user_id: null, mode: "human" });
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    fireEvent.click(screen.getByTestId("keeper-cmd-resolve_intent"));
    const conditions =
      screen.getByLabelText("已告知条件（一行一条，避免重复劝留）");
    expect(conditions.tagName).toBe("TEXTAREA");
    expect(screen.getByLabelText("尚未执行什么").tagName).toBe("TEXTAREA");
    expect(screen.getByLabelText("说明").tagName).toBe("TEXTAREA");
    fireEvent.change(conditions, {
      target: { value: "医生需要确认身份\n先询问开放时间" },
    });
    expect(conditions).toHaveValue("医生需要确认身份\n先询问开放时间");
    expect(sent).toHaveLength(0);
  });
  it("optional enum fields visibly remain unspecified instead of displaying a fake success or thread operation", () => {
    enableStructured({ user_id: null, mode: "human" });
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    fireEvent.click(screen.getByTestId("keeper-cmd-resolve_intent"));
    expect(screen.getByLabelText("领域结果")).toHaveValue("");
    expect(screen.getByLabelText("交互线程操作（可选）")).toHaveValue("");
    fireEvent.change(screen.getByLabelText("领域结果"), {
      target: { value: "success" },
    });
    expect(screen.getByLabelText("领域结果")).toHaveValue("success");
    fireEvent.change(screen.getByLabelText("领域结果"), {
      target: { value: "" },
    });
    expect(screen.getByLabelText("领域结果")).toHaveValue("");
    expect(sent).toHaveLength(0);
  });
  it.each([
    {
      target: { kind: "unresolved", text: "门边生锈的锁" },
      select: "unresolved:",
    },
    {
      target: { kind: "scene_object", id: "door:lock" },
      select: "scene_object:door:lock",
    },
  ])(
    "retains $target.kind item targets through preparation and explicit submission",
    ({ target, select }) => {
      enableStructured({ user_id: null, mode: "human" });
      useStructuredStore.setState((state) => ({
        targets: [
          ...state.targets,
          { kind: "scene_object", id: "door:lock", name: "生锈的门锁" },
        ],
      }));
      useStructuredStore.getState().applyEvent({
        ...EVENT_FIXTURES.snapshot,
        type: "intent_pending",
        payload: {
          request_id: "target-item",
          investigator_id: "inv-alice",
          summary: "使用物品",
          action: {
            kind: "use_item",
            item_id: "item_bandage",
            quantity: 1,
            operation: "custom",
            approach: "将绷带绕在锁边避免划伤",
            target,
          },
        },
      });
      render(<KeeperConsole />);
      fireEvent.click(screen.getByTestId("btn-keeper-console"));
      fireEvent.click(screen.getByRole("button", { name: "准备使用" }));
      expect(screen.getByLabelText("目标（可选）")).toHaveValue(select);
      if (target.kind === "unresolved")
        expect(screen.getByLabelText("描述对象")).toHaveValue(target.text);
      expect(sent).toHaveLength(0);
      fireEvent.click(screen.getByTestId("keeper-submit"));
      expect(sent).toHaveLength(1);
      expect((sent[0].payload as { target: unknown }).target).toEqual(target);
    },
  );
  it("prepares typed combat request without executing and preserves original cause on explicit approval", () => {
    enableStructured({ user_id: null, mode: "human" });
    useStructuredStore.setState((s) => ({
      capabilities: {
        ...s.capabilities,
        commands: [...s.capabilities.commands, "combat_action"],
        combatWeaponItemId: true,
      },
      keeperInvestigators: ["inv-alice", "inv-bob"].map((id) => ({
        investigatorId: id,
        name: id === "inv-alice" ? "爱丽丝" : "鲍勃",
        occupation: "记者",
        hp: 8,
        maxHp: 10,
        san: 60,
        maxSan: 99,
        attributes: {},
        skills: {},
        conditions: [],
        inventory: [
          {
            id: id === "inv-alice" ? "weapon-second" : "bob-weapon",
            label: "手枪（5发）",
            quantity: 1,
            operations: [],
          },
        ],
      })),
      combat: {
        active: true,
        participants: [
          {
            id: "inv-alice",
            name: "爱丽丝",
            kind: "pc",
            hp: 8,
            max_hp: 10,
            conditions: [],
          },
          {
            id: "guard",
            name: "守卫",
            kind: "npc",
            hp: 8,
            max_hp: 10,
            conditions: [],
          },
          {
            id: "inv-bob",
            name: "鲍勃",
            kind: "pc",
            hp: 8,
            max_hp: 10,
            conditions: [],
          },
        ],
      },
    }));
    useStructuredStore.getState().applyEvent({
      ...EVENT_FIXTURES.snapshot,
      type: "intent_pending",
      payload: {
        request_id: "combat-intent",
        investigator_id: "inv-alice",
        summary: "申报战斗动作",
        action: {
          kind: "combat",
          encounter_id: "encounter-a",
          action_type: "firearm",
          target_id: "guard",
          approach: "掩护同伴",
          weapon_item_id: "weapon-second",
        },
      },
    });
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    fireEvent.click(screen.getByRole("button", { name: "准备战斗动作" }));
    expect(screen.getByLabelText("行动者")).toHaveValue("inv-alice");
    expect(screen.getByLabelText("目标", { exact: true })).toHaveValue("guard");
    expect(screen.getByLabelText("动作")).toHaveValue("firearm");
    const weaponSelect = screen.getByLabelText("武器物品") as HTMLSelectElement;
    expect(weaponSelect).toHaveValue("weapon-second");
    expect([...weaponSelect.options].map((o) => o.value)).not.toContain(
      "bob-weapon",
    );
    expect(sent).toHaveLength(0);
    fireEvent.click(screen.getByTestId("keeper-submit"));
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({
      kind: "combat_action",
      cause_id: "combat-intent",
      payload: {
        actor_id: "inv-alice",
        target_id: "guard",
        action_type: "firearm",
        description: "掩护同伴",
        weapon_item_id: "weapon-second",
      },
    });
    fireEvent.change(screen.getByLabelText("行动者"), {
      target: { value: "inv-bob" },
    });
    const changedWeaponSelect = screen.getByLabelText(
      "武器物品",
    ) as HTMLSelectElement;
    expect(changedWeaponSelect).toHaveValue("");
    expect([...changedWeaponSelect.options].map((o) => o.value)).toContain(
      "bob-weapon",
    );
    expect([...changedWeaponSelect.options].map((o) => o.value)).not.toContain(
      "weapon-second",
    );
  });
  it("prepares the exact item request without consuming, settling or submitting it", () => {
    enableStructured({ user_id: null, mode: "human" });
    useStructuredStore.getState().applyEvent({
      ...EVENT_FIXTURES.snapshot,
      type: "intent_pending",
      payload: {
        request_id: "item-player-1",
        investigator_id: "inv-alice",
        summary: "使用随身物品",
        action: {
          kind: "use_item",
          item_id: "item_bandage",
          quantity: 2,
          operation: "包扎",
          target: { kind: "npc", id: "john_whitcroft" },
          approach: "先清洗伤口",
        },
      },
    });
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    fireEvent.click(screen.getByRole("button", { name: "准备使用" }));
    expect(screen.getByLabelText("调查员")).toHaveValue("inv-alice");
    expect(screen.getByLabelText("物品")).toHaveValue("item_bandage");
    expect(screen.getByLabelText("数量")).toHaveValue(2);
    expect(screen.getByLabelText("用法")).toHaveValue("包扎");
    expect(screen.getByLabelText("补充做法")).toHaveValue("先清洗伤口");
    expect(screen.getByLabelText("扣减物品")).not.toBeChecked();
    expect(sent).toHaveLength(0);
    expect(useStructuredStore.getState().requests["item-player-1"].status).toBe(
      "queued",
    );
    // Original known target survives preparation; no free text intent parser.
    expect(screen.getByLabelText("目标（可选）")).toHaveValue(
      "npc:john_whitcroft",
    );
    act(() =>
      useStructuredStore.setState((state) => ({
        capabilities: {
          ...state.capabilities,
          commands: state.capabilities.commands.filter(
            (kind) => kind !== "use_item",
          ),
        },
      })),
    );
    expect(screen.getByRole("button", { name: "准备使用" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "准备使用" }));
    expect(sent).toHaveLength(0);
  });
  it("offers all exact skills only for the selected investigator", () => {
    enableStructured({ user_id: null, mode: "human" });
    useStructuredStore.getState().applySnapshot({
      keeper_investigators: [
        { investigator_id: "alice", name: "甲", skills: { rare_skill: 17 } },
        { investigator_id: "bob", name: "乙", skills: { other_skill: 71 } },
      ],
    });
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    fireEvent.click(screen.getByTestId("keeper-cmd-request_check"));
    fireEvent.change(screen.getByLabelText("调查员"), {
      target: { value: "alice" },
    });
    expect(
      document.querySelector(
        '#keeper-skill-options option[value="rare_skill"]',
      ),
    ).toHaveTextContent("17%");
    expect(
      document.querySelector(
        '#keeper-skill-options option[value="other_skill"]',
      ),
    ).toBeNull();
    fireEvent.change(screen.getByLabelText("调查员"), {
      target: { value: "bob" },
    });
    expect(
      document.querySelector(
        '#keeper-skill-options option[value="other_skill"]',
      ),
    ).toHaveTextContent("71%");
    expect(
      document.querySelector(
        '#keeper-skill-options option[value="rare_skill"]',
      ),
    ).toBeNull();
    expect(sent).toHaveLength(0);
  });
  it("receives a full player request live; quick actions only prepare explicit forms", () => {
    enableStructured({ user_id: null, mode: "human" });
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    const text =
      "我详细说明自己准备调查的方式。".repeat(12) +
      "最后这句话不能被摘要截断。";
    act(() =>
      useStructuredStore.getState().applyEvent({
        ...EVENT_FIXTURES.snapshot,
        type: "intent_pending",
        payload: {
          request_id: "player-long",
          investigator_id: "inv-alice",
          summary: text.slice(0, 80),
          action: { kind: "freeform", text },
        },
      }),
    );
    expect(screen.getByText(text)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "准备检定" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "准备检定" }));
    expect(screen.getByLabelText("关联请求")).toHaveValue("player-long");
    fireEvent.click(screen.getByRole("button", { name: "准备裁定" }));
    expect(screen.getByLabelText("玩家请求")).toHaveValue("player-long");
    expect(screen.getByLabelText("领域结果")).toHaveValue("not_executed");
    fireEvent.click(screen.getByRole("button", { name: "准备回应" }));
    expect(
      document.querySelector('[data-field="audience_kind"] select'),
    ).toHaveValue("investigators");
    expect(screen.getByLabelText("接收调查员")).toHaveValue("inv-alice");
    expect(sent).toHaveLength(0);
    act(() =>
      useStructuredStore.getState().applyEvent({
        ...EVENT_FIXTURES.snapshot,
        type: "action_status",
        cause_request_id: "player-long",
        payload: {
          request_id: "player-long",
          status: "completed",
          outcome: "not_executed",
        },
      }),
    );
    expect(
      screen.queryByTestId("keeper-pending-request"),
    ).not.toBeInTheDocument();
  });
  it("offers the correct character list for each speech identity and clears stale IDs", () => {
    enableStructured({ user_id: null, mode: "human" });
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    expect(screen.queryByLabelText("身份 ID")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("发言身份"), {
      target: { value: "npc" },
    });
    fireEvent.change(screen.getByLabelText("身份 ID"), {
      target: { value: "john_whitcroft" },
    });
    fireEvent.change(screen.getByLabelText("发言身份"), {
      target: { value: "investigator" },
    });
    expect(screen.getByLabelText("身份 ID")).toHaveValue("");
    expect(
      screen.getByRole("option", { name: "爱丽丝（inv-alice）" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("option", { name: /john_whitcroft/ }),
    ).not.toBeInTheDocument();
    expect(sent).toHaveLength(0);
  });
  it("keeps namespace colons in the selected investigator target", () => {
    enableStructured({ user_id: null, mode: "human" });
    useStructuredStore.setState({
      targets: [
        { kind: "investigator", id: "default:调查员乙", name: "调查员乙" },
      ],
    });
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    fireEvent.click(screen.getByTestId("keeper-cmd-present_information"));
    fireEvent.change(screen.getByLabelText("线索"), {
      target: { value: "clue_death_certificate" },
    });
    fireEvent.change(screen.getByLabelText("目标"), {
      target: { value: "investigator:default:调查员乙" },
    });
    fireEvent.click(screen.getByTestId("keeper-submit"));
    expect((sent[0].payload as { target: unknown }).target).toEqual({
      kind: "investigator",
      id: "default:调查员乙",
    });
  });
  it("shows Chinese choices without changing protocol values and confirms only server outcomes", () => {
    enableStructured({ user_id: null, mode: "human" });
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    expect(screen.getByRole("option", { name: "守秘人旁白" })).toHaveValue(
      "keeper",
    );
    expect(screen.getByRole("option", { name: "所有人" })).toHaveValue(
      "public",
    );
    fireEvent.change(screen.getByRole("textbox", { name: "内容" }), {
      target: { value: "雨渐渐停了。" },
    });
    fireEvent.click(screen.getByTestId("keeper-submit"));
    expect(screen.getByRole("status")).toHaveTextContent("等待服务端确认");
    expect(screen.getByTestId("keeper-submit")).toBeDisabled();
    fireEvent.click(screen.getByTestId("keeper-submit"));
    expect(sent).toHaveLength(1);
    expect(screen.getByRole("status")).not.toHaveTextContent(
      "服务端已确认提交",
    );
    const id = String(sent[0].command_id);
    act(() =>
      useStructuredStore.getState().applyEvent({
        ...EVENT_FIXTURES.actionCompleted,
        type: "action_ack",
        payload: { request_id: id, status: "queued" },
      }),
    );
    expect(screen.getByRole("status")).toHaveTextContent(
      "服务端已收件，等待本次命令结算",
    );
    expect(screen.getByRole("status")).not.toHaveTextContent(
      "服务端已确认提交",
    );
    expect(screen.getByTestId("keeper-submit")).toBeDisabled();
    act(() =>
      useStructuredStore.getState().applyEvent({
        ...EVENT_FIXTURES.actionCompleted,
        payload: { command_id: id, status: "completed", detail: "旁白已发布" },
      }),
    );
    expect(screen.getByRole("status")).toHaveTextContent("服务端已确认提交");
    expect(screen.getByRole("status")).toHaveTextContent("旁白已发布");
    expect(screen.getByTestId("keeper-submit")).toBeEnabled();
  });

  it("keeps the narration draft and displays the actual server rejection", () => {
    enableStructured({ user_id: null, mode: "human" });
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    fireEvent.change(screen.getByRole("textbox", { name: "内容" }), {
      target: { value: "未发布的草稿" },
    });
    fireEvent.click(screen.getByTestId("keeper-submit"));
    act(() =>
      useStructuredStore
        .getState()
        .applyRequestError(
          String(sent[0].command_id),
          "revision_conflict",
          "世界版本已变化",
          true,
        ),
    );
    expect(screen.getByRole("status")).toHaveTextContent("未完成");
    expect(screen.getByRole("status")).not.toHaveTextContent("已确认提交");
    expect(screen.getByRole("textbox", { name: "内容" })).toHaveValue(
      "未发布的草稿",
    );
  });

  it("selects actual investigator recipients without typing their IDs", () => {
    enableStructured({ user_id: null, mode: "human" });
    useStructuredStore.setState({
      targets: [
        { kind: "investigator", id: "inv-one", name: "调查员甲" },
        { kind: "investigator", id: "inv-two", name: "调查员乙" },
      ],
    });
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    fireEvent.click(screen.getByTestId("keeper-cmd-grant_clue"));
    fireEvent.click(screen.getByRole("checkbox", { name: "调查员甲" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "调查员乙" }));
    expect(screen.getByRole("textbox", { name: "接收调查员" })).toHaveValue(
      "inv-one,inv-two",
    );
    fireEvent.click(screen.getByRole("checkbox", { name: "调查员甲" }));
    expect(screen.getByRole("textbox", { name: "接收调查员" })).toHaveValue(
      "inv-two",
    );
    expect(sent).toHaveLength(0);
  });

  it("opens ready for narration and preserves its draft while distributing a clue", () => {
    enableStructured({ user_id: null, mode: "human" });
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    const draft = "雨声落在窗沿。\n医生将一份病历放到你的面前。";
    fireEvent.change(screen.getByRole("textbox", { name: "内容" }), {
      target: { value: draft },
    });
    fireEvent.click(screen.getByTestId("keeper-cmd-grant_clue"));
    fireEvent.click(screen.getByTestId("keeper-cmd-publish_message"));
    expect(screen.getByRole("textbox", { name: "内容" })).toHaveValue(draft);
    expect(screen.getByRole("button", { name: "发布叙事" })).toBeEnabled();
    expect(sent).toHaveLength(0);
  });

  it("clears private drafts and closes the workspace when changing worlds", () => {
    enableStructured({ user_id: null, mode: "human" });
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    fireEvent.change(screen.getByRole("textbox", { name: "内容" }), {
      target: { value: "旧世界的主持秘密" },
    });
    act(() =>
      useStructuredStore.setState({
        identity: {
          ...useStructuredStore.getState().identity,
          worldId: "another-world",
        },
      }),
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    expect(screen.getByRole("textbox", { name: "内容" })).toHaveValue("");
    expect(sent).toHaveLength(0);
  });

  it("没有 keeper_console 能力时不渲染入口", () => {
    render(<KeeperConsole />);
    expect(screen.queryByTestId("btn-keeper-console")).not.toBeInTheDocument();
  });

  it("有能力和 keeper 身份时显示入口与命令清单", () => {
    enableStructured({ user_id: "user-keeper", mode: "human" });
    useOnlineStore.setState({
      user: { id: "user-keeper", username: "keeper" },
    });
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    for (const kind of [
      "publish_message",
      "grant_clue",
      "request_check",
      "adjust_stat",
      "move_party",
      "advance_time",
      "resolve_intent",
      "present_handout",
      "set_npc_presence",
      "record_fact",
      "transfer_item",
      "use_item",
      "resolve_check",
      "present_information",
    ]) {
      expect(screen.getByTestId(`keeper-cmd-${kind}`)).toBeInTheDocument();
    }
  });

  it("非 keeper 不显示主持入口，即使服务器支持主持命令", () => {
    enableStructured({ user_id: "user-keeper", mode: "human" });
    useOnlineStore.setState({
      user: { id: "user-player", username: "player" },
    });
    render(<KeeperConsole />);
    expect(screen.queryByTestId("btn-keeper-console")).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(sent).toHaveLength(0);
  });

  it("NPC 发言提交的 payload 使用 M0 speaker 形态", () => {
    enableStructured({ user_id: null, mode: "human" });
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    fireEvent.click(screen.getByTestId("keeper-cmd-publish_message"));

    fireEvent.change(
      document.querySelector('[data-field="speaker_kind"] select')!,
      {
        target: { value: "npc" },
      },
    );
    fireEvent.change(
      document.querySelector(
        '[data-field="speaker_id"] select, [data-field="speaker_id"] input',
      )!,
      {
        target: { value: "john_whitcroft" },
      },
    );
    fireEvent.change(document.querySelector('[data-field="text"] textarea')!, {
      target: { value: "停尸房不对外开放。" },
    });
    fireEvent.click(screen.getByTestId("keeper-submit"));

    expect(sent).toHaveLength(1);
    const frame = sent[0];
    expect(frame.type).toBe("command_request");
    expect(frame.kind).toBe("publish_message");
    expect(frame.protocol_version).toBe(1);
    expect(frame.command_id).toBeTruthy();
    expect(frame.expected_revision).toBe(12);
    expect(frame.payload).toEqual({
      speaker: { kind: "npc", id: "john_whitcroft" },
      audience: { kind: "public" },
      text: "停尸房不对外开放。",
    });
  });

  it("必填缺失时不发帧，并在表单里报错（草稿保留）", () => {
    enableStructured({ user_id: null, mode: "human" });
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    fireEvent.click(screen.getByTestId("keeper-cmd-grant_clue"));
    fireEvent.change(
      document.querySelector(
        '[data-field="clue_id"] select, [data-field="clue_id"] input',
      )!,
      {
        target: { value: "clue_death_certificate" },
      },
    );
    fireEvent.click(screen.getByTestId("keeper-submit"));

    expect(sent).toHaveLength(0);
    expect(screen.getByRole("alert")).toHaveTextContent("接收调查员");
    // 草稿仍在：线索选择没有被清空。
    expect(
      document.querySelector(
        '[data-field="clue_id"] select, [data-field="clue_id"] input',
      )!,
    ).toHaveValue("clue_death_certificate");
  });

  it("私发线索带接收者与依据；提交后提示“接收不等于执行成功”", () => {
    enableStructured({ user_id: null, mode: "human" });
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    fireEvent.click(screen.getByTestId("keeper-cmd-grant_clue"));
    fireEvent.change(
      document.querySelector(
        '[data-field="clue_id"] select, [data-field="clue_id"] input',
      )!,
      {
        target: { value: "clue_death_certificate" },
      },
    );
    fireEvent.change(
      document.querySelector(
        '[data-field="recipient_investigator_ids"] input[type="text"]',
      )!,
      { target: { value: "inv-alice" } },
    );
    fireEvent.change(
      document.querySelector(
        '[data-field="basis"] select, [data-field="basis"] input',
      )!,
      {
        target: { value: "医生当面说明" },
      },
    );
    fireEvent.click(screen.getByTestId("keeper-submit"));

    expect(sent[0].kind).toBe("grant_clue");
    expect(sent[0].payload).toEqual({
      clue_id: "clue_death_certificate",
      recipient_investigator_ids: ["inv-alice"],
      basis: "医生当面说明",
    });
    expect(screen.getByText(/不代表执行成功/)).toBeInTheDocument();
  });

  it("待处理行动区显示玩家请求的状态", () => {
    enableStructured({ user_id: null, mode: "human" });
    useStructuredStore.getState().registerOutgoing({
      requestId: "req-player-1",
      kind: "move",
      label: "前往",
      payload: {},
      digest: "",
    });
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    expect(screen.getByText("req-player-1")).toBeInTheDocument();
    expect(screen.getByText("待处理行动")).toBeInTheDocument();
  });

  it("收尾表单的 request_id / thread_id 提供候选，不允许主持手抄 ID", () => {
    enableStructured({ user_id: null, mode: "human" });
    useStructuredStore.getState().registerOutgoing({
      requestId: "req-player-1",
      kind: "move",
      label: "前往",
      payload: {},
      digest: "",
    });
    useStructuredStore.getState().applySnapshot(
      {
        ...EVENT_FIXTURES.snapshot.payload,
        interactions: [
          {
            thread_id: "thr_open_1",
            status: "open",
            investigator_id: "inv-alice",
            pending_action: { kind: "move", note: "尚未出发前往医学院" },
            disclosed: [],
            waiting_on: "inv-alice",
            origin_request_id: "req-player-1",
            last_request_id: "req-player-1",
          },
        ],
      } as Record<string, unknown>,
      WORLD_ID,
    );
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    fireEvent.click(screen.getByTestId("keeper-cmd-resolve_intent"));

    const requestOptions = Array.from(
      document.querySelectorAll('[data-field="request_id"] option'),
    ).map((option) => (option as HTMLOptionElement).value);
    expect(requestOptions).toContain("req-player-1");

    const threadOptions = Array.from(
      document.querySelectorAll('[data-field="thread_id"] option'),
    ).map((option) => (option as HTMLOptionElement).value);
    expect(threadOptions).toContain("thr_open_1");
    // 已关闭的线程不再出现在候选里（不会误关）
    expect(threadOptions).not.toContain("thr_closed");
  });

  it("授权资料：keeper 看到完整线索登记表；未提供主持资料时如实说明", () => {
    enableStructured({ user_id: null, mode: "human" });
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    expect(screen.getByText("授权模组资料")).toBeInTheDocument();
    // 快照里的线索是 keeper 视角的完整登记表。
    expect(screen.getByText(/clue_death_certificate/)).toBeInTheDocument();
    expect(screen.getByTestId("keeper-material-missing")).toHaveTextContent(
      "本模组暂未提供额外的主持资料",
    );
  });

  it("服务端下发 keeper_material 时直接显示", () => {
    enableStructured({ user_id: null, mode: "human" });
    useStructuredStore.getState().applySnapshot(
      {
        ...EVENT_FIXTURES.snapshot.payload,
        keeper_material: [
          { title: "惠特克罗夫特的秘密", text: "他在压力下签署了死亡证明。" },
        ],
      } as Record<string, unknown>,
      WORLD_ID,
    );
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    expect(screen.getByTestId("keeper-material")).toHaveTextContent(
      "惠特克罗夫特的秘密",
    );
    expect(screen.getByTestId("keeper-material")).toHaveTextContent(
      "他在压力下签署了死亡证明。",
    );
  });

  it("提供存档与续团入口", () => {
    enableStructured({ user_id: null, mode: "human" });
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    expect(screen.getByTestId("keeper-save")).toBeInTheDocument();
    expect(screen.getByTestId("keeper-load")).toBeInTheDocument();
    expect(screen.getByTestId("keeper-save-panel")).toBeInTheDocument();
  });

  it("协议不可用时命令禁用并显示原因", () => {
    enableStructured({ user_id: null, mode: "human" });
    useStructuredStore
      .getState()
      .setProtocolNotice("服务端使用不同版本的协议。");
    render(<KeeperConsole />);
    fireEvent.click(screen.getByTestId("btn-keeper-console"));
    expect(screen.getByRole("alert")).toHaveTextContent("不同版本的协议");
    const advance = screen.getByTestId("keeper-cmd-advance_time");
    expect(advance).toBeDisabled();
    expect(advance).toHaveAttribute("title", "服务端使用不同版本的协议。");
    // 被阻断时不会出现可提交的表单。
    expect(screen.queryByTestId("keeper-submit")).not.toBeInTheDocument();
  });
});
