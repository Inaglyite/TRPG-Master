import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAppStore, type Handout } from "../../state/app-store";
import { HandoutCard } from "./HandoutCard";

const material: Handout = {
  id: "map",
  file: "map.png",
  label: "调查地图",
  asset_data_uri: "",
  asset_url: "/test/map.png",
  entity_type: "clue",
  entity_id: "map",
};

describe("材料提示与查看（独立组件）", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useAppStore.setState({ handouts: [material] });
  });
  afterEach(() => vi.useRealTimers());
  it("未交互的提示仍自动收起，保持既有瞬时提示语义", () => {
    render(<HandoutCard handout={material} />);
    act(() => vi.advanceTimersByTime(10000));
    act(() => vi.advanceTimersByTime(300));
    expect(useAppStore.getState().handouts).toHaveLength(0);
  });
  it("阅读、焦点与悬停均暂停提示超时，关闭返回入口", () => {
    render(<HandoutCard handout={material} />);
    const entry = screen.getByRole("button", { name: "查看材料：调查地图" });
    entry.focus();
    fireEvent.focus(entry);
    act(() => vi.advanceTimersByTime(11000));
    expect(useAppStore.getState().handouts).toHaveLength(1);
    fireEvent.click(entry);
    act(() => vi.advanceTimersByTime(11000));
    expect(
      screen.getByRole("dialog", { name: "调查地图" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "关闭材料查看" }));
    expect(entry).toHaveFocus();
    fireEvent.mouseEnter(entry.closest(".handout-card")!);
    fireEvent.blur(entry, { relatedTarget: document.body });
    act(() => vi.advanceTimersByTime(11000));
    expect(useAppStore.getState().handouts).toHaveLength(1);
  });
  it("预览错误仍提供有名称的查看入口", () => {
    render(<HandoutCard handout={material} />);
    fireEvent.error(screen.getByRole("img"));
    expect(
      screen.getByRole("button", { name: "查看材料：调查地图" }),
    ).toHaveTextContent("预览未能加载");
  });
  it("组件卸载清理延迟收起，不继续写全局状态", () => {
    const { unmount } = render(<HandoutCard handout={material} />);
    fireEvent.click(
      screen.getByRole("button", { name: "收起材料提示：调查地图" }),
    );
    unmount();
    act(() => vi.advanceTimersByTime(20000));
    expect(useAppStore.getState().handouts).toHaveLength(1);
  });
});
