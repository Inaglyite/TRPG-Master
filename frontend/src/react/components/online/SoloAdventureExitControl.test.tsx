import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { abandonSoloWorld, enterSoloLobby } from "../../../online";
import {
  initialOnlineState,
  useOnlineStore,
} from "../../../state/online-store";
import { SoloAdventureExitControl } from "./SoloAdventureExitControl";

vi.mock("../../../online", () => ({
  abandonSoloWorld: vi.fn(),
  enterSoloLobby: vi.fn(),
}));

const alice = { id: "u1", username: "alice" };

function setupSoloRoom(patch: Record<string, unknown> = {}) {
  useOnlineStore.setState({
    ...initialOnlineState,
    authStatus: "authenticated",
    user: alice,
    view: "room",
    activeWorldId: "world-solo",
    roomStatus: "playing",
    roomMetadata: { name: "雾中宅邸", play_mode: "solo" },
    members: [
      { user_id: "u1", username: "alice", role: "owner", investigator: null },
    ],
    ...patch,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(enterSoloLobby).mockResolvedValue(undefined);
  vi.mocked(abandonSoloWorld).mockResolvedValue(true);
  setupSoloRoom();
});

describe("SoloAdventureExitControl", () => {
  it("只在云端单人房主的开局/进行阶段显示", () => {
    const { container, rerender } = render(<SoloAdventureExitControl />);
    expect(
      screen.getByRole("button", { name: "离开当前冒险" }),
    ).toBeInTheDocument();

    setupSoloRoom({ roomMetadata: { name: "周五调查夜" } });
    rerender(<SoloAdventureExitControl />);
    expect(
      screen.queryByRole("button", { name: "离开当前冒险" }),
    ).not.toBeInTheDocument();
    expect(container.firstChild).toBeNull();
  });

  it("普通成员即使被意外放入单人世界也没有归档入口", () => {
    setupSoloRoom({
      members: [
        {
          user_id: "u1",
          username: "alice",
          role: "player",
          investigator: null,
        },
      ],
    });
    const { container } = render(<SoloAdventureExitControl />);
    expect(
      screen.queryByRole("button", { name: "离开当前冒险" }),
    ).not.toBeInTheDocument();
    expect(container.firstChild).toBeNull();
  });

  it("暂停仅返回我的冒险，不调用归档接口", async () => {
    render(<SoloAdventureExitControl />);
    fireEvent.click(screen.getByRole("button", { name: "离开当前冒险" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("返回会保留当前进度");

    fireEvent.click(
      screen.getByRole("button", { name: "返回我的冒险（保留进度）" }),
    );
    await waitFor(() => expect(enterSoloLobby).toHaveBeenCalledTimes(1));
    expect(abandonSoloWorld).not.toHaveBeenCalled();
  });

  it("放弃需要第二次确认，才调用专用归档流程", async () => {
    render(<SoloAdventureExitControl />);
    fireEvent.click(screen.getByRole("button", { name: "离开当前冒险" }));
    fireEvent.click(screen.getByRole("button", { name: "放弃并删除存档" }));

    expect(abandonSoloWorld).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toHaveTextContent("确认放弃冒险？");
    fireEvent.click(screen.getByRole("button", { name: "确认放弃并删除" }));

    await waitFor(() => expect(abandonSoloWorld).toHaveBeenCalledTimes(1));
    expect(enterSoloLobby).not.toHaveBeenCalled();
  });

  it("服务端拒绝归档时保留确认层并显示错误", async () => {
    vi.mocked(abandonSoloWorld).mockImplementation(async () => {
      useOnlineStore.setState({ roomError: "守秘人仍在处理本回合" });
      return false;
    });
    render(<SoloAdventureExitControl />);
    fireEvent.click(screen.getByRole("button", { name: "离开当前冒险" }));
    fireEvent.click(screen.getByRole("button", { name: "放弃并删除存档" }));
    fireEvent.click(screen.getByRole("button", { name: "确认放弃并删除" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "守秘人仍在处理本回合",
    );
    expect(
      screen.getByRole("button", { name: "确认放弃并删除" }),
    ).toBeInTheDocument();
  });
});
