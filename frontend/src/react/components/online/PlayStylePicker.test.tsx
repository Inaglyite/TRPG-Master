import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PlayStylePicker } from "./PlayStylePicker";

describe("PlayStylePicker", () => {
  it("exposes human play directly without requiring the structured-mode toggle", () => {
    const onChange = vi.fn();
    render(
      <PlayStylePicker
        structured={false}
        keeperMode="human"
        disabled={false}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole("radio", { name: "人类主持" }));
    expect(onChange).toHaveBeenCalledWith({
      structured: true,
      keeperMode: "human",
    });
    expect(screen.getByRole("radio", { name: "经典 AI 叙事" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  it("reflects the selected mode and explains that human play does not call a model", () => {
    render(
      <PlayStylePicker
        structured
        keeperMode="human"
        disabled={false}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByRole("radio", { name: "人类主持" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.getByRole("note")).toHaveTextContent("不调用模型");
    expect(screen.getByText(/调查、社交、战斗与结案/)).toBeVisible();
    expect(screen.getByText(/当前服务器开放的功能/)).toBeVisible();
    expect(screen.queryByText(/尚无完整战斗/)).not.toBeInTheDocument();
  });

  it("does not promise keeper secrecy from the same solo player", () => {
    render(
      <PlayStylePicker
        structured
        keeperMode="human"
        disabled={false}
        solo
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByText(/主持资料也会对你可见/)).toBeInTheDocument();
  });

  it("retains the legacy creation contract when returning to classic play", () => {
    const onChange = vi.fn();
    render(
      <PlayStylePicker
        structured
        keeperMode="agent"
        disabled={false}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole("radio", { name: "经典 AI 叙事" }));
    expect(onChange).toHaveBeenCalledWith({
      structured: false,
      keeperMode: "agent",
    });
  });

  it("supports arrow-key selection and moves focus to the chosen option", () => {
    const onChange = vi.fn();
    render(
      <PlayStylePicker
        structured={false}
        keeperMode="human"
        disabled={false}
        onChange={onChange}
      />,
    );
    const classic = screen.getByRole("radio", { name: "经典 AI 叙事" });
    classic.focus();
    fireEvent.keyDown(classic, { key: "ArrowRight" });
    expect(onChange).toHaveBeenCalledWith({
      structured: true,
      keeperMode: "human",
    });
    expect(screen.getByRole("radio", { name: "人类主持" })).toHaveFocus();
  });

  it("disables every mode while creation is in progress", () => {
    const onChange = vi.fn();
    render(
      <PlayStylePicker
        structured
        keeperMode="human"
        disabled
        onChange={onChange}
      />,
    );
    for (const radio of screen.getAllByRole("radio"))
      expect(radio).toBeDisabled();
    fireEvent.click(screen.getByRole("radio", { name: "AI 主持" }));
    expect(onChange).not.toHaveBeenCalled();
  });
});
