import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { useAppStore } from "../../state/app-store";
import { DecisionModal, GameControls } from "./GameControls";

describe("game interaction components", () => {
  beforeEach(() => {
    useAppStore.setState({
      inputEnabled: false,
      inputPlaceholder: "等待守秘人叙述……",
      choices: [],
      dialog: null,
      ending: null,
    });
  });

  it("renders choices and input state from the store", () => {
    render(<GameControls />);
    act(() => {
      useAppStore.getState().setChoices([{ label: "检查门锁", isFree: false }]);
      useAppStore.getState().setInput(true, "你决定做什么？");
    });

    expect(
      screen.getByRole("button", { name: "1. 检查门锁" }),
    ).toBeInTheDocument();
    expect(screen.getByPlaceholderText("你决定做什么？")).toBeEnabled();
  });

  it("renders a structured decision without injecting HTML", () => {
    render(<DecisionModal />);
    act(() => {
      useAppStore.getState().setDialog({
        kind: "decision",
        id: "defense-1",
        title: "如何防御？",
        description: "选择本轮反应",
        options: [{ id: "dodge", label: "闪避", description: "尝试避开攻击" }],
      });
    });

    expect(screen.getByRole("dialog")).toHaveTextContent("如何防御？");
    expect(screen.getByRole("button", { name: /闪避/ })).toBeInTheDocument();
  });
});
