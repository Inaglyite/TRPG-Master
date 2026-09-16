import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  EVENT_FIXTURES,
  HUMAN_ONLY_CAPABILITIES,
  LEGACY_CAPABILITIES,
  STRUCTURED_CAPABILITIES_WIRE,
  WORLD_ID,
} from "../../../protocol/structured-fixtures";
import { useAppStore } from "../../../state/app-store";
import { useInvestigatorPanelStore } from "../../../state/investigator-panel-store";
import { useOnlineStore } from "../../../state/online-store";
import { useStructuredEditorStore } from "../../../state/structured-editor-store";
import {
  initialStructuredState,
  useStructuredStore,
} from "../../../state/structured-store";
import { setStructuredSender } from "../../../structured-transport";
import { ClueCard } from "./ClueCard";
import { InventoryCard } from "./InventoryCard";
import { StructuredActionDialog } from "./StructuredActionDialog";

let sent: Record<string, unknown>[] = [];

function structuredClueStore() {
  useStructuredStore.setState((state) => ({
    ...state,
    capabilities: { ...state.capabilities },
  }));
  useStructuredStore.getState().applyCapabilities(STRUCTURED_CAPABILITIES_WIRE);
  useStructuredStore.getState().applySnapshot(
    {
      ...EVENT_FIXTURES.snapshot.payload,
      clues: [
        {
          id: "clue_death_certificate",
          category: "investigation",
          text: "莱特的死亡证明由惠特克罗夫特医生签署。",
          presentation: ["describe", "image"],
          allowed_physical_item_ids: ["item_certificate"],
        },
        {
          id: "clue_burned_letters",
          category: "investigation",
          text: "壁炉里残留着匆忙焚烧的信件碎片。",
          presentation: ["describe", "original"],
          allowed_physical_item_ids: ["item_letters"],
        },
      ],
      items: [
        {
          id: "item_certificate",
          label: "死亡证明",
          quantity: 1,
          operations: [],
        },
        {
          id: "item_letters",
          label: "烧焦的信件",
          quantity: 1,
          operations: [],
        },
        {
          id: "item_bandage",
          label: "绷带",
          quantity: 3,
          operations: ["apply", "give"],
        },
      ],
      targets: [
        { kind: "npc", id: "john_whitcroft", name: "约翰·惠特克罗夫特" },
        { kind: "investigator", id: "inv-bob", name: "鲍勃" },
      ],
    } as Record<string, unknown>,
    WORLD_ID,
  );
}

beforeEach(() => {
  useStructuredStore.setState({ ...initialStructuredState });
  useStructuredEditorStore.setState({
    draft: null,
    sending: false,
    error: null,
  });
  useAppStore.setState({
    mode: "local",
    connection: "connected",
    inputEnabled: true,
    activeWorldId: WORLD_ID,
    clues: {},
    character: { name: "爱丽丝", inventory: ["死亡证明", "绷带", "绷带"] },
  });
  useOnlineStore.setState({ activeInvestigatorId: "inv-alice" });
  useInvestigatorPanelStore.setState({ editor: null });
  sent = [];
  setStructuredSender((payload) => {
    sent.push(payload as Record<string, unknown>);
    return true;
  });
});

describe("线索卡的路径切换", () => {
  it("legacy 世界：出示按钮打开旧编辑器，发的是文字行动", () => {
    useStructuredStore.getState().applyCapabilities(LEGACY_CAPABILITIES);
    useAppStore.setState({
      clues: { investigation: [{ id: "c1", text: "一封烧焦的信" }] },
    });
    render(<ClueCard onImage={vi.fn()} />);
    expect(screen.queryByText(/结构化模式/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "出示" }));
    expect(useInvestigatorPanelStore.getState().editor?.draft.kind).toBe(
      "present",
    );
  });

  it("结构化世界：出示按钮打开结构化编辑器，不再打开旧编辑器", () => {
    structuredClueStore();
    render(<ClueCard onImage={vi.fn()} />);
    expect(screen.getByText(/结构化模式/)).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "出示" })[0]);
    expect(useStructuredEditorStore.getState().draft?.kind).toBe("present");
    expect(useInvestigatorPanelStore.getState().editor).toBeNull();
  });

  it("结构化世界但服务端未提供投影：不显示旧文字线索，并说明在等投影", () => {
    useStructuredStore
      .getState()
      .applyCapabilities(STRUCTURED_CAPABILITIES_WIRE);
    useAppStore.setState({
      clues: { investigation: [{ id: "c1", text: "一封烧焦的信" }] },
    });
    render(<ClueCard onImage={vi.fn()} />);
    // 结构化模式下不使用文本标签冒充 ID：旧列表不渲染，等公开投影。
    expect(
      screen.queryByRole("button", { name: "出示" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/等待服务端提供公开线索投影/)).toBeInTheDocument();
  });

  it("结构化世界缺少稳定 ID 时禁用出示，并说明不用文本冒充 ID", () => {
    structuredClueStore();
    // 让投影里出现一条没有 id 的记录（防御性：正常投影一定有 id）。
    useStructuredStore.setState((state) => ({
      ...state,
      clues: [
        ...state.clues,
        {
          id: "",
          category: "investigation",
          text: "匿名记录",
          presentation: ["describe"],
          allowedPhysicalItemIds: [],
        },
      ],
    }));
    render(<ClueCard onImage={vi.fn()} />);
    const buttons = screen.getAllByRole("button", { name: "出示" });
    const blocked = buttons.find((button) => button.hasAttribute("disabled"));
    expect(blocked?.getAttribute("title")).toContain("稳定 ID");
  });
});

describe("道具卡的路径切换", () => {
  it("legacy：使用按钮打开旧编辑器", () => {
    useStructuredStore.getState().applyCapabilities(LEGACY_CAPABILITIES);
    render(<InventoryCard />);
    fireEvent.click(screen.getAllByRole("button", { name: "使用" })[0]);
    expect(useInvestigatorPanelStore.getState().editor?.draft.kind).toBe("use");
  });

  it("结构化：使用按钮带物品 ID/数量/操作打开结构化编辑器", () => {
    structuredClueStore();
    render(<InventoryCard />);
    expect(screen.getByText(/结构化模式/)).toBeInTheDocument();
    const row = document.querySelector('[data-item-id="item_bandage"]')!;
    fireEvent.click(row.querySelector("button")!);
    const draft = useStructuredEditorStore.getState().draft!;
    expect(draft.itemId).toBe("item_bandage");
    expect(draft.availableQuantity).toBe(3);
    expect(draft.operations).toEqual(["apply", "give"]);
    // 显示数量来自服务端投影，前端不预扣。
    expect(
      document.querySelector('[data-item-id="item_bandage"]')?.textContent,
    ).toContain("×3");
  });
});

describe("结构化出示编辑器", () => {
  it("出示方式只有服务端允许的可选，其余禁用并说明原因", () => {
    structuredClueStore();
    useStructuredEditorStore.getState().openPresent({
      clueId: "clue_death_certificate",
      subject: "死亡证明",
      presentations: ["describe", "image"],
      allowedPhysicalItemIds: [],
      worldId: WORLD_ID,
    });
    render(<StructuredActionDialog />);
    expect(screen.getByRole("radio", { name: "说明内容" })).toBeEnabled();
    expect(screen.getByRole("radio", { name: "展示图片" })).toBeEnabled();
    const original = screen.getByRole("radio", { name: "展示原件" });
    expect(original).toBeDisabled();
    expect(original).toHaveAttribute(
      "title",
      "这条线索还没有可出示的关联实物。",
    );
  });

  it("提交的载荷是结构请求：线索 ID + 方式 + 目标 ID + 询问", () => {
    structuredClueStore();
    useStructuredEditorStore.getState().openPresent({
      clueId: "clue_death_certificate",
      subject: "死亡证明",
      presentations: ["describe", "image"],
      allowedPhysicalItemIds: [],
      worldId: WORLD_ID,
    });
    render(<StructuredActionDialog />);
    fireEvent.click(screen.getByRole("radio", { name: "展示图片" }));
    fireEvent.change(screen.getByLabelText("向谁出示 / 说明"), {
      target: { value: "npc:john_whitcroft" },
    });
    fireEvent.change(screen.getByLabelText("想询问什么（可选）"), {
      target: { value: "你认得这份证明吗？" },
    });
    fireEvent.click(screen.getByRole("button", { name: "提交请求" }));

    expect(sent).toHaveLength(1);
    expect(sent[0].type).toBe("action_request");
    expect(sent[0].action).toEqual({
      kind: "present_clue",
      clue_id: "clue_death_certificate",
      presentation: "image",
      physical_item_id: null,
      target: { kind: "npc", id: "john_whitcroft" },
      question: "你认得这份证明吗？",
    });
    expect(JSON.stringify(sent[0])).not.toContain("我向");
  });

  it("展示原件必须选一件持有的实物，未选时禁止提交", () => {
    structuredClueStore();
    useStructuredEditorStore.getState().openPresent({
      clueId: "clue_burned_letters",
      subject: "烧焦的信件",
      presentations: ["describe", "original"],
      allowedPhysicalItemIds: ["item_letters"],
      worldId: WORLD_ID,
    });
    render(<StructuredActionDialog />);
    fireEvent.click(screen.getByRole("radio", { name: "展示原件" }));
    fireEvent.change(screen.getByLabelText("向谁出示 / 说明"), {
      target: { value: "npc:john_whitcroft" },
    });
    const submit = screen.getByRole("button", { name: "提交请求" });
    expect(submit).toBeDisabled();
    expect(submit).toHaveAttribute(
      "title",
      "展示原件需要选择你实际持有的一件物品。",
    );

    fireEvent.change(screen.getByLabelText("出示哪件原件"), {
      target: { value: "item_letters" },
    });
    fireEvent.click(screen.getByRole("button", { name: "提交请求" }));
    expect((sent[0].action as Record<string, unknown>).physical_item_id).toBe(
      "item_letters",
    );
  });

  it("目标未定时可以“描述其他对象”，提交未解析目标", () => {
    structuredClueStore();
    useStructuredEditorStore.getState().openPresent({
      clueId: "clue_death_certificate",
      subject: "死亡证明",
      presentations: ["describe", "image"],
      allowedPhysicalItemIds: [],
      worldId: WORLD_ID,
    });
    render(<StructuredActionDialog />);
    fireEvent.change(screen.getByLabelText("向谁出示 / 说明"), {
      target: { value: "__unresolved__" },
    });
    fireEvent.change(screen.getByLabelText("描述对象"), {
      target: { value: "站在门边的那位" },
    });
    fireEvent.click(screen.getByRole("button", { name: "提交请求" }));
    expect((sent[0].action as Record<string, unknown>).target).toEqual({
      kind: "unresolved",
      text: "站在门边的那位",
    });
  });

  it("使用道具：数量/用法/目标进结构字段，补充做法进 approach", () => {
    structuredClueStore();
    useStructuredEditorStore.getState().openUse({
      itemId: "item_bandage",
      subject: "绷带",
      operations: ["apply", "give"],
      availableQuantity: 3,
      worldId: WORLD_ID,
    });
    render(<StructuredActionDialog />);
    fireEvent.change(screen.getByLabelText("数量"), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText("常见用法"), {
      target: { value: "apply" },
    });
    fireEvent.change(screen.getByLabelText("目标 / 对象（可选）"), {
      target: { value: "investigator:inv-bob" },
    });
    fireEvent.change(screen.getByLabelText("补充做法（即兴用法）"), {
      target: { value: "先清创再包扎" },
    });
    fireEvent.click(screen.getByRole("button", { name: "提交请求" }));

    expect(sent[0].action).toEqual({
      kind: "use_item",
      item_id: "item_bandage",
      quantity: 2,
      operation: "apply",
      target: { kind: "investigator", id: "inv-bob" },
      approach: "先清创再包扎",
    });
  });

  it("数量超出可用值时就地拦截", () => {
    structuredClueStore();
    useStructuredEditorStore.getState().openUse({
      itemId: "item_bandage",
      subject: "绷带",
      operations: ["apply"],
      availableQuantity: 3,
      worldId: WORLD_ID,
    });
    render(<StructuredActionDialog />);
    fireEvent.change(screen.getByLabelText("数量"), { target: { value: "9" } });
    expect(screen.getByRole("button", { name: "提交请求" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "提交请求" })).toHaveAttribute(
      "title",
      "最多只能选择 3 个。",
    );
  });

  it("协议不可用时提交按钮禁用、原因可读、草稿保留", () => {
    structuredClueStore();
    useStructuredStore.getState().applyCapabilities(HUMAN_ONLY_CAPABILITIES);
    useStructuredStore
      .getState()
      .setProtocolNotice("服务端使用不同版本的协议。");
    useStructuredEditorStore.getState().openPresent({
      clueId: "clue_death_certificate",
      subject: "死亡证明",
      presentations: ["describe"],
      allowedPhysicalItemIds: [],
      worldId: WORLD_ID,
    });
    render(<StructuredActionDialog />);
    fireEvent.change(screen.getByLabelText("向谁出示 / 说明"), {
      target: { value: "npc:john_whitcroft" },
    });
    const submit = screen.getByRole("button", { name: "提交请求" });
    expect(submit).toBeDisabled();
    expect(submit).toHaveAttribute("title", "服务端使用不同版本的协议。");
    expect(sent).toHaveLength(0);
    // 草稿仍在，供用户改完再提交。
    expect(useStructuredEditorStore.getState().draft?.targetId).toBe(
      "john_whitcroft",
    );
  });

  it("Escape 关闭不产生任何请求", () => {
    structuredClueStore();
    useStructuredEditorStore.getState().openPresent({
      clueId: "clue_death_certificate",
      subject: "死亡证明",
      presentations: ["describe"],
      allowedPhysicalItemIds: [],
      worldId: WORLD_ID,
    });
    render(<StructuredActionDialog />);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(sent).toHaveLength(0);
  });
});

describe("重新打开与延迟关闭的竞态", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  /**
   * 回归：提交成功后会先关掉编辑器，再排一个 150ms 的退出动画定时器；
   * 如果玩家在这段窗口里点「重新编辑」，那个遗留定时器会把刚恢复的草稿清掉
   * （CI 上定时器更晚触发，必现）。重新打开必须撤销上一次的延迟关闭。
   */
  it("提交后立刻重新打开：遗留的关闭定时器不得清掉刚恢复的草稿", () => {
    structuredClueStore();
    useStructuredEditorStore.getState().openPresent({
      clueId: "clue_death_certificate",
      subject: "死亡证书",
      presentations: ["describe"],
      allowedPhysicalItemIds: [],
      worldId: WORLD_ID,
    });
    useStructuredEditorStore.getState().update({
      targetKind: "npc",
      targetId: "john_whitcroft",
      question: "你认得这份证明吗？",
    });
    render(<StructuredActionDialog />);
    expect(screen.getByLabelText("想询问什么（可选）")).toHaveValue(
      "你认得这份证明吗？",
    );

    // 提交 → 立即 close() 并排下 150ms 的退出定时器
    fireEvent.click(screen.getByRole("button", { name: "提交请求" }));
    expect(sent).toHaveLength(1);
    expect(useStructuredEditorStore.getState().draft).toBeNull();

    // 玩家在退出动画窗口内重新打开（等价于测试里的「重新编辑」）
    act(() => {
      useStructuredEditorStore.getState().openPresent({
        clueId: "clue_death_certificate",
        subject: "死亡证书",
        presentations: ["describe"],
        allowedPhysicalItemIds: [],
        worldId: WORLD_ID,
      });
    });

    // 重新打开后草稿必须在（中间断言：确认重开本身成功）
    expect(
      useStructuredEditorStore.getState().draft,
      "重新打开后草稿应存在",
    ).not.toBeNull();
    // 让遗留定时器到点：它不该再把刚打开的草稿关掉
    act(() => {
      vi.advanceTimersByTime(300);
    });
    expect(useStructuredEditorStore.getState().draft).not.toBeNull();
    expect(screen.getByLabelText("想询问什么（可选）")).toBeInTheDocument();
  });
});
