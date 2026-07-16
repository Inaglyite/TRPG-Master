import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { useAppStore } from "../../state/app-store";
import { CharacterPanelContent } from "./CharacterPanelContent";

describe("CharacterPanelContent", () => {
  beforeEach(() => {
    useAppStore.setState({ character: null, clues: {} });
  });

  it("renders authoritative character and categorized clue state", () => {
    render(<CharacterPanelContent />);
    act(() => {
      useAppStore.getState().setCharacter({
        name: "伊莱亚斯",
        occupation: "记者",
        hp: 8,
        max_hp: 10,
        san: 52,
        max_san: 60,
        attributes: { 侦查: 70 },
        inventory: ["相机"],
      });
      useAppStore.getState().setClues({
        investigation: [
          {
            id: "photo",
            text: "沾血的照片",
            asset: {
              file: "photo.png",
              asset_data_uri: "data:image/png;base64,AA==",
            },
          },
        ],
      });
    });

    expect(
      screen.getByRole("heading", { name: "伊莱亚斯" }),
    ).toBeInTheDocument();
    expect(screen.getByText("8 / 10")).toBeInTheDocument();
    expect(screen.getByText("相机")).toBeInTheDocument();
    expect(screen.getByText("沾血的照片")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "photo.png" }));
    expect(screen.getAllByRole("img", { name: "photo.png" })).toHaveLength(2);
  });
});
