import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAppStore } from "../../state/app-store";
import { initialOnlineState, useOnlineStore } from "../../state/online-store";
import { UtilityPanel } from "./UtilityPanel";
import { sendAction, sendPlayerText } from "../../options";
import {
  initialStructuredState,
  useStructuredStore,
} from "../../state/structured-store";
import { handleStructuredPayload } from "../../structured-transport";
import { EVENT_FIXTURES } from "../../protocol/structured-fixtures";
import { closeUtility } from "../../utility";

vi.mock("../../utility", () => ({
  requestNotes: vi.fn(),
  saveNotes: vi.fn(),
  closeUtility: vi.fn(),
}));
vi.mock("../../options", () => ({
  sendAction: vi.fn(),
  sendPlayerText: vi.fn(),
}));

describe("UtilityPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(closeUtility).mockImplementation(() =>
      useAppStore.getState().setUtilityOpen(false),
    );
    vi.mocked(sendPlayerText).mockReturnValue({ ok: true });
    useStructuredStore.setState({ ...initialStructuredState });
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
      connection: "connected",
    });
  });

  it("默认进入私人笔记，展开快捷行动本身不发送请求", () => {
    handleStructuredPayload(EVENT_FIXTURES.snapshot);
    render(<UtilityPanel />);
    expect(screen.getByRole("textbox", { name: "私人笔记" })).toBeVisible();
    const toggle = screen.getByRole("button", {
      name: "快捷行动",
    });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(
      screen.queryByRole("button", { name: "观察环境" }),
    ).not.toBeInTheDocument();
    fireEvent.click(toggle);
    expect(screen.getByRole("button", { name: "观察环境" })).toBeVisible();
    expect(sendPlayerText).not.toHaveBeenCalled();
  });

  it("结构化快捷行动不受旧回合锁限制，走统一玩家请求而非 legacy 入口", () => {
    handleStructuredPayload(EVENT_FIXTURES.snapshot);
    render(<UtilityPanel />);
    fireEvent.click(screen.getByRole("button", { name: "快捷行动" }));
    const button = screen.getByRole("button", { name: "观察环境" });
    expect(button).toBeEnabled();
    fireEvent.click(button);
    expect(sendPlayerText).toHaveBeenCalledWith("观察当前环境");
    expect(sendAction).not.toHaveBeenCalled();
    expect(useAppStore.getState().utilityOpen).toBe(false);
  });

  it("快捷行动发送失败保留笔记窗口并展示拒绝原因", () => {
    handleStructuredPayload(EVENT_FIXTURES.snapshot);
    vi.mocked(sendPlayerText).mockReturnValue({
      ok: false,
      reason: "请求未能发出，请检查连接后重试。",
    });
    render(<UtilityPanel />);
    fireEvent.click(screen.getByRole("button", { name: "快捷行动" }));
    fireEvent.click(screen.getByRole("button", { name: "观察环境" }));
    expect(useAppStore.getState().utilityOpen).toBe(true);
    expect(screen.getByRole("alert")).toHaveTextContent("请求未能发出");
  });

  it("人类主持房间无当前行动者仍可申报，但断线和降为旁观者立即禁用", () => {
    handleStructuredPayload(EVENT_FIXTURES.snapshot);
    useAppStore.setState({ mode: "online" });
    useOnlineStore.setState({
      authStatus: "authenticated",
      user: { id: "u1", username: "alice" },
      roomConnection: "connected",
      roomSnapshotReady: true,
      roomStatus: "playing",
      currentActorUserId: null,
      members: [{ user_id: "u1", username: "alice", role: "player" }],
    });
    const { rerender } = render(<UtilityPanel />);
    fireEvent.click(screen.getByRole("button", { name: "快捷行动" }));
    expect(screen.getByRole("button", { name: "观察环境" })).toBeEnabled();
    useOnlineStore.setState({ roomConnection: "disconnected" });
    rerender(<UtilityPanel />);
    expect(screen.getByRole("button", { name: "观察环境" })).toBeDisabled();
    useOnlineStore.setState({
      roomConnection: "connected",
      members: [{ user_id: "u1", username: "alice", role: "viewer" }],
    });
    rerender(<UtilityPanel />);
    expect(screen.getByRole("button", { name: "观察环境" })).toBeDisabled();
  });

  it("中文输入法的 Escape 不关闭笔记；普通 Escape 可关闭", () => {
    render(<UtilityPanel />);
    const notes = screen.getByRole("textbox");
    notes.focus();
    fireEvent.keyDown(notes, { key: "Escape", isComposing: true });
    expect(closeUtility).not.toHaveBeenCalled();
    fireEvent.keyDown(notes, { key: "Escape" });
    expect(closeUtility).toHaveBeenCalledTimes(1);
  });

  it("keeps notes controlled and gates game actions independently", () => {
    render(<UtilityPanel />);
    fireEvent.click(screen.getByRole("button", { name: "快捷行动" }));
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
    fireEvent.click(screen.getByRole("button", { name: "快捷行动" }));
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
