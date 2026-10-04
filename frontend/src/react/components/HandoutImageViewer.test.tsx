import { act, fireEvent, render, screen } from "@testing-library/react";
import { createRef } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { HandoutImageViewer } from "./HandoutImageViewer";

describe("材料查看器（尚未接线的独立组件）", () => {
  afterEach(() => vi.useRealTimers());
  it("图片请求不结束也有有界反馈，始终可以关闭", () => {
    vi.useFakeTimers();
    render(
      <HandoutImageViewer
        source="/test/image.png"
        label="调查地图"
        onClose={vi.fn()}
        returnFocus={createRef()}
      />,
    );
    act(() => vi.advanceTimersByTime(12000));
    expect(screen.getByRole("alert")).toHaveTextContent("图片未能加载");
    expect(screen.getByRole("button", { name: "关闭材料查看" })).toBeEnabled();
  });
  it("加载、原尺寸和失败重试都有明确状态", () => {
    render(
      <HandoutImageViewer
        source="/test/image.png"
        label="调查地图"
        onClose={vi.fn()}
        returnFocus={createRef()}
      />,
    );
    expect(screen.getByRole("button", { name: "关闭材料查看" })).toHaveFocus();
    expect(screen.getByRole("status")).toHaveTextContent("正在加载");
    expect(screen.getByRole("button", { name: "原尺寸查看" })).toBeDisabled();
    fireEvent.load(screen.getByRole("img"));
    fireEvent.click(screen.getByRole("button", { name: "原尺寸查看" }));
    expect(screen.getByRole("button", { name: "适应窗口" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    fireEvent.error(screen.getByRole("img"));
    expect(screen.getByRole("alert")).toHaveTextContent("图片未能加载");
    fireEvent.click(screen.getByRole("button", { name: "重新加载图片" }));
    expect(screen.getByRole("status")).toHaveTextContent("正在加载");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
  it("图片内的点击不关闭，只有显式关闭和空白背景关闭", () => {
    const close = vi.fn();
    const { container } = render(
      <HandoutImageViewer
        source="/test/image.png"
        label="调查地图"
        onClose={close}
        returnFocus={createRef()}
      />,
    );
    fireEvent.mouseDown(screen.getByRole("img"));
    expect(close).not.toHaveBeenCalled();
    fireEvent.mouseDown(container.querySelector(".handout-overlay")!);
    expect(close).toHaveBeenCalledTimes(1);
  });
});
