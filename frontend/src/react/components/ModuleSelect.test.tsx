import { fireEvent, render, screen } from "@testing-library/react";
import { act } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ModuleSelect } from "./ModuleSelect";

const OPTIONS = [
  { id: "scarlet", title: "猩红文档" },
  { id: "mansion", title: "疯狂宅邸" },
];

function renderSelect(overrides: Partial<Parameters<typeof ModuleSelect>[0]>) {
  const onSelect = vi.fn();
  render(
    <ModuleSelect
      options={OPTIONS}
      value="scarlet"
      onSelect={onSelect}
      {...overrides}
    />,
  );
  return onSelect;
}

const trigger = () => screen.getByRole("button", { name: "猩红文档" });
const dropdown = () => document.querySelector(".module-select-dropdown");

describe("ModuleSelect 自绘模组下拉", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("触发器显示当前模组，aria 指向 listbox", () => {
    renderSelect({});
    expect(trigger()).toHaveAttribute("aria-haspopup", "listbox");
    expect(trigger()).toHaveAttribute("aria-expanded", "false");
    expect(dropdown()).toBeNull();
  });

  it("点击展开选项列表，焦点落在当前选中项", () => {
    renderSelect({});
    fireEvent.click(trigger());
    expect(screen.getByRole("listbox", { name: "当前模组" })).toBeTruthy();
    const selected = screen.getByRole("option", { name: "猩红文档" });
    expect(selected).toHaveAttribute("aria-selected", "true");
    expect(selected).toHaveClass("selected");
    expect(document.activeElement).toBe(selected);
  });

  it("选择另一项：回调新 id，面板先 closing 再卸载", () => {
    vi.useFakeTimers();
    const onSelect = renderSelect({});
    fireEvent.click(trigger());
    fireEvent.click(screen.getByRole("option", { name: "疯狂宅邸" }));
    expect(onSelect).toHaveBeenCalledWith("mansion");
    expect(dropdown()).toHaveClass("closing");
    act(() => {
      vi.advanceTimersByTime(140);
    });
    expect(dropdown()).toBeNull();
    expect(document.activeElement).toBe(trigger());
  });

  it("选择当前项不触发切换回调", () => {
    vi.useFakeTimers();
    const onSelect = renderSelect({});
    fireEvent.click(trigger());
    fireEvent.click(screen.getByRole("option", { name: "猩红文档" }));
    expect(onSelect).not.toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(140);
    });
    expect(dropdown()).toBeNull();
  });

  it("Escape 关闭面板并把焦点还给触发器", () => {
    renderSelect({});
    fireEvent.click(trigger());
    fireEvent.keyDown(screen.getByRole("option", { name: "疯狂宅邸" }), {
      key: "Escape",
    });
    expect(dropdown()).toHaveClass("closing");
    expect(document.activeElement).toBe(trigger());
  });

  it("点击组件外部关闭面板", () => {
    render(
      <div>
        <ModuleSelect options={OPTIONS} value="scarlet" onSelect={vi.fn()} />
        <button type="button">外部</button>
      </div>,
    );
    fireEvent.click(trigger());
    expect(dropdown()).not.toBeNull();
    fireEvent.pointerDown(screen.getByRole("button", { name: "外部" }));
    expect(dropdown()).toHaveClass("closing");
  });

  it("键盘：ArrowDown 打开，方向键移动焦点，Enter 选中", () => {
    const onSelect = renderSelect({});
    fireEvent.keyDown(trigger(), { key: "ArrowDown" });
    expect(document.activeElement).toBe(
      screen.getByRole("option", { name: "猩红文档" }),
    );
    fireEvent.keyDown(document.activeElement as Element, { key: "ArrowDown" });
    expect(document.activeElement).toBe(
      screen.getByRole("option", { name: "疯狂宅邸" }),
    );
    fireEvent.keyDown(document.activeElement as Element, { key: "Enter" });
    expect(onSelect).toHaveBeenCalledWith("mansion");
  });

  it("disabled（模组切换中）时不展开", () => {
    renderSelect({ disabled: true });
    fireEvent.click(trigger());
    expect(trigger()).toBeDisabled();
    expect(dropdown()).toBeNull();
  });

  it("prefers-reduced-motion 下关闭立即卸载，无 closing 阶段", () => {
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: query.includes("reduce"),
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
    renderSelect({});
    fireEvent.click(trigger());
    fireEvent.click(screen.getByRole("option", { name: "疯狂宅邸" }));
    expect(dropdown()).toBeNull();
  });
});
