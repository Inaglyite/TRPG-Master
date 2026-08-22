import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { invokeTurnBranch } from "./renderer";
import { useAppStore } from "./state/app-store";
import { useMessageStore } from "./state/message-store";
import { initialOnlineState, useOnlineStore } from "./state/online-store";
import { displayWorldHistory, setActiveTransport } from "./ws";

/**
 * 消息级 ⑂ 分支按钮的发送分派：云端单人房间必须发 solo_branch_create
 * （通用 turn_branch_create 会被服务端当作多人不支持消息拒绝），多人房间
 * 直接不发送；本地模式保持 turn_branch_create 不变。
 */

let sent: ReturnType<typeof vi.fn>;

function setupRoom(
  role: "owner" | "player",
  mode: "online" | "local",
  playMode: string | null = null,
) {
  useOnlineStore.setState({
    ...initialOnlineState,
    authStatus: "authenticated",
    user: { id: "u1", username: "alice" },
    members: [{ user_id: "u1", username: "alice", role }],
    playMode,
  });
  useAppStore.setState({ mode });
}

function attachBranchViaHistory() {
  displayWorldHistory([
    { turn_id: "t0", player_input: "调查", narrative: "叙述0" },
    {
      turn_id: "t1",
      parent_turn_id: "t0",
      player_input: "开门",
      narrative: "叙述1",
    },
  ]);
}

beforeEach(() => {
  sent = vi.fn();
  setActiveTransport({ send: sent });
  useMessageStore.setState({ messages: [], actionReset: 0 });
});

afterEach(() => {
  setActiveTransport(null);
  useOnlineStore.setState({ ...initialOnlineState });
  useAppStore.setState({ mode: "local" });
});

describe("消息级时间线分支发送分派", () => {
  it("云端单人房主：发 solo_branch_create（回合行动前的父回合 ID）", () => {
    setupRoom("owner", "online", "solo");
    attachBranchViaHistory();
    invokeTurnBranch("t1");
    expect(sent).toHaveBeenCalledWith(
      JSON.stringify({ type: "solo_branch_create", turn_id: "t0" }),
    );
    expect(sent).not.toHaveBeenCalledWith(
      expect.stringContaining('"turn_branch_create"'),
    );
  });

  it("多人房间房主：不发送任何分支消息", () => {
    setupRoom("owner", "online", "multiplayer");
    attachBranchViaHistory();
    invokeTurnBranch("t1");
    expect(sent).not.toHaveBeenCalledWith(expect.stringContaining("branch"));
  });

  it("本地模式：保持发 turn_branch_create", () => {
    setupRoom("owner", "local");
    attachBranchViaHistory();
    invokeTurnBranch("t1");
    expect(sent).toHaveBeenCalledWith(
      JSON.stringify({ type: "turn_branch_create", turn_id: "t0" }),
    );
  });
});
