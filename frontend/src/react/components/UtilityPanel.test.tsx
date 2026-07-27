import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAppStore } from "../../state/app-store";
import { initialOnlineState, useOnlineStore } from "../../state/online-store";
import { UtilityPanel } from "./UtilityPanel";

vi.mock("../../utility", () => ({
  requestNotes: vi.fn(),
  saveNotes: vi.fn(),
  closeUtility: vi.fn(),
}));
vi.mock("../../options", () => ({ sendAction: vi.fn() }));

describe("UtilityPanel", () => {
  beforeEach(() => {
    useOnlineStore.setState({ ...initialOnlineState });
    useAppStore.setState({
      mode: "local",
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

  it("多人模式仅当前玩家行动者可用快捷行动，旁观者始终禁用", () => {
    useAppStore.setState({ mode: "online", inputEnabled: true });
    useOnlineStore.setState({
      authStatus: "authenticated",
      user: { id: "u1", username: "alice" },
      roomConnection: "connected",
      roomStatus: "playing",
      currentActorUserId: "u1",
      members: [{ user_id: "u1", username: "alice", role: "viewer" }],
    });
    const { rerender } = render(<UtilityPanel />);
    expect(screen.getByRole("button", { name: "观察环境" })).toBeDisabled();

    useOnlineStore.setState({
      members: [{ user_id: "u1", username: "alice", role: "player" }],
    });
    rerender(<UtilityPanel />);
    expect(screen.getByRole("button", { name: "观察环境" })).toBeEnabled();

    useOnlineStore.setState({ currentActorUserId: "u2" });
    rerender(<UtilityPanel />);
    expect(screen.getByRole("button", { name: "观察环境" })).toBeDisabled();
  });
});
