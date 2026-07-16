import { render, screen } from "@testing-library/react";
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
});
