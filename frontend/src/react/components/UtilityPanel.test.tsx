import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAppStore } from "../../state/app-store";
import { UtilityPanel } from "./UtilityPanel";

vi.mock("../../utility", () => ({
  requestNotes: vi.fn(),
  saveNotes: vi.fn(),
  closeUtility: vi.fn(),
}));
vi.mock("../../options", () => ({ sendAction: vi.fn() }));

describe("UtilityPanel", () => {
  beforeEach(() => {
    useAppStore.setState({
      utilityOpen: true,
      notesText: "",
      notesDirty: false,
      notesLoading: false,
      notesSaving: false,
      notesStatus: "",
      inputEnabled: false,
    });
  });

  it("keeps notes controlled and gates game actions independently", () => {
    render(<UtilityPanel />);
    expect(screen.getByRole("button", { name: "观察环境" })).toBeDisabled();
    const notes = screen.getByRole("textbox");
    fireEvent.change(notes, { target: { value: "考特知道停尸间的事" } });
    expect(notes).toHaveValue("考特知道停尸间的事");
    expect(screen.getByRole("button", { name: "保存笔记" })).toBeEnabled();
  });
});
