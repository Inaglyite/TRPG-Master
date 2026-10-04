import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
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
  it("退出与归档确认都约束 Tab；取消恢复到安全入口，不发归档请求", async () => {
    render(<SoloAdventureExitControl />);
    const trigger = screen.getByRole("button", { name: "离开当前冒险" });
    trigger.focus();
    fireEvent.click(trigger);
    const safe = screen.getByRole("button", { name: "继续调查" });
    expect(safe).toHaveFocus();
    const first = screen.getByRole("button", {
      name: "返回我的冒险（保留进度）",
    });
    const last = safe;
    last.focus();
    fireEvent.keyDown(last, { key: "Tab" });
    expect(first).toHaveFocus();
    fireEvent.keyDown(first, { key: "Tab", shiftKey: true });
    expect(last).toHaveFocus();
    fireEvent.click(screen.getByRole("button", { name: "放弃并归档冒险" }));
    const keep = screen.getByRole("button", { name: "继续保留" });
    expect(keep).toHaveFocus();
    const confirm = screen.getByRole("button", { name: "确认归档" });
    confirm.focus();
    fireEvent.keyDown(confirm, { key: "Tab" });
    expect(keep).toHaveFocus();
    fireEvent.keyDown(keep, { key: "Escape" });
    expect(screen.getByRole("button", { name: "继续调查" })).toHaveFocus();
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(trigger).toHaveFocus();
    expect(abandonSoloWorld).not.toHaveBeenCalled();
    expect(enterSoloLobby).not.toHaveBeenCalled();
  });

  it("同为单人房主但世界切换时，旧归档确认必须关闭", () => {
    render(<SoloAdventureExitControl />);
    fireEvent.click(screen.getByRole("button", { name: "离开当前冒险" }));
    fireEvent.click(screen.getByRole("button", { name: "放弃并归档冒险" }));
    act(() =>
      useOnlineStore.setState({
        activeWorldId: "world-another",
        roomMetadata: { name: "另一份冒险", play_mode: "solo" },
      }),
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(abandonSoloWorld).not.toHaveBeenCalled();
  });

  it("归档提交中焦点留在面板；失败后恢复安全选择", async () => {
    let finish!: (result: boolean) => void;
    vi.mocked(abandonSoloWorld).mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    render(<SoloAdventureExitControl />);
    fireEvent.click(screen.getByRole("button", { name: "离开当前冒险" }));
    fireEvent.click(screen.getByRole("button", { name: "放弃并归档冒险" }));
    const confirm = screen.getByRole("button", { name: "确认归档" });
    confirm.focus();
    fireEvent.click(confirm);
    expect(screen.getByRole("dialog")).toHaveFocus();
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Tab" });
    expect(screen.getByRole("dialog")).toHaveFocus();
    await act(async () => {
      finish(false);
    });
    expect(screen.getByRole("button", { name: "继续保留" })).toHaveFocus();
    expect(abandonSoloWorld).toHaveBeenCalledOnce();
  });
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
    fireEvent.click(screen.getByRole("button", { name: "放弃并归档冒险" }));

    expect(abandonSoloWorld).not.toHaveBeenCalled();
    expect(
      screen.getByRole("dialog", { name: "归档这场冒险？" }),
    ).toHaveTextContent("整场冒险及其分支");
    fireEvent.click(screen.getByRole("button", { name: "确认归档" }));

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
    fireEvent.click(screen.getByRole("button", { name: "放弃并归档冒险" }));
    fireEvent.click(screen.getByRole("button", { name: "确认归档" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "守秘人仍在处理本回合",
    );
    expect(
      screen.getByRole("button", { name: "确认归档" }),
    ).toBeInTheDocument();
  });

  it("归档进行中禁用两种选择，连点不会重复调用", async () => {
    let finish!: (success: boolean) => void;
    vi.mocked(abandonSoloWorld).mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    render(<SoloAdventureExitControl />);
    fireEvent.click(screen.getByRole("button", { name: "离开当前冒险" }));
    fireEvent.click(screen.getByRole("button", { name: "放弃并归档冒险" }));
    fireEvent.click(screen.getByRole("button", { name: "确认归档" }));
    fireEvent.click(screen.getByRole("button", { name: "正在归档…" }));
    expect(abandonSoloWorld).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "继续保留" })).toBeDisabled();
    finish(false);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "确认归档" })).toBeEnabled(),
    );
  });
});
