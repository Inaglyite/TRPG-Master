import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { useAppStore } from "../../state/app-store";
import { HandoutLayer, SavePanel } from "./PanelLayers";

describe("React panel layers", () => {
  beforeEach(() => {
    useAppStore.setState({
      savePanelOpen: false,
      savePanelMode: "manage",
      saves: [],
      worlds: [],
      handouts: [],
      clueToast: null,
    });
  });

  it("renders save metadata from application state", () => {
    useAppStore.setState({
      savePanelOpen: true,
      saves: [
        {
          id: "slot_001",
          label: "停尸间",
          character_name: "阿瑟",
          hp: 8,
          san: 52,
        },
      ],
    });
    render(<SavePanel />);
    expect(screen.getByText(/停尸间/)).toBeInTheDocument();
    expect(screen.getByText("阿瑟")).toBeInTheDocument();
    expect(screen.getByText("HP 8 SAN 52")).toBeInTheDocument();
  });

  it("renders handouts and clue feedback from application state", () => {
    useAppStore.setState({
      clueToast: "获得尸检线索",
      handouts: [
        {
          id: "court",
          file: "court.png",
          label: "考特",
          asset_data_uri: "data:image/png;base64,AA==",
          asset_url: "",
          entity_type: "npc",
          entity_id: "court",
        },
      ],
    });
    render(<HandoutLayer />);
    expect(screen.getByRole("img", { name: "考特" })).toBeInTheDocument();
    expect(screen.getByText("获得尸检线索")).toBeInTheDocument();
  });
});
