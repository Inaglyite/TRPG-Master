import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAppStore, type Handout } from "../../state/app-store";
import { HandoutLayer } from "./PanelLayers";

const material: Handout = {
  id: "map",
  file: "map.png",
  label: "街区调查地图",
  asset_data_uri: "",
  asset_url: "/test/map.png",
  entity_type: "clue",
  entity_id: "map",
};

describe("玩家材料查看", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useAppStore.setState({ handouts: [material], clueToast: null });
  });
  afterEach(() => vi.useRealTimers());

  it("图片具有可键盘操作的入口和有名称的查看对话框", () => {
    render(<HandoutLayer />);
    fireEvent.click(
      screen.getByRole("button", { name: "查看材料：街区调查地图" }),
    );
    expect(
      screen.getByRole("dialog", { name: "街区调查地图" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "关闭材料查看" })).toHaveFocus();
  });

  it("阅读大图时十秒提示超时不得把材料与查看器一起移除", () => {
    render(<HandoutLayer />);
    fireEvent.click(screen.getByRole("img", { name: "街区调查地图" }));
    act(() => vi.advanceTimersByTime(11000));
    expect(useAppStore.getState().handouts).toHaveLength(1);
    expect(
      screen.getByRole("dialog", { name: "街区调查地图" }),
    ).toBeInTheDocument();
  });

  it("加载失败有明确反馈和重试，不留下无法关闭的空黑屏", () => {
    render(<HandoutLayer />);
    fireEvent.click(screen.getByRole("img", { name: "街区调查地图" }));
    const images = screen.getAllByRole("img", { name: "街区调查地图" });
    fireEvent.error(images[images.length - 1]);
    expect(screen.getByRole("alert")).toHaveTextContent("图片未能加载");
    expect(screen.getByRole("button", { name: "重新加载图片" })).toBeEnabled();
  });

  it("关闭查看返回材料入口，输入法 Escape 不误关", () => {
    render(<HandoutLayer />);
    const image = screen.getByRole("img", { name: "街区调查地图" });
    fireEvent.click(image);
    const close = screen.getByRole("button", { name: "关闭材料查看" });
    close.focus();
    fireEvent.keyDown(close, { key: "Escape", isComposing: true });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.keyDown(close, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "查看材料：街区调查地图" }),
    ).toHaveFocus();
  });

  it("权威状态清除展示材料后，已打开的大图也立即消失", () => {
    render(<HandoutLayer />);
    fireEvent.click(screen.getByRole("img", { name: "街区调查地图" }));
    act(() => useAppStore.setState({ handouts: [] }));
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
