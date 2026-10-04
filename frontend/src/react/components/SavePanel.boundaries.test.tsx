import {
  act,
  fireEvent,
  render,
  screen,
  within,
  waitFor,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as panels from "../../panels";
import { useAppStore } from "../../state/app-store";
import { useOnlineStore } from "../../state/online-store";
import { SavePanel } from "./PanelLayers";

describe("SavePanel operation and keyboard boundaries", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    useAppStore.setState({
      mode: "local",
      activeWorldId: "save-world-a",
      savePanelOpen: true,
      savePanelMode: "manage",
      adventuresReady: false,
      adventures: [],
      worlds: [],
      renameSlotId: null,
      saves: [
        {
          id: "slot_001",
          label: "读到旧档案",
          scene_name: "图书馆",
          hp: 8,
          san: 52,
        },
      ],
    });
  });

  it("opens a named dialog and contains focus at both ends", () => {
    render(<SavePanel />);
    expect(
      screen.getByRole("dialog", { name: "存档管理" }),
    ).toBeInTheDocument();
    const close = screen.getByRole("button", { name: "关闭存档管理" });
    expect(close).toHaveFocus();
    const last = screen.getByRole("button", { name: "删除" });
    last.focus();
    fireEvent.keyDown(last, { key: "Tab" });
    expect(close).toHaveFocus();
  });

  it("requires a named second confirmation before loading and Escape only cancels that confirmation", () => {
    const load = vi.spyOn(panels, "loadSave").mockImplementation(() => {});
    const close = vi.spyOn(panels, "closeSavePanel");
    render(<SavePanel />);
    const read = screen.getByRole("button", { name: "读取" });
    fireEvent.click(read);
    expect(load).not.toHaveBeenCalled();
    const confirmation = screen.getByRole("group", {
      name: "确认读取读到旧档案",
    });
    expect(within(confirmation).getByText(/未保存/)).toBeInTheDocument();
    const cancel = within(confirmation).getByRole("button", { name: "取消" });
    cancel.focus();
    fireEvent.keyDown(cancel, { key: "Escape" });
    expect(close).not.toHaveBeenCalled();
    expect(read).toHaveFocus();
    fireEvent.click(read);
    fireEvent.click(screen.getByRole("button", { name: "确认读取" }));
    expect(load).toHaveBeenCalledExactlyOnceWith("slot_001");
  });

  it("does not delete a manual point on the first click or on cancellation", () => {
    const remove = vi.spyOn(panels, "deleteSave").mockImplementation(() => {});
    render(<SavePanel />);
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    expect(remove).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(remove).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    fireEvent.click(screen.getByRole("button", { name: "确认删除存档点" }));
    expect(remove).toHaveBeenCalledExactlyOnceWith("slot_001");
  });

  it("does not treat composing Enter as confirmation of a rename", () => {
    const rename = vi.spyOn(panels, "renameSave").mockImplementation(() => {});
    render(<SavePanel />);
    fireEvent.click(screen.getByRole("button", { name: "重命名" }));
    const input = screen.getByDisplayValue("读到旧档案");
    fireEvent.keyDown(input, { key: "Enter", isComposing: true });
    expect(rename).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(rename).toHaveBeenCalledExactlyOnceWith("slot_001", "读到旧档案");
  });

  it("room members get an explicit read-only panel, not usable owner controls", () => {
    useAppStore.setState({ mode: "online" });
    useOnlineStore.setState({
      user: { id: "member", username: "member" },
      members: [{ user_id: "member", role: "player", username: "member" }],
    });
    render(<SavePanel />);
    expect(screen.getByText(/仅房主/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "读取" })).toBeDisabled();
    expect(
      screen.queryByRole("button", { name: "新建存档" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "删除" }),
    ).not.toBeInTheDocument();
  });

  it("cancels a confirmation when the session changes even if the slot ID is reused", () => {
    const load = vi.spyOn(panels, "loadSave").mockImplementation(() => {});
    render(<SavePanel />);
    fireEvent.click(screen.getByRole("button", { name: "读取" }));
    act(() => useAppStore.setState({ activeWorldId: "save-world-b" }));
    expect(
      screen.queryByRole("button", { name: "确认读取" }),
    ).not.toBeInTheDocument();
    expect(load).not.toHaveBeenCalled();
  });

  it("revoking ownership prevents a previously opened confirmation from executing", () => {
    const load = vi.spyOn(panels, "loadSave").mockImplementation(() => {});
    useAppStore.setState({ mode: "online" });
    useOnlineStore.setState({
      user: { id: "owner", username: "owner" },
      members: [{ user_id: "owner", username: "owner", role: "owner" }],
    });
    render(<SavePanel />);
    fireEvent.click(screen.getByRole("button", { name: "读取" }));
    act(() =>
      useOnlineStore.setState({
        members: [{ user_id: "owner", username: "owner", role: "player" }],
      }),
    );
    expect(screen.getByRole("button", { name: "确认读取" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "确认读取" }));
    expect(load).not.toHaveBeenCalled();
  });

  it("load-only mode does not expose rename or delete, and editor Escape keeps the panel open", async () => {
    const close = vi.spyOn(panels, "closeSavePanel");
    render(<SavePanel />);
    fireEvent.click(screen.getByRole("button", { name: "重命名" }));
    const input = screen.getByDisplayValue("读到旧档案");
    input.focus();
    fireEvent.keyDown(input, { key: "Escape" });
    expect(close).not.toHaveBeenCalled();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "关闭存档管理" }),
      ).toHaveFocus(),
    );
    act(() => useAppStore.setState({ savePanelMode: "load" }));
    expect(
      screen.queryByRole("button", { name: "重命名" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "删除" }),
    ).not.toBeInTheDocument();
  });
});
