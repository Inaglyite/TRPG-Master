import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { loadSave, openSavePanel, quickSave } from "../../../panels";
import { STRUCTURED_CAPABILITIES_WIRE } from "../../../protocol/structured-fixtures";
import { useAppStore } from "../../../state/app-store";
import {
  initialOnlineState,
  useOnlineStore,
} from "../../../state/online-store";
import {
  initialStructuredState,
  useStructuredStore,
} from "../../../state/structured-store";
import { KeeperSaveActions } from "./KeeperSaveActions";

vi.mock("../../../panels", () => ({
  loadSave: vi.fn(),
  openSavePanel: vi.fn(),
  quickSave: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  useAppStore.setState({ mode: "online", connection: "connected" });
  useOnlineStore.setState({
    ...initialOnlineState,
    user: { id: "owner", username: "owner" },
    members: [
      { user_id: "owner", username: "owner", role: "owner", can_keeper: true },
    ],
    playMode: "solo",
    roomConnection: "connected",
    roomSnapshotReady: true,
    roomMetadata: { execution_profile: "structured_v1", play_mode: "solo" },
  });
  useStructuredStore.setState({ ...initialStructuredState });
  useStructuredStore.getState().applyCapabilities({
    ...STRUCTURED_CAPABILITIES_WIRE,
    structured_solo_restore: true,
  });
});

describe("主持台存档动作不是主持授权的扩权", () => {
  it("具备能力的单人房主能分别保存、读取当前自动点和管理", () => {
    render(<KeeperSaveActions />);
    fireEvent.click(screen.getByTestId("keeper-save"));
    fireEvent.click(screen.getByTestId("keeper-load"));
    fireEvent.click(screen.getByTestId("keeper-save-panel"));
    expect(quickSave).toHaveBeenCalledExactlyOnceWith();
    expect(loadSave).toHaveBeenCalledExactlyOnceWith("slot_000");
    expect(openSavePanel).toHaveBeenCalledExactlyOnceWith("manage");
    expect(screen.getByText(/未保存进度不会保留/)).toBeVisible();
  });

  it("多人房主仅禁用读取，保存与管理不被一起误关", () => {
    useOnlineStore.setState({ playMode: "room" });
    render(<KeeperSaveActions />);
    expect(screen.getByTestId("keeper-load")).toBeDisabled();
    expect(screen.getByTestId("keeper-save")).toBeEnabled();
    expect(screen.getByTestId("keeper-save-panel")).toBeEnabled();
    expect(screen.getByTestId("keeper-save-reason")).toHaveTextContent(
      "当前多人房间不支持读档",
    );
    fireEvent.click(screen.getByTestId("keeper-load"));
    expect(loadSave).not.toHaveBeenCalled();
  });

  it.each([false, undefined, "true", 1])(
    "读取能力不是严格true时禁用，不误关保存：%s",
    (capability) => {
      useStructuredStore.getState().applyCapabilities({
        ...STRUCTURED_CAPABILITIES_WIRE,
        structured_solo_restore: capability,
      });
      render(<KeeperSaveActions />);
      expect(screen.getByTestId("keeper-load")).toBeDisabled();
      expect(screen.getByTestId("keeper-save")).toBeEnabled();
      expect(screen.getByTestId("keeper-save-panel")).toBeEnabled();
      expect(screen.getByTestId("keeper-save-reason")).toHaveTextContent(
        "当前服务器未开放",
      );
    },
  );

  it("主持不是房主时三操作禁用，投影恢复也不自动提交", () => {
    render(<KeeperSaveActions />);
    act(() =>
      useOnlineStore.setState({
        members: [
          {
            user_id: "owner",
            username: "owner",
            role: "player",
            can_keeper: true,
          },
        ],
      }),
    );
    expect(screen.getByTestId("keeper-save-reason")).toHaveTextContent(
      "主持授权不包含房主权限",
    );
    for (const id of ["keeper-save", "keeper-load", "keeper-save-panel"]) {
      expect(screen.getByTestId(id)).toBeDisabled();
      fireEvent.click(screen.getByTestId(id));
    }
    expect(quickSave).not.toHaveBeenCalled();
    expect(loadSave).not.toHaveBeenCalled();
    expect(openSavePanel).not.toHaveBeenCalled();
    act(() =>
      useOnlineStore.setState({
        members: [
          {
            user_id: "owner",
            username: "owner",
            role: "owner",
            can_keeper: true,
          },
        ],
      }),
    );
    expect(screen.getByTestId("keeper-save")).toBeEnabled();
    expect(quickSave).not.toHaveBeenCalled();
  });

  it.each(["connection", "room", "snapshot"])(
    "未同步时不能发起存档：%s",
    (fault) => {
      if (fault === "connection")
        useAppStore.setState({ connection: "connecting" });
      if (fault === "room")
        useOnlineStore.setState({ roomConnection: "disconnected" });
      if (fault === "snapshot")
        useOnlineStore.setState({ roomSnapshotReady: false });
      render(<KeeperSaveActions />);
      for (const id of ["keeper-save", "keeper-load", "keeper-save-panel"])
        expect(screen.getByTestId(id)).toBeDisabled();
      expect(screen.getByTestId("keeper-save-reason")).toHaveTextContent(
        "正在连接并同步",
      );
    },
  );

  it("本地不依赖云端读档能力，但断线仍禁用", () => {
    useAppStore.setState({ mode: "local" });
    useStructuredStore.setState((s) => ({
      capabilities: { ...s.capabilities, structuredSoloRestore: false },
    }));
    render(<KeeperSaveActions />);
    expect(screen.getByTestId("keeper-load")).toBeEnabled();
    act(() => useAppStore.setState({ connection: "disconnected" }));
    expect(screen.getByTestId("keeper-load")).toBeDisabled();
  });
});
