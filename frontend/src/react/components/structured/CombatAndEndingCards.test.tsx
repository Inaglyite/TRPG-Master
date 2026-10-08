import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readServerCapabilities } from "../../../protocol/structured";
import { STRUCTURED_CAPABILITIES_WIRE } from "../../../protocol/structured-fixtures";
import { COMBAT_COMMAND_KINDS } from "../../../protocol/combat";
import { useAppStore } from "../../../state/app-store";
import {
  initialStructuredState,
  useStructuredStore,
} from "../../../state/structured-store";
import {
  resetStructuredTransport,
  setStructuredSender,
} from "../../../structured-transport";
import { CombatAndEndingCards } from "./CombatAndEndingCards";

vi.mock("../../../renderer", () => ({
  onDice: vi.fn(),
  onNarrativeChunk: vi.fn(),
  onNarrativeSegment: vi.fn(),
}));
vi.mock("../../../panels", () => ({ updateCharPanel: vi.fn() }));
vi.mock("../../../api/characterLibrary", () => ({
  previewCaseCharacter: vi.fn(async () => ({
    card: { name: "爱丽丝" },
    revision: 4,
    receipt_digest: "a".repeat(64),
    saved_entry: null,
  })),
  saveCaseCharacter: vi.fn(),
  exportCaseCharacter: vi.fn(),
}));

const combat = {
  active: true,
  encounter_id: "encounter-a",
  round: 2,
  current_actor: "alice",
  turn_order: ["alice", "guard"],
  participants: [
    {
      id: "alice",
      name: "爱丽丝",
      kind: "pc" as const,
      hp: 8,
      max_hp: 10,
      conditions: [],
    },
    {
      id: "guard",
      name: "守卫",
      kind: "npc" as const,
      hp: 6,
      max_hp: 8,
      conditions: [],
    },
  ],
  awaiting_roll: true,
};
const roll = {
  roll_id: "roll-a",
  investigator_id: "alice",
  actor_id: "alice",
  target_id: "guard",
  action_type: "firearm" as const,
  source: "action" as const,
};
const sent = vi.fn((_payload: unknown) => true);

beforeEach(() => {
  resetStructuredTransport();
  sent.mockClear();
  useAppStore.setState({ mode: "local", connection: "connected" });
  useStructuredStore.setState({
    ...initialStructuredState,
    identity: {
      ...initialStructuredState.identity,
      worldId: "battle-world",
      investigatorId: "alice",
      revision: 4,
    },
    capabilities: readServerCapabilities({
      ...STRUCTURED_CAPABILITIES_WIRE,
      commands: [
        ...STRUCTURED_CAPABILITIES_WIRE.commands,
        ...COMBAT_COMMAND_KINDS,
      ],
    }),
    combat,
    combatRoll: roll,
  });
  setStructuredSender(sent);
});
afterEach(() => {
  resetStructuredTransport();
});

describe("战斗与案件卡：只响应服务端待办", () => {
  it("伤势采用人物卡的同一标签，未知作者标记原样显示不丢失", () => {
    useStructuredStore.setState({
      combat: {
        ...combat,
        participants: [
          {
            ...combat.participants[0],
            conditions: ["unconscious", "major_wound", "custom-author-mark"],
          },
          combat.participants[1],
        ],
      },
    });
    render(<CombatAndEndingCards />);
    fireEvent.click(screen.getByText("查看行动顺序与状态"));
    expect(screen.getByText(/HP 8 \/ 10/)).toHaveTextContent(
      "昏迷、重伤、custom-author-mark",
    );
    expect(screen.queryByText(/unconscious/)).not.toBeInTheDocument();
    expect(sent).not.toHaveBeenCalled();
  });
  it("按钮申报类型化动作，不拼自由文本、掷骰或修改战况", () => {
    useStructuredStore.setState((s) => ({
      combatRoll: null,
      combat: { ...combat, awaiting_roll: false },
      capabilities: {
        ...s.capabilities,
        combatActionRequest: true,
        combatWeaponItemId: true,
      },
      items: [
        { id: "weapon-two", label: "手枪（5发）", quantity: 1, operations: [] },
      ],
    }));
    render(<CombatAndEndingCards />);
    const before = useStructuredStore.getState().combat;
    fireEvent.click(screen.getByRole("button", { name: "申报战斗动作" }));
    expect(screen.getByRole("button", { name: "提交申报" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("动作"), {
      target: { value: "firearm" },
    });
    fireEvent.change(screen.getByLabelText("目标"), {
      target: { value: "guard" },
    });
    fireEvent.change(screen.getByLabelText("使用的持有物品"), {
      target: { value: "weapon-two" },
    });
    fireEvent.change(screen.getByLabelText("补充做法（选填）"), {
      target: { value: "掩护同伴" },
    });
    expect(sent).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "提交申报" }));
    expect(sent).toHaveBeenCalledTimes(1);
    expect(sent.mock.calls[0][0]).toMatchObject({
      type: "action_request",
      investigator_id: "alice",
      expected_revision: 4,
      action: {
        kind: "combat",
        encounter_id: "encounter-a",
        action_type: "firearm",
        target_id: "guard",
        approach: "掩护同伴",
        weapon_item_id: "weapon-two",
      },
    });
    expect(useStructuredStore.getState().combat).toBe(before);
    expect(screen.getByRole("button", { name: "申报战斗动作" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "掷骰" })).toBeNull();
    const requestId = (sent.mock.calls[0][0] as { request_id: string })
      .request_id;
    act(() =>
      useStructuredStore.setState((s) => ({
        requests: {
          ...s.requests,
          [requestId]: {
            ...s.requests[requestId],
            status: "declined",
            detail: "请先准备武器。",
          },
        },
      })),
    );
    expect(screen.getByRole("button", { name: "申报战斗动作" })).toBeEnabled();
    expect(screen.getByText("请先准备武器。")).toBeVisible();
    expect(screen.queryByText(/已提交申报，等待主持审核/)).toBeNull();
  });
  it("旧服务端、当前等待响应和非本人行动不能打开申报", () => {
    render(<CombatAndEndingCards />);
    expect(screen.queryByRole("button", { name: "申报战斗动作" })).toBeNull();
    act(() =>
      useStructuredStore.setState({
        combatRoll: null,
        combat: { ...combat, awaiting_roll: false },
      }),
    );
    expect(screen.getByRole("button", { name: "申报战斗动作" })).toBeDisabled();
    act(() =>
      useStructuredStore.setState({
        combat: { ...combat, current_actor: "guard", awaiting_roll: false },
      }),
    );
    expect(screen.queryByRole("button", { name: "申报战斗动作" })).toBeNull();
    expect(sent).not.toHaveBeenCalled();
  });
  it("物品消失时不换枪或退成文字，旧服务端不会伪装支持武器选择", () => {
    useStructuredStore.setState((s) => ({
      combatRoll: null,
      combat: { ...combat, awaiting_roll: false },
      capabilities: {
        ...s.capabilities,
        combatActionRequest: true,
        combatWeaponItemId: true,
      },
      items: [
        {
          id: "weapon-first",
          label: "手枪（3发）",
          quantity: 1,
          operations: [],
        },
        {
          id: "weapon-second",
          label: "手枪（5发）",
          quantity: 1,
          operations: [],
        },
      ],
    }));
    render(<CombatAndEndingCards />);
    fireEvent.click(screen.getByRole("button", { name: "申报战斗动作" }));
    fireEvent.change(screen.getByLabelText("动作"), {
      target: { value: "firearm" },
    });
    fireEvent.change(screen.getByLabelText("目标"), {
      target: { value: "guard" },
    });
    fireEvent.change(screen.getByLabelText("使用的持有物品"), {
      target: { value: "weapon-second" },
    });
    act(() =>
      useStructuredStore.setState((s) => ({
        items: s.items.filter((item) => item.id !== "weapon-second"),
      })),
    );
    expect(screen.getByRole("button", { name: "提交申报" })).toBeDisabled();
    expect(screen.getByText("所选物品已失效，请重新选择。")).toBeVisible();
    expect(sent).not.toHaveBeenCalled();
    act(() =>
      useStructuredStore.setState((s) => ({
        capabilities: { ...s.capabilities, combatWeaponItemId: false },
      })),
    );
    expect(screen.getByLabelText("使用的持有物品")).toBeDisabled();
    expect(sent).not.toHaveBeenCalled();
  });
  it("取消申报不发送；换世界关闭旧表单，不能携带旧目标", () => {
    useStructuredStore.setState((s) => ({
      combatRoll: null,
      combat: { ...combat, awaiting_roll: false },
      capabilities: { ...s.capabilities, combatActionRequest: true },
    }));
    render(<CombatAndEndingCards />);
    fireEvent.click(screen.getByRole("button", { name: "申报战斗动作" }));
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(sent).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "申报战斗动作" }));
    act(() =>
      useStructuredStore.setState((s) => ({
        identity: { ...s.identity, worldId: "different-world" },
      })),
    );
    expect(screen.queryByRole("dialog", { name: "申报战斗动作" })).toBeNull();
    expect(sent).not.toHaveBeenCalled();
  });
  it("调查员防御准备不冒充已掷骰，按钮只提交本人 nonce", () => {
    useStructuredStore.setState({
      combatRoll: { ...roll, source: "pvp_defense" },
    });
    render(<CombatAndEndingCards />);
    expect(screen.getByText(/双方确认前不会产生骰点/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "确认掷骰" }));
    expect(sent.mock.calls[0][0]).toMatchObject({
      kind: "combat_roll",
      payload: { roll_id: "roll-a", response: "roll" },
    });
    expect(screen.getByRole("button", { name: "等待确认…" })).toBeDisabled();
    expect(useStructuredStore.getState().combat?.participants?.[0].hp).toBe(8);
    expect(useStructuredStore.getState().combatResults).toHaveLength(0);
  });
  it("对方玩家看不到私人按钮但能看到公共等待状态", () => {
    useStructuredStore.setState({
      combatRoll: null,
      combatDecision: null,
      combat: { ...combat, awaiting_decision: true, awaiting_roll: false },
    });
    render(<CombatAndEndingCards />);
    expect(screen.getByText("等待决定")).toBeVisible();
    expect(screen.queryByRole("button")).toBeNull();
  });
  it("只读记录展示实际骰点，不产生第二次掷骰请求", () => {
    useStructuredStore.setState({
      combatResults: [
        {
          roll_id: "done",
          investigator_id: "alice",
          encounter_id: "enc",
          round: 1,
          actor_id: "alice",
          target_id: "guard",
          action_type: "firearm",
          response: "roll",
          outcome: "attacker_hit",
          rolls: [
            { actor_id: "alice", role: "attack", roll: 17, level: "困难成功" },
          ],
          damage: { target_id: "guard", amount: 3, hp_before: 8, hp_after: 5 },
        },
      ],
    });
    render(<CombatAndEndingCards />);
    fireEvent.click(screen.getByText("查看已结算战斗记录（1）"));
    expect(screen.getByText(/d100=17/)).toBeVisible();
    expect(screen.getByText(/HP 8 → 5/)).toBeVisible();
    expect(sent).not.toHaveBeenCalled();
  });
  it("指定玩家点击后发送带 nonce 的命令，不改 HP 或提前结算", () => {
    render(<CombatAndEndingCards />);
    fireEvent.click(screen.getByRole("button", { name: "掷骰" }));
    expect(sent).toHaveBeenCalledTimes(1);
    expect(sent.mock.calls[0][0]).toMatchObject({
      type: "command_request",
      kind: "combat_roll",
      payload: { roll_id: "roll-a", response: "roll" },
    });
    expect(screen.getByRole("button", { name: "等待结算…" })).toBeDisabled();
    expect(useStructuredStore.getState().combat?.participants?.[0].hp).toBe(8);
    expect(useStructuredStore.getState().combatRoll).toEqual(roll);
  });
  it("其他玩家只读，不能替指定调查员掷骰", () => {
    useStructuredStore.setState((s) => ({
      identity: { ...s.identity, investigatorId: "bob" },
    }));
    render(<CombatAndEndingCards />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText(/主持不能代选或代掷/)).toBeVisible();
  });
  it("没有认领角色的主持不能代掷", () => {
    useStructuredStore.setState((s) => ({
      identity: { ...s.identity, investigatorId: "" },
    }));
    render(<CombatAndEndingCards />);
    expect(screen.queryByRole("button")).toBeNull();
  });
  it("断线时按钮禁用并给出原因", () => {
    useAppStore.setState({ connection: "disconnected" });
    render(<CombatAndEndingCards />);
    expect(screen.getByRole("button", { name: "掷骰" })).toBeDisabled();
    expect(screen.getByText(/连接已断开/)).toBeVisible();
  });
  it("旧服务端不开放响应能力时不假装可操作", () => {
    useStructuredStore.setState({
      capabilities: readServerCapabilities(STRUCTURED_CAPABILITIES_WIRE),
    });
    render(<CombatAndEndingCards />);
    expect(screen.getByRole("button", { name: "掷骰" })).toBeDisabled();
    expect(screen.getByText(/尚未开放战斗响应/)).toBeVisible();
  });
  it("防御选项原样展示，不根据台词猜测选择", () => {
    useStructuredStore.setState({
      combatRoll: null,
      combatDecision: {
        id: "defense-a",
        kind: "combat_defense",
        responding_investigator_id: "alice",
        title: "守卫向你挥拳",
        description: "选择应对方式。",
        default_option: "dodge",
        options: [
          { id: "dodge", label: "闪避" },
          { id: "fight_back", label: "反击" },
        ],
      },
    });
    render(<CombatAndEndingCards />);
    fireEvent.click(screen.getByRole("button", { name: "闪避" }));
    expect(sent.mock.calls[0][0]).toMatchObject({
      kind: "combat_decide",
      payload: { decision_id: "defense-a", option_id: "dodge" },
    });
  });
  it("结局只显示自己的奖励，不展示主持可读的其他角色履历", () => {
    const receipt = (id: string, reputation: number) => ({
      investigator_id: id,
      character_id: id,
      case: {
        case_id: "case-a",
        world_id: "battle-world",
        ending_type: "good" as const,
        reputation_delta: 2,
      },
      career: { reputation, completed_modules: [], case_history: [] },
    });
    useStructuredStore.setState({
      gameOver: {
        id: "good",
        type: "good",
        title: "真相浮现",
        summary: "案件结束。",
      },
      caseSettlements: [receipt("alice", 12), receipt("bob", 999)],
    });
    render(<CombatAndEndingCards />);
    expect(screen.getByText("真相浮现")).toBeVisible();
    expect(screen.getByText("12")).toBeVisible();
    expect(screen.queryByText("999")).toBeNull();
    expect(screen.queryByTestId("combat-field-record")).toBeNull();
    expect(screen.queryByRole("button", { name: "掷骰" })).toBeNull();
    expect(screen.getAllByTestId("case-character-actions")).toHaveLength(1);
    expect(
      screen.getByRole("button", { name: "保存为新角色" }),
    ).toBeInTheDocument();
  });
  it("收到结束投影后立即撤掉旧掷骰按钮", () => {
    render(<CombatAndEndingCards />);
    act(() =>
      useStructuredStore.setState({
        combat: { ...combat, active: false, outcome: "victory" },
        combatRoll: null,
      }),
    );
    expect(screen.queryByRole("button", { name: "掷骰" })).toBeNull();
    expect(screen.getByText("上一场战斗 · 胜利")).toBeVisible();
  });
});
