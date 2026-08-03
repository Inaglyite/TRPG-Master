import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useModelStore } from "../../state/model-store";
import { ModelSettingsPanel } from "./ModelSettingsPanel";

vi.mock("../../settings", () => ({
  closeSettings: vi.fn(),
  saveSettings: vi.fn(),
  requestTurnDiagnostics: vi.fn(),
}));

describe("ModelSettingsPanel", () => {
  beforeEach(() => {
    localStorage.clear();
    useModelStore.setState({
      open: true,
      narrativeModel: "flash",
      judgementModel: "pro",
      narrativeDraft: "flash",
      judgementDraft: "pro",
      availableModels: [
        { id: "flash", label: "Flash" },
        { id: "pro", label: "Pro" },
      ],
      saving: false,
      diagnosticsLoading: false,
      diagnostics: null,
    });
  });

  it("derives the routing preset from controlled model values", () => {
    render(<ModelSettingsPanel />);
    expect(
      screen.getByRole("dialog", { name: "模型设置" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "均衡" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByLabelText("叙述模型")).toHaveValue("flash");
    expect(screen.getByLabelText("判定模型")).toHaveValue("pro");
  });

  it("offers three narration speed tiers and persists the choice locally", () => {
    render(<ModelSettingsPanel />);
    // 默认与无效存储值都回退"标准"
    expect(screen.getByRole("button", { name: "标准" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "慢" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );

    fireEvent.click(screen.getByRole("button", { name: "快" }));
    expect(localStorage.getItem("trpg-narration-speed")).toBe("fast");
    expect(screen.getByRole("button", { name: "快" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    fireEvent.click(screen.getByRole("button", { name: "慢" }));
    expect(localStorage.getItem("trpg-narration-speed")).toBe("slow");
  });
});
