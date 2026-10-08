import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { loadSave } from "./panels";
import { saveReadBlockReason } from "./save-read-boundary";
import { safeSend } from "./ws";
import { addMsg } from "./renderer";
import { useAppStore } from "./state/app-store";
import { initialOnlineState, useOnlineStore } from "./state/online-store";
import {
  initialStructuredState,
  useStructuredStore,
} from "./state/structured-store";
import { STRUCTURED_CAPABILITIES_WIRE } from "./protocol/structured-fixtures";
import { SavePanel } from "./react/components/PanelLayers";

vi.mock("./ws", () => ({ safeSend: vi.fn() }));
vi.mock("./renderer", () => ({ addMsg: vi.fn(), removeLoading: vi.fn() }));
vi.mock("./start", () => ({ getGameStarted: () => true }));

beforeEach(() => {
  vi.clearAllMocks();
  useAppStore.setState({
    mode: "online",
    connection: "connected",
    activeWorldId: "solo-world",
    savePanelOpen: true,
    savePanelMode: "manage",
    adventuresReady: false,
    adventures: [],
    worlds: [],
    saves: [
      {
        id: "slot_001",
        label: "调查前",
        world_id: "solo-world",
        world_active: true,
      },
    ],
    renameSlotId: null,
  });
  useOnlineStore.setState({
    ...initialOnlineState,
    user: { id: "owner", username: "owner" },
    authStatus: "authenticated",
    activeWorldId: "solo-world",
    members: [{ user_id: "owner", username: "owner", role: "owner" }],
    playMode: "solo",
    roomConnection: "connected",
    roomSnapshotReady: true,
    roomMetadata: { play_mode: "solo", execution_profile: "structured_v1" },
  });
  useStructuredStore.setState({ ...initialStructuredState });
  useStructuredStore.getState().applyCapabilities({
    ...STRUCTURED_CAPABILITIES_WIRE,
    structured_solo_restore: true,
  });
  useStructuredStore.setState((s) => ({
    identity: { ...s.identity, worldId: "solo-world", revision: 7 },
  }));
});

describe("读取存档的运行方式边界", () => {
  it("云端单人发新控制帧及真实版本，不回退save_load", () => {
    expect(saveReadBlockReason()).toBeNull();
    loadSave("slot_001");
    expect(safeSend).toHaveBeenCalledExactlyOnceWith(
      JSON.stringify({
        type: "solo_save_load",
        world_id: "solo-world",
        slot_id: "slot_001",
        expected_revision: 7,
      }),
    );
  });
  it.each([false, undefined, "true", 1])(
    "新能力需显式布尔true：%s",
    (capability) => {
      useStructuredStore.getState().applyCapabilities({
        ...STRUCTURED_CAPABILITIES_WIRE,
        structured_solo_restore: capability,
      });
      loadSave("slot_001");
      expect(safeSend).not.toHaveBeenCalled();
      expect(addMsg).toHaveBeenCalledWith(
        "system",
        expect.stringContaining("未开放"),
      );
      expect(useAppStore.getState().savePanelOpen).toBe(true);
    },
  );
  it("多人结构化房主不能读档，但仍能管理存档点", () => {
    useOnlineStore.setState({ playMode: "room" });
    render(<SavePanel />);
    expect(screen.getByRole("button", { name: "读取" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "重命名" })).toBeEnabled();
    expect(screen.getByRole("button", { name: /^删除$/ })).toBeEnabled();
    expect(screen.getByText(/当前多人房间不支持读档/)).toBeVisible();
    loadSave("slot_001");
    expect(safeSend).not.toHaveBeenCalled();
  });
  it("能力撤销后已打开的确认也不能执行", () => {
    render(<SavePanel />);
    fireEvent.click(screen.getByRole("button", { name: "读取" }));
    expect(screen.getByText(/旧行动、检定和战斗授权/)).toBeVisible();
    act(() =>
      useStructuredStore.setState((s) => ({
        capabilities: { ...s.capabilities, structuredSoloRestore: false },
      })),
    );
    expect(screen.getByRole("button", { name: "确认读取" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "确认读取" }));
    expect(safeSend).not.toHaveBeenCalled();
  });
  it.each(["disconnected", "syncing", "player"])(
    "连接/快照/权限未就绪不提交：%s",
    (fault) => {
      if (fault === "disconnected")
        useOnlineStore.setState({ roomConnection: "disconnected" });
      if (fault === "syncing")
        useOnlineStore.setState({ roomSnapshotReady: false });
      if (fault === "player")
        useOnlineStore.setState({
          members: [{ user_id: "owner", username: "owner", role: "player" }],
        });
      loadSave("slot_001");
      expect(safeSend).not.toHaveBeenCalled();
    },
  );
  it("本地结构化继续走已存在的本地恢复，不要求云端能力", () => {
    useAppStore.setState({ mode: "local" });
    useStructuredStore.setState((s) => ({
      capabilities: { ...s.capabilities, structuredSoloRestore: false },
    }));
    loadSave("slot_001");
    expect(safeSend).toHaveBeenCalledExactlyOnceWith(
      JSON.stringify({ type: "save_load", slot_id: "slot_001" }),
    );
  });
});
