import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useStartStore } from "../../state/start-store";
import { StartScreen } from "./StartScreen";

vi.mock("../../start", () => ({
  switchModule: vi.fn(),
  continueGame: vi.fn(),
  startGame: vi.fn(),
}));
vi.mock("../../settings", () => ({ openSettings: vi.fn() }));

describe("StartScreen", () => {
  beforeEach(() => {
    useStartStore.setState({
      gameStarted: false,
      gameStarting: false,
      view: "menu",
      modules: [{ id: "scarlet", title: "猩红文档" }],
      activeModule: "scarlet",
      activeModuleTitle: "猩红文档",
      moduleSwitchPending: false,
      charactersReady: true,
      characterGroups: [
        {
          id: "module",
          title: "模组调查员",
          characters: [
            {
              ref: { source: "module", id: "arthur" },
              id: "arthur",
              name: "阿瑟",
              occupation: "侦探",
              source_label: "猩红文档",
              hp: 10,
              max_hp: 10,
              san: 60,
              max_san: 60,
              reputation: 0,
              completed_modules: 0,
            },
          ],
        },
      ],
      selectedCharacterId: "arthur",
      selectedCharacterRef: { source: "module", id: "arthur" },
      hasSaves: false,
      hint: "",
    });
  });

  it("moves from module menu to investigator selection without DOM adapters", () => {
    render(<StartScreen />);
    expect(screen.getByDisplayValue("猩红文档")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /开始新游戏/ }));
    expect(
      screen.getByRole("heading", { name: "选择调查员" }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("阿瑟").length).toBeGreaterThan(0);
    expect(
      screen.getByRole("button", { name: "以此调查员开始" }),
    ).toBeEnabled();
  });
});
