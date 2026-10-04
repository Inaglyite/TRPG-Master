import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useSceneStore } from "../../../state/scene-store";
import {
  initialStructuredState,
  useStructuredStore,
} from "../../../state/structured-store";
import { MoveDialog } from "./MoveDialog";
import { RollDialog } from "./StructuredCards";
import { CompactGameDialog } from "../CompactGameDialog";

const transport = vi.hoisted(() => ({ move: vi.fn(), roll: vi.fn() }));
vi.mock("../../../structured-transport", () => ({
  sendStructuredAction: transport.move,
  sendFreeRoll: transport.roll,
  structuredPlayerRequestReason: () => null,
  currentStructuredIdentity: () => ({ investigatorId: "inv-alice" }),
}));
vi.mock("../../../investigator-panel-view", () => ({
  currentPanelPath: () => "structured",
}));
vi.mock("../../../investigator-structured-actions", () => ({
  narrationGuardReason: () => null,
}));
vi.mock("../../../protocol/structured", async (original) => ({
  ...(await original<object>()),
  structuredUnavailableReason: () => null,
}));
vi.mock("../../../renderer", () => ({
  onDice: vi.fn(),
  onNarrativeChunk: vi.fn(),
  onNarrativeSegment: vi.fn(),
}));
vi.mock("../../../panels", () => ({ updateCharPanel: vi.fn() }));

beforeEach(() => {
  vi.clearAllMocks();
  transport.move.mockReturnValue({ ok: true });
  transport.roll.mockReturnValue({ ok: true });
  useStructuredStore.setState({
    ...initialStructuredState,
    destinations: [{ id: "medical_internal", name: "医学院" }],
  });
  useSceneStore.setState({ worldId: "world-a", status: "known", name: "大学" });
});

describe("紧凑操作面板", () => {
  it("初始焦点和 Tab 排除隐藏、禁用、关闭的 details 内容", () => {
    render(
      <CompactGameDialog
        id="focus-probe"
        title="焦点测试"
        closeLabel="关闭测试"
        onClose={vi.fn()}
        footer={
          <>
            <button>可用底部</button>
            <button hidden>隐藏</button>
            <div style={{ display: "none" }}>
              <button>祖先隐藏</button>
            </div>
            <fieldset disabled>
              <button>禁用</button>
            </fieldset>
            <details>
              <summary>展开</summary>
              <input aria-label="关闭区输入" />
              <button>关闭区按钮</button>
            </details>
          </>
        }
      >
        <input aria-label="输入" />
        <input hidden />
        <input aria-hidden="true" />
      </CompactGameDialog>,
    );
    expect(screen.getByRole("textbox", { name: "输入" })).toHaveFocus();
    const summary = screen.getByText("展开");
    summary.focus();
    fireEvent.keyDown(summary, { key: "Tab" });
    expect(screen.getByRole("button", { name: "关闭测试" })).toHaveFocus();
  });

  it("重新渲染不抢走已选输入焦点，Escape 使用新的关闭回调", () => {
    const oldClose = vi.fn();
    const newClose = vi.fn();
    const view = render(
      <CompactGameDialog
        id="focus-update"
        title="焦点测试"
        closeLabel="关闭"
        onClose={oldClose}
        footer={<button>底部</button>}
      >
        <input aria-label="输入" />
      </CompactGameDialog>,
    );
    const bottom = screen.getByRole("button", { name: "底部" });
    bottom.focus();
    view.rerender(
      <CompactGameDialog
        id="focus-update"
        title="焦点测试"
        closeLabel="关闭"
        onClose={newClose}
        footer={<button>底部</button>}
      >
        <input aria-label="输入" />
      </CompactGameDialog>,
    );
    expect(bottom).toHaveFocus();
    fireEvent.keyDown(bottom, { key: "Escape" });
    expect(newClose).toHaveBeenCalledOnce();
    expect(oldClose).not.toHaveBeenCalled();
  });
  for (const [label, Component] of [
    ["前往", MoveDialog],
    ["掷骰", RollDialog],
  ] as const) {
    it(`${label}：打开后聚焦面板、Tab 不越界、Escape 不提交并恢复入口焦点`, () => {
      const trigger = document.createElement("button");
      document.body.append(trigger);
      trigger.focus();
      const onClose = vi.fn();
      const view = render(<Component onClose={onClose} />);
      const dialog = screen.getByRole("dialog");
      expect(dialog.contains(document.activeElement)).toBe(true);
      const cancel = screen.getByRole("button", { name: "取消" });
      const last =
        label === "掷骰"
          ? screen.getByRole("button", { name: "掷骰" })
          : cancel;
      last.focus();
      fireEvent.keyDown(last, { key: "Tab" });
      expect(document.activeElement).toBe(
        screen.getByRole("button", { name: /关闭/ }),
      );
      fireEvent.keyDown(document.activeElement!, {
        key: "Tab",
        shiftKey: true,
      });
      expect(document.activeElement).toBe(last);
      fireEvent.keyDown(dialog, { key: "Escape" });
      expect(onClose).toHaveBeenCalledOnce();
      expect(transport.move).not.toHaveBeenCalled();
      expect(transport.roll).not.toHaveBeenCalled();
      view.unmount();
      expect(document.activeElement).toBe(trigger);
      trigger.remove();
    });
  }

  it("只显示公开地点名称，提交仍使用原始 ID；位置不会因点击自行改变", () => {
    render(<MoveDialog onClose={vi.fn()} />);
    expect(screen.getByText(/当前场景/)).toHaveTextContent("大学");
    expect(screen.queryByText("medical_internal")).toBeNull();
    expect(screen.getByRole("button", { name: "医学院" })).toHaveAttribute(
      "data-scene-id",
      "medical_internal",
    );
    fireEvent.click(screen.getByRole("button", { name: "医学院" }));
    expect(transport.move).toHaveBeenCalledWith({
      kind: "move",
      destination_scene_id: "medical_internal",
    });
    expect(useSceneStore.getState().name).toBe("大学");
  });

  it("非法骰式不能提交，焦点约束排除禁用按钮；取消不掷骰", () => {
    render(<RollDialog onClose={vi.fn()} />);
    fireEvent.change(screen.getByRole("combobox"), {
      target: { value: "not-a-die" },
    });
    expect(screen.getByRole("button", { name: "掷骰" })).toBeDisabled();
    const cancel = screen.getByRole("button", { name: "取消" });
    cancel.focus();
    fireEvent.keyDown(cancel, { key: "Tab" });
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: /关闭/ }),
    );
    fireEvent.click(cancel);
    expect(transport.roll).not.toHaveBeenCalled();
  });
});
