import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { useAppStore } from "../../state/app-store";
import { HandoutLayer, SavePanel } from "./PanelLayers";

describe("React panel layers", () => {
  beforeEach(() => {
    useAppStore.setState({
      savePanelOpen: false,
      savePanelMode: "manage",
      renameSlotId: null,
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

  it("renders text-labeled save action buttons without emoji or empty tooltips", () => {
    useAppStore.setState({
      savePanelOpen: true,
      saves: [
        { id: "slot_000", label: "自动存档" },
        { id: "slot_001", label: "停尸间" },
      ],
    });
    const { container } = render(<SavePanel />);
    // 文字标签按钮（emoji 在部分平台渲染为色块/豆腐块，已移除）
    expect(
      screen.getAllByRole("button", { name: "读取" }).length,
    ).toBeGreaterThan(0);
    expect(
      screen.getAllByRole("button", { name: "重命名" }).length,
    ).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "删除" })).toBeInTheDocument();
    // 自动存档槽位不渲染删除按钮
    const autoRow = container.querySelector('[data-slot="slot_000"]');
    expect(autoRow?.querySelector(".save-action-del")).not.toBeInTheDocument();
    // 面板内所有带 data-tooltip 的按钮都必须非空，避免空黑框
    const tooltipButtons = container.querySelectorAll("button[data-tooltip]");
    for (const button of tooltipButtons) {
      expect(button.getAttribute("data-tooltip")?.trim()).not.toBe("");
    }
  });

  it("gives rename confirm/cancel buttons non-empty tooltips", () => {
    useAppStore.setState({
      savePanelOpen: true,
      renameSlotId: "slot_001",
      saves: [{ id: "slot_001", label: "停尸间" }],
    });
    render(<SavePanel />);
    expect(screen.getByRole("button", { name: "确认重命名" })).toHaveAttribute(
      "data-tooltip",
      "确认重命名",
    );
    expect(screen.getByRole("button", { name: "取消重命名" })).toHaveAttribute(
      "data-tooltip",
      "取消重命名",
    );
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
